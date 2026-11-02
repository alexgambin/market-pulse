"""Conexiones aisladas para la nueva capa; ninguna dependencia de IA."""
from contextlib import contextmanager
import sqlite3


@contextmanager
def connection(path):
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def initialize(path):
    """Migración aditiva; no descarga ni recalcula ni enlaza predicciones."""
    from migrations import migrate_positioning
    with connection(path) as con:
        migrate_positioning(con)
