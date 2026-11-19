"""Vistas HTML y endpoints JSON del sistema Invernadero Nona Olimpia."""
import datetime
import io
import json
from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import services
from .engine import SIMULADOR
from .models import Cultivo, Mecanismo, Parametro
from .seed import ejecutar_seed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _json_body(request):
    try:
        return json.loads(request.body or b'{}')
    except json.JSONDecodeError:
        raise services.ErrorValidacion('JSON inválido.')


def api(view):
    """Convierte errores de negocio en respuestas JSON 400 y asegura que exista el seed."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            if services.obtener_invernadero_activo() is None:
                ejecutar_seed(log=lambda m: None)
            return view(request, *args, **kwargs)
        except services.ErrorValidacion as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return wrapper


def _dia(valor):
    if not valor:
        return None
    try:
        return datetime.date.fromisoformat(valor)
    except ValueError:
        raise services.ErrorValidacion('Fecha inválida (use AAAA-MM-DD).')


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
@ensure_csrf_cookie
def index(request):
    if services.obtener_invernadero_activo() is None:
        ejecutar_seed(log=lambda m: None)   # seed automático en el primer arranque
    return render(request, 'App/index.html', {'encargada': services.ENCARGADA})


# ---------------------------------------------------------------------------
# Monitor en vivo
# ---------------------------------------------------------------------------
@require_GET
@api
def api_monitor(request):
    SIMULADOR.sincronizar()
    return JsonResponse({'ok': True, **SIMULADOR.estado()})


@require_GET
@api
def api_series(request):
    try:
        horas = int(request.GET.get('horas', 6))
    except (TypeError, ValueError):
        horas = 6
    horas = horas if horas in (3, 6, 12, 24) else 6
    SIMULADOR.sincronizar()
    inv = services.obtener_invernadero_activo()
    hasta = SIMULADOR.sim_time
    return JsonResponse({'ok': True, 'horas': horas,
                         **services.series(inv, hasta - datetime.timedelta(hours=horas), hasta)})


@require_POST
@api
def api_sim_control(request):
    datos = _json_body(request)
    accion = datos.get('accion')
    msg = ''
    if accion == 'pausar':
        SIMULADOR.pausar(True)
        msg = 'Simulador en pausa.'
    elif accion == 'reanudar':
        SIMULADOR.pausar(False)
        msg = 'Simulador reanudado.'
    elif accion == 'velocidad':
        SIMULADOR.set_velocidad(datos.get('valor', 1))
        msg = f'Velocidad {SIMULADOR.velocidad}x ({SIMULADOR.velocidad} min simulados por segundo).'
    elif accion == 'hora09':
        msg = SIMULADOR.poner_hora(9, 0)
    elif accion == 'automatico':
        SIMULADOR.set_automatico(datos.get('valor', False))
        msg = 'Modo automático ' + ('activado.' if SIMULADOR.automatico else 'desactivado (decisión manual).')
    else:
        raise services.ErrorValidacion('Acción de simulador desconocida.')
    return JsonResponse({'ok': True, 'mensaje': msg})


@require_POST
@api
def api_sim_escenario(request):
    clave = _json_body(request).get('clave', '')
    return JsonResponse({'ok': True, 'mensaje': SIMULADOR.activar_escenario(clave)})


@require_POST
@api
def api_accion_sugerida(request):
    return JsonResponse({'ok': True, **SIMULADOR.aplicar_accion_sugerida()})


@require_POST
@api
def api_mecanismo(request, mecanismo_id):
    mec = Mecanismo.objects.filter(pk=mecanismo_id).first()
    if mec is None:
        raise services.ErrorValidacion('Mecanismo inexistente.')
    estado = _json_body(request).get('estado')
    mec, hist = SIMULADOR.cambiar_mecanismo_manual(mec, estado)
    msg = (f'{mec.get_tipo_display()} → {estado} (registrado en HISTORIAL_MECANISMOS por {services.ENCARGADA}).'
           if hist else f'{mec.get_tipo_display()} ya estaba en {estado}.')
    return JsonResponse({'ok': True, 'mensaje': msg, 'mecanismo': services.mecanismo_dict(mec)})


# ---------------------------------------------------------------------------
# Parámetros y cultivos
# ---------------------------------------------------------------------------
@require_GET
@api
def api_config(request):
    inv = services.obtener_invernadero_activo()
    p = inv.id_cultivo.parametros
    cultivos = [{
        'id': c.id, 'nombre': c.nombre, 'recomendaciones': c.recomendaciones,
        'parametros': services.parametros_dict(c.parametros) if hasattr(c, 'parametros') else None,
        'invernaderos': [i.nombre for i in c.invernaderos.all()],
    } for c in Cultivo.objects.select_related('parametros').prefetch_related('invernaderos')]
    return JsonResponse({
        'ok': True,
        'invernadero': {'id': inv.id, 'nombre': inv.nombre, 'cultivo_id': inv.id_cultivo_id},
        'parametros': services.parametros_dict(p),
        'cultivos': cultivos,
        'historial_parametros': services.historial_parametros(p),
    })


@require_POST
@api
def api_parametros(request):
    datos = _json_body(request)
    inv = services.obtener_invernadero_activo()
    cultivo_id = datos.pop('cultivo_id', None) or inv.id_cultivo_id
    try:
        cultivo = Cultivo.objects.select_related('parametros').filter(pk=cultivo_id).first()
    except (TypeError, ValueError):
        cultivo = None
    if cultivo is None:
        raise services.ErrorValidacion('Cultivo inexistente.')
    try:
        parametro = cultivo.parametros
    except Parametro.DoesNotExist:
        raise services.ErrorValidacion('El cultivo no tiene parámetros configurados.')
    motivo = datos.pop('motivo', '') or 'Ajuste de umbrales desde el panel'
    p, cambios = services.actualizar_parametros(parametro, datos, services.ENCARGADA, motivo)
    if cambios and cultivo.id == inv.id_cultivo_id:
        SIMULADOR.reiniciar_evaluacion(
            f'Parámetros del cultivo activo ({cultivo.nombre}) actualizados.',
            cerrar_alertas=False,
        )
    msg = (f'Parámetros de {cultivo.nombre} actualizados ({", ".join(cambios)}); copia guardada en HISTORIAL_PARAMETROS.'
           if cambios else 'Sin cambios en los parámetros.')
    return JsonResponse({'ok': True, 'mensaje': msg, 'parametros': services.parametros_dict(p), 'cambios': cambios})


@require_POST
@api
def api_cultivo(request):
    datos = _json_body(request)
    inv = services.obtener_invernadero_activo()
    cultivo_anterior_id = inv.id_cultivo_id
    asignar = bool(datos.get('asignar'))
    cultivo = services.guardar_cultivo(
        datos.get('nombre'), datos.get('recomendaciones', ''), cultivo_id=datos.get('id') or None,
        parametros=datos.get('parametros') or None, invernadero=inv, asignar=asignar,
    )
    if asignar and cultivo.id != cultivo_anterior_id:
        SIMULADOR.reiniciar_evaluacion(
            f'Cerrada por cambio de cultivo activo a {cultivo.nombre}.',
            cerrar_alertas=True,
        )
    elif cultivo.id == cultivo_anterior_id:
        SIMULADOR.reiniciar_evaluacion(
            f'Cultivo activo ({cultivo.nombre}) modificado.',
            cerrar_alertas=False,
        )
    return JsonResponse({'ok': True, 'mensaje': f'Cultivo "{cultivo.nombre}" guardado.', 'id': cultivo.id})


@require_POST
@api
def api_asignar_cultivo(request, cultivo_id):
    inv = services.obtener_invernadero_activo()
    cultivo = Cultivo.objects.filter(pk=cultivo_id).first()
    if cultivo is None:
        raise services.ErrorValidacion('Cultivo inexistente.')
    cambio_cultivo = inv.id_cultivo_id != cultivo.id
    services.asignar_cultivo(inv, cultivo)
    if cambio_cultivo:
        SIMULADOR.reiniciar_evaluacion(
            f'Cerrada por cambio de cultivo activo a {cultivo.nombre}.',
            cerrar_alertas=True,
        )
    return JsonResponse({'ok': True, 'mensaje': f'{inv.nombre} ahora cultiva {cultivo.nombre}.'})


# ---------------------------------------------------------------------------
# Historial y comparador
# ---------------------------------------------------------------------------
@require_GET
@api
def api_historial_dias(request):
    inv = services.obtener_invernadero_activo()
    return JsonResponse({'ok': True, 'dias': services.dias_disponibles(inv)})


@require_GET
@api
def api_historial_comparar(request):
    inv = services.obtener_invernadero_activo()
    p = inv.id_cultivo.parametros
    dias = [d for d in (_dia(request.GET.get('dia1')), _dia(request.GET.get('dia2'))) if d]
    if not dias:
        raise services.ErrorValidacion('Seleccione al menos una jornada.')
    return JsonResponse({'ok': True, 'parametros': services.parametros_dict(p),
                         'jornadas': [services.estadisticas_jornada(inv, d, p) for d in dias]})


# ---------------------------------------------------------------------------
# Eventos, alertas y movimientos
# ---------------------------------------------------------------------------
def _filtros_eventos(request):
    tipo = request.GET.get('tipo', '')
    variable = request.GET.get('variable', '')
    if tipo and tipo not in services.TIPOS_EVENTO:
        raise services.ErrorValidacion('Tipo de evento inválido.')
    return tipo, variable, _dia(request.GET.get('dia'))


@require_GET
@api
def api_eventos(request):
    inv = services.obtener_invernadero_activo()
    tipo, variable, dia = _filtros_eventos(request)
    filas = services.eventos(inv, tipo, variable, dia)
    return JsonResponse({'ok': True, 'eventos': filas, 'total': len(filas)})


@require_GET
@api
def api_eventos_xlsx(request):
    inv = services.obtener_invernadero_activo()
    tipo, variable, dia = _filtros_eventos(request)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Eventos'

    headers = ['Fecha', 'Tipo', 'Variable', 'Valor', 'Duración/Origen', 'Descripción', 'Usuario']
    ws.append(headers)

    header_fill = PatternFill(start_color='1B3B22', end_color='1B3B22', fill_type='solid')
    header_font = Font(bold=True, color='FFFFFF')
    header_align = Alignment(horizontal='center', vertical='center')
    even_fill = PatternFill(start_color='F4F8F4', end_color='F4F8F4', fill_type='solid')
    thin_side = Side(border_style='thin', color='D6E2D6')
    cell_border = Border(left=thin_side, right=thin_side, top=thin_side, bottom=thin_side)

    ws.row_dimensions[1].height = 24
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        cell.border = cell_border

    data_align = Alignment(vertical='top')
    desc_align = Alignment(vertical='top', wrap_text=True)

    for e in services.eventos(inv, tipo, variable, dia, limite=10000):
        ws.append([e['fecha'], e['tipo'], e['variable'], e['valor'], e['extra'], e['descripcion'], e['usuario']])
        row_idx = ws.max_row
        is_even = row_idx % 2 == 0
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = cell_border
            cell.alignment = desc_align if col_idx == 6 else data_align
            if is_even:
                cell.fill = even_fill

    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:G{ws.max_row}'

    for col_idx in range(1, len(headers) + 1):
        max_len = 0
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, min_col=col_idx, max_col=col_idx):
            val = row[0].value
            text = '' if val is None else str(val)
            longest_line = max((len(line) for line in text.splitlines()), default=0)
            if longest_line > max_len:
                max_len = longest_line
        width = max_len + 4
        if col_idx == 6:
            width = min(max(width, 28), 65)
        else:
            width = min(max(width, 12), 34)
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    resp = HttpResponse(
        buffer.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = f'attachment; filename="eventos_nona_olimpia_{dia or "todos"}.xlsx"'
    return resp
