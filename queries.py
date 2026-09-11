"""KPI queries for the equipment dealer dashboard.

Conventions used throughout:

* Revenue is measured from ``InvoiceDetail.NetExt`` on active, finalized
  invoices. Using the line items rather than ``InvoiceHeader.TotalInvoice``
  keeps the headline number and the department breakdown tied to the same
  source. Trade-in lines (``TR``) are negative, so revenue is net of trades.
* Cost is only available for parts (``SalePart.AvgCost``) and whole units
  (``SaleUnit.InvoiceCost``). Every ``AppUser.HourlyRate`` in this dump is
  zero, so labour has no cost basis and is excluded from margin.
"""

from db import cached, query, query_one

# Line-item categories in InvoiceDetail.ItemType.
ITEM_TYPE_LABELS = {
    "PA": "Parts",
    "UN": "Units",
    "SL": "Service labor",
    "RU": "Rental",
    "MC": "Misc charges",
    "TR": "Trade-ins",
    "RE": "Rental returns",
    "QU": "Quoted items",
}

# Applied to every revenue query.
FINALIZED = "h.IsActive = 1 AND h.Status = 'finalized'"
DATE_RANGE = "h.ActivityDate >= ? AND h.ActivityDate < ?"


def _pct(part, whole):
    if not whole:
        return 0.0
    return round(100.0 * part / whole, 1)


def _round(value, digits=0):
    return round(value or 0, digits)


@cached
def date_bounds() -> dict:
    """Earliest and latest invoice dates, used to seed the date filter."""
    row = query_one(
        "SELECT MIN(ActivityDate) lo, MAX(ActivityDate) hi "
        "FROM InvoiceHeader WHERE IsActive = 1"
    )
    return {"min": (row.get("lo") or "")[:10], "max": (row.get("hi") or "")[:10]}


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------


@cached
def overview(start: str, end: str) -> dict:
    p = (start, end)

    revenue = query_one(
        f"""SELECT ROUND(SUM(d.NetExt), 2) revenue, COUNT(DISTINCT h.InvoiceDocId) invoices
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    parts_margin = query_one(
        f"""SELECT ROUND(SUM(sp.NetExt), 2) rev, ROUND(SUM(sp.AvgCost * sp.Qty), 2) cost
            FROM SalePart sp
            JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    unit_margin = query_one(
        f"""SELECT ROUND(SUM(su.NetExt), 2) rev, ROUND(SUM(su.InvoiceCost), 2) cost
            FROM SaleUnit su
            JOIN InvoiceDetail d ON d.ItemId = su.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    costed_rev = (parts_margin.get("rev") or 0) + (unit_margin.get("rev") or 0)
    costed_cost = (parts_margin.get("cost") or 0) + (unit_margin.get("cost") or 0)
    gross_profit = costed_rev - costed_cost

    by_type = query(
        f"""SELECT d.ItemType item_type, ROUND(SUM(d.NetExt), 2) amt
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 HAVING SUM(d.NetExt) <> 0 ORDER BY amt DESC""",
        p,
    )
    type_revenue = {r["item_type"]: r["amt"] for r in by_type}

    trend = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, ROUND(SUM(d.NetExt), 2) revenue,
                   COUNT(DISTINCT h.InvoiceDocId) invoices
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY 1""",
        p,
    )

    customers = query_one(
        f"""SELECT COUNT(DISTINCT h.CustomerId) n FROM InvoiceHeader h
            WHERE {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    open_wo = query_one(
        f"""SELECT COUNT(*) n FROM InvoiceHeader h
            WHERE h.IsActive = 1 AND h.InvoiceType = 'wo'
              AND h.Status IN ('draft', 'committed', 'quote') AND {DATE_RANGE}""",
        p,
    )

    total_rev = revenue.get("revenue") or 0
    invoices = revenue.get("invoices") or 0

    return {
        "kpis": {
            "revenue": _round(total_rev, 2),
            "invoices": invoices,
            "avg_invoice": _round(total_rev / invoices if invoices else 0, 2),
            "gross_profit": _round(gross_profit, 2),
            "margin_pct": _pct(gross_profit, costed_rev),
            "active_customers": customers.get("n") or 0,
            "open_work_orders": open_wo.get("n") or 0,
        },
        "range": {"start": start, "end": end},
        "by_department": [
            {
                "item_type": r["item_type"],
                "label": ITEM_TYPE_LABELS.get(r["item_type"], r["item_type"]),
                "value": r["amt"],
            }
            for r in by_type
        ],
        "trend": trend,
        # Service and rentals appear here for completeness even though this
        # extract records no cost against either: every AppUser.HourlyRate is
        # zero and every RentalUnit depreciation and salvage field is zero.
        "margin_detail": [
            {
                "label": "Parts",
                "revenue": _round(parts_margin.get("rev"), 2),
                "cost": _round(parts_margin.get("cost"), 2),
                "margin_pct": _pct(
                    (parts_margin.get("rev") or 0) - (parts_margin.get("cost") or 0),
                    parts_margin.get("rev") or 0,
                ),
                "basis": "SalePart.AvgCost",
            },
            {
                "label": "Units",
                "revenue": _round(unit_margin.get("rev"), 2),
                "cost": _round(unit_margin.get("cost"), 2),
                "margin_pct": _pct(
                    (unit_margin.get("rev") or 0) - (unit_margin.get("cost") or 0),
                    unit_margin.get("rev") or 0,
                ),
                "basis": "SaleUnit.InvoiceCost",
            },
            {
                "label": "Service labor",
                "revenue": _round(type_revenue.get("SL"), 2),
                "cost": None,
                "margin_pct": None,
                "basis": "No cost recorded (all technician rates are zero)",
            },
            {
                "label": "Rental",
                "revenue": _round(type_revenue.get("RU"), 2),
                "cost": None,
                "margin_pct": None,
                "basis": "No cost recorded (all depreciation fields are zero)",
            },
        ],
    }


@cached
def drill_department(item_type: str, start: str, end: str) -> dict:
    """Detail behind one bar of the overview department chart."""
    p = (item_type, start, end)
    where = f"d.IsActive = 1 AND d.ItemType = ? AND {FINALIZED} AND {DATE_RANGE}"

    totals = query_one(
        f"""SELECT ROUND(SUM(d.NetExt), 2) revenue, COUNT(*) lines,
                   COUNT(DISTINCT h.InvoiceDocId) invoices,
                   COUNT(DISTINCT h.CustomerId) customers
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE {where}""",
        p,
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE {where} GROUP BY 1 ORDER BY 1""",
        p,
    )

    items = query(
        f"""SELECT d.DisplayText item, MAX(d.Description) description,
                   COUNT(*) lines, ROUND(SUM(d.Qty), 0) qty,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE {where} GROUP BY 1 ORDER BY revenue DESC LIMIT 25""",
        p,
    )

    customers = query(
        f"""SELECT h.CustomerName name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE {where} GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 15""",
        p,
    )

    revenue = totals.get("revenue") or 0
    lines = totals.get("lines") or 0

    return {
        "title": ITEM_TYPE_LABELS.get(item_type, item_type),
        "range": {"start": start, "end": end},
        "kpis": {
            "revenue": _round(revenue, 2),
            "lines": lines,
            "invoices": totals.get("invoices") or 0,
            "customers": totals.get("customers") or 0,
            "avg_line": _round(revenue / lines if lines else 0, 2),
        },
        "monthly": monthly,
        "items": items,
        "customers_list": customers,
    }


@cached
def drill_year(year: str) -> dict:
    """Detail behind one bar of the overview revenue-by-year chart."""
    start = f"{int(year)}-01-01"
    end = f"{int(year) + 1}-01-01"
    p = (start, end)

    totals = query_one(
        f"""SELECT ROUND(SUM(d.NetExt), 2) revenue,
                   COUNT(DISTINCT h.InvoiceDocId) invoices,
                   COUNT(DISTINCT h.CustomerId) customers
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, ROUND(SUM(d.NetExt), 2) revenue,
                   COUNT(DISTINCT h.InvoiceDocId) invoices
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY 1""",
        p,
    )

    by_type = query(
        f"""SELECT d.ItemType item_type, ROUND(SUM(d.NetExt), 2) amt
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 HAVING SUM(d.NetExt) <> 0 ORDER BY amt DESC""",
        p,
    )

    customers = query(
        f"""SELECT h.CustomerName name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 15""",
        p,
    )

    salespeople = query(
        f"""SELECT TRIM(h.SalesPersonName) name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
              AND TRIM(h.SalesPersonName) <> ''
            GROUP BY 1 ORDER BY revenue DESC LIMIT 10""",
        p,
    )

    revenue = totals.get("revenue") or 0
    invoices = totals.get("invoices") or 0

    return {
        "title": f"Year {year}",
        "range": {"start": start, "end": end},
        "kpis": {
            "revenue": _round(revenue, 2),
            "invoices": invoices,
            "avg_invoice": _round(revenue / invoices if invoices else 0, 2),
            "customers": totals.get("customers") or 0,
        },
        "monthly": monthly,
        "by_department": [
            {
                "item_type": r["item_type"],
                "label": ITEM_TYPE_LABELS.get(r["item_type"], r["item_type"]),
                "value": r["amt"],
            }
            for r in by_type
        ],
        "customers_list": customers,
        "salespeople": salespeople,
    }


# --------------------------------------------------------------------------
# Sales drill-downs
# --------------------------------------------------------------------------

# Every KPI card on the Sales tab is a slice of one of three record sets. The
# third element narrows the source to exactly the rows the card counted, so a
# drill-down always re-derives the number on the card rather than approximating
# it. Keep these clauses in step with the aggregates in sales().
SALES_METRICS = {
    "units_sold": ("Units sold", "unit", ""),
    "unit_revenue": ("Unit revenue", "unit", ""),
    "unit_margin_pct": ("Unit margin, priced units", "unit", "AND su.NetExt <> 0"),
    "unit_margin_all_pct": ("Unit margin, all units", "unit", ""),
    "zero_priced_units": ("Zero-priced units", "unit", "AND su.NetExt = 0"),
    "avg_unit_price": ("Average unit price", "unit", "AND su.NetExt <> 0"),
    "trade_ins": ("Trade-ins", "trade", ""),
    "trade_value": ("Trade value", "trade", ""),
    "quotes_won": ("Quotes won", "quote", "AND q.QuoteStatus IN ('won', 'salescontract')"),
    "quotes_open": ("Quotes open", "open_quote", ""),
}

# The detail tables are filtered in the browser, so the whole slice is sent at
# once. The largest slice in the full nine years is well inside this cap.
DETAIL_ROW_LIMIT = 6000

_UNIT_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Invoice"},
    {"key": "stock_no", "label": "Stock no"},
    {"key": "description", "label": "Description"},
    {"key": "condition", "label": "Condition"},
    {"key": "customer", "label": "Customer"},
    {"key": "salesperson", "label": "Salesperson"},
    {"key": "price", "label": "Price", "fmt": "moneyFull", "num": True},
    {"key": "cost", "label": "Cost", "fmt": "moneyFull", "num": True},
    {"key": "margin", "label": "Margin", "fmt": "moneyFull", "num": True},
]

