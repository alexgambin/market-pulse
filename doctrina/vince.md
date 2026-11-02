# Doctrina: matemática del riesgo (destilada de Vince, *The Mathematics of Money Management*)

Lente central: toda decisión repetida es una apuesta con distribución, y lo que crece una cuenta no es la expectativa aritmética sino la **media geométrica** de los retornos — el factor de crecimiento por jugada. La varianza la erosiona: dos apuestas con la misma expectativa aritmética no valen lo mismo si una es más volátil, y una media geométrica menor que 1 es un sistema perdedor por mucho que gane "de media".

## Optimal f y la curva de un solo pico
Para cualquier ventaja existe una fracción óptima de riesgo (f) que maximiza el crecimiento compuesto. La curva tiene un solo pico y castiga ambos lados — pero el derecho mata: **por encima del óptimo, más riesgo produce menos retorno con drawdowns desproporcionados**. Corolarios para el análisis: (1) el apalancamiento agregado del mercado también tiene su f — cuando el posicionamiento está pasado de rosca, las correcciones son no lineales (puertas estrechas); (2) operar la ventaja correcta al tamaño incorrecto sigue siendo ruina.

## Riesgo de ruina y el peor caso
Con apuestas repetidas, la ruina depende del tamaño relativo y de la ventaja; rachas de pérdidas perfectamente normales aniquilan tamaños excesivos. Y la trampa de Vince: **la mayor pérdida histórica es una estimación, no un límite** — el peor caso observado no es el peor caso posible. Distingue siempre en los escenarios el riesgo *de varianza* (recuperable, camino doloroso) del riesgo *de ruina* (irreversible, no lineal: gaps, cierres de mercado, liquidez evaporada).

## La aritmética convexa del drawdown
Perder 50% exige ganar 100% para volver: el drawdown es convexo y el tiempo dentro de él, largo. Protegerse cuesta siempre menos que recuperarse. En escenarios, pondera el *camino* además del destino.

## Aplicación del agente (en cada análisis)
1. Expresa los escenarios como distribución: ramas con probabilidades que sumen 1, no una sola dirección.
2. Etiqueta cada riesgo: ¿varianza o ruina? Lo irreversible pesa más que su probabilidad.
3. Evalúa asimetría payoff×probabilidad, no solo dirección: una rama improbable con payoff convexo puede dominar el análisis.
4. Recuerda que la dependencia entre eventos (rachas, contagio entre hilos) multiplica el riesgo: los escenarios no son independientes cuando comparten catalizador.
