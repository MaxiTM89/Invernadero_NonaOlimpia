"""
Motor de evaluación y simulador de sensores - Invernadero Nona Olimpia.

* ``ModeloFisico``: genera valores sintéticos realistas (ciclo diurno/nocturno de Las
  Pampitas, Perico, Jujuy en primavera) de temperatura interna (°C) y radiación solar
  interior (W/m²), considerando el estado de los mecanismos (media sombra y ventilación)
  y los escenarios de prueba activos.
* ``MotorEvaluacion`` (<<extend>> de Registrar Valores): evalúa cada lectura contra los
  PARAMETROS del cultivo activo aplicando:
    - franja diurna para la radiación (por defecto 09:00 a 16:30),
    - criterio de persistencia (variaciones cortas => transitorias filtradas),
    - recomendación exacta (SOMBREAR, VENTILAR, ambas, ABRIR MEDIA SOMBRA, CERRAR VENTILACIÓN),
    - histéresis de retorno antes de cerrar una alerta activa.
* ``Simulador``: reloj simulado con pausa, velocidad (1x, 2x, 5x, 15x), salto a las
  09:00, escenarios de prueba y modo automático. 1x = 1 minuto simulado por segundo real.
"""
import datetime
import math
import random
import threading
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import services
from .models import Alerta, HistorialClima, HistorialMecanismo, HistorialTemperatura, Mecanismo

TEMP = Alerta.TEMPERATURA
RAD = Alerta.RADIACION

# Astronomía aproximada de Perico (Jujuy, 24.4°S) en primavera-verano (hora local UTC-3)
AMANECER = 6.4      # 06:24
OCASO = 20.1        # 20:06
PICO_RAD_INTERIOR = 520.0   # W/m² bajo cubierta difusora a cielo despejado
FACTOR_MEDIA_SOMBRA = 0.5   # malla 50 %
INERCIA_TERMICA = 0.2       # fracción de acercamiento al objetivo por minuto

CONF = getattr(settings, 'NONA_OLIMPIA', {})
MIN_POR_SEGUNDO = CONF.get('MINUTOS_SIMULADOS_POR_SEGUNDO', 1)
MAX_MIN_SYNC = CONF.get('MAX_MINUTOS_POR_SINCRONIZACION', 90)
VELOCIDADES = (1, 2, 5, 15)

ESCENARIOS = {
    'nube_5': {
        'nombre': 'Nube pasajera 5 min', 'duracion': 5, 'rad_mult': 0.25, 'rad_add': 0.0, 'temp_add': 0.0,
        'clima': 'PARCIAL', 'descripcion': 'Caída brusca de radiación por 5 min: debe filtrarse como transitoria.',
    },
    'nublado_45': {
        'nombre': 'Nublado largo 45 min', 'duracion': 45, 'rad_mult': 0.22, 'rad_add': 0.0, 'temp_add': -1.5,
        'clima': 'NUBLADO', 'descripcion': 'Radiación baja persistente: alerta y sugerencia de abrir media sombra.',
    },
    'sol_30': {
        'nombre': 'Sol intenso 30 min', 'duracion': 30, 'rad_mult': 1.0, 'rad_add': 380.0, 'temp_add': 0.0,
        'clima': 'DESPEJADO', 'descripcion': 'Radiación por encima del máximo: sugerencia de sombrear.',
    },
    'calor_60': {
        'nombre': 'Ola de calor 60 min', 'duracion': 60, 'rad_mult': 1.0, 'rad_add': 0.0, 'temp_add': 9.0,
        'clima': 'CALUROSO', 'descripcion': 'Temperatura interna elevada: sugerencia de ventilar.',
    },
    'sol_calor_45': {
        'nombre': 'Sol + Calor extremo 45 min', 'duracion': 45, 'rad_mult': 1.0, 'rad_add': 380.0, 'temp_add': 9.5,
        'clima': 'CALUROSO', 'descripcion': 'Radiación y temperatura altas: sugerencia de sombrear y ventilar.',
    },
}

# Requisitos físicos sobre mecanismos para considerar mitigado un escenario.
# Los escenarios nublados no se mitigan mecánicamente: ningún mecanismo despeja las nubes.
CONDICIONES_MITIGACION = {
    'sol_30': {Mecanismo.MEDIA_SOMBRA: 'CERRADA'},
    'calor_60': {Mecanismo.VENTILACION: 'ABIERTA'},
    'sol_calor_45': {Mecanismo.MEDIA_SOMBRA: 'CERRADA', Mecanismo.VENTILACION: 'ABIERTA'},
}


