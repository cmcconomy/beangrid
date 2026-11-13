// Beangrid front end: no dependencies, no build step. Data comes from data/offers.json,
// produced daily by the Python pipeline (src/beangrid/pipeline.py).

const PAGE = 60;
const $ = (sel, root = document) => root.querySelector(sel);
const money = (n) => `$${n.toFixed(2)}`;
const pct = (f) => `${Math.round(f * 100)}%`;

const FLAG_TEXT = {
  no_weight: "Bag size not found on the listing",
  implausible_price_per_kg: "Price per kg is implausible (likely a wrong size or price)",
  price_per_kg_outlier: "Far cheaper per kg than this roaster's other coffees: probably a listing error",
  weight_conflict: "Size on the label disagrees with Shopify's shipping weight (label used)",
  weight_from_shipping: "Size guessed from shipping weight; no size on the label",
};

const DEAL_TABS = [
  {
    id: "top", label: "Top deals",
    hint: "Highest deal score: discounts weighted 60%, price-per-kg value 40%. Retail bags and up; samples excluded.",
    pick: (o) => o.deal_score != null && (o.drop_pct || o.sale_pct || o.value_pct <= 0.1),
    sort: (a, b) => b.deal_score - a.deal_score,
  },
  {
    id: "drop", label: "Price drops",
    hint: "At least 5% under the coffee's own typical price over the past 30 days.",
    pick: (o) => eligible(o) && o.drop_pct,
    sort: (a, b) => b.drop_pct - a.drop_pct,
  },
  {
    id: "sale", label: "On sale",
    hint: "The roaster shows a higher \"compare at\" price. Markdowns running 60+ days are treated as the regular price.",
    pick: (o) => eligible(o) && o.sale_pct && !o.perma_sale,
    sort: (a, b) => b.sale_pct - a.sale_pct,
  },
  {
    id: "value", label: "Best $/kg · bags",
    hint: "Cheapest per kilo among in-stock retail bags (150–599 g).",
    pick: (o) => eligible(o) && o.bucket === "bag",
    sort: (a, b) => a.ppk - b.ppk,
  },
  {
    id: "bulk", label: "Best $/kg · bulk",
    hint: "Cheapest per kilo among in-stock 600 g+ bags.",
    pick: (o) => eligible(o) && (o.bucket === "large" || o.bucket === "bulk"),
    sort: (a, b) => a.ppk - b.ppk,
  },
  {
    id: "new", label: "New arrivals",
    hint: "First seen in the last 7 days.",
    pick: (o) => o.available && o.is_new && !o.sample,
    sort: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""),
  },
  {
    id: "back", label: "Back in stock",
    hint: "Restocked this week after being sold out for 3+ days.",
    pick: (o) => o.available && o.back_in_stock && !o.sample,
    sort: (a, b) => (b.deal_score ?? 0) - (a.deal_score ?? 0),
  },
];

const state = { data: null, offers: [], shown: PAGE, tab: "top" };

function eligible(o) {
  return o.available && !o.suspect && !o.sample && o.ppk != null;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) node.append(c);
  return node;
}

function sparkline(points, w = 96, h = 26) {
  if (!points || points.length === 0) return null;
  if (points.length === 1 && points[0][0] === state.data.date) return null; // first seen today: no trend yet
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
  svg.setAttribute("preserveAspectRatio", "none"); // lets cards shrink it horizontally
  const today = Date.parse(state.data.date);
  const start = today - 120 * 864e5;
  const xs = (d) => ((Date.parse(d) - start) / (today - start)) * (w - 4) + 2;
  const prices = points.map((p) => p[1]);
  const lo = Math.min(...prices), hi = Math.max(...prices);
  const ys = (p) => (hi === lo ? h / 2 : h - 3 - ((p - lo) / (hi - lo)) * (h - 6));
  let d = "";
  points.forEach(([date, price], i) => {
    const x = Math.max(2, xs(date)), y = ys(price);
    d += i === 0 ? `M${x},${y}` : `H${x}V${y}`;
  });
  const last = points[points.length - 1];
  d += `H${w - 2}`;
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", d);
  const dot = document.createElementNS(ns, "circle");
  dot.setAttribute("cx", w - 2);
  dot.setAttribute("cy", ys(last[1]));
  dot.setAttribute("r", 2.2);
  dot.setAttribute("vector-effect", "non-scaling-stroke");
  const title = document.createElementNS(ns, "title");
  title.textContent = hi === lo
    ? `Steady at ${money(lo)} over the last 120 days`
    : `120-day range ${money(lo)}–${money(hi)}`;
  svg.append(title, path, dot);
  return svg;
}

