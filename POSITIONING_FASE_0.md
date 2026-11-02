# POSITIONING — Auditoría y propuesta de FASE 0

> Registro histórico de propuestas. Las decisiones posteriores del usuario en
> [POSITIONING_DECISIONES.md](POSITIONING_DECISIONES.md) prevalecen, especialmente
> el universo limitado a precios actuales y la herramienta del agente desactivada.
> Implementación y estado por fases: [POSITIONING.md](POSITIONING.md).

Estado: propuesta pendiente de decisiones del usuario; no se ha implementado.
Fuente de verdad: Market Pulse del workspace, commit bdbf958, con sus cambios
actuales. La carpeta cot-dashboard-main ya estaba presente al comenzar; sus cinco
archivos coinciden byte a byte con el ZIP proporcionado en Downloads.

Se han leído ambos proyectos, el esquema SQLite real, consumidores de COT,
derivados, herramientas del agente, migraciones, observabilidad y frontend.
La batería actual pasa: 69 pruebas offline. No se han modificado código, datos,
scheduler, prompts, Validation Engine ni configuración de Telegram en esta fase.

## 1. Qué contiene realmente el ZIP

- Backend Node/Express, puerto 3001, node-fetch y CORS.
- GET /api/cot: Socrata Legacy Futures Only, dataset 6dca-aqww. Filtra por nombre
  textual exacto y devuelve hasta 260 filas, ordenadas cronológicamente.
- Declara 42 instrumentos directos: 8 FX, 5 metales, 4 energía, 5 índices,
  4 tipos/bonos, 12 agrícolas/ganaderos y 4 cripto.
- Declara 25 transformaciones FX: 4 inversiones de una serie y 21 cruces de dos.
- Selecciona long/short de non-commercial, commercial y non-reportable; calcula
  los tres netos y descarta el desglose bruto. No descarga open interest.
- HTML/JavaScript con Chart.js: barras de tres participantes, cambios entre los
  dos últimos informes, buscador, categorías, selección 13/52/104/260 informes,
  cambio de tema y actualización manual.
- Caché sólo en memoria del navegador, sin TTL ni persistencia. La carga del
  sidebar solicita datos para numerosos instrumentos y cruces; las solicitudes
  simultáneas de una misma serie no se deduplican mientras están en vuelo.
- Las etiquetas direccionales dependen de +/-30.000 y +/-80.000 contratos.
- No hay tests, base de datos, versionado de observaciones ni calendario de ingesta.

La descripción de package.json anuncia COT Index y exportación; no he encontrado
implementación de esos cálculos ni de exportación en los archivos del ZIP. Tampoco
hay listeners de teclado para los atajos anunciados. Express sirve una carpeta
public que no viene incluida; index.html está en la raíz. Estas son características
anunciadas o problemas de empaquetado, no funcionalidades que debamos dar por existentes.

Errores relevantes: parseFloat(...) || 0 convierte ausencias/valores inválidos en
cero; los cruces también sustituyen una segunda pata ausente por cero. Restar
contratos de mercados diferentes no controla el tamaño del contrato ni el mercado.
La barra de estado indica la hora del navegador, no prueba actualidad del informe.

El catálogo está declarado en código: falta verificar con datos oficiales actuales
cada código, continuidad y cobertura. Una consulta agregada al endpoint desde la
herramienta web no devolvió un resultado utilizable. No certifico que las 42 series
estén activas ni que todas tengan cinco años de histórico.

## 2. Qué existe en Market Pulse y dónde se solapa

| Pieza | Implementación actual | Consecuencia |
|---|---|---|
| cot.py | Disaggregated 72hh-3qpy: WTI, Oro, Plata; TFF gpe5-46if: yen, euro, Bitcoin CME | Son categorías distintas de Legacy; no se pueden reemplazar silenciosamente |
| cot.resumen() | Neto/delta/OI, seis mercados, caché de texto 24 h | Se incorpora ya al agente, análisis de eventos e informes |
| cot.historico() | Neto de un participante, normalmente 52 informes; caché 24 h | Lo consumen /api/cot, vistas de mercados y motores |
| derivados.datos() | BTCUSDT: funding, OI, histórico de OI y ratios de cuentas global/top | Es la fuente reutilizable de cripto; cada llamada hace peticiones |
| derivados.resumen() | Texto interpretativo, caché 30 min | La caché es del texto, no del dict de datos ni de su historial |
| main.py | Motores cripto/COT y consumidores adicionales | No añadir otra descarga paralela para los mismos consumidores |
| precios.py / velas.py | Claves de precios + alias de presentación | No hay registro general canónico; los alias tolerantes no sirven para validar mappings CFTC |
| SQLite | Predicciones, validación, consumo IA, ejecuciones y resto del producto | No hay tablas de observaciones CFTC/derivados ni sus versiones |
| frontend | / sirve static/mercados.html; static/index.html es otro dashboard | Integrar POSITIONING principalmente en el frontend que realmente sirve / |