_TRADE_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Invoice"},
    {"key": "stock_no", "label": "Stock no"},
    {"key": "description", "label": "Description"},
    {"key": "customer", "label": "Customer"},
    {"key": "salesperson", "label": "Salesperson"},
    {"key": "meter", "label": "Meter", "fmt": "num", "num": True},
    {"key": "retail", "label": "Retail", "fmt": "moneyFull", "num": True},
    {"key": "trade_value", "label": "Trade value", "fmt": "moneyFull", "num": True},
    {"key": "over_allowance", "label": "Over-allowance", "fmt": "moneyFull", "num": True},
]

_QUOTE_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Quote"},
    {"key": "status", "label": "Status"},
    {"key": "customer", "label": "Customer"},
    {"key": "salesperson", "label": "Salesperson"},
    {"key": "confidence", "label": "Confidence", "fmt": "num", "num": True},
    {"key": "closed", "label": "Closed"},
    {"key": "total", "label": "Quote total", "fmt": "moneyFull", "num": True},
]


def _unit_detail(extra: str, p: tuple) -> tuple[list, int, dict, list]:
    join = """FROM SaleUnit su
              JOIN InvoiceDetail d ON d.ItemId = su.ItemId
              JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId"""
    where = f"WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} {extra}"

    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   su.StockNo stock_no, su.Description description,
                   CASE WHEN su.IsNew = 1 THEN 'New' ELSE 'Used' END condition,
                   h.CustomerName customer, TRIM(h.SalesPersonName) salesperson,
                   ROUND(su.NetExt, 2) price, ROUND(su.InvoiceCost, 2) cost,
                   ROUND(su.NetExt - su.InvoiceCost, 2) margin
            {join} {where}
            ORDER BY su.NetExt DESC, h.ActivityDate DESC
            LIMIT {DETAIL_ROW_LIMIT}""",
        p,
    )
    t = query_one(
        f"""SELECT COUNT(*) n, ROUND(SUM(su.NetExt), 2) revenue,
                   ROUND(SUM(su.InvoiceCost), 2) cost
            {join} {where}""",
        p,
    )
    n, rev, cost = t.get("n") or 0, t.get("revenue") or 0, t.get("cost") or 0
    kpis = {
        "units": n,
        "revenue": _round(rev, 2),
        "cost": _round(cost, 2),
        "margin_pct": _pct(rev - cost, rev),
        "avg_price": _round(rev / n if n else 0, 2),
    }
    defs = [
        {"key": "units", "label": "Units", "fmt": "num"},
        {"key": "revenue", "label": "Revenue", "fmt": "moneyFull"},
        {"key": "cost", "label": "Cost", "fmt": "moneyFull"},
        {"key": "margin_pct", "label": "Margin", "fmt": "pct"},
        {"key": "avg_price", "label": "Avg price", "fmt": "moneyFull"},
    ]
    return rows, n, kpis, defs


def _trade_detail(p: tuple) -> tuple[list, int, dict, list]:
    join = """FROM SaleUnitTradeIn t
              JOIN InvoiceDetail d ON d.ItemId = t.ItemId
              JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId"""
    where = f"WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}"

    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   t.StockNo stock_no, t.Description description,
                   h.CustomerName customer, TRIM(h.SalesPersonName) salesperson,
                   ROUND(t.MeterReading, 0) meter, ROUND(t.Retail, 2) retail,
                   ROUND(t.TradeValue, 2) trade_value,
                   ROUND(t.OverAllowance, 2) over_allowance
            {join} {where}
            ORDER BY t.TradeValue DESC LIMIT {DETAIL_ROW_LIMIT}""",
        p,
    )
    t = query_one(
        f"""SELECT COUNT(*) n, ROUND(SUM(t.TradeValue), 2) value,
                   ROUND(SUM(t.OverAllowance), 2) over_allowance
            {join} {where}""",
        p,
    )
    n, value = t.get("n") or 0, t.get("value") or 0
    kpis = {
        "trades": n,
        "value": _round(value, 2),
        "over_allowance": _round(t.get("over_allowance"), 2),
        "avg_value": _round(value / n if n else 0, 2),
    }
    defs = [
        {"key": "trades", "label": "Trade-ins", "fmt": "num"},
        {"key": "value", "label": "Trade value", "fmt": "moneyFull"},
        {"key": "avg_value", "label": "Avg value", "fmt": "moneyFull"},
        {"key": "over_allowance", "label": "Over-allowance", "fmt": "moneyFull"},
    ]
    return rows, n, kpis, defs


def _quote_detail(extra: str, p: tuple, open_only: bool) -> tuple[list, int, dict, list]:
    if open_only:
        # Open quotes never reached QuoteDetails, so they come off the header.
        where = f"WHERE h.IsActive = 1 AND h.Status = 'quote' AND {DATE_RANGE}"
        rows = query(
            f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                       'open' status, h.CustomerName customer,
                       TRIM(h.SalesPersonName) salesperson, NULL confidence,
                       NULL closed, ROUND(h.TotalInvoice, 2) total
                FROM InvoiceHeader h {where}
                ORDER BY h.TotalInvoice DESC LIMIT {DETAIL_ROW_LIMIT}""",
            p,
        )
        t = query_one(
            f"""SELECT COUNT(*) n, ROUND(SUM(h.TotalInvoice), 2) total
                FROM InvoiceHeader h {where}""",
            p,
        )
    else:
        join = "FROM QuoteDetails q JOIN InvoiceHeader h ON h.InvoiceDocId = q.InvoiceDocId"
        where = (f"WHERE h.IsActive = 1 AND q.QuoteStatus <> 'unqualified' {extra} "
                 f"AND {DATE_RANGE}")
        rows = query(
            f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                       q.QuoteStatus status, h.CustomerName customer,
                       TRIM(h.SalesPersonName) salesperson, q.Confidence confidence,
                       substr(q.ClosureDate, 1, 10) closed,
                       ROUND(h.TotalInvoice, 2) total
                {join} {where}
                ORDER BY h.TotalInvoice DESC LIMIT {DETAIL_ROW_LIMIT}""",
            p,
        )
        t = query_one(
            f"""SELECT COUNT(*) n, ROUND(SUM(h.TotalInvoice), 2) total {join} {where}""",
            p,
        )

    n, total = t.get("n") or 0, t.get("total") or 0
    kpis = {
        "quotes": n,
        "total": _round(total, 2),
        "avg_total": _round(total / n if n else 0, 2),
    }
    defs = [
        {"key": "quotes", "label": "Quotes", "fmt": "num"},
        {"key": "total", "label": "Quoted value", "fmt": "moneyFull"},
        {"key": "avg_total", "label": "Avg quote", "fmt": "moneyFull"},
    ]
    return rows, n, kpis, defs


@cached
def drill_sales_metric(metric: str, start: str, end: str) -> dict:
    """Detail behind one KPI card on the Sales tab."""
    label, source, extra = SALES_METRICS[metric]
    p = (start, end)

    if source == "unit":
        rows, total, kpis, defs = _unit_detail(extra, p)
        columns = _UNIT_COLUMNS
    elif source == "trade":
        rows, total, kpis, defs = _trade_detail(p)
        columns = _TRADE_COLUMNS
    else:
        rows, total, kpis, defs = _quote_detail(extra, p, source == "open_quote")
        columns = _QUOTE_COLUMNS

    return {
        "title": label,
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "columns": columns,
        "rows": rows,
        "total_rows": total,
        "truncated": len(rows) < total,
    }


