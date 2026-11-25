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
    b.classList.toggle('active', b.dataset.metric === HIST.metric);
  });
  document.querySelectorAll('#rangeSelector button').forEach(b => {
    b.classList.toggle('active', Number(b.dataset.hours) === HIST.hours);
  });
  const meta = METRIC_META[HIST.metric] || { label: HIST.metric, unit: '', decimals: 1 };
  HIST.label = meta.label; HIST.unit = meta.unit; HIST.decimals = meta.decimals;
  const rangeLabel = HIST.hours <= 1 ? HIST.hours + ' hour' :
    HIST.hours < 24 ? HIST.hours + ' hours' : Math.round(HIST.hours / 24) + ' days';
  $('historyTitle').textContent = HIST.label + ' — last ' + rangeLabel;
};

window.updateHistoryStats = function() {
  const s = HIST.stats;
  if (!s) { $('hmin').textContent = '--'; $('havg').textContent = '--'; $('hmax').textContent = '--'; return; }
  $('hmin').textContent = fmtVal(s.min, HIST.decimals);
  $('havg').textContent = fmtVal(s.avg, HIST.decimals);
  $('hmax').textContent = fmtVal(s.max, HIST.decimals);
  $('hminunit').textContent = HIST.unit; $('havgunit').textContent = HIST.unit; $('hmaxunit').textContent = HIST.unit;
};

window.loadHistory = async function() {
  HIST.requestSeq++;
  const seq = HIST.requestSeq;
  try {
    const r = await fetch('/api/history?metric=' + HIST.metric + '&hours=' + HIST.hours, { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    if (seq !== HIST.requestSeq) return;
    HIST.samples = d.samples || [];
    HIST.stats = d.stats || null;
    HIST.error = '';
    HIST.viewStart = 0; HIST.viewEnd = 1;
    updateHistoryStats(); drawHistory();
    $('historyMeta').textContent = d.returned + ' samples · ' + (d.hours || HIST.hours) + 'h range';
  } catch (e) {
    if (seq !== HIST.requestSeq) return;
    HIST.error = e.message;
    $('historyMeta').textContent = 'History unavailable: ' + e.message;
    drawHistory();
  }
};

window.selectMetric = function(metric) {
  HIST.metric = metric; HIST.samples = []; HIST.stats = null; HIST.error = '';
  HIST.viewStart = 0; HIST.viewEnd = 1;
  updateHistoryControls(); updateHistoryStats(); drawHistory(); loadHistory();
};

window.selectRange = function(hours) {
  HIST.hours = Number(hours); HIST.samples = []; HIST.stats = null; HIST.error = '';
  HIST.viewStart = 0; HIST.viewEnd = 1;
  updateHistoryControls(); drawHistory(); loadHistory();
};
