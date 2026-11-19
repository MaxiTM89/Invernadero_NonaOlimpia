"""
Esquema relacional SQLite - Invernadero Nona Olimpia.

10 tablas vinculadas (ninguna aislada):

    CULTIVOS 1──1 PARAMETROS 1──N HISTORIAL_PARAMETROS
    CULTIVOS 1──N INVERNADEROS
    INVERNADEROS 1──N HISTORIAL_TEMPERATURA
    INVERNADEROS 1──N HISTORIAL_RADIACION
    INVERNADEROS 1──N HISTORIAL_CLIMA
    INVERNADEROS 1──N MECANISMOS 1──N HISTORIAL_MECANISMOS
    INVERNADEROS 1──N ALERTAS
"""
import datetime

from django.db import models


# ---------------------------------------------------------------------------
# 1. CULTIVOS
# ---------------------------------------------------------------------------
class Cultivo(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    recomendaciones = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'CULTIVOS'
        ordering = ['nombre']
        verbose_name = 'Cultivo'
        verbose_name_plural = 'Cultivos'

    def __str__(self):
        return self.nombre


# ---------------------------------------------------------------------------
# 2. PARAMETROS  (OneToOne con CULTIVOS)
# ---------------------------------------------------------------------------
class Parametro(models.Model):
    id_cultivo = models.OneToOneField(
        Cultivo, on_delete=models.CASCADE, related_name='parametros', db_column='id_cultivo'
    )
    temp_min = models.FloatField(help_text='Temperatura mínima óptima (°C)')
    temp_max = models.FloatField(help_text='Temperatura máxima óptima (°C)')
    rad_min = models.FloatField(help_text='Radiación mínima óptima (W/m²)')
    rad_max = models.FloatField(help_text='Radiación máxima óptima (W/m²)')
    tiempo_persistencia_min = models.PositiveIntegerField(
        default=10, help_text='Minutos fuera de rango antes de emitir alerta'
    )
    # Umbrales avanzados (extensión del motor de evaluación)
    histeresis_temp = models.FloatField(default=1.0, help_text='Histéresis de retorno (°C)')
    histeresis_rad = models.FloatField(default=40.0, help_text='Histéresis de retorno (W/m²)')
    hora_inicio_diurna = models.TimeField(default=datetime.time(9, 0))
    hora_fin_diurna = models.TimeField(default=datetime.time(16, 30))
    fecha_actualizacion = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'PARAMETROS'
        verbose_name = 'Parámetros del cultivo'
        verbose_name_plural = 'Parámetros de cultivos'

    def __str__(self):
        return f'Parámetros de {self.id_cultivo.nombre}'


# ---------------------------------------------------------------------------
# 3. HISTORIAL_PARAMETROS  (auditoría de cada modificación)
# ---------------------------------------------------------------------------
class HistorialParametro(models.Model):
    id_parametro = models.ForeignKey(
        Parametro, on_delete=models.CASCADE, related_name='historial', db_column='id_parametro'
    )
    temp_min = models.FloatField()
    temp_max = models.FloatField()
    rad_min = models.FloatField()
    rad_max = models.FloatField()
    tiempo_persistencia_min = models.PositiveIntegerField()
    histeresis_temp = models.FloatField(default=1.0)
    histeresis_rad = models.FloatField(default=40.0)
    hora_inicio_diurna = models.TimeField(default=datetime.time(9, 0))
    hora_fin_diurna = models.TimeField(default=datetime.time(16, 30))
    usuario = models.CharField(max_length=80, default='Ana Savio')
    motivo = models.CharField(max_length=200, blank=True, default='')
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'HISTORIAL_PARAMETROS'
        ordering = ['-fecha', '-id']
        verbose_name = 'Historial de parámetros'
        verbose_name_plural = 'Historial de parámetros'

    def __str__(self):
        return f'{self.id_parametro} @ {self.fecha:%d/%m/%Y %H:%M}'


# ---------------------------------------------------------------------------
# 4. INVERNADEROS
# ---------------------------------------------------------------------------
class Invernadero(models.Model):
    nombre = models.CharField(max_length=120, unique=True)
    id_cultivo = models.ForeignKey(
        Cultivo, on_delete=models.PROTECT, related_name='invernaderos', db_column='id_cultivo'
    )
    modo_automatico = models.BooleanField(default=False)

    class Meta:
        db_table = 'INVERNADEROS'
        ordering = ['id']
        verbose_name = 'Invernadero'
        verbose_name_plural = 'Invernaderos'

    def __str__(self):
        return self.nombre


# ---------------------------------------------------------------------------
# 5. HISTORIAL_TEMPERATURA
# ---------------------------------------------------------------------------
class HistorialTemperatura(models.Model):
    grados = models.FloatField()
    fecha = models.DateTimeField(db_index=True)
    id_invernadero = models.ForeignKey(
        Invernadero, on_delete=models.CASCADE, related_name='temperaturas', db_column='id_invernadero'
    )

    class Meta:
        db_table = 'HISTORIAL_TEMPERATURA'
        ordering = ['fecha']
        indexes = [models.Index(fields=['id_invernadero', 'fecha'], name='idx_temp_inv_fecha')]
        verbose_name = 'Lectura de temperatura'
        verbose_name_plural = 'Historial de temperatura'

    def __str__(self):
        return f'{self.grados:.1f} °C @ {self.fecha:%d/%m %H:%M}'


# ---------------------------------------------------------------------------
# 6. HISTORIAL_RADIACION
# ---------------------------------------------------------------------------
class HistorialRadiacion(models.Model):
    valor = models.FloatField(help_text='W/m²')
    fecha = models.DateTimeField(db_index=True)
    id_invernadero = models.ForeignKey(
        Invernadero, on_delete=models.CASCADE, related_name='radiaciones', db_column='id_invernadero'
    )

    class Meta:
        db_table = 'HISTORIAL_RADIACION'
        ordering = ['fecha']
        indexes = [models.Index(fields=['id_invernadero', 'fecha'], name='idx_rad_inv_fecha')]
        verbose_name = 'Lectura de radiación'
        verbose_name_plural = 'Historial de radiación'

    def __str__(self):
        return f'{self.valor:.0f} W/m² @ {self.fecha:%d/%m %H:%M}'


# ---------------------------------------------------------------------------
# 7. HISTORIAL_CLIMA  (clima actual y pronósticos)
# ---------------------------------------------------------------------------
class HistorialClima(models.Model):
    ESTADOS = [
        ('DESPEJADO', 'Despejado'),
        ('PARCIAL', 'Parcialmente nublado'),
        ('NUBLADO', 'Nublado'),
        ('LLUVIA', 'Lluvia'),
        ('CALUROSO', 'Caluroso / Ola de calor'),
    ]
    id_invernadero = models.ForeignKey(
        Invernadero, on_delete=models.CASCADE, related_name='climas', db_column='id_invernadero'
    )
    estado = models.CharField(max_length=20, choices=ESTADOS)
    temp_externa = models.FloatField()
    probabilidad_lluvia = models.PositiveSmallIntegerField(default=0)
    es_pronostico = models.BooleanField(default=False)
    fecha = models.DateTimeField(db_index=True)

    class Meta:
        db_table = 'HISTORIAL_CLIMA'
        ordering = ['-fecha']
        verbose_name = 'Registro de clima'
        verbose_name_plural = 'Historial de clima'

    def __str__(self):
        tipo = 'Pronóstico' if self.es_pronostico else 'Actual'
        return f'{tipo}: {self.get_estado_display()} {self.temp_externa:.1f} °C @ {self.fecha:%d/%m %H:%M}'


# ---------------------------------------------------------------------------
# 8. MECANISMOS
# ---------------------------------------------------------------------------
class Mecanismo(models.Model):
    MEDIA_SOMBRA = 'MEDIA_SOMBRA'
    VENTILACION = 'VENTILACION'
    TIPOS = [
        (MEDIA_SOMBRA, 'Media sombra'),
        (VENTILACION, 'Ventilación lateral'),
    ]
    # Estados válidos por tipo de mecanismo
    ESTADOS_VALIDOS = {
        MEDIA_SOMBRA: ['ABIERTA', 'CERRADA'],   # CERRADA = desplegada (sombrea)
        VENTILACION: ['ABIERTA', 'CERRADA'],
    }
    ESTADO_INICIAL = {MEDIA_SOMBRA: 'ABIERTA', VENTILACION: 'CERRADA'}

    id_invernadero = models.ForeignKey(
        Invernadero, on_delete=models.CASCADE, related_name='mecanismos', db_column='id_invernadero'
    )
    tipo = models.CharField(max_length=20, choices=TIPOS)
    estado = models.CharField(max_length=20)

    class Meta:
        db_table = 'MECANISMOS'
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['id_invernadero', 'tipo'], name='uniq_mecanismo_por_invernadero')
        ]
        verbose_name = 'Mecanismo'
        verbose_name_plural = 'Mecanismos'

    def __str__(self):
        return f'{self.get_tipo_display()} ({self.estado}) - {self.id_invernadero}'