@cached
def drill_salesperson(name: str, start: str, end: str) -> dict:
    """Detail behind one row of the top-salespeople table."""
    p = (name, start, end)
    where = (f"WHERE d.IsActive = 1 AND {FINALIZED} AND TRIM(h.SalesPersonName) = ? "
             f"AND {DATE_RANGE}")
    join = """FROM InvoiceDetail d
              JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId"""

    totals = query_one(
        f"""SELECT ROUND(SUM(d.NetExt), 2) revenue,
                   COUNT(DISTINCT h.InvoiceDocId) invoices,
                   COUNT(DISTINCT h.CustomerId) customers
            {join} {where}""",
        p,
    )
    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, ROUND(SUM(d.NetExt), 2) revenue
            {join} {where} GROUP BY 1 ORDER BY 1""",
        p,
    )
    by_type = query(
        f"""SELECT d.ItemType item_type, ROUND(SUM(d.NetExt), 2) amt
            {join} {where}
            GROUP BY 1 HAVING SUM(d.NetExt) <> 0 ORDER BY amt DESC""",
        p,
    )
    customers = query(
        f"""SELECT h.CustomerName name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            {join} {where} GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 15""",
        p,
    )
    units = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   su.StockNo stock_no, su.Description description,
                   CASE WHEN su.IsNew = 1 THEN 'New' ELSE 'Used' END condition,
                   h.CustomerName customer, TRIM(h.SalesPersonName) salesperson,
                   ROUND(su.NetExt, 2) price, ROUND(su.InvoiceCost, 2) cost,
                   ROUND(su.NetExt - su.InvoiceCost, 2) margin
            FROM SaleUnit su
            JOIN InvoiceDetail d ON d.ItemId = su.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            {where}
            ORDER BY su.NetExt DESC LIMIT {DETAIL_ROW_LIMIT}""",
        p,
    )
    # Counted rather than taken from len(units), which the row cap could clip.
    unit_count = query_one(
        f"""SELECT COUNT(*) n
            FROM SaleUnit su
            JOIN InvoiceDetail d ON d.ItemId = su.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            {where}""",
        p,
    )

    revenue = totals.get("revenue") or 0
    invoices = totals.get("invoices") or 0

    return {
        "title": name,
        "range": {"start": start, "end": end},
        "kpis": {
            "revenue": _round(revenue, 2),
            "invoices": invoices,
            "avg_invoice": _round(revenue / invoices if invoices else 0, 2),
            "customers": totals.get("customers") or 0,
            "units_sold": unit_count.get("n") or 0,
        },
        "monthly": monthly,
        "by_department": [
            {
                "item_type": r["item_type"],
                "label": ITEM_TYPE_LABELS.get(r["item_type"], r["item_type"]),
                "value": r["amt"],
            }
            for r in by_type
        ],
        "customers_list": customers,
        "columns": _UNIT_COLUMNS,
        "rows": units,
        "total_rows": unit_count.get("n") or 0,
        "truncated": len(units) < (unit_count.get("n") or 0),
    }


# --------------------------------------------------------------------------
# Parts drill-downs
# --------------------------------------------------------------------------

# As on the Sales tab, each card is a slice of one source. Note that the three
# catalog and stocking metrics are deliberately not date-filtered, because the
# cards they back count current inventory state rather than activity in the
# selected window.
PARTS_METRICS = {
    "revenue": ("Parts revenue", "line", ""),
    "profit": ("Parts gross profit", "line", ""),
    "margin_pct": ("Parts margin", "line", ""),
    "qty_sold": ("Units shipped", "line", ""),
    "lines": ("Parts line items", "line", ""),
    "active_parts": ("Active catalog", "catalog", ""),
    "never_sold": ("Parts never sold", "catalog",
                   "AND NOT EXISTS (SELECT 1 FROM SalePart sp WHERE sp.PartId = pm.PartId)"),
    "stale_counts": ("Stale counts", "location", ""),
}

_PART_LINE_JOIN = """FROM SalePart sp
                     JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
                     JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
                     LEFT JOIN PartManufacturer m ON m.MfgId = sp.MfgId"""

_PART_LINE_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Invoice"},
    {"key": "part_no", "label": "Part no"},
    {"key": "description", "label": "Description"},
    {"key": "manufacturer", "label": "Manufacturer"},
    {"key": "customer", "label": "Customer"},
    {"key": "qty", "label": "Qty", "fmt": "num1", "num": True},
    {"key": "revenue", "label": "Revenue", "fmt": "moneyFull", "num": True},
    {"key": "cost", "label": "Cost", "fmt": "moneyFull", "num": True},
    {"key": "profit", "label": "Profit", "fmt": "moneyFull", "num": True},
]

_CATALOG_COLUMNS = [
    {"key": "part_no", "label": "Part no"},
    {"key": "description", "label": "Description"},
    {"key": "manufacturer", "label": "Manufacturer"},
    {"key": "added", "label": "Added"},
    {"key": "lifetime_qty", "label": "Lifetime qty", "fmt": "num", "num": True},
    {"key": "lifetime_revenue", "label": "Lifetime revenue", "fmt": "moneyFull", "num": True},
    {"key": "last_sold", "label": "Last sold"},
]

# Every never-sold part has zero lifetime sales by definition, so those columns
# are dropped and the list is ordered oldest-first instead: how long a part has
# sat unsold is the only thing that separates one row from another.
_NEVER_SOLD_COLUMNS = [
    {"key": "part_no", "label": "Part no"},
    {"key": "description", "label": "Description"},
    {"key": "manufacturer", "label": "Manufacturer"},
    {"key": "added", "label": "Added"},
    {"key": "years_on_file", "label": "Years on file", "fmt": "num1", "num": True},
]

_LOCATION_COLUMNS = [
    {"key": "part_no", "label": "Part no"},
    {"key": "description", "label": "Description"},
    {"key": "manufacturer", "label": "Manufacturer"},
    {"key": "bin", "label": "Bin"},
    {"key": "last_count", "label": "Last counted"},
    {"key": "lifetime_qty", "label": "Lifetime qty", "fmt": "num", "num": True},
    {"key": "lifetime_revenue", "label": "Lifetime revenue", "fmt": "moneyFull", "num": True},
]

# Lifetime totals are deliberately taken straight from SalePart with no invoice
# join, so they line up with the "never sold" test, which asks only whether a
# part has ever appeared on a sale line.
_LIFETIME_SALES = """LEFT JOIN (SELECT PartId, SUM(Qty) qty, SUM(NetExt) rev,
                                      MAX(EntDate) last_sold
                               FROM SalePart GROUP BY PartId) s"""


