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


def _unit_detail(extra: str, p: tuple) -> tuple[list, dict, list]:
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
    return rows, kpis, defs


def _trade_detail(p: tuple) -> tuple[list, dict, list]:
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
    return rows, kpis, defs


def _quote_detail(extra: str, p: tuple, open_only: bool) -> tuple[list, dict, list]:
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
    return rows, kpis, defs


@cached
def drill_sales_metric(metric: str, start: str, end: str) -> dict:
    """Detail behind one KPI card on the Sales tab."""
    label, source, extra = SALES_METRICS[metric]
    p = (start, end)

    if source == "unit":
        rows, kpis, defs = _unit_detail(extra, p)
        columns = _UNIT_COLUMNS
    elif source == "trade":
        rows, kpis, defs = _trade_detail(p)
        columns = _TRADE_COLUMNS
    else:
        rows, kpis, defs = _quote_detail(extra, p, source == "open_quote")
        columns = _QUOTE_COLUMNS

    return {
        "title": label,
        "range": {"start": start, "end": end},
        "kpis": kpis,
        "kpi_defs": defs,
        "columns": columns,
        "rows": rows,
        "truncated": len(rows) >= DETAIL_ROW_LIMIT,
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
    }


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

    top_units = query(
        f"""SELECT r.Model model, r.StockNo stock_no, COUNT(*) rentals,
                   ROUND(SUM(r.NetExt), 2) revenue,
                   ROUND(SUM(julianday(r.EndDate) - julianday(r.StartDate)), 0) days
            FROM RentalUnit r
            JOIN InvoiceDetail d ON d.ItemId = r.ItemId
            JOIN InvoiceHeader h ON h.InvoiceDocId = d.InvoiceDocId
            WHERE d.IsActive = 1 AND {FINALIZED} AND {DATE_RANGE} AND r.IsReturned = 0
            GROUP BY 1, 2 ORDER BY revenue DESC LIMIT 15""",
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
