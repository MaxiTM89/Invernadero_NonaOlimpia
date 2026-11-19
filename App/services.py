"""
Capa de servicios - Casos de uso del sistema Invernadero Nona Olimpia.

Todos los casos de uso que escriben en SQLite se ejecutan dentro de
``transaction.atomic()`` para garantizar consistencia:

* Ingresar Cultivo (alta / edición / asignación al invernadero)
* Registrar Valores (lecturas de sensores y clima)
* Evaluar Datos y Generar Alertas  (<<extend>>  -> ver engine.py)
* Establecer Parámetros para Automatizar (con HISTORIAL_PARAMETROS)
* Activar Mecanismos y Registrar Movimientos (<<include>> HISTORIAL_MECANISMOS)
"""
import datetime
from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import (
    Alerta, Cultivo, HistorialClima, HistorialMecanismo, HistorialParametro,
    HistorialRadiacion, HistorialTemperatura, Invernadero, Mecanismo, Parametro,
)

ENCARGADA = settings.NONA_OLIMPIA.get('ENCARGADA', 'Ana Savio')
USUARIO_AUTOMATICO = 'Sistema (modo automático)'

PARAMETROS_POR_DEFECTO = {
    'temp_min': 10.0,
    'temp_max': 25.0,
    'rad_min': 150.0,
    'rad_max': 600.0,
    'tiempo_persistencia_min': 10,
    'histeresis_temp': 1.0,
    'histeresis_rad': 40.0,
    'hora_inicio_diurna': datetime.time(9, 0),
    'hora_fin_diurna': datetime.time(16, 30),
}

CAMPOS_PARAMETROS = list(PARAMETROS_POR_DEFECTO.keys())

# ---------------------------------------------------------------------------
# Recomendaciones -> acciones sobre mecanismos
# ---------------------------------------------------------------------------
ACCIONES_RECOMENDACION = {
    'SOMBREAR': [(Mecanismo.MEDIA_SOMBRA, 'CERRADA')],
    'VENTILAR': [(Mecanismo.VENTILACION, 'ABIERTA')],
    'SOMBREAR_Y_VENTILAR': [(Mecanismo.MEDIA_SOMBRA, 'CERRADA'), (Mecanismo.VENTILACION, 'ABIERTA')],
    'ABRIR_MEDIA_SOMBRA': [(Mecanismo.MEDIA_SOMBRA, 'ABIERTA')],
    'CERRAR_VENTILACION': [(Mecanismo.VENTILACION, 'CERRADA')],
    'VENTILAR_Y_ABRIR_MEDIA_SOMBRA': [(Mecanismo.VENTILACION, 'ABIERTA'), (Mecanismo.MEDIA_SOMBRA, 'ABIERTA')],
    'SOMBREAR_Y_CERRAR_VENTILACION': [(Mecanismo.MEDIA_SOMBRA, 'CERRADA'), (Mecanismo.VENTILACION, 'CERRADA')],
    'ABRIR_MEDIA_SOMBRA_Y_CERRAR_VENTILACION': [(Mecanismo.MEDIA_SOMBRA, 'ABIERTA'), (Mecanismo.VENTILACION, 'CERRADA')],
    'MANTENER': [],
}

TEXTO_CORTO_RECOMENDACION = {
    'SOMBREAR': 'Sombrear',
    'VENTILAR': 'Ventilar',
    'SOMBREAR_Y_VENTILAR': 'Sombrear y ventilar',
    'ABRIR_MEDIA_SOMBRA': 'Abrir media sombra',
    'CERRAR_VENTILACION': 'Cerrar ventilación',
    'VENTILAR_Y_ABRIR_MEDIA_SOMBRA': 'Ventilar y abrir media sombra',
    'SOMBREAR_Y_CERRAR_VENTILACION': 'Sombrear y cerrar ventilación',
    'ABRIR_MEDIA_SOMBRA_Y_CERRAR_VENTILACION': 'Abrir media sombra y cerrar ventilación',
    'MANTENER': 'Mantener',
}

ETIQUETA_RECOMENDACION = dict(Alerta.RECOMENDACIONES)

