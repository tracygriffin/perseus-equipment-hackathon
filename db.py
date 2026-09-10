"""Read-only SQLite access and result caching for the dealer dashboard."""

import functools
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "perseus_equipment_database.db"

# Cached KPI results live for an hour; the underlying file is a static dump.
CACHE_TTL_SECONDS = 3600

_local = threading.local()
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    """Return this thread's read-only connection, opening it on first use."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(f"Database not found at {DB_PATH}")
        uri = f"file:{DB_PATH.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a SELECT and return rows as plain dicts."""
    cur = connect().execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def query_one(sql: str, params: tuple = ()) -> dict:
    rows = query(sql, params)
    return rows[0] if rows else {}


def cached(fn):
    """Memoize a KPI function on its arguments for CACHE_TTL_SECONDS."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        key = f"{fn.__name__}:{args!r}:{sorted(kwargs.items())!r}"
        now = time.time()
        with _cache_lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < CACHE_TTL_SECONDS:
                return hit[1]
        result = fn(*args, **kwargs)
        with _cache_lock:
            _cache[key] = (now, result)
        return result

    return wrapper


def cache_size() -> int:
    with _cache_lock:
        return len(_cache)
