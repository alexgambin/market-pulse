"""
historias.py — ¿Son la misma noticia? Agrupación de historias entre fuentes.

Reimplementación propia (en Python, sin dependencias) de la idea de identidad
léxica de historias: vectores de rasgos hasheados — tokens, bigramas de orden
y 4-gramas de caracteres, con refuerzo para entidades y números — comparados
por coseno. Tolerante a ediciones de titular (sufijos de fuente, recortes,
morfología), no semántico: una paráfrasis completa entre idiomas no se fusiona.

Uso: agrupar(noticias) → lista de grupos con representante y corroboración.
Autotest: python historias.py
"""
import math
import re
import unicodedata

DIM = 512
UMBRAL = 0.60          # conservador: fusiona lo seguro, jamás inventa corroboración
PESO_TOKEN = 2.0
PESO_BIGRAMA = 1.5
PESO_CHARGRAMA = 1.0
REFUERZO_ENTIDAD = 2.0  # capitalizadas pesan más, sin dominar el vector
REFUERZO_NUMERO = 2.0   # las cifras separan historias ("2 muertos" vs "9 muertos")

_PALABRA = re.compile(r"[A-Za-zÀ-ÿ0-9]+")


def _fnv(texto: str) -> int:
    h = 2166136261
    for b in texto.encode("utf-8"):
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


def _normalizar(palabra: str) -> str:
    return (
        unicodedata.normalize("NFKD", palabra)
        .encode("ascii", "ignore")
        .decode()
        .lower()
    )


def vector(titulo: str) -> dict:
    """Vector disperso L2-normalizado del titular."""
    crudas = _PALABRA.findall(titulo or "")
    tokens = []
    v: dict[int, float] = {}

    def _add(rasgo: str, peso: float):
        h = _fnv(rasgo)
        signo = 1.0 if (h & 0x80000000) == 0 else -1.0
        v[h % DIM] = v.get(h % DIM, 0.0) + signo * peso

    for cruda in crudas:
        tk = _normalizar(cruda)
        if not tk:
            continue
        refuerzo = 1.0
        if any(c.isdigit() for c in tk):
            refuerzo = REFUERZO_NUMERO
        elif cruda[:1].isupper() and len(tk) > 3:
            refuerzo = REFUERZO_ENTIDAD
        _add("t:" + tk, PESO_TOKEN * refuerzo)
        if len(tk) >= 5:
            for i in range(len(tk) - 3):
                _add("c:" + tk[i : i + 4], PESO_CHARGRAMA)
        tokens.append(tk)

    for a, b in zip(tokens, tokens[1:]):
        _add("b:" + a + "_" + b, PESO_BIGRAMA)

    norma = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {k: x / norma for k, x in v.items()}


def similitud(v1: dict, v2: dict) -> float:
    if len(v2) < len(v1):
        v1, v2 = v2, v1
    return sum(x * v2.get(k, 0.0) for k, x in v1.items())


def agrupar(noticias: list[dict]) -> list[dict]:
    """
    Agrupa noticias que cuentan la misma historia.
    Devuelve: [{"rep": noticia, "ids": [...], "n": int, "fuentes": [...], "tier": int}]
    El representante es la versión de mejor fuente (tier más bajo) y resumen más largo.
    """
    grupos: list[dict] = []
    for n in noticias:
        v = vector(n.get("titular", ""))
        mejor, mejor_sim = None, UMBRAL
        for g in grupos:
            s = similitud(v, g["v"])
            if s >= mejor_sim:
                mejor, mejor_sim = g, s
        if mejor is None:
            grupos.append(
                {"v": v, "rep": n, "ids": [n["id"]], "fuentes": [n.get("fuente", "?")],
                 "tier": n.get("tier", 2)}
            )
        else:
            mejor["ids"].append(n["id"])
            if n.get("fuente") not in mejor["fuentes"]:
                mejor["fuentes"].append(n.get("fuente", "?"))
            mejor["tier"] = min(mejor["tier"], n.get("tier", 2))
            actual, cand = mejor["rep"], n
            if (cand.get("tier", 2), -len(cand.get("resumen", ""))) < (
                actual.get("tier", 2), -len(actual.get("resumen", ""))
            ):
                mejor["rep"] = cand
    for g in grupos:
        g["n"] = len(g["ids"])
        del g["v"]
    return grupos


if __name__ == "__main__":
    pruebas = [
        {"id": 1, "tier": 2, "fuente": "A", "resumen": "x" * 50,
         "titular": "Oil prices surge as Houthis declare naval blockade of Saudi Arabia"},
        {"id": 2, "tier": 1, "fuente": "B", "resumen": "x" * 200,
         "titular": "Houthis declare Saudi Arabia naval blockade; oil prices surge"},
        {"id": 3, "tier": 2, "fuente": "C", "resumen": "x" * 80,
         "titular": "Fed's Warsh signals inflation fight remains priority in testimony"},
        {"id": 4, "tier": 3, "fuente": "D", "resumen": "x" * 30,
         "titular": "Oil surges after Houthi naval blockade declaration against Saudis"},
    ]
    for g in agrupar(pruebas):
        print(f"x{g['n']} (tier {g['tier']}, fuentes {g['fuentes']}): {g['rep']['titular'][:60]}")
