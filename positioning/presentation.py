"""Proyección de las seis series COT existentes para una vista de solo lectura.

No activa el nuevo motor, escribe snapshots, altera cot.resumen ni calcula scores.
La caché de descargas sigue siendo cot.historico; el último válido de esta vista
se conserva sólo en memoria y no equivale a persistencia point-in-time.
"""
import asyncio
from datetime import date, datetime, timezone
import math

import cot


_markets = {
    "WTI": ("WTI", "ENERGY", "WTI", "Futuros de crudo WTI"),
    "Oro": ("Oro", "METALS", "Oro", "Futuros COMEX; no cotización spot"),
    "Plata": ("Plata", "METALS", "Plata", "Futuros COMEX; no cotización spot"),
    "Yen (6J vs USD)": ("USD/JPY", "FX", "Yen · 6J/USD", "Posiciones en yen frente a USD. La orientación es inversa al precio USD/JPY; el neto mostrado no se invierte."),
    "Euro (6E vs USD)": ("EUR/USD", "FX", "Euro · 6E/USD", "Posiciones en euro frente a USD"),
    "Bitcoin (CME)": ("BTC", "CRYPTO", "Bitcoin · CME", "Futuros CME; distintos del spot y de los perpetuos Binance"),
}
_last_good = {}
_loading = asyncio.Lock()


def _history(points, today):
    if not points:
        raise ValueError("SOURCE_UNAVAILABLE")
    by_date = {}
    for point in points:
        day, net = point.get("fecha"), point.get("neto")
        if not isinstance(day, str) or len(day) != 10:
            raise ValueError("INVALID_DATA")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            raise ValueError("INVALID_DATA") from None
        if parsed > today or isinstance(net, bool) or not isinstance(net, (int, float)):
            raise ValueError("INVALID_DATA")
        if not math.isfinite(net) or int(net) != net:
            raise ValueError("INVALID_DATA")
        if day in by_date and by_date[day] != net:
            raise ValueError("CONFLICTING_DUPLICATES")
        by_date[day] = int(net)
    return [{"date": day, "net": value} for day, value in sorted(by_date.items())]


def describe(name, points, *, now=None):
    now = now or datetime.now(timezone.utc)
    instrument, category, label, note = _markets[name]
    dataset, code, prefix, _ = cot.CONTRATOS[name]
    family = {"72hh-3qpy": "DISAGGREGATED", "gpe5-46if": "TFF"}[dataset]
    participant = {"m_money": "MANAGED_MONEY", "lev_money": "LEVERAGED_FUNDS"}[prefix]
    result = {
        "key": name, "instrument": instrument, "label": label, "category": category,
        "source": "CFTC", "dataset_id": dataset, "market_code": code,
        "source_url": f"https://publicreporting.cftc.gov/resource/{dataset}.json?$where=cftc_contract_market_code='{code}'&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=52",
        "report_type": family, "report_scope": "FUTURES_ONLY",
        "participant_category": participant, "data_kind": "OBSERVED_CFTC",
        "unit": "CONTRACTS", "note": note, "published_at": None, "fetched_at": None,
        "status": "UNAVAILABLE", "error": None, "history": [], "report_date": None,
        "net": None, "weekly_change": None, "previous_report_date": None,
        "gap_days": None, "age_days": None, "age_warning": False,
    }
    try:
        history = _history(points, now.astimezone(timezone.utc).date())
    except (ValueError, TypeError, AttributeError) as exc:
        result["error"] = str(exc) if isinstance(exc, ValueError) else "INVALID_DATA"
        return result
    latest = history[-1]
    gap = (date.fromisoformat(latest["date"]) - date.fromisoformat(history[-2]["date"])).days if len(history)>1 else None
    age = (now.astimezone(timezone.utc).date()-date.fromisoformat(latest["date"])).days
    result.update(status="AVAILABLE", history=history, report_date=latest["date"],
                  net=latest["net"], gap_days=gap, age_days=age, age_warning=age>14,
                  previous_report_date=history[-2]["date"] if len(history)>1 else None,
                  weekly_change=latest["net"]-history[-2]["net"] if gap==7 else None)
    return result


async def overview():
    # Serializa peticiones simultáneas de la vista: la segunda reutiliza la caché
    # ya poblada de cot.historico, sin repetir las seis descargas iniciales.
    async with _loading:
        now = datetime.now(timezone.utc)
        names = list(_markets)
        responses = await asyncio.gather(*(cot.historico(name) for name in names), return_exceptions=True)
        items = []
        for name, points in zip(names, responses):
            item = describe(name, [] if isinstance(points, BaseException) else points, now=now)
            previous = _last_good.get(name)
            if previous and item["report_date"] and item["report_date"] < previous[-1]["fecha"]:
                item.update(status="UNAVAILABLE", error="REPORT_REGRESSION")
            if item["status"] == "AVAILABLE":
                _last_good[name] = [{"fecha": p["date"], "neto": p["net"]} for p in item["history"]]
            elif previous:
                error = item["error"]
                item = describe(name, previous, now=now)
                item.update(status="LAST_VALID", error=error)
            items.append(item)
        return {"api_version": "cot-overview-v1", "consulted_at": now.isoformat(), "items": items,
                "history_limit": 52, "age_warning_days": 14,
                "freshness_policy": "Report age only; no certification against the CFTC release calendar",
                "history_provenance": "existing_adapter_memory_cache; publication/download timestamps unavailable"}
