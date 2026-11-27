/**
 * store.js — the single browser-side data pull for the dashboard.
 *
 * The Pi only publishes datapoints and serves static assets. This module owns
 * polling, ETag revalidation, tab leadership, visibility pausing, and the
 * on-demand history cache. Every widget subscribes here; no widget fetches the
 * datapoint API itself. Wrapped in an IIFE to preserve the window.* contract.
 */
(function () {
  'use strict';

  const LATEST_URL = '/api/datapoints?mode=latest';
  const HISTORY_URL = '/api/datapoints?mode=history';
  const POLL_MS = 5000;
  const LEASE_MS = 15000;
  const RETRY_CAP_MS = 60000;
  const HISTORY_TTL_MS = 300000;
  const CHANNEL_NAME = 'stingray-datapoints';
  const LEADER_KEY = 'stingray.datapoints.leader';
  const CACHE_KEY = 'stingray.datapoints.latest';
  const CLIENT_ID = window.crypto && window.crypto.randomUUID
    ? window.crypto.randomUUID()
    : String(Date.now()) + '-' + String(Math.random());

  let latest = null;
  let revision = 0;
  let etag = null;
  let online = false;
  let leader = false;
  let lastError = '';
  let failures = 0;
  let retryAfter = 0;
  let timer = null;
  let inflight = null;
  const listeners = new Set();
  const historyCache = new Map();
  const historyInflight = new Map();

  let channel = null;
  try {
    if ('BroadcastChannel' in window) channel = new BroadcastChannel(CHANNEL_NAME);
  } catch (err) {
    channel = null;
  }

  function storageGet(key) {
    try { return window.localStorage.getItem(key); } catch (err) { return null; }
  }

  function storageSet(key, value) {
    try { window.localStorage.setItem(key, value); } catch (err) { /* storage disabled */ }
  }

  function snapshot() {
    return {
      latest: latest,
      revision: revision,
      online: online,
      leader: leader,
      lastError: lastError
    };
  }

  function notify() {
    listeners.forEach(function (listener) {
      try { listener(snapshot()); } catch (err) { console.error('DP listener error:', err); }
    });
  }

  function subscribe(listener) {
    if (typeof listener !== 'function') return function () {};
    listeners.add(listener);
    listener(snapshot());
    return function () { listeners.delete(listener); };
  }

  function readLeader() {
    const raw = storageGet(LEADER_KEY);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch (err) { return null; }
  }

  function writeLeader(record) {
    storageSet(LEADER_KEY, JSON.stringify(record));
  }

  function hasLease() {
    const record = readLeader();
    return !!record && record.id === CLIENT_ID && record.until > Date.now();
  }

  function tryElect() {
    const now = Date.now();
    const record = readLeader();
    if (record && record.id !== CLIENT_ID && record.until > now) return false;
    writeLeader({ id: CLIENT_ID, until: now + LEASE_MS });
    return true;
  }

  function renewLease() {
    if (hasLease()) writeLeader({ id: CLIENT_ID, until: Date.now() + LEASE_MS });
  }

  function releaseLease() {
    const wasLeader = leader;
    const record = readLeader();
    if (record && record.id === CLIENT_ID) writeLeader({ id: '', until: 0 });
    leader = false;
    if (channel) channel.postMessage({ type: 'yield' });
    if (wasLeader) notify();
  }

  function adopt(payload, broadcast) {
    if (!payload || typeof payload !== 'object') return;
    const nextRevision = Number(payload.revision) || 0;
    const changed = latest === null || nextRevision !== revision || !online;
    latest = payload;
    revision = nextRevision;
    online = true;
    lastError = '';
    failures = 0;
    retryAfter = 0;
    if (broadcast) {
      if (channel) channel.postMessage({ type: 'latest', payload: payload });
      storageSet(CACHE_KEY, JSON.stringify(payload));
    }
    if (changed) notify();
  }

  function adoptCached() {
    const cached = storageGet(CACHE_KEY);
    if (!cached) return;
    try { adopt(JSON.parse(cached), false); } catch (err) { /* ignore stale cache */ }
  }

  function poll() {
    if (inflight) return inflight;
    const headers = etag ? { 'If-None-Match': etag } : {};
    inflight = fetch(LATEST_URL, { cache: 'no-store', headers: headers })
      .then(async function (response) {
        const responseEtag = response.headers && response.headers.get
          ? response.headers.get('ETag')
          : null;
        if (responseEtag) etag = responseEtag;
        if (response.status === 304) {
          const wasOnline = online;
          online = true;
          lastError = '';
          failures = 0;
          retryAfter = 0;
          if (!wasOnline) notify();
          return;
        }
        if (!response.ok) throw new Error('HTTP ' + response.status);
        adopt(await response.json(), true);
      })
      .catch(function (err) {
        online = false;
        lastError = err && err.message ? err.message : String(err);
        failures += 1;
        retryAfter = Date.now() + Math.min(POLL_MS * Math.pow(2, failures), RETRY_CAP_MS);
        notify();
      })
      .finally(function () { inflight = null; });
    return inflight;
  }

  function tick() {
    if (document.hidden) return;
    if (!hasLease() && !tryElect()) {
      // Without BroadcastChannel, non-leader tabs still see shared snapshots.
      if (!channel) adoptCached();
      if (leader) {
        leader = false;
        notify();
      }
      return;
    }
    if (!leader) {
      leader = true;
      notify();
    }
    renewLease();
    if (Date.now() < retryAfter) return;
    poll();
  }

  function historyKey(metric, hours) {
    return String(metric) + '|' + String(hours);
  }

  function applyRemoteHistory(message) {
    if (!message.key || !message.entry) return;
    historyCache.set(message.key, message.entry);
  }

  function history(metric, hours, options) {
    const opts = options || {};
    const key = historyKey(metric, hours);
    let since = null;
    if (opts.since !== undefined && opts.since !== null && opts.since !== '') {
      since = Number(opts.since);
      if (!Number.isFinite(since)) {
        return Promise.reject(new Error('Invalid history cursor'));
      }
    }
    const cached = historyCache.get(key);
    if (!opts.force && since === null && cached && Date.now() - cached.fetchedAt < HISTORY_TTL_MS) {
      return Promise.resolve(cached);
    }
    let url = HISTORY_URL + '?metric=' + encodeURIComponent(metric) +
      '&hours=' + encodeURIComponent(hours) + '&limit=900';
    if (since !== null) url += '&since=' + encodeURIComponent(since);
    const inflightKey = key + '|' + (since === null ? 'new' : String(since));
    if (historyInflight.has(inflightKey)) return historyInflight.get(inflightKey);

    const request = fetch(url, { cache: 'no-store' })
      .then(async function (response) {
        if (!response.ok) throw new Error('HTTP ' + response.status);
        const payload = await response.json();
        if (since === null) {
          const entry = Object.assign({}, payload, { fetchedAt: Date.now() });
          historyCache.set(key, entry);
          if (channel) channel.postMessage({ type: 'history', key: key, entry: entry });
        }
        return payload;
      })
      .finally(function () { historyInflight.delete(inflightKey); });
    historyInflight.set(inflightKey, request);
    return request;
  }

  function cachedHistory(metric, hours) {
    return historyCache.get(historyKey(metric, hours)) || null;
  }

  function mergeHistory(metric, hours, samples) {
    const entry = historyCache.get(historyKey(metric, hours));
    if (!entry || !samples || !samples.length) return entry || null;
    const merged = new Map();
    (entry.samples || []).forEach(function (row) { merged.set(Number(row[0]), Number(row[1])); });
    samples.forEach(function (row) { merged.set(Number(row[0]), Number(row[1])); });
    entry.samples = Array.from(merged.entries())
      .sort(function (a, b) { return a[0] - b[0]; });
    entry.next_cursor = entry.samples.length ? entry.samples[entry.samples.length - 1][0] : entry.next_cursor;
    entry.fetchedAt = Date.now();
    historyCache.set(historyKey(metric, hours), entry);
    return entry;
  }

  function start() {
    if (timer !== null) return;
    adoptCached();
    if (channel) {
      channel.onmessage = function (event) {
        const message = event.data;
        if (!message) return;
        if (message.type === 'latest') adopt(message.payload, false);
        else if (message.type === 'history') applyRemoteHistory(message);
        else if (message.type === 'yield' && !document.hidden) tick();
      };
    }
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) releaseLease();
      else tick();
    });
    window.addEventListener('pagehide', releaseLease);
    timer = setInterval(tick, POLL_MS);
    tick();
  }

  window.DP = {
    start: start,
    subscribe: subscribe,
    refresh: poll,
    history: history,
    cachedHistory: cachedHistory,
    mergeHistory: mergeHistory,
    get state() { return snapshot(); }
  };
})();
