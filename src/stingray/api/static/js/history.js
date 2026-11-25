/**
 * history.js — /api/history fetch, metric/range state, stats display.
 *
 * Owns window.HIST state. Calls chart.js for rendering.
 */
'use strict';

const $ = id => document.getElementById(id);

const METRIC_META = {
  pm1_ug_m3: { label: 'PM1.0', unit: 'µg/m³', decimals: 1 },
  pm2_5_ug_m3: { label: 'PM2.5', unit: 'µg/m³', decimals: 1 },
  pm4_ug_m3: { label: 'PM4.0', unit: 'µg/m³', decimals: 1 },
  pm10_ug_m3: { label: 'PM10', unit: 'µg/m³', decimals: 1 },
  co2_ppm: { label: 'CO₂', unit: 'ppm', decimals: 0 },
  temperature_c: { label: 'Temperature', unit: '°C', decimals: 1 },
  relative_humidity_percent: { label: 'Humidity', unit: '%RH', decimals: 1 },
  voc_index: { label: 'VOC Index', unit: 'Index', decimals: 0 },
  nox_index: { label: 'NOx Index', unit: 'Index', decimals: 0 }
};

window.HIST = {
  metric: 'pm2_5_ug_m3', hours: 24, samples: [], label: 'PM2.5', unit: 'µg/m³', decimals: 1,
  stats: null, lastLoad: 0, error: '', requestSeq: 0,
  viewStart: 0, viewEnd: 1,
  dragging: false, dragStartX: 0, dragEndX: 0,
  hoverIdx: -1
};

window.updateHistoryControls = function() {
  document.querySelectorAll('#metricSelector button').forEach(b => {
    b.classList.toggle('active', b.dataset.metric === window.HIST.metric);
  });
  document.querySelectorAll('#rangeSelector button').forEach(b => {
    b.classList.toggle('active', Number(b.dataset.hours) === window.HIST.hours);
  });
  const meta = METRIC_META[window.HIST.metric] || { label: window.HIST.metric, unit: '', decimals: 1 };
  window.HIST.label = meta.label; window.HIST.unit = meta.unit; window.HIST.decimals = meta.decimals;
  const rangeLabel = window.HIST.hours <= 1 ? window.HIST.hours + ' hour' :
    window.HIST.hours < 24 ? window.HIST.hours + ' hours' : Math.round(window.HIST.hours / 24) + ' days';
  $('historyTitle').textContent = window.HIST.label + ' — last ' + rangeLabel;
};

window.updateHistoryStats = function() {
  const s = window.HIST.stats;
  if (!s) { $('hmin').textContent = '--'; $('havg').textContent = '--'; $('hmax').textContent = '--'; return; }
  $('hmin').textContent = fmtVal(s.min, window.HIST.decimals);
  $('havg').textContent = fmtVal(s.avg, window.HIST.decimals);
  $('hmax').textContent = fmtVal(s.max, window.HIST.decimals);
  $('hminunit').textContent = window.HIST.unit; $('havgunit').textContent = window.HIST.unit; $('hmaxunit').textContent = window.HIST.unit;
};

window.loadHistory = async function() {
  window.HIST.requestSeq++;
  const seq = window.HIST.requestSeq;
  try {
    const r = await fetch('/api/history?metric=' + window.HIST.metric + '&hours=' + window.HIST.hours, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (seq !== window.HIST.requestSeq) return;
    window.HIST.samples = d.samples || [];
    window.HIST.stats = d.stats || null;
    window.HIST.error = '';
    window.HIST.viewStart = 0; window.HIST.viewEnd = 1;
    updateHistoryStats(); drawHistory();
    $('historyMeta').textContent = d.returned + ' samples · ' + (d.hours || window.HIST.hours) + 'h range';
  } catch (e) {
    if (seq !== window.HIST.requestSeq) return;
    window.HIST.error = e.message;
    $('historyMeta').textContent = 'History unavailable: ' + e.message;
    drawHistory();
  }
};

window.selectMetric = function(metric) {
  window.HIST.metric = metric; window.HIST.samples = []; window.HIST.stats = null; window.HIST.error = '';
  window.HIST.viewStart = 0; window.HIST.viewEnd = 1;
  updateHistoryControls(); updateHistoryStats(); drawHistory(); loadHistory();
};

window.selectRange = function(hours) {
  window.HIST.hours = Number(hours); window.HIST.samples = []; window.HIST.stats = null; window.HIST.error = '';
  window.HIST.viewStart = 0; window.HIST.viewEnd = 1;
  updateHistoryControls(); drawHistory(); loadHistory();
};
