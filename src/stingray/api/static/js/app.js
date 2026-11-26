/**
 * app.js — Entry point: theme toggle, init, polling loops.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts. Loads after chart.js, history.js, live.js.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  // Default to dark mode
  document.documentElement.setAttribute('data-theme', 'dark');
  const themeBtn = $('theme');
  if (themeBtn) themeBtn.textContent = '☀️';

  function toggleTheme() {
    const root = document.documentElement;
    const isDark = root.getAttribute('data-theme') === 'dark';
    root.setAttribute('data-theme', isDark ? 'light' : 'dark');
    if (themeBtn) themeBtn.textContent = isDark ? '🌙' : '☀️';
    window.drawHistory();
  }

  window.toggleTheme = toggleTheme;

  function start() {
    window.updateHistoryControls();
    window.fetchLive();
    window.fetchSystem();
    window.fetchSummary();
    window.loadHistory();
    window.setupChartInteractions();

    setInterval(window.fetchLive, 5000);
    setInterval(window.fetchSystem, 30000);
    setInterval(window.fetchSummary, 300000);

    // Refresh history on a slower cadence than live values.
    setInterval(function () {
      const refreshMs = window.HIST.hours <= 6 ? 30000 : 300000;
      if (Date.now() - window.HIST.lastLoad >= refreshMs) {
        window.HIST.lastLoad = Date.now();
        window.loadHistory();
      }
    }, 30000);

    window.addEventListener('resize', window.drawHistory);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
