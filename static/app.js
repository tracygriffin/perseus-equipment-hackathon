/* Equipment dealer dashboard: tab config, rendering, and Chart.js wiring. */

// --------------------------------------------------------------------------
// Formatting
// --------------------------------------------------------------------------

const nf = new Intl.NumberFormat("en-US");

function money(v) {
  if (v === null || v === undefined) return "\u2014";
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`;
  return `${sign}$${a.toFixed(0)}`;
}

function moneyFull(v) {
  if (v === null || v === undefined) return "\u2014";
  return (v < 0 ? "-$" : "$") + nf.format(Math.round(Math.abs(v)));
}

function num(v) {
  if (v === null || v === undefined) return "\u2014";
  return nf.format(Math.round(v));
}

function num1(v) {
  if (v === null || v === undefined) return "\u2014";
  return nf.format(Math.round(v * 10) / 10);
}

function pct(v) {
  if (v === null || v === undefined) return "n/a";
  return `${v.toFixed(1)}%`;
}

function text(v) {
  if (v === null || v === undefined || v === "") return "\u2014";
  return String(v);
}

const FMT = { money, moneyFull, num, num1, pct, text };

// --------------------------------------------------------------------------
// Chart theming
// --------------------------------------------------------------------------

const PALETTE = [
  "#f5a524", "#3b82f6", "#22c55e", "#a855f7", "#ef4444",
  "#14b8a6", "#eab308", "#ec4899", "#64748b", "#8b5cf6",
];

Chart.defaults.font.family = "Segoe UI, system-ui, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.maintainAspectRatio = false;
// Mutate the duration rather than replacing defaults.animation: the object it
// ships with also carries easing and the interpolator lookups, and dropping
// those makes Chart.js throw mid-animation on hover, which kills its shared
// requestAnimationFrame loop and silently blanks every chart drawn afterwards.
Chart.defaults.animation.duration = 350;
// Without a cap, charts with only two or three categories draw comically wide bars.
Chart.defaults.datasets.bar.maxBarThickness = 64;

// Line charts draw a marker on every reading so it is obvious where to point
// for a tooltip. Long ranges pack the months tightly, so the markers shrink
// rather than merging into a solid band. The hit radius stays generous either
// way, which keeps the points easy to hit once they are small.
Chart.defaults.elements.point.radius = (ctx) =>
  (ctx.chart.data.labels || []).length > 60 ? 2 : 3;
Chart.defaults.elements.point.hoverRadius = 6;
Chart.defaults.elements.point.hitRadius = 12;
Chart.defaults.elements.point.borderWidth = 0;
// A line's own backgroundColor is the translucent area fill, which would leave
// the markers almost invisible, so they take the line colour instead.
Chart.defaults.elements.point.backgroundColor = (ctx) =>
  ctx.dataset?.borderColor || PALETTE[0];

// Axis grids and tick labels inherit these, so re-reading them from the
// stylesheet is all it takes to move Chart.js between themes.
function themeVars() {
  const cs = getComputedStyle(document.documentElement);
  const read = (name) => cs.getPropertyValue(name).trim();
  return { muted: read("--muted"), grid: read("--grid"), panel: read("--panel") };
}

function applyChartTheme() {
  const t = themeVars();
  Chart.defaults.color = t.muted;
  Chart.defaults.borderColor = t.grid;
}

const moneyAxis = { ticks: { callback: (v) => money(v) } };

const UNITS = { money: moneyFull, hours: num1, count: num };

// Without a declared unit the tooltip has to guess, and the guess used to read
// any value over a thousand as dollars -- which turned four thousand technician
// hours into $4,191. A panel can set `unit` to a name, or to a map of dataset
// label to name when one chart mixes the two.
function unitFmt(unit, seriesName, v) {
  const named = typeof unit === "string" ? unit : unit?.[seriesName];
  if (named) return UNITS[named];
  const looksLikeMoney = Math.abs(v) >= 1000
    || String(seriesName).match(/revenue|profit|value|cost/i);
  return looksLikeMoney ? moneyFull : num1;
}

// Trade-ins are a single large negative line. Charted alongside the others it
// doubles the axis range and squashes every real department into the right
// half, so the breakdown charts drop it and caption the amount instead. The
// Revenue KPI is still net of trade-ins, so the bars deliberately no longer
// sum to it.
const TRADE_IN_TYPE = "TR";

const withoutTradeIns = (rows) => rows.filter((r) => r.item_type !== TRADE_IN_TYPE);

function tradeInCaption(rows) {
  const tr = rows.find((r) => r.item_type === TRADE_IN_TYPE);
  if (!tr) return null;
  return `Excludes ${moneyFull(Math.abs(tr.value))} of trade-ins, which the `
    + `revenue total nets out`;
}

function departmentBar(drillable) {
  return {
    title: "Revenue by department", type: "bar", horizontal: true,
    caption: (d) => tradeInCaption(d.by_department),
    build: (d) => {
      const rows = withoutTradeIns(d.by_department);
      return {
        labels: rows.map((r) => r.label),
        datasets: [{
          label: "Revenue", data: rows.map((r) => r.value),
          backgroundColor: rows.map((r, i) =>
            r.value < 0 ? "#ef4444" : PALETTE[i % PALETTE.length]),
        }],
      };
    },
    ...(drillable && {
      onClick: (i, d) => openDrill("department", withoutTradeIns(d.by_department)[i].item_type),
    }),
    scales: { x: moneyAxis },
  };
}

// Shared by the Parts tab and the manufacturer drill, which rank parts the same
// way. Clicking through opens that one part's history.
const topPartsTable = {
  title: "Top 20 parts by revenue", type: "table", span: "wide",
  caption: () => "Click a row for that part's detail",
  cols: [
    { key: "part_no", label: "Part no" },
    { key: "description", label: "Description" },
    { key: "qty", label: "Qty", fmt: "num", num: true },
    { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
    { key: "profit", label: "Profit", fmt: "moneyFull", num: true },
  ],
  rows: (d) => d.top_parts,
  onRowClick: (r) => openDrill("part", r.part_no),
};

const byManufacturerTable = {
  title: "Revenue and margin by manufacturer", type: "table",
  caption: () => "Click a row for that manufacturer's detail",
  cols: [
    { key: "manufacturer", label: "Manufacturer" },
    { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
    { key: "margin_pct", label: "Margin", fmt: "pct", num: true },
  ],
  rows: (d) => d.by_manufacturer,
  onRowClick: (r) => openDrill("manufacturer", r.manufacturer),
};

// --------------------------------------------------------------------------
// Tab definitions
// --------------------------------------------------------------------------

/** Pivot [{month, <groupKey>, <valueKey>}] into Chart.js datasets. */
function pivot(rows, groupKey, valueKey, labelMap = {}) {
  const months = [...new Set(rows.map((r) => r.month))].sort();
  const groups = [...new Set(rows.map((r) => r[groupKey]))];
  const index = new Map(months.map((m, i) => [m, i]));
  const datasets = groups.map((g, i) => {
    const data = new Array(months.length).fill(0);
    rows.filter((r) => r[groupKey] === g).forEach((r) => {
      data[index.get(r.month)] = r[valueKey] || 0;
    });
    return { label: labelMap[g] || g, data, backgroundColor: PALETTE[i % PALETTE.length] };
  });
  return { labels: months, datasets };
}

const INVOICE_TYPES = { in: "Counter sales", wo: "Work orders", rl: "Rentals" };

const DAY_MS = 86400000;

/** Days of a "YYYY-MM" month that fall inside the selected range. */
function daysInMonthWithinRange(month, range) {
  const [y, m] = month.split("-").map(Number);
  const monthStart = Date.UTC(y, m - 1, 1);
  const monthEnd = Date.UTC(y, m, 1);
  if (!range) return Math.round((monthEnd - monthStart) / DAY_MS);
  const lo = Math.max(monthStart, Date.parse(`${range.start}T00:00:00Z`));
  const hi = Math.min(monthEnd, Date.parse(`${range.end}T00:00:00Z`));
  return Math.max(1, Math.round((hi - lo) / DAY_MS));
}

const TABS = [
  {
    id: "overview",
    label: "Overview",
    endpoint: "/api/overview",
    kpis: [
      { key: "revenue", label: "Revenue", fmt: "moneyFull", hint: "Net of trade-ins" },
      { key: "gross_profit", label: "Gross profit", fmt: "moneyFull", hint: "Parts + units" },
      { key: "margin_pct", label: "Gross margin", fmt: "pct", tone: (v) => (v < 10 ? "bad" : "good") },
      { key: "invoices", label: "Invoices", fmt: "num" },
      { key: "avg_invoice", label: "Avg invoice", fmt: "moneyFull" },
      { key: "active_customers", label: "Active customers", fmt: "num" },
      { key: "open_work_orders", label: "Open work orders", fmt: "num" },
    ],
    panels: [
      {
        title: "Monthly revenue", type: "line", span: "wide",
        build: (d) => ({
          labels: d.trend.map((r) => r.month),
          datasets: [{
            label: "Revenue", data: d.trend.map((r) => r.revenue),
            borderColor: PALETTE[0], backgroundColor: "rgba(245,165,36,.12)",
            fill: true, tension: .3, borderWidth: 2,
          }],
        }),
        tooltip: (d) => ({
          afterLabel: (ctx) => {
            const days = daysInMonthWithinRange(ctx.label, d.range);
            const row = d.trend[ctx.dataIndex] || {};
            return [
              `Per day: ${moneyFull(ctx.parsed.y / days)} across ${days} days`,
              `Invoices: ${num(row.invoices)}`,
            ];
          },
        }),
        scales: { y: moneyAxis },
      },
      departmentBar(true),
      {
        title: "Revenue by year (all history)", type: "bar",
        build: (d, meta) => ({
          labels: meta.revenue_by_year.map((r) => r.year),
          datasets: [{
            label: "Revenue", data: meta.revenue_by_year.map((r) => r.revenue),
            backgroundColor: PALETTE[1],
          }],
        }),
        onClick: (i) => openDrill("year", state.meta.revenue_by_year[i].year),
        scales: { y: moneyAxis },
      },
      {
        title: "Margin by line of business", type: "table", span: "wide",
        cols: [
          { key: "label", label: "Line of business" },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
          { key: "cost", label: "Cost", fmt: "moneyFull", num: true },
          { key: "margin_pct", label: "Margin", fmt: "pct", num: true },
          { key: "basis", label: "Cost basis" },
        ],
        rows: (d) => d.margin_detail,
      },
    ],
  },

  {
    id: "sales",
    label: "Sales",
    endpoint: "/api/sales",
    // Every card here has a matching slice in SALES_METRICS server-side.
    kpiDrill: (spec) => openDrill("sales-kpi", spec.key),
    kpis: [
      { key: "units_sold", label: "Units sold", fmt: "num" },
      { key: "unit_revenue", label: "Unit revenue", fmt: "moneyFull" },
      { key: "unit_margin_pct", label: "Unit margin", fmt: "pct", hint: "Priced units only", tone: (v) => (v < 5 ? "bad" : "good") },
      { key: "unit_margin_all_pct", label: "Unit margin (all)", fmt: "pct", hint: "Includes $0 lines" },
      { key: "zero_priced_units", label: "Zero-priced units", fmt: "num", hint: "Cost but no price" },
      { key: "avg_unit_price", label: "Avg unit price", fmt: "moneyFull" },
      { key: "trade_ins", label: "Trade-ins", fmt: "num" },
      { key: "trade_value", label: "Trade value", fmt: "moneyFull" },
      { key: "quotes_won", label: "Quotes won", fmt: "num" },
      { key: "quotes_open", label: "Quotes open", fmt: "num" },
    ],
    panels: [
      {
        title: "Monthly revenue by invoice type", type: "bar", span: "wide", stacked: true,
        build: (d) => pivot(d.monthly_by_type, "type", "revenue", INVOICE_TYPES),
        scales: { y: moneyAxis },
      },
      {
        title: "New vs used", type: "table",
        cols: [
          { key: "label", label: "Condition" },
          { key: "units", label: "Units", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
          { key: "margin_pct", label: "Margin", fmt: "pct", num: true },
        ],
        rows: (d) => d.condition_mix,
      },
      {
        title: "Quote outcomes", type: "bar", unit: "count",
        build: (d) => ({
          labels: d.quote_status.map((r) => r.status),
          datasets: [{
            label: "Quotes", data: d.quote_status.map((r) => r.n),
            backgroundColor: PALETTE.slice(0, d.quote_status.length),
          }],
        }),
      },
      {
        title: "Top salespeople by revenue", type: "table", span: "wide",
        caption: () => "Click a row for that salesperson's detail",
        cols: [
          { key: "name", label: "Salesperson" },
          { key: "invoices", label: "Invoices", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.salespeople,
        onRowClick: (r) => openDrill("salesperson", r.name),
      },
    ],
  },

  {
    id: "parts",
    label: "Parts",
    endpoint: "/api/parts",
    kpis: [
      { key: "revenue", label: "Parts revenue", fmt: "moneyFull" },
      { key: "profit", label: "Gross profit", fmt: "moneyFull" },
      { key: "margin_pct", label: "Margin", fmt: "pct", tone: (v) => (v >= 25 ? "good" : "bad") },
      { key: "qty_sold", label: "Units shipped", fmt: "num" },
      { key: "lines", label: "Line items", fmt: "num" },
      { key: "active_parts", label: "Active catalog", fmt: "num" },
      { key: "never_sold", label: "Never sold", fmt: "num", hint: "Dead-stock proxy", tone: () => "bad" },
      { key: "stale_counts", label: "Stale counts", fmt: "num", hint: "Not counted in 12 months" },
    ],
    kpiDrill: (spec) => openDrill("parts-kpi", spec.key),
    panels: [
      {
        title: "Monthly parts revenue and profit", type: "line", span: "wide",
        build: (d) => ({
          labels: d.monthly.map((r) => r.month),
          datasets: [
            {
              label: "Revenue", data: d.monthly.map((r) => r.revenue),
              borderColor: PALETTE[0], backgroundColor: "rgba(245,165,36,.12)",
              fill: true, tension: .3, borderWidth: 2,
            },
            {
              label: "Profit", data: d.monthly.map((r) => r.profit),
              borderColor: PALETTE[2], backgroundColor: "rgba(34,197,94,.1)",
              fill: true, tension: .3, borderWidth: 2,
            },
          ],
        }),
        scales: { y: moneyAxis },
      },
      byManufacturerTable,
      {
        title: "Revenue share by manufacturer", type: "doughnut",
        caption: () => "Click a slice for that manufacturer's detail",
        build: (d) => ({
          labels: d.by_manufacturer.map((r) => r.manufacturer),
          datasets: [{
            data: d.by_manufacturer.map((r) => r.revenue),
            backgroundColor: PALETTE, borderColor: "#171d26", borderWidth: 2,
          }],
        }),
        onClick: (i, d) => openDrill("manufacturer", d.by_manufacturer[i].manufacturer),
      },
      topPartsTable,
    ],
  },

  {
    id: "service",
    label: "Service",
    endpoint: "/api/service",
    kpis: [
      { key: "labor_revenue", label: "Labor revenue", fmt: "moneyFull" },
      { key: "actual_hours", label: "Actual hours", fmt: "num" },
      { key: "effective_rate", label: "Effective rate", fmt: "moneyFull", hint: "Revenue per hour" },
      { key: "work_orders", label: "Work orders", fmt: "num" },
      { key: "avg_hours_per_wo", label: "Avg hrs / WO", fmt: "num1" },
      { key: "avg_revenue_per_wo", label: "Avg rev / WO", fmt: "moneyFull" },
      { key: "segments", label: "Job segments", fmt: "num" },
      { key: "discounts", label: "Labor discounts", fmt: "moneyFull" },
    ],
    panels: [
      {
        title: "Monthly labor revenue and hours", type: "line", span: "wide",
        unit: { Revenue: "money", Hours: "hours" },
        build: (d) => ({
          labels: d.monthly.map((r) => r.month),
          datasets: [
            {
              label: "Revenue", data: d.monthly.map((r) => r.revenue), yAxisID: "y",
              borderColor: PALETTE[0], backgroundColor: "rgba(245,165,36,.12)",
              fill: true, tension: .3, borderWidth: 2,
            },
            {
              label: "Hours", data: d.monthly.map((r) => r.hours), yAxisID: "y1",
              borderColor: PALETTE[1], tension: .3, borderWidth: 2,
            },
          ],
        }),
        scales: {
          y: { ...moneyAxis, position: "left" },
          y1: { position: "right", grid: { drawOnChartArea: false } },
        },
      },
      {
        title: "Hours by technician", type: "bar", horizontal: true, tall: true,
        unit: "hours",
        build: (d) => ({
          labels: d.by_tech.map((r) => r.tech),
          datasets: [{
            label: "Hours", data: d.by_tech.map((r) => r.hours),
            backgroundColor: PALETTE[1],
          }],
        }),
        tooltip: (d) => ({
          afterLabel: (ctx) => `${num(d.by_tech[ctx.dataIndex].entries)} clock entries`,
        }),
        onClick: (i, d) => openDrill("technician", d.by_tech[i].tech),
      },
      {
        title: "Billed vs actual hours", type: "table",
        cols: [
          { key: "bill_as", label: "Billing basis" },
          { key: "segments", label: "Segments", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
          { key: "actual_hours", label: "Actual hrs", fmt: "num1", num: true },
          { key: "flat_hours", label: "Flat-rate hrs", fmt: "num1", num: true },
        ],
        rows: (d) => d.billing_mix,
      },
      {
        title: "Work orders by status", type: "bar", span: "wide",
        unit: "count",
        build: (d) => ({
          labels: d.wo_status.map((r) => r.status),
          datasets: [{
            label: "Work orders", data: d.wo_status.map((r) => r.n),
            backgroundColor: PALETTE.slice(0, d.wo_status.length),
          }],
        }),
      },
    ],
  },

  {
    id: "rentals",
    label: "Rentals",
    endpoint: "/api/rentals",
    kpis: [
      { key: "revenue", label: "Rental revenue", fmt: "moneyFull" },
      { key: "contracts", label: "Contracts", fmt: "num" },
      { key: "avg_contract_value", label: "Avg contract", fmt: "moneyFull" },
      { key: "units_rented", label: "Units rented", fmt: "num", hint: "Distinct, in range" },
      { key: "fleet_size", label: "Fleet flagged today", fmt: "num" },
      { key: "rental_days", label: "Days on rent", fmt: "num" },
      { key: "utilization_pct", label: "Utilization", fmt: "pct", tone: (v) => (v >= 50 ? "good" : "bad") },
      { key: "revenue_per_unit", label: "Revenue / unit", fmt: "moneyFull" },
    ],
    panels: [
      {
        title: "Monthly rental revenue", type: "line", span: "wide",
        build: (d) => ({
          labels: d.monthly.map((r) => r.month),
          datasets: [{
            label: "Revenue", data: d.monthly.map((r) => r.revenue),
            borderColor: PALETTE[2], backgroundColor: "rgba(34,197,94,.12)",
            fill: true, tension: .3, borderWidth: 2,
          }],
        }),
        scales: { y: moneyAxis },
      },
      {
        title: "Top rental units by revenue", type: "table",
        cols: [
          { key: "model", label: "Model" },
          { key: "rentals", label: "Rentals", fmt: "num", num: true },
          { key: "days", label: "Days", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.top_units,
      },
      {
        title: "Revenue by rental duration", type: "doughnut",
        build: (d) => ({
          labels: d.duration_mix.map((r) => r.duration || "unspecified"),
          datasets: [{
            data: d.duration_mix.map((r) => r.revenue),
            backgroundColor: PALETTE, borderColor: "#171d26", borderWidth: 2,
          }],
        }),
      },
      {
        title: "Contracts by status", type: "bar", span: "wide", unit: "count",
        build: (d) => ({
          labels: d.contract_status.map((r) => r.status),
          datasets: [{
            label: "Contracts", data: d.contract_status.map((r) => r.n),
            backgroundColor: PALETTE.slice(0, d.contract_status.length),
          }],
        }),
      },
    ],
  },

  {
    id: "customers",
    label: "Customers",
    endpoint: "/api/customers",
    kpis: [
      { key: "active_customers", label: "Active customers", fmt: "num" },
      { key: "revenue", label: "Revenue", fmt: "moneyFull" },
      { key: "avg_revenue_per_customer", label: "Revenue / customer", fmt: "moneyFull" },
      { key: "repeat_pct", label: "Repeat rate", fmt: "pct", hint: "More than one invoice", tone: (v) => (v >= 50 ? "good" : "bad") },
      { key: "top10_share_pct", label: "Top 10 share", fmt: "pct", hint: "Concentration risk" },
      { key: "ar_balance", label: "Open receivables", fmt: "moneyFull", hint: "Lifetime, all dates" },
    ],
    panels: [
      {
        title: "Top 20 customers by revenue", type: "table", span: "wide",
        cols: [
          { key: "name", label: "Customer" },
          { key: "customer_no", label: "Account" },
          { key: "invoices", label: "Invoices", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.top_customers,
      },
      {
        title: "New customers by year", type: "bar", unit: "count",
        build: (d) => ({
          labels: d.new_by_year.map((r) => r.year),
          datasets: [{
            label: "New customers", data: d.new_by_year.map((r) => r.new_customers),
            backgroundColor: PALETTE[1],
          }],
        }),
      },
      {
        title: "Largest open receivable balances", type: "table",
        cols: [
          { key: "name", label: "Customer" },
          { key: "balance", label: "Balance", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.receivables,
      },
    ],
  },

  {
    id: "equipment",
    label: "Equipment",
    endpoint: "/api/equipment",
    usesRange: false,
    kpis: [
      { key: "total_units", label: "Units tracked", fmt: "num" },
      { key: "in_stock", label: "In stock", fmt: "num" },
      { key: "at_customer", label: "At customer", fmt: "num" },
      { key: "stock_cost", label: "Stock at cost", fmt: "moneyFull" },
      { key: "cost_coverage", label: "With cost data", fmt: "num", hint: "Of in-stock units" },
      { key: "received_date_coverage", label: "With received date", fmt: "num", hint: "Limits ageing" },
    ],
    panels: [
      {
        title: "Units by category", type: "bar", horizontal: true, unit: "count",
        build: (d) => ({
          labels: d.categories.map((r) => r.category),
          datasets: [{
            label: "Units", data: d.categories.map((r) => r.n),
            backgroundColor: PALETTE[1],
          }],
        }),
      },
      {
        title: "Units by condition", type: "doughnut",
        build: (d) => ({
          labels: d.condition.map((r) => r.condition),
          datasets: [{
            data: d.condition.map((r) => r.n),
            backgroundColor: PALETTE, borderColor: "#171d26", borderWidth: 2,
          }],
        }),
      },
      {
        title: "Most common models", type: "bar", span: "wide", unit: "count",
        build: (d) => ({
          labels: d.top_models.map((r) => r.model),
          datasets: [{
            label: "Units", data: d.top_models.map((r) => r.n),
            backgroundColor: PALETTE[0],
          }],
        }),
      },
      {
        title: "Trade-ins by year", type: "bar",
        build: (d) => ({
          labels: d.trade_history.map((r) => r.year),
          datasets: [{
            label: "Trade value", data: d.trade_history.map((r) => r.trade_value),
            backgroundColor: PALETTE[3],
          }],
        }),
        scales: { y: moneyAxis },
      },
      {
        title: "Oldest in-stock units", type: "table",
        cols: [
          { key: "stock_no", label: "Stock" },
          { key: "model", label: "Model" },
          { key: "received", label: "Received", fmt: "text" },
          { key: "age_days", label: "Age (days)", fmt: "num", num: true },
          { key: "cost", label: "Cost", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.in_stock_units,
      },
    ],
  },
];

// --------------------------------------------------------------------------
// Drill-down definitions
// --------------------------------------------------------------------------

const monthlyRevenueLine = (color) => ({
  title: "Monthly revenue", type: "line", span: "wide",
  build: (d) => ({
    labels: d.monthly.map((r) => r.month),
    datasets: [{
      label: "Revenue", data: d.monthly.map((r) => r.revenue),
      borderColor: color, backgroundColor: "rgba(245,165,36,.12)",
      fill: true, tension: .3, borderWidth: 2,
    }],
  }),
  tooltip: (d) => ({
    afterLabel: (ctx) => {
      const days = daysInMonthWithinRange(ctx.label, d.range);
      return `Per day: ${moneyFull(ctx.parsed.y / days)} across ${days} days`;
    },
  }),
  scales: { y: moneyAxis },
});

const topCustomersTable = {
  title: "Top customers", type: "table",
  cols: [
    { key: "name", label: "Customer" },
    { key: "invoices", label: "Invoices", fmt: "num", num: true },
    { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
  ],
  rows: (d) => d.customers_list,
};

// Every drill ends in the same searchable list of source records, with columns
// supplied by the payload because the shape differs per metric.
const detailTable = (title) => ({
  title, type: "table", span: "wide", filter: true,
  caption: (d) => {
    const help = "Type to filter, click a column heading to sort";
    if (!d.truncated) return help;
    return `Showing the top ${num(d.rows.length)} of ${num(d.total_rows)} rows. ${help}`;
  },
  cols: (d) => d.columns,
  rows: (d) => d.rows,
});

const monthlyPartsLine = {
  title: "Monthly revenue and profit", type: "line", span: "wide",
  build: (d) => ({
    labels: d.monthly.map((r) => r.month),
    datasets: [
      {
        label: "Revenue", data: d.monthly.map((r) => r.revenue),
        borderColor: PALETTE[0], backgroundColor: "rgba(245,165,36,.12)",
        fill: true, tension: .3, borderWidth: 2,
      },
      {
        label: "Profit", data: d.monthly.map((r) => r.profit),
        borderColor: PALETTE[2], backgroundColor: "rgba(34,197,94,.1)",
        fill: true, tension: .3, borderWidth: 2,
      },
    ],
  }),
  scales: { y: moneyAxis },
};

const DRILLS = {
  department: {
    url: (key, range) =>
      `/api/drill/department?item_type=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: [
      { key: "revenue", label: "Revenue", fmt: "moneyFull" },
      { key: "lines", label: "Line items", fmt: "num" },
      { key: "invoices", label: "Invoices", fmt: "num" },
      { key: "customers", label: "Customers", fmt: "num" },
      { key: "avg_line", label: "Avg line", fmt: "moneyFull" },
    ],
    panels: [
      monthlyRevenueLine(PALETTE[0]),
      {
        title: "Top items by revenue", type: "table",
        cols: [
          { key: "item", label: "Item" },
          { key: "description", label: "Description" },
          { key: "qty", label: "Qty", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.items,
      },
      topCustomersTable,
    ],
  },

  "sales-kpi": {
    url: (key, range) =>
      `/api/drill/sales-kpi?metric=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    // Shape varies by metric, so the cards and columns come from the payload.
    kpis: (d) => d.kpi_defs,
    panels: [detailTable("Records behind this number")],
  },

  "parts-kpi": {
    url: (key, range) =>
      `/api/drill/parts-kpi?metric=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: (d) => d.kpi_defs,
    panels: [detailTable("Records behind this number")],
  },

  manufacturer: {
    url: (key, range) =>
      `/api/drill/manufacturer?name=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: (d) => d.kpi_defs,
    panels: [
      monthlyPartsLine,
      topPartsTable,
      topCustomersTable,
      detailTable("Parts sale lines"),
    ],
  },

  part: {
    url: (key, range) =>
      `/api/drill/part?part_no=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: (d) => d.kpi_defs,
    panels: [
      monthlyPartsLine,
      topCustomersTable,
      detailTable("Sale lines"),
    ],
  },

  technician: {
    url: (key, range) =>
      `/api/drill/technician?name=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: [
      { key: "hours", label: "Hours clocked", fmt: "num1" },
      { key: "revenue", label: "Revenue share", fmt: "moneyFull" },
      { key: "effective_rate", label: "Effective rate", fmt: "moneyFull" },
      { key: "entries", label: "Clock entries", fmt: "num" },
      { key: "work_orders", label: "Work orders", fmt: "num" },
      { key: "customers", label: "Customers", fmt: "num" },
    ],
    panels: [
      {
        title: "Monthly hours and revenue", type: "line", span: "wide",
        unit: { Revenue: "money", Hours: "hours" },
        build: (d) => ({
          labels: d.monthly.map((r) => r.month),
          datasets: [
            {
              label: "Hours", data: d.monthly.map((r) => r.hours), yAxisID: "y",
              borderColor: PALETTE[1], backgroundColor: "rgba(96,165,250,.12)",
              fill: true, tension: .3, borderWidth: 2,
            },
            {
              label: "Revenue", data: d.monthly.map((r) => r.revenue), yAxisID: "y1",
              borderColor: PALETTE[0], tension: .3, borderWidth: 2,
            },
          ],
        }),
        scales: {
          y: { position: "left" },
          y1: { ...moneyAxis, position: "right", grid: { drawOnChartArea: false } },
        },
      },
      {
        title: "Top customers by hours", type: "table",
        cols: [
          { key: "name", label: "Customer" },
          { key: "invoices", label: "Work orders", fmt: "num", num: true },
          { key: "hours", label: "Hours", fmt: "num1", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.customers_list,
      },
      detailTable("Clock entries"),
    ],
  },

  salesperson: {
    url: (key, range) =>
      `/api/drill/salesperson?name=${encodeURIComponent(key)}` +
      `&start=${range.start}&end=${range.end}`,
    kpis: [
      { key: "revenue", label: "Revenue", fmt: "moneyFull" },
      { key: "invoices", label: "Invoices", fmt: "num" },
      { key: "avg_invoice", label: "Avg invoice", fmt: "moneyFull" },
      { key: "units_sold", label: "Units sold", fmt: "num" },
      { key: "customers", label: "Customers", fmt: "num" },
    ],
    panels: [
      monthlyRevenueLine(PALETTE[3]),
      departmentBar(false),
      topCustomersTable,
      detailTable("Units sold"),
    ],
  },

  year: {
    url: (key) => `/api/drill/year?year=${encodeURIComponent(key)}`,
    kpis: [
      { key: "revenue", label: "Revenue", fmt: "moneyFull" },
      { key: "invoices", label: "Invoices", fmt: "num" },
      { key: "avg_invoice", label: "Avg invoice", fmt: "moneyFull" },
      { key: "customers", label: "Customers", fmt: "num" },
    ],
    panels: [
      monthlyRevenueLine(PALETTE[1]),
      departmentBar(false),
      topCustomersTable,
      {
        title: "Top salespeople", type: "table", span: "wide",
        cols: [
          { key: "name", label: "Salesperson" },
          { key: "invoices", label: "Invoices", fmt: "num", num: true },
          { key: "revenue", label: "Revenue", fmt: "moneyFull", num: true },
        ],
        rows: (d) => d.salespeople,
      },
    ],
  },
};

// --------------------------------------------------------------------------
// Rendering
// --------------------------------------------------------------------------

const state = {
  meta: null, active: "overview", charts: {}, loaded: {}, data: {}, drill: null,
};

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
}

function renderKpis(container, specs, kpis, onClick) {
  container.innerHTML = "";
  specs.forEach((spec) => {
    const raw = kpis[spec.key];
    const card = el("div", "kpi");
    card.appendChild(el("div", "label", spec.label));
    const tone = spec.tone && raw !== null && raw !== undefined ? spec.tone(raw) : "";
    card.appendChild(el("div", `value ${tone}`, FMT[spec.fmt || "num"](raw)));
    if (spec.hint) card.appendChild(el("div", "hint", spec.hint));
    if (onClick) {
      card.classList.add("clickable");
      card.title = `Show the records behind ${spec.label}`;
      card.onclick = () => onClick(spec);
    }
    container.appendChild(card);
  });
}

function renderTable(panel, spec, data) {
  // Drill-downs describe their own columns, since the shape depends on which
  // record set sits behind the card that was clicked.
  const cols = typeof spec.cols === "function" ? spec.cols(data) : spec.cols;
  const allRows = spec.rows(data) || [];

  let sortKey = null;
  let sortDir = 1;
  let term = "";

  const wrap = el("div", "table-scroll");
  const table = el("table");
  const thead = el("thead");
  const htr = el("tr");
  const headers = cols.map((c) => {
    const th = el("th", c.num ? "num" : "", c.label);
    if (spec.filter) {
      th.classList.add("sortable");
      th.onclick = () => {
        // First click on a number sorts high to low, on text A to Z.
        if (sortKey === c.key) sortDir = -sortDir;
        else { sortKey = c.key; sortDir = c.num ? -1 : 1; }
        draw();
      };
    }
    htr.appendChild(th);
    return th;
  });
  thead.appendChild(htr);
  table.appendChild(thead);
  const tbody = el("tbody");
  table.appendChild(tbody);
  wrap.appendChild(table);

  let count = null;
  if (spec.filter) {
    const bar = el("div", "table-tools");
    const input = el("input", "table-search");
    input.type = "search";
    input.placeholder = "Filter rows\u2026";
    input.oninput = () => {
      term = input.value.trim().toLowerCase();
      draw();
    };
    count = el("span", "table-count");
    bar.appendChild(input);
    bar.appendChild(count);
    panel.appendChild(bar);
  }
  panel.appendChild(wrap);

  function matches(row) {
    if (!term) return true;
    return cols.some((c) => String(row[c.key] ?? "").toLowerCase().includes(term));
  }

  function compare(a, b) {
    const x = a[sortKey];
    const y = b[sortKey];
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "number" && typeof y === "number") return (x - y) * sortDir;
    return String(x).localeCompare(String(y)) * sortDir;
  }

  function draw() {
    let rows = term ? allRows.filter(matches) : allRows.slice();
    if (sortKey) rows.sort(compare);

    headers.forEach((th, i) => {
      th.classList.toggle("sorted", cols[i].key === sortKey);
      th.dataset.dir = cols[i].key === sortKey ? (sortDir > 0 ? "asc" : "desc") : "";
    });

    tbody.innerHTML = "";
    if (!rows.length) {
      const tr = el("tr");
      const td = el("td", "", term ? "No rows match that filter" : "No data in this range");
      td.colSpan = cols.length;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    rows.forEach((r) => {
      const tr = el("tr");
      cols.forEach((c) => {
        const fmt = FMT[c.fmt || "text"];
        tr.appendChild(el("td", c.num ? "num" : "", fmt(r[c.key])));
      });
      if (spec.onRowClick) {
        tr.classList.add("clickable");
        tr.onclick = () => spec.onRowClick(r, data);
      }
      tbody.appendChild(tr);
    });

    if (count) {
      const shown = rows.length;
      const total = allRows.length;
      count.textContent = shown === total
        ? `${num(total)} rows`
        : `${num(shown)} of ${num(total)} rows`;
    }
  }

  draw();
}

function renderChart(panel, spec, data, key) {
  const box = el("div", "chart-box");
  const canvas = document.createElement("canvas");
  box.appendChild(canvas);
  panel.appendChild(box);

  const chartData = spec.build(data, state.meta);
  const stacked = !!spec.stacked;
  const horizontal = !!spec.horizontal;
  const isPie = spec.type === "doughnut";

  // Doughnut dividers have to match whatever the panel behind them is.
  if (isPie) {
    const panel = themeVars().panel;
    chartData.datasets.forEach((ds) => { ds.borderColor = panel; });
  }

  const options = {
    indexAxis: horizontal ? "y" : "x",
    plugins: {
      legend: {
        display: isPie || chartData.datasets.length > 1,
        position: isPie ? "right" : "top",
        labels: { boxWidth: 12, boxHeight: 12, padding: 10 },
      },
      tooltip: {
        callbacks: {
          label: (ctx) => {
            const v = ctx.parsed[horizontal ? "x" : "y"] ?? ctx.parsed;
            const name = isPie ? ctx.label : ctx.dataset.label;
            return `${name}: ${unitFmt(spec.unit, name, v)(v)}`;
          },
          ...(spec.tooltip ? spec.tooltip(data) : {}),
        },
      },
    },
    scales: isPie ? undefined : {
      x: { stacked, ...(spec.scales?.x || {}) },
      y: { stacked, ...(spec.scales?.y || {}) },
      ...(spec.scales?.y1 ? { y1: spec.scales.y1 } : {}),
    },
  };

  if (spec.onClick) {
    options.onClick = (_evt, els) => {
      if (els.length) spec.onClick(els[0].index, data);
    };
    options.onHover = (evt, els) => {
      evt.native.target.style.cursor = els.length ? "pointer" : "default";
    };
    panel.querySelector("h3").appendChild(el("span", "drill-hint", "click to drill in"));
  }

  if (state.charts[key]) state.charts[key].destroy();
  state.charts[key] = new Chart(canvas, { type: spec.type, data: chartData, options });
}

function renderPanels(container, specs, data, keyPrefix) {
  const panels = el("div", "panels");
  container.appendChild(panels);

  // Each panel is attached before its chart is built. Chart.js sizes itself
  // from the canvas at construction, and a detached node measures zero, which
  // leaves the chart blank until something else forces a resize.
  specs.forEach((spec, i) => {
    const classes = ["panel", spec.span || "", spec.tall ? "tall" : ""].filter(Boolean);
    const panel = el("div", classes.join(" "));
    panel.appendChild(el("h3", "", spec.title));
    const caption = spec.caption ? spec.caption(data) : null;
    if (caption) panel.appendChild(el("div", "caption", caption));
    panels.appendChild(panel);
    try {
      if (spec.type === "table") renderTable(panel, spec, data);
      else renderChart(panel, spec, data, `${keyPrefix}-${i}`);
    } catch (e) {
      panel.appendChild(el("div", "err", `Could not render: ${e.message}`));
    }
  });
}

// --------------------------------------------------------------------------
// Drill-down modal
// --------------------------------------------------------------------------

function closeDrill() {
  const backdrop = document.getElementById("drill");
  if (!backdrop) return;
  Object.keys(state.charts)
    .filter((k) => k.startsWith("drill-"))
    .forEach((k) => {
      state.charts[k].destroy();
      delete state.charts[k];
    });
  backdrop.remove();
  document.body.style.overflow = "";
  state.drill = null;
}

function renderDrill(spec, data) {
  const body = document.querySelector("#drill .modal-body");
  if (!body) return;
  body.innerHTML = "";
  if (data.note) body.appendChild(el("div", "note", data.note));
  const kpiWrap = el("div", "kpis");
  const defs = typeof spec.kpis === "function" ? spec.kpis(data) : spec.kpis;
  renderKpis(kpiWrap, defs, data.kpis);
  body.appendChild(kpiWrap);
  renderPanels(body, spec.panels, data, "drill");
}

async function openDrill(kind, key) {
  const spec = DRILLS[kind];
  if (!spec) return;
  closeDrill();

  const backdrop = el("div", "modal-backdrop");
  backdrop.id = "drill";
  backdrop.onclick = (e) => {
    if (e.target === backdrop) closeDrill();
  };

  const title = el("h2", "", "Loading\u2026");
  const close = el("button", "modal-close", "&times;");
  close.title = "Close";
  close.onclick = closeDrill;

  const head = el("div", "modal-head");
  head.appendChild(title);
  head.appendChild(close);

  const body = el("div", "modal-body", '<div class="spinner">Loading&hellip;</div>');
  const modal = el("div", "modal");
  modal.appendChild(head);
  modal.appendChild(body);
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  document.body.style.overflow = "hidden";

  try {
    const res = await fetch(spec.url(key, currentRange()));
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    title.textContent = data.title;
    state.drill = { spec, data };
    renderDrill(spec, data);
  } catch (e) {
    title.textContent = "Detail";
    body.innerHTML = `<div class="err">Could not load detail: ${e.message}</div>`;
  }
}

function renderTab(tab, data) {
  const view = document.getElementById(`view-${tab.id}`);
  view.innerHTML = "";

  if (data.note) view.appendChild(el("div", "note", data.note));

  if (tab.kpis && data.kpis) {
    const kpiWrap = el("div", "kpis");
    renderKpis(kpiWrap, tab.kpis, data.kpis, tab.kpiDrill);
    view.appendChild(kpiWrap);
  }

  renderPanels(view, tab.panels, data, tab.id);
}

// --------------------------------------------------------------------------
// Data loading
// --------------------------------------------------------------------------

function currentRange() {
  return {
    start: document.getElementById("start").value,
    end: document.getElementById("end").value,
  };
}

async function loadTab(tab, force = false) {
  const { start, end } = currentRange();
  const key = tab.usesRange === false ? tab.id : `${tab.id}:${start}:${end}`;
  if (!force && state.loaded[key]) return;

  const view = document.getElementById(`view-${tab.id}`);
  if (!state.loaded[key]) view.innerHTML = '<div class="spinner">Loading&hellip;</div>';

  const url = tab.usesRange === false
    ? tab.endpoint
    : `${tab.endpoint}?start=${start}&end=${end}`;
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.data[tab.id] = data;
    renderTab(tab, data);
    state.loaded[key] = true;
  } catch (e) {
    view.innerHTML = `<div class="err">Failed to load ${tab.label}: ${e.message}</div>`;
  }
}

function setStatus(msg) {
  document.getElementById("status").textContent = msg || "";
}

// --------------------------------------------------------------------------
// Theme
// --------------------------------------------------------------------------

const THEME_KEY = "dealer-dashboard-theme";

function storedTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

function setTheme(theme, rerender = true) {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private browsing; the theme still applies for this session */
  }
  applyChartTheme();
  // Charts bake in their colours at construction, so every tab already drawn
  // has to be rebuilt from the payload it was rendered with.
  if (rerender) {
    TABS.forEach((tab) => {
      if (state.data[tab.id]) renderTab(tab, state.data[tab.id]);
    });
    if (state.drill) renderDrill(state.drill.spec, state.drill.data);
  }
}

function initTheme() {
  const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  setTheme(storedTheme() || (prefersLight ? "light" : "dark"), false);
}

async function refresh() {
  state.loaded = {};
  const btn = document.getElementById("apply");
  btn.disabled = true;
  setStatus("Loading\u2026");
  const active = TABS.find((t) => t.id === state.active);
  await loadTab(active, true);
  setStatus("");
  btn.disabled = false;
}

function selectTab(id) {
  state.active = id;
  if (location.hash.slice(1) !== id) location.hash = id;
  document.querySelectorAll("#tabs button").forEach((b) => {
    b.classList.toggle("active", b.dataset.id === id);
  });
  document.querySelectorAll(".tab").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${id}`);
  });
  loadTab(TABS.find((t) => t.id === id));
}

