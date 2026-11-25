// Beangrid Explore: every offer in an AG Grid (Community edition) for sorting/filtering any column.
// Shares the main page's hidden-roasters list (localStorage) so opting out carries over.
"use strict";

const $ = (s) => document.querySelector(s);
const HIDE_KEY = "beangrid.hiddenRoasters";
const money = (n) => (n == null ? "" : `$${Number(n).toFixed(2)}`);
const pct = (n) => (n == null ? "" : `${Math.round(n * 100)}%`);

function loadHidden() {
  try { return new Set(JSON.parse(localStorage.getItem(HIDE_KEY) || "[]")); } catch { return new Set(); }
}

function access(o) {
  if (o.members_only) return "Members only";
  if (o.wholesale) return "Wholesale";
  return "Retail";
}

const columnDefs = [
  { field: "roaster", headerName: "Roaster", width: 170, pinned: "left" },
  {
    field: "title", headerName: "Coffee", width: 280, pinned: "left",
    cellRenderer: (p) => {
      const a = document.createElement("a");
      a.href = p.data.url; a.target = "_blank"; a.rel = "noopener"; a.textContent = p.value;
      return a;
    },
  },
  { field: "grams", headerName: "Size (g)", width: 105, filter: "agNumberColumnFilter", type: "numericColumn" },
  { field: "price", headerName: "Price", width: 100, filter: "agNumberColumnFilter", type: "numericColumn",
    valueFormatter: (p) => money(p.value) },
  { field: "ppk", headerName: "$/kg", width: 100, filter: "agNumberColumnFilter", type: "numericColumn", sort: "asc",
    valueFormatter: (p) => money(p.value) },
  { field: "sale_pct", headerName: "Sale", width: 90, filter: "agNumberColumnFilter", type: "numericColumn",
    valueFormatter: (p) => pct(p.value) },
  { field: "drop_pct", headerName: "Drop vs 30d", width: 120, filter: "agNumberColumnFilter", type: "numericColumn",
    valueFormatter: (p) => pct(p.value) },
  { field: "deal_score", headerName: "Deal score", width: 115, filter: "agNumberColumnFilter", type: "numericColumn" },
  { field: "origins_text", headerName: "Origin", width: 150 },
  { field: "process_text", headerName: "Process", width: 140 },
  { field: "roast", headerName: "Roast", width: 120 },
  { field: "caffeine", headerName: "Caffeine", width: 115 },
  { field: "style", headerName: "Style", width: 100 },
  { field: "access", headerName: "Access", width: 130 },
  { field: "kind", headerName: "Beans", width: 100 },
  { field: "stock", headerName: "Stock", width: 105 },
  { field: "first_seen", headerName: "First seen", width: 120 },
  { field: "options_text", headerName: "Options", width: 260 },
];

const filters = { instock: true, public: true, clean: true, green: false, hidden: true };
let hiddenRoasters = new Set();

const gridOptions = {
  columnDefs,
  defaultColDef: { sortable: true, resizable: true, filter: true, floatingFilter: true },
  rowData: [],
  animateRows: false,
  isExternalFilterPresent: () => true,
  doesExternalFilterPass: (node) => {
    const o = node.data;
    if (filters.instock && !o.available) return false;
    if (filters.public && (o.wholesale || o.members_only)) return false;
    if (filters.clean && o.suspect) return false;
    if (!filters.green && o.green) return false;
    if (filters.hidden && hiddenRoasters.has(o.roaster)) return false;
    return true;
  },
  onModelUpdated: (e) => {
    $("#grid-count").textContent = `${e.api.getDisplayedRowCount().toLocaleString()} rows`;
  },
};

function applyTheme() {
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  $("#grid").className = dark ? "ag-theme-quartz-dark" : "ag-theme-quartz";
}

async function main() {
  applyTheme();
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);
  const api = agGrid.createGrid($("#grid"), gridOptions);

  const data = await (await fetch("data/offers.json", { cache: "no-cache" })).json();
  const rows = data.offers.map((o) => ({
    ...o,
    origins_text: (o.origins || []).join(", "),
    process_text: (o.process || []).join(", "),
    caffeine: o.half_caf ? "Half-caf" : o.decaf ? "Decaf" : "Regular",
    access: access(o),
    kind: o.green ? "Green" : "Roasted",
    stock: o.available ? "In stock" : "Sold out",
    options_text: (o.options || []).join(" · "),
  }));
  hiddenRoasters = loadHidden(); // before rowData: the external filter runs as rows arrive
  api.setGridOption("rowData", rows);
  $("#meta").textContent = `${rows.length.toLocaleString()} offers · updated ${new Date(data.generated_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;

  if (hiddenRoasters.size) {
    $("#f-hidden-wrap").hidden = false;
    $("#f-hidden-label").textContent = `Hide my ${hiddenRoasters.size} hidden roaster${hiddenRoasters.size === 1 ? "" : "s"}`;
  }

  for (const [id, key] of [["#f-instock", "instock"], ["#f-public", "public"], ["#f-clean", "clean"],
    ["#f-green", "green"], ["#f-hidden", "hidden"]]) {
    $(id).addEventListener("change", (e) => { filters[key] = e.target.checked; api.onFilterChanged(); });
  }
  let t;
  $("#quick").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => api.setGridOption("quickFilterText", e.target.value), 150);
  });
  $("#csv").addEventListener("click", () => api.exportDataAsCsv({ fileName: `beangrid-${data.date}.csv` }));
}

main().catch((err) => {
  console.error(err);
  $("#meta").textContent = "Couldn't load the data. Try refreshing.";
});