def fraccion_solar(h):
    """0..1 según la altura aparente del sol (0 de noche)."""
    if h <= AMANECER or h >= OCASO:
        return 0.0
    return math.sin(math.pi * (h - AMANECER) / (OCASO - AMANECER))


def temperatura_base(h):
    """Curva térmica interior con ventilación cerrada: mín ~03:00, máx ~15:00."""
    return 17.0 + 5.5 * math.sin(2 * math.pi * (h - 9) / 24)


def factores_dia(dia):
    """Variabilidad determinística entre jornadas (nubosidad media y temperatura)."""
    r = random.Random(dia.toordinal() * 7919 + 13)
    return r.uniform(0.93, 1.04), r.uniform(-1.0, 1.0)


def hora_decimal(fecha):
    loc = timezone.localtime(fecha)
    return loc.hour + loc.minute / 60 + loc.second / 3600


# ---------------------------------------------------------------------------
# Modelo físico sintético
# ---------------------------------------------------------------------------
class ModeloFisico:
    def __init__(self, rng):
        self.rng = rng
        self.ruido_rad = 0.0

    def calcular(self, fecha, mecanismos, efectos, temp_prev):
        h = hora_decimal(fecha)
        f_rad, d_temp = factores_dia(timezone.localtime(fecha).date())
        s = fraccion_solar(h)
        rad_despejado = PICO_RAD_INTERIOR * f_rad * (s ** 1.3)
        self.ruido_rad = 0.85 * self.ruido_rad + self.rng.gauss(0, 5)
        rad = rad_despejado * efectos['rad_mult'] + efectos['rad_add'] * min(1.0, s * 1.8)
        if s > 0.02:
            rad += self.ruido_rad
        if mecanismos.get(Mecanismo.MEDIA_SOMBRA) == 'CERRADA':
            rad *= FACTOR_MEDIA_SOMBRA
        rad = max(0.0, rad)

        objetivo = temperatura_base(h) + d_temp
        objetivo += 0.004 * (rad - rad_despejado)          # calentamiento/enfriamiento radiativo
        if mecanismos.get(Mecanismo.VENTILACION) == 'ABIERTA':
            objetivo -= 3.0                                  # recambio de aire con el exterior
        objetivo += efectos['temp_add']
        previo = objetivo if temp_prev is None else temp_prev
        temp = previo + (objetivo - previo) * INERCIA_TERMICA + self.rng.gauss(0, 0.06)
        return temp, rad

    def clima(self, fecha, efectos=None, es_pronostico=False):
        loc = timezone.localtime(fecha)
        h = loc.hour + loc.minute / 60
        _, d_temp = factores_dia(loc.date())
        r = random.Random(loc.date().toordinal() * 31 + loc.hour // 3)
        p = r.random()
        if efectos and efectos.get('clima'):
            estado = efectos['clima']
            temp_add = efectos['temp_add'] * 0.8
        else:
            # En condiciones normales sin escenario, el clima actual es coherente con la radiación despejada;
            # los pronósticos pueden anticipar nubosidad parcial suave pero reservan NUBLADO para nublado_45.
            estado = ('DESPEJADO' if p < 0.75 else 'PARCIAL') if es_pronostico else 'DESPEJADO'
            temp_add = 0.0
        prob = {'DESPEJADO': r.randint(0, 8), 'PARCIAL': r.randint(10, 25), 'NUBLADO': r.randint(35, 55),
                'LLUVIA': r.randint(70, 95), 'CALUROSO': r.randint(0, 5)}[estado]
        temp_ext = temperatura_base(h) + d_temp - 2.0 + temp_add + r.uniform(-0.5, 0.5)
        return estado, temp_ext, prob


# ---------------------------------------------------------------------------
# Seguimiento de persistencia por variable
# ---------------------------------------------------------------------------
@dataclass
class Seguimiento:
    fuera_desde: datetime.datetime = None
    direccion: str = None
    pico: float = None
    minutos_fuera: int = 0
    alerta_id: int = None
    alerta_dir: str = None
    alerta_pico: float = None

    def reiniciar_episodio(self):
        self.fuera_desde = None
        self.direccion = None
        self.pico = None
        self.minutos_fuera = 0

    def cerrar_alerta(self):
        self.alerta_id = None
        self.alerta_dir = None
        self.alerta_pico = None


@dataclass
class Contexto:
    invernadero: object
    parametro: object
    mecanismos: dict

    def estados(self):
        return {t: m.estado for t, m in self.mecanismos.items()}


# ---------------------------------------------------------------------------
# Motor de evaluación (<<extend>>)
# ---------------------------------------------------------------------------
class MotorEvaluacion:
    def __init__(self):
        self.seg = {TEMP: Seguimiento(), RAD: Seguimiento()}

    def reconstruir(self, invernadero):
        """Recupera las alertas activas desde SQLite (tras reinicio del servidor)."""
        self.seg = {TEMP: Seguimiento(), RAD: Seguimiento()}
        for var in (TEMP, RAD):
            activas = list(Alerta.objects.filter(id_invernadero=invernadero, variable=var,
                                                 tipo=Alerta.ALERTA, resuelta=False).order_by('-fecha', '-id'))
            if activas:
                a = activas[0]
                s = self.seg[var]
                s.alerta_id, s.alerta_dir, s.alerta_pico = a.id, a.direccion, a.valor_pico
                for vieja in activas[1:]:
                    services.resolver_alerta(vieja.id, a.fecha, vieja.valor_detectado, 'Reemplazada por alerta posterior.')

    @staticmethod
    def es_diurno(parametro, fecha):
        t = timezone.localtime(fecha).time()
        return parametro.hora_inicio_diurna <= t <= parametro.hora_fin_diurna

    def direccion_activa(self, var):
        # alerta_dir se fija al alcanzar la persistencia (alerta activa o recién emitida)
        return self.seg[var].alerta_dir

    def direccion_pendiente(self, var):
        s = self.seg[var]
        return s.alerta_dir if s.alerta_id else (s.direccion if s.fuera_desde else None)

    def recomendacion_actual(self):
        return services.calcular_recomendacion(self.direccion_activa(RAD), self.direccion_activa(TEMP))

    def recomendacion_preventiva(self):
        return services.calcular_recomendacion(self.direccion_pendiente(RAD), self.direccion_pendiente(TEMP))

    def evaluar(self, ctx, fecha, temp, rad):
        """Evalúa una lectura. Devuelve (alertas_creadas, codigo_recomendacion|None)."""
        p = ctx.parametro
        diurno = self.es_diurno(p, fecha)
        nuevas = []
        for var, valor, vmin, vmax, hist, evaluable in (
            (RAD, rad, p.rad_min, p.rad_max, p.histeresis_rad, diurno),
            (TEMP, temp, p.temp_min, p.temp_max, p.histeresis_temp, True),
        ):
            if self._evaluar_variable(ctx, var, valor, fecha, vmin, vmax, hist, p.tiempo_persistencia_min, evaluable):
                nuevas.append((var, valor, vmin, vmax))
        if not nuevas:
            return [], None
        codigo = self.recomendacion_actual()
        creadas = []
        for var, valor, vmin, vmax in nuevas:
            s = self.seg[var]
            limite = vmax if s.alerta_dir == 'ALTA' else vmin
            alerta = services.crear_alerta(ctx.invernadero, var, s.alerta_dir, valor, s.alerta_pico, limite,
                                           p.tiempo_persistencia_min, fecha, codigo)
            s.alerta_id = alerta.id
            creadas.append(alerta)
        return creadas, codigo

    def _evaluar_variable(self, ctx, var, valor, fecha, vmin, vmax, hist, persistencia, evaluable):
        s = self.seg[var]
        if not evaluable:
            if s.alerta_id:
                services.resolver_alerta(s.alerta_id, fecha, valor, 'Cerrada al finalizar la franja diurna de evaluación.')
                s.cerrar_alerta()
            s.reiniciar_episodio()
            return False

        direccion = 'ALTA' if valor > vmax else ('BAJA' if valor < vmin else None)

        # 1) Alerta activa: se mantiene hasta superar la histéresis de retorno
        if s.alerta_id:
            if direccion == s.alerta_dir:
                if s.alerta_pico is None:
                    s.alerta_pico = valor
                else:
                    s.alerta_pico = max(s.alerta_pico, valor) if direccion == 'ALTA' else min(s.alerta_pico, valor)
                return False
            if direccion is None:
                retorno = valor <= vmax - hist if s.alerta_dir == 'ALTA' else valor >= vmin + hist
                if not retorno:
                    return False   # dentro de la banda de histéresis: la alerta sigue activa
            services.resolver_alerta(s.alerta_id, fecha, valor)
            s.cerrar_alerta()
            if direccion is None:
                return False

        # 2) Valor en rango: si hubo un episodio corto, se registra como transitorio filtrado
        if direccion is None:
            if s.fuera_desde:
                self._registrar_transitoria(ctx, var, s, vmin, vmax, persistencia)
            return False

        # 3) Fuera de rango: criterio de persistencia
        if s.fuera_desde and s.direccion != direccion:
            self._registrar_transitoria(ctx, var, s, vmin, vmax, persistencia)
        if not s.fuera_desde:
            s.fuera_desde, s.direccion, s.pico, s.minutos_fuera = fecha, direccion, valor, 1
        else:
            s.minutos_fuera += 1
            s.pico = max(s.pico, valor) if direccion == 'ALTA' else min(s.pico, valor)
        if s.minutos_fuera >= persistencia:
            s.alerta_dir, s.alerta_pico = direccion, s.pico
            s.reiniciar_episodio()
            return True
        return False

    @staticmethod
    def _registrar_transitoria(ctx, var, s, vmin, vmax, persistencia):
        limite = vmax if s.direccion == 'ALTA' else vmin
        services.registrar_transitoria(ctx.invernadero, var, s.direccion, s.pico, limite,
                                       s.minutos_fuera, s.fuera_desde, persistencia)
        s.reiniciar_episodio()

    def cerrar_todo(self, fecha, motivo):
        for var, s in self.seg.items():
            if s.alerta_id:
                services.resolver_alerta(s.alerta_id, fecha, s.alerta_pico or 0, motivo)
            s.cerrar_alerta()
            s.reiniciar_episodio()


# ---------------------------------------------------------------------------
# Simulador en vivo
# ---------------------------------------------------------------------------
class Simulador:
    def __init__(self, semilla=None):
        self.lock = threading.RLock()
        self.rng = random.Random(semilla)
        self.modelo = ModeloFisico(self.rng)
        self.motor = MotorEvaluacion()
        self.listo = False
        self.velocidad = 1
        self.pausado = False
        self.automatico = False
        self.escenario = None
        self.sim_time = None
        self.temp_prev = None
        self.ultima = None
        self._ultimo_real = time.monotonic()
        self._acarreo = 0.0

    # ------------------------------------------------------------------ init
    def contexto(self):
        inv = services.obtener_invernadero_activo()
        if inv is None:
            return None
        mecs = {m.tipo: m for m in Mecanismo.objects.filter(id_invernadero=inv)}
        return Contexto(inv, inv.id_cultivo.parametros, mecs)

    def asegurar_inicializado(self):
        if self.listo:
            return True
        inv = services.obtener_invernadero_activo()
        if inv is None:
            return False
        self.automatico = inv.modo_automatico
        ultima_t = HistorialTemperatura.objects.filter(id_invernadero=inv).order_by('-fecha').first()
        if ultima_t:
            self.sim_time = ultima_t.fecha
            self.temp_prev = ultima_t.grados
            r = inv.radiaciones.order_by('-fecha').first()
            self.ultima = (ultima_t.grados, r.valor if r else 0.0, ultima_t.fecha)
        else:
            self.sim_time = timezone.now().replace(second=0, microsecond=0)
        self.motor.reconstruir(inv)
        self._ultimo_real = time.monotonic()
        self._acarreo = 0.0
        self.listo = True
        return True

    def reiniciar(self):
        with self.lock:
            self.listo = False
            self.automatico = False
            self.escenario = None
            self.sim_time = None
            self.temp_prev = None
            self.ultima = None
            self.motor = MotorEvaluacion()

    def reiniciar_evaluacion(self, motivo, cerrar_alertas=False):
        """Sincroniza el estado en memoria del motor tras cambiar cultivo o parámetros."""
        with self.lock:
            fecha = self.sim_time or timezone.now()
            if cerrar_alertas:
                with transaction.atomic():
                    self.motor.cerrar_todo(fecha, motivo)
                    inv = services.obtener_invernadero_activo()
                    if inv is not None:
                        abiertas = Alerta.objects.filter(
                            id_invernadero=inv, tipo=Alerta.ALERTA, resuelta=False
                        ).values_list('id', 'valor_pico', 'valor_detectado')
                        for aid, pico, val in abiertas:
                            services.resolver_alerta(
                                aid, fecha, pico if pico is not None else val, motivo
                            )
            else:
                for seg in self.motor.seg.values():
                    seg.reiniciar_episodio()

    # ------------------------------------------------------------- efectos
    def efectos(self, fecha):
        base = {'rad_mult': 1.0, 'rad_add': 0.0, 'temp_add': 0.0, 'clima': None}
        e = self.escenario
        if not e or not (e['inicio'] < fecha <= e['fin']):
            return base
        k = 0.15 if e['mitigado'] else 1.0
        return {'rad_mult': 1 - (1 - e['rad_mult']) * k, 'rad_add': e['rad_add'] * k,
                'temp_add': e['temp_add'] * k, 'clima': e['clima']}

    # --------------------------------------------------------------- pasos
    def _paso(self, ctx, fecha):
        ef = self.efectos(fecha)
        temp, rad = self.modelo.calcular(fecha, ctx.estados(), ef, self.temp_prev)
        services.registrar_lectura(ctx.invernadero, fecha, temp, rad)           # CU Registrar Valores
        self.temp_prev = temp
        self.ultima = (temp, rad, fecha)
        self._registrar_clima(ctx, fecha, ef)
        creadas, codigo = self.motor.evaluar(ctx, fecha, temp, rad)             # <<extend>> Evaluar Datos
        if creadas and self.automatico:
            self._aplicar(ctx, codigo, HistorialMecanismo.AUTOMATICO, fecha, services.USUARIO_AUTOMATICO,
                          'Modo automático: acción aplicada al emitirse la alerta.')
        self.sim_time = fecha
        if self.escenario and fecha >= self.escenario['fin']:
            self.escenario = None
            self._registrar_clima_actual(ctx, fecha, None)

    def _registrar_clima_actual(self, ctx, fecha, ef=None):
        estado, t_ext, prob = self.modelo.clima(fecha, ef, es_pronostico=False)
        HistorialClima.objects.filter(id_invernadero=ctx.invernadero, es_pronostico=False, fecha=fecha).delete()
        return services.registrar_clima(ctx.invernadero, fecha, estado, t_ext, prob, es_pronostico=False)

    def _registrar_clima(self, ctx, fecha, ef):
        loc = timezone.localtime(fecha)
        if loc.minute % 30 == 0:
            self._registrar_clima_actual(ctx, fecha, ef)
        if loc.minute == 0 and loc.hour % 3 == 0:
            for horas in (3, 6, 12, 24):
                objetivo = fecha + datetime.timedelta(hours=horas)
                estado, t_ext, prob = self.modelo.clima(objetivo, es_pronostico=True)
                services.registrar_clima(ctx.invernadero, objetivo, estado, t_ext, prob, es_pronostico=True)

    def _actualizar_mitigacion_escenario(self, ctx, fecha):
        """Evalúa si los estados físicos actuales contrarrestan el escenario activo."""
        e = self.escenario
        if not e or not (e['inicio'] <= fecha <= e['fin']):
            return False
        requeridos = CONDICIONES_MITIGACION.get(e['clave'])
        if not requeridos:
            e['mitigado'] = False
            return False
        estados = ctx.estados()
        e['mitigado'] = all(estados.get(t) == est for t, est in requeridos.items())
        return e['mitigado']

    def _aplicar(self, ctx, codigo, origen, fecha, usuario, detalle):
        pendientes = services.acciones_pendientes(codigo, ctx.estados())
        if not pendientes:
            return []
        movs = services.aplicar_recomendacion(ctx.invernadero, codigo, origen, fecha, usuario, detalle)
        for h in movs:   # refrescar el estado en memoria del contexto
            ctx.mecanismos[h.id_mecanismo.tipo].estado = h.a_que_estado_paso
        if movs:
            self._actualizar_mitigacion_escenario(ctx, fecha)
        return movs

    def avanzar(self, minutos):
        ctx = self.contexto()
        if ctx is None or minutos <= 0:
            return 0
        with transaction.atomic():
            for _ in range(minutos):
                self._paso(ctx, self.sim_time + datetime.timedelta(minutes=1))
        return minutos

    def sincronizar(self):
        """Avanza el reloj simulado según el tiempo real transcurrido y la velocidad."""
        with self.lock:
            if not self.asegurar_inicializado():
                return 0
            ahora = time.monotonic()
            if self.pausado:
                self._ultimo_real = ahora
                return 0
            minutos = (ahora - self._ultimo_real) * self.velocidad * MIN_POR_SEGUNDO + self._acarreo
            n = int(minutos)
            self._acarreo = minutos - n
            self._ultimo_real = ahora
            if n > MAX_MIN_SYNC:
                n, self._acarreo = MAX_MIN_SYNC, 0.0
            return self.avanzar(n)

    # ----------------------------------------------------------- controles
    def pausar(self, valor=True):
        with self.lock:
            self.sincronizar()
            self.pausado = bool(valor)
            self._ultimo_real = time.monotonic()

    def set_velocidad(self, v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise services.ErrorValidacion(f'Velocidad inválida. Opciones: {VELOCIDADES}')
        if v not in VELOCIDADES:
            raise services.ErrorValidacion(f'Velocidad inválida. Opciones: {VELOCIDADES}')
        with self.lock:
            self.sincronizar()
            self.velocidad = v

    def set_automatico(self, valor):
        with self.lock:
            self.sincronizar()
            nuevo = bool(valor)
            self.automatico = nuevo
            inv = services.obtener_invernadero_activo()
            if inv is not None and inv.modo_automatico != nuevo:
                inv.modo_automatico = nuevo
                inv.save(update_fields=['modo_automatico'])

    def activar_escenario(self, clave):
        with self.lock:
            self.sincronizar()
            ctx = self.contexto()
            if clave == 'quitar':
                self.escenario = None
                if ctx is not None and self.sim_time is not None:
                    self._registrar_clima_actual(ctx, self.sim_time, None)
                return 'Escenarios de prueba quitados.'
            if clave not in ESCENARIOS:
                raise services.ErrorValidacion('Escenario desconocido.')
            cfg = ESCENARIOS[clave]
            inicio = self.sim_time
            self.escenario = {**cfg, 'clave': clave, 'inicio': inicio,
                              'fin': inicio + datetime.timedelta(minutes=cfg['duracion']), 'mitigado': False}
            if ctx is not None and inicio is not None:
                self._registrar_clima_actual(ctx, inicio, {'clima': cfg['clima'], 'temp_add': cfg['temp_add']})
            aviso = ''
            if clave != 'calor_60' and ctx and not self.motor.es_diurno(ctx.parametro, inicio):
                aviso = ' Atención: la radiación sólo se evalúa dentro de la franja diurna.'
            return f'Escenario "{cfg["nombre"]}" activado.{aviso}'

    def poner_hora(self, hora=9, minuto=0):
        """Salta el reloj simulado a la próxima HH:MM completando el hueco con lecturas sintéticas."""
        with self.lock:
            self.sincronizar()
            ctx = self.contexto()
            if ctx is None:
                raise services.ErrorValidacion('No hay invernadero configurado.')
            loc = timezone.localtime(self.sim_time)
            objetivo = loc.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if objetivo <= loc:
                objetivo += datetime.timedelta(days=1)
            total = int((objetivo - loc).total_seconds() // 60)
            ef = {'rad_mult': 1.0, 'rad_add': 0.0, 'temp_add': 0.0, 'clima': None}  # hueco sin escenarios
            lecturas, climas = [], []
            temp = self.temp_prev
            for i in range(1, total + 1):
                fecha = self.sim_time + datetime.timedelta(minutes=i)
                temp, rad = self.modelo.calcular(fecha, ctx.estados(), ef, temp)
                lecturas.append((fecha, temp, rad))
                if timezone.localtime(fecha).minute % 30 == 0:
                    estado, t_ext, prob = self.modelo.clima(fecha)
                    climas.append(HistorialClima(id_invernadero=ctx.invernadero, estado=estado,
                                                 temp_externa=round(t_ext, 1), probabilidad_lluvia=prob,
                                                 es_pronostico=False, fecha=fecha))
            with transaction.atomic():
                services.registrar_lecturas_lote(ctx.invernadero, lecturas)
                HistorialClima.objects.bulk_create(climas)
                self.motor.cerrar_todo(self.sim_time, 'Cerrada por ajuste manual del reloj del simulador.')
            self.escenario = None
            self.temp_prev = temp
            self.sim_time = objetivo
            self.ultima = (lecturas[-1][1], lecturas[-1][2], objetivo) if lecturas else self.ultima
            self._ultimo_real = time.monotonic()
            self._acarreo = 0.0
            return f'Reloj simulado ajustado a {objetivo:%d/%m %H:%M} ({total} min completados con datos sintéticos).'

    def aplicar_accion_sugerida(self):
        """Aplica la acción mecánica pendiente sugerida (manual, Ana Savio)."""
        with self.lock:
            self.sincronizar()
            ctx = self.contexto()
            if ctx is None:
                raise services.ErrorValidacion('No hay invernadero configurado.')
            codigo_alerta = self.motor.recomendacion_actual()
            codigo_objetivo = (
                codigo_alerta if codigo_alerta != 'MANTENER' else self.motor.recomendacion_preventiva()
            )
            estados = ctx.estados()
            pendientes = services.acciones_pendientes(codigo_objetivo, estados)
            codigo_efectivo = services.codigo_para_acciones(pendientes)
            if not pendientes:
                if codigo_objetivo == 'MANTENER':
                    msg = 'No hay acción pendiente: las variables están dentro del rango óptimo.'
                    sin_accion = False
                else:
                    detalle_pos = services.resumen_mecanismos_en_posicion(codigo_objetivo, estados)
                    msg = (
                        f'Sin acción mecánica pendiente ({detalle_pos}). '
                        'No se realizaron movimientos; continúe monitoreando.'
                    )
                    sin_accion = True
                return {'codigo': 'MANTENER', 'mensaje': msg, 'movimientos': 0, 'sin_accion_mecanica': sin_accion}

            with transaction.atomic():
                movs = self._aplicar(
                    ctx, codigo_efectivo, HistorialMecanismo.MANUAL, self.sim_time, services.ENCARGADA,
                    'Acción sugerida tomada por la encargada.',
                )
            txt = services.ETIQUETA_RECOMENDACION.get(codigo_efectivo, codigo_efectivo)
            detalle = ', '.join(f'{h.id_mecanismo.get_tipo_display()} → {h.a_que_estado_paso}' for h in movs)
            msg = f'Acción "{txt}" aplicada y registrada en HISTORIAL_MECANISMOS: {detalle}.'
            return {'codigo': codigo_efectivo, 'mensaje': msg, 'movimientos': len(movs), 'sin_accion_mecanica': False}

    def cambiar_mecanismo_manual(self, mecanismo, estado):
        with self.lock:
            self.sincronizar()
            fecha = self.sim_time or timezone.now()
            mec, hist = services.cambiar_estado_mecanismo(
                mecanismo, estado, HistorialMecanismo.MANUAL, fecha, services.ENCARGADA, 'Accionamiento manual desde el panel.')
            if hist is not None:
                ctx = self.contexto()
                if ctx is not None:
                    self._actualizar_mitigacion_escenario(ctx, fecha)
            return mec, hist

    # ------------------------------------------------------- generación lote
    def generar_historial(self, inicio, fin, programa=None):
        """Genera historial minuto a minuto entre inicio y fin ejecutando el motor completo.

        programa: dict {fecha(datetime, minuto exacto): [acciones]} con acciones
          ('escenario', clave) | ('mecanismo', tipo, estado, origen, usuario)
        """
        programa = programa or {}
        ctx = self.contexto()
        self.sim_time = inicio
        self.listo = True
        total = int((fin - inicio).total_seconds() // 60)
        with transaction.atomic():
            for _ in range(total):
                fecha = self.sim_time + datetime.timedelta(minutes=1)
                for accion in programa.get(fecha, []):
                    if accion[0] == 'escenario':
                        cfg = ESCENARIOS[accion[1]]
                        self.escenario = {**cfg, 'clave': accion[1], 'inicio': self.sim_time,
                                          'fin': self.sim_time + datetime.timedelta(minutes=cfg['duracion']),
                                          'mitigado': False}
                    elif accion[0] == 'mecanismo':
                        _, tipo, estado, origen, usuario = accion
                        _, hist = services.cambiar_estado_mecanismo(ctx.mecanismos[tipo], estado, origen, fecha,
                                                                    usuario, 'Operación registrada (historial inicial).')
                        if hist:
                            ctx.mecanismos[tipo].estado = estado
                self._paso(ctx, fecha)
        return total

    # ---------------------------------------------------------------- estado
    def estado(self):
        with self.lock:
            ctx = self.contexto()
            if ctx is None or not self.listo:
                return None
            p = ctx.parametro
            fecha = self.sim_time
            loc = timezone.localtime(fecha)
            h = hora_decimal(fecha)
            temp, rad, _ = self.ultima or (None, None, None)
            diurno = self.motor.es_diurno(p, fecha)

            def var_estado(var, valor, vmin, vmax, hist, evaluada):
                s = self.motor.seg[var]
                if not evaluada:
                    est = 'NO_EVALUADA'
                elif s.alerta_id:
                    est = 'ALERTA'
                elif s.fuera_desde:
                    est = 'PERSISTENCIA'
                else:
                    est = 'OK'
                en_hist = bool(s.alerta_id and valor is not None and vmin <= valor <= vmax)
                return {
                    'valor': None if valor is None else round(valor, 1), 'estado': est, 'evaluada': evaluada,
                    'direccion': s.alerta_dir or s.direccion, 'minutos_fuera': s.minutos_fuera,
                    'persistencia': p.tiempo_persistencia_min, 'en_histeresis': en_hist,
                    'min': vmin, 'max': vmax, 'histeresis': hist,
                    'fuera_de_rango': valor is not None and (valor < vmin or valor > vmax),
                }

            estados = ctx.estados()
            codigo_alerta = self.motor.recomendacion_actual()
            preventiva_raw = self.motor.recomendacion_preventiva()
            pend_act = services.acciones_pendientes(codigo_alerta, estados)
            pend_prev = services.acciones_pendientes(preventiva_raw, estados)
            codigo = services.codigo_para_acciones(pend_act)
            preventiva = services.codigo_para_acciones(pend_prev)
            pendientes_mostrar = pend_act if codigo_alerta != 'MANTENER' else pend_prev
            acciones = [
                f'{dict(Mecanismo.TIPOS)[t]} → {services.ETIQUETA_ESTADO_MECANISMO[(t, e)]}'
                for t, e in pendientes_mostrar
            ]
            sin_accion_mecanica = bool(
                (codigo_alerta != 'MANTENER' and not pend_act)
                or (codigo_alerta == 'MANTENER' and preventiva_raw != 'MANTENER' and not pend_prev)
            )
            if pend_act:
                decision_texto = services.TEXTO_CORTO_RECOMENDACION[codigo]
                decision_etiqueta = services.ETIQUETA_RECOMENDACION[codigo]
            elif codigo_alerta != 'MANTENER':
                detalle_pos = services.resumen_mecanismos_en_posicion(codigo_alerta, estados)
                decision_texto = 'Monitorear — sin acción mecánica'
                decision_etiqueta = f'Sin acción mecánica pendiente ({detalle_pos}). Mecanismos ya en posición óptima.'
            elif preventiva_raw != 'MANTENER' and not pend_prev:
                detalle_pos = services.resumen_mecanismos_en_posicion(preventiva_raw, estados)
                decision_texto = 'Mantener (sin acción mecánica)'
                decision_etiqueta = f'En evaluación de persistencia — sin acción mecánica pendiente ({detalle_pos}).'
            else:
                decision_texto = 'Mantener'
                decision_etiqueta = 'Variables dentro del rango óptimo configurado'
            esc = None
            if self.escenario:
                restante = max(0, int((self.escenario['fin'] - fecha).total_seconds() // 60))
                esc = {'clave': self.escenario['clave'], 'nombre': self.escenario['nombre'],
                       'descripcion': self.escenario['descripcion'], 'restante_min': restante,
                       'mitigado': self.escenario['mitigado'], 'clima': self.escenario['clima']}
            clima_actual = (
                ctx.invernadero.climas.filter(es_pronostico=False, fecha__lte=fecha)
                .order_by('-fecha', '-id').first()
            )
            pronos = (ctx.invernadero.climas.filter(es_pronostico=True, fecha__gt=fecha).order_by('fecha')[:4])
            activas = Alerta.objects.filter(id_invernadero=ctx.invernadero, tipo=Alerta.ALERTA, resuelta=False)
            f_rad, _ = factores_dia(loc.date())
            return {
                'sim': {
                    'iso': loc.isoformat(), 'hora': loc.strftime('%H:%M'), 'fecha': loc.strftime('%d/%m/%Y'),
                    'dia_semana': ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo'][loc.weekday()],
                    'ms': services.ms_local(fecha), 'hora_decimal': round(h, 3), 'velocidad': self.velocidad,
                    'velocidades': VELOCIDADES, 'pausado': self.pausado, 'automatico': self.automatico,
                    'diurno': diurno, 'amanecer': AMANECER, 'ocaso': OCASO,
                    'fraccion_solar': round(fraccion_solar(h), 3),
                    'rad_despejado': round(PICO_RAD_INTERIOR * f_rad * fraccion_solar(h) ** 1.3, 1),
                    'escenario': esc, 'escenarios': {k: v['nombre'] for k, v in ESCENARIOS.items()},
                },
                'invernadero': {'id': ctx.invernadero.id, 'nombre': ctx.invernadero.nombre},
                'cultivo': {'id': ctx.invernadero.id_cultivo.id, 'nombre': ctx.invernadero.id_cultivo.nombre,
                            'recomendaciones': ctx.invernadero.id_cultivo.recomendaciones},
                'parametros': services.parametros_dict(p),
                'variables': {
                    'RADIACION': var_estado(RAD, rad, p.rad_min, p.rad_max, p.histeresis_rad, diurno),
                    'TEMPERATURA': var_estado(TEMP, temp, p.temp_min, p.temp_max, p.histeresis_temp, True),
                },
                'decision': {
                    'codigo': codigo,
                    'codigo_alerta': codigo_alerta,
                    'texto': decision_texto,
                    'etiqueta': decision_etiqueta,
                    'preventiva': preventiva,
                    'preventiva_texto': services.TEXTO_CORTO_RECOMENDACION[preventiva],
                    'acciones': acciones,
                    'sin_accion_mecanica': sin_accion_mecanica,
                },
                'mecanismos': [services.mecanismo_dict(m) for m in ctx.mecanismos.values()],
                'clima': {
                    'actual': {'estado': clima_actual.estado, 'estado_txt': clima_actual.get_estado_display(),
                               'temp_externa': clima_actual.temp_externa,
                               'probabilidad_lluvia': clima_actual.probabilidad_lluvia} if clima_actual else None,
                    'pronostico': [{'hora': timezone.localtime(c.fecha).strftime('%d/%m %H:%M'), 'estado': c.estado,
                                    'estado_txt': c.get_estado_display(), 'temp_externa': c.temp_externa,
                                    'probabilidad_lluvia': c.probabilidad_lluvia} for c in pronos],
                },
                'alertas_activas': [services.alerta_dict(a) for a in activas],
            }


SIMULADOR = Simulador()