Los diccionarios de derivados descartan timestamps y parte de los datos originales.
No bastaría con envolver ese dict y afirmar que conserva provenance completa.
Propongo una extracción mínima compartida que conserve payloads/timestamps antes
 del formateo, manteniendo las funciones y lecturas existentes. No un segundo cliente
Binance. Funding se expresa actualmente suponiendo períodos de 8 h; esa convención
se documentaría, sin extenderla automáticamente a otros instrumentos.

Onchain contiene hashrate, actividad, transacciones y comisiones. Son métricas de
red/fundamentals; no las convertiría en positioning por compartir la sección cripto.

## 3. Arquitectura propuesta

Un paquete Python positioning/, sin otro servidor ni aplicación incrustada:

- instruments.py: registro explícito y versionado de activos, series, alias y relaciones.
- contracts.py: contratos de API, participantes, unidades, errores y versiones.
- cftc.py: adaptador Legacy y validación del payload; reutiliza HTTP/Socrata, no JS.
- engine.py: funciones puras de netos, deltas, ventanas, percentiles, índices y score.
- repository.py: ingestas, revisiones, consultas as_of y snapshots en la SQLite actual.
- service.py: composición para API, herramienta, vistas y actualización programada.
- derivatives.py: adaptación de las lecturas existentes de derivados; sin cliente duplicado.

migrations.py incorporaría una migración independiente e idempotente.
main.py expondría rutas y un job exclusivamente relacionado con POSITIONING.
static/positioning.js y CSS acotado incorporarían una sección nativa a mercados.html.
El detalle conservaría series/participantes/cambios usando la librería gráfica ya
presente cuando cubra la visualización; no hace falta introducir React ni iframe.

La primera versión del score sería CFTC Legacy. Binance tendría métricas con el
mismo sobre de provenance, pero no un score inventado sin histórico comparable.
La extensión a futuros datasets incorpora adaptadores/cohortes, no renombra
non-commercial como managed money o leveraged funds.

Registro mínimo de instrumentos: reutilizar los nombres actuales como etiquetas
compatibles y añadir IDs internos estables, sin cambiar las columnas de predicciones.
Separar asset_id de series_id: dataset + código CFTC + tipo de informe + variante
+ versión de mapping. Los códigos se guardan como texto, incluidos ceros y '+'.

Ejemplos de mappings:

- Gold -> Oro; Crude Oil WTI -> WTI; Bitcoin -> BTC como activo subyacente.
- Futuros CME BTC y perpetuo Binance BTCUSDT conservan series/venue/unidades propias.
- JPY/USD nativo no es USD/JPY: el dato original se conserva y la presentación
  invertida se marca derivada, con orientación explícita.
- NASDAQ 100 no es el Nasdaq Composite de ^IXIC: no crear un alias engañoso.
- T-Note 10Y no es US 10A (^TNX, yield). Un vínculo económico no es equivalencia.
- Brent no se cubre sustituyendo su serie por WTI.
- Consolidated, e-mini y micro no se empalman ni suman automáticamente.

## 4. Esquema de datos propuesto

| Tabla | Contenido y claves esenciales |
|---|---|
| positioning_series | asset_id, source, dataset_id, report_type, market_code, variante, participante/unidad aplicable, orientation, mapping_version, vigencia y relación con precios |
| positioning_ingestions | started_at, finished_at, source, query/filtros, estado técnico, conteos recibidos/aceptados/rechazados, instrumentos, duración, error, checksum y payload crudo |
| positioning_observations | serie, report_date/source_timestamp, first_seen_at/known_at, ingestion_id, revision, content_hash, OI y campos long/short/spreading por cohorte; raw row; UNIQUE(serie, fecha, revisión) |
| positioning_snapshots | contrato JSON inmutable devuelto por el motor, snapshot_at, as_of, engine_version, mapping_version, estado/calidad y hash |
| positioning_snapshot_inputs | snapshot_id -> IDs de revisiones o snapshots de las patas sintéticas; papel de cada input y transformación |
| positioning_exposures | ejecución, llamada/herramienta y snapshot efectivamente entregado al modelo; momento de entrega |
| prediction_positioning | prediccion_id, snapshot_id, relación AVAILABLE_AT_CREATION o EXPOSED_TO_MODEL, asociado_at |

