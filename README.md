**AgroByte** es un sistema de información web desarrollado como prototipo funcional para la supervisión y el control lógico ambiental del invernadero hidropónico de **Nona Olimpia**, ubicado en Las Pampitas, Perico, Jujuy, y operado por su responsable de cultivo, Ana Savio. Su propósito principal es resolver la sobreactuación mecánica del esquema de automatización tradicional —que responde de forma instantánea a fluctuaciones pasajeras como el paso de una nube— mediante una capa de software que evalúa la persistencia real de las variables climáticas antes de emitir alertas o sugerir acciones.

El sistema monitorea de forma continua la **temperatura interna (°C)** y la **radiación solar interior (W/m²)**, integrando además un registro local de condiciones climáticas exteriores y pronósticos calibrados a la zona. Dentro del alcance definido para el prototipo, gestiona de manera lógica únicamente la **media sombra** y la **ventilación lateral**, dejando explícitamente excluidos la intervención física sobre tableros o motores existentes y la automatización del sistema de riego o solución nutritiva.

---

### Módulos y funcionalidades principales

* **Motor de evaluación con persistencia e histéresis:** Compara cada lectura ambiental contra los umbrales óptimos del cultivo activo aplicando una franja diurna configurable para la radiación (por defecto de 09:00 a 16:30). Si una desviación se revierte antes de cumplir el `tiempo_persistencia_min`, el sistema la descarta sin emitir alertas ni accionar mecanismos y la documenta como variación transitoria filtrada (`TRANSITORIA`). Cuando el desvío se sostiene, genera una alerta persistente (`ALERTA`) y mantiene su vigencia hasta que la variable regresa superando la banda de histéresis de retorno.


* **Asistencia a la decisión y modos de operación:** Calcula recomendaciones específicas (*Sombrear*, *Ventilar*, *Abrir media sombra*, *Cerrar ventilación* o sus combinaciones) verificando el estado actual de cada mecanismo para evitar sugerencias redundantes. Permite alternar entre **modo supervisado** (priorizando la intervención manual de la encargada) y **modo automático** (aplicando la acción recomendada en el modelo lógico), conservando la configuración en la base de datos y auditando cada conmutación con su origen (`MANUAL` o `AUTOMATICO`).


* **Monitor en vivo y esquema visual del invernadero:** Presenta en tiempo real los indicadores de radiación, temperatura, avance de persistencia, alertas activas y el estado de la media sombra y la ventilación, acompañados por un esquema gráfico dinámico que refleja el ciclo diurno/nocturno, la nubosidad y la posición de los mecanismos.


* **Gestión de cultivos y parámetros con trazabilidad:** Permite crear, editar y asignar cultivos con sus recomendaciones agronómicas, así como ajustar umbrales de temperatura, radiación, persistencia, histéresis y franja diurna. Cada modificación queda respaldada automáticamente en una tabla histórica de parámetros con fecha, usuario y motivo.


* **Historial, comparador de jornadas y exportación:** Ofrece gráficos evolutivos por ventana horaria (3, 6, 12 y 24 horas) con marcas de alertas, transitorias y movimientos, un comparador entre dos días con indicadores de máximos, mínimos, promedios y horas críticas, una línea de tiempo unificada filtrable por tipo, variable y fecha, y la descarga directa del registro en formato Excel (`.xlsx`).


* **Simulador físico y escenarios de validación:** Incorpora un modelo físico sintético con inercia térmica, curva solar y efecto real de la media sombra y la ventilación, junto con un reloj controlable (pausa, velocidades de `1x` a `15x` y salto a las 09:00) y cinco escenarios predefinidos (*Nube pasajera 5 min*, *Nublado largo 45 min*, *Sol intenso 30 min*, *Ola de calor 60 min* y *Sol + Calor extremo 45 min*) para demostrar el comportamiento del sistema sin hardware físico ni dependencia de APIs externas.



---

### Base técnica

La aplicación está construida en **Python** con el framework **Django**, utilizando una base de datos relacional **SQLite** estructurada en 10 tablas vinculadas (`CULTIVOS`, `PARAMETROS`, `HISTORIAL_PARAMETROS`, `INVERNADEROS`, `HISTORIAL_TEMPERATURA`, `HISTORIAL_RADIACION`, `HISTORIAL_CLIMA`, `MECANISMOS`, `HISTORIAL_MECANISMOS` y `ALERTAS`) con operaciones transaccionales atómicas. La interfaz web emplea HTML, CSS y JavaScript modular con gráficos interactivos, identidad visual adaptada a Nona Olimpia y generación de reportes `.xlsx` mediante `openpyxl`, respaldada por una batería de pruebas automatizadas sobre el motor de control, los servicios y los endpoints.
