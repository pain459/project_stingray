/**
 * history.js — /api/history fetch, metric/range state, stats display.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts. Owns window.HIST. Exposes:
 *   window.updateHistoryControls, window.updateHistoryStats,
 *   window.loadHistory, window.selectMetric, window.selectRange
 */
(function () {
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
    nox_index: { label: 'NOx Index', unit: 'Index', decimals: 0 },
    tps_um: { label: 'Typical particle size', unit: 'µm', decimals: 2 },
    cpu_temperature_c: { label: 'Pi CPU temperature', unit: '°C', decimals: 1 }
  };

  window.HIST = {
    metric: 'pm2_5_ug_m3', hours: 24, samples: [], label: 'PM2.5', unit: 'µg/m³', decimals: 1,
    stats: null, lastLoad: 0, error: '', requestSeq: 0,
    viewStart: 0, viewEnd: 1,
    dragging: false, dragStartX: 0, dragEndX: 0,
    hoverIdx: -1
  };

  function updateHistoryControls() {
    const H = window.HIST;
    document.querySelectorAll('#metricSelector button').forEach(b => {
      b.classList.toggle('active', b.dataset.metric === H.metric);
    });
    document.querySelectorAll('#rangeSelector button').forEach(b => {
      b.classList.toggle('active', Number(b.dataset.hours) === H.hours);
    });
    const meta = METRIC_META[H.metric] || { label: H.metric, unit: '', decimals: 1 };
    H.label = meta.label; H.unit = meta.unit; H.decimals = meta.decimals;
    const rangeLabel = H.hours <= 1 ? H.hours + ' hour' :
      H.hours < 24 ? H.hours + ' hours' : Math.round(H.hours / 24) + ' days';
    $('historyTitle').textContent = H.label + ' — last ' + rangeLabel;
  }

  function updateHistoryStats() {
    const H = window.HIST;
    const s = H.stats;
    if (!s) {
      $('hmin').textContent = '--'; $('havg').textContent = '--'; $('hmax').textContent = '--';
      return;
    }
    $('hmin').textContent = window.fmtVal(s.min, H.decimals);
    $('havg').textContent = window.fmtVal(s.avg, H.decimals);
    $('hmax').textContent = window.fmtVal(s.max, H.decimals);
    $('hminunit').textContent = H.unit;
    $('havgunit').textContent = H.unit;
    $('hmaxunit').textContent = H.unit;
  }

  async function loadHistory() {
    const H = window.HIST;
    H.requestSeq++;
    const seq = H.requestSeq;
    try {
      const r = await fetch('/api/history?metric=' + encodeURIComponent(H.metric) + '&hours=' + H.hours, { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      if (seq !== H.requestSeq) return;
      H.samples = d.samples || [];
      H.stats = d.stats || null;
      H.error = '';
      H.viewStart = 0; H.viewEnd = 1;
      updateHistoryStats();
      window.drawHistory();
      $('historyMeta').textContent = (d.returned || H.samples.length) + ' samples · ' + (d.hours || H.hours) + 'h range';
    } catch (e) {
      if (seq !== H.requestSeq) return;
      H.error = e.message;
      $('historyMeta').textContent = 'History unavailable: ' + e.message;
      window.drawHistory();
    }
  }

  function selectMetric(metric) {
    const H = window.HIST;
    H.metric = metric; H.samples = []; H.stats = null; H.error = '';
    H.viewStart = 0; H.viewEnd = 1;
    updateHistoryControls(); updateHistoryStats(); window.drawHistory(); loadHistory();
  }

  function selectRange(hours) {
    const H = window.HIST;
    H.hours = Number(hours); H.samples = []; H.stats = null; H.error = '';
    H.viewStart = 0; H.viewEnd = 1;
    updateHistoryControls(); window.drawHistory(); loadHistory();
  }

  window.updateHistoryControls = updateHistoryControls;
  window.updateHistoryStats = updateHistoryStats;
  window.loadHistory = loadHistory;
  window.selectMetric = selectMetric;
  window.selectRange = selectRange;
})();
