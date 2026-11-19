"""Suite de pruebas unitarias e integrales – Invernadero Nona Olimpia."""
import datetime
import io
import json
import math
import random

from django.test import Client, TestCase
from django.utils import timezone
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from App import services
from App.engine import Contexto, FACTOR_MEDIA_SOMBRA, ModeloFisico, MotorEvaluacion, SIMULADOR, Simulador
from App.models import (
    Alerta, Cultivo, HistorialClima, HistorialMecanismo, HistorialParametro,
    HistorialRadiacion, HistorialTemperatura, Invernadero, Mecanismo, Parametro,
)
from App.seed import ejecutar_seed


def dt_local(year, month, day, hour, minute):
    return timezone.make_aware(datetime.datetime(year, month, day, hour, minute))


def crear_entorno_base(persistencia=5, modo_automatico=False):
    cultivo = services.guardar_cultivo(
        'Lechuga Salanova',
        'Cultivo de prueba',
        parametros={
            **services.PARAMETROS_POR_DEFECTO,
            'temp_min': 10.0,
            'temp_max': 25.0,
            'rad_min': 150.0,
            'rad_max': 600.0,
            'tiempo_persistencia_min': persistencia,
            'histeresis_temp': 1.0,
            'histeresis_rad': 40.0,
            'hora_inicio_diurna': datetime.time(9, 0),
            'hora_fin_diurna': datetime.time(16, 30),
        },
    )
    inv = Invernadero.objects.create(
        nombre='Invernadero 1 - Sector A',
        id_cultivo=cultivo,
        modo_automatico=modo_automatico,
    )
    mecs = {
        tipo: Mecanismo.objects.create(
            id_invernadero=inv,
            tipo=tipo,
            estado=Mecanismo.ESTADO_INICIAL[tipo],
        )
        for tipo, _ in Mecanismo.TIPOS
    }
    return cultivo, inv, mecs


# ===========================================================================
# a) Pruebas del Motor de Evaluación y Simulador (automático vs supervisado)
# ===========================================================================
class MotorEvaluacionTests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=5)
        self.ctx = Contexto(self.inv, self.cultivo.parametros, self.mecs)
        self.motor = MotorEvaluacion()

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_fluctuacion_corta_filtrada_como_transitoria_sin_alerta_ni_movimiento(self):
        inicio = dt_local(2026, 11, 19, 11, 0)
        # 4 minutos con radiación alta (menor a persistencia=5) y luego vuelve al rango
        for i in range(4):
            creadas, codigo = self.motor.evaluar(self.ctx, inicio + datetime.timedelta(minutes=i), 20.0, 720.0)
            self.assertEqual(creadas, [])
            self.assertIsNone(codigo)

        creadas, _ = self.motor.evaluar(self.ctx, inicio + datetime.timedelta(minutes=4), 20.0, 350.0)
        self.assertEqual(creadas, [])
        self.assertEqual(Alerta.objects.filter(tipo=Alerta.ALERTA).count(), 0)
        trans = Alerta.objects.filter(tipo=Alerta.TRANSITORIA)
        self.assertEqual(trans.count(), 1)
        self.assertEqual(trans.first().duracion_min, 4)
        self.assertEqual(trans.first().variable, Alerta.RADIACION)
        self.assertEqual(HistorialMecanismo.objects.count(), 0)

    def test_desvio_sostenido_genera_alerta_y_recomendaciones_exactas(self):
        # 1) Radiación alta -> SOMBREAR
        t0 = dt_local(2026, 11, 19, 10, 0)
        for i in range(4):
            creadas, _ = self.motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 20.0, 700.0)
            self.assertEqual(creadas, [])
        creadas, codigo = self.motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=4), 20.0, 710.0)
        self.assertEqual(len(creadas), 1)
        self.assertEqual(codigo, 'SOMBREAR')
        self.assertEqual(creadas[0].recomendacion, 'SOMBREAR')

        # 2) Se suma temperatura alta -> SOMBREAR_Y_VENTILAR
        t1 = dt_local(2026, 11, 19, 10, 10)
        for i in range(4):
            self.motor.evaluar(self.ctx, t1 + datetime.timedelta(minutes=i), 27.5, 710.0)
        creadas, codigo = self.motor.evaluar(self.ctx, t1 + datetime.timedelta(minutes=4), 28.0, 710.0)
        self.assertEqual(len(creadas), 1)
        self.assertEqual(codigo, 'SOMBREAR_Y_VENTILAR')

        # 3) Motor limpio: temperatura alta sola -> VENTILAR
        motor2 = MotorEvaluacion()
        for i in range(5):
            creadas, codigo = motor2.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 27.0, 300.0)
        self.assertEqual(codigo, 'VENTILAR')

        # 4) Motor limpio: radiación baja -> ABRIR_MEDIA_SOMBRA
        motor3 = MotorEvaluacion()
        for i in range(5):
            creadas, codigo = motor3.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 20.0, 80.0)
        self.assertEqual(codigo, 'ABRIR_MEDIA_SOMBRA')

        # 5) Motor limpio: temperatura baja -> CERRAR_VENTILACION
        motor4 = MotorEvaluacion()
        for i in range(5):
            creadas, codigo = motor4.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 7.5, 300.0)
        self.assertEqual(codigo, 'CERRAR_VENTILACION')

    def test_histeresis_retorno_mantiene_alerta_hasta_cruzar_banda(self):
        t0 = dt_local(2026, 11, 19, 12, 0)
        for i in range(5):
            self.motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 27.0, 300.0)

        alerta = Alerta.objects.get(tipo=Alerta.ALERTA, variable=Alerta.TEMPERATURA)
        self.assertFalse(alerta.resuelta)

        # temp_max=25.0, histeresis_temp=1.0 -> umbral retorno <= 24.0
        # 24.5 °C está dentro del rango [10, 25], pero dentro de la banda de histéresis (> 24.0)
        self.motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=5), 24.5, 300.0)
        alerta.refresh_from_db()
        self.assertFalse(alerta.resuelta)
        self.assertIsNotNone(self.motor.seg[Alerta.TEMPERATURA].alerta_id)

        # 23.8 °C cruza la histéresis -> debe resolver la alerta
        t_cierre = t0 + datetime.timedelta(minutes=6)
        self.motor.evaluar(self.ctx, t_cierre, 23.8, 300.0)
        alerta.refresh_from_db()
        self.assertTrue(alerta.resuelta)
        self.assertEqual(alerta.fecha_resolucion, t_cierre)
        self.assertIsNone(self.motor.seg[Alerta.TEMPERATURA].alerta_id)

    def test_franja_diurna_para_radiacion(self):
        # 08:30 está fuera de 09:00–16:30: radiación 0 o 800 W/m² no acumula ni alerta
        t_temprano = dt_local(2026, 11, 19, 8, 30)
        for i in range(6):
            creadas, _ = self.motor.evaluar(self.ctx, t_temprano + datetime.timedelta(minutes=i), 19.0, 20.0)
            self.assertEqual(creadas, [])
        self.assertEqual(Alerta.objects.count(), 0)

        # Dentro de la franja (16:26..16:30) dispara alerta de radiación, y a las 16:31 se cierra automáticamente
        t_tarde = dt_local(2026, 11, 19, 16, 26)
        for i in range(5):
            self.motor.evaluar(self.ctx, t_tarde + datetime.timedelta(minutes=i), 19.0, 720.0)
        alerta = Alerta.objects.get(tipo=Alerta.ALERTA, variable=Alerta.RADIACION)
        self.assertFalse(alerta.resuelta)

        self.motor.evaluar(self.ctx, dt_local(2026, 11, 19, 16, 31), 19.0, 720.0)
        alerta.refresh_from_db()
        self.assertTrue(alerta.resuelta)

    def test_modo_automatico_acciona_mecanismo_y_registra_historial(self):
        sim = Simulador(semilla=42)
        sim.sim_time = dt_local(2026, 11, 19, 12, 0)
        sim.temp_prev = 28.0
        sim.listo = True
        sim.automatico = True

        sim.activar_escenario('calor_60')
        sim.avanzar(5)

        self.assertGreaterEqual(Alerta.objects.filter(tipo=Alerta.ALERTA, variable=Alerta.TEMPERATURA).count(), 1)
        vent = Mecanismo.objects.get(id_invernadero=self.inv, tipo=Mecanismo.VENTILACION)
        self.assertEqual(vent.estado, 'ABIERTA')
        movs = HistorialMecanismo.objects.filter(id_mecanismo=vent, origen=HistorialMecanismo.AUTOMATICO)
        self.assertEqual(movs.count(), 1)
        self.assertEqual(movs.first().a_que_estado_paso, 'ABIERTA')

    def test_modo_supervisado_no_mueve_mecanismos(self):
        sim = Simulador(semilla=42)
        sim.sim_time = dt_local(2026, 11, 19, 12, 0)
        sim.temp_prev = 28.0
        sim.listo = True
        sim.automatico = False

        sim.activar_escenario('calor_60')
        sim.avanzar(5)

        self.assertGreaterEqual(Alerta.objects.filter(tipo=Alerta.ALERTA, variable=Alerta.TEMPERATURA).count(), 1)
        vent = Mecanismo.objects.get(id_invernadero=self.inv, tipo=Mecanismo.VENTILACION)
        self.assertEqual(vent.estado, 'CERRADA')
        self.assertEqual(HistorialMecanismo.objects.count(), 0)