# ---------------------------------------------------------------------------
# 9. HISTORIAL_MECANISMOS
# ---------------------------------------------------------------------------
class HistorialMecanismo(models.Model):
    MANUAL = 'MANUAL'
    AUTOMATICO = 'AUTOMATICO'
    ORIGENES = [(MANUAL, 'Manual'), (AUTOMATICO, 'Automático')]

    id_mecanismo = models.ForeignKey(
        Mecanismo, on_delete=models.CASCADE, related_name='historial', db_column='id_mecanismo'
    )
    fecha = models.DateTimeField(db_index=True)
    a_que_estado_paso = models.CharField(max_length=20)
    origen = models.CharField(max_length=12, choices=ORIGENES)
    usuario = models.CharField(max_length=80, default='Sistema')
    detalle = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'HISTORIAL_MECANISMOS'
        ordering = ['-fecha', '-id']
        verbose_name = 'Movimiento de mecanismo'
        verbose_name_plural = 'Historial de mecanismos'

    def __str__(self):
        return f'{self.id_mecanismo.get_tipo_display()} → {self.a_que_estado_paso} ({self.origen})'


# ---------------------------------------------------------------------------
# 10. ALERTAS
# ---------------------------------------------------------------------------
class Alerta(models.Model):
    TEMPERATURA = 'TEMPERATURA'
    RADIACION = 'RADIACION'
    VARIABLES = [(TEMPERATURA, 'Temperatura'), (RADIACION, 'Radiación')]

    ALERTA = 'ALERTA'
    TRANSITORIA = 'TRANSITORIA'
    TIPOS = [(ALERTA, 'Alerta persistente'), (TRANSITORIA, 'Variación transitoria filtrada')]

    RECOMENDACIONES = [
        ('SOMBREAR', 'SOMBREAR (cerrar media sombra)'),
        ('VENTILAR', 'VENTILAR (abrir ventilación)'),
        ('SOMBREAR_Y_VENTILAR', 'SOMBREAR Y VENTILAR (Ambas)'),
        ('ABRIR_MEDIA_SOMBRA', 'ABRIR MEDIA SOMBRA'),
        ('CERRAR_VENTILACION', 'CERRAR VENTILACIÓN'),
        ('VENTILAR_Y_ABRIR_MEDIA_SOMBRA', 'VENTILAR Y ABRIR MEDIA SOMBRA'),
        ('SOMBREAR_Y_CERRAR_VENTILACION', 'SOMBREAR Y CERRAR VENTILACIÓN'),
        ('ABRIR_MEDIA_SOMBRA_Y_CERRAR_VENTILACION', 'ABRIR MEDIA SOMBRA Y CERRAR VENTILACIÓN'),
        ('MANTENER', 'MANTENER'),
    ]

    id_invernadero = models.ForeignKey(
        Invernadero, on_delete=models.CASCADE, related_name='alertas', db_column='id_invernadero'
    )
    variable = models.CharField(max_length=20, choices=VARIABLES)
    valor_detectado = models.FloatField()
    descripcion = models.TextField()
    fecha = models.DateTimeField(db_index=True)
    resuelta = models.BooleanField(default=False)
    # Extensión para el motor de persistencia
    tipo = models.CharField(max_length=12, choices=TIPOS, default=ALERTA)
    direccion = models.CharField(max_length=5, choices=[('ALTA', 'Por encima'), ('BAJA', 'Por debajo')])
    recomendacion = models.CharField(max_length=45, choices=RECOMENDACIONES, default='MANTENER')
    valor_pico = models.FloatField(null=True, blank=True)
    duracion_min = models.PositiveIntegerField(default=0)
    fecha_resolucion = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'ALERTAS'
        ordering = ['-fecha', '-id']
        indexes = [models.Index(fields=['id_invernadero', 'resuelta'], name='idx_alerta_inv_resuelta')]
        verbose_name = 'Alerta'
        verbose_name_plural = 'Alertas'

    def __str__(self):
        return f'[{self.tipo}] {self.get_variable_display()} {self.valor_detectado:.1f} @ {self.fecha:%d/%m %H:%M}'
