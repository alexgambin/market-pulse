"""
db.py — Capa de datos de Market Pulse v2.

Dos tablas propias (noticias, analisis). Las tablas de memoria del agente
(hilos, noticia_hilo, predicciones) viven en memoria.py y comparten el
mismo archivo SQLite.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

DB_PATH = os.environ.get("MP_DB_PATH", "market_pulse.db")


@contextmanager
def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def _ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS noticias (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            fuente    TEXT NOT NULL,
            titular   TEXT NOT NULL,
            resumen   TEXT DEFAULT '',
            url       TEXT UNIQUE,                -- clave de deduplicación
            publicado TEXT,
            procesada INTEGER NOT NULL DEFAULT 0, -- 0 = pendiente de pasar por el agente
            creado    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS analisis (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            texto      TEXT NOT NULL,
            n_noticias INTEGER NOT NULL DEFAULT 0,
            creado     TEXT NOT NULL
        );
        """)
        try:  # migración: credibilidad de la fuente
            c.execute("ALTER TABLE noticias ADD COLUMN tier INTEGER NOT NULL DEFAULT 2")
        except sqlite3.OperationalError:
            pass


def guardar_noticias(items: list[dict]) -> int:
    """Inserta noticias nuevas (dedup por URL). Devuelve cuántas eran nuevas."""
    nuevas = 0
    with _conn() as c:
        for n in items:
            cur = c.execute(
                "INSERT OR IGNORE INTO noticias (fuente, titular, resumen, url, publicado, tier, creado) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    n.get("fuente", "?"),
                    n.get("titular", "").strip(),
                    n.get("resumen", "").strip(),
                    n.get("url"),
                    n.get("publicado"),
                    int(n.get("tier", 2)),
                    _ahora(),
                ),
            )
            nuevas += cur.rowcount
    return nuevas


def noticias_pendientes(limite: int = 40) -> list[dict]:
    """Noticias aún no procesadas por el agente, más recientes primero."""
    with _conn() as c:
        filas = c.execute(
            "SELECT id, fuente, titular, resumen, tier FROM noticias "
            "WHERE procesada = 0 ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(f) for f in filas]


def marcar_procesadas(ids: list[int]):
    if not ids:
        return
    with _conn() as c:
        c.executemany("UPDATE noticias SET procesada = 1 WHERE id = ?", [(i,) for i in ids])


def guardar_analisis(texto: str, n_noticias: int):
    with _conn() as c:
        c.execute(
            "INSERT INTO analisis (texto, n_noticias, creado) VALUES (?, ?, ?)",
            (texto, n_noticias, _ahora()),
        )


def ultimos_analisis(limite: int = 5) -> list[dict]:
    with _conn() as c:
        filas = c.execute(
            "SELECT id, texto, n_noticias, creado FROM analisis ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(f) for f in filas]


def ultimas_noticias(limite: int = 30) -> list[dict]:
    with _conn() as c:
        filas = c.execute(
            "SELECT id, fuente, titular, url, publicado, procesada, creado "
            "FROM noticias ORDER BY id DESC LIMIT ?",
            (limite,),
        ).fetchall()
    return [dict(f) for f in filas]
