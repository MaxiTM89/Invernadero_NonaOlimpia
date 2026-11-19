"""Precarga inicial (seed) del sistema Invernadero Nona Olimpia."""
import datetime

from django.db import transaction
from django.utils import timezone

from . import engine, services
from .models import (
    Alerta, Cultivo, HistorialClima, HistorialMecanismo, HistorialParametro, HistorialRadiacion,
    HistorialTemperatura, Invernadero, Mecanismo, Parametro,
)

USUARIO_SEED = 'Sistema (carga inicial)'

RECOMENDACIONES_SALANOVA = (
    'Lechuga Salanova (Rijk Zwaan): cultivo de hoja de ciclo corto (45-60 días). '
    'Temperatura óptima diurna 15-22 °C; tolera 10-25 °C. Por encima de 25 °C aumenta el riesgo de '
    'espigado (bolting), tipburn y amargor. Radiación interior recomendada 150-600 W/m² en la franja '
    '09:00-16:30; en Jujuy (alta radiación de altura) desplegar media sombra al 50 % en horas pico. '
    'Mantener ventilación lateral adecuada para evitar acumulación térmica.'
)


def _limpiar_todo():
    for modelo in (HistorialMecanismo, Alerta, HistorialClima, HistorialRadiacion, HistorialTemperatura,
                   Mecanismo, Invernadero, HistorialParametro, Parametro, Cultivo):
        modelo.objects.all().delete()


def _programa(inicio, fin):
    """Eventos de una jornada típica ubicados dentro de la ventana [inicio, fin]."""
    tz = timezone.get_current_timezone()
    programa = {}

    def ultimo(hh, mm, duracion):
        d = timezone.localtime(fin).date()
        for _ in range(4):
            dt = datetime.datetime.combine(d, datetime.time(hh, mm), tzinfo=tz)
            if inicio < dt and dt + datetime.timedelta(minutes=duracion) <= fin:
                return dt
            d -= datetime.timedelta(days=1)
        return None

    def agregar(dt, accion):
        if dt is not None and inicio < dt <= fin:
            programa.setdefault(dt, []).append(accion)

    ms, ve = Mecanismo.MEDIA_SOMBRA, Mecanismo.VENTILACION
    man = HistorialMecanismo.MANUAL
    ana = services.ENCARGADA
    m = lambda x: datetime.timedelta(minutes=x)  # noqa: E731

    t = ultimo(10, 0, 50)
    if t:
        agregar(t, ('escenario', 'nublado_45'))
    t = ultimo(11, 20, 10)
    if t:
        agregar(t, ('escenario', 'nube_5'))
    t = ultimo(12, 30, 35)
    if t:
        agregar(t, ('escenario', 'sol_30'))
        agregar(t + m(285), ('mecanismo', ms, 'ABIERTA', man, ana))
    t = ultimo(14, 0, 65)
    if t:
        agregar(t, ('escenario', 'calor_60'))
        agregar(t + m(80), ('mecanismo', ve, 'CERRADA', man, ana))
    t = ultimo(15, 45, 45)
    if t:
        agregar(t, ('escenario', 'sol_calor_45'))
        agregar(t + m(90), ('mecanismo', ms, 'ABIERTA', man, ana))
        agregar(t + m(100), ('mecanismo', ve, 'CERRADA', man, ana))
    return programa


def ejecutar_seed(horas=24, reiniciar=False, log=print):
    """Precarga cultivo, invernadero, mecanismos y N horas de historial sintético.

    Devuelve False si ya existían datos y no se pidió reiniciar.
    """
    if Invernadero.objects.exists() and not reiniciar:
        log('La base ya contiene datos. Use --reset para regenerarla.')
        return False

    with transaction.atomic():
        from django.contrib.auth.models import User
        admin_user, _ = User.objects.update_or_create(
            username='admin',
            defaults={
                'email': 'anasavio@nonaolimpia.com',
                'first_name': 'Ana',
                'last_name': 'Savio',
                'is_staff': True,
                'is_superuser': True,
            },
        )
        admin_user.set_password('nona1234')
        admin_user.save()
        _limpiar_todo()
        lechuga = services.guardar_cultivo('Lechuga Salanova', RECOMENDACIONES_SALANOVA,
                                           parametros=services.PARAMETROS_POR_DEFECTO, usuario=USUARIO_SEED)
        services.guardar_cultivo(
            'Rúcula', 'Hoja de ciclo corto, tolera algo más de radiación. Evitar > 24 °C.',
            parametros={**services.PARAMETROS_POR_DEFECTO, 'temp_min': 8, 'temp_max': 24,
                        'rad_min': 120, 'rad_max': 650, 'tiempo_persistencia_min': 12},
            usuario=USUARIO_SEED)
        inv = Invernadero.objects.create(nombre='Invernadero 1 - Sector A', id_cultivo=lechuga)
        for tipo, _ in Mecanismo.TIPOS:
            Mecanismo.objects.create(id_invernadero=inv, tipo=tipo, estado=Mecanismo.ESTADO_INICIAL[tipo])
        log(f'Cultivo "{lechuga}", "{inv}" y {len(Mecanismo.TIPOS)} mecanismos creados.')

        fin = timezone.now().replace(second=0, microsecond=0)
        inicio = fin - datetime.timedelta(hours=horas)
        sim = engine.Simulador(semilla=2026)
        sim.automatico = True   # durante la carga inicial las alertas aplican la acción automáticamente
        minutos = sim.generar_historial(inicio, fin, _programa(inicio, fin))
        log(f'{minutos} minutos de historial sintético generados ({horas} h).')

    engine.SIMULADOR.reiniciar()
    log(f'Alertas: {Alerta.objects.filter(tipo=Alerta.ALERTA).count()} · '
        f'Transitorias: {Alerta.objects.filter(tipo=Alerta.TRANSITORIA).count()} · '
        f'Movimientos: {HistorialMecanismo.objects.count()} · Clima: {HistorialClima.objects.count()}')
    return True
