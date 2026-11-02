# Medición de IA y Telegram silenciado

Cambio pasivo: conserva modelos, prompts, herramientas, max_tokens, triaje,
selección de noticias, umbrales y todos los horarios existentes. No añade llamadas
IA ni caché. Tampoco modifica el Validation Engine ni los datos de predicciones.

## Activación y consulta

Al arrancar Market Pulse se crean idempotentemente `consumo_ia` y
`ejecuciones_agente` mediante `migrations.migrate_observability`. La migración
se ha aplicado también a la base local existente. El proceso servidor debe cargar
esta versión (recarga automática con `--reload`, o reinicio del servidor habitual).
No se arranca una segunda instancia ni una pasada extra para medir.

La medición continúa mientras funcione esta versión; no se detiene a las 48 horas.
No se rellena consumo histórico a partir de logs ni se generan datos sintéticos.

Consultas JSON, desde la web local:

- `/api/consumo-ia?horas=24`
- `/api/consumo-ia?horas=48`
- `/api/consumo-ia?dia=2026-09-08`

`dia` usa el día civil de Europe/Madrid, incluidos cambios de hora. Los timestamps
almacenados están en UTC. El intervalo es inclusivo al inicio y exclusivo al final.

La respuesta contiene:

- `total`: llamadas, fallos, tokens, caché, coste y contadores de datos desconocidos.
- `grupos`: componente + subcomponente + modelo, coste y porcentaje.
- `ejecuciones`: productividad y coste de cada pasada, incluidos sus triajes;
  coste por hilo actualizado y por predicción creada cuando son calculables.
- `horas_observadas`, `proyeccion_diaria`, `proyeccion_mensual_30d`: extrapolación
  lineal desde la primera llamada registrada dentro de la cobertura disponible.
  Son aproximaciones; una fracción de día no representa todos los horarios.

Los importes son estimaciones **USD**, sin impuestos ni descuentos contractuales.
Si falta una tarifa o usage, `coste_total=NULL`. `coste_conocido` expone la parte
calculable; `llamadas_sin_coste` evita confundirla con el total. Los porcentajes del
coste total y las proyecciones son NULL si hay costes desconocidos; se ofrece por
separado `porcentaje_coste_conocido`. Los sumatorios de tokens usan sólo los
contadores disponibles y `llamadas_sin_usage` señala la cobertura incompleta.

## Atribución

| Componente | Subcomponente / origen |
|---|---|
| `pasada_programada` | `06:45`, `10:00`, `14:00`, `18:00`, `21:45` |
| `pasada_vigilante` | `ruptura`; metadatos con motivo, instrumento y variación |
| `pasada_extra` | `hilo_critico` o `noticias_relevantes` |
| `pasada_manual` | `api` |
| `triaje` | Conserva el subcomponente, origen e ID de la pasada que lo llamó |
| `informe_manana` | `programado` o `manual` |
| `informe_noche` | `programado` o `manual` |
| `informe_ahora` | `convergencia` o `manual` |
| `evento_prevision` | Título del evento |
| `evento_reaccion` | Título del evento |
| `otro` | Llamada directa sin contexto de origen conocido |

Cada llamada recibe su contexto explícitamente. No hay atribución posterior por
hora aproximada o lectura de logs. Los contextos independientes evitan mezclar
llamadas simultáneas de eventos, informes y pasadas.

Las métricas de productividad reutilizan la selección y respuestas de herramientas:
`noticias_consideradas` antes de agrupar, `noticias_candidatas` tras agrupar,
`noticias_relevantes` tras el triaje. Hilos/predicciones cuentan IDs distintos cuyas
herramientas confirmaron éxito; actualizar el mismo hilo dos veces cuenta un hilo.
Una pasada sin noticias también queda registrada, con cero llamadas y coste cero.
Las ejecuciones no finalizadas conservan `fin=NULL`; no se inventa su estado final.

## Usage y tarifas

Los cuatro puntos de llamada a Anthropic están envueltos por
`consumo_ia.llamar`. Devuelve la misma respuesta y propaga la misma excepción que
el SDK, conservando íntegros los argumentos de la llamada. No añade reintentos.
Los reintentos internos del SDK conservan su comportamiento: se registra la
invocación lógica y el usage que devuelve el proveedor; no se inventan contadores
para intentos internos sin usage observable.

Se usa el objeto `usage` del SDK real, y se guarda también como `usage_json`:

- `input_tokens` es la entrada base, excluyendo caché en Anthropic.
- `cache_read_input_tokens` y `cache_creation_input_tokens` son contadores separados.
- `total_tokens` suma entrada base, salida, lecturas y escrituras, sin duplicarlas.
- Ausencia de campos de caché dentro de un usage válido: 0.
- Ausencia de usage: contadores y coste NULL, sin estimación por caracteres.
- Si un error proporciona usage, se conserva; si no, permanece desconocido.

Las tarifas viven sólo en [ai_pricing.py](ai_pricing.py), con versión y fecha,
verificadas el 2026-09-08 en la [tarifa oficial de Anthropic](https://platform.claude.com/docs/en/about-claude/pricing).
Incluyen Sonnet 4.6 y Haiku 4.5, entrada/salida, lectura y escritura de caché de
5 minutos y 1 hora. Se guarda el modelo solicitado y el devuelto. Un modelo
inesperado conserva sus tokens y queda sin coste estimado.

No se aplican estas tarifas estándar/global a tiers/geografías desconocidos o
cargos adicionales de herramientas del servidor. Si falta el desglose de TTL de
una escritura no nula, no se supone una tarifa. Las categorías conocidas de uso
se conservan igualmente. Actualizar tarifas futuras no reescribe tokens ni
costes ya registrados.

`ahorro_cache_estimado` compara el coste de lectura/escritura con el contrafactual
de procesar esos tokens como entrada base. Puede ser negativo al crear caché
antes de reutilizarla; no es un ahorro verificado en la factura. También se expone
el porcentaje de entrada servido desde caché.

## Telegram

`config.TELEGRAM_ENABLED = False` bloquea `informes.enviar_telegram` antes de leer
credenciales, crear el cliente HTTP o enviar mensajes. Todos los envíos existentes
pasan por esa función. No se ha eliminado el transporte: puede reactivarse
explícitamente cambiando la configuración y recargando el proceso.

Los informes siguen generándose, midiendo usage y archivándose en `analisis`.
Eventos, vigilante y recap conservan sus tareas; únicamente el envío externo queda
silenciado. Nunca se imprime el token ni la URL autenticada de Telegram.

## Fallos y pruebas

La persistencia es best effort, con espera corta ante bloqueos SQLite. Una avería
de observabilidad se registra sin interrumpir la lógica principal. Si se pierde
un registro de una pasada, se incrementa `registros_perdidos` y su coste final queda
NULL. Los errores guardan clase y estado HTTP, nunca cuerpos, prompts ni claves.
Los logs `[AI COST]` y `[TELEGRAM] disabled` usan el logger `consumo_ia` (nivel INFO
para ver llamadas exitosas; los fallos de registro son WARNING).

```sh
conda activate mp
python -m unittest discover -s tests -v
```

Las APIs están simuladas: las pruebas no gastan tokens ni envían Telegram.
`tests/observability_contracts.json` contiene huellas del contrato analítico previo
al cambio; la prueba detecta alteraciones de prompts, modelos, herramientas,
parámetros de llamadas, horarios y umbrales. No contiene prompts ni credenciales.
