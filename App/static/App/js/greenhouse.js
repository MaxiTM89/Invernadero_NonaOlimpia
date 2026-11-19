/* ==========================================================================
   Esquema SVG dinámico del Invernadero Nona Olimpia (Vanilla JS)
   - Trayectoria del sol / luna según hora simulada
   - Intensidad de rayos solares proporcional a la radiación
   - Nubes animadas según estado climático / escenario activo
   - Despliegue visual de la media sombra en el techo (ABIERTA vs CERRADA)
   - Flechas animadas de flujo de ventilación lateral (ABIERTA vs CERRADA)
   ========================================================================== */

(function () {
  function renderGreenhouse(container, estado) {
    if (!container || !estado) return;

    const sim = estado.sim || {};
    const mecs = {};
    (estado.mecanismos || []).forEach(m => { mecs[m.tipo] = m.estado; });

    const sombraCerrada = mecs.MEDIA_SOMBRA === 'CERRADA';
    const ventAbierta = mecs.VENTILACION === 'ABIERTA';

    const h = sim.hora_decimal ?? 12;
    const amanecer = sim.amanecer ?? 6.4;
    const ocaso = sim.ocaso ?? 20.1;
    const esDia = h >= amanecer && h <= ocaso;

    // Posición en arco para el sol (día) o la luna (noche)
    let prog = 0.5;
    if (esDia) {
      prog = (h - amanecer) / Math.max(0.1, ocaso - amanecer);
    } else {
      const nocheDur = 24 - ocaso + amanecer;
      const desdeOcaso = h > ocaso ? h - ocaso : (24 - ocaso) + h;
      prog = desdeOcaso / Math.max(0.1, nocheDur);
    }
    prog = Math.max(0.03, Math.min(0.97, prog));
    const cx = 55 + prog * 410;
    const cy = 108 - Math.sin(prog * Math.PI) * 76;

    const radVal = estado.variables?.RADIACION?.valor ?? 0;
    const tempVal = estado.variables?.TEMPERATURA?.valor ?? 0;
    const rayOpacity = esDia ? Math.min(0.85, Math.max(0.08, radVal / 750)) : 0;

    const climaEstado = sim.escenario?.clima || estado.clima?.actual?.estado || 'DESPEJADO';
    const cubierto = climaEstado === 'NUBLADO' || climaEstado === 'LLUVIA';
    const parcial = climaEstado === 'PARCIAL';

    const skyTop = esDia ? (cubierto ? '#8FA6B8' : (parcial ? '#8ED0EA' : '#7EC8E3')) : '#0F1E36';
    const skyBot = esDia ? (cubierto ? '#CFD9E2' : (parcial ? '#E6F5FC' : '#E3F4FA')) : '#243B55';

    const ghEstado = document.getElementById('ghEstado');
    if (ghEstado) {
      ghEstado.textContent = `${esDia ? '☀️ Día' : '🌙 Noche'} · Sombra ${sombraCerrada ? 'desplegada' : 'recogida'} · Vent. ${ventAbierta ? 'abierta' : 'cerrada'}`;
    }

    // Camas de Lechuga Salanova
    const plantas = [];
    for (let i = 0; i < 9; i++) {
      const px = 118 + i * 35;
      plantas.push(`
        <g transform="translate(${px}, 224)">
          <ellipse cx="0" cy="4" rx="12" ry="5" fill="#3D7A2A"/>
          <circle cx="-4" cy="0" r="6" fill="#68A845"/>
          <circle cx="4" cy="0" r="6" fill="#7BC054"/>
          <circle cx="0" cy="-3" r="5.5" fill="#8CD162"/>
        </g>`);
    }

    container.innerHTML = `
    <svg viewBox="0 0 520 265" role="img" aria-label="Esquema dinámico del invernadero">
      <defs>
        <linearGradient id="skyGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${skyTop}"/>
          <stop offset="100%" stop-color="${skyBot}"/>
        </linearGradient>
        <pattern id="shadeMesh" width="8" height="8" patternUnits="userSpaceOnUse">
          <path d="M0 4h8M4 0v8" stroke="#1B3B22" stroke-width="1.6" stroke-opacity="0.65"/>
        </pattern>
      </defs>

      <!-- Cielo -->
      <rect width="520" height="265" fill="url(#skyGrad)"/>

      <!-- Arco de trayectoria solar/lunar -->
      <path d="M 55 108 Q 260 -42 465 108" fill="none" stroke="${esDia ? 'rgba(255,255,255,0.45)' : 'rgba(255,255,255,0.2)'}" stroke-dasharray="4 4" stroke-width="1.3"/>

      <!-- Rayos solares hacia el invernadero -->
      ${esDia ? `
      <g opacity="${rayOpacity.toFixed(2)}" stroke="#FACC15" stroke-width="2" stroke-dasharray="6 5">
        <line x1="${cx}" y1="${cy}" x2="180" y2="128"/>
        <line x1="${cx}" y1="${cy}" x2="260" y2="115"/>
        <line x1="${cx}" y1="${cy}" x2="340" y2="128"/>
      </g>` : ''}

      <!-- Sol o Luna -->
      ${esDia ? `
      <g transform="translate(${cx.toFixed(1)}, ${cy.toFixed(1)})">
        <circle r="21" fill="#FDE047" opacity="0.35"/>
        <circle r="14" fill="#FACC15" stroke="#F59E0B" stroke-width="2"/>
      </g>` : `
      <g transform="translate(${cx.toFixed(1)}, ${cy.toFixed(1)})">
        <circle r="13" fill="#FEF9C3"/>
        <circle cx="5" cy="-3" r="11" fill="${skyTop}"/>
      </g>`}

      <!-- Nubes animadas -->
      <g opacity="${cubierto ? '0.92' : (parcial ? '0.72' : '0.25')}" fill="#FFFFFF">
        <g>
          <ellipse cx="145" cy="54" rx="28" ry="12"/>
          <ellipse cx="162" cy="48" rx="20" ry="11"/>
          <ellipse cx="128" cy="52" rx="18" ry="9"/>
          <animateTransform attributeName="transform" type="translate" values="-18,0; 22,0; -18,0" dur="9s" repeatCount="indefinite"/>
        </g>
        ${(cubierto || parcial) ? `
        <g fill="${cubierto ? '#E2E8F0' : '#FFFFFF'}">
          <ellipse cx="330" cy="46" rx="34" ry="14"/>
          <ellipse cx="352" cy="40" rx="24" ry="12"/>
          <ellipse cx="308" cy="44" rx="22" ry="10"/>
          <animateTransform attributeName="transform" type="translate" values="20,0; -24,0; 20,0" dur="7s" repeatCount="indefinite"/>
        </g>` : ''}
      </g>

      <!-- Suelo -->
      <rect x="0" y="232" width="520" height="33" fill="#6D5A43"/>
      <rect x="0" y="226" width="520" height="8" fill="#4D7C3E"/>

      <!-- Estructura del Invernadero -->
      <polygon points="80,145 260,82 440,145 440,230 80,230" fill="rgba(255,255,255,0.62)" stroke="#275331" stroke-width="2.5"/>
      <line x1="260" y1="82" x2="260" y2="230" stroke="#275331" stroke-width="1.5" stroke-opacity="0.5"/>
      <line x1="80" y1="145" x2="440" y2="145" stroke="#275331" stroke-width="1.8"/>

      <!-- Media sombra en el techo -->
      ${sombraCerrada ? `
      <g>
        <polygon points="84,144 260,84 436,144" fill="url(#shadeMesh)" stroke="#1B3B22" stroke-width="1.5"/>
        <rect x="196" y="106" width="128" height="18" rx="4" fill="#1B3B22" opacity="0.88"/>
        <text x="260" y="119" text-anchor="middle" fill="#FFFFFF" font-size="10.5" font-weight="700">MEDIA SOMBRA CERRADA</text>
      </g>` : `
      <g>
        <circle cx="260" cy="84" r="6" fill="#1B3B22"/>
        <text x="260" y="74" text-anchor="middle" fill="#1B3B22" font-size="10" font-weight="700">Media sombra abierta</text>
      </g>`}

      <!-- Ventanas laterales y flechas de flujo de ventilación -->
      <rect x="76" y="160" width="8" height="48" rx="2" fill="${ventAbierta ? '#38BDF8' : '#475569'}"/>
      <rect x="436" y="160" width="8" height="48" rx="2" fill="${ventAbierta ? '#38BDF8' : '#475569'}"/>

      ${ventAbierta ? `
      <g stroke="#0284C7" stroke-width="2.2" fill="none">
        <path d="M 28 175 L 106 175 M 98 169 L 106 175 L 98 181">
          <animateTransform attributeName="transform" type="translate" values="0,0; 10,0; 0,0" dur="1.4s" repeatCount="indefinite"/>
        </path>
        <path d="M 28 195 L 106 195 M 98 189 L 106 195 L 98 201">
          <animateTransform attributeName="transform" type="translate" values="0,0; 10,0; 0,0" dur="1.4s" repeatCount="indefinite"/>
        </path>
        <path d="M 414 175 L 492 175 M 484 169 L 492 175 L 484 181">
          <animateTransform attributeName="transform" type="translate" values="0,0; 10,0; 0,0" dur="1.4s" repeatCount="indefinite"/>
        </path>
        <path d="M 414 195 L 492 195 M 484 189 L 492 195 L 484 201">
          <animateTransform attributeName="transform" type="translate" values="0,0; 10,0; 0,0" dur="1.4s" repeatCount="indefinite"/>
        </path>
      </g>` : ''}

      <!-- Cultivo Lechuga Salanova -->
      ${plantas.join('')}

      <!-- Indicadores internos -->
      <g transform="translate(96, 152)">
        <rect width="112" height="24" rx="5" fill="rgba(27,59,34,0.86)"/>
        <text x="56" y="16" text-anchor="middle" fill="#FFFFFF" font-size="11.5" font-weight="700">🌡️ ${tempVal.toFixed(1)} °C</text>
      </g>
      <g transform="translate(312, 152)">
        <rect width="112" height="24" rx="5" fill="rgba(27,59,34,0.86)"/>
        <text x="56" y="16" text-anchor="middle" fill="#FFFFFF" font-size="11.5" font-weight="700">☀️ ${Math.round(radVal)} W/m²</text>
      </g>
    </svg>`;
  }

  window.NonaGreenhouse = { renderGreenhouse };
})();