function chips(o) {
  const out = [];
  for (const c of o.origins || []) out.push(c);
  if (o.blend && !(o.origins || []).length) out.push("Blend");
  for (const p of o.process || []) out.push(p);
  if (o.roast) out.push(`${o.roast} roast`);
  if (o.decaf) out.push("Decaf");
  if (o.half_caf) out.push("Half-caf");
  if (o.style) out.push(o.style[0].toUpperCase() + o.style.slice(1));
  return out.map((t) => el("span", { class: "chip", text: t }));
}

function badges(o, { compact = false } = {}) {
  const out = [];
  if (o.drop_pct) out.push(el("span", { class: "badge b-drop", title: `Typically ${money(o.ref_price)} over the past 30 days`, text: `↓ ${pct(o.drop_pct)} vs 30-day` }));
  if (o.sale_pct && o.perma_sale) {
    if (!compact) out.push(el("span", { class: "badge b-value", title: `Marked down for ${o.sale_days} days: this is effectively the regular price`, text: `"Sale" −${pct(o.sale_pct)} for ${o.sale_days}d` }));
  } else if (o.sale_pct) {
    out.push(el("span", { class: "badge b-sale", title: o.sale_days ? `On sale for ${o.sale_days} days` : "", text: `Sale −${pct(o.sale_pct)}` }));
  }
  if (o.low_90d && !compact) out.push(el("span", { class: "badge b-drop", text: "90-day low" }));
  if (o.is_new) out.push(el("span", { class: "badge b-new", text: "New" }));
  if (o.back_in_stock) out.push(el("span", { class: "badge b-back", text: "Back in stock" }));
  if (o.value_pct != null && o.value_pct <= 0.1 && !compact) {
    out.push(el("span", { class: "badge b-value", title: "Price per kg in the cheapest 10% for this bag size", text: "Top 10% value" }));
  }
  const warn = (o.flags || []).filter((f) => FLAG_TEXT[f] && (o.suspect || f === "weight_from_shipping"));
  if (warn.length && !compact) {
    out.push(el("span", { class: "badge b-warn", title: warn.map((f) => FLAG_TEXT[f]).join("\n"), text: o.suspect ? "⚠ check listing" : "~ size guessed" }));
  }
  return out;
}

function card(o) {
  const node = $("#card-tpl").content.firstElementChild.cloneNode(true);
  const a = $(".card-link", node);
  a.href = o.url;
  $(".roaster", node).textContent = o.roaster;
  const [b] = badges(o, { compact: true });
  if (b) $(".badge", node).replaceWith(b);
  $(".title", node).textContent = o.title;
  $(".chips", node).append(...chips(o).slice(0, 4));
  const price = $(".price", node);
  price.textContent = money(o.price);
  const was = o.compare_at || (o.drop_pct ? o.ref_price : null);
  if (was) price.append(el("span", { class: "was", text: money(was) }));
  $(".size", node).textContent = o.size;
  $(".ppk", node).textContent = o.ppk != null ? `${money(o.ppk)}/kg` : "";
  const sp = sparkline(o.history);
  if (sp) $(".spark", node).append(sp);
  return node;
}

function renderTabs() {
  const tabs = $("#deal-tabs");
  tabs.replaceChildren();
  for (const t of DEAL_TABS) {
    const n = state.offers.filter(t.pick).length;
    if (!n && t.id !== "top") continue;
    const btn = el("button", { class: "tab", role: "tab", type: "button", "aria-selected": String(t.id === state.tab) },
      t.label, el("span", { class: "n", text: n }));
    btn.addEventListener("click", () => { state.tab = t.id; renderTabs(); renderDeals(); });
    tabs.append(btn);
  }
}

function renderDeals() {
  const tab = DEAL_TABS.find((t) => t.id === state.tab) || DEAL_TABS[0];
  $("#deal-hint").textContent = tab.hint;
  const picked = state.offers.filter(tab.pick).sort(tab.sort);
  // One card per coffee: the best size of each product.
  const seen = new Set();
  const unique = [];
  for (const o of picked) {
    const k = `${o.site}|${o.title}`;
    if (seen.has(k)) continue;
    seen.add(k);
    unique.push(o);
    if (unique.length >= 12) break;
  }
  const box = $("#deal-cards");
  box.replaceChildren(...(unique.length ? unique.map(card) : [el("p", { class: "empty", text: "Nothing here today." })]));
}

