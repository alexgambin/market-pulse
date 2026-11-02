# POSITIONING — implementación por fases

## Estado a 2026-09-09

Fase 1 implementada: arquitectura, esquema, contratos y aislamiento del baseline.
La especificación acordada está conservada en [POSITIONING_DECISIONES.md](POSITIONING_DECISIONES.md).
La auditoría inicial queda como referencia histórica, no como decisiones vigentes.

Por petición posterior del usuario, se ha adelantado una **vista nativa de las seis
series COT existentes**, accesible en `/#positioning`, con una API de presentación.
No están implementados aún los adaptadores nuevos, scores/percentiles, backfill,
jobs de POSITIONING, su API completa ni registro de contexto en el bucle. La Fase 2 depende
de cerrar la política de completitud que se ha consultado al usuario. No existe
una versión de cálculo activa ni se han generado métricas de producción.

## Límite experimental

- `config.POSITIONING_AGENT_ENABLED = False`.
- `agente.py`, `cot.py`, `derivados.py`, `memoria.py`, `validation.py`, prompts,
  modelos y el contrato de observabilidad permanecen intactos.
- La herramienta nueva NO existe en `HERRAMIENTAS` ni en el payload del proveedor.
- La constante es una reserva para Fase 6: cambiarla hoy a true no habilita nada.
- Se conserva `ENGINE_VERSION = price-daily-v1`, también en predicciones nuevas.
  No se reetiquetan pronósticos ni se modifica el motor para desplegar infraestructura.
- El futuro experimento necesitará una versión descriptiva de generación y una
  activación explícita. La disponibilidad de POSITIONING será el único cambio
  experimental previsto. No se cambia a la vez modelo, instrucciones o probabilidades.

Que `used_positioning=false` no implica ausencia de todo COT: el resumen COT que
ya recibe el agente forma parte del baseline. La comparación futura estudia el
valor incremental de la nueva capa, no «COT frente a ningún COT».

## Arquitectura

Implementado:

- `positioning/contracts.py`: identidad CFTC, familias/participantes, JSON canónico,
  rechazo de NaN/Infinity, timestamps UTC y política de disponibilidad.
- `positioning/schema.py`: DDL, restricciones, procedencia externa y registro
  inmutable de versiones.
- `positioning/repository.py`: conexión SQLite con foreign keys y cierre garantizado.
- `migrations.migrate_positioning(c)`: entrada consistente con las migraciones actuales.
- Inicialización en `main.lifespan`, después de memoria y observabilidad. No añade jobs.

Siguientes fases: `cftc.py` y `engine.py` puros; servicio/repositorio de ingestas;
API compartida; captura general de consultas y contexto; herramienta desactivada;
ampliación de la vista nativa de `static/mercados.html`. El adaptador BTC reutilizará derivados.py,
conservando unidades y timestamps, sin un segundo cliente ni score cripto.

La capa no importa el agente ni SDKs IA. Importar `positioning` no ejecuta I/O.
SQLite es la base compartida actual: no hay servidor Node, iframe ni otra aplicación.

## Vista COT existente — entrega de frontend

Entrada **POSITIONING · COT** en la navegación principal, tarjeta de acceso desde
Resumen y enlace desde el dashboard secundario. URL: `http://localhost:8000/#positioning`.

Archivos de esta entrega: `positioning/presentation.py`, `static/positioning.js`,
`static/positioning.css`, `tests/test_cot_overview.py`, `tests/browser/cot_view.cjs`
y su fixture sintético `tests/fixtures/cot_overview.json`. Cambios acotados en
`main.py`, `static/mercados.html` y `static/index.html`.

La nueva ruta **GET /api/cot/overview** usa `cot.historico()` y su caché existente de
24 h. Devuelve `api_version=cot-overview-v1`: identidad, familia, participante,
fecha de posiciones, neto, delta semanal, antigüedad e histórico. El endpoint
`/api/cot` original conserva su contrato. No se añade polling ni se fuerzan
descargas al pulsar «Actualizar vista»; se vuelve a consultar la caché del servidor.
Las aperturas simultáneas se serializan para reutilizar las descargas iniciales.

