/**
 * summary.js — Fetch and render the latest hourly local-LLM summary.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  function formatTime(iso) {
    if (!iso) return 'unknown';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? 'unknown' : d.toLocaleString();
  }

  function render(summary) {
    const meta = $('summaryMeta');
    const headline = $('summaryHeadline');
    const text = $('summaryText');
    const actions = $('summaryActions');
    if (!meta || !headline || !text || !actions) return;

    if (!summary) {
      meta.textContent = 'Waiting for the first hourly summary...';
      headline.textContent = '--';
      text.textContent = 'No summary has been generated yet.';
      actions.replaceChildren();
      return;
    }

    const model = summary.model || 'unknown model';
    const suffix = summary.model_used ? '' : ' (deterministic fallback)';
    meta.textContent = `${formatTime(summary.generated_at)} · ${model}${suffix}`;
    headline.textContent = summary.headline || '--';
    text.textContent = summary.text || '';
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