// ---- browse table ------------------------------------------------------------------------

function readFilters() {
  const f = new FormData($("#filters"));
  return {
    q: (f.get("q") || "").trim().toLowerCase(),
    roaster: f.get("roaster"), origin: f.get("origin"), process: f.get("process"), roast: f.get("roast"),
    decaf: f.get("decaf"), bucket: f.get("bucket"), maxppk: parseFloat(f.get("maxppk")) || null,
    sort: f.get("sort"), instock: f.get("instock") === "on", nosamples: f.get("nosamples") === "on",
    clean: f.get("clean") === "on",
  };
}

function matches(o, f) {
  if (f.instock && !o.available) return false;
  if (f.nosamples && o.sample) return false;
  if (f.clean && o.suspect) return false;
  if (f.roaster && o.roaster !== f.roaster) return false;
  if (f.origin && !(o.origins || []).includes(f.origin)) return false;
  if (f.process && !(o.process || []).includes(f.process)) return false;
  if (f.roast && o.roast !== f.roast) return false;
  if (f.decaf === "no" && o.decaf) return false;
  if (f.decaf === "only" && !o.decaf) return false;
  if (f.bucket && o.bucket !== f.bucket) return false;
  if (f.maxppk && !(o.ppk != null && o.ppk <= f.maxppk)) return false;
  if (f.q) {
    const terms = f.q.split(/\s+/);
    if (!terms.every((t) => o._text.includes(t))) return false;
  }
  return true;
}

const SORTS = {
  deal: (a, b) => (b.deal_score ?? -1) - (a.deal_score ?? -1) || (a.ppk ?? 1e9) - (b.ppk ?? 1e9),
  ppk: (a, b) => (a.ppk ?? 1e9) - (b.ppk ?? 1e9),
  price: (a, b) => a.price - b.price,
  drop: (a, b) => Math.max(b.drop_pct || 0, b.perma_sale ? 0 : b.sale_pct || 0) - Math.max(a.drop_pct || 0, a.perma_sale ? 0 : a.sale_pct || 0) || (b.deal_score ?? -1) - (a.deal_score ?? -1),
  new: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""),
  roaster: (a, b) => a.roaster.localeCompare(b.roaster) || a.title.localeCompare(b.title) || (a.grams ?? 0) - (b.grams ?? 0),
};

function optionsLine(o) {
  const opts = o.options || [];
  if (opts.length < 2) return null;
  if (opts.length <= 4) return el("div", { class: "opts", text: opts.join(" · ") });
  return el("div", { class: "opts", title: opts.join("\n"), text: `${opts.length} grind / roast options` });
}

function row(o) {
  const coffee = el("td", { class: "c-coffee" },
    el("a", { class: "t", href: o.url, target: "_blank", rel: "noopener", text: o.title }),
    el("div", { class: "r", text: o.roaster }, o.stale ? el("span", { class: "stale", title: "This roaster's store didn't respond today; showing its most recent data", text: " · data from an earlier day" }) : null),
    el("div", {}, chips(o)),
    optionsLine(o),
  );
  const ppkCell = el("td", { class: "num c-ppk" }, o.ppk != null ? `${money(o.ppk)}` : "–");
  if (o.value_pct != null) {
    const bar = el("span", { class: "vbar", title: `Cheaper per kg than ${Math.round((1 - o.value_pct) * 100)}% of ${o.bucket}-size coffees` }, el("i"));
    bar.firstChild.style.width = `${Math.round((1 - o.value_pct) * 100)}%`;
    ppkCell.append(bar);
  }
  const priceCell = el("td", { class: "num c-price" }, money(o.price));
  if (o.compare_at) priceCell.append(el("span", { class: "was", text: money(o.compare_at) }));
  const trend = el("td", { class: "c-trend" });
  const sp = sparkline(o.history);
  if (sp) trend.append(sp);
  return el("tr", { class: o.available ? null : "oos" },
    coffee,
    el("td", { class: "num c-size", text: o.size + (o.available ? "" : " (sold out)") }),
    priceCell,
    ppkCell,
    el("td", { class: "c-deal" }, el("div", { class: "deal" }, badges(o))),
    trend,
  );
}