Series: WTI, Oro, Plata (Disaggregated / Managed Money); yen, euro y Bitcoin CME
(TFF / Leveraged Funds). No se añade ningún instrumento ni Legacy en esta entrega.
Yen conserva la orientación 6J frente a USD: no se invierte su neto ni se presenta
como un dato CFTC observado de USD/JPY. Bitcoin CME se identifica como futuros.

Python valida las filas recibidas y calcula `weekly_change` únicamente si los
dos informes se separan por siete días; de lo contrario devuelve NULL. Fechas
inválidas/futuras, valores no finitos, ausencia de datos y duplicados contradictorios
no producen cero ni una posición neutral. Los duplicados idénticos se deduplican.
No hay percentiles, COT Index, scores, clasificaciones direccionales ni rankings
de extremos: continúan pendientes del motor y de la política de completitud.

La UI permite filtrar FX/Metales/Energía/Cripto y seleccionar 13/26/52 informes
del histórico disponible. El gráfico SVG es nativo, con barras separadas, escala
temporal que respeta huecos, valores al pasar el cursor o enfocar con teclado,
y tabla accesible de datos. No requiere otra librería gráfica o servicio.

Antigüedad: días desde la fecha de posiciones; aviso explícito al superar 14.
Esta convención de pantalla NO certifica actualidad frente al calendario oficial.
El adaptador original no conserva fecha de publicación/descarga: `published_at`
y `fetched_at` permanecen NULL. `consulted_at` no se presenta como descarga.

Ante fallo se conserva el último histórico válido que haya visto esta vista en
la sesión del servidor, con estado `LAST_VALID` y aviso de error. No se acepta un
retroceso silencioso a un informe anterior. Esa retención es sólo en RAM y no
garantiza recuperación tras reinicio; persistencia y vintages siguen en Fase 3.
La vista tampoco recupera filas que el parser antiguo haya descartado antes de
entregarlas: no se presenta como un nuevo adaptador CFTC auditado.

El agente YA recibía `cot.resumen()` dentro de su contexto y sigue recibiéndolo.
Esta entrega no cambia su resumen, sus herramientas, los prompts ni los modelos.
La nueva herramienta POSITIONING permanece ausente del payload. El resumen
disponible no demuestra influencia causal sobre cada predicción; aún no existe
la captura de exposición prevista en las fases siguientes.

Verificación: **106 tests Python pasan** (9 nuevos para la vista COT), incluidas
las protecciones del baseline. Prueba de navegador offline en Chrome: seis series,
filtros, ventanas, orientación del yen, errores HTTP, conservación del gráfico,
estado sin datos, móvil de 390 px y ausencia de errores JS. No se necesitan llamadas
a CFTC/Yahoo/Binance/IA para esas pruebas.

Para repetir la prueba visual con Playwright instalado en un directorio temporal:

```sh
npm install --prefix /tmp/mp-positioning-browser --no-save playwright
NODE_PATH=/tmp/mp-positioning-browser/node_modules node tests/browser/cot_view.cjs
```

Usa Chrome instalado; opcionalmente `PLAYWRIGHT_CHROMIUM_EXECUTABLE` indica el
binario. Capturas en un directorio temporal anunciado por el script. Playwright
no es una dependencia de producción de Market Pulse.

Además de los tests, una comprobación de lectura contra el servidor real devolvió
las seis series con 52 informes cada una y última fecha de posiciones 01/09/2026.
Esa comprobación sí consultó fuentes reales mediante el adaptador existente.

## Identidad y universo

Una identidad de serie incluye instrumento, fuente, familia, alcance, código de
mercado, participante y versión del mapping. El ID se obtiene de una representación
canónica, sin depender del orden de un diccionario ni de etiquetas traducidas.
El código es texto: `067651` y `13874+` conservan todos sus caracteres.

El instrumento usa la clave exacta de `precios.INSTRUMENTOS`. Estar en ese catálogo
es necesario pero no demuestra equivalencia de un contrato CFTC. No se habilita
ningún mapping nuevo en esta fase. Cada alta deberá conservar símbolo de precio,
relación, nombre oficial, vigencia y evidencia de validación. La pertenencia al
universo se valida en Python; no se replica ni congela todo precios.py en SQL.