# ===========================================================================
# b y c) Persistencia de modo_automatico y reinicio de evaluación
# ===========================================================================
class SimuladorPersistenciaYEstadoTests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo1, self.inv, self.mecs = crear_entorno_base(persistencia=5, modo_automatico=False)
        self.cultivo2 = services.guardar_cultivo(
            'Rúcula',
            'Segundo cultivo',
            parametros={**services.PARAMETROS_POR_DEFECTO, 'temp_min': 8.0, 'temp_max': 24.0},
        )
        self.client = Client(HTTP_HOST='localhost')

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_persistencia_modo_automatico_tras_reiniciar_simulador(self):
        SIMULADOR.asegurar_inicializado()
        self.assertFalse(SIMULADOR.automatico)

        SIMULADOR.set_automatico(True)
        self.inv.refresh_from_db()
        self.assertTrue(self.inv.modo_automatico)

        # Reiniciar singleton y también probar una instancia nueva
        SIMULADOR.reiniciar()
        self.assertTrue(SIMULADOR.asegurar_inicializado())
        self.assertTrue(SIMULADOR.automatico)

        nuevo_sim = Simulador(semilla=1)
        self.assertTrue(nuevo_sim.asegurar_inicializado())
        self.assertTrue(nuevo_sim.automatico)

    def test_reinicio_estado_motor_al_cambiar_cultivo_y_parametros(self):
        SIMULADOR.asegurar_inicializado()
        SIMULADOR.sim_time = dt_local(2026, 11, 19, 13, 0)
        ctx = SIMULADOR.contexto()

        # Disparar alerta activa de temperatura y dejar episodio pendiente en radiación
        for i in range(5):
            SIMULADOR.motor.evaluar(ctx, SIMULADOR.sim_time + datetime.timedelta(minutes=i), 27.5, 300.0)
        SIMULADOR.motor.evaluar(ctx, SIMULADOR.sim_time + datetime.timedelta(minutes=6), 27.5, 700.0)

        self.assertIsNotNone(SIMULADOR.motor.seg[Alerta.TEMPERATURA].alerta_id)
        self.assertEqual(SIMULADOR.motor.seg[Alerta.RADIACION].minutos_fuera, 1)

        # Actualizar parámetros del cultivo activo -> reinicia episodio pendiente sin cerrar alerta activa
        r_par = self.client.post(
            '/api/parametros/',
            data=json.dumps({'temp_max': 26.0, 'motivo': 'Prueba reinicio episodio'}),
            content_type='application/json',
        )
        self.assertEqual(r_par.status_code, 200)
        self.assertEqual(SIMULADOR.motor.seg[Alerta.RADIACION].minutos_fuera, 0)
        self.assertIsNone(SIMULADOR.motor.seg[Alerta.RADIACION].fuera_desde)
        self.assertIsNotNone(SIMULADOR.motor.seg[Alerta.TEMPERATURA].alerta_id)

        # Cambiar de cultivo activo vía API -> cierra alertas activas y reinicia todo
        r_asig = self.client.post(f'/api/cultivos/{self.cultivo2.id}/asignar/', data='{}', content_type='application/json')
        self.assertEqual(r_asig.status_code, 200)
        self.assertIsNone(SIMULADOR.motor.seg[Alerta.TEMPERATURA].alerta_id)
        self.assertEqual(Alerta.objects.filter(id_invernadero=self.inv, resuelta=False).count(), 0)