El modelo físico de participantes/métricas puede usar columnas tipadas para Legacy
y payloads estructurados versionados por fuente; evitar una tabla gigantesca de
columnas nulas o un EAV sin unidades/contratos. Los índices servirán serie-fecha,
known_at, ingesta, predicción y snapshot. Las observaciones/snapshots son append-only.

Una descarga idéntica no crea revisiones nuevas; sí puede registrar un intento.
Una corrección posterior crea otra revisión y no sobrescribe la anterior. Si una
corrección vuelve a un valor antiguo, es una nueva revisión: no deduplicar globalmente
sólo por hash. Duplicados contradictorios en la misma descarga se rechazan, sin
escoger arbitrariamente la primera o última fila.

Las fechas de publicación planificadas y reales deben ser campos distintos. La
publicación real queda NULL cuando no esté probada. known_at es cuándo se hizo
visible para Market Pulse, no una fecha retrospectiva asignada al backfill.

## 5. Métricas y propuesta de score v1

Para cada cohorte Legacy: net=long-short, delta frente al informe semanal previo,
percentiles e índice min-max sobre su propio histórico. Conservar también los
componentes originales, open interest y net/OI. No llamar cambio semanal a una
diferencia entre informes separados por varias semanas: delta_1w=NULL con gap
expreso; puede mostrarse adicionalmente delta_since_previous_report con su intervalo.

Ventanas propuestas: 13/52/156/260 informes semanales, etiquetando 156/260 como
aproximadamente 3/5 años. Validar cobertura temporal y secuencia, no seleccionar
13 observaciones muy separadas y llamarlas 13 semanas. Las excepciones se apoyan
en fechas oficiales. No rellenar huecos ni NaN, ni transformar una ventana constante
en neutralidad. Cada métrica lleva valid_count, required_count, window_start/end
y quality_reason. Las ventanas cortas válidas se muestran aunque falten las largas.

Percentil empírico con empates: P=100*(n_menores + 0.5*n_iguales)/n.
La ventana incluye el informe evaluado y nunca informes posteriores. El índice es
100*(actual-min)/(max-min). Si max=min, índice y señal afectada NULL, ZERO_RANGE.
Si no hay suficiente cobertura, NULL, INSUFFICIENT_HISTORY. Los estados son propios
de POSITIONING; no se reutilizan ni alteran estados de validación de predicciones.

Propuesta principal para decidir antes de implementar:

    x_t = LargeSpecsNet_t / OpenInterest_t
    p_t = percentil de x_t en 52 informes válidos
    PositioningScore_t = 2 * (p_t / 100) - 1

Es una medida de posicionamiento relativo respecto al tamaño e histórico del
mercado. No mezcla participantes, ventanas ni variación semanal con pesos arbitrarios.
Los percentiles de neto pedidos se muestran aparte: no confundirlos con el percentil
de net/OI usado por el score. El COT Index tampoco se presenta como un percentil.
Sin OI válido no se sustituye automáticamente por el neto bruto; score=NULL.

Umbrales iniciales propuestos, descriptivos y no optimizados contra rentabilidad:

| Percentil de net/OI 52w | Clasificación |
|---|---|
| p >= 90 | EXTREME_LONG |
| 70 <= p < 90 | LONG |
| 30 < p < 70 | NEUTRAL |
| 10 < p <= 30 | SHORT |
| p <= 10 | EXTREME_SHORT |

El signo mide posición RELATIVA. EXTREME_LONG no significa BUY, previsión alcista,
ni necesariamente neto bruto positivo. Mostrar también net_direction y explicar
esa semántica en UI/API. Commercials pueden reflejar coberturas; no invertir su
neto como señal automática de precio.

Alternativa: usar 156 informes para el score, ganando estabilidad y perdiendo
cobertura en mercados jóvenes. La recomendación inicial es 52 y publicar también
las métricas de 3/5 años. No hacer fallback silencioso de 156 a 52 o 13.