Legacy Non-Commercial, Managed Money y Leveraged Funds son categorías separadas.
La base rechaza mezclas de familia, dataset, participante o contrato. No se unen
variantes consolidated/e-mini/micro, ni Nasdaq-100 con Nasdaq Composite, ni un
futuro de Treasury con ^TNX. `INVERSE_QUOTE` expresa una relación con un precio,
no convierte la serie observada en un dato CFTC del par inverso.

Se mantienen los seis contratos usados actualmente por cot.py: tres Disaggregated
y tres TFF. La nueva capa validará mappings dentro del universo actual; no añade
agricultura ni criptomonedas nuevas. La presencia de ETH en precios permite evaluar
un mapping CFTC, no ampliar los derivados Binance más allá de BTC.

## Esquema físico

| Tabla nueva | Responsabilidad |
|---|---|
| positioning_versions | Especificación JSON, hash, tipo y fecha de cada versión; una versión no puede redefinirse |
| positioning_series | Instrumento y participante CFTC con mapping versionado, alcance, unidad y evidencia |
| positioning_ingestions | Un resultado final por intento: consulta, conteos, errores y duración; no costes IA |
| positioning_releases | Evidencia versionada de calendario/publicación: programado, confirmado, retrasado o sin informe |
| positioning_raw_reports | Payload CFTC por contrato/fecha/revisión; OI una vez por informe, fetched_at y known_at |
| positioning_observations | Long/short/spreading por participante, referencia al bruto y calidad |
| positioning_metrics | Snapshot derivado: net, net/OI, delta, percentiles 52/156, scores, estado y versión |
| positioning_metric_inputs | IDs exactos de observaciones históricas o snapshots de patas sintéticas y transformación |
| forecast_context_runs | Ejecución, versión real del motor y esquema completo de herramientas expuesto al modelo |
| tool_invocations | Invocación general, argumentos, resultado exacto, error, referencias y secuencia de respuesta |
| tool_result_deliveries | Entrega de un resultado en una petición posterior al proveedor |
| prediction_contexts | Corte AVAILABLE, ejecución/secuencia de generación, estado de la traza y metadatos externos |
| prediction_tool_usage | Asociación de predicción con invocaciones cuyo resultado llegó antes de formularla |

Las 13 tablas son append-only, con triggers que impiden UPDATE y DELETE. Los
intentos se insertarán al terminar; un proceso interrumpido antes de escribir tendrá
log de inicio, pero no se debe fingir una ingesta completada. La implementación de
recuperación de Fase 3 cubrirá esta distinción.

El OI reside en el informe bruto y long/short en la observación del participante.
Net/normalized_net son derivados versionados; la API podrá ensamblar todos los
componentes sin repetir OI por participante como una segunda verdad independiente.
Los percentiles 52/156 tienen columnas explícitas. El JSON de métricas alojará
ventanas adicionales, COT Index y detalles de cobertura con contrato versionado.
Cada campo calculado tendrá una ubicación canónica: no duplicar las columnas como
valores independientes dentro de ese JSON.

Los sintéticos no tienen net de contratos, OI ni clasificación observados. Los
padres deben compartir fecha y versión de cálculo. El servicio deberá verificar
también familias, participantes, ventana, alineación y ambas patas antes de insertar
el snapshot; las restricciones SQL no sustituyen el motor ni esos tests de Fase 2.

El esquema CFTC de observaciones no pretende almacenar funding como contratos.
Los futuros adaptadores compartirán el contrato de respuesta/procedencia; su
almacenamiento físico se decidirá sin convertir estas columnas CFTC en un EAV.

## Migración y compatibilidad

Versión actual: `positioning-schema-v1.2`. La primera revisión `v1` fue aplicada
por la recarga del servidor durante Fase 1. Al añadir la protección de inputs contra
look-ahead, la comprobación detectó correctamente el cambio de definición. Se
introdujo `v1.1` de forma aditiva y se conservó el registro `v1`; no se sobrescribió
su hash. La revisión `v1.2` añade la restricción de entrega de herramientas anterior
a la fecha de creación del pronóstico, además del orden de iteraciones. En una
base nueva se registra directamente la revisión actual.

