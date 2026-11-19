from django.core.management.base import BaseCommand

from App.seed import ejecutar_seed


class Command(BaseCommand):
    help = ('Precarga el cultivo "Lechuga Salanova", el "Invernadero 1 - Sector A", sus 2 mecanismos '
            'y N horas (24 por defecto) de historial sintético con alertas, transitorias y movimientos.')

    def add_arguments(self, parser):
        parser.add_argument('--horas', type=int, default=24, help='Horas de historial a generar (por defecto 24).')
        parser.add_argument('--reset', action='store_true', help='Borra los datos existentes y regenera todo.')

    def handle(self, *args, **opts):
        horas = max(1, min(opts['horas'], 24 * 14))
        ok = ejecutar_seed(horas=horas, reiniciar=opts['reset'], log=lambda m: self.stdout.write(m))
        if ok:
            self.stdout.write(self.style.SUCCESS('Seed de Nona Olimpia completado.'))
        else:
            self.stdout.write(self.style.WARNING('Sin cambios.'))
