/* ==========================================================================
   Motor de gráficos en Canvas 2D (Vanilla JS, sin dependencias externas)
   - Series temporales con banda verde de rango óptimo, franja diurna,
     movimientos de mecanismos, triángulos (ALERTA) y círculos (TRANSITORIA)
   - Comparador de curvas entre dos jornadas
   - Gráfico de barras de horas críticas
   ========================================================================== */

(function () {
  const tooltip = () => document.getElementById('tooltip');

  function prepCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const w = Math.max(260, Math.floor(rect.width));
    const h = Math.max(150, Math.floor(rect.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  function fmtHoraMs(ms) {
    const d = new Date(ms);
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mm = String(d.getUTCMinutes()).padStart(2, '0');
    return `${hh}:${mm}`;
  }

  function parseMinutoHora(str) {
    if (!str) return 0;
    const [h, m] = str.split(':').map(Number);
    return h * 60 + (m || 0);
  }

  function drawTriangle(ctx, x, y, r, up) {
    ctx.beginPath();
    if (up) {
      ctx.moveTo(x, y - r);
      ctx.lineTo(x - r, y + r);
      ctx.lineTo(x + r, y + r);
    } else {
      ctx.moveTo(x, y + r);
      ctx.lineTo(x - r, y - r);
      ctx.lineTo(x + r, y - r);
    }
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }

  /* ------------------------------------------------------------------------
     1. Serie temporal (Monitor en Vivo)
     ------------------------------------------------------------------------ */
  function drawTimeSeries(canvas, opts) {
    if (!canvas) return;
    const { ctx, w, h } = prepCanvas(canvas);
    const pad = { l: 48, r: 16, t: 22, b: 26 };
    const pw = w - pad.l - pad.r;
    const ph = h - pad.t - pad.b;

    const serie = opts.serie || [];
    const tMin = opts.desde || (serie[0] && serie[0][0]) || Date.now() - 3600000;
    const tMax = opts.hasta || (serie[serie.length - 1] && serie[serie.length - 1][0]) || Date.now();
    const dt = Math.max(1, tMax - tMin);

    const vals = serie.map(p => p[1]);
    let yMin = Math.min(opts.yMin ?? 0, opts.optMin ?? Infinity, ...(vals.length ? vals : [0]));
    let yMax = Math.max(opts.yMax ?? 100, opts.optMax ?? -Infinity, ...(vals.length ? vals : [100]));
    const span = Math.max(1, yMax - yMin);
    yMin = Math.max(opts.clampZero ? 0 : -10, Math.floor((yMin - span * 0.08) / 5) * 5);
    yMax = Math.ceil((yMax + span * 0.08) / 5) * 5;

    const xOf = t => pad.l + ((t - tMin) / dt) * pw;
    const yOf = v => pad.t + (1 - (v - yMin) / Math.max(1e-6, yMax - yMin)) * ph;

    // Título interno
    ctx.fillStyle = '#1B3B22';
    ctx.font = '600 12px system-ui, sans-serif';
    ctx.fillText(opts.titulo || '', pad.l, 14);

    // Franja diurna (para radiación)
    if (opts.diurnaInicio && opts.diurnaFin) {
      const mIni = parseMinutoHora(opts.diurnaInicio);
      const mFin = parseMinutoHora(opts.diurnaFin);
      const dayMs = 86400000;
      const baseDay = Math.floor(tMin / dayMs) * dayMs;
      ctx.fillStyle = 'rgba(250, 204, 21, 0.13)';
      for (let d = baseDay - dayMs; d <= tMax + dayMs; d += dayMs) {
        const a = Math.max(tMin, d + mIni * 60000);
        const b = Math.min(tMax, d + mFin * 60000);
        if (b > a) {
          ctx.fillRect(xOf(a), pad.t, xOf(b) - xOf(a), ph);
        }
      }
    }

    // Banda verde de rango óptimo
    if (opts.optMin != null && opts.optMax != null) {
      const y1 = Math.max(pad.t, Math.min(pad.t + ph, yOf(opts.optMax)));
      const y2 = Math.max(pad.t, Math.min(pad.t + ph, yOf(opts.optMin)));
      ctx.fillStyle = 'rgba(138, 184, 46, 0.22)';
      ctx.fillRect(pad.l, y1, pw, Math.max(0, y2 - y1));
      ctx.strokeStyle = 'rgba(116, 158, 34, 0.85)';
      ctx.setLineDash([4, 3]);
      ctx.lineWidth = 1;
      [y1, y2].forEach(y => {
        ctx.beginPath();
        ctx.moveTo(pad.l, y);
        ctx.lineTo(pad.l + pw, y);
        ctx.stroke();
      });
      ctx.setLineDash([]);
    }

    // Grilla horizontal y eje Y
    ctx.strokeStyle = '#E4ECE4';
    ctx.fillStyle = '#5A6E5E';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    const steps = 4;
    for (let i = 0; i <= steps; i++) {
      const v = yMin + ((yMax - yMin) * i) / steps;
      const y = yOf(v);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + pw, y);
      ctx.stroke();
      ctx.fillText(opts.decimales ? v.toFixed(1) : Math.round(v), pad.l - 6, y + 3);
    }

    // Eje X (horas)
    ctx.textAlign = 'center';
    const ticksX = 6;
    for (let i = 0; i <= ticksX; i++) {
      const t = tMin + (dt * i) / ticksX;
      const x = xOf(t);
      ctx.fillText(fmtHoraMs(t), x, h - 7);
    }

    // Movimientos de mecanismos (líneas verticales celestes)
    (opts.movimientos || []).forEach(m => {
      if (m.t < tMin || m.t > tMax) return;
      const x = xOf(m.t);
      ctx.strokeStyle = 'rgba(2, 132, 199, 0.55)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, pad.t);
      ctx.lineTo(x, pad.t + ph);
      ctx.stroke();
      ctx.setLineDash([]);
    });

    // Curva principal
    if (serie.length > 1) {
      ctx.strokeStyle = opts.color || '#1B3B22';
      ctx.lineWidth = 2;
      ctx.beginPath();
      serie.forEach((p, i) => {
        const x = xOf(p[0]);
        const y = Math.max(pad.t, Math.min(pad.t + ph, yOf(p[1])));
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    // Marcadores: Triángulo = ALERTA, Círculo = TRANSITORIA
    const hitPoints = [];
    (opts.marcas || []).forEach(m => {
      if (m.t < tMin || m.t > tMax) return;
      const x = xOf(m.t);
      const y = Math.max(pad.t + 6, Math.min(pad.t + ph - 6, yOf(m.valor)));
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = '#FFFFFF';
      if (m.tipo === 'ALERTA') {
        ctx.fillStyle = '#B91C1C';
        drawTriangle(ctx, x, y, 6.5, m.valor >= (opts.optMax ?? 0));
      } else {
        ctx.fillStyle = '#D97706';
        ctx.beginPath();
        ctx.arc(x, y, 5.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
      hitPoints.push({ x, y, label: `${fmtHoraMs(m.t)} · ${m.valor} ${opts.unidad || ''} · ${m.texto}` });
    });

    // Tooltip interactivo
    canvas._chartMeta = { serie, xOf, yOf, pad, pw, ph, tMin, dt, opts, hitPoints };
    if (!canvas._boundHover) {
      canvas._boundHover = true;
      canvas.addEventListener('mousemove', e => {
        const meta = canvas._chartMeta;
        const tip = tooltip();
        if (!meta || !tip) return;
        const r = canvas.getBoundingClientRect();
        const mx = e.clientX - r.left;
        const my = e.clientY - r.top;
        for (const hp of meta.hitPoints) {
          if (Math.hypot(mx - hp.x, my - hp.y) < 10) {
            tip.textContent = hp.label;
            tip.hidden = false;
            tip.style.left = `${e.clientX + 12}px`;
            tip.style.top = `${e.clientY - 10}px`;
            return;
          }
        }
        if (mx < meta.pad.l || mx > meta.pad.l + meta.pw || !meta.serie.length) {
          tip.hidden = true;
          return;
        }
        const tTarget = meta.tMin + ((mx - meta.pad.l) / meta.pw) * meta.dt;
        let best = meta.serie[0];
        let bestDiff = Math.abs(best[0] - tTarget);
        for (const p of meta.serie) {
          const d = Math.abs(p[0] - tTarget);
          if (d < bestDiff) { best = p; bestDiff = d; }
        }
        tip.textContent = `${fmtHoraMs(best[0])} → ${best[1]} ${meta.opts.unidad || ''}`;
        tip.hidden = false;
        tip.style.left = `${e.clientX + 12}px`;
        tip.style.top = `${e.clientY - 10}px`;
      });
      canvas.addEventListener('mouseleave', () => {
        const tip = tooltip();
        if (tip) tip.hidden = true;
      });
    }
  }

  /* ------------------------------------------------------------------------
     2. Comparador de curvas entre dos jornadas (00:00 a 24:00)
     ------------------------------------------------------------------------ */
  function drawComparison(canvas, jornadas, parametros) {
    if (!canvas) return;
    const { ctx, w, h } = prepCanvas(canvas);
    const pad = { l: 46, r: 18, t: 26, b: 28 };
    const pw = w - pad.l - pad.r;
    const ph = h - pad.t - pad.b;

    const allVals = [];
    (jornadas || []).forEach(j => (j.serie || []).forEach(p => allVals.push(p[1])));
    const yMin = Math.floor(Math.min(parametros.temp_min - 2, ...(allVals.length ? allVals : [5])));
    const yMax = Math.ceil(Math.max(parametros.temp_max + 2, ...(allVals.length ? allVals : [30])));
    const xOf = m => pad.l + (m / 1440) * pw;
    const yOf = v => pad.t + (1 - (v - yMin) / Math.max(1, yMax - yMin)) * ph;

    // Banda óptima
    const y1 = yOf(parametros.temp_max);
    const y2 = yOf(parametros.temp_min);
    ctx.fillStyle = 'rgba(138, 184, 46, 0.20)';
    ctx.fillRect(pad.l, y1, pw, Math.max(0, y2 - y1));

    // Grilla
    ctx.strokeStyle = '#E4ECE4';
    ctx.fillStyle = '#5A6E5E';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 5; i++) {
      const v = yMin + ((yMax - yMin) * i) / 5;
      const y = yOf(v);
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + pw, y);
      ctx.stroke();
      ctx.fillText(`${v.toFixed(0)}°`, pad.l - 6, y + 3);
    }

    ctx.textAlign = 'center';
    for (let hh = 0; hh <= 24; hh += 3) {
      const x = xOf(hh * 60);
      ctx.fillText(`${String(hh).padStart(2, '0')}:00`, x, h - 8);
    }

    const colors = ['#1B3B22', '#D97706'];
    (jornadas || []).forEach((j, idx) => {
      const s = j.serie || [];
      ctx.strokeStyle = colors[idx % colors.length];
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      s.forEach((p, i) => {
        const x = xOf(p[0]);
        const y = yOf(p[1]);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();

      // Leyenda arriba
      ctx.fillStyle = colors[idx % colors.length];
      ctx.fillRect(pad.l + idx * 170, 8, 12, 10);
      ctx.fillStyle = '#18261B';
      ctx.textAlign = 'left';
      ctx.font = '600 12px system-ui, sans-serif';
      ctx.fillText(`Jornada ${j.dia}`, pad.l + idx * 170 + 18, 17);
    });
  }

  /* ------------------------------------------------------------------------
     3. Gráfico de barras de horas críticas (00..23 h)
     ------------------------------------------------------------------------ */
  function drawCriticalHours(canvas, jornadas) {
    if (!canvas) return;
    const { ctx, w, h } = prepCanvas(canvas);
    const pad = { l: 38, r: 12, t: 22, b: 26 };
    const pw = w - pad.l - pad.r;
    const ph = h - pad.t - pad.b;

    let maxVal = 10;
    (jornadas || []).forEach(j => (j.horas_criticas || []).forEach(v => { if (v > maxVal) maxVal = v; }));
    maxVal = Math.ceil(maxVal / 10) * 10;

    ctx.strokeStyle = '#E4ECE4';
    ctx.fillStyle = '#5A6E5E';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textAlign = 'right';
    for (let i = 0; i <= 4; i++) {
      const v = (maxVal * i) / 4;
      const y = pad.t + (1 - v / maxVal) * ph;
      ctx.beginPath();
      ctx.moveTo(pad.l, y);
      ctx.lineTo(pad.l + pw, y);
      ctx.stroke();
      ctx.fillText(`${Math.round(v)}m`, pad.l - 5, y + 3);
    }

    const colors = ['#8AB82E', '#D97706'];
    const groupW = pw / 24;
    const barW = Math.max(3, (groupW - 4) / Math.max(1, (jornadas || []).length));

    ctx.textAlign = 'center';
    for (let hr = 0; hr < 24; hr++) {
      const gx = pad.l + hr * groupW + 2;
      (jornadas || []).forEach((j, idx) => {
        const val = (j.horas_criticas || [])[hr] || 0;
        const bh = (val / maxVal) * ph;
        ctx.fillStyle = colors[idx % colors.length];
        ctx.fillRect(gx + idx * barW, pad.t + ph - bh, barW - 1, bh);
      });
      if (hr % 3 === 0) {
        ctx.fillStyle = '#5A6E5E';
        ctx.fillText(`${String(hr).padStart(2, '0')}h`, gx + groupW / 2, h - 7);
      }
    }
  }

  window.NonaCharts = { drawTimeSeries, drawComparison, drawCriticalHours };
})();
