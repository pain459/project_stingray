/**
 * app.js — Entry point: theme toggle and one-time widget setup.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts. The polling loop lives in store.js, not here.
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
    window.setupChartInteractions();
    window.loadHistory();
    // One store owns the single poll loop; widgets only subscribe to it.
    window.DP.start();
    window.addEventListener('resize', window.drawHistory);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