def _part_lines(extra: str, params: tuple) -> tuple[list, int, dict, list]:
    """Parts sale lines plus the aggregate the Parts cards are built from."""
    where = f"WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} {extra}"

    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   sp.PartNo part_no, sp.Description description,
                   m.DisplayText manufacturer, h.CustomerName customer,
                   ROUND(sp.Qty, 1) qty, ROUND(sp.NetExt, 2) revenue,
                   ROUND(sp.AvgCost * sp.Qty, 2) cost,
                   ROUND(sp.NetExt - sp.AvgCost * sp.Qty, 2) profit
            {_PART_LINE_JOIN} {where}
            ORDER BY sp.NetExt DESC LIMIT {DETAIL_ROW_LIMIT}""",
        params,
    )
    t = query_one(
        f"""SELECT COUNT(*) lines, ROUND(SUM(sp.Qty), 0) qty,
                   ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.AvgCost * sp.Qty), 2) cost,
                   COUNT(DISTINCT sp.PartNo) parts,
                   COUNT(DISTINCT h.CustomerId) customers
            {_PART_LINE_JOIN} {where}""",
        params,
    )
    rev, cost = t.get("revenue") or 0, t.get("cost") or 0
    kpis = {
        "revenue": _round(rev, 2),
        "cost": _round(cost, 2),
        "profit": _round(rev - cost, 2),
        "margin_pct": _pct(rev - cost, rev),
        "qty": t.get("qty") or 0,
        "lines": t.get("lines") or 0,
        "parts": t.get("parts") or 0,
        "customers": t.get("customers") or 0,
    }
    defs = [
        {"key": "revenue", "label": "Revenue", "fmt": "moneyFull"},
        {"key": "profit", "label": "Gross profit", "fmt": "moneyFull"},
        {"key": "margin_pct", "label": "Margin", "fmt": "pct"},
        {"key": "qty", "label": "Units shipped", "fmt": "num"},
        {"key": "lines", "label": "Line items", "fmt": "num"},
        {"key": "parts", "label": "Distinct parts", "fmt": "num"},
    ]
    return rows, t.get("lines") or 0, kpis, defs


@cached
def drill_parts_metric(metric: str, start: str, end: str) -> dict:
    """Detail behind one KPI card on the Parts tab."""
    label, source, extra = PARTS_METRICS[metric]

    if source == "line":
        rows, total, kpis, defs = _part_lines(extra, (start, end))
        columns = _PART_LINE_COLUMNS
        note = None
    elif source == "catalog":
        where = f"WHERE pm.IsActive = 1 {extra}"
        if metric == "never_sold":
            rows = query(
                f"""SELECT pm.PartNo part_no, pm.Description description,
                           m.DisplayText manufacturer, substr(pm.EntDate, 1, 10) added,
                           ROUND((julianday('now') - julianday(pm.EntDate)) / 365.25, 1)
                               years_on_file
                    FROM PartMaster pm
                    LEFT JOIN PartManufacturer m ON m.MfgId = pm.MfgId
                    {where} ORDER BY pm.EntDate LIMIT {DETAIL_ROW_LIMIT}"""
            )
            columns = _NEVER_SOLD_COLUMNS
            note = ("The catalog reflects current state, so the date filter does "
                    "not apply. Longest-standing parts are listed first.")
        else:
            rows = query(
                f"""SELECT pm.PartNo part_no, pm.Description description,
                           m.DisplayText manufacturer, substr(pm.EntDate, 1, 10) added,
                           ROUND(COALESCE(s.qty, 0), 0) lifetime_qty,
                           ROUND(COALESCE(s.rev, 0), 2) lifetime_revenue,
                           COALESCE(substr(s.last_sold, 1, 10), 'Never sold') last_sold
                    FROM PartMaster pm
                    LEFT JOIN PartManufacturer m ON m.MfgId = pm.MfgId
                    {_LIFETIME_SALES} ON s.PartId = pm.PartId
                    {where} ORDER BY COALESCE(s.rev, 0) DESC, pm.PartNo
                    LIMIT {DETAIL_ROW_LIMIT}"""
            )
            columns = _CATALOG_COLUMNS
            note = ("The catalog reflects current state, so the date filter does "
                    "not apply. Lifetime figures cover every sale line on record.")
        t = query_one(f"SELECT COUNT(*) n FROM PartMaster pm {where}")
        total = t.get("n") or 0
        kpis = {"parts": total}
        defs = [{"key": "parts", "label": "Parts", "fmt": "num"}]
    else:
        where = ("WHERE pl.IsActive = 1 AND (pl.LastCountDate IS NULL "
                 "OR pl.LastCountDate < date('now', '-365 day'))")
        rows = query(
            f"""SELECT pm.PartNo part_no, pm.Description description,
                       m.DisplayText manufacturer, NULLIF(pl.Bin, '') bin,
                       COALESCE(substr(pl.LastCountDate, 1, 10), 'Never counted') last_count,
                       ROUND(COALESCE(s.qty, 0), 0) lifetime_qty,
                       ROUND(COALESCE(s.rev, 0), 2) lifetime_revenue
                FROM PartLocation pl
                LEFT JOIN PartMaster pm ON pm.PartId = pl.PartId
                LEFT JOIN PartManufacturer m ON m.MfgId = pm.MfgId
                {_LIFETIME_SALES} ON s.PartId = pl.PartId
                {where} ORDER BY COALESCE(s.rev, 0) DESC
                LIMIT {DETAIL_ROW_LIMIT}"""
        )
        t = query_one(f"SELECT COUNT(*) n FROM PartLocation pl {where}")
        total = t.get("n") or 0
        kpis = {"locations": total}
        defs = [{"key": "locations", "label": "Stocking records", "fmt": "num"}]
        columns = _LOCATION_COLUMNS
        note = ("Stocking records reflect current state, so the date filter does "
                "not apply. Highest-selling bins are listed first.")

    return {
        "title": label,
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "columns": columns,
        "rows": rows,
        "total_rows": total,
        "truncated": len(rows) < total,
        "note": note,
    }


def _parts_slice(title: str, extra: str, params: tuple, start: str, end: str) -> dict:
    """Shared shape for the manufacturer and single-part drill-downs."""
    rows, total, kpis, defs = _part_lines(extra, params)
    where = f"WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} {extra}"

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month,
                   ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.NetExt) - SUM(sp.AvgCost * sp.Qty), 2) profit
            {_PART_LINE_JOIN} {where} GROUP BY 1 ORDER BY 1""",
        params,
    )
    top_parts = query(
        f"""SELECT sp.PartNo part_no, MAX(sp.Description) description,
                   ROUND(SUM(sp.Qty), 0) qty, ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.NetExt) - SUM(sp.AvgCost * sp.Qty), 2) profit
            {_PART_LINE_JOIN} {where}
            GROUP BY 1 ORDER BY revenue DESC LIMIT 20""",
        params,
    )
    customers = query(
        f"""SELECT h.CustomerName name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(sp.NetExt), 2) revenue
            {_PART_LINE_JOIN} {where}
            GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 15""",
        params,
    )

    return {
        "title": title,
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "monthly": monthly,
        "top_parts": top_parts,
        "customers_list": customers,
        "columns": _PART_LINE_COLUMNS,
        "rows": rows,
        "total_rows": total,
        "truncated": len(rows) < total,
    }


@cached
def drill_manufacturer(manufacturer: str, start: str, end: str) -> dict:
    return _parts_slice(
        manufacturer, "AND m.DisplayText = ?", (start, end, manufacturer), start, end
    )


@cached
def drill_part(part_no: str, start: str, end: str) -> dict:
    return _parts_slice(
        f"Part {part_no}", "AND sp.PartNo = ?", (start, end, part_no), start, end
    )


@cached
def revenue_by_year() -> dict:
    """Full-history yearly revenue, independent of the date filter."""
    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 4) year, ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED}
            GROUP BY 1 ORDER BY 1"""
    )
    return {"years": rows}


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------


@cached
def sales(start: str, end: str) -> dict:
    p = (start, end)

    # A number of unit lines carry real cost but no price: attachments bundled
    # into a machine deal, or fleet transfers. They drag reported margin below
    # zero, so priced units are measured separately from the raw total.
    units = query_one(
        f"""SELECT COUNT(*) n, ROUND(SUM(su.NetExt), 2) revenue,
                   ROUND(SUM(su.InvoiceCost), 2) cost,
                   SUM(CASE WHEN su.NetExt = 0 THEN 1 ELSE 0 END) zero_priced,
                   COUNT(CASE WHEN su.NetExt <> 0 THEN 1 END) priced_n,
                   ROUND(SUM(CASE WHEN su.NetExt <> 0 THEN su.NetExt END), 2) priced_rev,
                   ROUND(SUM(CASE WHEN su.NetExt <> 0 THEN su.InvoiceCost END), 2) priced_cost
            FROM SaleUnit su
            JOIN InvoiceDetail d ON d.ItemId = su.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    condition_mix = query(
        f"""SELECT CASE WHEN su.IsNew = 1 THEN 'New' ELSE 'Used' END label,
                   COUNT(*) units, ROUND(SUM(su.NetExt), 2) revenue,
                   ROUND(SUM(su.InvoiceCost), 2) cost
            FROM SaleUnit su
            JOIN InvoiceDetail d ON d.ItemId = su.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )

    salespeople = query(
        f"""SELECT TRIM(h.SalesPersonName) name, COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
              AND TRIM(h.SalesPersonName) <> ''
            GROUP BY 1 ORDER BY revenue DESC LIMIT 12""",
        p,
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, h.InvoiceType type,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1, 2 ORDER BY 1""",
        p,
    )

    trades = query_one(
        f"""SELECT COUNT(*) n, ROUND(SUM(t.TradeValue), 2) value,
                   ROUND(SUM(t.OverAllowance), 2) over_allowance
            FROM SaleUnitTradeIn t
            JOIN InvoiceDetail d ON d.ItemId = t.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    # QuoteDetails carries one row per invoice and defaults to 'unqualified',
    # so a raw win rate over every row is meaningless. Only decided outcomes
    # are counted, and the rate is suppressed when nothing was lost.
    quotes = query(
        """SELECT q.QuoteStatus status, COUNT(*) n
           FROM QuoteDetails q
           JOIN InvoiceHeader h ON h.InvoiceDocId = q.InvoiceDocId
           WHERE h.IsActive = 1 AND q.QuoteStatus <> 'unqualified'
             AND h.ActivityDate >= ? AND h.ActivityDate < ?
           GROUP BY 1 ORDER BY n DESC""",
        p,
    )
    won = sum(r["n"] for r in quotes if r["status"] in ("won", "salescontract"))
    lost = sum(r["n"] for r in quotes if r["status"] in ("inactive", "lost"))
    open_quotes = query_one(
        f"""SELECT COUNT(*) n FROM InvoiceHeader h
            WHERE h.IsActive = 1 AND h.Status = 'quote' AND {DATE_RANGE}""",
        p,
    )

    unit_rev = units.get("revenue") or 0
    unit_cost = units.get("cost") or 0
    priced_rev = units.get("priced_rev") or 0
    priced_cost = units.get("priced_cost") or 0
    priced_n = units.get("priced_n") or 0

    return {
        "kpis": {
            "units_sold": units.get("n") or 0,
            "unit_revenue": _round(unit_rev, 2),
            "unit_margin_pct": _pct(priced_rev - priced_cost, priced_rev),
            "unit_margin_all_pct": _pct(unit_rev - unit_cost, unit_rev),
            "zero_priced_units": units.get("zero_priced") or 0,
            "avg_unit_price": _round(priced_rev / priced_n if priced_n else 0, 2),
            "trade_ins": trades.get("n") or 0,
            "trade_value": _round(trades.get("value"), 2),
            "quotes_won": won,
            "quotes_lost": lost,
            "quotes_open": open_quotes.get("n") or 0,
            "quote_win_pct": _pct(won, won + lost) if lost else None,
        },
        "condition_mix": [
            {
                **r,
                "margin_pct": _pct((r["revenue"] or 0) - (r["cost"] or 0), r["revenue"] or 0),
            }
            for r in condition_mix
        ],
        "salespeople": salespeople,
        "monthly_by_type": monthly,
        "quote_status": quotes,
        "note": (
            "Unit margin covers priced units only. Lines with cost but no "
            "price are bundled attachments and fleet transfers; the all-in "
            "figure including them is shown alongside."
        ),
    }


# --------------------------------------------------------------------------
# Parts
# --------------------------------------------------------------------------


@cached
def parts(start: str, end: str) -> dict:
    p = (start, end)

    totals = query_one(
        f"""SELECT COUNT(*) lines, ROUND(SUM(sp.Qty), 0) qty,
                   ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.AvgCost * sp.Qty), 2) cost
            FROM SalePart sp
            JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    top_parts = query(
        f"""SELECT sp.PartNo part_no, MAX(sp.Description) description,
                   ROUND(SUM(sp.Qty), 0) qty, ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.NetExt) - SUM(sp.AvgCost * sp.Qty), 2) profit
            FROM SalePart sp
            JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY revenue DESC LIMIT 20""",
        p,
    )

    by_mfg = query(
        f"""SELECT m.DisplayText manufacturer, ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.AvgCost * sp.Qty), 2) cost
            FROM SalePart sp
            JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            JOIN PartManufacturer m ON m.MfgId = sp.MfgId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month,
                   ROUND(SUM(sp.NetExt), 2) revenue,
                   ROUND(SUM(sp.NetExt) - SUM(sp.AvgCost * sp.Qty), 2) profit
            FROM SalePart sp
            JOIN InvoiceDetail d ON d.ItemId = sp.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY 1""",
        p,
    )

    catalog = query_one(
        """SELECT COUNT(*) active_parts,
                  SUM(CASE WHEN NOT EXISTS
                      (SELECT 1 FROM SalePart sp WHERE sp.PartId = p.PartId)
                      THEN 1 ELSE 0 END) never_sold
           FROM PartMaster p WHERE p.IsActive = 1"""
    )

    stale = query_one(
        """SELECT COUNT(*) total,
                  SUM(CASE WHEN LastCountDate IS NULL OR LastCountDate < date('now', '-365 day')
                      THEN 1 ELSE 0 END) stale
           FROM PartLocation WHERE IsActive = 1"""
    )

    revenue = totals.get("revenue") or 0
    cost = totals.get("cost") or 0

    return {
        "kpis": {
            "revenue": _round(revenue, 2),
            "profit": _round(revenue - cost, 2),
            "margin_pct": _pct(revenue - cost, revenue),
            "lines": totals.get("lines") or 0,
            "qty_sold": totals.get("qty") or 0,
            "active_parts": catalog.get("active_parts") or 0,
            "never_sold": catalog.get("never_sold") or 0,
            "stale_counts": stale.get("stale") or 0,
            "stocked_parts": stale.get("total") or 0,
        },
        "top_parts": top_parts,
        "by_manufacturer": [
            {
                **r,
                "margin_pct": _pct((r["revenue"] or 0) - (r["cost"] or 0), r["revenue"] or 0),
            }
            for r in by_mfg
        ],
        "monthly": monthly,
        "note": (
            "This extract has no on-hand quantity column, so stock value and "
            "inventory turns cannot be calculated. Parts never sold is used as "
            "a dead-stock proxy."
        ),
    }