La migración usa SAVEPOINT, no executescript ni commit interno, y respeta una
transacción exterior. Una ejecución fallida revierte sus propias escrituras.
Se ejecuta con foreign_keys activadas por la conexión de positioning. No añade
columnas ni triggers a tablas existentes. No cambia PRAGMA user_version global.

Se verificó dos veces sobre una copia en memoria de la base activa, obtenida con
la fuente abierta en modo lectura: 18 tablas preexistentes, sus definiciones y
sus contenidos preservados; las 106 predicciones presentes en esa copia intactas.
La comprobación no ejecuta validaciones ni actualiza la base fuente.

## Tiempo y disponibilidad

Política guardada: `positioning-availability-local-v1`.

- report_date: fecha representada por las posiciones.
- scheduled_at: publicación prevista, respaldada por una versión de calendario.
- published_at: publicación acreditada; NULL si no se puede establecer.
- fetched_at: recepción efectiva de esa revisión en Market Pulse.
- known_at: disponibilidad local de la ingesta válida, nunca anterior a fetched_at.
- as_of: corte de conocimiento para seleccionar inputs, no sinónimo de report_date.
- computed_at: creación del resultado con su versión de cálculo.

Se conservará evidencia de cambios del calendario sin corregir fechas históricas
en el mismo registro. La evidencia conocida después del corte no se tratará como
conocida antes. Un timestamp programado no demuestra publicación efectiva.

No se inferirá disponibilidad histórica de un backfill. Si no hay publicación
acreditada, la política conservadora usa disponibilidad local para investigación
point-in-time. Las revisiones se seleccionarán por fecha de conocimiento y número
de revisión; A → B → A son tres revisiones, no dos hashes únicos globales.

## Completitud: pendiente de decisión, sin fórmula activa

La decisión del usuario exige definir una política, pero no fija un mínimo exacto.
Se ha preguntado antes de codificar una regla que cambiaría el histórico.

Propuesta sometida a decisión:

- Ventana de W semanas calendario, no «últimas W filas» extendidas hacia atrás.
- Exigir todos los informes esperados dentro de la ventana.
- Excluir de la expectativa únicamente una semana sin informe acreditada oficialmente.
- Exigir además ceil(0,95 × W) observaciones válidas: 50/149 para 52/156 semanas.
- Un festivo que retrasa la publicación no elimina por sí solo la observación.
- Un informe retrasado sigue ausente para ese corte hasta recibirlo; no se rellena.
- Sin evidencia de excepción, 50 de 52 no cumplen la propuesta por el mero hecho
  de superar el mínimo. Dos semanas oficialmente sin informe sí podrían cumplirla.
- No interpolar, arrastrar valores, convertir ausencias en cero o cambiar de ventana.

