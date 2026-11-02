# Validation Engine v2

`validation.py` decide por contrato y capacidad, sin importar al agente ni SDK de IA.
`tipo` sólo clasifica. `mantiene` conserva su modo y queda en `NEEDS_REVIEW`.

## Estado y resultado

- `PENDING`: aún abierto, sin evidencia de resolución anticipada.
- `NEEDS_REVIEW`: contrato ausente o semántica no implementada; revisión humana.
- `DATA_ERROR`: fallo del proveedor o ventana sin barras utilizables; reintento automático.
- `RESOLVED`: resultado final escrito una sola vez.

`resultado`: **1 = ocurrió, 0 = no ocurrió, NULL = desconocido**. No expresa si el
agente acertó. Brier = `(probabilidad - resultado) ** 2`: una probabilidad 0.20 y
resultado 0 producen Brier 0.04. El nombre de API legacy `tasa_acierto` se conserva
por compatibilidad y representa la frecuencia observada del evento.

`resuelto` guarda el timestamp UTC de escritura. `fecha_resolucion` guarda el día
del toque/cierre o del vencimiento sin toque. Las velas diarias no aportan precisión
intradía. `validacion_auditoria` conserva cada evaluación distinta, versión del
motor, rango, barras utilizadas y límites temporales aportados por el adaptador.
La predicción referencia la evidencia en `fuente_resolucion`.

## Ventana conservadora

Se inspeccionó el esquema y los 102 registros reales antes de implementar el
resolver: 101 `creado` tienen hora y zona; uno usa timestamp sin zona. No se
reescribe ninguna fecha antigua. Los nuevos registros usan ISO UTC completo.

- Siempre se excluye el día de creación, también para `cierra`.
- Se excluyen velas del día UTC actual y cualquier vela cuyo fin conservador sea
  posterior al momento de evaluación. No se usa la cinta en vivo.
- El adaptador exige zona horaria en el índice de yfinance. Para futuros y FX,
  considera como límite inferior de inicio la medianoche del día anterior a la
  etiqueta de sesión; si ese intervalo puede preceder a la predicción, lo excluye.
  Esto puede sacrificar una sesión adicional para evitar contaminación temporal.
- No se atribuyen horas de liquidación a etiquetas de yfinance. El límite superior
  de fin es la medianoche local siguiente, usando la zona de la serie y su DST.
- `fecha_limite=YYYY-MM-DD` incluye esa sesión. Se espera hasta las **12:00 UTC del
  día siguiente** para declarar vencido el horizonte, un margen conservador para
  completar las velas. Un timestamp de plazo exige zona; sólo acepta barras cuyo
  límite de fin demostrado sea anterior o igual al plazo.
- `toca`: HIGH para `>=`/`>`, LOW para `<=`/`<`. El primer toque válido permite
  resultado 1 anticipado. Sin toque, permanece pendiente hasta vencer.
- `cierra`: sólo compara el CLOSE de la última barra válida del horizonte, nunca
  un cierre intermedio ni antes del vencimiento.
- Una serie vacía/inválida nunca equivale a resultado 0. Duplicados de sesión,
  NaN/inf y OHLC incoherentes tampoco producen resultados silenciosos.

El proveedor existente sigue siendo yfinance, con `auto_adjust=False` y
`back_adjust=False`. El motor sigue el contrato de última barra válida; no certifica
la integridad de todas las sesiones ni detecta todos los posibles huecos del proveedor.
Los tests usan series controladas y no requieren consultas a Yahoo ni llamadas a IA.

### Espera por solapamiento temporal — corrección aprobada el 2026-09-12

Si el plazo sigue abierto y el proveedor devuelve velas completas con OHLC válido,
pero todas las velas candidatas se excluyen porque su inicio conservador es anterior
o igual a la creación de la predicción, el estado es `PENDING`: se espera la primera
sesión admisible. El caso ya no se confunde con una descarga vacía.

La corrección conserva el margen temporal, las velas aceptadas y las fórmulas de
resultado/Brier. No recupera sesiones descartadas ni resuelve por precios anteriores
a la predicción. `ENGINE_VERSION` sigue identificando el cálculo `price-daily-v1`;
el diagnóstico de disponibilidad se identifica por separado como
`diagnostico_temporal.version=data-availability-v1` en la evidencia persistida.

Sin ninguna vela admisible, descargas vacías, errores del proveedor, OHLC inválido,
duplicados o un plazo ya vencido siguen siendo `DATA_ERROR`. También se conserva
el comportamiento existente de las velas cuyo fin todavía no es demostrablemente
completo. Una mezcla con datos inválidos no se convierte en una espera normal.

