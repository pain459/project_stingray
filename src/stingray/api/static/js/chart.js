/**
 * chart.js — Canvas chart rendering with zoom, drag, hover, tooltip.
 *
 * Wrapped in an IIFE so its top-level declarations do not collide with the
 * other dashboard scripts (classic scripts share one global lexical scope,
 * so a second `const $` would be a fatal redeclaration error).
 *
 * Reads shared state from window.HIST. Exposes:
 *   window.drawHistory, window.setupChartInteractions,
 *   window.resetZoom, window.zoomTo
 */
(function () {
  'use strict';

  const $ = id => document.getElementById(id);
  // Draw offscreen and blit once so a refresh never exposes a blank canvas.
  const buffer = document.createElement('canvas');
  const bufferCtx = buffer.getContext('2d');

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#667085';
  }

  function niceNum(v, round) {
    const exp = Math.floor(Math.log10(Math.abs(v) || 1));
    const f = v / Math.pow(10, exp);
    let nf;
    if (round) { nf = f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10; }
    else { nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10; }
    return nf * Math.pow(10, exp);
  }

  function niceTicks(min, max, maxTicks) {
    if (max - min < 1e-9) max = min + 1;
    const range = niceNum(max - min, false);
    const step = niceNum(range / (maxTicks - 1), true);
    const niceMin = Math.floor(min / step) * step;
    const niceMax = Math.ceil(max / step) * step;
    const ticks = [];
    for (let v = niceMin; v <= niceMax + step * 0.5; v += step)
      ticks.push(Math.round(v * 1e6) / 1e6);
    return { ticks, step };
  }

  function fmtTime(epoch, rangeHours) {
    const d = new Date(epoch * 1000);
    if (rangeHours <= 6) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
    } else if (rangeHours <= 24) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    } else if (rangeHours <= 168) {
      return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' }) + ' ' +
             d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }
    return d.toLocaleDateString([], { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function getVisibleSamples() {
    const H = window.HIST;
    const pts = H.samples;
    if (!pts || pts.length < 2) return [];
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const total = t1 - t0;
    if (total <= 0) return pts;
    const vs = t0 + total * H.viewStart;
    const ve = t0 + total * H.viewEnd;
    const vis = [];
    for (const p of pts) {
      if (p[0] >= vs && p[0] <= ve) vis.push(p);
    }
    return vis;
  }

  function drawHistory() {
    const H = window.HIST;
    const canvas = $('historyChart');
    if (!canvas) return;
    const wrap = canvas.parentElement;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(wrap.offsetWidth * dpr));
    const h = Math.max(1, Math.round(wrap.offsetHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    buffer.width = w;
    buffer.height = h;
    const ctx = bufferCtx;
    ctx.clearRect(0, 0, w, h);

    function present() {
      const visible = canvas.getContext('2d');
      visible.clearRect(0, 0, w, h);
      visible.drawImage(buffer, 0, 0);
    }

    const pts = getVisibleSamples();
    if (pts.length < 2) {
      ctx.fillStyle = cssVar('--sec');
      ctx.font = '14px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(H.error || 'No data yet', w / 2, h / 2);
      present();
      return;
    }

    const padL = 55 * dpr, padR = 15 * dpr, padT = 15 * dpr, padB = 35 * dpr;
    const plotW = w - padL - padR, plotH = h - padT - padB;

    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const total = t1 - t0 || 1;
    const vals = pts.map(p => p[1]);
    let vMin = Math.min(...vals), vMax = Math.max(...vals);
    const vRange = vMax - vMin || 1;
    vMin -= vRange * 0.05; vMax += vRange * 0.05;

    const yTicks = niceTicks(vMin, vMax, 5);
    const xTicks = niceTicks(t0, t1, 6);

    // Grid
    ctx.strokeStyle = cssVar('--grid');
    ctx.lineWidth = 1;
    for (const tv of yTicks.ticks) {
      const y = padT + plotH * (1 - (tv - vMin) / (vMax - vMin));
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    }
    for (const tx of xTicks.ticks) {
      const x = padL + plotW * ((tx - t0) / total);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, h - padB); ctx.stroke();
    }

    // Y-axis labels
    ctx.fillStyle = cssVar('--sec');
    ctx.font = '11px system-ui';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (const tv of yTicks.ticks) {
      const y = padT + plotH * (1 - (tv - vMin) / (vMax - vMin));
      ctx.fillText(tv.toFixed(H.decimals > 0 ? 1 : 0), padL - 6 * dpr, y);
    }

    // X-axis labels
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (const tx of xTicks.ticks) {
      const x = padL + plotW * ((tx - t0) / total);
      ctx.fillText(fmtTime(tx, H.hours), x, h - padB + 6 * dpr);
    }

    // Line
    ctx.beginPath();
    pts.forEach((p, i) => {
      const x = padL + plotW * ((p[0] - t0) / total);
      const y = padT + plotH * (1 - (p[1] - vMin) / (vMax - vMin));
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = cssVar('--accent');
    ctx.lineWidth = 2 * dpr;
    ctx.stroke();

    // Fill under the line
    ctx.lineTo(padL + plotW, h - padB);
    ctx.lineTo(padL, padT + plotH);
    ctx.closePath();
    ctx.fillStyle = cssVar('--accent') + '18';
    ctx.fill();

    // Drag selection rectangle
    if (H.dragging) {
      const x1 = Math.min(H.dragStartX, H.dragEndX) * dpr;
      const x2 = Math.max(H.dragStartX, H.dragEndX) * dpr;
      ctx.fillStyle = 'rgba(100,150,255,0.15)';
      ctx.fillRect(x1, padT, x2 - x1, plotH);
      ctx.strokeStyle = 'rgba(100,150,255,0.5)';
      ctx.lineWidth = 1;
      ctx.strokeRect(x1, padT, x2 - x1, plotH);
    }

    // Hover crosshair + dot
    if (H.hoverIdx >= 0 && H.hoverIdx < pts.length) {
      const p = pts[H.hoverIdx];
      const x = padL + plotW * ((p[0] - t0) / total);
      const y = padT + plotH * (1 - (p[1] - vMin) / (vMax - vMin));
      ctx.strokeStyle = cssVar('--sec') + '60';
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, h - padB); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(x, y, 5 * dpr, 0, Math.PI * 2);
      ctx.fillStyle = cssVar('--accent'); ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 2 * dpr; ctx.stroke();
    }
    present();
  }

  function showTooltip(clientX, clientY, epoch, value) {
    const H = window.HIST;
    const tt = $('chartTooltip');
    const wrap = $('historyChart').parentElement;
    const rect = wrap.getBoundingClientRect();
    const x = clientX - rect.left + 12;
    const y = clientY - rect.top - 10;
    tt.style.display = 'block';
    tt.style.left = Math.min(x, rect.width - 150) + 'px';
    tt.style.top = Math.max(0, y) + 'px';
    tt.innerHTML = '<div class="tt-time">' + fmtTime(epoch, H.hours) + '</div>' +
      '<div class="tt-val">' + window.fmtVal(value, H.decimals) +
      ' <span class="tt-unit">' + H.unit + '</span></div>';
  }

  function hideTooltip() {
    $('chartTooltip').style.display = 'none';
  }

  function resetZoom() {
    window.HIST.viewStart = 0;
    window.HIST.viewEnd = 1;
    drawHistory();
  }

  function zoomTo(fracStart, fracEnd) {
    if (fracEnd - fracStart < 0.02) return;
    window.HIST.viewStart = Math.max(0, fracStart);
    window.HIST.viewEnd = Math.min(1, fracEnd);
    drawHistory();
  }

  function setupChartInteractions() {
    const canvas = $('historyChart');
    if (!canvas) return;

    canvas.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const rect = canvas.getBoundingClientRect();
      window.HIST.dragging = true;
      window.HIST.dragStartX = e.clientX - rect.left;
      window.HIST.dragEndX = e.clientX - rect.left;
      hideTooltip();
    });

    canvas.addEventListener('mousemove', e => {
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;

      if (window.HIST.dragging) {
        window.HIST.dragEndX = mx;
        drawHistory();
        return;
      }

      const pts = getVisibleSamples();
      if (pts.length < 2) return;
      const dpr = window.devicePixelRatio || 1;
      const padL = 55 * dpr, padR = 15 * dpr;
      const plotW = canvas.width - padL - padR;
      const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
      const total = t1 - t0 || 1;

      const frac = (mx * dpr - padL) / plotW;
      if (frac < 0 || frac > 1) { hideTooltip(); window.HIST.hoverIdx = -1; drawHistory(); return; }

      const targetT = t0 + total * frac;
      let best = 0, bestD = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const d = Math.abs(pts[i][0] - targetT);
        if (d < bestD) { bestD = d; best = i; }
      }
      window.HIST.hoverIdx = best;
      drawHistory();
      showTooltip(e.clientX, e.clientY, pts[best][0], pts[best][1]);
    });

    canvas.addEventListener('mouseup', () => {
      if (!window.HIST.dragging) return;
      window.HIST.dragging = false;
      const x1 = Math.min(window.HIST.dragStartX, window.HIST.dragEndX);
      const x2 = Math.max(window.HIST.dragStartX, window.HIST.dragEndX);
      if (x2 - x1 < 10) { resetZoom(); return; }
      const dpr = window.devicePixelRatio || 1;
      const padL = 55 * dpr, padR = 15 * dpr;
      const plotW = canvas.width - padL - padR;
      const f1 = Math.max(0, (x1 * dpr - padL) / plotW);
      const f2 = Math.min(1, (x2 * dpr - padL) / plotW);
      zoomTo(f1, f2);
    });

    canvas.addEventListener('mouseleave', () => {
      window.HIST.dragging = false;
      window.HIST.hoverIdx = -1;
      hideTooltip();
      drawHistory();
    });

    canvas.addEventListener('dblclick', resetZoom);

    canvas.addEventListener('wheel', e => {
      e.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const dpr = window.devicePixelRatio || 1;
      const padL = 55 * dpr, padR = 15 * dpr;
      const plotW = canvas.width - padL - padR;
      const centerFrac = Math.max(0, Math.min(1, (mx * dpr - padL) / plotW));
      const zoomAmt = e.deltaY > 0 ? 1.2 : 0.8;
      const span = window.HIST.viewEnd - window.HIST.viewStart;
      const newSpan = Math.min(1, Math.max(0.02, span * zoomAmt));
      const center = window.HIST.viewStart + span * centerFrac;
      let ns = center - newSpan * centerFrac;
      let ne = center + newSpan * (1 - centerFrac);
      if (ns < 0) { ne -= ns; ns = 0; }
      if (ne > 1) { ns -= ne - 1; ne = 1; }
      window.HIST.viewStart = Math.max(0, ns);
      window.HIST.viewEnd = Math.min(1, ne);
      drawHistory();
    }, { passive: false });
  }

  window.drawHistory = drawHistory;
  window.setupChartInteractions = setupChartInteractions;
  window.resetZoom = resetZoom;
  window.zoomTo = zoomTo;
})();
