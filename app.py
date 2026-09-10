"""FastAPI server for the equipment dealer dashboard.

Run with:  .venv\\Scripts\\python -m uvicorn app:app --port 8000
Then open: http://127.0.0.1:8000
"""

import datetime as dt
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
import queries

STATIC_DIR = Path(__file__).parent / "static"

# Default window: the two years ending at the last invoice in the file.
DEFAULT_MONTHS = 24


def default_range() -> tuple[str, str]:
    bounds = queries.date_bounds()
    last = dt.date.fromisoformat(bounds["max"])
    end = last + dt.timedelta(days=1)
    start = dt.date(end.year - DEFAULT_MONTHS // 12, end.month, 1)
    return start.isoformat(), end.isoformat()


def _warm() -> None:
    """Populate the cache in the background so the first page load is fast."""
    start, end = default_range()
    for fn in (queries.overview, queries.sales, queries.parts,
               queries.service, queries.rentals, queries.customers):
        try:
            fn(start, end)
        except Exception:
            pass
    for fn in (queries.equipment, queries.revenue_by_year):
        try:
            fn()
        except Exception:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="Equipment Dealer Dashboard", lifespan=lifespan)


def _range(start: str | None, end: str | None) -> tuple[str, str]:
    d_start, d_end = default_range()
    return start or d_start, end or d_end


@app.get("/api/meta")
def meta():
    start, end = default_range()
    bounds = queries.date_bounds()
    return {
        "bounds": bounds,
        "default": {"start": start, "end": end},
        "revenue_by_year": queries.revenue_by_year()["years"],
        "cached_entries": db.cache_size(),
    }


@app.get("/api/overview")
def api_overview(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.overview(*_range(start, end))


@app.get("/api/sales")
def api_sales(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.sales(*_range(start, end))


@app.get("/api/parts")
def api_parts(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.parts(*_range(start, end))


@app.get("/api/service")
def api_service(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.service(*_range(start, end))


@app.get("/api/rentals")
def api_rentals(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.rentals(*_range(start, end))


@app.get("/api/customers")
def api_customers(start: str | None = Query(None), end: str | None = Query(None)):
    return queries.customers(*_range(start, end))


@app.get("/api/equipment")
def api_equipment():
    return queries.equipment()


@app.get("/api/drill/department")
def api_drill_department(
    item_type: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    if item_type not in queries.ITEM_TYPE_LABELS:
        raise HTTPException(status_code=404, detail=f"Unknown item type {item_type}")
    return queries.drill_department(item_type, *_range(start, end))


@app.get("/api/drill/sales-kpi")
def api_drill_sales_kpi(
    metric: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    if metric not in queries.SALES_METRICS:
        raise HTTPException(status_code=404, detail=f"Unknown metric {metric}")
    return queries.drill_sales_metric(metric, *_range(start, end))


@app.get("/api/drill/salesperson")
def api_drill_salesperson(
    name: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    return queries.drill_salesperson(name, *_range(start, end))


@app.get("/api/drill/year")
def api_drill_year(year: str):
    if not (year.isdigit() and len(year) == 4):
        raise HTTPException(status_code=400, detail="Year must be four digits")
    return queries.drill_year(year)


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