La [CFTC distingue fecha del informe y fecha de publicación](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm).
También ha [publicado informes acumulados tras interrupciones](https://www.cftc.gov/PressRoom/PressReleases/9147-25):
un retraso no demuestra una semana de posiciones definitivamente omitida.

Los resultados de 52w y 156w se calcularán y persistirán por separado. Net/OI es la
base acordada. Percentil empírico con empates, COT Index, ventanas 13/260 adicionales
y transformación score tendrán una especificación de cálculo completa antes de
escribir resultados. Cambiar fórmula o cobertura requerirá otra versión y no
sobrescribir históricos. Las métricas de las pruebas actuales son fixtures de
persistencia, no implementaciones de esas fórmulas.

## AVAILABLE / EXPOSED / USED

AVAILABLE: snapshot/observaciones locales identificadas en el corte al crear la
predicción. Una captura posterior no puede inventarse como contexto contemporáneo.

EXPOSED: esquema de capacidades enviado en la ejecución, más entregas concretas de
datos cuando las hubo. Exponer una herramienta no demuestra que se haya consultado.

USED: invocación registrada en esa cadena y resultado entregado en una petición
previa a la respuesta que formuló el pronóstico. No afirma influencia causal ni
que todos los contenidos de ese resultado fueran relevantes para el instrumento.
Se conservan argumentos, resultados, errores y secuencias para afinar las cohortes.

Una llamada y registrar_prediccion dentro de la misma respuesta del modelo no
permiten marcar esa llamada como información leída para esa predicción. Los triggers
rechazan esa asociación aunque se ejecutaran primero las herramientas de consulta.
Tampoco permiten unir llamadas de otra ejecución. Una predicción admite varias
invocaciones. Cuando una traza sea incompleta, ausencia de enlaces no equivale a
`used=false`; las vistas futuras deberán devolver desconocido o trace_status.

`consultas_biblioteca` se mantiene sin cambios. Su patrón actual es un log de consulta,
no una relación ya existente con predicciones. `tool_invocations.legacy_reference_json`
permite enlazar una consulta futura a ese registro sin duplicar ni inventar sus
asociaciones pasadas. Fase 5 incorporará la captura real; hoy las tablas están vacías.

Los metadatos y referencias a hilos de las predicciones permitirán estratificar por
narrativa, activo, horizonte, período, motor y herramientas. No se implanta A/B ni
se asegura causalidad: consulta seleccionada por el agente y clusters de narrativas
impiden interpretar un Brier antes/después como efecto causal sin controles.

## Crecimiento previsto antes del backfill

Medición del esquema vacío en SQLite: 188.416 bytes (184 KiB), sin datos de mercado.
La base real puede reutilizar páginas libres, por lo que su incremento físico no
tiene por qué coincidir exactamente con esa cifra.

Ejemplo de capacidad, NO lista de instrumentos habilitados: 16 contratos Legacy
con 3 participantes y los 6 participantes Disaggregated/TFF actuales sumarían
54 series. Diez años (~520 informes) darían unas 11.440 filas brutas y 28.080
observaciones por participante; hasta otras 28.080 filas de métricas por versión.
Si se incorporan más participantes de las familias actuales, escalará por su número.

La procedencia exacta de todas las ventanas domina el tamaño: hasta 260 referencias
por snapshot. Un fixture con 100 snapshots y 26.000 referencias añadió 4.661.248
bytes, unos 46,6 KB por snapshot completo. Con calentamiento y el universo ilustrativo,
prever aproximadamente **0,8–1,5 GB** para bruto, métricas e índices con lineage explícita,
antes de revisiones adicionales y futuras versiones de cálculo. Es una estimación
de capacidad sobre fixtures, no una descarga ni tamaño observado de CFTC.

No crear nuevos snapshots por cada lectura de API: reutilizar resultados idénticos
por inputs y versión. Las revisiones, otras versiones y nuevas series sí añaden datos.
Antes de Fase 3/backfill conviene dimensionar copias con este volumen; se podrá evaluar
una representación compacta de manifests si hace falta reducir espacio, preservando
IDs exactos y versionando cualquier cambio de esquema. No se ha cargado histórico.

## Verificación de Fase 1

Pruebas offline en `tests/test_positioning_foundation.py`, con fixture del esquema
real de herramientas del baseline. Cubren identidad/familias, universo de precios,
NULL, versiones, migraciones transaccionales/idempotentes, revisiones A/B/A,
referencias temporales, sintéticos, separación de ejecuciones y múltiples consultas.

La prueba del flag captura la petición efectiva al SDK simulado del bucle del agente,
compara las herramientas completas con el fixture y exige que no aparezca
consultar_posicionamiento. Comprueba además continuidad del resumen COT y sistema.
Los tests previos siguen comprobando las huellas de prompts, constantes, argumentos
del proveedor, schedules, observabilidad y Validation v2. No se actualizó ni debilitó
`tests/observability_contracts.json`.

Ejecución: `python -m unittest discover -s tests -q`.
Resultado final de Fase 1: **93 tests pasan: 69 existentes y 24 nuevos**.
No dependen de CFTC, Binance, Yahoo ni servicios IA. Los tests de parsing/cálculo,
ingesta, reintentos, API/UI y captura efectiva se añadirán con sus fases respectivas.
