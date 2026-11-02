"""
polymarket.py — Motor de paper trading de prediction markets (Polymarket).

Portado desde el paper-engine.js de EDGE. Estrategia: actuar como maker capturando
el "Optimism Tax" (framework de Becker) en zonas de precio donde el sesgo es mayor,
con sizing Kelly fraccionario y gestión de riesgo de Vince (cap por trade + filtro
de concentración por tema).

TODO ES PAPER TRADING: nunca toca dinero real ni envía órdenes. Solo simula.

Estado en SQLite (tabla pm_trades + pm_estado), no en journal.json, para encajar
con la arquitectura de Market Pulse. El scanner corre en segundo plano vía APScheduler.

Autotest:  python polymarket.py
"""
import asyncio
import json
import logging
import math
import os
import sqlite3
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone

import httpx
from polymarket_policy import financial_category, identity_keys

DB_PATH = os.environ.get("MP_DB_PATH", "market_pulse.db")
POLY_API = "https://gamma-api.polymarket.com"
logger = logging.getLogger(__name__)
_scan_lock = asyncio.Lock()

# ── Gaps de Becker por categoría (pp de mispricing esperado) ──
BECKER_GAPS = {
    "Sports": 2.23, "Politics": 1.02, "Crypto": 2.69,
    "Finance": 0.17, "Weather": 2.57, "Entertainment": 4.79,
    "Media": 7.28, "World Events": 7.32, "Other": 1.5,
}

# ── Configuración de estrategia (portada del CONFIG de EDGE) ──
CONFIG = {
    "initial_bankroll": 10000,
    "kelly_fraction": 0.20,
    "min_gap": 1.5,
    "min_volume_24h": 5000,
    "max_open_positions": 25,
    "min_edge_pp": 2,
    "spread_cents": 2,
    "longshot_threshold": 25,
    "max_horizon_days": 60,
    "max_exposure_pct": 1.5,
    "max_position_pct": 0.04,
    "blocked_categories": ["Sports", "Media", "Entertainment", "Weather", "Politics", "World Events", "Other"],
    "block_numeric_ranges": True,
    # Filtro de concentración por tema (el que evitó el desastre "Irán")
    "max_per_cluster": 5,
    "theme_clusters": {
        "oriente_medio": ["iran", "iranian", "tehran", "ceasefire", "hormuz", "uranium",
                          "kharg", "khamenei", "irgc", "israel", "gaza", "hamas",
                          "hezbollah", "netanyahu", "idf", "west bank", "middle east"],
        "russia": ["russia", "russian", "ukraine", "putin", "zelensky", "kyiv", "moscow", "donbas"],
        "us_elect": ["trump", "biden", "harris", "election", "gop", "democrat", "republican", "senate", "congress"],
        "china": ["china", "chinese", "taiwan", "xi jinping", "beijing", "pla"],
        "fed": ["fed", "fomc", "powell", "interest rate", "rate cut", "rate hike"],
        "crypto": ["bitcoin", "btc", "ethereum", "eth", "solana", "crypto"],
    },
    "sweet_spots_default": [
        {"min": 88, "max": 97, "bias": "sell_no", "edge_mult": 1.2},
        {"min": 3, "max": 12, "bias": "sell_yes", "edge_mult": 1.2},
    ],
    "sweet_spots_by_category": {
        "Sports": [
            {"min": 75, "max": 87, "bias": "sell_no", "edge_mult": 1.0},
            {"min": 13, "max": 25, "bias": "sell_yes", "edge_mult": 1.0},
        ],
    },
}


