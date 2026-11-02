"""
fuentes.py — Ingesta RSS de Market Pulse v2.

Autotest — comprueba qué fuentes responden desde tu máquina:
    python fuentes.py
"""
import html
import re
import socket
from datetime import datetime

import feedparser

FUENTES = {
    "OilPrice":    "https://oilprice.com/rss/main",
    "ZeroHedge":   "https://feeds.feedburner.com/zerohedge/feed",
    "CNBC":        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "FXStreet":    "https://www.fxstreet.com/rss/news",
    "Investing":   "https://www.investing.com/rss/news.rss",
    "Fed":         "https://www.federalreserve.gov/feeds/press_all.xml",
    "EIA":         "https://www.eia.gov/rss/todayinenergy.xml",
    "BBC":         "https://feeds.bbci.co.uk/news/business/rss.xml",
    "SCMP":        "https://www.scmp.com/rss/91/feed",
    "CoinDesk":    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Expansion":   "https://e00-expansion.uecdn.es/rss/mercados.xml",
    "GN-Crudo":          "https://news.google.com/rss/search?q=(WTI+OR+Brent+OR+OPEC+OR+%22crude+oil%22)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-Reuters-Energia": "https://news.google.com/rss/search?q=site:reuters.com+(oil+OR+energy+OR+OPEC)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-Bloomberg-Macro": "https://news.google.com/rss/search?q=site:bloomberg.com+(Fed+OR+inflation+OR+%22central+bank%22+OR+yen)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-BoJ-Yen":        "https://news.google.com/rss/search?q=(%22Bank+of+Japan%22+OR+yen+OR+%22Japan+intervention%22)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-Oro":            "https://news.google.com/rss/search?q=(gold+OR+bullion)+(price+OR+%22central+bank%22+OR+ETF+OR+%22real+yields%22)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-BCE-Euro":       "https://news.google.com/rss/search?q=(%22European+Central+Bank%22+OR+ECB+OR+euro+OR+%22bund+spread%22)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-Bitcoin":        "https://news.google.com/rss/search?q=(Bitcoin+OR+BTC)+(ETF+OR+price+OR+SEC+OR+halving+OR+%22spot+ETF%22)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "GN-Ethereum":       "https://news.google.com/rss/search?q=(Ethereum+OR+ETH)+(ETF+OR+price+OR+staking+OR+upgrade)+when:1d&hl=en-US&gl=US&ceid=US:en",
    "BCE":               "https://www.ecb.europa.eu/rss/press.html",
}

TIERS = {
    "Fed": 1, "EIA": 1, "BCE": 1,
    "GN-Crudo": 1, "GN-Reuters-Energia": 1, "GN-Bloomberg-Macro": 1, "GN-BoJ-Yen": 1,
    "GN-Oro": 1, "GN-BCE-Euro": 1,
    "GN-Bitcoin": 2, "GN-Ethereum": 2,
    "OilPrice": 2, "CNBC": 2, "MarketWatch": 2, "FXStreet": 2, "Investing": 2,
    "BBC": 2, "SCMP": 2, "Expansion": 2,
    "ZeroHedge": 3, "CoinDesk": 3,
}

_EFIMERAS = re.compile(
    r"^\s*(WATCH(\s+LIVE)?\s*:|LIVE\s*:|VIDEO\s*:|EN\s+(VIVO|DIRECTO)\s*:?|"
    r"SIGUE\s+EN\s+DIRECTO|STREAMING\s*:)", re.IGNORECASE)

MAX_POR_FUENTE = 15
MAX_RESUMEN = 500
TIMEOUT_SEGUNDOS = 15

_TAGS = re.compile(r"<[^>]+>")


def _limpiar(texto: str) -> str:
    limpio = html.unescape(_TAGS.sub(" ", texto or "")).replace("\xa0", " ")
    return re.sub(r"\s+", " ", limpio).strip()


def _publicado(entrada) -> str | None:
    t = entrada.get("published_parsed") or entrada.get("updated_parsed")
    if not t:
        return None
    return datetime(*t[:6]).isoformat(timespec="seconds")


def recolectar() -> tuple[list[dict], dict]:
    socket.setdefaulttimeout(TIMEOUT_SEGUNDOS)
    noticias, salud = [], {}

    for nombre, url in FUENTES.items():
        try:
            feed = feedparser.parse(url)
            if feed.bozo and not feed.entries:
                raise RuntimeError(str(feed.bozo_exception))
            n = 0
            for e in feed.entries[:MAX_POR_FUENTE]:
                titular = _limpiar(e.get("title", ""))
                enlace = e.get("link")
                if not titular or not enlace:
                    continue
                if _EFIMERAS.search(titular):
                    continue
                noticias.append(
                    {
                        "fuente": nombre,
                        "tier": TIERS.get(nombre, 2),
                        "titular": titular,
                        "resumen": _limpiar(e.get("summary", ""))[:MAX_RESUMEN],
                        "url": enlace,
                        "publicado": _publicado(e),
                    }
                )
                n += 1
            salud[nombre] = {"ok": n > 0, "n": n, "error": None if n else "feed vacío"}
        except Exception as exc:
            salud[nombre] = {"ok": False, "n": 0, "error": str(exc)[:200]}

    return noticias, salud


if __name__ == "__main__":
    print("Probando fuentes…\n")
    items, estado = recolectar()
    ancho = max(len(k) for k in FUENTES)
    for nombre, s in estado.items():
        marca = "OK " if s["ok"] else "FALLO"
        detalle = f'{s["n"]:>3} entradas' if s["ok"] else s["error"]
        print(f"  {nombre:<{ancho}}  {marca}  {detalle}")
    print(f"\nTotal recogido: {len(items)} noticias")