Los rankings MOST CROWDED se calcularían con el mismo score, universo y calidad;
excluir series stale, inválidas, insuficientes y separar sintéticos. Una puntuación
alta identifica un extremo relativo, no prueba crowding peligroso ni capacidad
predictiva. Esto último exige la investigación posterior que se quiere habilitar.

## 6. FX sintético

No reutilizar la diferencia de contratos absolutos del ZIP como métrica principal.
Propuesta: S(A/B)=(S(A/USD)-S(B/USD))/2; inversión USD/A=-S(A/USD).
Ambas patas requieren misma fecha de informe, metodología, ventana válida y
vintages disponibles en el corte as_of. Una pata ausente invalida el resultado;
no usar cero ni mezclar semanas.

Las transformaciones se marcan SYNTHETIC_POSITIONING y referencian las patas,
operación, coeficientes y versión. No tienen net contracts CFTC propios.
Los originales llevan OBSERVED_CFTC; Binance puede llevar OBSERVED_DERIVATIVES.
No añadir una segunda normalización o trasladar umbrales Legacy a los cruces sin
definirlo: propongo publicar el diferencial continuo, classification=NULL y sin
incluirlo en rankings principales hasta acordar una metodología específica.

## 7. Disponibilidad temporal y actualizaciones

La CFTC publica normalmente a las 15:30 America/New_York del viernes sobre
posiciones del martes; hay festivos y retrasos excepcionales. No fijar 21:30 CET
todo el año. Usar el calendario oficial y persistir la versión del calendario.

Propuesta: descarga inicial paginada de 10 años cuando existan, manteniendo las
ventanas de cálculo solicitadas. Sólo 5 años cargados permiten un score 5y actual,
pero no una curva histórica 5y completa desde el principio; 10 años permiten
aproximadamente 5 de evolución con ese calentamiento.

Después: job al horario oficial +15 minutos; reintentos acotados si el nuevo
informe aún no aparece; un chequeo ligero diario de recuperación para retrasos o
reinicios, descargando datos sólo cuando falten o haya revisión identificada.
Reconsultar periódicamente la historia de forma acotada para detectar revisiones,
registrando el momento en que se detectan. La ausencia de un endpoint de versiones
impide garantizar detección instantánea de todas las correcciones históricas.

Si falla una descarga, conservar el último dato bueno y el error del último intento
por separado. UI/API muestran report_date, downloaded_at, last_success,
next_expected_release, age y stale. No escribir una fila vacía que borre el histórico.
No modificar las pasadas, vigilante, validación ni jobs no relacionados.

## 8. Riesgos de look-ahead y snapshots

1. Tratar la foto del martes como conocida antes de su publicación.
2. Suponer viernes normal ante festivos, cierres administrativos o retrasos.
3. Sobrescribir revisiones y usarlas en retrospectiva como si siempre se conocieran.
4. Normalizar una fecha histórica con observaciones futuras o mappings posteriores.
5. Asociar a una predicción un snapshot descargado después de su creación.
6. Combinar patas FX de fechas/vintages incompatibles.
7. Confundir metadatos actuales de descarga con disponibilidad histórica.
8. Empalmar cambios de contrato/clasificación y mantener percentiles comparables.

El backfill descargado hoy sirve para cálculo contextual y exploración retrospectiva;
no demuestra qué conocía Market Pulse hace años. El dataset auditable de conocimiento
local empieza con las ingestas guardadas. Los análisis previos deben etiquetarse
como retrospectivos, salvo evidencia histórica adicional de vintages/publicación.

Para nuevas predicciones propongo dos asociaciones externas diferentes:
AVAILABLE_AT_CREATION (estado local disponible) y EXPOSED_TO_MODEL (JSON realmente
entregado al modelo antes de que formulara la predicción). Esto no demuestra que el
modelo lo utilizara causalmente; evita afirmar una utilización que no se puede probar.

Importa el orden del bucle del agente: si solicita consultar positioning y registrar
una predicción en el MISMO mensaje de tool_use, no había leído aún el resultado de
la consulta. Ese snapshot no se puede marcar como expuesto para esa predicción.
La entrega se anota por iteración de mensajes, no sólo por orden de ejecución de tools.

No rellenar asociaciones históricas fingiendo contexto utilizado ni modificar
ningún campo congelado de predicciones v1/v2. Los enlaces nuevos deberían guardarse
atómicamente con la creación cuando corresponda, sin cambiar su contrato.

## 9. Agente, UI y coste

