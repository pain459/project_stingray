/**
 * summary.js — Fetch and render the latest hourly local-LLM summary.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  const LABELS = {
    pm2_5_ug_m3: 'PM2.5', co2_ppm: 'CO2', voc_index: 'VOC', nox_index: 'NOx',
    temperature_c: 'Temperature', relative_humidity_percent: 'Humidity', tps_um: 'TPS'
  };

  function formatTime(iso) {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? 'unknown' : d.toLocaleString();
  }

  function render(summary) {
    const meta = $('summaryMeta');
    const headline = $('summaryHeadline');
    const text = $('summaryText');
    const compare = $('summaryCompare');
    const actions = $('summaryActions');
    if (!meta || !headline || !text || !compare || !actions) return;

    if (!summary) {
      meta.textContent = 'Waiting for the first hourly summary...';
      headline.textContent = '--';
      text.textContent = 'No summary has been generated yet.';
      compare.textContent = '';
      actions.replaceChildren();
      return;
    }

    const model = summary.model || 'unknown model';
    const suffix = summary.model_used ? '' : ' (deterministic fallback)';
    meta.textContent = `${formatTime(summary.generated_at)} · ${model}${suffix}`;
    headline.textContent = summary.headline || '--';
    text.textContent = summary.text || '';

    compare.textContent = '';
    const metrics = (summary.facts && summary.facts.metrics) || {};
    const parts = [];
    Object.keys(LABELS).forEach(function (key) {
      const m = metrics[key];
      if (!m || m.previous_avg == null || m.change_vs_previous_pct == null) return;
      const pct = Number(m.change_vs_previous_pct);
      const sign = pct > 0 ? '+' : '';
      parts.push(
        `${LABELS[key]}: ${sign}${pct.toFixed(0)}% vs last hour ` +
        `(${Number(m.previous_avg).toFixed(1)} to ${Number(m.avg).toFixed(1)} ${m.unit || ''})`.trim()
      );
    });
    if (parts.length) compare.textContent = 'Compared with previous hour - ' + parts.join(' | ');

    actions.replaceChildren();
    (summary.actions || []).forEach(function (action) {
      const li = document.createElement('li');
      li.textContent = action;
      actions.appendChild(li);
    });
  }

  async function fetchSummary() {
    try {
      const response = await fetch('/api/summaries/latest', { cache: 'no-store' });
      if (!response.ok) throw new Error('HTTP ' + response.status);
      const body = await response.json();
      render(body && body.summary ? body.summary : null);
    } catch (err) {
      render(null);
    }
  }

  window.fetchSummary = fetchSummary;
})();
