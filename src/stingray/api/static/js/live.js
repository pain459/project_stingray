/**
 * live.js — render live datapoints supplied by store.js.
 *
 * No direct datapoint fetches: the browser pulls one cached snapshot through
 * window.DP and this file only unpacks it into the DOM. Wrapped in an IIFE to
 * preserve the existing window.* contract.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  let lastRevision = -1;

  function setText(id, value) {
    const el = $(id);
    if (!el) return;
    const next = value == null ? '' : String(value);
    if (el.textContent !== next) el.textContent = next;
  }

  function fmtVal(v, dec) {
    if (v == null || isNaN(v)) return '--';
    return Number(v).toFixed(dec);
  }

  function fmtUptime(s) {
    if (s == null) return '--';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + sec + 's';
    return sec + 's';
  }

  function setMetricText(id, point, dec, suffix) {
    setText(id, point ? fmtVal(point.value, dec) + (suffix || '') : '--');
    const el = $(id);
    if (!el) return;
    if (point && point.observed_at) {
      const observed = new Date(point.observed_at);
      const age = Number.isNaN(observed.getTime())
        ? ''
        : Math.max(0, Math.round((Date.now() - observed.getTime()) / 1000)) + 's ago';
      const title = 'Observed ' + observed.toLocaleString() + (age ? ' · ' + age : '');
      if (el.title !== title) el.title = title;
      if (el.dataset && el.dataset.observedAt !== point.observed_at) {
        el.dataset.observedAt = point.observed_at;
      }
    } else {
      if (el.title) el.title = '';
      if (el.dataset && el.dataset.observedAt) delete el.dataset.observedAt;
    }
  }

  function setStat(id, state) {
    const el = $(id);
    if (!el) return;
    const s = state && state.state;
    const text = !s ? '--' :
      s === 'ready' ? 'Online' : s === 'degraded' ? 'Degraded' : String(s).toUpperCase();
    const className = !s ? 'status' :
      'status ' + (s === 'ready' ? 'good' : s === 'degraded' ? 'warn' : 'bad');
    if (el.textContent !== text) el.textContent = text;
    if (el.className !== className) el.className = className;
  }

  function statsTotal(system) {
    const stats = (system && system.stats) || {};
    return Object.keys(stats).reduce(function (sum, key) {
      return sum + (Number(stats[key].measurements) || 0);
    }, 0);
  }

  function renderLatest(payload) {
    const points = payload.datapoints || payload.live || [];
    const lm = {};
    points.forEach(function (row) { lm[row.sensor + ':' + row.metric] = row; });

    setMetricText('p25', lm['sps30:pm2_5_ug_m3'], 1);
    setMetricText('co2', lm['scd4x:co2_ppm'], 0);

    setMetricText('p1', lm['sps30:pm1_ug_m3'], 1);
    setMetricText('p10', lm['sps30:pm10_ug_m3'], 1);
    setMetricText('p4', lm['sps30:pm4_ug_m3'], 1);

    setMetricText('temp', lm['sht45:temperature_c'], 1);
    setMetricText('hum', lm['sht45:relative_humidity_percent'], 1);

    const voc = lm['sgp41:voc_index'];
    setMetricText('voc', voc, 0);
    setMetricText('voc2', voc, 0);
    setMetricText('nox', lm['sgp41:nox_index'], 0);
    setMetricText('vocraw', lm['sgp41:voc_raw'], 0);
    setMetricText('noxraw', lm['sgp41:nox_raw'], 0);

    setMetricText('n1', lm['sps30:pm1_num_m3'], 1);
    setMetricText('n25', lm['sps30:pm2_5_num_m3'], 1);
    setMetricText('n4', lm['sps30:pm4_num_m3'], 1);
    setMetricText('n10', lm['sps30:pm10_num_m3'], 1);
    setMetricText('tps', lm['sps30:tps_um'], 2, ' µm');
    setMetricText('picpu', lm['pi:cpu_temperature_c'], 1, ' °C');

    const net = payload.network || {};
    setText('netif', net.interface || '--');
    setText('ipaddr', net.ip_address || '--');
    setText('wifirssi', net.wifi_rssi == null ? '--' : net.wifi_rssi + ' dBm');

    const health = payload.health || {};
    setStat('spsstatus', health.sps30);
    setStat('scdstatus', health.scd4x);
    setStat('shtstatus', health.sht45);
    setStat('sgpstatus', health.sgp41);

    const total = payload.total != null ? payload.total : statsTotal(payload.system);
    setText('total', total);
  }

  function renderSystem(system) {
    if (!system) return;
    setText('up', fmtUptime(system.uptime_seconds));
    setText('mode', system.mode || '--');
    // #dt is the browser-local clock owned by app.js. Keep device time as a
    // diagnostic tooltip only; never overwrite the visible local time.
    const el = $('dt');
    if (!el) return;
    const dt = new Date(system.time);
    const title = Number.isNaN(dt.getTime()) ? '' : 'Device time: ' + dt.toLocaleString();
    if (el.title !== title) el.title = title;
  }

  function render(state) {
    if (!state || !state.latest || state.revision === lastRevision) return;
    lastRevision = state.revision;
    renderLatest(state.latest);
    renderSystem(state.latest.system || {});
  }

  // Kept for callers that still expect the old function names.
  async function fetchLive() { await window.DP.refresh(); }
  async function fetchSystem() { await window.DP.refresh(); }

  window.fmtVal = fmtVal;
  window.fmtUptime = fmtUptime;
  window.fetchLive = fetchLive;
  window.fetchSystem = fetchSystem;

  if (window.DP) window.DP.subscribe(render);
})();
