# Polymarket: simulación financiera

Solo paper trading, con capital inicial de 10.000 USD y escaneo cada 15 minutos.
No envía órdenes ni utiliza fondos reales. El reset no cambia el scheduler.

## Ámbito

`polymarket_policy.financial_category` exige una pregunta sobre precios de activos
reconocidos, rendimientos, divisas, magnitudes macro o decisiones de tipos.
Deportes, Elon Musk, publicaciones en redes y entretenimiento se excluyen antes
de evaluar la estrategia, aunque lleven etiquetas financieras o mencionen BTC.
Guerras, elecciones y otros sucesos no entran solo por su posible efecto en los
mercados. Las etiquetas/descripciones no bastan para acreditar relevancia.

Es una lista conservadora de patrones deterministas: puede rechazar preguntas
financieras no reconocidas. Las ampliaciones deben acompañarse de fixtures.
No se usa IA. Los umbrales Becker/Kelly se conservan: Finance tiene gap 0,17,
inferior al mínimo 1,5, y actualmente queda descartado por la estrategia; Crypto
puede entrar si cumple los demás filtros. No se han rebajado umbrales para llenar
la cartera tras el reset. Se siguen consultando los 80 mercados activos con más
volumen; el filtrado puede dejar el escaneo sin candidatos financieros.

## Identidad y concurrencia

`pm_trade_keys` reserva market ID, condition ID, pregunta normalizada y todos los
event IDs entregados por el proveedor. Una sola operación por identidad durante
la simulación, incluso si ya cerró. Así se rechazan también distintos strikes del
mismo evento. Si faltan event IDs no se inventan: siguen protegiendo las otras
claves, pero no se puede demostrar que dos preguntas distintas son del mismo
evento. Las reservas y la apertura se escriben en una transacción `BEGIN IMMEDIATE`,
con clave primaria `(kind, identity)`; los límites de exposición y concentración
se comprueban dentro de esa misma transacción.

La migración es idempotente y reserva las identidades conocidas de operaciones
anteriores sin borrar su historial. El reset vacía también las reservas. Un lock
serializa escaneos manuales/automáticos en el mismo proceso; SQLite protege las
escrituras entre procesos. Los cierres actualizan saldo y posición juntos y solo
si la posición sigue abierta, evitando contabilizar el PnL dos veces.

## Reset explícito

`polymarket.reset_paper(backup_path)` exige una ruta de backup nueva. Hace una copia
SQLite consistente de la base completa bajo bloqueo de escritura, y después
vacía únicamente `pm_trades`, `pm_trade_keys`, `pm_senales_fund` y `pm_estado`.
Reinicia saldo/máximo a 10.000, borra el último escaneo y registra `ultimo_reset`.
Conserva predicciones, auditoría, COT, noticias, memoria y consumo IA.

Cada reset cambia una generación persistida en `pm_estado`. Las respuestas de
escaneos anteriores no pueden abrir operaciones ni modificar el saldo después
del reset. El puente comprueba transaccionalmente que el trade aún existe antes
de registrar contexto, y no duplica su señal. El reset es mantenimiento local,
no un nuevo endpoint de borrado. Las copias `.db` quedan excluidas de Git.

La API de lectura conserva el contrato del dashboard y añade `scope` y
`ultimo_reset`. `ultimo_escaneo` indica un intento, no certifica éxito del proveedor.
El escáner devuelve el número de nuevas operaciones y registra errores técnicos.

## Verificación

`python -m unittest discover -s tests -p test_polymarket.py -q`

Fixtures HTTP y puente simulado, SQLite temporal: filtros, IDs normalizados,
eventos, concurrencia, límites, reset/backup, aislamiento de predicciones,
respuestas tardías, idempotencia, errores del proveedor y API. Sin llamadas reales
a Polymarket, Yahoo o proveedores IA. La suite general comprueba además el
contrato de observabilidad y Validation Engine.
