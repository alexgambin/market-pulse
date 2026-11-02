"""Ámbito financiero conservador y claves de identidad, sin red ni IA.

Solo la pregunta decide la relevancia financiera; una mención a Bitcoin en la
descripción o una etiqueta Finance no convierte un suceso ajeno en financiero.
Las exclusiones explícitas también inspeccionan etiquetas y título del evento.
"""
import re
import unicodedata


def normalized_question(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", text).strip().rstrip("?.! ")


def _labels(value):
    if isinstance(value, list):
        return " ".join(_labels(item) for item in value)
    if isinstance(value, dict):
        return " ".join(str(value.get(k) or "") for k in ("label", "slug", "title", "name"))
    return str(value or "")


def financial_category(market):
    """Devuelve Finance/Crypto o None (fuera de ámbito / no reconocido)."""
    question = normalized_question(market.get("question"))
    metadata = " ".join(_labels(market.get(k)) for k in ("tags", "events", "category", "slug"))
    text = question + " " + metadata.casefold().replace("-", " ")
    excluded = (
        r"\b(elon|musk|tweet\w*|twitter|tiktok|youtube|followers?|retweets?|"
        r"sports?|nba|nfl|nhl|mlb|soccer|football|basketball|baseball|tennis|"
        r"ufc|boxing|cricket|esports?|playoffs?|moneyline|touchdowns?|"
        r"premier league|champions league|world cup|formula 1|f1|grand prix|"
        r"laliga|bundesliga|serie a|ligue 1|wimbledon|pga|golf|"
        r"oscars?|grammys?|emmys?|box office|celebrity)\b"
    )
    if not question or re.search(excluded, text):
        return None
    # No confundir predicciones sobre menciones/discursos con el valor del activo.
    if re.search(r"\b(say|says|mention\w*|post|posts|posting|announce\w*|endorse\w*)\b", question):
        return None
    # Decisiones de política monetaria y magnitudes macro observables.
    macro = r"\b(inflation|cpi|pce|gdp|unemployment|nonfarm payrolls|non-farm payrolls|recession|tariffs?)\b"
    central_bank = r"\b(fed|fomc|federal reserve|ecb|bce|boj|boe|bank of japan|bank of england|european central bank)\b"
    rates = r"\b(rates?|cut|cuts|hike|hikes|bps|basis points?)\b"
    measurement = r"\b(above|below|over|under|between|reach|hit|exceed|rise|fall|increase|decrease|be|report|reports|print|enter|impose)\b"
    if (re.search(macro, question) and re.search(measurement, question)) or (
        re.search(central_bank, question) and re.search(rates, question)
    ):
        return "Finance"
    # Precios, capitalización, rendimientos y tipos de cambio, no cualquier
    # acontecimiento que mencione una empresa o criptomoneda.
    price_action = r"\b(above|below|over|under|between|reach|hit|dip|exceed|close|trade|trading|price|prices|yield|yields|market cap)\b"
    if not re.search(price_action, question):
        return None
    crypto = r"\b(bitcoin|btc|ethereum|eth|solana|sol|xrp|dogecoin|doge|crypto)\b"
    if re.search(crypto, question):
        return "Crypto"
    assets = (
        r"\b(wti|brent|crude oil|oil|natural gas|gold|silver|copper|"
        r"s&p\s*500|nasdaq|dow jones|russell\s*2000|vix|dax|ftse|nikkei|"
        r"treasur\w*|bond|bonds|eur|usd|jpy|gbp|chf|aud|cad|nzd|"
        r"euro|yen|pound|dollar|dxy|stock|stocks|shares|"
        r"aapl|apple|msft|microsoft|nvda|nvidia|amzn|amazon|"
        r"googl|alphabet|meta|tsla|tesla)\b"
    )
    return "Finance" if re.search(assets, question) else None


def identity_keys(market):
    """Un mercado/evento se opera una sola vez por simulación, incluso cerrado.

    Se conservan todos los IDs de evento recibidos. Sin evento, siguen vigentes
    market_id, conditionId y pregunta normalizada; no inventamos un event_id.
    """
    market_id = str(market.get("id") or "").strip()
    question = normalized_question(market.get("question"))
    if not market_id or not question:
        return []
    keys = {("market", market_id), ("question", question)}
    condition = str(market.get("conditionId") or "").strip().casefold()
    if condition:
        keys.add(("condition", condition))
    events = market.get("events") or []
    if isinstance(events, list):
        for event in events:
            if isinstance(event, dict) and event.get("id") is not None:
                event_id = str(event["id"]).strip()
                if event_id:
                    keys.add(("event", event_id))
    if market.get("eventId") is not None:
        event_id = str(market["eventId"]).strip()
        if event_id:
            keys.add(("event", event_id))
    return sorted(keys)
