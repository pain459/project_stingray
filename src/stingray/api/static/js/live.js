/**
 * live.js — /api/live and /api/health fetch, DOM updates for live readings.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts. Exposes:
 *   window.fmtVal, window.fmtUptime, window.fetchLive, window.fetchSystem
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);

  function setText(id, value) {
    const el = $(id);
    if (el) el.textContent = value;
  }

  function fmtVal(v, dec) {
    if (v == null || isNaN(v)) return '--';
    return Number(v).toFixed(dec);
  }

  function fmtUptime(s) {
    if (s == null) return '--';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
    if (h > 0) return h + 'h ' + m + 'm';
    if (m > 0) return m + 'm ' + sec + 's';
    return sec + 's';
  }

  async function fetchLive() {
    try {
      const r = await fetch('/api/live', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      const lm = {};
      for (const row of (d.live || [])) { lm[row.sensor + ':' + row.metric] = row; }

      const p25 = lm['sps30:pm2_5_ug_m3'];
      setText('p25', p25 ? fmtVal(p25.value, 1) : '--');
      const co2 = lm['scd4x:co2_ppm'];
      setText('co2', co2 ? fmtVal(co2.value, 0) : '--');

      const p1 = lm['sps30:pm1_ug_m3']; setText('p1', p1 ? fmtVal(p1.value, 1) : '--');
      const p10 = lm['sps30:pm10_ug_m3']; setText('p10', p10 ? fmtVal(p10.value, 1) : '--');
      const p4 = lm['sps30:pm4_ug_m3']; setText('p4', p4 ? fmtVal(p4.value, 1) : '--');

      const temp = lm['sht45:temperature_c']; setText('temp', temp ? fmtVal(temp.value, 1) : '--');
      const hum = lm['sht45:relative_humidity_percent']; setText('hum', hum ? fmtVal(hum.value, 1) : '--');

      const voc = lm['sgp41:voc_index'];
      setText('voc', voc ? fmtVal(voc.value, 0) : '--');
      setText('voc2', voc ? fmtVal(voc.value, 0) : '--');
      const nox = lm['sgp41:nox_index']; setText('nox', nox ? fmtVal(nox.value, 0) : '--');
      const vocraw = lm['sgp41:voc_raw']; setText('vocraw', vocraw ? fmtVal(vocraw.value, 0) : '--');
      const noxraw = lm['sgp41:nox_raw']; setText('noxraw', noxraw ? fmtVal(noxraw.value, 0) : '--');

      const n1 = lm['sps30:pm1_num_m3']; setText('n1', n1 ? fmtVal(n1.value, 1) : '--');
      const n25 = lm['sps30:pm2_5_num_m3']; setText('n25', n25 ? fmtVal(n25.value, 1) : '--');
      const n4 = lm['sps30:pm4_num_m3']; setText('n4', n4 ? fmtVal(n4.value, 1) : '--');
      const n10 = lm['sps30:pm10_num_m3']; setText('n10', n10 ? fmtVal(n10.value, 1) : '--');
      const tps = lm['sps30:tps_um']; setText('tps', tps ? fmtVal(tps.value, 2) + ' µm' : '--');

      const net = d.network || {};
      setText('netif', net.interface || '--');
      setText('ipaddr', net.ip_address || '--');
      setText('wifirssi', net.wifi_rssi == null ? '--' : net.wifi_rssi + ' dBm');

      const h = d.health || {};
      const setStat = (id, state) => {
        const el = $(id);
        if (!el) return;
        if (!state) { el.textContent = '--'; el.className = 'status'; return; }
        const s = state.state;
        el.textContent = s === 'ready' ? 'Online' : s === 'degraded' ? 'Degraded' : String(s).toUpperCase();
        el.className = 'status ' + (s === 'ready' ? 'good' : s === 'degraded' ? 'warn' : 'bad');
      };
      setStat('spsstatus', h.sps30);
      setStat('scdstatus', h.scd4x);
      setStat('shtstatus', h.sht45);
      setStat('sgpstatus', h.sgp41);

      if (d.total != null) setText('total', d.total);
    } catch (e) {
      console.error('Live API error:', e);
    }
  }

  async function fetchSystem() {
    try {
      const r = await fetch('/api/health', { cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      setText('up', fmtUptime(d.uptime_seconds));
      setText('mode', d.mode || '--');
      const dt = new Date(d.time);
      setText('dt', dt.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }) +
        '  ' + dt.toLocaleTimeString([], { hour12: false }));
    } catch (e) {
      console.error('System API error:', e);
    }
  }

  window.fmtVal = fmtVal;
  window.fmtUptime = fmtUptime;
  window.fetchLive = fetchLive;
  window.fetchSystem = fetchSystem;
})();