# --------------------------------------------------------------------------
# Service
# --------------------------------------------------------------------------


@cached
def service(start: str, end: str) -> dict:
    p = (start, end)

    labor = query_one(
        f"""SELECT COUNT(*) segments, ROUND(SUM(s.NetExt), 2) revenue,
                   ROUND(SUM(s.ActualHrs), 1) actual_hours,
                   ROUND(SUM(s.LaborDiscAmt), 2) discounts
            FROM InvoiceSegment s
            JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
            WHERE s.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    by_tech = query(
        f"""SELECT TRIM(u.FirstName || ' ' || u.LastName) tech,
                   COUNT(*) entries, ROUND(SUM(w.ElapsedHours), 1) hours
            FROM WorkInProgress w
            JOIN AppUser u ON u.AppUserId = w.TechId
            JOIN InvoiceSegment s ON s.SegmentId = w.SegmentId
            JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
            WHERE w.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY hours DESC LIMIT 15""",
        p,
    )

    # Flat-rate segments are the only place a billed-vs-actual gap is visible.
    billing = query(
        f"""SELECT TRIM(s.BillAs) bill_as, COUNT(*) segments,
                   ROUND(SUM(s.NetExt), 2) revenue,
                   ROUND(SUM(s.ActualHrs), 1) actual_hours,
                   ROUND(SUM(s.FlatRateLaborHrs), 1) flat_hours
            FROM InvoiceSegment s
            JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
            WHERE s.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month,
                   ROUND(SUM(s.NetExt), 2) revenue,
                   ROUND(SUM(s.ActualHrs), 1) hours
            FROM InvoiceSegment s
            JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
            WHERE s.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY 1 ORDER BY 1""",
        p,
    )

    wo_status = query(
        f"""SELECT h.Status status, COUNT(*) n FROM InvoiceHeader h
            WHERE h.IsActive = 1 AND h.InvoiceType = 'wo' AND {DATE_RANGE}
            GROUP BY 1 ORDER BY n DESC""",
        p,
    )

    work_orders = query_one(
        f"""SELECT COUNT(*) n FROM InvoiceHeader h
            WHERE h.InvoiceType = 'wo' AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    revenue = labor.get("revenue") or 0
    hours = labor.get("actual_hours") or 0
    wo_count = work_orders.get("n") or 0

    return {
        "kpis": {
            "labor_revenue": _round(revenue, 2),
            "actual_hours": _round(hours, 1),
            "effective_rate": _round(revenue / hours if hours else 0, 2),
            "work_orders": wo_count,
            "avg_hours_per_wo": _round(hours / wo_count if wo_count else 0, 1),
            "avg_revenue_per_wo": _round(revenue / wo_count if wo_count else 0, 2),
            "segments": labor.get("segments") or 0,
            "discounts": _round(labor.get("discounts"), 2),
        },
        "by_tech": by_tech,
        "billing_mix": billing,
        "monthly": monthly,
        "wo_status": wo_status,
        "note": (
            "Every AppUser.HourlyRate in this extract is zero, so labour cost "
            "and service margin cannot be derived. Hours and revenue are shown "
            "instead."
        ),
    }


_TECH_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Work order"},
    {"key": "customer", "label": "Customer"},
    {"key": "job", "label": "Job"},
    {"key": "time_on", "label": "On"},
    {"key": "time_off", "label": "Off"},
    {"key": "hours", "label": "Hours", "fmt": "num1", "num": True},
    {"key": "revenue", "label": "Revenue share", "fmt": "moneyFull", "num": True},
    {"key": "notes", "label": "Notes"},
]

# About one segment in eight has more than one technician clocked on it, and
# those tend to be the big jobs: crediting each technician with the whole
# segment overstates service revenue by 72%. Instead each one is credited with
# the share of the segment matching the share of the hours they clocked on it,
# which adds back to labour revenue exactly.
#
# The * 1.0 is load-bearing. ElapsedHours is stored as INTEGER on about a tenth
# of the rows, and without it SQLite does integer division and silently drops
# the fractional shares -- worth $41.5k over the default window.
_TECH_SHARE = """CASE WHEN seg_hours > 0 THEN w_hours * 1.0 / seg_hours
                      ELSE 1.0 / seg_entries END"""

# The window functions have to be worked out before the technician filter is
# applied, otherwise each partition only sees one technician's rows and every
# share collapses to 100%.
_TECH_WIP = f"""WITH wip AS (
    SELECT w.TechId, w.ElapsedHours w_hours, w.TimeOn, w.TimeOff,
           COALESCE(NULLIF(TRIM(w.TechComment), ''),
                    NULLIF(TRIM(w.Comment), '')) notes,
           s.NetExt seg_revenue, TRIM(s.DisplayText) job,
           h.InvoiceDocId, h.DocNo, h.CustomerId, h.CustomerName, h.ActivityDate,
           SUM(w.ElapsedHours) OVER (PARTITION BY w.SegmentId) seg_hours,
           COUNT(*)            OVER (PARTITION BY w.SegmentId) seg_entries,
           w.SegmentId
    FROM WorkInProgress w
    JOIN InvoiceSegment s ON s.SegmentId = w.SegmentId
    JOIN InvoiceHeader h ON h.InvoiceDocId = s.InvDocId
    WHERE w.IsActive = 1 AND s.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
), mine AS (
    SELECT wip.* FROM wip
    JOIN AppUser u ON u.AppUserId = wip.TechId
    WHERE TRIM(u.FirstName || ' ' || u.LastName) = ?
)"""