ETIQUETA_ESTADO_MECANISMO = {
    (Mecanismo.MEDIA_SOMBRA, 'CERRADA'): 'Cerrada (desplegada, sombreando)',
    (Mecanismo.MEDIA_SOMBRA, 'ABIERTA'): 'Abierta (recogida)',
    (Mecanismo.VENTILACION, 'ABIERTA'): 'Abierta (ventilando)',
    (Mecanismo.VENTILACION, 'CERRADA'): 'Cerrada',
}


class ErrorValidacion(Exception):
    """Error de reglas de negocio que se informa al usuario."""


def calcular_recomendacion(dir_radiacion, dir_temperatura):
    """Determina la recomendación exacta según la dirección del desvío de cada variable.

    dir_* puede ser 'ALTA', 'BAJA' o None.
    """
    tabla = {
        ('ALTA', 'ALTA'): 'SOMBREAR_Y_VENTILAR',
        ('ALTA', None): 'SOMBREAR',
        (None, 'ALTA'): 'VENTILAR',
        ('BAJA', None): 'ABRIR_MEDIA_SOMBRA',
        (None, 'BAJA'): 'CERRAR_VENTILACION',
        ('BAJA', 'ALTA'): 'VENTILAR_Y_ABRIR_MEDIA_SOMBRA',
        ('ALTA', 'BAJA'): 'SOMBREAR_Y_CERRAR_VENTILACION',
        ('BAJA', 'BAJA'): 'ABRIR_MEDIA_SOMBRA_Y_CERRAR_VENTILACION',
        (None, None): 'MANTENER',
    }
    return tabla[(dir_radiacion, dir_temperatura)]


_CODIGO_POR_ACCIONES = {frozenset(accs): cod for cod, accs in ACCIONES_RECOMENDACION.items()}
_NOMBRE_CORTO_MECANISMO = {
    Mecanismo.MEDIA_SOMBRA: 'media sombra',
    Mecanismo.VENTILACION: 'ventilación',
}


def acciones_pendientes(codigo, estados_mecanismos):
    """Devuelve únicamente las tuplas (tipo, estado_objetivo) que aún no están en ese estado."""
    estados = estados_mecanismos or {}
    return [
        (tipo, objetivo)
        for tipo, objetivo in ACCIONES_RECOMENDACION.get(codigo, [])
        if estados.get(tipo) != objetivo
    ]


def codigo_para_acciones(pendientes):
    """Mapea un subconjunto de acciones pendientes al código de recomendación correspondiente."""
    return _CODIGO_POR_ACCIONES.get(frozenset(pendientes), 'MANTENER')


def resumen_mecanismos_en_posicion(codigo, estados_mecanismos):
    """Describe brevemente los mecanismos de una recomendación que ya están en posición."""
    estados = estados_mecanismos or {}
    partes = []
    for tipo, objetivo in ACCIONES_RECOMENDACION.get(codigo, []):
        if estados.get(tipo) == objetivo:
            nombre = _NOMBRE_CORTO_MECANISMO.get(tipo, tipo.lower())
            partes.append(f'{nombre} ya {objetivo.lower()}')
    if not partes:
        return 'mecanismos ya en posición óptima'
    return ' y '.join(partes)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def obtener_invernadero_activo():
    return (Invernadero.objects.select_related('id_cultivo', 'id_cultivo__parametros')
            .order_by('id').first())


def a_local(dt):
    return timezone.localtime(dt) if dt else None


