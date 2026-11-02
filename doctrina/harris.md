# Doctrina: microestructura de mercados (destilada de Harris, *Trading and Exchanges*)

Lente central: todo precio es el resultado de un encuentro entre traders con motivos distintos. Ante cualquier movimiento o noticia, la primera pregunta no es "¿alcista o bajista?" sino "¿quién compra, quién vende, y por qué?".

## El zoo de traders (por qué opera cada uno)
- Utilitarios: operan por razones ajenas al precio futuro (coberturas, flujos comerciales, rebalanceos, necesidad de caja). Pierden en expectativa frente a los informados y lo aceptan: son el subsidio que hace existir al mercado.
- Informados: operan sobre valor (información nueva, análisis). Empujan el precio hacia el valor; su beneficio sale de utilitarios y desinformados.
- Proveedores de liquidez (dealers): venden inmediatez. Su spread compensa dos costes: inventario y, sobre todo, selección adversa — el riesgo de estar comprándole justo a un informado. Spread que se ensancha = miedo a selección adversa.
- Anticipadores de órdenes: no saben nada del valor; predicen el flujo de otros (front-running de flujo previsible, caza de racimos de stops, squeezes de posicionamiento conocido). Su presencia delata dónde hay flujo previsible.
- Bluffers/manipuladores: fabrican señales baratas para mover a los demás.

Implicación: identifica qué tipo domina cada movimiento; la interpretación cambia por completo según quién empuja.

## Jerarquía de credibilidad de las señales
Las señales caras de fabricar (flujo ejecutado, posiciones tomadas, inventario movido, capacidad comprometida) valen más que las baratas (declaraciones, titulares, amenazas, filtraciones). Una intervención verbal sin flujo detrás es señal barata: el mercado la descuenta hasta que cuesta dinero sostenerla. Pondera cada noticia por su coste de emisión.

## Liquidez: cuatro dimensiones y una traición
Inmediatez (cuán rápido), anchura (a qué spread), profundidad (cuánto tamaño), resiliencia (cuán rápido se recompone). La liquidez es procíclica: abunda cuando nadie la necesita y se evapora exactamente cuando todos la buscan — crisis, aperturas tras fin de semana, festivos, horas finas. En liquidez fina, el mismo flujo produce más precio: el contexto exagera el mensaje.

## Volatilidad fundamental vs transitoria
Dos volatilidades que se parecen y no son lo mismo. Fundamental: llega información; el precio salta y SE QUEDA. Transitoria: fricción de flujo — alguien grande cruza un mercado fino; el precio salta y REVIERTE. Test operativo: reversión rápida delata flujo; persistencia delata información. No interpretes como "el mercado opina X" lo que puede ser "alguien necesitaba vender ya".

## Descubrimiento de precios y lo descontado
Los precios agregan la información de los informados. Corolarios: (1) noticia ampliamente conocida = flujo informado ya gastado = sin recorrido sostenido; (2) si el precio no reacciona a una noticia "importante", o ya estaba en precio o el mercado no se la cree — ambas cosas son información; (3) la reacción A la noticia informa más que la noticia. El arbitraje cose los precios de mercados relacionados (WTI-Brent, crudo-productos): una divergencia sostenida entre ellos es información sobre fricciones físicas o fundamentales locales, no ruido.

## Órdenes y sus huellas
Orden de mercado = comprar inmediatez pagando spread e impacto. Orden limitada = ofrecer liquidez regalando una opción (te ejecutan justo cuando el mercado va en tu contra). Stops = liquidez tomada en el peor momento; los racimos de stops previsibles atraen anticipadores. Las órdenes grandes se trocean para esconder la huella: detectar goteo persistente en una dirección es detectar flujo institucional.

## Costes de implementación
Coste real = spread + impacto + oportunidad, y crece con tamaño y urgencia. Todo análisis que ignore el coste de entrar o salir en el momento de liquidez relevante (apertura del domingo, publicación de datos, festivos) sobreestima el edge disponible.

## Extremos: cascadas, burbujas, puertas estrechas
En los extremos manda la microestructura, no los fundamentales: cascadas de liquidez (1987 y el seguro de cartera), circuit breakers, squeezes. Cuando el posicionamiento está apiñado, el riesgo principal no es la noticia: es la puerta estrecha por la que todos querrán salir a la vez.

## Checklist del agente (aplicar en cada pasada)
1. ¿Quién está al otro lado de este movimiento y por qué opera?
2. ¿La señal es cara o barata de fabricar?
3. ¿Salto con persistencia (información) o con reversión (flujo)?
4. ¿Cuánta liquidez había cuando ocurrió? ¿El contexto exagera el mensaje?
5. ¿Qué parte está ya descontada? ¿Es la no-reacción el verdadero dato?
6. ¿Hay posicionamiento apiñado que convierta una chispa en cascada?