API propuesta: GET /api/positioning, /api/positioning/{instrument}/history,
/api/positioning/{instrument}, /api/positioning/status; contrato versionado con
source, report_type, data_kind, observed_at, known_at, as_of, quality, métricas,
score_method y snapshot_id. La herramienta consultar_posicionamiento(instrumento)
lee ese mismo servicio local, sin fetch síncrono a CFTC y sin llamar a IA para calcular.

Por compatibilidad propongo mantener el breve bloque COT actual y sumar la herramienta
para detalle; no volcar las 42 series al prompt. Sustituir los bloques existentes
por consulta exclusiva cambia el contexto actual y requiere una decisión explícita.

No se añaden nuevas llamadas autónomas/modelos ni análisis programados. Sin embargo,
una nueva herramienta puede ocasionar otra vuelta del bucle de agente que ya existe:
no se puede prometer mismo número exacto de tokens/llamadas por pasada. Se mantienen
límites y observabilidad, que contabilizará cualquier uso real. Si la condición es
cero vueltas adicionales, hay que resolver ese conflicto antes de exponer la tool.

Frontend principal: nueva entrada POSITIONING en mercados.html, con overview,
FX/equities/energy/metals/rates/crypto y agriculture si se conserva el catálogo.
Mantener tipografía/paleta actuales, detalle por instrumento con barras/series de
participantes, long/short/net, variación y selección temporal. Filtros por fuente,
cohorte y calidad; distinguir observado/sintético, neto/score e histórico/staleness.
El dashboard secundario index.html puede enlazar la sección y consumir su resumen.

## 10. Qué se reutiliza y qué no

Reutilizar del ZIP: inventario inicial a validar, taxonomía, relación de patas FX,
comparación de participantes, navegación/buscador y conceptos de gráficos/cambios.
Traducir los cálculos útiles a Python con tests y unidades explícitas.

No trasladar: servidor Node, API que colisiona con /api/cot actual, caché del
navegador como fuente de verdad, umbrales absolutos, ausencia convertida en cero,
resta de contratos entre mercados, horario CET fijo y nombres duplicados sin IDs.
Conservar el ZIP/carpeta como referencia durante la integración; no borrar funciones
sin inventariarlas y no reconstruir Market Pulse desde esos archivos.

## 11. Preguntas pendientes para cerrar FASE 0

1. ¿Conservar las seis series Disaggregated/TFF actuales, diferenciadas de Legacy?
2. ¿Catálogo completo validado, incluyendo AGRICULTURE y activos sin precio, o universo MP?
3. ¿Score inicial net/OI percentil 52w, la variante 156w, o prefieres neto bruto normalizado?
4. ¿Ventanas estrictas sin fallback y NULL hasta cubrirlas, o métricas provisionales señaladas?
5. ¿Incluir sintéticos como diferencial normalizado sin clasificación/ranking, o posponerlos?
6. ¿Backfill de hasta 10 años para disponer de calentamiento, o limitar carga inicial a 5?
7. ¿Primera capa cripto con BTC/Binance existente, sin ampliar monedas ni crear score cripto?
8. ¿Mantener bloque COT breve + herramienta, aceptando posibles vueltas del bucle existente,
   o exigir reemplazar el bloque por tool, o exigir presupuesto idéntico de llamadas?
9. ¿Guardar ambos contextos externos, disponible y expuesto, para nuevas predicciones?
10. ¿Aceptar calendario oficial + reintentos acotados + chequeo diario de recuperación?

No se empezará la implementación hasta resolver las decisiones relevantes.
En las fases siguientes se informarán archivos, cambios, decisiones, pruebas y
riesgos pendientes. Se cubrirán los 21 grupos de tests solicitados, además de
vintages/revisiones, alineación FX, cohortes, DST y entrega de tools. Se actualizará
únicamente la huella de HERRAMIENTAS/prompt que se apruebe; se conservarán las
protecciones de observabilidad, horarios y límites restantes.

## Fuentes externas contrastadas

- Legacy Futures Only: https://dev.socrata.com/foundry/publicreporting.cftc.gov/6dca-aqww
- Disaggregated Futures Only: https://dev.socrata.com/foundry/publicreporting.cftc.gov/72hh-3qpy
- TFF Futures Only: https://dev.socrata.com/foundry/publicreporting.cftc.gov/gpe5-46if
- Calendario oficial: https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm
- Anuncios/cambios de informes: https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm

Las propuestas de score, esquema, ventanas y política de actualización son diseños
propuestos para Market Pulse, no recomendaciones publicadas por CFTC.