def ms_local(dt):
    """Epoch (ms) del reloj de pared local (Jujuy) tratado como UTC, para graficar sin desfasajes."""
    loc = timezone.localtime(dt)
    return int(loc.replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def rango_jornada(dia):
    tz = timezone.get_current_timezone()
    inicio = datetime.datetime.combine(dia, datetime.time.min, tzinfo=tz)
    return inicio, inicio + datetime.timedelta(days=1)


def _parse_hora(valor):
    if isinstance(valor, datetime.time):
        return valor
    try:
        h, m = str(valor).split(':')[:2]
        return datetime.time(int(h), int(m))
    except (ValueError, TypeError):
        raise ErrorValidacion(f'Hora inválida: {valor!r} (formato HH:MM)')


# ---------------------------------------------------------------------------
# CU: Establecer Parámetros para Automatizar
# ---------------------------------------------------------------------------
def validar_parametros(datos):
    limpio = {}
    try:
        for campo in ('temp_min', 'temp_max', 'rad_min', 'rad_max', 'histeresis_temp', 'histeresis_rad'):
            limpio[campo] = round(float(datos[campo]), 2)
        limpio['tiempo_persistencia_min'] = int(float(datos['tiempo_persistencia_min']))
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorValidacion(f'Parámetro numérico inválido o faltante: {exc}')
    limpio['hora_inicio_diurna'] = _parse_hora(datos['hora_inicio_diurna'])
    limpio['hora_fin_diurna'] = _parse_hora(datos['hora_fin_diurna'])

    if not -10 <= limpio['temp_min'] < limpio['temp_max'] <= 60:
        raise ErrorValidacion('La temperatura mínima debe ser menor que la máxima (rango -10 a 60 °C).')
    if not 0 <= limpio['rad_min'] < limpio['rad_max'] <= 1500:
        raise ErrorValidacion('La radiación mínima debe ser menor que la máxima (rango 0 a 1500 W/m²).')
    if not 1 <= limpio['tiempo_persistencia_min'] <= 240:
        raise ErrorValidacion('La persistencia debe estar entre 1 y 240 minutos.')
    if not 0 <= limpio['histeresis_temp'] < (limpio['temp_max'] - limpio['temp_min']) / 2:
        raise ErrorValidacion('La histéresis de temperatura debe ser ≥ 0 y menor que la mitad del rango.')
    if not 0 <= limpio['histeresis_rad'] < (limpio['rad_max'] - limpio['rad_min']) / 2:
        raise ErrorValidacion('La histéresis de radiación debe ser ≥ 0 y menor que la mitad del rango.')
    if limpio['hora_inicio_diurna'] >= limpio['hora_fin_diurna']:
        raise ErrorValidacion('La franja diurna debe iniciar antes de finalizar.')
    return limpio


def _snapshot_historial(parametro, usuario, motivo):
    return HistorialParametro.objects.create(
        id_parametro=parametro,
        **{c: getattr(parametro, c) for c in CAMPOS_PARAMETROS},
        usuario=usuario, motivo=motivo[:200],
    )


@transaction.atomic
def actualizar_parametros(parametro, datos, usuario=ENCARGADA, motivo='Actualización de umbrales'):
    """Actualiza umbrales y persistencia; guarda copia histórica automáticamente."""
    parametro = Parametro.objects.select_for_update().get(pk=parametro.pk)
    actuales = {c: getattr(parametro, c) for c in CAMPOS_PARAMETROS}
    combinados = {**actuales, **{k: v for k, v in datos.items() if k in CAMPOS_PARAMETROS and v not in (None, '')}}
    limpio = validar_parametros(combinados)
    cambios = [c for c in CAMPOS_PARAMETROS if limpio[c] != actuales[c]]
    if not cambios:
        return parametro, []
    for campo in cambios:
        setattr(parametro, campo, limpio[campo])
    parametro.save()
    _snapshot_historial(parametro, usuario, motivo or f'Cambios: {", ".join(cambios)}')
    return parametro, cambios


# ---------------------------------------------------------------------------
# CU: Ingresar Cultivo
# ---------------------------------------------------------------------------
@transaction.atomic
def guardar_cultivo(nombre, recomendaciones='', cultivo_id=None, parametros=None,
                    invernadero=None, asignar=False, usuario=ENCARGADA):
    nombre = (nombre or '').strip()
    if len(nombre) < 3:
        raise ErrorValidacion('El nombre del cultivo debe tener al menos 3 caracteres.')
    duplicado = Cultivo.objects.filter(nombre__iexact=nombre)
    if cultivo_id:
        duplicado = duplicado.exclude(pk=cultivo_id)
    if duplicado.exists():
        raise ErrorValidacion(f'Ya existe un cultivo llamado "{nombre}".')

    if cultivo_id:
        try:
            cultivo = Cultivo.objects.select_for_update().get(pk=cultivo_id)
        except Cultivo.DoesNotExist:
            raise ErrorValidacion('El cultivo indicado no existe.')
        cultivo.nombre = nombre
        cultivo.recomendaciones = recomendaciones or ''
        cultivo.save()
    else:
        cultivo = Cultivo.objects.create(nombre=nombre, recomendaciones=recomendaciones or '')

    parametro = Parametro.objects.filter(id_cultivo=cultivo).first()
    if parametro is None:
        datos = {**PARAMETROS_POR_DEFECTO, **{k: v for k, v in (parametros or {}).items()
                                              if k in CAMPOS_PARAMETROS and v not in (None, '')}}
        parametro = Parametro.objects.create(id_cultivo=cultivo, **validar_parametros(datos))
        _snapshot_historial(parametro, usuario, f'Alta de parámetros del cultivo {cultivo.nombre}')
    elif parametros:
        actualizar_parametros(parametro, parametros, usuario, f'Edición del cultivo {cultivo.nombre}')

    if asignar and invernadero is not None:
        asignar_cultivo(invernadero, cultivo)
    return cultivo


@transaction.atomic
def asignar_cultivo(invernadero, cultivo):
    if not hasattr(cultivo, 'parametros'):
        raise ErrorValidacion('El cultivo no tiene parámetros configurados.')
    invernadero.id_cultivo = cultivo
    invernadero.save(update_fields=['id_cultivo'])
    return invernadero


# ---------------------------------------------------------------------------
# CU: Registrar Valores (sensores y clima)
# ---------------------------------------------------------------------------
@transaction.atomic
def registrar_lectura(invernadero, fecha, grados, radiacion):
    t = HistorialTemperatura.objects.create(id_invernadero=invernadero, grados=round(grados, 2), fecha=fecha)
    r = HistorialRadiacion.objects.create(id_invernadero=invernadero, valor=round(max(0.0, radiacion), 1), fecha=fecha)
    return t, r


@transaction.atomic
def registrar_lecturas_lote(invernadero, lecturas):
    """lecturas: iterable de (fecha, grados, radiacion). Inserción masiva atómica."""
    temps, rads = [], []
    for fecha, grados, rad in lecturas:
        temps.append(HistorialTemperatura(id_invernadero=invernadero, grados=round(grados, 2), fecha=fecha))
        rads.append(HistorialRadiacion(id_invernadero=invernadero, valor=round(max(0.0, rad), 1), fecha=fecha))
    HistorialTemperatura.objects.bulk_create(temps, batch_size=500)
    HistorialRadiacion.objects.bulk_create(rads, batch_size=500)
    return len(temps)


@transaction.atomic
def registrar_clima(invernadero, fecha, estado, temp_externa, probabilidad_lluvia, es_pronostico=False):
    if es_pronostico:
        # Un pronóstico nuevo reemplaza al anterior para el mismo instante objetivo.
        HistorialClima.objects.filter(id_invernadero=invernadero, es_pronostico=True, fecha=fecha).delete()
    return HistorialClima.objects.create(
        id_invernadero=invernadero, estado=estado, temp_externa=round(temp_externa, 1),
        probabilidad_lluvia=int(max(0, min(100, probabilidad_lluvia))), es_pronostico=es_pronostico, fecha=fecha,
    )


# ---------------------------------------------------------------------------
# CU: Activar Mecanismos y Registrar Movimientos (<<include>>)
# ---------------------------------------------------------------------------
@transaction.atomic
def cambiar_estado_mecanismo(mecanismo, nuevo_estado, origen, fecha, usuario=None, detalle=''):
    """Actualiza MECANISMOS e inserta obligatoriamente el movimiento en HISTORIAL_MECANISMOS.

    Devuelve (mecanismo, historial|None). Si el estado no cambia no hay movimiento.
    """
    nuevo_estado = str(nuevo_estado).upper() if nuevo_estado is not None else ''
    validos = Mecanismo.ESTADOS_VALIDOS[mecanismo.tipo]
    if nuevo_estado not in validos:
        raise ErrorValidacion(f'Estado inválido para {mecanismo.get_tipo_display()}: use {", ".join(validos)}.')
    if origen not in (HistorialMecanismo.MANUAL, HistorialMecanismo.AUTOMATICO):
        raise ErrorValidacion('Origen inválido (MANUAL o AUTOMATICO).')
    mecanismo = Mecanismo.objects.select_for_update().get(pk=mecanismo.pk)
    if mecanismo.estado == nuevo_estado:
        return mecanismo, None
    mecanismo.estado = nuevo_estado
    mecanismo.save(update_fields=['estado'])
    historial = HistorialMecanismo.objects.create(
        id_mecanismo=mecanismo, fecha=fecha, a_que_estado_paso=nuevo_estado, origen=origen,
        usuario=usuario or (ENCARGADA if origen == HistorialMecanismo.MANUAL else USUARIO_AUTOMATICO),
        detalle=detalle[:255],
    )
    return mecanismo, historial


@transaction.atomic
def aplicar_recomendacion(invernadero, codigo, origen, fecha, usuario=None, detalle=''):
    """Ejecuta sobre los mecanismos las acciones asociadas a una recomendación."""
    movimientos = []
    for tipo, estado in ACCIONES_RECOMENDACION.get(codigo, []):
        mec = Mecanismo.objects.filter(id_invernadero=invernadero, tipo=tipo).first()
        if mec is None:
            continue
        _, hist = cambiar_estado_mecanismo(
            mec, estado, origen, fecha, usuario,
            detalle or f'Acción recomendada: {ETIQUETA_RECOMENDACION.get(codigo, codigo)}',
        )
        if hist:
            movimientos.append(hist)
    return movimientos


# ---------------------------------------------------------------------------
# CU: Evaluar Datos y Generar Alertas (<<extend>>) - persistencia en ALERTAS
# ---------------------------------------------------------------------------
UNIDAD = {Alerta.TEMPERATURA: '°C', Alerta.RADIACION: 'W/m²'}
NOMBRE_VAR = {Alerta.TEMPERATURA: 'Temperatura interna', Alerta.RADIACION: 'Radiación solar'}


def _fmt(variable, valor):
    return f'{valor:.1f} °C' if variable == Alerta.TEMPERATURA else f'{valor:.0f} W/m²'


@transaction.atomic
def crear_alerta(invernadero, variable, direccion, valor, pico, limite, duracion_min, fecha, recomendacion):
    lado = 'por encima del máximo' if direccion == 'ALTA' else 'por debajo del mínimo'
    descripcion = (
        f'{NOMBRE_VAR[variable]} {lado} ({_fmt(variable, limite)}) durante {duracion_min} min '
        f'(supera la persistencia configurada). Valor pico: {_fmt(variable, pico)}. '
        f'Recomendación: {ETIQUETA_RECOMENDACION.get(recomendacion, recomendacion)}.'
    )
    return Alerta.objects.create(
        id_invernadero=invernadero, variable=variable, valor_detectado=round(valor, 2),
        descripcion=descripcion, fecha=fecha, resuelta=False, tipo=Alerta.ALERTA,
        direccion=direccion, recomendacion=recomendacion, valor_pico=round(pico, 2),
        duracion_min=duracion_min,
    )


@transaction.atomic
def registrar_transitoria(invernadero, variable, direccion, pico, limite, duracion_min, fecha_inicio, persistencia):
    lado = 'sobre el máximo' if direccion == 'ALTA' else 'bajo el mínimo'
    descripcion = (
        f'Variación transitoria filtrada: {NOMBRE_VAR[variable].lower()} {lado} '
        f'({_fmt(variable, limite)}) por {duracion_min} min, menor a la persistencia de {persistencia} min. '
        f'Pico: {_fmt(variable, pico)}. No se emitió alerta.'
    )
    return Alerta.objects.create(
        id_invernadero=invernadero, variable=variable, valor_detectado=round(pico, 2),
        descripcion=descripcion, fecha=fecha_inicio, resuelta=True, tipo=Alerta.TRANSITORIA,
        direccion=direccion, recomendacion='MANTENER', valor_pico=round(pico, 2),
        duracion_min=duracion_min, fecha_resolucion=fecha_inicio + datetime.timedelta(minutes=duracion_min),
    )


@transaction.atomic
def resolver_alerta(alerta_id, fecha, valor, motivo=''):
    alerta = Alerta.objects.select_for_update().filter(pk=alerta_id, resuelta=False).first()
    if alerta is None:
        return None
    alerta.resuelta = True
    alerta.fecha_resolucion = fecha
    alerta.duracion_min = max(alerta.duracion_min, int((fecha - alerta.fecha).total_seconds() // 60))
    nota = motivo or f'Normalizada con histéresis de retorno (valor {_fmt(alerta.variable, valor)}).'
    alerta.descripcion = f'{alerta.descripcion} | {nota}'
    alerta.save(update_fields=['resuelta', 'fecha_resolucion', 'duracion_min', 'descripcion'])
    return alerta


# ---------------------------------------------------------------------------
# Consultas (lectura) para las vistas
# ---------------------------------------------------------------------------
def parametros_dict(p):
    return {
        'id': p.id,
        'temp_min': p.temp_min, 'temp_max': p.temp_max,
        'rad_min': p.rad_min, 'rad_max': p.rad_max,
        'tiempo_persistencia_min': p.tiempo_persistencia_min,
        'histeresis_temp': p.histeresis_temp, 'histeresis_rad': p.histeresis_rad,
        'hora_inicio_diurna': p.hora_inicio_diurna.strftime('%H:%M'),
        'hora_fin_diurna': p.hora_fin_diurna.strftime('%H:%M'),
        'fecha_actualizacion': a_local(p.fecha_actualizacion).strftime('%d/%m/%Y %H:%M:%S'),
    }


def mecanismo_dict(m):
    return {
        'id': m.id, 'tipo': m.tipo, 'nombre': m.get_tipo_display(), 'estado': m.estado,
        'etiqueta': ETIQUETA_ESTADO_MECANISMO.get((m.tipo, m.estado), m.estado),
        'estados_validos': Mecanismo.ESTADOS_VALIDOS[m.tipo],
    }


def alerta_dict(a):
    return {
        'id': a.id, 'tipo': a.tipo, 'variable': a.variable, 'direccion': a.direccion,
        'valor': a.valor_detectado, 'pico': a.valor_pico, 'recomendacion': a.recomendacion,
        'recomendacion_txt': ETIQUETA_RECOMENDACION.get(a.recomendacion, a.recomendacion),
        'descripcion': a.descripcion, 'duracion_min': a.duracion_min, 'resuelta': a.resuelta,
        'fecha': a_local(a.fecha).strftime('%d/%m/%Y %H:%M'),
    }


def series(invernadero, desde, hasta):
    temps = (HistorialTemperatura.objects.filter(id_invernadero=invernadero, fecha__gt=desde, fecha__lte=hasta)
             .order_by('fecha').values_list('fecha', 'grados'))
    rads = (HistorialRadiacion.objects.filter(id_invernadero=invernadero, fecha__gt=desde, fecha__lte=hasta)
            .order_by('fecha').values_list('fecha', 'valor'))
    marcas = (Alerta.objects.filter(id_invernadero=invernadero, fecha__gt=desde, fecha__lte=hasta)
              .order_by('fecha'))
    movimientos = (HistorialMecanismo.objects.filter(id_mecanismo__id_invernadero=invernadero,
                                                     fecha__gt=desde, fecha__lte=hasta)
                   .select_related('id_mecanismo').order_by('fecha'))
    return {
        'temperatura': [[ms_local(f), v] for f, v in temps],
        'radiacion': [[ms_local(f), v] for f, v in rads],
        'marcas': [{
            't': ms_local(a.fecha), 'variable': a.variable, 'tipo': a.tipo, 'valor': a.valor_pico or a.valor_detectado,
            'texto': (f'{"ALERTA" if a.tipo == Alerta.ALERTA else "Transitoria"} · {a.duracion_min} min · '
                      f'{ETIQUETA_RECOMENDACION.get(a.recomendacion) if a.tipo == Alerta.ALERTA else "filtrada"}'),
        } for a in marcas],
        'movimientos': [{
            't': ms_local(m.fecha), 'tipo': m.id_mecanismo.tipo, 'estado': m.a_que_estado_paso, 'origen': m.origen,
        } for m in movimientos],
        'desde': ms_local(desde), 'hasta': ms_local(hasta),
    }


def dias_disponibles(invernadero):
    dias = (HistorialTemperatura.objects.filter(id_invernadero=invernadero)
            .annotate(d=TruncDate('fecha')).values('d').annotate(n=Count('id')).order_by('-d'))
    return [{'dia': x['d'].isoformat(), 'lecturas': x['n']} for x in dias if x['d']]


def estadisticas_jornada(invernadero, dia, parametro):
    inicio, fin = rango_jornada(dia)
    temps = list(HistorialTemperatura.objects.filter(id_invernadero=invernadero, fecha__gte=inicio, fecha__lt=fin)
                 .order_by('fecha').values_list('fecha', 'grados'))
    rads = list(HistorialRadiacion.objects.filter(id_invernadero=invernadero, fecha__gte=inicio, fecha__lt=fin)
                .order_by('fecha').values_list('fecha', 'valor'))
    p = parametro
    criticos = [0] * 24
    fuera_temp = 0
    buckets = defaultdict(list)
    for f, v in temps:
        loc = timezone.localtime(f)
        minuto = loc.hour * 60 + loc.minute
        buckets[minuto // 5 * 5].append(v)
        if v < p.temp_min or v > p.temp_max:
            fuera_temp += 1
            criticos[loc.hour] += 1
    fuera_rad = 0
    for f, v in rads:
        loc = timezone.localtime(f)
        if p.hora_inicio_diurna <= loc.time() <= p.hora_fin_diurna and (v < p.rad_min or v > p.rad_max):
            fuera_rad += 1
            criticos[loc.hour] += 1
    valores = [v for _, v in temps]
    alertas = Alerta.objects.filter(id_invernadero=invernadero, fecha__gte=inicio, fecha__lt=fin)
    movs = HistorialMecanismo.objects.filter(id_mecanismo__id_invernadero=invernadero, fecha__gte=inicio, fecha__lt=fin)
    return {
        'dia': dia.isoformat(),
        'lecturas': len(valores),
        'temp_max': round(max(valores), 1) if valores else None,
        'temp_min': round(min(valores), 1) if valores else None,
        'temp_media': round(sum(valores) / len(valores), 1) if valores else None,
        'rad_max': round(max(v for _, v in rads), 0) if rads else None,
        'min_fuera_temp': fuera_temp,
        'min_fuera_rad': fuera_rad,
        'alertas': alertas.filter(tipo=Alerta.ALERTA).count(),
        'transitorias': alertas.filter(tipo=Alerta.TRANSITORIA).count(),
        'movimientos': movs.count(),
        'horas_criticas': criticos,  # minutos fuera de rango por hora (temp + rad)
        'serie': [[m, round(sum(vs) / len(vs), 2)] for m, vs in sorted(buckets.items())],
    }


TIPOS_EVENTO = ['ALERTA', 'RECOMENDACION', 'TRANSITORIA', 'NORMALIZACION', 'MOVIMIENTO']


def eventos(invernadero, tipo='', variable='', dia=None, limite=1500):
    """Línea de tiempo unificada: alertas, recomendaciones, transitorias, normalizaciones y movimientos."""
    filas = []
    alertas = Alerta.objects.filter(id_invernadero=invernadero)
    movimientos = HistorialMecanismo.objects.filter(id_mecanismo__id_invernadero=invernadero).select_related('id_mecanismo')
    if dia:
        inicio, fin = rango_jornada(dia)
        from django.db.models import Q
        alertas = alertas.filter(Q(fecha__gte=inicio, fecha__lt=fin) | Q(fecha_resolucion__gte=inicio, fecha_resolucion__lt=fin))
        movimientos = movimientos.filter(fecha__gte=inicio, fecha__lt=fin)
        en_dia = lambda f: f is not None and inicio <= f < fin  # noqa: E731
    else:
        en_dia = lambda f: f is not None  # noqa: E731

    if variable in (Alerta.TEMPERATURA, Alerta.RADIACION):
        alertas = alertas.filter(variable=variable)
    incluir_mov = variable in ('', 'MECANISMO') and tipo in ('', 'MOVIMIENTO')
    incluir_alertas = variable != 'MECANISMO' and tipo != 'MOVIMIENTO'

    if incluir_alertas:
        if tipo == Alerta.TRANSITORIA:
            alertas = alertas.filter(tipo=Alerta.TRANSITORIA)
        elif tipo in ('ALERTA', 'RECOMENDACION', 'NORMALIZACION'):
            alertas = alertas.filter(tipo=Alerta.ALERTA)
            if tipo == 'NORMALIZACION':
                alertas = alertas.filter(resuelta=True)
        orden_alertas = ('-fecha_resolucion', '-fecha') if tipo == 'NORMALIZACION' else ('-fecha',)
        for a in alertas.order_by(*orden_alertas)[:limite]:
            unidad_valor = _fmt(a.variable, a.valor_pico if a.valor_pico is not None else a.valor_detectado)
            if a.tipo == Alerta.TRANSITORIA:
                if en_dia(a.fecha):
                    filas.append(('TRANSITORIA', a.fecha, a.variable, unidad_valor, f'{a.duracion_min} min',
                                  a.descripcion, 'Sistema'))
                continue
            if en_dia(a.fecha):
                filas.append(('ALERTA', a.fecha, a.variable, unidad_valor, f'{a.duracion_min} min',
                              a.descripcion.split(' | ')[0], 'Sistema'))
                filas.append(('RECOMENDACION', a.fecha, a.variable, ETIQUETA_RECOMENDACION.get(a.recomendacion, ''),
                              '', f'Acción sugerida: {ETIQUETA_RECOMENDACION.get(a.recomendacion, a.recomendacion)}',
                              'Motor de evaluación'))
            if a.resuelta and en_dia(a.fecha_resolucion):
                nota = a.descripcion.split(' | ')[-1] if ' | ' in a.descripcion else 'Variable normalizada.'
                filas.append(('NORMALIZACION', a.fecha_resolucion, a.variable, unidad_valor,
                              f'{a.duracion_min} min', nota, 'Sistema'))
    if incluir_mov:
        for m in movimientos.order_by('-fecha')[:limite]:
            filas.append(('MOVIMIENTO', m.fecha, 'MECANISMO', m.a_que_estado_paso, m.origen,
                          f'{m.id_mecanismo.get_tipo_display()} → '
                          f'{ETIQUETA_ESTADO_MECANISMO.get((m.id_mecanismo.tipo, m.a_que_estado_paso), m.a_que_estado_paso)}'
                          f'{". " + m.detalle if m.detalle else ""}', m.usuario))
    if tipo:
        filas = [f for f in filas if f[0] == tipo]
    filas.sort(key=lambda f: f[1], reverse=True)
    return [{
        'tipo': t, 'fecha': a_local(f).strftime('%d/%m/%Y %H:%M'), 'ts': ms_local(f), 'variable': v,
        'valor': val, 'extra': extra, 'descripcion': desc, 'usuario': usr,
    } for t, f, v, val, extra, desc, usr in filas[:limite]]


def historial_parametros(parametro, limite=200):
    return [{
        'id': h.id, 'fecha': a_local(h.fecha).strftime('%d/%m/%Y %H:%M:%S'),
        'temp_min': h.temp_min, 'temp_max': h.temp_max, 'rad_min': h.rad_min, 'rad_max': h.rad_max,
        'tiempo_persistencia_min': h.tiempo_persistencia_min, 'histeresis_temp': h.histeresis_temp,
        'histeresis_rad': h.histeresis_rad, 'franja': f'{h.hora_inicio_diurna:%H:%M}–{h.hora_fin_diurna:%H:%M}',
        'usuario': h.usuario, 'motivo': h.motivo,
    } for h in parametro.historial.all()[:limite]]
