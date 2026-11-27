/**
 * app.js — Entry point: theme toggle and one-time widget setup.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts. The polling loop lives in store.js, not here.
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  const DATE_FORMAT = { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' };
  const TIME_FORMAT = { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false };
  let clockTimer = null;

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

  /** Browser-local clock; independent of snapshot revisions and device time. */
  function updateClock() {
    const el = $('dt');
    if (!el) return;
    const now = new Date();
    const next = now.toLocaleDateString([], DATE_FORMAT) + '  ' +
      now.toLocaleTimeString([], TIME_FORMAT);
    if (el.textContent !== next) el.textContent = next;
  }

  function startClock() {
    updateClock();
    if (clockTimer === null) clockTimer = window.setInterval(updateClock, 1000);
    document.addEventListener('visibilitychange', updateClock);
  }

  function start() {
    window.updateHistoryControls();
    window.setupChartInteractions();
    window.loadHistory();
    startClock();
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
