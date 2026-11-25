/**
 * app.js — Entry point: theme toggle, init, polling loops.
 *
 * Loads after chart.js, history.js, live.js.
 */
'use strict';

const $ = id => document.getElementById(id);

// Default to dark mode
document.documentElement.setAttribute('data-theme', 'dark');
$('theme').textContent = '☀️';

window.toggleTheme = function() {
  const h = document.documentElement;
  const dark = h.getAttribute('data-theme') === 'dark';
  h.setAttribute('data-theme', dark ? '' : 'dark');
  $('theme').textContent = dark ? '🌙' : '☀️';
  window.drawHistory();
};

// Init
window.updateHistoryControls();
window.fetchLive();
window.fetchSystem();
window.loadHistory();
window.setupChartInteractions();

// Polling
setInterval(window.fetchLive, 5000);
setInterval(window.fetchSystem, 30000);
setInterval(() => {
  const refresh = window.HIST.hours <= 6 ? 30000 : 300000;
  if (Date.now() - window.HIST.lastLoad >= refresh) {
    window.HIST.lastLoad = Date.now();
    window.loadHistory();
  }
}, 30000);

addEventListener('resize', window.drawHistory);
