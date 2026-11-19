from django.contrib import admin

from .models import (
    Alerta, Cultivo, HistorialClima, HistorialMecanismo, HistorialParametro,
    HistorialRadiacion, HistorialTemperatura, Invernadero, Mecanismo, Parametro,
)

admin.site.site_header = 'Invernadero Nona Olimpia – Administración'
admin.site.site_title = 'Nona Olimpia'
admin.site.index_title = 'Sistema de Monitoreo y Asistencia a la Decisión'


class ParametroInline(admin.StackedInline):
    model = Parametro
    can_delete = False
    extra = 0


class MecanismoInline(admin.TabularInline):
    model = Mecanismo
    extra = 0


@admin.register(Cultivo)
class CultivoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre')
    search_fields = ('nombre',)
    inlines = [ParametroInline]


@admin.register(Parametro)
class ParametroAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_cultivo', 'temp_min', 'temp_max', 'rad_min', 'rad_max',
                    'tiempo_persistencia_min', 'histeresis_temp', 'histeresis_rad',
                    'hora_inicio_diurna', 'hora_fin_diurna', 'fecha_actualizacion')


@admin.register(HistorialParametro)
class HistorialParametroAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_parametro', 'temp_min', 'temp_max', 'rad_min', 'rad_max',
                    'tiempo_persistencia_min', 'usuario', 'fecha')
    list_filter = ('id_parametro',)
    date_hierarchy = 'fecha'


@admin.register(Invernadero)
class InvernaderoAdmin(admin.ModelAdmin):
    list_display = ('id', 'nombre', 'id_cultivo', 'modo_automatico')
    inlines = [MecanismoInline]


@admin.register(HistorialTemperatura)
class HistorialTemperaturaAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_invernadero', 'grados', 'fecha')
    list_filter = ('id_invernadero',)
    date_hierarchy = 'fecha'


@admin.register(HistorialRadiacion)
class HistorialRadiacionAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_invernadero', 'valor', 'fecha')
    list_filter = ('id_invernadero',)
    date_hierarchy = 'fecha'


@admin.register(HistorialClima)
class HistorialClimaAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_invernadero', 'estado', 'temp_externa', 'probabilidad_lluvia',
                    'es_pronostico', 'fecha')
    list_filter = ('es_pronostico', 'estado')
    date_hierarchy = 'fecha'


@admin.register(Mecanismo)
class MecanismoAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_invernadero', 'tipo', 'estado')
    list_filter = ('tipo',)


@admin.register(HistorialMecanismo)
class HistorialMecanismoAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_mecanismo', 'a_que_estado_paso', 'origen', 'usuario', 'fecha')
    list_filter = ('origen', 'id_mecanismo__tipo')
    date_hierarchy = 'fecha'


@admin.register(Alerta)
class AlertaAdmin(admin.ModelAdmin):
    list_display = ('id', 'id_invernadero', 'tipo', 'variable', 'direccion', 'valor_detectado',
                    'recomendacion', 'duracion_min', 'resuelta', 'fecha', 'fecha_resolucion')
    list_filter = ('tipo', 'variable', 'resuelta', 'recomendacion')
    date_hierarchy = 'fecha'
    search_fields = ('descripcion',)
