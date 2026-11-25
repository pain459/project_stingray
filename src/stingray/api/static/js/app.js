/**
 * app.js — Entry point: theme toggle, init, polling loops.
 *
 * Loads after chart.js, history.js, live.js.
 */
'use strict';

const $ = id => document.getElementById(id);

function toggleTheme() {
  const h = document.documentElement;
  const dark = h.getAttribute('data-theme') === 'dark';
  h.setAttribute('data-theme', dark ? '' : 'dark');
  $('theme').textContent = dark ? '🌙' : '☀️';
  drawHistory();
}

// Init
updateHistoryControls();
fetchLive();
fetchSystem();
loadHistory();
setupChartInteractions();

// Polling
setInterval(fetchLive, 5000);
setInterval(fetchSystem, 30000);
setInterval(() => {
  const refresh = HIST.hours <= 6 ? 30000 : 300000;
  if (Date.now() - HIST.lastLoad >= refresh) {
    HIST.lastLoad = Date.now();
    loadHistory();
  }
}, 30000);

addEventListener('resize', drawHistory);