function renderTable(resetPaging = false) {
  const f = readFilters();
  if (resetPaging) state.shown = PAGE;
  const list = state.offers.filter((o) => matches(o, f)).sort(SORTS[f.sort] || SORTS.deal);
  $("#count").textContent = `${list.length.toLocaleString()} matching`;
  $("#results tbody").replaceChildren(...list.slice(0, state.shown).map(row));
  const more = $("#more");
  more.hidden = list.length <= state.shown;
  more.textContent = `Show more (${(list.length - state.shown).toLocaleString()} left)`;
  writeHash(f);
}

// ---- URL state so filtered views can be shared ------------------------------------------

const DEFAULTS = { sort: "deal", instock: true, nosamples: true, clean: true };

function writeHash(f) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) {
    if (k in DEFAULTS) { if (v !== DEFAULTS[k]) p.set(k, v === true ? "1" : v === false ? "0" : v); }
    else if (v) p.set(k, v);
  }
  const s = p.toString();
  history.replaceState(null, "", s ? `#${s}` : location.pathname);
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const form = $("#filters");
  for (const [k, v] of p) {
    const input = form.elements[k];
    if (!input) continue;
    if (input.type === "checkbox") input.checked = v === "1";
    else input.value = v;
  }
}

function fillSelect(name, values) {
  const sel = $(`#filters [name=${name}]`);
  for (const v of values) sel.append(el("option", { value: v, text: v }));
}

function renderCoverage(sites) {
  const rows = Object.values(sites).sort((a, b) => (a.roaster || "").localeCompare(b.roaster || ""));
  const ok = rows.filter((s) => s.status === "ok").length;
  $("#coverage-summary").textContent = `Roaster coverage: ${ok} of ${rows.length} stores read cleanly today`;
  $("#coverage tbody").replaceChildren(...rows.map((s) => el("tr", {},
    el("td", {}, el("a", { href: s.url, target: "_blank", rel: "noopener", text: s.roaster || s.url })),
    el("td", { class: "num", text: s.coffee_variants ?? 0 }),
    el("td", { class: `st-${s.status}`, title: s.error || "", text: s.status === "ok" ? "ok" : s.status === "stale" ? `using ${s.last_ok} data` : "failed today" }),
  )));
}

async function main() {
  const res = await fetch("data/offers.json", { cache: "no-cache" });
  const data = await res.json();
  state.data = data;
  state.offers = data.offers.map((o) => ({
    ...o,
    _text: [o.title, o.roaster, o.size, ...(o.origins || []), ...(o.process || []), o.roast, o.desc, ...(o.options || [])]
      .filter(Boolean).join(" ").toLowerCase(),
  }));

  const inStock = state.offers.filter((o) => o.available && !o.sample);
  const roasters = new Set(inStock.map((o) => o.roaster));
  const updated = new Date(data.generated_at);
  $("#meta").textContent = `${inStock.length.toLocaleString()} coffees in stock from ${roasters.size} roasters · updated ${updated.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}`;

  const uniq = (fn) => [...new Set(state.offers.flatMap(fn))].filter(Boolean).sort();
  fillSelect("roaster", uniq((o) => [o.roaster]));
  fillSelect("origin", uniq((o) => o.origins || []));
  fillSelect("process", uniq((o) => o.process || []));
  fillSelect("roast", ["Light", "Medium-light", "Medium", "Medium-dark", "Dark"].filter((r) => state.offers.some((o) => o.roast === r)));

  const csvUrl = new URL("beangrid.csv", location.href).href;
  $("#datasette").href = `https://lite.datasette.io/?csv=${encodeURIComponent(csvUrl)}#/data/beangrid`;

  $("#history-links").replaceChildren(...(data.history_years || []).flatMap((y, i) => [
    i ? ", " : "", el("a", { href: `data/history/${y}.csv`, text: String(y) }),
  ]));

  renderTabs();
  renderDeals();
  readHash();
  renderTable(true);
  renderCoverage(data.sites || {});

  const form = $("#filters");
  let t;
  form.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => renderTable(true), 120); });
  form.addEventListener("submit", (e) => e.preventDefault());
  form.addEventListener("reset", () => setTimeout(() => renderTable(true), 0));
  $("#more").addEventListener("click", () => { state.shown += PAGE; renderTable(); });
}

main().catch((err) => {
  console.error(err);
  $("#meta").textContent = "Couldn't load today's data. Try refreshing.";
});
