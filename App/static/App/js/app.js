/* ==========================================================================
   Aplicación cliente – Invernadero Nona Olimpia (Vanilla JS)
   Conecta las 4 vistas con los endpoints JSON de Django + SQLite
   ========================================================================== */

(function () {
  const state = {
    view: 'monitor',
    horas: 6,
    monitor: null,
    series: null,
    eventos: [],
    cultivos: [],
    draggingSlider: false,
    tickCount: 0,
  };

  const VIEW_TITLES = {
    monitor: 'Monitor en vivo',
    historial: 'Historial y comparador de jornadas',
    eventos: 'Eventos, alertas y movimientos de mecanismos',
    config: 'Configuración de umbrales y cultivos',
  };

  /* ------------------------------------------------------------------------
     Utilidades HTTP y notificaciones
     ------------------------------------------------------------------------ */
  function getCsrf() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function toast(msg, isError = false) {
    if (!msg) return;
    const zone = document.getElementById('toasts');
    if (!zone) return;
    const el = document.createElement('div');
    el.className = `toast${isError ? ' error' : ''}`;
    el.textContent = msg;
    zone.appendChild(el);
    setTimeout(() => el.remove(), 4200);
  }

  async function apiGet(url) {
    const res = await fetch(url, { headers: { Accept: 'application/json' } });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `Error HTTP ${res.status}`);
    }
    return data;
  }

  async function apiPost(url, body = {}) {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrf(),
        Accept: 'application/json',
      },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || data.ok === false) {
      throw new Error(data.error || `Error HTTP ${res.status}`);
    }
    return data;
  }

  /* ------------------------------------------------------------------------
     Navegación y barra lateral
     ------------------------------------------------------------------------ */
  function setView(view) {
    if (!VIEW_TITLES[view]) view = 'monitor';
    state.view = view;
    document.querySelectorAll('.nav-item').forEach(a => {
      a.classList.toggle('active', a.dataset.view === view);
    });
    document.querySelectorAll('.view').forEach(sec => {
      sec.classList.toggle('active', sec.id === `view-${view}`);
    });
    const titleEl = document.getElementById('viewTitle');
    if (titleEl) titleEl.textContent = VIEW_TITLES[view];

    document.getElementById('sidebar')?.classList.remove('open');
    document.getElementById('overlay')?.classList.remove('open');

    if (view === 'monitor') {
      refreshMonitor(true);
    } else if (view === 'historial') {
      loadHistorialView();
    } else if (view === 'eventos') {
      loadEventosView();
    } else if (view === 'config') {
      loadConfigView();
    }
  }

  function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        const v = a.dataset.view;
        history.replaceState(null, '', `#${v}`);
        setView(v);
      });
    });

    const btnMenu = document.getElementById('btnMenu');
    const overlay = document.getElementById('overlay');
    btnMenu?.addEventListener('click', () => {
      if (window.innerWidth <= 780) {
        document.getElementById('sidebar')?.classList.toggle('open');
        overlay?.classList.toggle('open');
      } else {
        document.getElementById('app')?.classList.toggle('collapsed');
        setTimeout(redrawMonitorCharts, 240);
      }
    });
    overlay?.addEventListener('click', () => {
      document.getElementById('sidebar')?.classList.remove('open');
      overlay.classList.remove('open');
    });

    window.addEventListener('resize', () => {
      if (state.view === 'monitor') redrawMonitorCharts();
    });
  }

  /* ------------------------------------------------------------------------
     VISTA 1: MONITOR EN VIVO
     ------------------------------------------------------------------------ */
  function updateRangeBar(barId, val, minOpt, maxOpt, scaleMin, scaleMax) {
    const bar = document.getElementById(barId);
    if (!bar) return;
    const span = Math.max(1, scaleMax - scaleMin);
    const pct = v => Math.max(0, Math.min(100, ((v - scaleMin) / span) * 100));
    const left = pct(minOpt);
    const right = pct(maxOpt);
    const band = bar.querySelector('.band');
    const marker = bar.querySelector('.marker');
    if (band) {
      band.style.left = `${left}%`;
      band.style.width = `${Math.max(1, right - left)}%`;
    }
    if (marker && val != null) {
      marker.style.left = `${pct(val)}%`;
    }
  }

  function renderKpiVariable(prefix, v, unidad, scaleMin, scaleMax, sim) {
    const card = document.getElementById(prefix === 'rad' ? 'kpiRad' : 'kpiTemp');
    const pill = document.getElementById(`${prefix}Pill`);
    const valorEl = document.getElementById(`${prefix}Valor`);
    const rangoEl = document.getElementById(`${prefix}RangoTxt`);
    const persTxt = document.getElementById(`${prefix}PersTxt`);
    const persBar = document.getElementById(`${prefix}PersBar`);
    const notaEl = document.getElementById(`${prefix}Nota`);

    if (!v || !card) return;
    card.className = `card kpi state-${v.estado}`;

    valorEl.textContent = v.valor != null ? (prefix === 'rad' ? Math.round(v.valor) : v.valor.toFixed(1)) : '—';
    rangoEl.textContent = `Óptimo ${v.min} – ${v.max} ${unidad}`;
    updateRangeBar(`${prefix}Bar`, v.valor, v.min, v.max, scaleMin, scaleMax);

    let pillTxt = 'En rango óptimo';
    let pillCls = 'pill';
    if (v.estado === 'NO_EVALUADA') {
      pillTxt = 'Fuera de horario diurno';
      pillCls = 'pill neutral';
    } else if (v.estado === 'ALERTA') {
      pillTxt = `ALERTA (${v.direccion === 'ALTA' ? 'Alta' : 'Baja'})`;
      pillCls = 'pill danger';
    } else if (v.estado === 'PERSISTENCIA') {
      pillTxt = 'Evaluando persistencia';
      pillCls = 'pill warn';
    }
    pill.className = pillCls;
    pill.textContent = pillTxt;

    const minFuera = v.estado === 'ALERTA' ? v.persistencia : v.minutos_fuera;
    persTxt.textContent = `${minFuera} / ${v.persistencia} min`;
    const pctPers = Math.min(100, (minFuera / Math.max(1, v.persistencia)) * 100);
    persBar.style.width = `${pctPers}%`;
    persBar.className = v.estado === 'ALERTA' ? 'danger' : (v.estado === 'PERSISTENCIA' ? 'warn' : '');

    if (v.estado === 'NO_EVALUADA') {
      notaEl.textContent = `Se evalúa en franja diurna (${state.monitor.parametros.hora_inicio_diurna} a ${state.monitor.parametros.hora_fin_diurna}).`;
    } else if (v.en_histeresis) {
      notaEl.textContent = `En banda de histéresis (±${v.histeresis} ${unidad}) antes de cerrar alerta.`;
    } else if (v.estado === 'PERSISTENCIA') {
      notaEl.textContent = `Si revierte antes de ${v.persistencia} min se filtrará como variación transitoria.`;
    } else if (v.estado === 'ALERTA') {
      notaEl.textContent = `Superó los ${v.persistencia} min de persistencia configurados.`;
    } else {
      notaEl.textContent = `Histéresis de retorno: ${v.histeresis} ${unidad}.`;
    }
  }

  function renderMonitor(data) {
    state.monitor = data;
    const { sim, invernadero, cultivo, parametros, variables, decision, mecanismos, clima, alertas_activas } = data;

    // Cabecera
    document.getElementById('tbInvernadero').textContent = invernadero.nombre;
    document.getElementById('tbCultivo').textContent = cultivo.nombre;
    document.getElementById('simHora').textContent = sim.hora;
    document.getElementById('simFecha').textContent = `${sim.dia_semana} ${sim.fecha}`;
    document.getElementById('liveDot').classList.toggle('paused', sim.pausado);

    const badge = document.getElementById('navBadge');
    if (badge) {
      badge.hidden = alertas_activas.length === 0;
      badge.textContent = alertas_activas.length;
    }

    // Controles del simulador
    const btnPausa = document.getElementById('btnPausa');
    if (btnPausa) btnPausa.textContent = sim.pausado ? '▶ Reanudar' : '⏸ Pausar';
    document.querySelectorAll('#segVelocidad button').forEach(b => {
      b.classList.toggle('on', Number(b.dataset.v) === sim.velocidad);
    });
    const chkAuto = document.getElementById('chkAuto');
    if (chkAuto) chkAuto.checked = !!sim.automatico;

    document.querySelectorAll('[data-esc]').forEach(b => {
      b.classList.toggle('active', sim.escenario && sim.escenario.clave === b.dataset.esc);
    });
    const escEl = document.getElementById('escActivo');
    if (escEl) {
      if (sim.escenario) {
        escEl.hidden = false;
        escEl.textContent = `⚡ ${sim.escenario.nombre} (${sim.escenario.restante_min} min restantes${sim.escenario.mitigado ? ' · mitigado' : ''})`;
      } else {
        escEl.hidden = true;
      }
    }

    // Tarjetas KPI
    renderKpiVariable('rad', variables.RADIACION, 'W/m²', 0, 1000, sim);
    renderKpiVariable('temp', variables.TEMPERATURA, '°C', 0, 40, sim);

    // Tarjeta Decisión sugerida
    const decCard = document.getElementById('kpiDecision');
    const decPill = document.getElementById('decPill');
    const decIcono = document.getElementById('decIcono');
    const decTexto = document.getElementById('decTexto');
    const decEtiq = document.getElementById('decEtiqueta');
    const decAcc = document.getElementById('decAcciones');
    const decAlertas = document.getElementById('decAlertas');

    decCard.classList.remove('active-action', 'preventive-action');
    if (decision.codigo !== 'MANTENER') {
      decCard.classList.add('active-action');
      decPill.className = 'pill danger';
      decPill.textContent = 'Acción requerida';
      decIcono.textContent = '!';
      decTexto.textContent = decision.texto;
      decEtiq.textContent = decision.etiqueta;
    } else if (decision.sin_accion_mecanica && alertas_activas.length > 0) {
      decCard.classList.add('preventive-action');
      decPill.className = 'pill warn';
      decPill.textContent = 'Monitorear';
      decIcono.textContent = 'ℹ';
      decTexto.textContent = decision.texto;
      decEtiq.textContent = decision.etiqueta;
    } else if (decision.preventiva !== 'MANTENER') {
      decCard.classList.add('preventive-action');
      decPill.className = 'pill warn';
      decPill.textContent = 'En evaluación';
      decIcono.textContent = '⏳';
      decTexto.textContent = `Mantener (posible: ${decision.preventiva_texto})`;
      decEtiq.textContent = 'Esperando cumplir tiempo de persistencia antes de emitir alerta';
    } else if (decision.sin_accion_mecanica) {
      decCard.classList.add('preventive-action');
      decPill.className = 'pill warn';
      decPill.textContent = 'En evaluación';
      decIcono.textContent = '⏳';
      decTexto.textContent = decision.texto;
      decEtiq.textContent = decision.etiqueta;
    } else {
      decPill.className = 'pill';
      decPill.textContent = 'Óptimo';
      decIcono.textContent = '✓';
      decTexto.textContent = 'Mantener';
      decEtiq.textContent = 'Variables dentro del rango óptimo configurado';
    }

    decAcc.innerHTML = (decision.acciones.length
      ? decision.acciones
      : [decision.sin_accion_mecanica
          ? 'Sin acción mecánica pendiente: mecanismos ya en posición óptima.'
          : 'Sin cambios requeridos sobre los mecanismos.']
    ).map(a => `<li>${a}</li>`).join('');

    decAlertas.innerHTML = alertas_activas.map(a =>
      `<div class="alerta-chip">🔔 <b>${a.variable}</b> (${a.valor}) · ${a.recomendacion_txt}</div>`
    ).join('');

    const btnAccion = document.getElementById('btnAccion');
    if (btnAccion) {
      const sinAccion = !decision.acciones.length || !!decision.sin_accion_mecanica;
      btnAccion.disabled = sinAccion;
      btnAccion.title = sinAccion
        ? 'No hay acciones mecánicas pendientes para ejecutar.'
        : 'Aplicar acción sugerida y registrar movimiento en SQLite.';
    }

    // Esquema SVG del invernadero
    window.NonaGreenhouse?.renderGreenhouse(document.getElementById('greenhouse'), data);

    // Mecanismos accionables
    const mecBox = document.getElementById('mecanismos');
    if (mecBox) {
      mecBox.innerHTML = mecanismos.map(m => {
        const activo = (m.tipo === 'MEDIA_SOMBRA' && m.estado === 'CERRADA')
          || (m.tipo === 'VENTILACION' && m.estado === 'ABIERTA');
        const nextEstado = m.estados_validos.find(e => e !== m.estado) || m.estado;
        return `
          <div class="mec-card ${activo ? 'on' : ''}">
            <strong>${m.nombre}</strong>
            <span class="mec-state">${m.etiqueta}</span>
            <button class="btn" data-mec="${m.id}" data-next="${nextEstado}">
              Pasar a ${nextEstado}
            </button>
          </div>`;
      }).join('');
      mecBox.querySelectorAll('button[data-mec]').forEach(btn => {
        btn.addEventListener('click', async () => {
          try {
            const r = await apiPost(`/api/mecanismos/${btn.dataset.mec}/`, { estado: btn.dataset.next });
            toast(r.mensaje);
            refreshMonitor(true);
          } catch (err) {
            toast(err.message, true);
          }
        });
      });
    }

    // Clima exterior y pronósticos
    const climaBox = document.getElementById('clima');
    if (climaBox && clima) {
      const act = clima.actual;
      climaBox.innerHTML = `
        <div class="clima-actual">
          <span>🌤️ Exterior: <b>${act ? `${act.estado_txt} · ${act.temp_externa} °C` : '—'}</b></span>
          <span>Lluvia: <b>${act ? `${act.probabilidad_lluvia}%` : '—'}</b></span>
        </div>
        <div class="clima-pronos">
          ${(clima.pronostico || []).map(p => `
            <div class="prono-item">
              <span>${p.hora}</span>
              <b>${p.temp_externa} °C</b>
              <span>${p.estado_txt} (${p.probabilidad_lluvia}%)</span>
            </div>`).join('')}
        </div>`;
    }

    // Sliders rápidos (si la usuaria no está arrastrando uno en este instante)
    if (!state.draggingSlider && parametros) {
      document.querySelectorAll('#sliders input[data-p]').forEach(inp => {
        const key = inp.dataset.p;
        if (parametros[key] != null) {
          inp.value = parametros[key];
          const out = document.querySelector(`#sliders [data-out="${key}"]`);
          if (out) out.textContent = parametros[key];
        }
      });
    }
  }

  function redrawMonitorCharts() {
    if (!state.series || !state.monitor) return;
    const p = state.monitor.parametros;
    const s = state.series;
    window.NonaCharts?.drawTimeSeries(document.getElementById('chartRad'), {
      titulo: `Radiación solar interior (W/m²) · últimas ${s.horas} h`,
      unidad: 'W/m²',
      serie: s.radiacion,
      desde: s.desde,
      hasta: s.hasta,
      optMin: p.rad_min,
      optMax: p.rad_max,
      yMin: 0,
      yMax: 850,
      clampZero: true,
      diurnaInicio: p.hora_inicio_diurna,
      diurnaFin: p.hora_fin_diurna,
      color: '#D97706',
      marcas: (s.marcas || []).filter(m => m.variable === 'RADIACION'),
      movimientos: (s.movimientos || []).filter(m => m.tipo === 'MEDIA_SOMBRA'),
    });
    window.NonaCharts?.drawTimeSeries(document.getElementById('chartTemp'), {
      titulo: `Temperatura interna (°C) · últimas ${s.horas} h`,
      unidad: '°C',
      decimales: true,
      serie: s.temperatura,
      desde: s.desde,
      hasta: s.hasta,
      optMin: p.temp_min,
      optMax: p.temp_max,
      yMin: 5,
      yMax: 35,
      color: '#1B3B22',
      marcas: (s.marcas || []).filter(m => m.variable === 'TEMPERATURA'),
      movimientos: (s.movimientos || []).filter(m => m.tipo === 'VENTILACION'),
    });
  }

  async function refreshMonitor(withSeries = false) {
    try {
      const data = await apiGet('/api/monitor/');
      renderMonitor(data);
      if (withSeries || state.tickCount % 2 === 0 || !state.series) {
        state.series = await apiGet(`/api/series/?horas=${state.horas}`);
        redrawMonitorCharts();
      }
      state.tickCount++;
    } catch (err) {
      console.error(err);
    }
  }

  function initMonitorControls() {
    document.getElementById('btnPausa')?.addEventListener('click', async () => {
      const pausado = state.monitor?.sim?.pausado;
      const r = await apiPost('/api/sim/control/', { accion: pausado ? 'reanudar' : 'pausar' });
      toast(r.mensaje);
      refreshMonitor(false);
    });

    document.querySelectorAll('#segVelocidad button').forEach(btn => {
      btn.addEventListener('click', async () => {
        const r = await apiPost('/api/sim/control/', { accion: 'velocidad', valor: Number(btn.dataset.v) });
        toast(r.mensaje);
        refreshMonitor(false);
      });
    });

    document.getElementById('btnHora09')?.addEventListener('click', async () => {
      try {
        const r = await apiPost('/api/sim/control/', { accion: 'hora09' });
        toast(r.mensaje);
        refreshMonitor(true);
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById('chkAuto')?.addEventListener('change', async e => {
      const r = await apiPost('/api/sim/control/', { accion: 'automatico', valor: e.target.checked });
      toast(r.mensaje);
      refreshMonitor(true);
    });

    document.querySelectorAll('[data-esc]').forEach(btn => {
      btn.addEventListener('click', async () => {
        try {
          const r = await apiPost('/api/sim/escenario/', { clave: btn.dataset.esc });
          toast(r.mensaje);
          refreshMonitor(true);
        } catch (err) {
          toast(err.message, true);
        }
      });
    });

    document.getElementById('btnAccion')?.addEventListener('click', async () => {
      try {
        const r = await apiPost('/api/sim/accion-sugerida/', {});
        toast(r.mensaje);
        refreshMonitor(true);
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.querySelectorAll('#segHoras button').forEach(btn => {
      btn.addEventListener('click', () => {
        state.horas = Number(btn.dataset.h);
        document.querySelectorAll('#segHoras button').forEach(b => b.classList.toggle('on', b === btn));
        refreshMonitor(true);
      });
    });

    // Sliders rápidos sincronizados con SQLite
    document.querySelectorAll('#sliders input[data-p]').forEach(inp => {
      inp.addEventListener('pointerdown', () => { state.draggingSlider = true; });
      inp.addEventListener('input', () => {
        const out = document.querySelector(`#sliders [data-out="${inp.dataset.p}"]`);
        if (out) out.textContent = inp.value;
      });
      inp.addEventListener('change', async () => {
        const payload = { motivo: 'Ajuste desde sliders rápidos del monitor' };
        document.querySelectorAll('#sliders input[data-p]').forEach(el => {
          payload[el.dataset.p] = Number(el.value);
        });
        state.draggingSlider = false;
        try {
          const r = await apiPost('/api/parametros/', payload);
          toast(r.mensaje);
          refreshMonitor(true);
        } catch (err) {
          toast(err.message, true);
          refreshMonitor(false);
        }
      });
    });
  }

  /* ------------------------------------------------------------------------
     VISTA 2: HISTORIAL Y COMPARADOR
     ------------------------------------------------------------------------ */
  async function loadHistorialView() {
    try {
      const { dias } = await apiGet('/api/historial/dias/');
      const selA = document.getElementById('diaA');
      const selB = document.getElementById('diaB');
      if (!selA || !selB) return;
      const opts = dias.map(d => `<option value="${d.dia}">${d.dia} (${d.lecturas} lecturas)</option>`).join('');
      selA.innerHTML = opts;
      selB.innerHTML = `<option value="">— Sin segunda jornada —</option>${opts}`;
      if (dias.length >= 2) selB.value = dias[1].dia;
      await runComparacion();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function runComparacion() {
    const dia1 = document.getElementById('diaA')?.value || '';
    const dia2 = document.getElementById('diaB')?.value || '';
    if (!dia1) return;
    const qs = new URLSearchParams({ dia1, ...(dia2 ? { dia2 } : {}) });
    const data = await apiGet(`/api/historial/comparar/?${qs}`);
    const [jA, jB] = data.jornadas;

    document.getElementById('thA').textContent = jA ? `Jornada ${jA.dia}` : 'Jornada A';
    document.getElementById('thB').textContent = jB ? `Jornada ${jB.dia}` : 'Jornada B';

    const filas = [
      ['Lecturas registradas', j => j.lecturas],
      ['Temperatura máxima (°C)', j => j.temp_max != null ? `${j.temp_max} °C` : '—'],
      ['Temperatura mínima (°C)', j => j.temp_min != null ? `${j.temp_min} °C` : '—'],
      ['Temperatura media (°C)', j => j.temp_media != null ? `${j.temp_media} °C` : '—'],
      ['Radiación máxima (W/m²)', j => j.rad_max != null ? `${j.rad_max} W/m²` : '—'],
      ['Minutos fuera de rango (Temperatura)', j => `${j.min_fuera_temp} min`],
      ['Minutos fuera de rango (Radiación diurna)', j => `${j.min_fuera_rad} min`],
      ['Alertas persistentes emitidas', j => j.alertas],
      ['Variaciones transitorias filtradas', j => j.transitorias],
      ['Movimientos de mecanismos', j => j.movimientos],
    ];
    const tbody = document.querySelector('#tablaIndicadores tbody');
    if (tbody) {
      tbody.innerHTML = filas.map(([nombre, fn]) => `
        <tr>
          <td><b>${nombre}</b></td>
          <td>${jA ? fn(jA) : '—'}</td>
          <td>${jB ? fn(jB) : '—'}</td>
        </tr>`).join('');
    }

    window.NonaCharts?.drawComparison(document.getElementById('chartComparar'), data.jornadas, data.parametros);
    window.NonaCharts?.drawCriticalHours(document.getElementById('chartCriticas'), data.jornadas);
  }

  /* ------------------------------------------------------------------------
     VISTA 3: EVENTOS, ALERTAS Y MOVIMIENTOS
     ------------------------------------------------------------------------ */
  async function loadEventosView() {
    try {
      const { dias } = await apiGet('/api/historial/dias/');
      const selDia = document.getElementById('fDia');
      if (selDia) {
        const prev = selDia.value;
        selDia.innerHTML = `<option value="">Todas las jornadas</option>`
          + dias.map(d => `<option value="${d.dia}">${d.dia}</option>`).join('');
        selDia.value = prev;
      }
      await refreshEventos();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function refreshEventos() {
    const tipo = document.getElementById('fTipo')?.value || '';
    const variable = document.getElementById('fVariable')?.value || '';
    const dia = document.getElementById('fDia')?.value || '';
    const qs = new URLSearchParams();
    if (tipo) qs.set('tipo', tipo);
    if (variable) qs.set('variable', variable);
    if (dia) qs.set('dia', dia);

    const linkXlsx = document.getElementById('btnDescargarXlsx');
    const q = qs.toString();
    if (linkXlsx) linkXlsx.href = q ? `/api/eventos.xlsx?${q}` : '/api/eventos.xlsx';

    const data = await apiGet(`/api/eventos/?${qs.toString()}`);
    state.eventos = data.eventos || [];
    document.getElementById('evTotal').textContent = `${data.total} registros`;

    const counts = { ALERTA: 0, RECOMENDACION: 0, TRANSITORIA: 0, NORMALIZACION: 0, MOVIMIENTO: 0 };
    state.eventos.forEach(e => { counts[e.tipo] = (counts[e.tipo] || 0) + 1; });
    const countersEl = document.getElementById('evCounters');
    if (countersEl) {
      countersEl.innerHTML = [
        ['ALERTA', 'Alertas persistentes'],
        ['RECOMENDACION', 'Recomendaciones'],
        ['TRANSITORIA', 'Transitorias filtradas'],
        ['NORMALIZACION', 'Normalizaciones'],
        ['MOVIMIENTO', 'Movimientos de mecanismos'],
      ].map(([k, label]) => `<span class="ev-chip"><b>${counts[k] || 0}</b> ${label}</span>`).join('');
    }

    const badgeCls = {
      ALERTA: 'pill danger',
      RECOMENDACION: 'pill warn',
      TRANSITORIA: 'pill info',
      NORMALIZACION: 'pill',
      MOVIMIENTO: 'pill info',
    };
    const tbody = document.querySelector('#tablaEventos tbody');
    if (tbody) {
      tbody.innerHTML = state.eventos.map(e => `
        <tr>
          <td>${e.fecha}</td>
          <td><span class="${badgeCls[e.tipo] || 'pill'}">${e.tipo}</span></td>
          <td>${e.variable}</td>
          <td><b>${e.valor}</b></td>
          <td>${e.extra || '—'}</td>
          <td>${e.descripcion}</td>
          <td>${e.usuario}</td>
        </tr>`).join('');
    }
  }

  function copyEventosTabla() {
    const clean = v => String(v ?? '').replace(/[\t\r\n]+/g, ' ').trim();
    const rows = [
      ['Fecha', 'Tipo', 'Variable', 'Valor', 'Duración/Origen', 'Descripción', 'Usuario'],
      ...state.eventos.map(e => [e.fecha, e.tipo, e.variable, e.valor, e.extra, e.descripcion, e.usuario]),
    ];
    const tsv = rows.map(r => r.map(clean).join('\t')).join('\n');
    navigator.clipboard.writeText(tsv).then(
      () => toast('Tabla de eventos copiada al portapapeles (lista para pegar en Excel).'),
      () => toast('No se pudo copiar al portapapeles.', true)
    );
  }

  /* ------------------------------------------------------------------------
     VISTA 4: CONFIGURACIÓN Y CULTIVOS
     ------------------------------------------------------------------------ */
  async function loadConfigView() {
    try {
      const data = await apiGet('/api/config/');
      state.cultivos = data.cultivos || [];
      document.getElementById('cfgInvernadero').textContent = data.invernadero.nombre;
      document.getElementById('cfgActualizado').textContent = `Actualizado: ${data.parametros.fecha_actualizacion}`;

      // Lista de cultivos
      const lista = document.getElementById('listaCultivos');
      if (lista) {
        lista.innerHTML = state.cultivos.map(c => {
          const esActivo = c.id === data.invernadero.cultivo_id;
          const p = c.parametros;
          return `
            <div class="cultivo-item ${esActivo ? 'activo' : ''}">
              <div>
                <strong>${c.nombre} ${esActivo ? '· (Activo en invernadero)' : ''}</strong>
                <p>${c.recomendaciones || 'Sin notas agronómicas.'}</p>
                ${p ? `<p>T° ${p.temp_min}–${p.temp_max} °C · Rad ${p.rad_min}–${p.rad_max} W/m² · Persistencia ${p.tiempo_persistencia_min} min</p>` : ''}
              </div>
              <div class="cultivo-actions">
                <button class="btn" data-edit="${c.id}">Editar</button>
                ${esActivo ? '' : `<button class="btn primary" data-assign="${c.id}">Asignar</button>`}
              </div>
            </div>`;
        }).join('');

        lista.querySelectorAll('button[data-assign]').forEach(btn => {
          btn.addEventListener('click', async () => {
            try {
              const r = await apiPost(`/api/cultivos/${btn.dataset.assign}/asignar/`, {});
              toast(r.mensaje);
              loadConfigView();
              refreshMonitor(true);
            } catch (err) {
              toast(err.message, true);
            }
          });
        });

        lista.querySelectorAll('button[data-edit]').forEach(btn => {
          btn.addEventListener('click', () => {
            const c = state.cultivos.find(x => x.id === Number(btn.dataset.edit));
            if (!c) return;
            const f = document.getElementById('formCultivo');
            document.getElementById('formCultivoTitulo').textContent = `Editar cultivo: ${c.nombre}`;
            f.elements.id.value = c.id;
            f.elements.nombre.value = c.nombre;
            f.elements.recomendaciones.value = c.recomendaciones || '';
            if (c.parametros) {
              f.elements.temp_min.value = c.parametros.temp_min;
              f.elements.temp_max.value = c.parametros.temp_max;
              f.elements.rad_min.value = c.parametros.rad_min;
              f.elements.rad_max.value = c.parametros.rad_max;
              f.elements.tiempo_persistencia_min.value = c.parametros.tiempo_persistencia_min;
            }
          });
        });
      }

      // Formulario de umbrales avanzados
      const fp = document.getElementById('formParametros');
      if (fp && data.parametros) {
        ['temp_min', 'temp_max', 'histeresis_temp', 'rad_min', 'rad_max', 'histeresis_rad',
         'tiempo_persistencia_min', 'hora_inicio_diurna', 'hora_fin_diurna'].forEach(k => {
          if (fp.elements[k]) fp.elements[k].value = data.parametros[k];
        });
        fp.elements.motivo.value = '';
      }

      // Tabla HISTORIAL_PARAMETROS
      const tbody = document.querySelector('#tablaHistParam tbody');
      if (tbody) {
        tbody.innerHTML = (data.historial_parametros || []).map(h => `
          <tr>
            <td>${h.fecha}</td>
            <td>${h.temp_min} °C</td>
            <td>${h.temp_max} °C</td>
            <td>${h.rad_min} W/m²</td>
            <td>${h.rad_max} W/m²</td>
            <td><b>${h.tiempo_persistencia_min} min</b></td>
            <td>${h.histeresis_temp} °C</td>
            <td>${h.histeresis_rad} W/m²</td>
            <td>${h.franja}</td>
            <td>${h.usuario}</td>
            <td>${h.motivo}</td>
          </tr>`).join('');
      }
    } catch (err) {
      toast(err.message, true);
    }
  }

  function resetFormCultivo() {
    const f = document.getElementById('formCultivo');
    if (!f) return;
    f.reset();
    f.elements.id.value = '';
    document.getElementById('formCultivoTitulo').textContent = 'Alta de cultivo';
  }

  function initSecondaryViews() {
    document.getElementById('btnComparar')?.addEventListener('click', runComparacion);
    document.getElementById('btnEvRefrescar')?.addEventListener('click', refreshEventos);
    document.getElementById('fTipo')?.addEventListener('change', refreshEventos);
    document.getElementById('fVariable')?.addEventListener('change', refreshEventos);
    document.getElementById('fDia')?.addEventListener('change', refreshEventos);
    document.getElementById('btnCopiarTabla')?.addEventListener('click', copyEventosTabla);
    document.getElementById('btnCultivoNuevo')?.addEventListener('click', resetFormCultivo);

    document.getElementById('formCultivo')?.addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      const body = {
        id: f.elements.id.value ? Number(f.elements.id.value) : null,
        nombre: f.elements.nombre.value,
        recomendaciones: f.elements.recomendaciones.value,
        asignar: f.elements.asignar.checked,
        parametros: {
          temp_min: Number(f.elements.temp_min.value),
          temp_max: Number(f.elements.temp_max.value),
          rad_min: Number(f.elements.rad_min.value),
          rad_max: Number(f.elements.rad_max.value),
          tiempo_persistencia_min: Number(f.elements.tiempo_persistencia_min.value),
        },
      };
      try {
        const r = await apiPost('/api/cultivos/', body);
        toast(r.mensaje);
        resetFormCultivo();
        loadConfigView();
        refreshMonitor(true);
      } catch (err) {
        toast(err.message, true);
      }
    });

    document.getElementById('formParametros')?.addEventListener('submit', async e => {
      e.preventDefault();
      const f = e.target;
      const body = {
        temp_min: Number(f.elements.temp_min.value),
        temp_max: Number(f.elements.temp_max.value),
        histeresis_temp: Number(f.elements.histeresis_temp.value),
        rad_min: Number(f.elements.rad_min.value),
        rad_max: Number(f.elements.rad_max.value),
        histeresis_rad: Number(f.elements.histeresis_rad.value),
        tiempo_persistencia_min: Number(f.elements.tiempo_persistencia_min.value),
        hora_inicio_diurna: f.elements.hora_inicio_diurna.value,
        hora_fin_diurna: f.elements.hora_fin_diurna.value,
        motivo: f.elements.motivo.value,
      };
      try {
        const r = await apiPost('/api/parametros/', body);
        toast(r.mensaje);
        loadConfigView();
        refreshMonitor(true);
      } catch (err) {
        toast(err.message, true);
      }
    });
  }

  /* ------------------------------------------------------------------------
     Inicialización general
     ------------------------------------------------------------------------ */
  document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    initMonitorControls();
    initSecondaryViews();

    const hashView = (location.hash || '#monitor').replace('#', '');
    setView(hashView);
    if (state.view !== 'monitor') {
      apiGet('/api/monitor/').then(renderMonitor).catch(() => {});
    }

    setInterval(() => {
      if (state.view === 'monitor') {
        refreshMonitor(false);
      }
    }, 1000);
  });
})();