# ── ESQUEMA ──────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_trades (
                id TEXT PRIMARY KEY,
                market_id TEXT,
                question TEXT,
                category TEXT,
                cluster TEXT,
                becker_gap REAL,
                direction TEXT,
                entry_price INTEGER,
                yes_price_at_entry INTEGER,
                estimated_edge REAL,
                kelly_fraction REAL,
                position_size REAL,
                shares INTEGER,
                reason TEXT,
                status TEXT,
                opened_at TEXT,
                end_date TEXT,
                closed_at TEXT,
                resolution TEXT,
                pnl REAL,
                pnl_pct REAL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_estado (
                clave TEXT PRIMARY KEY,
                valor TEXT
            )
        """)
        # Reservas de identidad persistentes: el UNIQUE se aplica dentro de la
        # misma transacción que abre el trade, también entre varios procesos.
        c.execute("""CREATE TABLE IF NOT EXISTS pm_trade_keys (
            kind TEXT NOT NULL, identity TEXT NOT NULL, trade_id TEXT NOT NULL,
            PRIMARY KEY (kind, identity)
        )""")
        for tid, market_id, question in c.execute("SELECT id, market_id, question FROM pm_trades").fetchall():
            c.executemany("INSERT OR IGNORE INTO pm_trade_keys VALUES (?,?,?)",
                          [(kind, key, tid) for kind, key in identity_keys({"id": market_id, "question": question})])
        c.execute("INSERT OR IGNORE INTO pm_estado VALUES ('generation', ?)", (uuid4().hex,))
        # bankroll inicial si no existe
        cur = c.execute("SELECT valor FROM pm_estado WHERE clave='bankroll'").fetchone()
        if cur is None:
            c.execute("INSERT INTO pm_estado (clave, valor) VALUES ('bankroll', ?)",
                      (str(CONFIG["initial_bankroll"]),))
            c.execute("INSERT INTO pm_estado (clave, valor) VALUES ('peak_bankroll', ?)",
                      (str(CONFIG["initial_bankroll"]),))


def _get_estado(clave, defecto=None):
    with sqlite3.connect(DB_PATH) as c:
        r = c.execute("SELECT valor FROM pm_estado WHERE clave=?", (clave,)).fetchone()
    return r[0] if r else defecto


def _set_estado(clave, valor):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("INSERT OR REPLACE INTO pm_estado (clave, valor) VALUES (?, ?)", (clave, str(valor)))


def _bankroll():
    return float(_get_estado("bankroll", CONFIG["initial_bankroll"]))


# ── ÁMBITO FINANCIERO ──
def classify_category(market):
    return financial_category(market) or "Other"


def theme_of(question):
    """Cluster temático de un mercado (para el filtro de concentración)."""
    q = (question or "").lower()
    for cluster, keywords in CONFIG["theme_clusters"].items():
        if any(kw in q for kw in keywords):
            return cluster
    return None


def _has_numeric_range(question):
    import re
    q = (question or "").lower()
    if re.search(r"[<>]\s*\d+\s+(tweet|post|point|goal|run|hit|view|like|follower)", q):
        return True
    if re.search(r"between\s+\d+\s+and\s+\d+", q):
        return True
    return False


def _market_end_date(market):
    for campo in ("endDate", "end_date_iso", "endDateIso"):
        if market.get(campo):
            return market[campo]
    return None


# ── EVALUACIÓN (portado fiel del evaluateMarket) ──
def evaluate_market(market):
    cat = financial_category(market)
    keys = identity_keys(market)
    if cat is None or not keys or market.get("closed") or market.get("active") is False:
        return None
    prices_raw = market.get("outcomePrices")
    if not prices_raw:
        return None
    try:
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(prices, list) or len(prices) != 2:
        return None
    try:
        outcomes = market.get("outcomes", ["Yes", "No"])
        outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
        labels = [str(item).casefold() for item in outcomes]
        if sorted(labels) != ["no", "yes"]:
            return None
        values = [float(value) for value in prices]
        vol24 = float(market.get("volume24hr") or 0)
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in values):
            return None
        if not math.isfinite(vol24):
            return None
        yes_price = round(values[labels.index("yes")] * 100)
    except (ValueError, TypeError, OverflowError):
        return None
    no_price = 100 - yes_price
    gap = BECKER_GAPS.get(cat, 1.5)

    # Filtros
    if cat in CONFIG["blocked_categories"]:
        return None
    if CONFIG["block_numeric_ranges"] and _has_numeric_range(market.get("question")):
        return None
    if gap < CONFIG["min_gap"]:
        return None
    if vol24 < CONFIG["min_volume_24h"]:
        return None
    if yes_price <= 1 or yes_price >= 99:
        return None

    end_str = _market_end_date(market)
    try:
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        days_until = (end - datetime.now(timezone.utc)).total_seconds() / 86400
        if not 0 < days_until <= CONFIG["max_horizon_days"]:
            return None
    except (ValueError, AttributeError, TypeError):
        return None

    spots = CONFIG["sweet_spots_by_category"].get(cat, CONFIG["sweet_spots_default"])
    spot = next((s for s in spots if s["min"] <= yes_price <= s["max"]), None)
    if not spot:
        return None

    estimated_edge = gap * spot["edge_mult"]
    thr = CONFIG["longshot_threshold"]
    if yes_price <= thr:
        estimated_edge += (thr - yes_price) * 0.1
    if yes_price >= 100 - thr:
        estimated_edge += (yes_price - (100 - thr)) * 0.1
    if estimated_edge < CONFIG["min_edge_pp"]:
        return None

    # Dirección (Becker: NO rinde más en niveles altos)
    if spot["bias"] == "sell_yes":
        direction, entry_price = "NO", no_price + CONFIG["spread_cents"]
        reason = f"Maker vendiendo YES a {yes_price}¢ (compra NO a {entry_price}¢). {cat} gap {gap}pp. Zona longshot."
    elif spot["bias"] == "sell_no":
        direction, entry_price = "YES", yes_price - CONFIG["spread_cents"]
        reason = f"Maker vendiendo NO a {no_price}¢ (compra YES a {entry_price}¢). {cat} gap {gap}pp."
    else:
        direction, entry_price = "NO", no_price
        reason = f"Zona neutral, NO por defecto (asimetría Becker). {cat} gap {gap}pp."

    entry_price = max(1, min(99, entry_price))

    # Sizing Kelly fraccionario
    implied = entry_price / 100
    true_prob = max(0.02, min(0.98, implied + estimated_edge / 100))
    b = (1 / implied) - 1
    kelly = max(0, (true_prob * b - (1 - true_prob)) / b) if b > 0 else 0
    q_kelly = kelly * CONFIG["kelly_fraction"]

    return {
        "market_id": str(market["id"]).strip(),
        "identity_keys": keys,
        "question": market.get("question"),
        "category": cat,
        "cluster": theme_of(market.get("question")),
        "becker_gap": gap,
        "yes_price": yes_price,
        "direction": direction,
        "entry_price": entry_price,
        "estimated_edge": round(estimated_edge, 2),
        "kelly_fraction": round(q_kelly, 4),
        "reason": reason,
        "end_date": end_str,
    }


# ── ESCANEO Y APERTURA ───────────────────────────────────────
def _same_generation(c, generation):
    row = c.execute("SELECT valor FROM pm_estado WHERE clave='generation'").fetchone()
    return row is not None and row[0] == generation


def _insert_position(signal, generation):
    """Identidad, límites y alta atómicos; ninguna red dentro de la transacción."""
    with sqlite3.connect(DB_PATH, timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        if not _same_generation(c, generation):
            return None
        keys = signal["identity_keys"]
        if any(c.execute("SELECT 1 FROM pm_trade_keys WHERE kind=? AND identity=?", key).fetchone()
               for key in keys):
            return None
        n_open, exposure = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(position_size),0) FROM pm_trades WHERE status='open'"
        ).fetchone()
        if n_open >= CONFIG["max_open_positions"]:
            return None
        cl = signal["cluster"]
        if cl and c.execute("SELECT COUNT(*) FROM pm_trades WHERE status='open' AND cluster=?",
                            (cl,)).fetchone()[0] >= CONFIG["max_per_cluster"]:
            return None
        bankroll = float(c.execute("SELECT valor FROM pm_estado WHERE clave='bankroll'").fetchone()[0])
        size = round(min(bankroll * signal["kelly_fraction"],
                         bankroll * CONFIG["max_position_pct"]), 2)
        if size < 1 or exposure + size > bankroll * CONFIG["max_exposure_pct"]:
            return None
        shares = round(size / (signal["entry_price"] / 100))
        tid = "T" + uuid4().hex
        c.executemany("INSERT INTO pm_trade_keys VALUES (?,?,?)", [(kind, key, tid) for kind, key in keys])
        c.execute(
            "INSERT INTO pm_trades (id, market_id, question, category, cluster, becker_gap, "
            "direction, entry_price, yes_price_at_entry, estimated_edge, kelly_fraction, "
            "position_size, shares, reason, status, opened_at, end_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, signal["market_id"], signal["question"], signal["category"], cl,
             signal["becker_gap"], signal["direction"], signal["entry_price"],
             signal["yes_price"], signal["estimated_edge"], signal["kelly_fraction"],
             size, shares, signal["reason"], "open",
             datetime.now(timezone.utc).isoformat(timespec="seconds"), signal["end_date"]),
        )
        return tid


async def _open_positions(web, generation=None):
    """Escanea y devuelve (nuevas, detalle). No repite mercado, condición o evento."""
    generation = generation or _get_estado("generation")
    r = await web.get(
        f"{POLY_API}/markets",
        params={"limit": 80, "active": "true", "closed": "false",
                "order": "volume24hr", "ascending": "false"},
    )
    if r.status_code != 200:
        return 0, f"HTTP {r.status_code}"
    markets = r.json()
    if not isinstance(markets, list):
        raise ValueError("Respuesta de mercados inválida")
    nuevos = []
    for market in markets:
        if _get_estado("generation") != generation:
            break  # un reset invalida toda la descarga del escaneo anterior
        if not isinstance(market, dict):
            continue
        signal = evaluate_market(market)
        if not signal:
            continue
        tid = _insert_position(signal, generation)
        if not tid:
            continue
        nuevos.append(f"{signal['direction']} {signal['question'][:50]} @ {signal['entry_price']}¢")
        # Puente determinista: solo anota el contexto, sin cambiar el sizing.
        try:
            import puente
            senal = await puente.evaluar(signal["question"], signal["yes_price"], signal["end_date"])
            if senal:
                puente.registrar_senal(tid, signal["market_id"], senal)
        except Exception:
            logger.exception("No se pudo registrar el contexto fundamental de %s", tid)
    return len(nuevos), nuevos


# ── RESOLUCIÓN (portado fiel del checkResolutions) ──
async def _check_resolutions(web, generation=None):
    generation = generation or _get_estado("generation")
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        abiertos = c.execute("SELECT * FROM pm_trades WHERE status='open'").fetchall()

    resolved = 0
    for trade in abiertos:
        try:
            r = await web.get(f"{POLY_API}/markets/{trade['market_id']}")
            if r.status_code != 200:
                continue
            m = r.json()
            if not (m.get("closed") or m.get("resolved")):
                # ¿pasó la fecha de fin?
                end_str = _market_end_date(m) or trade["end_date"]
                if end_str:
                    try:
                        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                        if datetime.now(timezone.utc) <= end:
                            continue
                    except (ValueError, AttributeError):
                        continue
                else:
                    continue

            prices_raw = m.get("outcomePrices")
            if not prices_raw:
                continue
            outcomes = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
            labels = m.get("outcomes", ["Yes", "No"])
            labels = json.loads(labels) if isinstance(labels, str) else labels
            labels = [str(label).casefold() for label in labels]
            if sorted(labels) != ["no", "yes"] or len(outcomes) != 2:
                continue
            yes_price = float(outcomes[labels.index("yes")])
            if not math.isfinite(yes_price) or not 0 <= yes_price <= 1:
                continue

            if yes_price >= 0.95:
                yes_won = True
            elif yes_price <= 0.05:
                yes_won = False
            else:
                yes_won = yes_price > 0.5

            won = yes_won if trade["direction"] == "YES" else not yes_won
            if won:
                pnl = trade["shares"] * ((100 - trade["entry_price"]) / 100)
            else:
                pnl = -(trade["shares"] * (trade["entry_price"] / 100))
            pnl_pct = (pnl / trade["position_size"]) * 100 if trade["position_size"] else 0

            with sqlite3.connect(DB_PATH, timeout=30) as c:
                c.execute("BEGIN IMMEDIATE")
                if not _same_generation(c, generation):
                    continue
                changed = c.execute(
                    "UPDATE pm_trades SET status='closed', closed_at=?, resolution=?, pnl=?, pnl_pct=? "
                    "WHERE id=? AND status='open'",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "WIN" if won else "LOSS", round(pnl, 2), round(pnl_pct, 2), trade["id"]),
                ).rowcount
                if not changed:
                    continue  # otra ejecución ya la resolvió, o el reset la eliminó
                bankroll = float(c.execute("SELECT valor FROM pm_estado WHERE clave='bankroll'").fetchone()[0]) + pnl
                peak = max(float(c.execute("SELECT valor FROM pm_estado WHERE clave='peak_bankroll'").fetchone()[0]), bankroll)
                c.executemany("UPDATE pm_estado SET valor=? WHERE clave=?",
                              [(str(bankroll), "bankroll"), (str(peak), "peak_bankroll")])
            resolved += 1
        except Exception:
            continue
    return resolved


async def escanear():
    """Serializa manual/scheduler; las transacciones protegen además otros procesos."""
    async with _scan_lock:
        init_db()
        generation = _get_estado("generation")
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "market-pulse/1.0"}) as web:
            try:
                resueltas = await _check_resolutions(web, generation)
            except Exception:
                logger.exception("Error resolviendo Polymarket")
                resueltas = 0
            try:
                nuevas, detalle = await _open_positions(web, generation)
            except Exception as e:
                logger.exception("Error descargando/abriendo Polymarket")
                nuevas, detalle = 0, str(e)
        with sqlite3.connect(DB_PATH, timeout=30) as c:
            c.execute("BEGIN IMMEDIATE")
            if not _same_generation(c, generation):
                return {"nuevas": 0, "resueltas": 0, "detalle": "Escaneo descartado por reset"}
            c.execute("INSERT OR REPLACE INTO pm_estado VALUES ('ultimo_escaneo', ?)",
                      (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        logger.info("Polymarket financiero: nuevas=%s resueltas=%s", nuevas, resueltas)
        return {"nuevas": nuevas, "resueltas": resueltas, "detalle": detalle}


def reset_paper(backup_path):
    """Reset explícito de la simulación, con backup SQLite obligatorio y atómico.

    No borra predicciones, validación, noticias, COT ni consumo IA. No se expone
    como endpoint: esta operación de mantenimiento se ejecuta deliberadamente.
    """
    init_db()
    backup = Path(backup_path)
    backup.parent.mkdir(parents=True, exist_ok=True)
    # Nunca sobrescribir una copia anterior o la propia base de datos.
    with backup.open("xb"):
        pass
    with sqlite3.connect(DB_PATH, timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        # Otra conexión lectora permite copiar el estado confirmado mientras
        # esta conexión impide que otros escritores lo cambien antes del reset.
        with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        deleted = {}
        for table in ("pm_senales_fund", "pm_trade_keys", "pm_trades", "pm_estado"):
            if table in tables:
                deleted[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                c.execute(f"DELETE FROM {table}")
        c.executemany("INSERT INTO pm_estado VALUES (?,?)", [
            ("bankroll", str(CONFIG["initial_bankroll"])),
            ("peak_bankroll", str(CONFIG["initial_bankroll"])),
            ("generation", uuid4().hex),
            ("ultimo_reset", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ])
    logger.info("Reset Polymarket: backup=%s eliminados=%s", backup, deleted)
    return {"backup": str(backup.resolve()), "eliminados": deleted,
            "bankroll": CONFIG["initial_bankroll"]}


# ── LECTURA PARA EL DASHBOARD ────────────────────────────────
def resumen():
    """Estado del paper trading para la vista: posiciones, stats, equity."""
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        c.row_factory = sqlite3.Row
        trades = [dict(t) for t in c.execute(
            "SELECT * FROM pm_trades ORDER BY opened_at DESC").fetchall()]

    abiertas = [t for t in trades if t["status"] == "open"]
    cerradas = [t for t in trades if t["status"] == "closed"]
    wins = [t for t in cerradas if t["resolution"] == "WIN"]
    losses = [t for t in cerradas if t["resolution"] == "LOSS"]

    ganancia = sum(t["pnl"] for t in wins if t["pnl"])
    perdida = sum(abs(t["pnl"]) for t in losses if t["pnl"])
    profit_factor = round(ganancia / perdida, 2) if perdida else None
    bankroll = _bankroll()
    peak = float(_get_estado("peak_bankroll", bankroll))
    dd = round((peak - bankroll) / peak * 100, 2) if peak else 0

    # Señales fundamentales del puente (fase B), mapeadas por trade_id
    senales = {}
    try:
        with sqlite3.connect(DB_PATH) as c:
            c.row_factory = sqlite3.Row
            for s in c.execute("SELECT * FROM pm_senales_fund").fetchall():
                senales[s["trade_id"]] = {
                    "prob_fundamentada": s["prob_fundamentada"],
                    "prob_mercado": s["prob_mercado"],
                    "divergencia_pp": s["divergencia_pp"],
                    "senal": s["senal"],
                    "activo": s["activo"],
                }
    except Exception:
        pass
    for t in abiertas:
        t["fund"] = senales.get(t["id"])
    for t in cerradas:
        t["fund"] = senales.get(t["id"])

    return {
        "scope": "FINANCIAL_ONLY",
        "ultimo_reset": _get_estado("ultimo_reset"),
        "bankroll": round(bankroll, 2),
        "pnl_total": round(bankroll - CONFIG["initial_bankroll"], 2),
        "rendimiento_pct": round((bankroll - CONFIG["initial_bankroll"]) / CONFIG["initial_bankroll"] * 100, 2),
        "n_abiertas": len(abiertas),
        "n_cerradas": len(cerradas),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(cerradas) * 100, 1) if cerradas else None,
        "profit_factor": profit_factor,
        "max_drawdown": dd,
        "exposicion": round(sum(t["position_size"] or 0 for t in abiertas), 2),
        "ultimo_escaneo": _get_estado("ultimo_escaneo"),
        "abiertas": abiertas,
        "cerradas": cerradas[:50],
    }


if __name__ == "__main__":
    async def _t():
        print("Inicializando BD…")
        init_db()
        print("Escaneando Polymarket (paper)…")
        res = await escanear()
        print(f"Nuevas: {res['nuevas']}, Resueltas: {res['resueltas']}")
        r = resumen()
        print(f"\nBankroll: ${r['bankroll']} ({r['rendimiento_pct']:+.2f}%)")
        print(f"Abiertas: {r['n_abiertas']}, Cerradas: {r['n_cerradas']}, WR: {r['win_rate']}%")
    asyncio.run(_t())