Los cambios de estado pasan por `guardar_evaluacion`: conservan la auditoría previa,
son idempotentes y no modifican contratos ni resoluciones finales. Pruebas offline
en `tests/test_validation_temporal.py`, con los seis casos observados como fixture,
cubren el diagnóstico, errores reales, persistencia y protección de resultados.

### Vencimientos en días sin sesión — criterio acordado el 2026-09-09

Para una predicción de cierre, si la fecha límite cae en fin de semana o festivo
del mercado correspondiente, se comprueba el cierre de la **última sesión anterior**.
No se usa la sesión siguiente ni se resta siempre un único día natural: un domingo
o un lunes festivo puede remitir al viernes. La fecha límite original se conserva;
`fecha_resolucion` identifica la sesión usada. No se adelanta por ello el margen
conservador de espera para escribir una resolución automática.

`cierra` ya selecciona la última barra válida del horizonte y aplica este criterio
cuando la serie contiene correctamente las sesiones del mercado. Se añaden pruebas
explícitas para sábado, domingo y un festivo simulado. BTC/ETH no se desplazan al
viernes por ser fin de semana si existe su sesión diaria válida.

La ausencia de una vela no demuestra un festivo: no se ha añadido un calendario de
mercado ni detección completa de huecos del proveedor. Esta limitación ya existente
se mantiene explícita; una revisión manual debe contrastar el motivo de la ausencia.
No se cambian los prompts ni la generación del baseline para aplicar este criterio
de comprobación de cierres.

En la revisión legacy, el usuario confirmó expresamente que #88 se evalúa por toque
intradía (la frase «tocará o superará 160»), y #85 por la sesión anterior al sábado
29/08. Estas decisiones se guardan en la auditoría manual, conservando los textos
y campos originales. No se infieren contratos estructurados retrospectivos.

## Migración y conservación

`memoria.init_memoria()` ejecuta `migrations.migrate_predictions()` en transacción.
Añade las columnas faltantes antes de hacer backfill. En esta base:

- 91 resueltas: v1, autor `agente`, estado `RESOLVED`; resultado/Brier originales.
- 11 abiertas: v1, origen `legacy`, `NEEDS_REVIEW`; sin inferir condición/modo.

Copia previa: `market_pulse.db.pre_validation_v2.backup` (ignorada por Git).
La migración es repetible. Triggers bloquean cambios al contrato, incluso rellenar
NULL, resultados centinela, nuevas filas sin procedencia v2 y reescrituras de
resoluciones finales. No se ofrece edición retrospectiva del contrato legacy.
`specification_source` permite separar cualquier futura especificación manual;
las nuevas predicciones se insertan como `original` y permanecen congeladas.

La herramienta del agente sólo registra: guarda provider, modelo solicitado,
identificador de modelo devuelto en la respuesta (`model_snapshot`), versión del
prompt y versión del motor. Si el proveedor devuelve un alias, se conserva ese
identificador tal cual; no se inventa un snapshot más preciso.

## Operación

La aplicación registra `validacion_predicciones` **cada hora en el minuto 10**, con
una sola instancia y coalescencia. Arranca con el servidor, después de migrar.
El estado del último job aparece en `/api/estado`, dentro de `validacion`.
`DATA_ERROR` se reintenta. Las resoluciones finales y las decisiones manuales
NO RESOLUBLE se preservan. Una nueva resolución manual explícita puede revisar
una decisión NO RESOLUBLE cuando aparezca evidencia.

La cola está en `/calibracion#revision`:

- **OCURRIÓ / NO OCURRIÓ** requieren fuente/justificación y fecha real del hecho.
- **NO RESOLUBLE** exige justificación y deja resultado/Brier/resuelto en NULL.
- Los errores técnicos se muestran aparte y no se pueden ocultar con una decisión
  manual. Sólo `NEEDS_REVIEW` admite resolución humana.

API: `/api/predicciones/revision`, `/api/predicciones/{id}/auditoria` y
`POST /api/predicciones/{id}/resolver`. Un POST sin `resultado` es inválido; jamás
se presupone 0. Los cálculos de calibración y el historial admiten filtros
`schema_version`, `resuelto_por`, `specification_source`. El panel de calibración
abre por defecto el dataset v2 original; legacy se selecciona expresamente.

## Verificación

```sh
conda activate mp
python -m unittest discover -s tests -v
```

Incluye los 12 casos obligatorios, migración legacy, inmutabilidad de NULL y
metadatos, resolución concurrente/idempotencia, separación de estados, auditoría,
API, procedencia del agente, adaptador OHLC y conexión al scheduler. Los tests de
integración requieren las dependencias del proyecto; los del motor usan stdlib.

Quedan fuera del alcance actual el resolver de `mantiene`, eventos complejos,
proveedores IA comunes, A/B y trading real.