# ===========================================================================
# d) Pruebas de Services (validaciones, auditoría, mecanismos y eventos)
# ===========================================================================
class ServicesTests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=10)

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_riego_eliminado_de_mecanismos(self):
        tipos = dict(Mecanismo.TIPOS)
        self.assertEqual(set(tipos.keys()), {Mecanismo.MEDIA_SOMBRA, Mecanismo.VENTILACION})
        self.assertFalse(hasattr(Mecanismo, 'RIEGO'))

    def test_validacion_parametros_e_historial(self):
        param = self.cultivo.parametros
        inicial = param.historial.count()

        with self.assertRaises(services.ErrorValidacion):
            services.actualizar_parametros(param, {'temp_min': 26.0, 'temp_max': 20.0})
        with self.assertRaises(services.ErrorValidacion):
            services.actualizar_parametros(param, {'tiempo_persistencia_min': 0})
        with self.assertRaises(services.ErrorValidacion):
            services.actualizar_parametros(param, {'hora_inicio_diurna': '17:00', 'hora_fin_diurna': '09:00'})
        with self.assertRaises(services.ErrorValidacion):
            services.actualizar_parametros(param, {'histeresis_temp': 10.0})

        # Sin cambios no inserta snapshot
        _, cambios = services.actualizar_parametros(param, {'temp_min': 10.0})
        self.assertEqual(cambios, [])
        self.assertEqual(param.historial.count(), inicial)

        # Con cambios inserta snapshot en HISTORIAL_PARAMETROS
        _, cambios = services.actualizar_parametros(param, {'temp_max': 24.5}, motivo='Prueba auditoría')
        self.assertEqual(cambios, ['temp_max'])
        self.assertEqual(param.historial.count(), inicial + 1)
        self.assertEqual(param.historial.first().motivo, 'Prueba auditoría')

    def test_cambiar_estado_mecanismo_registra_historial(self):
        ms = self.mecs[Mecanismo.MEDIA_SOMBRA]
        fecha = dt_local(2026, 11, 19, 12, 0)
        with self.assertRaises(services.ErrorValidacion):
            services.cambiar_estado_mecanismo(ms, 'ENCENDIDO', HistorialMecanismo.MANUAL, fecha)

        _, hist = services.cambiar_estado_mecanismo(ms, 'CERRADA', HistorialMecanismo.MANUAL, fecha, 'Ana Savio')
        self.assertIsNotNone(hist)
        ms.refresh_from_db()
        self.assertEqual(ms.estado, 'CERRADA')
        self.assertEqual(HistorialMecanismo.objects.filter(id_mecanismo=ms).count(), 1)

    def test_eventos_filtra_alerta_sin_truncar_por_muchas_transitorias(self):
        base = dt_local(2026, 11, 19, 10, 0)
        # 5 alertas reales más antiguas (una de ellas resuelta)
        for i in range(5):
            a = services.crear_alerta(
                self.inv, Alerta.TEMPERATURA, 'ALTA', 26.5, 27.0, 25.0, 10,
                base + datetime.timedelta(minutes=i), 'VENTILAR',
            )
            if i == 0:
                services.resolver_alerta(a.id, base + datetime.timedelta(minutes=20), 23.0)

        # 25 variaciones transitorias posteriores (más recientes)
        for i in range(25):
            services.registrar_transitoria(
                self.inv, Alerta.RADIACION, 'BAJA', 110.0, 150.0, 4,
                base + datetime.timedelta(minutes=30 + i), 10,
            )

        # Con limite=10, antes el slice se llenaba con las transitorias más recientes y devolvía 0 alertas
        solo_alertas = services.eventos(self.inv, tipo='ALERTA', limite=10)
        self.assertEqual(len(solo_alertas), 5)
        self.assertTrue(all(e['tipo'] == 'ALERTA' for e in solo_alertas))

        solo_norm = services.eventos(self.inv, tipo='NORMALIZACION', limite=10)
        self.assertEqual(len(solo_norm), 1)
        self.assertEqual(solo_norm[0]['tipo'], 'NORMALIZACION')


