from django.urls import path

from . import views

app_name = 'App'

urlpatterns = [
    path('', views.index, name='index'),
    # Monitor en vivo / simulador
    path('api/monitor/', views.api_monitor, name='api_monitor'),
    path('api/series/', views.api_series, name='api_series'),
    path('api/sim/control/', views.api_sim_control, name='api_sim_control'),
    path('api/sim/escenario/', views.api_sim_escenario, name='api_sim_escenario'),
    path('api/sim/accion-sugerida/', views.api_accion_sugerida, name='api_accion_sugerida'),
    path('api/mecanismos/<int:mecanismo_id>/', views.api_mecanismo, name='api_mecanismo'),
    # Configuración y cultivos
    path('api/config/', views.api_config, name='api_config'),
    path('api/parametros/', views.api_parametros, name='api_parametros'),
    path('api/cultivos/', views.api_cultivo, name='api_cultivo'),
    path('api/cultivos/<int:cultivo_id>/asignar/', views.api_asignar_cultivo, name='api_asignar_cultivo'),
    # Historial y comparador
    path('api/historial/dias/', views.api_historial_dias, name='api_historial_dias'),
    path('api/historial/comparar/', views.api_historial_comparar, name='api_historial_comparar'),
    # Eventos
    path('api/eventos/', views.api_eventos, name='api_eventos'),
    path('api/eventos.xlsx', views.api_eventos_xlsx, name='api_eventos_xlsx'),
]
