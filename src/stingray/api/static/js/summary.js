/**
 * summary.js — Fetch, render, and on-demand refresh the local-LLM summary.
 *
 * Wrapped in an IIFE: no top-level declarations leak into the other dashboard
 * scripts. All DOM content is set with textContent, never raw HTML.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  const LABELS = {
    pm2_5_ug_m3: 'PM2.5', co2_ppm: 'CO2', voc_index: 'VOC', nox_index: 'NOx',
    temperature_c: 'Temperature', relative_humidity_percent: 'Humidity', tps_um: 'TPS'
  };
  const DIGITS = {
    pm2_5_ug_m3: 1, co2_ppm: 0, voc_index: 0, nox_index: 0,
    temperature_c: 1, relative_humidity_percent: 1, tps_um: 2
  };

  let cooldownUntil = 0;
  let busy = false;
  let pollTimer = null;

  function formatTime(iso) {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? 'unknown' : d.toLocaleString();
  }

  function fmt(value, digits) {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : '--';
  }

  function formatCountdown(seconds) {
    const total = Math.max(0, Math.ceil(Number(seconds) || 0));
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }

  function setStatus(message) {
    const el = $('summaryCooldown');
    if (el) el.textContent = message || '';
  }

  function updateButton() {
    const button = $('summaryRefresh');
    if (!button) return;
    const remaining = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000));
    if (busy) {
      button.disabled = true;
      button.textContent = 'Generating...';
    } else if (remaining > 0) {
      button.disabled = true;
      button.textContent = `Try again in ${formatCountdown(remaining)}`;
    } else {
      button.disabled = false;
      button.textContent = 'Generate now';
    }
  }

  function applyRefreshState(refresh) {
    if (!refresh || typeof refresh !== 'object') return;
    busy = !!refresh.busy;
    const parsed = refresh.next_allowed_at ? Date.parse(refresh.next_allowed_at) : NaN;
    if (Number.isFinite(parsed)) {
      cooldownUntil = parsed;
    } else if (refresh.cooldown_remaining_seconds != null) {
      cooldownUntil = Date.now() + Number(refresh.cooldown_remaining_seconds) * 1000;
    }
    updateButton();
  }

  function clear(el) {
    if (el) el.replaceChildren();
  }

  function addItem(el, text) {
    if (!el) return;
    const li = document.createElement('li');
    li.textContent = text;
    el.appendChild(li);
  }

  function renderValues(metrics) {
    const container = $('summaryValues');
    clear(container);
    if (!container) return;
    Object.keys(LABELS).forEach(function (key) {
      const m = metrics[key];
      if (!m) return;
      const digits = DIGITS[key] == null ? 1 : DIGITS[key];
      const card = document.createElement('div');
      card.className = 'summary-metric';

      const label = document.createElement('div');
      label.className = 'summary-metric-label';
      label.textContent = LABELS[key];

      const avg = document.createElement('div');
      avg.className = 'summary-metric-avg';
      avg.textContent = `${fmt(m.avg, digits)} ${m.unit || ''}`.trim();

      const range = document.createElement('div');
      range.className = 'summary-metric-range';
      range.textContent = `min ${fmt(m.minimum, digits)} · max ${fmt(m.maximum, digits)}`;
      card.append(label, avg, range);

      if (m.change_vs_previous_pct != null) {
        const pct = Number(m.change_vs_previous_pct);
        if (Number.isFinite(pct) && Math.abs(pct) >= 1) {
          const change = document.createElement('div');
          change.className = 'summary-change ' + (pct > 0 ? 'up' : 'down');
          change.textContent = `${pct > 0 ? '▲' : '▼'} ${Math.abs(pct).toFixed(0)}% vs last hour`;
          card.appendChild(change);
        }
      }
      container.appendChild(card);
    });
  }

  function renderCompare(metrics) {
    const compare = $('summaryCompare');
    clear(compare);
    if (!compare) return;
    const parts = [];
    Object.keys(LABELS).forEach(function (key) {
      const m = metrics[key];
      if (!m || m.previous_avg == null || m.change_vs_previous_pct == null) return;
      const pct = Number(m.change_vs_previous_pct);
      if (!Number.isFinite(pct)) return;
      const digits = DIGITS[key] == null ? 1 : DIGITS[key];
      const sign = pct > 0 ? '+' : '';
      parts.push(
        `${LABELS[key]} ${sign}${pct.toFixed(0)}% ` +
        `(${fmt(m.previous_avg, digits)} → ${fmt(m.avg, digits)} ${m.unit || ''})`.trim()
      );
    });
    if (parts.length) compare.textContent = 'Compared with previous hour · ' + parts.join(' · ');
  }

  function render(summary) {
    const meta = $('summaryMeta');
    const headline = $('summaryHeadline');
    const text = $('summaryText');
    const notes = $('summaryNotes');
    const actions = $('summaryActions');
    if (!meta || !headline || !text || !notes || !actions) return;

    if (!summary) {
      meta.textContent = 'Waiting for the first hourly summary...';
      headline.textContent = '--';
      text.textContent = 'No summary has been generated yet.';
      clear($('summaryValues'));
      clear($('summaryCompare'));
      clear(notes);
      clear(actions);
      return;
    }

    const model = summary.model || 'unknown model';
    const suffix = summary.model_used ? '' : ' (deterministic fallback)';
    meta.textContent = `${formatTime(summary.generated_at)} · ${model}${suffix}`;
    headline.textContent = summary.headline || '--';
    text.textContent = summary.text || '';

    const metrics = (summary.facts && summary.facts.metrics) || {};
    renderValues(metrics);
    renderCompare(metrics);

    clear(notes);
    (summary.findings || []).forEach(function (finding) {
      if (!finding || !finding.code || finding.code.endsWith('_hour_change')) return;
      addItem(notes, finding.message || '');
    });

    clear(actions);
    (summary.actions || []).forEach(function (action) {
      addItem(actions, action);
    });
  }

  async function fetchLatest() {
    const response = await fetch('/api/summaries/latest', { cache: 'no-store' });
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return await response.json();
  }

  async function fetchSummary() {
    try {
      const body = await fetchLatest();
      render(body && body.summary ? body.summary : null);
      applyRefreshState(body.refresh);
    } catch (err) {
      render(null);
    }
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function pollAfterRefresh(previousGenerated) {
    stopPolling();
    let attempts = 0;
    pollTimer = setInterval(async function () {
      attempts += 1;
      try {
        const body = await fetchLatest();
        applyRefreshState(body.refresh);
        const summary = body && body.summary;
        if (summary && summary.generated_at !== previousGenerated) {
          render(summary);
          setStatus('Summary ready');
          stopPolling();
          return;
        }
        if ((!body.refresh || !body.refresh.busy) || attempts >= 18) {
          if (summary) render(summary);
          setStatus(attempts >= 18 ? 'Still generating - check shortly' : 'Summary ready');
          stopPolling();
        }
      } catch (err) {
        setStatus('Could not check summary status');
        stopPolling();
      }
    }, 5000);
  }

  async function triggerRefresh() {
    const button = $('summaryRefresh');
    if (!button || button.disabled) return;
    button.disabled = true;
    button.textContent = 'Generating...';
    setStatus('Starting local summary...');

    let previousGenerated = null;
    try {
      const latest = await fetchLatest();
      previousGenerated = latest && latest.summary ? latest.summary.generated_at : null;
      applyRefreshState(latest.refresh);
    } catch (err) {
      // A failed status read must not block the actual trigger.
    }

    try {
      const response = await fetch('/api/summaries/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}'
      });
      const body = await response.json().catch(() => ({}));
      applyRefreshState(body);
      if (response.ok) {
        setStatus('Generating locally with real sensor data...');
        pollAfterRefresh(previousGenerated);
      } else if (response.status === 429) {
        setStatus('Cooling down - try again in ' + formatCountdown(body.retry_after_seconds));
      } else if (response.status === 409) {
        setStatus('A summary is already being generated');
        pollAfterRefresh(previousGenerated);
      } else {
        setStatus('Could not start summary: ' + (body.message || body.error || 'error'));
      }
    } catch (err) {
      setStatus('Could not reach the summary API');
    } finally {
      updateButton();
    }
  }

  function init() {
    const button = $('summaryRefresh');
    if (button) button.addEventListener('click', triggerRefresh);
    setInterval(updateButton, 1000);
    updateButton();
  }

  window.fetchSummary = fetchSummary;
  window.triggerSummaryRefresh = triggerRefresh;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