# ===========================================================================
# e) Pruebas de Views, API JSON/XLSX, Clima intacto y Seed
# ===========================================================================
class ViewsApiTests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        ejecutar_seed(horas=2, reiniciar=True, log=lambda m: None)
        self.client = Client(HTTP_HOST='localhost')

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_seed_y_monitor_conserva_clima_y_sin_riego(self):
        self.assertEqual(Mecanismo.objects.count(), 2)
        self.assertEqual(Mecanismo.objects.filter(tipo='RIEGO').count(), 0)
        self.assertGreater(HistorialClima.objects.count(), 0)

        resp = self.client.get('/api/monitor/')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertIn('automatico', data['sim'])
        self.assertIn('clima', data)
        self.assertIsNotNone(data['clima']['actual'])
        tipos_mecs = [m['tipo'] for m in data['mecanismos']]
        self.assertEqual(set(tipos_mecs), {Mecanismo.MEDIA_SOMBRA, Mecanismo.VENTILACION})

    def test_api_series_con_horas_no_numericas_usa_defecto(self):
        resp = self.client.get('/api/series/?horas=abc')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['horas'], 6)

    def test_api_parametros_cultivo_inexistente_devuelve_400(self):
        r1 = self.client.post(
            '/api/parametros/',
            data=json.dumps({'cultivo_id': 999999, 'temp_max': 24}),
            content_type='application/json',
        )
        self.assertEqual(r1.status_code, 400)
        self.assertFalse(r1.json()['ok'])

        r2 = self.client.post(
            '/api/parametros/',
            data=json.dumps({'cultivo_id': 'abc', 'temp_max': 24}),
            content_type='application/json',
        )
        self.assertEqual(r2.status_code, 400)
        self.assertFalse(r2.json()['ok'])

    def test_api_control_automatico_persiste_en_sqlite(self):
        r = self.client.post(
            '/api/sim/control/',
            data=json.dumps({'accion': 'automatico', 'valor': True}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        inv = services.obtener_invernadero_activo()
        self.assertTrue(inv.modo_automatico)

        SIMULADOR.reiniciar()
        mon = self.client.get('/api/monitor/').json()
        self.assertTrue(mon['sim']['automatico'])

    def test_api_eventos_xlsx_descarga_valida(self):
        inv = services.obtener_invernadero_activo()
        services.crear_alerta(
            inv, Alerta.TEMPERATURA, 'ALTA', 27.0, 27.5, 25.0, 10,
            dt_local(2026, 11, 19, 14, 0), 'VENTILAR',
        )
        resp = self.client.get('/api/eventos.xlsx')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        wb = load_workbook(io.BytesIO(resp.content))
        self.assertIn('Eventos', wb.sheetnames)
        ws = wb['Eventos']
        headers = [cell.value for cell in ws[1]]
        self.assertEqual(headers, ['Fecha', 'Tipo', 'Variable', 'Valor', 'Duración/Origen', 'Descripción', 'Usuario'])
        self.assertGreater(ws.max_row, 1)
        for i in range(1, 8):
            self.assertGreaterEqual(ws.column_dimensions[get_column_letter(i)].width, 12)

    def test_index_y_activos_de_marca(self):
        from django.contrib.staticfiles import finders

        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        for nombre in ('logo-nona.png', 'isotipo.jpg', 'banner-nona.jpg', 'invernadero-real.jpg'):
            self.assertIn(nombre, html)
            self.assertIsNotNone(finders.find(f'App/img/{nombre}'))


# ===========================================================================
# f) Coherencia entre mecanismos, recomendaciones y mitigación de escenarios
# ===========================================================================
class CoherenciaEscenariosYMecanismosTests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=5)

    def tearDown(self):
        SIMULADOR.reiniciar()

    def _sim_en(self, hora, minuto, temp_prev=20.0):
        sim = Simulador(semilla=123)
        sim.sim_time = dt_local(2026, 11, 19, hora, minuto)
        sim.temp_prev = temp_prev
        sim.listo = True
        return sim

    def test_nublado_45_con_sombra_abierta_no_sugiere_abrir_ni_mitiga(self):
        sim = self._sim_en(12, 0)
        sim.activar_escenario('nublado_45')
        sim.avanzar(5)

        est = sim.estado()
        self.assertEqual(len(est['alertas_activas']), 1)
        self.assertEqual(est['alertas_activas'][0]['variable'], Alerta.RADIACION)
        self.assertEqual(est['decision']['acciones'], [])
        self.assertEqual(est['decision']['codigo'], 'MANTENER')
        self.assertTrue(est['decision']['sin_accion_mecanica'])

        res = sim.aplicar_accion_sugerida()
        self.assertEqual(res['movimientos'], 0)
        self.assertTrue(res['sin_accion_mecanica'])
        self.assertFalse(sim.escenario['mitigado'])
        self.assertEqual(HistorialMecanismo.objects.count(), 0)

    def test_nublado_45_con_sombra_cerrada_sugiere_abrir_y_limpia_pendiente(self):
        ms = self.mecs[Mecanismo.MEDIA_SOMBRA]
        ms.estado = 'CERRADA'
        ms.save(update_fields=['estado'])

        sim = self._sim_en(12, 0)
        sim.activar_escenario('nublado_45')
        sim.avanzar(5)

        est = sim.estado()
        self.assertEqual(est['decision']['codigo'], 'ABRIR_MEDIA_SOMBRA')
        self.assertFalse(est['decision']['sin_accion_mecanica'])
        self.assertEqual(len(est['decision']['acciones']), 1)

        res = sim.aplicar_accion_sugerida()
        self.assertEqual(res['movimientos'], 1)
        self.assertFalse(res['sin_accion_mecanica'])
        self.assertFalse(sim.escenario['mitigado'])

        ms.refresh_from_db()
        self.assertEqual(ms.estado, 'ABIERTA')
        est_post = sim.estado()
        self.assertEqual(est_post['decision']['acciones'], [])
        self.assertTrue(est_post['decision']['sin_accion_mecanica'])

        # Misma verificación accionando manualmente cuando estaba CERRADA
        SIMULADOR.reiniciar()
        ms.estado = 'CERRADA'
        ms.save(update_fields=['estado'])
        sim2 = self._sim_en(12, 0)
        sim2.activar_escenario('nublado_45')
        sim2.avanzar(5)
        _, hist = sim2.cambiar_mecanismo_manual(ms, 'ABIERTA')
        self.assertIsNotNone(hist)
        self.assertFalse(sim2.escenario['mitigado'])
        est2 = sim2.estado()
        self.assertEqual(est2['decision']['acciones'], [])
        self.assertTrue(est2['decision']['sin_accion_mecanica'])

    def test_control_manual_estabiliza_calor_60_sol_30_y_sol_calor_45(self):
        # 1) calor_60 -> abrir VENTILACION manualmente mitiga y normaliza la temperatura
        sim_calor = self._sim_en(13, 0, temp_prev=26.5)
        sim_calor.activar_escenario('calor_60')
        sim_calor.avanzar(5)
        self.assertEqual(sim_calor.estado()['decision']['codigo'], 'VENTILAR')
        self.assertFalse(sim_calor.escenario['mitigado'])

        vent = Mecanismo.objects.get(id_invernadero=self.inv, tipo=Mecanismo.VENTILACION)
        sim_calor.cambiar_mecanismo_manual(vent, 'ABIERTA')
        self.assertTrue(sim_calor.escenario['mitigado'])
        sim_calor.avanzar(20)
        self.assertEqual(Alerta.objects.filter(id_invernadero=self.inv, resuelta=False).count(), 0)

        # Limpiar estado para el siguiente subcaso
        vent.estado = 'CERRADA'
        vent.save(update_fields=['estado'])

        # 2) sol_30 -> cerrar MEDIA_SOMBRA manualmente mitiga y normaliza la radiación
        sim_sol = self._sim_en(12, 30)
        sim_sol.activar_escenario('sol_30')
        sim_sol.avanzar(5)
        self.assertEqual(sim_sol.estado()['decision']['codigo'], 'SOMBREAR')
        self.assertFalse(sim_sol.escenario['mitigado'])

        ms = Mecanismo.objects.get(id_invernadero=self.inv, tipo=Mecanismo.MEDIA_SOMBRA)
        sim_sol.cambiar_mecanismo_manual(ms, 'CERRADA')
        self.assertTrue(sim_sol.escenario['mitigado'])
        sim_sol.avanzar(5)
        self.assertEqual(Alerta.objects.filter(id_invernadero=self.inv, resuelta=False).count(), 0)

        ms.estado = 'ABIERTA'
        ms.save(update_fields=['estado'])

        # 3) sol_calor_45 -> requiere ambos mecanismos en posición para considerarse mitigado
        sim_ambos = self._sim_en(12, 30, temp_prev=26.5)
        sim_ambos.activar_escenario('sol_calor_45')
        sim_ambos.avanzar(5)
        sim_ambos.cambiar_mecanismo_manual(ms, 'CERRADA')
        self.assertFalse(sim_ambos.escenario['mitigado'])
        sim_ambos.cambiar_mecanismo_manual(vent, 'ABIERTA')
        self.assertTrue(sim_ambos.escenario['mitigado'])


# ===========================================================================
# g) QA Exhaustivo: Escenarios completos en diurno (Supervisado vs Automático)
# ===========================================================================
class EscenariosCompletosQATests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=10)

    def tearDown(self):
        SIMULADOR.reiniciar()

    def _sim(self, hora=11, minuto=0, temp_prev=20.0, automatico=False):
        sim = Simulador(semilla=2026)
        sim.sim_time = dt_local(2026, 11, 19, hora, minuto)
        sim.temp_prev = temp_prev
        sim.listo = True
        sim.automatico = automatico
        return sim

    def test_nube_5_persistencia_10_vs_3_y_quitar_escenario(self):
        # 1) Con persistencia=10 min: nube_5 (5 min) se filtra como TRANSITORIA
        sim = self._sim(11, 0)
        sim.activar_escenario('nube_5')
        sim.avanzar(6)  # 5 min de nube + 1 min de recuperación
        self.assertEqual(Alerta.objects.filter(tipo=Alerta.ALERTA).count(), 0)
        trans = Alerta.objects.filter(tipo=Alerta.TRANSITORIA)
        self.assertEqual(trans.count(), 1)
        self.assertEqual(trans.first().duracion_min, 5)
        self.assertEqual(HistorialMecanismo.objects.count(), 0)

        # 2) Quitar escenario y escenario inválido
        sim.activar_escenario('sol_30')
        self.assertIsNotNone(sim.escenario)
        msg = sim.activar_escenario('quitar')
        self.assertIn('quitados', msg.lower())
        self.assertIsNone(sim.escenario)
        with self.assertRaises(services.ErrorValidacion):
            sim.activar_escenario('inexistente')

        # 3) Con persistencia=3 min: nube_5 sí emite ALERTA y normaliza al finalizar
        services.actualizar_parametros(self.cultivo.parametros, {'tiempo_persistencia_min': 3})
        sim2 = self._sim(11, 30)
        sim2.activar_escenario('nube_5')
        sim2.avanzar(3)
        alerta = Alerta.objects.get(tipo=Alerta.ALERTA, variable=Alerta.RADIACION)
        self.assertEqual(alerta.direccion, 'BAJA')
        self.assertFalse(alerta.resuelta)

        sim2.avanzar(4)  # termina la nube y vuelve la radiación diurna
        alerta.refresh_from_db()
        self.assertTrue(alerta.resuelta)

    def test_sol_30_supervisado_vs_automatico_normaliza_con_histeresis(self):
        services.actualizar_parametros(self.cultivo.parametros, {'tiempo_persistencia_min': 5})
        ms = self.mecs[Mecanismo.MEDIA_SOMBRA]

        # Supervisado: alerta + acción sugerida -> mitigado y normalización
        sim_sup = self._sim(11, 0, automatico=False)
        sim_sup.activar_escenario('sol_30')
        sim_sup.avanzar(5)
        self.assertEqual(sim_sup.estado()['decision']['codigo'], 'SOMBREAR')
        self.assertEqual(HistorialMecanismo.objects.count(), 0)

        res = sim_sup.aplicar_accion_sugerida()
        self.assertEqual(res['movimientos'], 1)
        self.assertTrue(sim_sup.escenario['mitigado'])
        ms.refresh_from_db()
        self.assertEqual(ms.estado, 'CERRADA')

        sim_sup.avanzar(5)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

        # Automático: emite alerta, mueve sola la media sombra y normaliza
        ms.estado = 'ABIERTA'
        ms.save(update_fields=['estado'])
        Alerta.objects.all().delete()
        HistorialMecanismo.objects.all().delete()

        sim_aut = self._sim(11, 0, automatico=True)
        sim_aut.activar_escenario('sol_30')
        sim_aut.avanzar(5)
        self.assertTrue(sim_aut.escenario['mitigado'])
        mov = HistorialMecanismo.objects.get(id_mecanismo=ms)
        self.assertEqual(mov.origen, HistorialMecanismo.AUTOMATICO)
        self.assertEqual(mov.a_que_estado_paso, 'CERRADA')

        sim_aut.avanzar(5)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

    def test_calor_60_supervisado_vs_automatico_normaliza_con_histeresis(self):
        services.actualizar_parametros(self.cultivo.parametros, {'tiempo_persistencia_min': 5})
        vent = self.mecs[Mecanismo.VENTILACION]

        # Supervisado
        sim_sup = self._sim(11, 0, temp_prev=26.0, automatico=False)
        sim_sup.activar_escenario('calor_60')
        sim_sup.avanzar(5)
        self.assertEqual(sim_sup.estado()['decision']['codigo'], 'VENTILAR')
        self.assertEqual(HistorialMecanismo.objects.count(), 0)

        res = sim_sup.aplicar_accion_sugerida()
        self.assertEqual(res['movimientos'], 1)
        self.assertTrue(sim_sup.escenario['mitigado'])
        sim_sup.avanzar(20)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

        # Automático
        vent.estado = 'CERRADA'
        vent.save(update_fields=['estado'])
        Alerta.objects.all().delete()
        HistorialMecanismo.objects.all().delete()

        sim_aut = self._sim(11, 0, temp_prev=26.0, automatico=True)
        sim_aut.activar_escenario('calor_60')
        sim_aut.avanzar(5)
        self.assertTrue(sim_aut.escenario['mitigado'])
        mov = HistorialMecanismo.objects.get(id_mecanismo=vent)
        self.assertEqual(mov.origen, HistorialMecanismo.AUTOMATICO)
        self.assertEqual(mov.a_que_estado_paso, 'ABIERTA')
        sim_aut.avanzar(20)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

    def test_sol_calor_45_supervisado_vs_automatico_normaliza_ambas(self):
        services.actualizar_parametros(self.cultivo.parametros, {'tiempo_persistencia_min': 5})
        ms = self.mecs[Mecanismo.MEDIA_SOMBRA]
        vent = self.mecs[Mecanismo.VENTILACION]

        # Supervisado
        sim_sup = self._sim(11, 0, temp_prev=26.0, automatico=False)
        sim_sup.activar_escenario('sol_calor_45')
        sim_sup.avanzar(5)
        self.assertEqual(sim_sup.estado()['decision']['codigo'], 'SOMBREAR_Y_VENTILAR')
        self.assertEqual(Alerta.objects.filter(tipo=Alerta.ALERTA, resuelta=False).count(), 2)

        res = sim_sup.aplicar_accion_sugerida()
        self.assertEqual(res['movimientos'], 2)
        self.assertTrue(sim_sup.escenario['mitigado'])
        sim_sup.avanzar(20)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

        # Automático
        ms.estado, vent.estado = 'ABIERTA', 'CERRADA'
        ms.save(update_fields=['estado'])
        vent.save(update_fields=['estado'])
        Alerta.objects.all().delete()
        HistorialMecanismo.objects.all().delete()

        sim_aut = self._sim(11, 0, temp_prev=26.0, automatico=True)
        sim_aut.activar_escenario('sol_calor_45')
        sim_aut.avanzar(5)
        self.assertTrue(sim_aut.escenario['mitigado'])
        self.assertEqual(HistorialMecanismo.objects.filter(origen=HistorialMecanismo.AUTOMATICO).count(), 2)
        sim_aut.avanzar(20)
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

    def test_nublado_45_modo_automatico_con_sombra_abierta_sin_movimientos_ni_mitigacion(self):
        services.actualizar_parametros(self.cultivo.parametros, {'tiempo_persistencia_min': 5})
        sim = self._sim(11, 0, automatico=True)
        sim.activar_escenario('nublado_45')
        sim.avanzar(5)

        est = sim.estado()
        self.assertEqual(len(est['alertas_activas']), 1)
        self.assertEqual(est['alertas_activas'][0]['variable'], Alerta.RADIACION)
        self.assertTrue(est['decision']['sin_accion_mecanica'])
        self.assertEqual(est['decision']['acciones'], [])
        self.assertFalse(sim.escenario['mitigado'])
        self.assertEqual(HistorialMecanismo.objects.count(), 0)


