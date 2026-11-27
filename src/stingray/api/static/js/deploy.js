/**
 * deploy.js — Web triggers for Pi release deployment.
 *
 * The deploy restarts stingray.service, so the POST returns 202 immediately and
 * this script polls /api/deploy/status until the CLI writes its final result.
 * Wrapped in an IIFE: no top-level declarations leak into other scripts, and
 * all DOM content is set with textContent, never raw HTML.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const POLL_MS = 2000;
  const MAX_POLLS = 240; // 8 minutes, longer than a typical release download.

  let busy = false;
  let pollTimer = null;

  function setStatus(message) {
    const el = $('deployStatus');
    if (el) el.textContent = message || '';
  }

  function setButtons(disabled) {
    ['deployLatest', 'deployForce'].forEach(function (id) {
      const button = $(id);
      if (button) button.disabled = disabled;
    });
  }

  function formatTime(value) {
    if (!value) return 'unknown time';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? 'unknown time' : d.toLocaleString();
  }

  function startedMillis(payload) {
    if (!payload) return NaN;
    if (payload.started_epoch != null) {
      const epoch = Number(payload.started_epoch) * 1000;
      if (Number.isFinite(epoch) && epoch > 0) return epoch;
    }
    const parsed = Date.parse(payload.started_at);
    return parsed;
  }

  function finish() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    busy = false;
    setButtons(false);
  }

  /** Return true when the deploy has reached a final state. */
  function applyResult(payload, since) {
    if (!payload) return false;
    if (payload.configured === false) {
      setStatus('Web deployment is not configured on this host');
      return true;
    }
    const state = payload.state;
    if (!state || state === 'idle') return false;

    // Ignore the previous deploy's status file until this trigger's run starts.
    const started = startedMillis(payload);
    if (!Number.isFinite(started) || started + 10000 < since) return false;

    if (state === 'running') {
      setStatus('Deploying ' + (payload.action || 'release') + '...');
      return false;
    }
    if (state === 'success') {
      setStatus('Deployed ' + (payload.version || 'latest') + ' · ' + (payload.message || 'OK'));
      return true;
    }
    if (state === 'error') {
      setStatus('Deploy failed: ' + (payload.message || 'unknown error'));
      return true;
    }
    return false;
  }

  async function fetchStatus() {
    const response = await fetch('/api/deploy/status', { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return await response.json();
  }

  function pollForResult(since) {
    let attempts = 0;
    if (pollTimer !== null) clearInterval(pollTimer);
    pollTimer = setInterval(async function () {
      attempts += 1;
      try {
        if (applyResult(await fetchStatus(), since)) {
          finish();
          return;
        }
      } catch (err) {
        // A failed status read is expected while the web service restarts.
        if (attempts % 5 === 0) setStatus('Waiting for the service to restart...');
      }
      if (attempts >= MAX_POLLS) {
        setStatus('Deploy is taking longer than expected - check journalctl -u stingray-deploy.service');
        finish();
      }
    }, POLL_MS);
  }

  async function trigger(force) {
    if (busy) return;
    busy = true;
    setButtons(true);
    const since = Date.now();
    setStatus(force ? 'Starting force redeploy...' : 'Starting deploy...');

    try {
      const response = await fetch(force ? '/api/deploy/force' : '/api/deploy', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        setStatus('Could not start deploy: ' + (body.message || body.error || 'HTTP ' + response.status));
        finish();
        return;
      }
      setStatus('Deploy started; waiting for the service to come back...');
      pollForResult(since);
    } catch (err) {
      setStatus('Could not reach the deploy API');
      finish();
    }
  }

  async function refreshStatus() {
    if (busy) return;
    try {
      const payload = await fetchStatus();
      if (!payload || payload.configured === false) {
        setStatus('Web deployment disabled');
      } else if (payload.state === 'running') {
        setStatus('A deployment is running...');
      } else if (payload.state === 'success' || payload.state === 'error') {
        setStatus(
          'Last deploy: ' + payload.state +
          (payload.version ? ' v' + payload.version : '') +
          ' · ' + formatTime(payload.finished_at)
        );
      } else {
        setStatus('No web deploy yet');
      }
    } catch (err) {
      setStatus('Deploy status unavailable');
    }
  }

  function init() {
    const latest = $('deployLatest');
    const force = $('deployForce');
    if (latest) latest.addEventListener('click', function () { trigger(false); });
    if (force) force.addEventListener('click', function () { trigger(true); });
    refreshStatus();
  }

  window.fetchDeployStatus = refreshStatus;
  window.triggerDeploy = trigger;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