@cached
def drill_technician(name: str, start: str, end: str) -> dict:
    """Detail behind one bar of the hours-by-technician chart."""
    p = (start, end, name)

    totals = query_one(
        f"""{_TECH_WIP}
            SELECT COUNT(*) entries, ROUND(SUM(w_hours), 1) hours,
                   ROUND(SUM(seg_revenue * {_TECH_SHARE}), 2) revenue,
                   COUNT(DISTINCT SegmentId) segments,
                   COUNT(DISTINCT InvoiceDocId) work_orders,
                   COUNT(DISTINCT CustomerId) customers
            FROM mine""",
        p,
    )
    monthly = query(
        f"""{_TECH_WIP}
            SELECT substr(ActivityDate, 1, 7) month,
                   ROUND(SUM(w_hours), 1) hours,
                   ROUND(SUM(seg_revenue * {_TECH_SHARE}), 2) revenue
            FROM mine GROUP BY 1 ORDER BY 1""",
        p,
    )
    customers = query(
        f"""{_TECH_WIP}
            SELECT CustomerName name, COUNT(DISTINCT InvoiceDocId) invoices,
                   ROUND(SUM(w_hours), 1) hours,
                   ROUND(SUM(seg_revenue * {_TECH_SHARE}), 2) revenue
            FROM mine GROUP BY CustomerId ORDER BY hours DESC LIMIT 15""",
        p,
    )
    rows = query(
        f"""{_TECH_WIP}
            SELECT substr(ActivityDate, 1, 10) date, DocNo doc_no,
                   CustomerName customer, job,
                   substr(TimeOn, 12, 5) time_on, substr(TimeOff, 12, 5) time_off,
                   ROUND(w_hours, 1) hours,
                   ROUND(seg_revenue * {_TECH_SHARE}, 2) revenue,
                   notes
            FROM mine ORDER BY date DESC, doc_no LIMIT {DETAIL_ROW_LIMIT}""",
        p,
    )

    hours = totals.get("hours") or 0
    revenue = totals.get("revenue") or 0
    entries = totals.get("entries") or 0

    return {
        "title": name,
        "range": {"start": start, "end": end},
        "kpis": {
            "hours": _round(hours, 1),
            "revenue": _round(revenue, 2),
            "effective_rate": _round(revenue / hours if hours else 0, 2),
            "entries": entries,
            "work_orders": totals.get("work_orders") or 0,
            "customers": totals.get("customers") or 0,
        },
        "monthly": monthly,
        "customers_list": customers,
        "columns": _TECH_COLUMNS,
        "rows": rows,
        "total_rows": entries,
        "truncated": len(rows) < entries,
        "note": (
            "Revenue is apportioned: where several technicians clocked on the "
            "same job, each is credited with the share matching the hours they "
            "put in, so these amounts add back to labour revenue rather than "
            "double-counting shared work."
        ),
    }


# --------------------------------------------------------------------------
# Rentals
# --------------------------------------------------------------------------


@cached
def rentals(start: str, end: str) -> dict:
    p = (start, end)

    totals = query_one(
        f"""SELECT COUNT(*) lines, ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM(julianday(r.EndDate) - julianday(r.StartDate)), 0) rental_days,
                   COUNT(DISTINCT r.UnitId) units_rented
            FROM RentalUnit r
            JOIN InvoiceDetail d ON d.ItemId = r.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
              AND r.IsReturned = 0""",
        p,
    )

    fleet = query_one(
        "SELECT COUNT(*) n FROM UnitBase WHERE IsActive = 1 AND Rental = 1"
    )

    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month, ROUND(SUM(r.NetExt), 2) revenue,
                   COUNT(*) lines
            FROM RentalUnit r
            JOIN InvoiceDetail d ON d.ItemId = r.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} AND r.IsReturned = 0
            GROUP BY 1 ORDER BY 1""",
        p,
    )

    # Grouped by stock number alone. The same physical unit is described with
    # two or three different model strings over its life, so grouping on the
    # model as well split single units across rows and pushed real earners out
    # of the list -- one stump grinder was the sixth best unit in the window
    # and did not appear at all.
    top_units = query(
        f"""SELECT r.StockNo stock_no, MAX(r.Model) model, COUNT(*) rentals,
                   ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM(julianday(r.EndDate) - julianday(r.StartDate)), 0) days
            FROM RentalUnit r
            JOIN InvoiceDetail d ON d.ItemId = r.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} AND r.IsReturned = 0
            GROUP BY 1 ORDER BY revenue DESC LIMIT 15""",
        p,
    )

    duration_mix = query(
        f"""SELECT TRIM(r.RentalDuration) duration, COUNT(*) n,
                   ROUND(SUM(r.NetExt), 2) revenue
            FROM RentalUnit r
            JOIN InvoiceDetail d ON d.ItemId = r.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} AND r.IsReturned = 0
            GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )

    contract_status = query(
        f"""SELECT c.ContractStatus status, COUNT(*) n
            FROM RentalContract c
            JOIN InvoiceHeader h ON h.InvoiceDocId = c.InvoiceDocId
            WHERE h.IsActive = 1 AND {DATE_RANGE}
            GROUP BY 1 ORDER BY n DESC""",
        p,
    )

    contracts = query_one(
        f"""SELECT COUNT(*) n FROM RentalContract c
            JOIN InvoiceHeader h ON h.InvoiceDocId = c.InvoiceDocId
            WHERE h.IsActive = 1 AND {DATE_RANGE}""",
        p,
    )

    # Utilisation measures days on rent against the days those units could
    # have been rented. The Rental flag on UnitBase only reflects the fleet as
    # it stands today (106 units) while 163 distinct units actually went out in
    # a recent two-year window, so the count of units that rented at all is the
    # more defensible denominator.
    span_days = query_one(
        "SELECT ROUND(julianday(?) - julianday(?), 0) d", (end, start)
    )
    fleet_n = fleet.get("n") or 0
    units_rented = totals.get("units_rented") or 0
    available = (span_days.get("d") or 0) * units_rented
    rented = totals.get("rental_days") or 0
    revenue = totals.get("revenue") or 0
    contract_n = contracts.get("n") or 0

    return {
        "kpis": {
            "revenue": _round(revenue, 2),
            "contracts": contract_n,
            "avg_contract_value": _round(revenue / contract_n if contract_n else 0, 2),
            "units_rented": units_rented,
            "fleet_size": fleet_n,
            "rental_days": _round(rented),
            "utilization_pct": _pct(rented, available),
            "revenue_per_unit": _round(revenue / units_rented if units_rented else 0, 2),
            "lines": totals.get("lines") or 0,
        },
        "monthly": monthly,
        "top_units": top_units,
        "duration_mix": duration_mix,
        "contract_status": contract_status,
        "note": (
            "Utilisation is billed rental days over the days those units could "
            "have rented, using the units that actually went out rather than "
            "the current rental-fleet flag, which only reflects today's fleet."
        ),
    }


# --------------------------------------------------------------------------
# Rental drill-downs
# --------------------------------------------------------------------------

# The eight Rentals cards come from four places: the billed rental lines, the
# distinct units behind them, the contract records, and the current rental
# fleet flag. Only the first three respect the date filter.
RENTAL_METRICS = {
    "revenue": ("Rental revenue", "line"),
    "rental_days": ("Days on rent", "line"),
    "contracts": ("Rental contract records", "contract"),
    "avg_contract_value": ("Average contract value", "contract"),
    "units_rented": ("Units rented", "unit"),
    "utilization_pct": ("Utilization", "unit"),
    "revenue_per_unit": ("Revenue per unit", "unit"),
    "fleet_size": ("Fleet flagged today", "fleet"),
}

# IsReturned marks the return counterpart of a rental line rather than a unit
# that came back, so the charge lines are the IsReturned = 0 side.
_RENTAL_JOIN = """FROM RentalUnit r
                  JOIN InvoiceDetail d ON d.ItemId = r.ItemId
                  JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId"""
_RENTAL_WHERE = f"WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} AND r.IsReturned = 0"
_SPAN = "julianday(r.EndDate) - julianday(r.StartDate)"

_RENTAL_LINE_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Invoice"},
    {"key": "stock_no", "label": "Stock"},
    {"key": "model", "label": "Model"},
    {"key": "customer", "label": "Customer"},
    {"key": "duration", "label": "Rate basis"},
    {"key": "start", "label": "Out"},
    {"key": "end", "label": "In"},
    {"key": "days", "label": "Days", "fmt": "num1", "num": True},
    {"key": "revenue", "label": "Revenue", "fmt": "moneyFull", "num": True},
]

_RENTAL_UNIT_COLUMNS = [
    {"key": "stock_no", "label": "Stock"},
    {"key": "model", "label": "Model"},
    {"key": "serial", "label": "Serial"},
    {"key": "rentals", "label": "Rentals", "fmt": "num", "num": True},
    {"key": "days", "label": "Days on rent", "fmt": "num1", "num": True},
    {"key": "utilization_pct", "label": "Utilization", "fmt": "pct", "num": True},
    {"key": "revenue", "label": "Revenue", "fmt": "moneyFull", "num": True},
    {"key": "revenue_per_day", "label": "Revenue / day", "fmt": "moneyFull", "num": True},
]

_RENTAL_CONTRACT_COLUMNS = [
    {"key": "date", "label": "Date"},
    {"key": "doc_no", "label": "Invoice"},
    {"key": "contract_no", "label": "Contract"},
    {"key": "type", "label": "Type"},
    {"key": "status", "label": "Status"},
    {"key": "customer", "label": "Customer"},
    {"key": "units", "label": "Units", "fmt": "num", "num": True},
    {"key": "revenue", "label": "Rental revenue", "fmt": "moneyFull", "num": True},
]