# ===========================================================================
# h) QA: Matriz de 9 recomendaciones, histéresis completa, diurna y cambios bruscos
# ===========================================================================
class MatrizRecomendacionesYHisteresisQATests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=5)
        self.ctx = Contexto(self.inv, self.cultivo.parametros, self.mecs)

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_matriz_9_combinaciones_recomendacion_y_filtrado_acciones_pendientes(self):
        esperado = {
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
        estados_posibles = [
            {Mecanismo.MEDIA_SOMBRA: ms, Mecanismo.VENTILACION: ve}
            for ms in ('ABIERTA', 'CERRADA')
            for ve in ('ABIERTA', 'CERRADA')
        ]
        for (dr, dt), cod in esperado.items():
            self.assertEqual(services.calcular_recomendacion(dr, dt), cod)
            for est in estados_posibles:
                pend = services.acciones_pendientes(cod, est)
                for tipo, obj in pend:
                    self.assertNotEqual(est[tipo], obj)
                cod_filtrado = services.codigo_para_acciones(pend)
                self.assertIn(cod_filtrado, services.ACCIONES_RECOMENDACION)
                self.assertEqual(services.ACCIONES_RECOMENDACION[cod_filtrado], pend)

    def test_histeresis_completa_ambos_sentidos_temp_y_rad(self):
        # 1) TEMPERATURA ALTA (max=25.0, hist=1.0 -> retorno <= 24.0)
        m_ta = MotorEvaluacion()
        t0 = dt_local(2026, 11, 19, 12, 0)
        for i in range(5):
            m_ta.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 27.0, 300.0)
        a_ta = Alerta.objects.get(variable=Alerta.TEMPERATURA, direccion='ALTA')
        m_ta.evaluar(self.ctx, t0 + datetime.timedelta(minutes=5), 24.1, 300.0)
        a_ta.refresh_from_db()
        self.assertFalse(a_ta.resuelta)
        m_ta.evaluar(self.ctx, t0 + datetime.timedelta(minutes=6), 24.0, 300.0)
        a_ta.refresh_from_db()
        self.assertTrue(a_ta.resuelta)

        # 2) TEMPERATURA BAJA (min=10.0, hist=1.0 -> retorno >= 11.0)
        m_tb = MotorEvaluacion()
        for i in range(5):
            m_tb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 8.0, 300.0)
        a_tb = Alerta.objects.get(variable=Alerta.TEMPERATURA, direccion='BAJA')
        m_tb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=5), 10.9, 300.0)
        a_tb.refresh_from_db()
        self.assertFalse(a_tb.resuelta)
        m_tb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=6), 11.0, 300.0)
        a_tb.refresh_from_db()
        self.assertTrue(a_tb.resuelta)

        # 3) RADIACION ALTA (max=600.0, hist=40.0 -> retorno <= 560.0)
        m_ra = MotorEvaluacion()
        for i in range(5):
            m_ra.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 20.0, 700.0)
        a_ra = Alerta.objects.get(variable=Alerta.RADIACION, direccion='ALTA')
        m_ra.evaluar(self.ctx, t0 + datetime.timedelta(minutes=5), 20.0, 561.0)
        a_ra.refresh_from_db()
        self.assertFalse(a_ra.resuelta)
        m_ra.evaluar(self.ctx, t0 + datetime.timedelta(minutes=6), 20.0, 560.0)
        a_ra.refresh_from_db()
        self.assertTrue(a_ra.resuelta)

        # 4) RADIACION BAJA (min=150.0, hist=40.0 -> retorno >= 190.0) + preservación de pico 0.0
        m_rb = MotorEvaluacion()
        for i in range(5):
            m_rb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 20.0, 0.0)
        self.assertEqual(m_rb.seg[Alerta.RADIACION].alerta_pico, 0.0)
        m_rb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=5), 20.0, 50.0)
        self.assertEqual(m_rb.seg[Alerta.RADIACION].alerta_pico, 0.0)

        a_rb = Alerta.objects.get(variable=Alerta.RADIACION, direccion='BAJA')
        m_rb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=6), 20.0, 189.0)
        a_rb.refresh_from_db()
        self.assertFalse(a_rb.resuelta)
        m_rb.evaluar(self.ctx, t0 + datetime.timedelta(minutes=7), 20.0, 190.0)
        a_rb.refresh_from_db()
        self.assertTrue(a_rb.resuelta)

    def test_franja_diurna_no_evaluada_cierre_y_temp_baja_nocturna(self):
        vent = self.mecs[Mecanismo.VENTILACION]
        vent.estado = 'ABIERTA'
        vent.save(update_fields=['estado'])

        sim = Simulador(semilla=9)
        sim.sim_time = dt_local(2026, 11, 19, 2, 55)
        sim.ultima = (8.0, 0.0, sim.sim_time)
        sim.listo = True

        # A las 03:00 la radiación es NO_EVALUADA, pero temperatura baja sí se evalúa 24h
        t_noche = dt_local(2026, 11, 19, 3, 0)
        for i in range(5):
            sim.motor.evaluar(self.ctx, t_noche + datetime.timedelta(minutes=i), 8.0, 0.0)
        sim.sim_time = t_noche + datetime.timedelta(minutes=4)
        sim.ultima = (8.0, 0.0, sim.sim_time)

        est = sim.estado()
        self.assertEqual(est['variables']['RADIACION']['estado'], 'NO_EVALUADA')
        self.assertEqual(est['variables']['TEMPERATURA']['estado'], 'ALERTA')
        self.assertEqual(est['decision']['codigo'], 'CERRAR_VENTILACION')
        self.assertEqual(len(est['decision']['acciones']), 1)

        # Cierre automático de alerta de radiación al cruzar las 16:30 -> 16:31
        motor_tarde = MotorEvaluacion()
        t_tarde = dt_local(2026, 11, 19, 16, 26)
        for i in range(5):
            motor_tarde.evaluar(self.ctx, t_tarde + datetime.timedelta(minutes=i), 20.0, 720.0)
        alerta_rad = Alerta.objects.get(variable=Alerta.RADIACION, resuelta=False)
        motor_tarde.evaluar(self.ctx, dt_local(2026, 11, 19, 16, 31), 20.0, 720.0)
        alerta_rad.refresh_from_db()
        self.assertTrue(alerta_rad.resuelta)

    def test_cambio_brusco_direccion_en_persistencia_y_en_alerta_activa(self):
        motor = MotorEvaluacion()
        t0 = dt_local(2026, 11, 19, 12, 0)

        # 1) Durante PERSISTENCIA: 3 min ALTA -> cambio brusco a BAJA
        for i in range(3):
            motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 27.0, 300.0)
        motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=3), 8.0, 300.0)

        trans = Alerta.objects.get(tipo=Alerta.TRANSITORIA)
        self.assertEqual(trans.direccion, 'ALTA')
        self.assertEqual(trans.duracion_min, 3)
        self.assertEqual(motor.seg[Alerta.TEMPERATURA].direccion, 'BAJA')
        self.assertEqual(motor.seg[Alerta.TEMPERATURA].minutos_fuera, 1)

        # 2) Completar persistencia BAJA -> emite ALERTA(BAJA), luego cambio brusco a ALTA
        for i in range(4, 8):
            motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 8.0, 300.0)
        alerta_baja = Alerta.objects.get(tipo=Alerta.ALERTA, direccion='BAJA')
        self.assertFalse(alerta_baja.resuelta)

        # Cambio brusco a ALTA: resuelve ALERTA(BAJA) e inicia nuevo conteo ALTA
        motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=8), 28.0, 300.0)
        alerta_baja.refresh_from_db()
        self.assertTrue(alerta_baja.resuelta)
        self.assertEqual(motor.seg[Alerta.TEMPERATURA].direccion, 'ALTA')
        self.assertEqual(motor.seg[Alerta.TEMPERATURA].minutos_fuera, 1)

        for i in range(9, 13):
            motor.evaluar(self.ctx, t0 + datetime.timedelta(minutes=i), 28.0, 300.0)
        self.assertTrue(Alerta.objects.filter(tipo=Alerta.ALERTA, direccion='ALTA', resuelta=False).exists())


