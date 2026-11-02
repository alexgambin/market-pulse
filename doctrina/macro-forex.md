# Doctrina de macro y divisas

Lente operativa para leer forex y macro. Anclada en tres manuales de la biblioteca
del agente: Blanchard *Macroeconomia* (7a ed., economia abierta, caps. 17-21), Romer
*Advanced Macroeconomics* (5a ed., politica monetaria e inflacion, caps. 11-13) y
Mas-Colell *Microeconomic Theory* (equilibrio y decision). Consulta la biblioteca
(consultar_biblioteca) cuando necesites el desarrollo formal; esto es el destilado
para aplicar en cada pasada.

## La condicion que gobierna las divisas: paridad descubierta de tipos

Blanchard (cap. 17) la deriva como una relacion de ARBITRAJE: un inversor elige
entre un bono nacional y uno extranjero, y solo mantiene ambos si ofrecen el mismo
rendimiento esperado EN SU MONEDA. De ahi la condicion de paridad descubierta:

  (1 + i) = (1 + i*) . (E_t / E_{t+1}^e)

En palabras operativas: si el tipo de interes de EEUU es, digamos, un 3% mas bajo
que el del Reino Unido, es porque el mercado espera que el dolar se apreciar ~3% frente
a la libra -- esa apreciacion esperada es lo que compensa al inversor por aceptar el
tipo mas bajo. La divisa de tipo alto tiende a estar "cara" porque el mercado ya
descuenta su depreciacion futura.

Blanchard subraya que la condicion falla por dos fricciones reales que a ti te
importan: COSTES DE TRANSACCION y, sobre todo, RIESGO (el tipo de cambio futuro es
incierto). Esas fricciones son exactamente lo que abre la puerta al carry.

Lectura para tu libro: cuando un par se mueve, descomponlo en (a) cambio del
diferencial de tipos, (b) cambio de las expectativas de tipo futuro -- que hara cada
banco central --, y (c) prima de riesgo. Un movimiento sin cambio en (a) ni (b) es
prima de riesgo pura (huida a refugio o apetito), y suele revertir.

## El carry trade y por que el yen se dispara de golpe

La paridad descubierta falla sistematicamente a corto/medio plazo: las divisas de tipo
alto rinden de mas en lugar de depreciarse. Eso es el CARRY TRADE -- financiarse en
divisa de tipo bajo (yen, franco suizo) e invertir en divisa de tipo alto. El yen es la
divisa de financiacion clasica por los tipos ultrabajos de Japon (dominancia fiscal:
deuda tan alta que el BoJ no puede subir tipos sin quebrar las cuentas del Estado --
Romer, cap. 13 sobre politica fiscal y sostenibilidad de la deuda).

Consecuencia operativa: el carry es una POSICION CORTA DE VOLATILIDAD. Funciona
mientras el tipo de cambio este tranquilo; cuando salta el riesgo (VIX arriba), los que
llevan carry cierran a la vez, recompran yen, y USD/JPY cae rapido y violento. Un
desarme de carry no es gradual. Si ves el VIX saltar y USD/JPY cayendo fuerte, no busques
una noticia sobre Japon: es el carry deshaciendose.

## El modelo Mundell-Fleming (Blanchard, cap. 19-20)

Extiende IS-LM a la economia abierta y liga produccion, tipo de interes y tipo de
cambio. Su nucleo operativo:

- Politica monetaria restrictiva (subir tipos) -> entra capital buscando el tipo mas
alto -> APRECIA la divisa -> enfria exportaciones y produccion. Expansiva: lo
contrario, divisa debil.
- Politica fiscal expansiva (mas deficit) -> presiona los tipos al alza -> APRECIA
la divisa a corto plazo. Contraintuitivo pero es lo que predice el modelo, y Blanchard
senala que empiricamente "supera la prueba con brillantez". La salvedad: si el deficit
amenaza la sostenibilidad, el mercado exige prima de riesgo soberano y la divisa puede
girar a la baja. La bisagra es la credibilidad fiscal.

El TRILEMA (la trinidad imposible): no se pueden tener a la vez tipo de cambio fijo,
libre movimiento de capital y politica monetaria autonoma. EEUU y la zona euro eligen
capital libre + autonomia -> flotacion. Japon y Suiza intervienen cuando la flotacion
les hace dano: por eso una intervencion verbal o real del BoJ o el SNB es un riesgo de
cola REAL en USD/JPY y USD/CHF, no una amenaza vacia.

## La regla del banco central (Romer, cap. 11-12)

El banco central no fija un tipo al azar: sigue una FUNCION DE REACCION tipo regla de
Taylor -- sube tipos cuando la inflacion supera el objetivo o la produccion esta por
encima del potencial, los baja en el caso contrario. Dos implicaciones que usaras cada
dia:

1. Lo que mueve la divisa es la SORPRESA respecto a la trayectoria esperada, no el dato
en si. El mercado ya cotiza la senda de tipos que espera; un dato de inflacion solo
mueve el par si reescribe esa senda. Lee cada dato macro como "esto adelanta o retrasa
el proximo movimiento del banco central respecto a lo que estaba descontado?".

2. Importan las EXPECTATIVAS de inflacion, no solo la realizada (Romer: las expectativas
entran via su efecto sobre el tipo nominal y sobre la fijacion de precios). Por eso el
breakeven de inflacion es un dato de primer orden, no un adorno.

Romer documenta que los errores de politica vienen a menudo de MEDIR MAL el output
potencial en tiempo real (su lectura de la Gran Inflacion de los 70): cuando el banco
central cree que hay mas holgura de la que hay, se queda corto de tipos e infla. Leccion:
desconfia de las estimaciones de brecha de produccion en tiempo real.

## El oro dentro del marco macro

El oro no renta, asi que su precio es casi todo coste de oportunidad y refugio.
- Tipo real a 10 anos (el driver mas fiable): cuando el tipo real baja, tener oro -- que
no paga cupon -- cuesta menos frente a un bono; el oro sube. Un oro subiendo CON tipos
reales al alza es una anomalia que exige explicacion (refugio o compras de bancos
centrales dominando).
- El dolar: el oro cotiza en dolares; dolar debil -> mas barato para el resto del mundo
-> sube. Separa siempre "fuerza del dolar" (DXY) de "historia propia del oro".
- Refugio y bancos centrales: demanda estructural (diversificacion fuera del dolar, suelo
lento) y tactica (miedo geopolitico, pico que se desinfla).

## Disciplina analitica (Mas-Colell)

- Margen y equilibrio. Un precio se mueve hasta que oferta y demanda se vacian al nuevo
nivel. Pregunta siempre quien es el comprador y el vendedor marginal a este precio, y que
los haria cambiar de idea.
- Solo la sorpresa mueve el precio. El mercado ya cotiza lo conocido; lee cada dato contra
el consenso, nunca en absoluto. Un dato "malo" mejor de lo temido hace subir el activo.
- Nivel vs tendencia. Un dato puntual malo (nivel) se absorbe; un cambio de trayectoria
(la inflacion deja de bajar) reescribe toda la curva de tipos esperada y mueve mucho mas.
El segundo pesa mas que el primero.
- Cuidado con los "no deberia mover". Hay efectos que la teoria cancela (equivalencia
ricardiana: mas deficit hoy = mas impuestos manana). En la practica los mercados
reaccionan igual a corto plazo por fricciones y horizonte. No descartes un movimiento solo
porque "en teoria se compensa".