_RENTAL_FLEET_COLUMNS = [
    {"key": "stock_no", "label": "Stock"},
    {"key": "make", "label": "Make"},
    {"key": "model", "label": "Model"},
    {"key": "serial", "label": "Serial"},
    {"key": "year", "label": "Year"},
    {"key": "status", "label": "Stock status"},
    {"key": "lifetime_rentals", "label": "Lifetime rentals", "fmt": "num", "num": True},
    {"key": "lifetime_revenue", "label": "Lifetime revenue", "fmt": "moneyFull", "num": True},
]


def _rental_lines(extra: str, params: tuple) -> tuple[list, int, dict, list]:
    """Billed rental lines plus the aggregate the line-based cards restate."""
    where = f"{_RENTAL_WHERE} {extra}"
    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   r.StockNo stock_no, r.Model model, h.CustomerName customer,
                   TRIM(r.RentalDuration) duration,
                   substr(r.StartDate, 1, 10) start, substr(r.EndDate, 1, 10) end,
                   ROUND({_SPAN}, 1) days, ROUND(r.NetExt, 2) revenue
            {_RENTAL_JOIN} {where}
            ORDER BY r.NetExt DESC LIMIT {DETAIL_ROW_LIMIT}""",
        params,
    )
    t = query_one(
        f"""SELECT COUNT(*) lines, ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM({_SPAN}), 0) days,
                   COUNT(DISTINCT r.StockNo) units,
                   COUNT(DISTINCT h.CustomerId) customers
            {_RENTAL_JOIN} {where}""",
        params,
    )
    revenue = t.get("revenue") or 0
    lines = t.get("lines") or 0
    days = t.get("days") or 0
    kpis = {
        "revenue": _round(revenue, 2),
        "rental_days": _round(days),
        "lines": lines,
        "units": t.get("units") or 0,
        "customers": t.get("customers") or 0,
        "avg_line": _round(revenue / lines if lines else 0, 2),
        "revenue_per_day": _round(revenue / days if days else 0, 2),
    }
    defs = [
        {"key": "revenue", "label": "Revenue", "fmt": "moneyFull"},
        {"key": "rental_days", "label": "Days on rent", "fmt": "num"},
        {"key": "revenue_per_day", "label": "Revenue / day", "fmt": "moneyFull"},
        {"key": "lines", "label": "Rental lines", "fmt": "num"},
        {"key": "units", "label": "Units", "fmt": "num"},
        {"key": "customers", "label": "Customers", "fmt": "num"},
    ]
    return rows, lines, kpis, defs


def _rental_units(span: float, start: str, end: str) -> tuple[list, int, dict, list]:
    """One row per unit that went out, which is what utilisation is measured on."""
    rows = query(
        f"""SELECT r.StockNo stock_no, MAX(r.Model) model, MAX(r.BaseSerial) serial,
                   COUNT(*) rentals, ROUND(SUM({_SPAN}), 1) days,
                   ROUND(100.0 * SUM({_SPAN}) / ?, 1) utilization_pct,
                   ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM(r.NetExt) / NULLIF(SUM({_SPAN}), 0), 2) revenue_per_day
            {_RENTAL_JOIN} {_RENTAL_WHERE}
            GROUP BY r.StockNo ORDER BY revenue DESC LIMIT {DETAIL_ROW_LIMIT}""",
        (span, start, end),
    )
    t = query_one(
        f"""SELECT COUNT(DISTINCT r.StockNo) units, ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM({_SPAN}), 0) days
            {_RENTAL_JOIN} {_RENTAL_WHERE}""",
        (start, end),
    )
    units = t.get("units") or 0
    revenue = t.get("revenue") or 0
    days = t.get("days") or 0
    kpis = {
        "units_rented": units,
        "revenue": _round(revenue, 2),
        "rental_days": _round(days),
        "utilization_pct": _pct(days, span * units),
        "revenue_per_unit": _round(revenue / units if units else 0, 2),
        "days_per_unit": _round(days / units if units else 0, 1),
    }
    defs = [
        {"key": "units_rented", "label": "Units rented", "fmt": "num"},
        {"key": "utilization_pct", "label": "Utilization", "fmt": "pct"},
        {"key": "revenue_per_unit", "label": "Revenue / unit", "fmt": "moneyFull"},
        {"key": "days_per_unit", "label": "Days / unit", "fmt": "num1"},
        {"key": "rental_days", "label": "Days on rent", "fmt": "num"},
        {"key": "revenue", "label": "Revenue", "fmt": "moneyFull"},
    ]
    return rows, units, kpis, defs


def _rental_contracts(start: str, end: str) -> tuple[list, int, dict, list]:
    """Contract records, counted the way the Contracts card counts them."""
    where = f"WHERE h.IsActive = 1 AND {DATE_RANGE}"
    join = f"""FROM RentalContract c
               JOIN InvoiceHeader h ON h.InvoiceDocId = c.InvoiceDocId
               LEFT JOIN InvoiceDetail d ON d.InvoiceDocId = h.InvoiceDocId AND d.IsActive = 1
               LEFT JOIN RentalUnit r ON r.ItemId = d.ItemId AND r.IsReturned = 0"""
    rows = query(
        f"""SELECT substr(h.ActivityDate, 1, 10) date, h.DocNo doc_no,
                   NULLIF(TRIM(c.ContractNo), '') contract_no,
                   c.TransactionType type, c.ContractStatus status,
                   h.CustomerName customer, COUNT(r.RentalUnitId) units,
                   ROUND(SUM(r.NetExt), 2) revenue
            {join} {where}
            GROUP BY c.RentalContractId
            ORDER BY revenue DESC NULLS LAST, date DESC LIMIT {DETAIL_ROW_LIMIT}""",
        (start, end),
    )
    t = query_one(
        f"""SELECT COUNT(*) n, COUNT(DISTINCT h.CustomerId) customers,
                   SUM(c.TransactionType = 'parent') parents
            FROM RentalContract c
            JOIN InvoiceHeader h ON h.InvoiceDocId = c.InvoiceDocId {where}""",
        (start, end),
    )
    # The card divides rental-line revenue by this count, so the drill restates
    # the same two numbers rather than recomputing revenue a different way.
    rev = query_one(
        f"""SELECT ROUND(SUM(r.NetExt), 2) revenue
            {_RENTAL_JOIN} {_RENTAL_WHERE}""",
        (start, end),
    )
    n = t.get("n") or 0
    revenue = rev.get("revenue") or 0
    kpis = {
        "contracts": n,
        "parents": t.get("parents") or 0,
        "revenue": _round(revenue, 2),
        "avg_contract_value": _round(revenue / n if n else 0, 2),
        "customers": t.get("customers") or 0,
    }
    defs = [
        {"key": "contracts", "label": "Contract records", "fmt": "num"},
        {"key": "parents", "label": "Of which agreements", "fmt": "num"},
        {"key": "revenue", "label": "Rental revenue", "fmt": "moneyFull"},
        {"key": "avg_contract_value", "label": "Avg per record", "fmt": "moneyFull"},
        {"key": "customers", "label": "Customers", "fmt": "num"},
    ]
    return rows, n, kpis, defs


def _rental_fleet() -> tuple[list, int, dict, list]:
    rows = query(
        """SELECT u.StockNo stock_no, u.Make make, u.Model model,
                  u.BaseSerial serial, u.Year year, u.StockStatus status,
                  COALESCE(f.n, 0) lifetime_rentals,
                  ROUND(COALESCE(f.rev, 0), 2) lifetime_revenue
           FROM UnitBase u
           LEFT JOIN (SELECT UnitId, COUNT(*) n, SUM(NetExt) rev
                      FROM RentalUnit WHERE IsReturned = 0 GROUP BY UnitId) f
                  ON f.UnitId = u.UnitId
           WHERE u.IsActive = 1 AND u.Rental = 1
           ORDER BY COALESCE(f.rev, 0) DESC"""
    )
    t = query_one("SELECT COUNT(*) n FROM UnitBase WHERE IsActive = 1 AND Rental = 1")
    n = t.get("n") or 0
    return rows, n, {"fleet_size": n}, [
        {"key": "fleet_size", "label": "Units in fleet", "fmt": "num"},
    ]


@cached
def drill_rental_metric(metric: str, start: str, end: str) -> dict:
    """Detail behind one KPI card on the Rentals tab."""
    label, source = RENTAL_METRICS[metric]
    span = query_one("SELECT julianday(?) - julianday(?) d", (end, start)).get("d") or 0
    note = None

    if source == "line":
        rows, total, kpis, defs = _rental_lines("", (start, end))
        columns = _RENTAL_LINE_COLUMNS
        note = ("A handful of lines carry an end date before their start date, "
                "so a few day counts come out negative. Sort by Days to see them.")
    elif source == "unit":
        rows, total, kpis, defs = _rental_units(span, start, end)
        columns = _RENTAL_UNIT_COLUMNS
        note = (f"Utilization is days on rent against the {span:,.0f} days in the "
                "selected range, for the units that actually went out.")
    elif source == "contract":
        rows, total, kpis, defs = _rental_contracts(start, end)
        columns = _RENTAL_CONTRACT_COLUMNS
        note = ("The Contracts card counts every rental contract record, which "
                "includes returns, billing and adjustment entries as well as the "
                "rental agreements themselves, and unlike the revenue figures it "
                "does not require the invoice to be finalized. That makes the "
                "average per record lower than an average per agreement.")
    else:
        rows, total, kpis, defs = _rental_fleet()
        columns = _RENTAL_FLEET_COLUMNS
        note = ("The rental flag reflects the fleet as it stands today, so the "
                "date filter does not apply. Lifetime figures cover every rental "
                "line on record.")

    return {
        "title": label,
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "columns": columns,
        "rows": rows,
        "total_rows": total,
        "truncated": len(rows) < total,
        "note": note,
    }


@cached
def drill_rental_unit(stock_no: str, start: str, end: str) -> dict:
    """Detail behind one row of the top rental units table."""
    p = (start, end, stock_no)
    extra = "AND r.StockNo = ?"
    rows, total, kpis, defs = _rental_lines(extra, p)
    where = f"{_RENTAL_WHERE} {extra}"
    span = query_one("SELECT julianday(?) - julianday(?) d", (end, start)).get("d") or 0

    ident = query_one(
        f"""SELECT MAX(r.Model) model, MAX(r.BaseSerial) serial
            {_RENTAL_JOIN} {where}""",
        p,
    )
    monthly = query(
        f"""SELECT substr(h.ActivityDate, 1, 7) month,
                   ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM({_SPAN}), 1) days
            {_RENTAL_JOIN} {where} GROUP BY 1 ORDER BY 1""",
        p,
    )
    customers = query(
        f"""SELECT h.CustomerName name, COUNT(*) invoices,
                   ROUND(SUM({_SPAN}), 1) days, ROUND(SUM(r.NetExt), 2) revenue
            {_RENTAL_JOIN} {where}
            GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 15""",
        p,
    )
    duration_mix = query(
        f"""SELECT TRIM(r.RentalDuration) duration, COUNT(*) n,
                   ROUND(SUM(r.NetExt), 2) revenue
            {_RENTAL_JOIN} {where} GROUP BY 1 ORDER BY revenue DESC""",
        p,
    )

    kpis["utilization_pct"] = _pct(kpis["rental_days"], span)
    defs = [
        {"key": "revenue", "label": "Revenue", "fmt": "moneyFull"},
        {"key": "lines", "label": "Rentals", "fmt": "num"},
        {"key": "rental_days", "label": "Days on rent", "fmt": "num"},
        {"key": "utilization_pct", "label": "Utilization", "fmt": "pct"},
        {"key": "revenue_per_day", "label": "Revenue / day", "fmt": "moneyFull"},
        {"key": "customers", "label": "Customers", "fmt": "num"},
    ]

    model = ident.get("model") or ""
    serial = ident.get("serial") or ""
    return {
        "title": f"Stock {stock_no} — {model}" + (f" ({serial})" if serial else ""),
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "monthly": monthly,
        "customers_list": customers,
        "duration_mix": duration_mix,
        "columns": _RENTAL_LINE_COLUMNS,
        "rows": rows,
        "total_rows": total,
        "truncated": len(rows) < total,
    }


# --------------------------------------------------------------------------
# Customers
# --------------------------------------------------------------------------


@cached
def customers(start: str, end: str) -> dict:
    p = (start, end)

    top = query(
        f"""SELECT h.CustomerName name, h.CustomerNo customer_no,
                   COUNT(DISTINCT h.InvoiceDocId) invoices,
                   ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}
            GROUP BY h.CustomerId ORDER BY revenue DESC LIMIT 20""",
        p,
    )

    totals = query_one(
        f"""SELECT COUNT(DISTINCT h.CustomerId) active, ROUND(SUM(d.NetExt), 2) revenue
            FROM InvoiceDetail d
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE}""",
        p,
    )

    repeat = query_one(
        f"""SELECT COUNT(*) total,
                   SUM(CASE WHEN n > 1 THEN 1 ELSE 0 END) repeat_customers
            FROM (SELECT h.CustomerId, COUNT(DISTINCT h.InvoiceDocId) n
                  FROM InvoiceHeader h
                  WHERE {FINALIZED} AND {DATE_RANGE}
                  GROUP BY 1)""",
        p,
    )

    new_by_year = query(
        """SELECT substr(first_seen, 1, 4) year, COUNT(*) new_customers
           FROM (SELECT CustomerId, MIN(ActivityDate) first_seen
                 FROM InvoiceHeader WHERE IsActive = 1 AND Status = 'finalized'
                 GROUP BY 1)
           GROUP BY 1 ORDER BY 1"""
    )

    # Receivables net charges (recv) against payments (recvpmt). Both link to a
    # customer through the invoice header, not PaymentReceivablesDetail.
    receivables = query(
        """SELECT h.CustomerName name, ROUND(SUM(p.Amount), 2) balance
           FROM Payment p
           JOIN InvoiceHeader h ON h.InvoiceDocId = p.InvoiceDocId
           WHERE p.IsActive = 1 AND p.PmtType IN ('recv', 'recvpmt')
           GROUP BY h.CustomerId
           HAVING SUM(p.Amount) > 1
           ORDER BY balance DESC LIMIT 20"""
    )

    ar_total = query_one(
        """SELECT ROUND(SUM(Amount), 2) balance FROM Payment
           WHERE IsActive = 1 AND PmtType IN ('recv', 'recvpmt')"""
    )

    total_rev = totals.get("revenue") or 0
    top10 = sum(r["revenue"] or 0 for r in top[:10])
    active = totals.get("active") or 0

    return {
        "kpis": {
            "active_customers": active,
            "revenue": _round(total_rev, 2),
            "avg_revenue_per_customer": _round(total_rev / active if active else 0, 2),
            "repeat_pct": _pct(
                repeat.get("repeat_customers") or 0, repeat.get("total") or 0
            ),
            "top10_share_pct": _pct(top10, total_rev),
            "ar_balance": _round(ar_total.get("balance"), 2),
        },
        "top_customers": top,
        "new_by_year": new_by_year,
        "receivables": receivables,
        "note": (
            "Receivables are the lifetime net of credit charges against "
            "payments and ignore the selected date range."
        ),
    }


# --------------------------------------------------------------------------
# Equipment
# --------------------------------------------------------------------------


@cached
def equipment() -> dict:
    """Fleet and inventory state. Reflects the whole file, not a date range."""

    status = query(
        """SELECT TRIM(StockStatus) status, COUNT(*) n
           FROM UnitBase WHERE IsActive = 1 GROUP BY 1 ORDER BY n DESC"""
    )

    condition = query(
        """SELECT c.DisplayText condition, COUNT(*) n
           FROM UnitBase u JOIN UnitCondition c ON c.UnitConditionId = u.UnitConditionId
           WHERE u.IsActive = 1 GROUP BY 1 ORDER BY n DESC"""
    )

    categories = query(
        """SELECT c.DisplayText category, COUNT(*) n
           FROM UnitBase u JOIN UnitCategory c ON c.UnitCategoryId = u.UnitCategoryId
           WHERE u.IsActive = 1 GROUP BY 1 ORDER BY n DESC LIMIT 12"""
    )

    top_models = query(
        """SELECT u.Model model, COUNT(*) n
           FROM UnitBase u WHERE u.IsActive = 1 AND TRIM(u.Model) <> ''
           GROUP BY 1 ORDER BY n DESC LIMIT 15"""
    )

    in_stock = query(
        """SELECT u.StockNo stock_no, u.Model model, u.Description description,
                  u.Year year, ROUND(u.BaseCost, 2) cost, ROUND(u.BaseRetail, 2) retail,
                  substr(u.DateReceived, 1, 10) received,
                  CAST(julianday('now') - julianday(u.DateReceived) AS INTEGER) age_days
           FROM UnitBase u
           WHERE u.IsActive = 1 AND TRIM(u.StockStatus) = 'instock'
           ORDER BY age_days DESC NULLS LAST LIMIT 25"""
    )

    stock_value = query_one(
        """SELECT COUNT(*) n, ROUND(SUM(BaseCost), 2) cost,
                  SUM(CASE WHEN BaseCost > 0 THEN 1 ELSE 0 END) with_cost,
                  SUM(CASE WHEN DateReceived IS NOT NULL THEN 1 ELSE 0 END) with_date
           FROM UnitBase WHERE IsActive = 1 AND TRIM(StockStatus) = 'instock'"""
    )

    trades = query(
        """SELECT substr(EntDate, 1, 4) year, COUNT(*) n,
                  ROUND(SUM(TradeValue), 2) trade_value,
                  ROUND(SUM(OverAllowance), 2) over_allowance
           FROM SaleUnitTradeIn GROUP BY 1 ORDER BY 1"""
    )

    return {
        "kpis": {
            "in_stock": stock_value.get("n") or 0,
            "stock_cost": _round(stock_value.get("cost"), 2),
            "cost_coverage": stock_value.get("with_cost") or 0,
            "received_date_coverage": stock_value.get("with_date") or 0,
            "total_units": sum(r["n"] for r in status),
            "at_customer": next(
                (r["n"] for r in status if r["status"] == "customer"), 0
            ),
        },
        "status": status,
        "condition": condition,
        "categories": categories,
        "top_models": top_models,
        "in_stock_units": in_stock,
        "trade_history": trades,
        "note": (
            "Stock value uses BaseCost, which is populated for most but not "
            "all in-stock units, and only some carry a received date, so "
            "ageing covers a subset of the yard. Retail prices are too sparse "
            "to report."
        ),
    }