# ===========================================================================
# i) QA: Controles, física, clima, corrida 24h y recuperación tras reinicio
# ===========================================================================
class ControlesFisicaClimaYResilienciaQATests(TestCase):
    def setUp(self):
        SIMULADOR.reiniciar()
        self.cultivo, self.inv, self.mecs = crear_entorno_base(persistencia=5)
        self.client = Client(HTTP_HOST='localhost')

    def tearDown(self):
        SIMULADOR.reiniciar()

    def test_controles_simulador_y_api_sim_control_completo(self):
        SIMULADOR.asegurar_inicializado()
        SIMULADOR.pausar(True)

        # Pausar y reanudar
        r = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'pausar'}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(SIMULADOR.pausado)
        r = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'reanudar'}), content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(SIMULADOR.pausado)

        # Velocidades válidas e inválidas
        for vel in (1, 2, 5, 15):
            r = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'velocidad', 'valor': vel}), content_type='application/json')
            self.assertEqual(r.status_code, 200)
            self.assertEqual(SIMULADOR.velocidad, vel)
        for bad_vel in (99, 'rapido', None):
            r = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'velocidad', 'valor': bad_vel}), content_type='application/json')
            self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'invalida'}), content_type='application/json')
        self.assertEqual(r.status_code, 400)

        # hora09 completa hueco y cierra alertas activas
        SIMULADOR.pausar(True)
        SIMULADOR.sim_time = dt_local(2026, 11, 19, 8, 30)
        SIMULADOR.temp_prev = 20.0
        services.crear_alerta(self.inv, Alerta.TEMPERATURA, 'ALTA', 27.0, 27.5, 25.0, 5, SIMULADOR.sim_time, 'VENTILAR')
        SIMULADOR.motor.reconstruir(self.inv)
        r_h9 = self.client.post('/api/sim/control/', data=json.dumps({'accion': 'hora09'}), content_type='application/json')
        self.assertEqual(r_h9.status_code, 200)
        self.assertEqual(timezone.localtime(SIMULADOR.sim_time).strftime('%H:%M'), '09:00')
        self.assertEqual(Alerta.objects.filter(resuelta=False).count(), 0)

        # aplicar_accion_sugerida vía endpoint sobre sol_30 y calor_60
        SIMULADOR.sim_time = dt_local(2026, 11, 19, 11, 0)
        SIMULADOR.temp_prev = 20.0
        self.client.post('/api/sim/escenario/', data=json.dumps({'clave': 'sol_30'}), content_type='application/json')
        SIMULADOR.avanzar(5)
        r_acc_sol = self.client.post('/api/sim/accion-sugerida/', data='{}', content_type='application/json')
        self.assertEqual(r_acc_sol.status_code, 200)
        self.assertEqual(r_acc_sol.json()['codigo'], 'SOMBREAR')
        self.assertEqual(r_acc_sol.json()['movimientos'], 1)
        SIMULADOR.avanzar(5)

        self.client.post('/api/sim/escenario/', data=json.dumps({'clave': 'calor_60'}), content_type='application/json')
        SIMULADOR.avanzar(12)
        r_acc_calor = self.client.post('/api/sim/accion-sugerida/', data='{}', content_type='application/json')
        self.assertEqual(r_acc_calor.status_code, 200)
        self.assertEqual(r_acc_calor.json()['codigo'], 'VENTILAR')
        self.assertEqual(r_acc_calor.json()['movimientos'], 1)

    def test_efecto_fisico_modelo_y_rechazo_estado_invalido_api_mecanismo(self):
        fecha = dt_local(2026, 11, 19, 12, 0)
        ef = {'rad_mult': 1.0, 'rad_add': 0.0, 'temp_add': 0.0, 'clima': None}

        m1 = ModeloFisico(random.Random(777))
        t_cerrada, rad_abierta = m1.calcular(
            fecha, {Mecanismo.MEDIA_SOMBRA: 'ABIERTA', Mecanismo.VENTILACION: 'CERRADA'}, ef, None
        )
        m2 = ModeloFisico(random.Random(777))
        t_abierta, rad_cerrada = m2.calcular(
            fecha, {Mecanismo.MEDIA_SOMBRA: 'CERRADA', Mecanismo.VENTILACION: 'ABIERTA'}, ef, None
        )
        self.assertAlmostEqual(rad_cerrada, rad_abierta * FACTOR_MEDIA_SOMBRA, places=6)
        # Cuando temp_prev=None y radiación cambia, ajustamos solo VENTILACION para aislar -3.0 °C exactos
        m3 = ModeloFisico(random.Random(777))
        t_solo_vent, _ = m3.calcular(
            fecha, {Mecanismo.MEDIA_SOMBRA: 'ABIERTA', Mecanismo.VENTILACION: 'ABIERTA'}, ef, None
        )
        self.assertAlmostEqual(t_cerrada - t_solo_vent, 3.0, places=6)

        ms = self.mecs[Mecanismo.MEDIA_SOMBRA]
        for bad_state in ('ENCENDIDO', 123, None):
            r = self.client.post(f'/api/mecanismos/{ms.id}/', data=json.dumps({'estado': bad_state}), content_type='application/json')
            self.assertEqual(r.status_code, 400)
        r_missing = self.client.post('/api/mecanismos/999999/', data=json.dumps({'estado': 'CERRADA'}), content_type='application/json')
        self.assertEqual(r_missing.status_code, 400)

    def test_registro_clima_cada_30_min_y_pronosticos_cada_3h_sin_duplicados(self):
        inicio = dt_local(2026, 11, 19, 0, 0)
        fin = inicio + datetime.timedelta(hours=6)
        sim = Simulador(semilla=10)
        sim.generar_historial(inicio, fin)

        actuales = HistorialClima.objects.filter(id_invernadero=self.inv, es_pronostico=False).order_by('fecha')
        self.assertEqual(actuales.count(), 12)
        self.assertTrue(all(timezone.localtime(c.fecha).minute in (0, 30) for c in actuales))

        pronos = list(HistorialClima.objects.filter(id_invernadero=self.inv, es_pronostico=True).order_by('fecha'))
        fechas_prono = [p.fecha for p in pronos]
        self.assertEqual(len(fechas_prono), 7)  # 03:00 (+3,+6,+12,+24) + 06:00 (+3 reemplaza, +6,+12,+24)
        self.assertEqual(len(fechas_prono), len(set(fechas_prono)))

    def test_corrida_continua_24h_sin_nan_rad_negativa_ni_alertas_duplicadas(self):
        inicio = dt_local(2026, 11, 19, 0, 0)
        fin = inicio + datetime.timedelta(hours=24)
        programa = {
            dt_local(2026, 11, 19, 10, 0): [('escenario', 'nublado_45')],
            dt_local(2026, 11, 19, 11, 30): [('escenario', 'nube_5')],
            dt_local(2026, 11, 19, 12, 30): [('escenario', 'sol_30')],
            dt_local(2026, 11, 19, 14, 0): [('escenario', 'calor_60')],
            dt_local(2026, 11, 19, 15, 30): [('escenario', 'sol_calor_45')],
        }
        sim = Simulador(semilla=55)
        sim.automatico = True
        total = sim.generar_historial(inicio, fin, programa)
        self.assertEqual(total, 1440)

        temps = list(HistorialTemperatura.objects.filter(id_invernadero=self.inv).values_list('grados', flat=True))
        rads = list(HistorialRadiacion.objects.filter(id_invernadero=self.inv).values_list('valor', flat=True))
        self.assertEqual(len(temps), 1440)
        self.assertEqual(len(rads), 1440)
        self.assertTrue(all(math.isfinite(t) for t in temps))
        self.assertTrue(all(math.isfinite(r) and r >= 0.0 for r in rads))
        for var in (Alerta.TEMPERATURA, Alerta.RADIACION):
            self.assertLessEqual(
                Alerta.objects.filter(id_invernadero=self.inv, variable=var, tipo=Alerta.ALERTA, resuelta=False).count(),
                1,
            )

    def test_recuperacion_tras_reinicio_reconstruir_y_modo_automatico(self):
        self.inv.modo_automatico = True
        self.inv.save(update_fields=['modo_automatico'])

        t1 = dt_local(2026, 11, 19, 12, 0)
        t2 = dt_local(2026, 11, 19, 12, 5)
        services.registrar_lectura(self.inv, t2, 27.5, 400.0)
        a_vieja = services.crear_alerta(self.inv, Alerta.TEMPERATURA, 'ALTA', 26.5, 27.0, 25.0, 5, t1, 'VENTILAR')
        a_nueva = services.crear_alerta(self.inv, Alerta.TEMPERATURA, 'ALTA', 27.5, 28.0, 25.0, 5, t2, 'VENTILAR')

        sim = Simulador(semilla=1)
        self.assertTrue(sim.asegurar_inicializado())
        self.assertTrue(sim.automatico)
        self.assertEqual(sim.motor.seg[Alerta.TEMPERATURA].alerta_id, a_nueva.id)
        a_vieja.refresh_from_db()
        a_nueva.refresh_from_db()
        self.assertTrue(a_vieja.resuelta)
        self.assertFalse(a_nueva.resuelta)

    def test_clima_vuelve_a_despejado_inmediatamente_tras_finalizar_o_quitar_nublado(self):
        # 1) Sin escenario, ModeloFisico.clima(es_pronostico=False) nunca devuelve NUBLADO en 24h
        modelo = ModeloFisico(random.Random(1))
        for hora in range(24):
            est_clima, _, _ = modelo.clima(dt_local(2026, 11, 19, hora, 15))
            self.assertEqual(est_clima, 'DESPEJADO')

        # 2) En minuto fuera de :00/:30 (11:07), activar y quitar nublado_45 actualiza el clima al instante
        sim = Simulador(semilla=15)
        sim.sim_time = dt_local(2026, 11, 19, 11, 7)
        sim.temp_prev = 20.0
        sim.listo = True

        sim.activar_escenario('nublado_45')
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'NUBLADO')
        sim.avanzar(10)  # 11:17
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'NUBLADO')

        sim.activar_escenario('quitar')
        self.assertIsNone(sim.escenario)
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'DESPEJADO')

        # 3) Al finalizar nube_5 (5 min) a las 11:22 vuelve inmediatamente a DESPEJADO
        sim.activar_escenario('nube_5')
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'PARCIAL')
        sim.avanzar(5)  # 11:22
        self.assertIsNone(sim.escenario)
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'DESPEJADO')

        # 4) Al finalizar nublado_45 completo (45 min) a las 12:07 vuelve inmediatamente a DESPEJADO
        sim.activar_escenario('nublado_45')
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'NUBLADO')
        sim.avanzar(45)  # 12:07
        self.assertIsNone(sim.escenario)
        self.assertEqual(sim.estado()['clima']['actual']['estado'], 'DESPEJADO')
