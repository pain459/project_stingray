/**
 * live.js — /api/live and /api/health fetch, DOM updates for live readings.
 */
'use strict';

const $ = id => document.getElementById(id);

window.fmtVal = function(v, dec) {
  if (v == null || isNaN(v)) return '--';
  return v.toFixed(dec);
};

window.fmtUptime = function(s) {
  if (s == null) return '--';
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  if (h > 0) return h + 'd ' + m + 'h';
  if (m > 0) return m + 'h ' + sec + 'm';
  return sec + 's';
};

window.fetchLive = async function() {
  try {
    const r = await fetch('/api/live', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    const lm = {};
    for (const row of d.live) { lm[row.sensor + ':' + row.metric] = row; }

    const p25 = lm['sps30:pm2_5_ug_m3'];
    $('p25').textContent = p25 ? p25.value.toFixed(1) : '--';
    const co2 = lm['scd4x:co2_ppm'];
    $('co2').textContent = co2 ? co2.value : '--';

    const p1 = lm['sps30:pm1_ug_m3']; $('p1').textContent = p1 ? p1.value.toFixed(1) : '--';
    const p10 = lm['sps30:pm10_ug_m3']; $('p10').textContent = p10 ? p10.value.toFixed(1) : '--';
    const p4 = lm['sps30:pm4_ug_m3']; $('p4').textContent = p4 ? p4.value.toFixed(1) : '--';

    const temp = lm['sht45:temperature_c']; $('temp').textContent = temp ? temp.value.toFixed(1) : '--';
    const hum = lm['sht45:relative_humidity_percent']; $('hum').textContent = hum ? hum.value.toFixed(1) : '--';

    const voc = lm['sgp41:voc_index']; $('voc').textContent = voc ? voc.value : '--';
    $('voc2').textContent = voc ? voc.value : '--';
    const nox = lm['sgp41:nox_index']; $('nox').textContent = nox ? nox.value : '--';
    const vocraw = lm['sgp41:voc_raw']; $('vocraw').textContent = vocraw ? vocraw.value : '--';
    const noxraw = lm['sgp41:nox_raw']; $('noxraw').textContent = noxraw ? noxraw.value : '--';

    const n1 = lm['sps30:pm1_num_m3']; $('n1').textContent = n1 ? n1.value.toFixed(1) : '--';
    const n25 = lm['sps30:pm2_5_num_m3']; $('n25').textContent = n25 ? n25.value.toFixed(1) : '--';
    const n4 = lm['sps30:pm4_num_m3']; $('n4').textContent = n4 ? n4.value.toFixed(1) : '--';
    const n10 = lm['sps30:pm10_num_m3']; $('n10').textContent = n10 ? n10.value.toFixed(1) : '--';

    const h = d.health || {};
    const setStat = (id, state) => {
      const el = $(id);
      if (!state) { el.textContent = '--'; el.className = 'status'; return; }
      const s = state.state;
      el.textContent = s === 'ready' ? 'Online' : s === 'degraded' ? 'Degraded' : s.toUpperCase();
      el.className = 'status ' + (s === 'ready' ? 'good' : s === 'degraded' ? 'warn' : 'bad');
    };
    setStat('spsstatus', h.sps30);
    setStat('scdstatus', h.scd4x);
    setStat('shtstatus', h.sht45);
    setStat('sgpstatus', h.sgp41);
    $('total').textContent = d.total;
  } catch (e) { console.error('Live API error:', e); }
};

window.fetchSystem = async function() {
  try {
    const r = await fetch('/api/health', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    $('up').textContent = window.fmtUptime(d.uptime_seconds);
    $('mode').textContent = d.mode;
    const dt = new Date(d.time);
    $('dt').textContent = dt.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) +
      '  ' + dt.toLocaleTimeString([], { hour12: false });
  } catch (e) { console.error('System API error:', e); }
};