function shiftDays(iso, days) {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

function applyPreset(value) {
  const { bounds } = state.meta;
  const startInput = document.getElementById("start");
  const endInput = document.getElementById("end");

  // The API filters ActivityDate < end, so the window has to reach the day
  // after the last invoice or that day's revenue is silently dropped. This is
  // the same end date the server picks for the initial page load.
  const end = shiftDays(bounds.max, 1);
  endInput.value = end;

  if (value === "all") {
    startInput.value = bounds.min;
    return;
  }
  const months = parseInt(value, 10);
  if (!months) return;
  // UTC throughout: local-time arithmetic here lands on the wrong day for
  // anyone east of Greenwich. Snapping to the first of the month before
  // shifting avoids overflowing out of short months.
  const start = new Date(`${end}T00:00:00Z`);
  start.setUTCDate(1);
  start.setUTCMonth(start.getUTCMonth() - months);
  startInput.value = start.toISOString().slice(0, 10);
}

async function init() {
  initTheme();

  const nav = document.getElementById("tabs");
  const views = document.getElementById("views");
  TABS.forEach((t, i) => {
    const b = el("button", i === 0 ? "active" : "", t.label);
    b.dataset.id = t.id;
    b.onclick = () => selectTab(t.id);
    nav.appendChild(b);
    const v = el("div", `tab ${i === 0 ? "active" : ""}`);
    v.id = `view-${t.id}`;
    views.appendChild(v);
  });

  try {
    state.meta = await (await fetch("/api/meta")).json();
  } catch (e) {
    views.innerHTML = `<div class="err">Cannot reach the API: ${e.message}</div>`;
    return;
  }

  document.getElementById("start").value = state.meta.default.start;
  document.getElementById("end").value = state.meta.default.end;
  document.getElementById("subtitle").textContent =
    `Records ${state.meta.bounds.min} to ${state.meta.bounds.max}`;

  document.getElementById("apply").onclick = refresh;
  document.getElementById("theme").onclick = () => {
    setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
  };
  document.getElementById("preset").onchange = (e) => {
    if (!e.target.value) return;
    applyPreset(e.target.value);
    refresh();
  };
  ["start", "end"].forEach((id) => {
    document.getElementById(id).onchange = () => {
      document.getElementById("preset").value = "";
    };
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeDrill();
  });

  window.addEventListener("hashchange", () => {
    const id = location.hash.slice(1);
    if (id && id !== state.active && TABS.some((t) => t.id === id)) selectTab(id);
  });

  const requested = location.hash.slice(1);
  selectTab(TABS.some((t) => t.id === requested) ? requested : "overview");
}

init();
