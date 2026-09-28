/*
 * Copyright 2026 Carlos Infantes
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

"use strict";

/* One deployment, four dimensions: System, Experience, Quality, Freshness.

   The page reads `GET /api/v1/status` (schema_version 2) and draws what the
   collector judged. It never judges anything itself: every target it draws
   comes from the document's `targets`, so what is drawn and what was judged
   cannot drift apart.

   The DOM is built with createElement and textContent only. Probe details,
   source prefixes and provenances are data from the platform, and data is
   never parsed as markup. */

const SVG_NS = "http://www.w3.org/2000/svg";
const API = "/api/v1/status";
const REFRESH_EVERY_MS = 60 * 1000;
// A snapshot this old is itself a finding, so it stops being quiet about it.
const STALE_AFTER_MS = 10 * 60 * 1000;

// Status is never encoded by colour alone: every state carries a drawn glyph and
// a word. `unknown` ranks with `degraded`: not knowing is a reason to look.
// `severity` orders findings: a problem the collector could see outranks one
// it could not look at, so the verdict names a known cause when there is one.
const STATUS = {
  healthy: { label: "healthy", rank: 0, severity: 0 },
  unknown: { label: "unknown", rank: 1, severity: 1 },
  degraded: { label: "degraded", rank: 1, severity: 2 },
  down: { label: "down", rank: 2, severity: 3 },
};

const DIMENSIONS = {
  system: { label: "System", question: "Is it up?" },
  experience: { label: "Experience", question: "Are users served well?" },
  quality: { label: "Quality", question: "Is the data complete?" },
  freshness: { label: "Freshness", question: "Is the data current?" },
};
const DIMENSION_ORDER = Object.keys(DIMENSIONS);

const PROBE_LABELS = {
  dc_api: "API endpoint",
  dc_service: "Cloud Run service",
  spanner: "Spanner",
  schema: "Schema",
  version_consistency: "Image / schema match",
  frontend: "Frontend",
  errors: "Errors",
  latency: "Latency",
  saturation: "Saturation",
  counts: "Row counts",
  data_sources: "Data sources",
  import_status: "Import status",
  row_drift: "Row drift",
  ingestions: "Ingestions",
  ingestion_lock: "Ingestion lock",
  pending_uploads: "Pending uploads",
};

const decimal = new Intl.NumberFormat("en");
const oneDecimal = new Intl.NumberFormat("en", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const twoDecimals = new Intl.NumberFormat("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const clockFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});
// Spelled out rather than left to a locale, which may write "Sept" or put the
// month first. Always UTC, like every time on the page.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const doc = window.document;

let latest = null;
let lastFetchAt = 0;
let loading = false;
let showEveryCheck = false;
let announced = "";

/* ---------- DOM helpers ---------- */

function element(tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svgNode(tag, attributes) {
  const node = doc.createElementNS(SVG_NS, tag);
  Object.entries(attributes || {}).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}

function hidden(text) {
  return element("span", "visually-hidden", text);
}

function statusOf(value) {
  return STATUS[value] ? value : "unknown";
}

function rankOf(value) {
  return STATUS[statusOf(value)].rank;
}

function severityOf(value) {
  return STATUS[statusOf(value)].severity;
}

// The glyphs are authored here rather than borrowed from the text font, so all
// four share one silhouette weight at 10 px: circle healthy, triangle degraded,
// square down, dashed ring unknown.
function statusIcon(key) {
  const frame = svgNode("svg", {
    class: "state__icon",
    viewBox: "0 0 12 12",
    "aria-hidden": "true",
    focusable: "false",
    fill: "currentColor",
  });
  if (key === "healthy") {
    frame.append(svgNode("circle", { cx: 6, cy: 6, r: 4 }));
  } else if (key === "degraded") {
    frame.append(
      svgNode("path", {
        d: "M6 1.6 10.4 9.9H1.6Z",
        "stroke-linejoin": "round",
        stroke: "currentColor",
        "stroke-width": 1.1,
      })
    );
  } else if (key === "down") {
    frame.append(svgNode("rect", { x: 2, y: 2, width: 8, height: 8, rx: 1.2 }));
  } else {
    frame.append(
      svgNode("circle", {
        cx: 6,
        cy: 6,
        r: 3.6,
        fill: "none",
        stroke: "currentColor",
        "stroke-width": 1.6,
        "stroke-dasharray": "2.2 1.9",
      })
    );
  }
  return frame;
}

// Glyph + word. `quiet` keeps the word for assistive technology only, where the
// label beside it already makes the glyph unambiguous (component chips).
function state(value, word, quiet) {
  const key = statusOf(value);
  const node = element("span", `state state--${key}`);
  node.append(statusIcon(key));
  const text = word || STATUS[key].label;
  node.append(quiet ? hidden(text) : element("span", "state__word", text));
  return node;
}

/* ---------- formatting ---------- */

function parseStamp(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatStamp(value) {
  const parsed = parseStamp(value);
  if (!parsed) return null;
  const day = String(parsed.getUTCDate()).padStart(2, "0");
  return `${day} ${MONTHS[parsed.getUTCMonth()]}, ${clockFormat.format(parsed)}`;
}

function formatAge(milliseconds) {
  const minutes = Math.round(milliseconds / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

// An age stays in hours for two days, because "1 d" hides the difference
// between yesterday morning and yesterday night.
function formatHours(hours) {
  if (typeof hours !== "number") return null;
  if (hours < 1) return { value: decimal.format(Math.round(hours * 60)), unit: "min" };
  if (hours < 48) return { value: decimal.format(Math.round(hours)), unit: "h" };
  return { value: decimal.format(Math.round(hours / 24)), unit: "d" };
}

// Ingestions run for hours, and "4127 s" is a number you have to do arithmetic
// on before it means anything.
function formatDuration(seconds) {
  if (typeof seconds !== "number") return null;
  if (seconds < 60) return `${decimal.format(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} m ${String(Math.round(seconds % 60)).padStart(2, "0")} s`;
  const hours = Math.floor(minutes / 60);
  return `${hours} h ${String(minutes % 60).padStart(2, "0")} m`;
}

function formatMs(value) {
  if (typeof value !== "number") return "n/a";
  return `${decimal.format(Math.round(value))} ms`;
}

function formatPct(value, digits) {
  if (typeof value !== "number") return "n/a";
  const format = digits === 2 ? twoDecimals : digits === 1 ? oneDecimal : decimal;
  return `${format.format(value)} %`;
}

function formatRate(value) {
  if (typeof value !== "number") return "n/a";
  return value < 10 ? twoDecimals.format(value) : oneDecimal.format(value);
}

function plural(count, one, many) {
  return `${decimal.format(count)} ${count === 1 ? one : many || `${one}s`}`;
}

// Probe details are written as fragments, so the page supplies the full stop it
// needs to sit beside another sentence.
function terminate(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return "";
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function capitalise(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function probeLabel(id) {
  return PROBE_LABELS[id] || id;
}

function deploymentLabel(snapshot) {
  const deployment = snapshot.deployment || {};
  return deployment.label || deployment.id || "This deployment";
}

// A figure: the number in the mono face, its unit set smaller beside it.
function figureNode(className, value, unit) {
  const node = element("span", className);
  node.append(element("span", "figure__value", value));
  if (unit) node.append(element("span", "figure__unit", unit));
  return node;
}

/* ---------- reading the document ---------- */

function probesOf(snapshot) {
  const map = new Map();
  (snapshot.probes || []).forEach((probe) => map.set(probe.id, probe));
  return map;
}

function dimensionsOf(snapshot) {
  const byId = new Map((snapshot.dimensions || []).map((dimension) => [dimension.id, dimension]));
  return DIMENSION_ORDER.map((id) => byId.get(id) || { id, status: "unknown", probes: [] });
}

function probesIn(snapshot, dimension) {
  const probes = probesOf(snapshot);
  return (dimension.probes || []).map(
    (id) => probes.get(id) || { id, dimension: dimension.id, status: "unknown", detail: "" }
  );
}

// Every probe in tile order, worst first when sorted: the verdict and findings
// both read from this one list.
function orderedProbes(snapshot) {
  const list = [];
  dimensionsOf(snapshot).forEach((dimension) => {
    probesIn(snapshot, dimension).forEach((probe) => list.push({ ...probe, dimension: dimension.id }));
  });
  return list;
}

function findingsOf(snapshot) {
  return orderedProbes(snapshot)
    .map((probe, index) => ({ probe, index }))
    .filter(({ probe }) => rankOf(probe.status) > 0)
    .sort((a, b) => severityOf(b.probe.status) - severityOf(a.probe.status) || a.index - b.index)
    .map(({ probe }) => probe);
}

function targets(snapshot) {
  return snapshot.targets || {};
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function ingestionKind(entry) {
  const status = (entry.status || "").toUpperCase();
  if (status === "SUCCESS") return "success";
  if (entry.failure || status === "FAILED" || status === "FAILURE" || status === "CANCELLED") {
    return "failure";
  }
  return "running";
}

const INGESTION_STATE = { success: "healthy", failure: "down", running: "unknown" };

/* ---------- the verdict ---------- */

function verdictOf(snapshot) {
  const label = deploymentLabel(snapshot);
  const overall = statusOf(snapshot.overall);
  const findings = findingsOf(snapshot);
  const version = (snapshot.deployment || {}).dcp_version;

  if (rankOf(overall) === 0 && !findings.length) {
    return {
      headline: `${label} is healthy.`,
      check: null,
      cause: version ? `Serving platform ${version}. Every check passed.` : "Every check passed.",
    };
  }

  const headline =
    overall === "unknown" ? `${label} could not be fully read.` : `${label} is ${STATUS[overall].label}.`;
  const worst = findings[0];
  if (!worst) return { headline, check: null, cause: "" };

  const detail = terminate(worst.detail) || `${capitalise(probeLabel(worst.id))} is ${STATUS[statusOf(worst.status)].label}.`;
  const others = findings.length - 1;
  const tail = others > 0 ? ` Also ${plural(others, "other finding")}.` : "";
  return {
    headline,
    check: `${DIMENSIONS[worst.dimension] ? DIMENSIONS[worst.dimension].label : worst.dimension} · ${probeLabel(worst.id)}: `,
    cause: `${detail}${tail}`,
  };
}

// The region is live, so it is only touched when its words change: a refresh
// that finds the same verdict stays silent.
function setVerdict(headline, check, cause) {
  const key = `${headline}|${check || ""}|${cause || ""}`;
  if (key === announced) return;
  announced = key;
  doc.getElementById("headline").textContent = headline;
  const node = doc.getElementById("cause");
  node.replaceChildren();
  if (check) node.append(element("span", "verdict__check", check));
  if (cause) node.append(doc.createTextNode(cause));
}

/* ---------- tiles: one per dimension, each a link to its section ---------- */

function tile(dimension, figure, caption, extra) {
  const link = element("a", "tile");
  link.href = `#${dimension.id}`;
  const head = element("span", "tile__head");
  head.append(element("span", "tile__name", DIMENSIONS[dimension.id].label));
  head.append(state(dimension.status));
  link.append(head);
  link.append(figure);
  link.append(element("span", "tile__caption", caption));
  (extra || []).forEach((node) => node && link.append(node));
  // One sentence for assistive technology, instead of the tile's pieces run
  // together: name, state, figure, what it means, then any note.
  const value = [...figure.children].map((part) => part.textContent).join(" ");
  const notes = [...link.querySelectorAll(".tile__note")].map((note) => note.textContent);
  link.setAttribute(
    "aria-label",
    [
      `${DIMENSIONS[dimension.id].label}, ${STATUS[statusOf(dimension.status)].label}.`,
      `${value}, ${caption}.`,
      ...notes.map((note) => `${capitalise(note)}.`),
      "Go to section.",
    ].join(" ")
  );
  return link;
}

function systemTile(snapshot, dimension) {
  const probes = probesIn(snapshot, dimension);
  const healthy = probes.filter((probe) => statusOf(probe.status) === "healthy").length;
  const chips = element("span", "chips");
  probes.forEach((probe) => {
    const chip = element("span", "chip");
    chip.append(state(probe.status, null, true));
    chip.append(element("span", null, probeLabel(probe.id)));
    chips.append(chip);
  });
  return tile(
    dimension,
    figureNode("tile__figure", `${healthy}/${probes.length}`),
    "checks healthy",
    [chips]
  );
}

function experienceTile(snapshot, dimension) {
  const signals = snapshot.signals;
  const target = targets(snapshot).availability_pct;
  if (!signals || !signals.errors) {
    return tile(dimension, figureNode("tile__figure tile__figure--absent", "n/a"), "Cloud Monitoring could not be read");
  }
  const errors = signals.errors;
  const parts = ["availability, last hour"];
  if (isNumber(target)) parts.push(`target ${formatPct(target)}`);
  if (errors.judged === false) parts.push("low traffic — not judged");
  const extra = [];
  const latency = signals.latency || {};
  const trend = element("span", "tile__chart");
  trend.dataset.chart = "tile-latency";
  extra.push(trend);
  const latencyTarget = targets(snapshot).latency_p95_ms;
  extra.push(
    element(
      "span",
      "tile__note",
      `p95 latency ${formatMs(latency.p95_ms)}${isNumber(latencyTarget) ? ` · target ${formatMs(latencyTarget)}` : ""}`
    )
  );
  return tile(
    dimension,
    figureNode("tile__figure", isNumber(errors.availability_pct) ? twoDecimals.format(errors.availability_pct) : "n/a", "%"),
    parts.join(" · "),
    extra
  );
}

function sourceStats(snapshot) {
  const sources = snapshot.data_sources || [];
  const serving = sources.filter((source) => isNumber(source.rows) && source.rows > 0);
  const empty = sources.filter((source) => !source.files);
  return { sources, serving, empty, orphans: snapshot.unmatched_provenances || [] };
}

function qualityTile(snapshot, dimension) {
  const { sources, serving, empty, orphans } = sourceStats(snapshot);
  const notes = [];
  if (empty.length) notes.push(`${plural(empty.length, "source")} with no files`);
  if (orphans.length) notes.push(plural(orphans.length, "orphan provenance"));
  const bar = element("span", "tile__chart");
  bar.dataset.chart = "tile-coverage";
  return tile(
    dimension,
    figureNode("tile__figure", `${serving.length}/${sources.length}`),
    "sources serving rows",
    [bar, notes.length ? element("span", "tile__note", notes.join(" · ")) : null]
  );
}

function freshnessTile(snapshot, dimension) {
  const freshness = snapshot.freshness || {};
  const age = formatHours(freshness.age_hours);
  const pending = (freshness.pending_uploads || []).length;
  const timeline = element("span", "tile__chart");
  timeline.dataset.chart = "tile-timeline";
  const limit = targets(snapshot).ingestion_max_age_hours;
  const caption = isNumber(limit)
    ? `since the last successful ingestion · target ${decimal.format(limit)} h`
    : "since the last successful ingestion";
  return tile(
    dimension,
    age ? figureNode("tile__figure", age.value, age.unit) : figureNode("tile__figure tile__figure--absent", "n/a"),
    age ? caption : "no successful ingestion on record",
    [
      timeline,
      element(
        "span",
        "tile__note",
        pending ? `${plural(pending, "upload")} not yet ingested` : "no uploads waiting"
      ),
    ]
  );
}

function renderTiles(snapshot) {
  const host = doc.getElementById("tiles");
  const builders = {
    system: systemTile,
    experience: experienceTile,
    quality: qualityTile,
    freshness: freshnessTile,
  };
  host.replaceChildren(...dimensionsOf(snapshot).map((dimension) => builders[dimension.id](snapshot, dimension)));
  host.hidden = false;
}

/* ---------- findings ---------- */

function consoleLink(url, label) {
  if (!url) return null;
  let parsed;
  try {
    parsed = new URL(url);
  } catch (error) {
    return null;
  }
  // Only a real web page. A `javascript:` URL in the document would otherwise
  // be one click from running.
  if (parsed.protocol !== "https:") return null;
  const link = element("a", "console-link");
  link.href = parsed.href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.append(doc.createTextNode("Open in console"));
  link.append(element("span", "console-link__arrow", " ↗"));
  link.append(hidden(`: ${label} (opens in a new tab)`));
  return link;
}

function findingRow(probe, revealed) {
  const node = element("div", "finding");
  if (revealed) node.dataset.revealed = "true";
  node.append(element("span", "finding__dimension", DIMENSIONS[probe.dimension] ? DIMENSIONS[probe.dimension].label : probe.dimension));
  const what = element("div", "finding__what");
  const head = element("div", "finding__head");
  head.append(element("span", "finding__check", probeLabel(probe.id)));
  head.append(state(probe.status));
  what.append(head);
  if (probe.detail) what.append(element("p", "finding__detail", probe.detail));
  node.append(what);
  const link = consoleLink(probe.console_url, probeLabel(probe.id));
  node.append(link || element("span"));
  return node;
}

function renderFindings(snapshot) {
  const host = doc.getElementById("findings");
  const all = orderedProbes(snapshot);
  const findings = findingsOf(snapshot);
  doc.getElementById("findings-count").textContent = `${decimal.format(findings.length)} of ${plural(all.length, "check")}`;

  const rows = findings.map((probe) => findingRow(probe, false));
  if (showEveryCheck) {
    all.filter((probe) => rankOf(probe.status) === 0).forEach((probe) => rows.push(findingRow(probe, true)));
  }
  if (!rows.length) host.replaceChildren(element("p", "empty", "Every check passed."));
  else host.replaceChildren(...rows);
  doc.getElementById("findings-block").hidden = false;
}

/* ---------- sections ---------- */

function section(snapshot, dimension, note) {
  const node = element("section", "dimension");
  node.id = dimension.id;
  node.setAttribute("aria-labelledby", `${dimension.id}-title`);
  const head = element("div", "block__head");
  const title = element("h2", "dimension__title");
  title.id = `${dimension.id}-title`;
  title.append(element("span", null, DIMENSIONS[dimension.id].label));
  head.append(title);
  head.append(state(dimension.status));
  if (note) head.append(element("span", "block__meta", note));
  node.append(head);
  return node;
}

function subheading(text) {
  return element("h3", "sub", text);
}

// Scroll regions are reachable by keyboard and named, so a table wider than a
// phone can still be read without a pointer.
function scroller(child, label) {
  const node = element("div", "scroller");
  node.tabIndex = 0;
  node.setAttribute("role", "region");
  node.setAttribute("aria-label", label);
  node.append(child);
  return node;
}

// headers: [{ label, numeric, className }]; cells: string | null | { node, text, absent, className }
function dataTable(caption, headers, rows) {
  const table = element("table", "data");
  table.append(element("caption", "visually-hidden", caption));
  const head = element("thead");
  const headRow = element("tr");
  headers.forEach((header) => {
    const classes = [header.numeric ? "numeric" : "", header.className || ""].join(" ").trim();
    const cell = element("th", classes || null, header.label);
    cell.scope = "col";
    headRow.append(cell);
  });
  head.append(headRow);
  table.append(head);
  const body = element("tbody");
  rows.forEach((cells) => {
    const row = element("tr");
    cells.forEach((raw, index) => {
      const content = raw === null || raw === undefined ? { text: "—", absent: true } : typeof raw === "string" ? { text: raw } : raw;
      const header = headers[index] || {};
      const classes = [];
      if (header.numeric) classes.push("numeric");
      if (header.className) classes.push(header.className);
      if (content.absent) classes.push("absent");
      if (content.className) classes.push(content.className);
      const cell = element(index === 0 ? "th" : "td", classes.join(" ") || null);
      if (index === 0) cell.scope = "row";
      // Narrow screens stack each row into label/value pairs; the label
      // travels with the cell so no column is ever cut off.
      else if (header.label) cell.dataset.label = header.label;
      if (content.node) cell.append(content.node);
      else cell.textContent = content.text;
      row.append(cell);
    });
    body.append(row);
  });
  table.append(body);
  return scroller(table, caption);
}

/* System: each check with the time it took against its budget. */

const BUDGET_WARN = 0.5;
const BUDGET_CRITICAL = 0.85;

// `402 ms` says nothing about whether that is comfortable. Against a budget it
// does. Linear, because the question is literally "how much of the allowance
// did this consume".
function timing(elapsed, budget) {
  if (!isNumber(budget) || budget <= 0) {
    const bare = element("span", "timing");
    bare.append(element("span", "timing__value timing__value--derived", isNumber(elapsed) && elapsed > 0 ? formatMs(elapsed) : "derived"));
    return bare;
  }
  const share = Math.min((elapsed || 0) / budget, 1);
  const level = share >= BUDGET_CRITICAL ? "critical" : share >= BUDGET_WARN ? "warning" : "calm";
  const node = element("span", `timing timing--${level}`);
  node.append(element("span", "timing__value", formatMs(elapsed)));
  const track = element("span", "timing__track");
  const fill = element("span", "timing__fill");
  // Floored so a fast check still reads as a mark, not as missing data.
  fill.style.width = `max(2px, ${(share * 100).toFixed(1)}%)`;
  track.append(fill);
  node.append(track);
  node.setAttribute("role", "img");
  node.setAttribute(
    "aria-label",
    `${formatMs(elapsed)}, ${oneDecimal.format(share * 100)} % of its ${formatMs(budget)} budget`
  );
  return node;
}

function checkList(probes) {
  const list = element("ul", "checks");
  probes.forEach((probe) => {
    const item = element("li", "check");
    const name = element("span", "check__name");
    name.append(element("span", "check__label", probeLabel(probe.id)));
    if (probe.detail) name.append(element("span", "check__detail", probe.detail));
    item.append(name);
    item.append(state(probe.status));
    item.append(timing(probe.elapsed_ms, probe.budget_ms));
    list.append(item);
  });
  return list;
}

function renderSystem(snapshot, dimension) {
  const node = section(snapshot, dimension, DIMENSIONS.system.question);
  node.append(checkList(probesIn(snapshot, dimension)));
  return node;
}

/* Experience: the golden signals. */

function signalCard({ title, value, unit, chart, notes }) {
  const card = element("div", "signal");
  card.append(element("h3", "signal__title", title));
  card.append(figureNode("signal__figure", value, unit));
  if (chart) card.append(chart);
  (notes || []).forEach((note) => note && card.append(element("p", "signal__note", note)));
  return card;
}

function chartHost(kind) {
  const host = element("div", "chart");
  host.dataset.chart = kind;
  return host;
}

function renderExperience(snapshot, dimension) {
  const signals = snapshot.signals;
  const node = section(
    snapshot,
    dimension,
    signals ? `golden signals · last ${decimal.format(signals.window_minutes || 60)} min` : DIMENSIONS.experience.question
  );

  if (!signals) {
    const empty = element("div", "empty-state");
    empty.append(element("p", "empty-state__title", "The golden signals could not be read for this snapshot."));
    empty.append(
      element(
        "p",
        "empty-state__body",
        "They come from Cloud Monitoring, which did not answer. The checks below say what the collector saw. If this persists, check that its service account holds roles/monitoring.viewer on the project."
      )
    );
    node.append(empty);
    node.append(checkList(probesIn(snapshot, dimension)));
    return node;
  }

  const t = targets(snapshot);
  const traffic = signals.traffic || {};
  const errors = signals.errors || {};
  const latency = signals.latency || {};
  const notJudged = "low traffic — not judged";
  const minimum = isNumber(t.min_requests_per_hour) ? ` (under ${decimal.format(t.min_requests_per_hour)} requests an hour)` : "";

  const grid = element("div", "signals");
  grid.append(
    signalCard({
      title: "Traffic",
      value: formatRate(traffic.rps),
      unit: "req/s",
      chart: chartHost("traffic"),
      notes: [isNumber(traffic.requests) ? `${decimal.format(traffic.requests)} requests in the window` : null],
    })
  );
  const errorBudget = isNumber(t.availability_pct) ? 100 - t.availability_pct : null;
  grid.append(
    signalCard({
      title: "Errors (5xx)",
      value: isNumber(errors.error_pct) ? twoDecimals.format(errors.error_pct) : "n/a",
      unit: "%",
      chart: chartHost("errors"),
      notes: [
        `availability ${formatPct(errors.availability_pct, 2)}${isNumber(t.availability_pct) ? ` · target ${formatPct(t.availability_pct)}` : ""}`,
        isNumber(errorBudget) ? `line: ${formatPct(Number(errorBudget.toFixed(3)))} error budget` : null,
        errors.judged === false ? `${notJudged}${minimum}` : null,
      ],
    })
  );
  grid.append(
    signalCard({
      title: "Latency p95",
      value: isNumber(latency.p95_ms) ? decimal.format(Math.round(latency.p95_ms)) : "n/a",
      unit: "ms",
      chart: chartHost("latency"),
      notes: [
        `p50 ${formatMs(latency.p50_ms)} · p99 ${formatMs(latency.p99_ms)}`,
        isNumber(t.latency_p95_ms) ? `line: ${formatMs(t.latency_p95_ms)} target` : null,
        latency.judged === false ? `${notJudged}${minimum}` : null,
      ],
    })
  );
  node.append(grid);

  node.append(subheading("Saturation"));
  const gauges = element("div", "gauges");
  gauges.dataset.chart = "gauges";
  node.append(gauges);
  return node;
}

/* Quality: coverage, sources, rows per table, imports. */

// A colour follows its source, never its rank, so a source keeps its hue when
// another one stops serving. Eight validated slots; past eight, sources share
// a neutral "other" mark rather than cycling hues.
const SLOTS = 8;

function sourceSlots(snapshot) {
  const slots = new Map();
  (snapshot.data_sources || []).forEach((source, index) => {
    slots.set(source.prefix, index < SLOTS ? String(index + 1) : "other");
  });
  return slots;
}

function sourceName(text, glyph) {
  const label = element("span", "source");
  label.append(glyph);
  const name = element("span", "source__name", text);
  name.title = text;
  label.append(name);
  return label;
}

function swatch(slot) {
  const node = element("span", "swatch");
  node.dataset.slot = slot;
  node.setAttribute("aria-hidden", "true");
  return node;
}

function renderSourcesTable(snapshot) {
  const { sources, serving } = sourceStats(snapshot);
  const slots = sourceSlots(snapshot);
  const total = serving.reduce((sum, source) => sum + source.rows, 0);
  const rows = sources.map((source) => {
    const serves = isNumber(source.rows) && source.rows > 0;
    const glyph = serves ? swatch(slots.get(source.prefix)) : state("degraded", source.files ? "serves no rows" : "no files", true);
    return [
      { node: sourceName(source.prefix, glyph) },
      isNumber(source.files) ? decimal.format(source.files) : null,
      formatStamp(source.last_updated),
      isNumber(source.rows) ? decimal.format(source.rows) : null,
      serves && total > 0 ? `${oneDecimal.format((source.rows / total) * 100)} %` : null,
    ];
  });
  (snapshot.unmatched_provenances || []).forEach((entry) => {
    const label = sourceName(entry.provenance, swatch("none"));
    label.append(element("span", "source__note", "orphan · no configured prefix"));
    rows.push([{ node: label, className: "orphan" }, null, null, isNumber(entry.rows) ? decimal.format(entry.rows) : null, null]);
  });
  if (!rows.length) return element("p", "empty", "No data sources are configured.");
  return dataTable(
    "Rows served by source",
    [
      { label: "Source", className: "col-source" },
      { label: "Files", numeric: true },
      { label: "Last upload", className: "col-stamp" },
      { label: "Rows served", numeric: true },
      { label: "Share", numeric: true },
    ],
    rows
  );
}

function historyFor(snapshot, table) {
  return (snapshot.count_history || [])
    .filter((entry) => isNumber(entry[table]))
    .map((entry) => ({ at: entry.completed_at, value: entry[table] }));
}

function renderCountsTable(snapshot) {
  const counts = snapshot.counts || {};
  const names = Object.keys(counts).sort((a, b) => (counts[b] || 0) - (counts[a] || 0) || a.localeCompare(b));
  if (!names.length) return element("p", "empty", "No row counts in this snapshot.");
  const rows = names.map((name) => {
    const history = historyFor(snapshot, name);
    let change = null;
    if (history.length >= 2) {
      const previous = history[history.length - 2].value;
      const last = history[history.length - 1].value;
      if (previous > 0) {
        const pct = ((last - previous) / previous) * 100;
        change = { text: `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${oneDecimal.format(Math.abs(pct))} %` };
      }
    }
    const trend = element("span", "trend");
    if (history.length >= 2) {
      trend.dataset.chart = "count";
      trend.dataset.table = name;
    }
    return [name, isNumber(counts[name]) ? decimal.format(counts[name]) : null, change, { node: trend, className: "trend-cell" }];
  });
  return dataTable(
    "Rows per table",
    [
      { label: "Table" },
      { label: "Rows", numeric: true },
      { label: "Last change", numeric: true },
      { label: "Ingestions", className: "col-trend" },
    ],
    rows
  );
}

function renderImports(snapshot) {
  const imports = snapshot.imports;
  const node = element("div", "imports");
  if (!imports) {
    node.append(element("p", "empty", "Import status is not available on this platform version."));
    return node;
  }
  const failed = imports.failed || [];
  const line = element("p", "imports__line");
  const status = failed.length ? "down" : imports.in_progress ? "unknown" : "healthy";
  line.append(state(status, failed.length ? "failed" : imports.in_progress ? "in progress" : "succeeded", true));
  line.append(
    doc.createTextNode(
      `${decimal.format(imports.succeeded || 0)} of ${plural(imports.total || 0, "import")} succeeded`
    )
  );
  if (imports.in_progress) line.append(doc.createTextNode(` · ${decimal.format(imports.in_progress)} in progress`));
  if (failed.length) line.append(doc.createTextNode(` · ${decimal.format(failed.length)} failed`));
  node.append(line);
  if (failed.length) {
    const list = element("ul", "imports__failed");
    failed.forEach((entry) => {
      const text = typeof entry === "string" ? entry : [entry.import || entry.name || entry.id, entry.state].filter(Boolean).join(" · ");
      list.append(element("li", null, text || "unnamed import"));
    });
    node.append(list);
  }
  return node;
}

function renderQuality(snapshot, dimension) {
  const node = section(snapshot, dimension, DIMENSIONS.quality.question);
  const columns = element("div", "columns");

  const left = element("div", "column");
  left.append(subheading("Rows served by source"));
  const coverage = element("div", "chart chart--coverage");
  coverage.dataset.chart = "coverage";
  left.append(coverage);
  left.append(renderSourcesTable(snapshot));
  columns.append(left);

  const right = element("div", "column");
  right.append(subheading("Rows per table"));
  right.append(renderCountsTable(snapshot));
  const drop = targets(snapshot).max_row_drop_pct;
  if (isNumber(drop)) {
    right.append(
      element("p", "caption", `Last change is against the previous successful ingestion; a drop of more than ${formatPct(drop)} is a finding.`)
    );
  }
  right.append(subheading("Imports"));
  right.append(renderImports(snapshot));
  columns.append(right);

  node.append(columns);
  return node;
}

/* Freshness: the ingestion timeline and the recent runs. */

function renderFreshness(snapshot, dimension) {
  const node = section(snapshot, dimension, DIMENSIONS.freshness.question);
  const timeline = element("div", "chart chart--timeline");
  timeline.dataset.chart = "timeline";
  node.append(timeline);

  const freshness = snapshot.freshness || {};
  const lock = freshness.lock || {};
  const facts = element("p", "caption");
  const lockText = lock.held
    ? `Lock: held${lock.owner ? ` by ${lock.owner}` : ""}${lock.since ? ` since ${formatStamp(lock.since)} UTC` : ""}.`
    : "Lock: free.";
  const limit = targets(snapshot).ingestion_max_age_hours;
  const ageText = isNumber(limit)
    ? ` An ingestion older than ${decimal.format(limit)} h is a finding.`
    : " No maximum age is set, so the age is shown but not judged.";
  facts.textContent = `${lockText}${ageText}`;
  node.append(facts);

  node.append(subheading("Recent ingestions"));
  const ingestions = snapshot.ingestions || [];
  if (!ingestions.length) {
    node.append(element("p", "empty", "No ingestion history yet."));
    return node;
  }
  node.append(
    dataTable(
      "Recent ingestions",
      [
        { label: "Started", className: "col-stamp" },
        { label: "Result" },
        { label: "Stage" },
        { label: "Workflow" },
        { label: "Duration", numeric: true },
      ],
      ingestions.map((entry) => {
        const kind = ingestionKind(entry);
        return [
          formatStamp(entry.creation),
          { node: state(INGESTION_STATE[kind], entry.status || kind) },
          entry.stage || null,
          entry.workflow_state || null,
          formatDuration(entry.execution_seconds),
        ];
      })
    )
  );
  return node;
}

function renderDimensions(snapshot) {
  const builders = {
    system: renderSystem,
    experience: renderExperience,
    quality: renderQuality,
    freshness: renderFreshness,
  };
  const host = doc.getElementById("dimensions");
  host.replaceChildren(...dimensionsOf(snapshot).map((dimension) => builders[dimension.id](snapshot, dimension)));
}

/* ---------- charts ----------

   Inline SVG, drawn at the width the layout gives it and redrawn when that
   width changes. Thin marks, hairline axes, a 2 px gap between stacked
   segments and a 2 px surface ring around dots. Colours come from CSS classes
   bound to the theme tokens, so a theme switch needs no redraw.

   Every chart has a text equivalent: an aria-label that summarises it, and a
   table elsewhere on the page that is its table view. Tooltips enhance and
   never gate — the same readout appears on hover and on keyboard focus. */

const tooltip = { node: null, owner: null };

function showTooltip(owner, rect, lines) {
  const node = tooltip.node || (tooltip.node = doc.getElementById("tooltip"));
  if (!node) return;
  tooltip.owner = owner;
  node.replaceChildren(
    ...lines.map((line) => {
      const row = element("span", "tooltip__row");
      if (line.value) row.append(element("span", "tooltip__value", line.value));
      if (line.label) row.append(element("span", "tooltip__label", line.label));
      return row;
    })
  );
  node.hidden = false;
  // Measured after it has content, then kept inside the viewport.
  const box = node.getBoundingClientRect();
  const margin = 8;
  let left = rect.left + rect.width / 2 - box.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - box.width - margin));
  let top = rect.top - box.height - margin;
  if (top < margin) top = rect.bottom + margin;
  node.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
  node.dataset.visible = "true";
}

function hideTooltip(owner) {
  const node = tooltip.node || doc.getElementById("tooltip");
  if (!node || (owner && tooltip.owner !== owner)) return;
  tooltip.owner = null;
  node.dataset.visible = "false";
  node.hidden = true;
}

function pointRect(svg, x, y) {
  const box = svg.getBoundingClientRect();
  return { left: box.left + x - 1, top: box.top + y - 6, width: 2, height: 12, bottom: box.top + y + 6 };
}

function frame(width, height, className) {
  return svgNode("svg", {
    class: `plot ${className || ""}`.trim(),
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    focusable: "false",
  });
}

function describe(svg, label, interactive) {
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  if (interactive) {
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("focusable", "true");
    svg.classList.add("plot--interactive");
  }
  return svg;
}

function extent(values) {
  const finite = values.filter(isNumber);
  if (!finite.length) return [0, 1];
  return [Math.min(...finite), Math.max(...finite)];
}

/* A sparkline: one series, a hairline baseline, the target as a line where the
   document has one, and the latest value as an end dot. Interactive ones get a
   crosshair that snaps to the nearest minute, driven by pointer or arrow keys. */
function sparkline({ width, height, values, target, zeroBased, area, interactive, label, readout, axis }) {
  const axisBand = axis ? 14 : 0;
  const top = 5;
  const plotHeight = height - axisBand - top - 4;
  const left = 1;
  const right = 5;
  const n = values.length;
  const svg = frame(width, height, "spark");
  if (!n) return describe(svg, `${label}: no data in the window.`, false);

  let [lo, hi] = extent(values.concat(isNumber(target) ? [target] : []));
  if (zeroBased) lo = Math.min(0, lo);
  if (hi === lo) hi = lo + 1;
  const span = hi - lo;
  hi += zeroBased ? span * 0.08 : span * 0.1;
  if (!zeroBased) lo -= span * 0.1;

  const x = (index) => left + (n === 1 ? 0 : (index / (n - 1)) * (width - left - right));
  const y = (value) => top + plotHeight - ((value - lo) / (hi - lo)) * plotHeight;
  const base = top + plotHeight;

  svg.append(svgNode("line", { class: "plot__axis", x1: 0, x2: width, y1: base + 0.5, y2: base + 0.5 }));

  const points = values.map((value, index) => (isNumber(value) ? [x(index), y(value)] : null));
  let path = "";
  let pen = false;
  points.forEach((point) => {
    if (!point) {
      pen = false;
      return;
    }
    path += `${pen ? "L" : "M"}${point[0].toFixed(1)},${point[1].toFixed(1)}`;
    pen = true;
  });

  if (area) {
    const first = points.findIndex(Boolean);
    const last = points.length - 1 - [...points].reverse().findIndex(Boolean);
    if (first >= 0 && !points.slice(first, last + 1).includes(null)) {
      svg.append(
        svgNode("path", {
          class: "plot__area",
          d: `${path}L${points[last][0].toFixed(1)},${base}L${points[first][0].toFixed(1)},${base}Z`,
        })
      );
    }
  }

  if (isNumber(target)) {
    const ty = Math.round(y(target)) + 0.5;
    svg.append(svgNode("line", { class: "plot__target", x1: 0, x2: width, y1: ty, y2: ty }));
  }

  svg.append(svgNode("path", { class: "plot__line", d: path }));

  const lastIndex = points.length - 1 - [...points].reverse().findIndex(Boolean);
  if (lastIndex < points.length && points[lastIndex]) {
    svg.append(svgNode("circle", { class: "plot__dot", cx: points[lastIndex][0], cy: points[lastIndex][1], r: 3 }));
  }

  if (axis) {
    const ticks = svgNode("g", { class: "plot__ticks" });
    const start = svgNode("text", { x: 0, y: height - 2 });
    start.textContent = axis[0];
    const end = svgNode("text", { x: width, y: height - 2, "text-anchor": "end" });
    end.textContent = axis[1];
    ticks.append(start, end);
    svg.append(ticks);
  }

  describe(svg, label, interactive);
  if (!interactive) return svg;

  const cross = svgNode("line", { class: "plot__crosshair", x1: 0, x2: 0, y1: top - 2, y2: base, visibility: "hidden" });
  const marker = svgNode("circle", { class: "plot__dot plot__dot--hover", r: 3.5, cx: 0, cy: 0, visibility: "hidden" });
  svg.append(cross, marker);

  let current = null;
  const select = (index) => {
    if (index === null || !points[index]) {
      current = null;
      cross.setAttribute("visibility", "hidden");
      marker.setAttribute("visibility", "hidden");
      hideTooltip(svg);
      return;
    }
    current = index;
    const [px, py] = points[index];
    cross.setAttribute("x1", px);
    cross.setAttribute("x2", px);
    cross.setAttribute("visibility", "visible");
    marker.setAttribute("cx", px);
    marker.setAttribute("cy", py);
    marker.setAttribute("visibility", "visible");
    showTooltip(svg, pointRect(svg, px, py), readout(index));
  };
  const nearest = (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const index = Math.round(((px - left) / (width - left - right)) * (n - 1));
    return Math.max(0, Math.min(n - 1, index));
  };
  svg.addEventListener("pointermove", (event) => select(nearest(event)));
  svg.addEventListener("pointerleave", () => {
    if (doc.activeElement !== svg) select(null);
  });
  svg.addEventListener("focus", () => select(lastIndex));
  svg.addEventListener("blur", () => select(null));
  svg.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (step) {
      event.preventDefault();
      select(Math.max(0, Math.min(n - 1, (current === null ? lastIndex : current) + step)));
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      select(event.key === "Home" ? 0 : n - 1);
    } else if (event.key === "Escape") {
      select(null);
    }
  });
  return svg;
}

/* A semicircular gauge with the limit as a tick on the arc. */
function gauge({ name, value, max, limit, valueText, limitText, over, detail }) {
  const node = element("div", "gauge");
  const width = 136;
  const height = 80;
  const cx = width / 2;
  const cy = 70;
  const r = 54;
  const svg = frame(width, height, "gauge__plot");
  const point = (fraction, radius) => {
    const angle = Math.PI * (1 - fraction);
    return [cx + radius * Math.cos(angle), cy - radius * Math.sin(angle)];
  };
  const arc = (from, to, className) => {
    const [x0, y0] = point(from, r);
    const [x1, y1] = point(to, r);
    return svgNode("path", {
      class: className,
      d: `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 0 1 ${x1.toFixed(2)},${y1.toFixed(2)}`,
    });
  };
  svg.append(arc(0, 1, "gauge__track"));
  const known = isNumber(value) && isNumber(max) && max > 0;
  if (known) {
    const fraction = Math.max(0, Math.min(value / max, 1));
    if (fraction > 0) svg.append(arc(0, Math.max(fraction, 0.01), `gauge__fill${over ? " gauge__fill--over" : ""}`));
  }
  if (isNumber(limit) && isNumber(max) && max > 0) {
    const fraction = Math.max(0, Math.min(limit / max, 1));
    const [x0, y0] = point(fraction, r - 7);
    const [x1, y1] = point(fraction, r + 8);
    svg.append(svgNode("line", { class: "gauge__limit", x1: x0, y1: y0, x2: x1, y2: y1 }));
  }
  const text = svgNode("text", { class: "gauge__value", x: cx, y: cy - 4, "text-anchor": "middle" });
  text.textContent = known ? valueText : "n/a";
  svg.append(text);

  const summary = known
    ? `${name}: ${valueText}${limitText ? `, ${limitText}` : ""}. ${over ? "At or above its limit." : "Within its limit."}`
    : `${name}: not available.`;
  describe(svg, summary, true);
  const lines = () => [
    { value: known ? valueText : "n/a", label: name },
    ...(limitText ? [{ label: limitText }] : []),
    ...(detail ? [{ label: detail }] : []),
  ];
  const open = () => showTooltip(svg, svg.getBoundingClientRect(), lines());
  svg.addEventListener("pointerenter", open);
  svg.addEventListener("pointerleave", () => {
    if (doc.activeElement !== svg) hideTooltip(svg);
  });
  svg.addEventListener("focus", open);
  svg.addEventListener("blur", () => hideTooltip(svg));

  node.append(svg);
  node.append(element("span", "gauge__name", name));
  const foot = element("span", "gauge__foot");
  if (limitText) foot.append(element("span", "gauge__limit-text", limitText));
  if (known) foot.append(state(over ? "degraded" : "healthy", over ? "at limit" : "within"));
  node.append(foot);
  return node;
}

/* A stacked proportion: one segment per serving source, 2 px surface gaps. */
function coverageBar({ width, height, snapshot, interactive }) {
  const { serving } = sourceStats(snapshot);
  const slots = sourceSlots(snapshot);
  const svg = frame(width, height, "coverage");
  const total = serving.reduce((sum, source) => sum + source.rows, 0);
  if (!serving.length || total <= 0) {
    svg.append(svgNode("rect", { class: "coverage__empty", x: 0.5, y: 0.5, width: width - 1, height: height - 1, rx: 2 }));
    return describe(svg, "No source is serving rows.", false);
  }
  const gap = 2;
  const minimum = 2;
  const usable = width - gap * (serving.length - 1);
  let cursor = 0;
  const parts = serving.map((source) => {
    const share = source.rows / total;
    return { source, share, width: Math.max(minimum, share * usable) };
  });
  // Floors can overshoot the track; take the excess back from the widest.
  const excess = parts.reduce((sum, part) => sum + part.width, 0) - usable;
  if (excess > 0) parts.reduce((a, b) => (b.width > a.width ? b : a)).width -= excess;
  parts.forEach((part) => {
    const rect = svgNode("rect", {
      class: `coverage__segment slot-${slots.get(part.source.prefix)}`,
      x: cursor.toFixed(2),
      y: 0,
      width: Math.max(part.width, 1).toFixed(2),
      height,
      rx: 1.5,
    });
    if (interactive) {
      const lines = [
        { value: `${oneDecimal.format(part.share * 100)} %`, label: part.source.prefix },
        { label: `${decimal.format(part.source.rows)} rows` },
      ];
      rect.addEventListener("pointerenter", () => showTooltip(rect, rect.getBoundingClientRect(), lines));
      rect.addEventListener("pointerleave", () => hideTooltip(rect));
    }
    svg.append(rect);
    cursor += part.width + gap;
  });
  return describe(
    svg,
    `Share of rows served by source: ${parts
      .map((part) => `${part.source.prefix} ${oneDecimal.format(part.share * 100)} %`)
      .join(", ")}.`,
    false
  );
}

/* The ingestion timeline: 30 days to now. A disc per success, a square per
   failure, a ring per run still going, and an open square above the axis for
   every upload the platform has not ingested yet. */
const DAY_MS = 24 * 60 * 60 * 1000;
const TIMELINE_DAYS = 30;

function timelineMarks(snapshot) {
  const now = parseStamp(snapshot.generated_at) || new Date();
  const from = now.getTime() - TIMELINE_DAYS * DAY_MS;
  const runs = (snapshot.ingestions || [])
    .map((entry) => ({ entry, at: parseStamp(entry.creation), kind: ingestionKind(entry) }))
    .filter((mark) => mark.at);
  const pending = ((snapshot.freshness || {}).pending_uploads || [])
    .map((upload) => ({ upload, at: parseStamp(upload.last_updated), kind: "pending" }))
    .filter((mark) => mark.at);
  const inside = (mark) => mark.at.getTime() >= from && mark.at.getTime() <= now.getTime() + 60000;
  return {
    now,
    from,
    marks: runs.filter(inside).concat(pending.filter(inside)).sort((a, b) => a.at - b.at),
    outside: runs.filter((mark) => !inside(mark)).length,
  };
}

function markShape(kind, cx, cy, size) {
  const s = size || 9;
  if (kind === "success") return svgNode("circle", { class: "mark mark--success", cx, cy, r: s / 2 });
  if (kind === "failure") {
    return svgNode("rect", { class: "mark mark--failure", x: cx - s / 2, y: cy - s / 2, width: s, height: s, rx: 1 });
  }
  if (kind === "running") return svgNode("circle", { class: "mark mark--running", cx, cy, r: s / 2 - 0.75 });
  const p = s * 0.72;
  return svgNode("rect", { class: "mark mark--pending", x: cx - p / 2, y: cy - p / 2, width: p, height: p });
}

const MARK_WORDS = {
  success: "successful ingestion",
  failure: "failed ingestion",
  running: "ingestion in progress",
  pending: "upload not yet ingested",
};

function markLines(mark) {
  if (mark.kind === "pending") {
    return [
      { value: `${formatStamp(mark.upload.last_updated)} UTC`, label: `${mark.upload.prefix}: ${MARK_WORDS.pending}` },
    ];
  }
  const entry = mark.entry;
  const duration = formatDuration(entry.execution_seconds);
  return [
    { value: `${formatStamp(entry.creation)} UTC`, label: `${entry.status || mark.kind}${entry.stage ? ` · ${entry.stage}` : ""}` },
    ...(duration ? [{ label: `took ${duration}` }] : []),
  ];
}

function timeline({ width, height, snapshot, interactive, compact }) {
  const { now, from, marks, outside } = timelineMarks(snapshot);
  const svg = frame(width, height, "timeline");
  const left = 6;
  const right = 8;
  const axisY = compact ? Math.round(height * 0.55) : Math.round(height * 0.55);
  const x = (time) => left + ((time - from) / (now.getTime() - from)) * (width - left - right);
  const nowX = Math.round(x(now.getTime())) + 0.5;

  svg.append(svgNode("line", { class: "plot__axis", x1: 0, x2: width, y1: axisY + 0.5, y2: axisY + 0.5 }));

  const ticks = svgNode("g", { class: "plot__ticks" });
  const tickDays = compact ? [30] : [30, 20, 10];
  tickDays.forEach((days) => {
    const tx = x(now.getTime() - days * DAY_MS);
    if (!compact) svg.append(svgNode("line", { class: "plot__tick", x1: tx, x2: tx, y1: axisY + 3, y2: axisY + 7 }));
    const label = svgNode("text", { x: Math.max(0, tx - (days === 30 ? left : 0)), y: height - 2, "text-anchor": days === 30 ? "start" : "middle" });
    label.textContent = `${days} d ago`;
    ticks.append(label);
  });
  const nowLabel = svgNode("text", { x: width, y: height - 2, "text-anchor": "end" });
  nowLabel.textContent = "now";
  ticks.append(nowLabel);
  svg.append(ticks);

  const limit = targets(snapshot).ingestion_max_age_hours;
  if (isNumber(limit) && limit > 0 && limit < TIMELINE_DAYS * 24) {
    const lx = Math.round(x(now.getTime() - limit * 60 * 60 * 1000)) + 0.5;
    svg.append(svgNode("line", { class: "plot__target", x1: lx, x2: lx, y1: 3, y2: axisY + 8 }));
  }

  svg.append(svgNode("line", { class: "timeline__now", x1: nowX, x2: nowX, y1: 2, y2: axisY + 8 }));

  const size = compact ? 8 : 10;
  const placed = marks.map((mark) => {
    const cx = Math.min(x(mark.at.getTime()), width - right);
    const cy = mark.kind === "pending" ? axisY - (compact ? 14 : 16) : axisY;
    const shape = markShape(mark.kind, cx, cy, size);
    svg.append(shape);
    return { mark, cx, cy, shape };
  });

  const counts = { success: 0, failure: 0, running: 0, pending: 0 };
  marks.forEach((mark) => {
    counts[mark.kind] += 1;
  });
  const summary = [
    `Ingestions in the last ${TIMELINE_DAYS} days: ${plural(counts.success, "success", "successes")}, ${plural(counts.failure, "failure")}`,
    counts.running ? `, ${decimal.format(counts.running)} in progress` : "",
    `. ${plural(counts.pending, "upload")} not yet ingested.`,
    outside ? ` ${plural(outside, "older run")} not shown.` : "",
  ].join("");
  describe(svg, summary, interactive && placed.length > 0);
  if (!interactive || !placed.length) return svg;

  const ring = svgNode("circle", { class: "timeline__focus", r: size, cx: 0, cy: 0, visibility: "hidden" });
  svg.append(ring);
  let current = null;
  const select = (index) => {
    if (index === null) {
      current = null;
      ring.setAttribute("visibility", "hidden");
      hideTooltip(svg);
      return;
    }
    current = index;
    const { mark, cx, cy } = placed[index];
    ring.setAttribute("cx", cx);
    ring.setAttribute("cy", cy);
    ring.setAttribute("visibility", "visible");
    showTooltip(svg, pointRect(svg, cx, cy), markLines(mark));
  };
  // The pointer only has to be closest, not on the 9 px mark itself.
  svg.addEventListener("pointermove", (event) => {
    const box = svg.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    const py = ((event.clientY - box.top) / box.height) * height;
    let best = null;
    let distance = Infinity;
    placed.forEach((item, index) => {
      const d = Math.hypot(item.cx - px, item.cy - py);
      if (d < distance) {
        distance = d;
        best = index;
      }
    });
    select(distance <= 16 ? best : null);
  });
  svg.addEventListener("pointerleave", () => {
    if (doc.activeElement !== svg) select(null);
  });
  svg.addEventListener("focus", () => select(placed.length - 1));
  svg.addEventListener("blur", () => select(null));
  svg.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (step) {
      event.preventDefault();
      const base = current === null ? placed.length - 1 : current;
      select((base + step + placed.length) % placed.length);
    } else if (event.key === "Escape") {
      select(null);
    }
  });
  return svg;
}

function timelineLegend(snapshot) {
  const legend = element("ul", "legend");
  ["success", "failure", "running", "pending"].forEach((kind) => {
    const item = element("li", "legend__item");
    const key = frame(12, 12, "legend__key");
    key.setAttribute("aria-hidden", "true");
    key.append(markShape(kind, 6, 6, 9));
    item.append(key);
    item.append(element("span", null, MARK_WORDS[kind]));
    legend.append(item);
  });
  const limit = targets(snapshot).ingestion_max_age_hours;
  if (isNumber(limit) && limit > 0) {
    const item = element("li", "legend__item");
    const key = frame(12, 12, "legend__key");
    key.setAttribute("aria-hidden", "true");
    key.append(svgNode("line", { class: "plot__target", x1: 6.5, x2: 6.5, y1: 0, y2: 12 }));
    item.append(key);
    item.append(element("span", null, `maximum age, ${decimal.format(limit)} h`));
    legend.append(item);
  }
  return legend;
}

/* ---------- series readouts ---------- */

function minuteLabels(snapshot, count) {
  const signals = snapshot.signals || {};
  const step = (signals.step_seconds || 60) * 1000;
  const end = parseStamp(snapshot.generated_at);
  return (index) => {
    if (!end) return `minute ${index + 1}`;
    return `${clockFormat.format(new Date(end.getTime() - (count - 1 - index) * step))} UTC`;
  };
}

function seriesSummary(values, format, target, targetText) {
  const finite = values.filter(isNumber);
  if (!finite.length) return "no data in the window.";
  const [lo, hi] = extent(finite);
  let text = `from ${format(lo)} to ${format(hi)}, latest ${format(finite[finite.length - 1])}.`;
  if (isNumber(target)) {
    const above = finite.filter((value) => value > target).length;
    text += ` ${decimal.format(above)} of ${plural(finite.length, "minute")} above the ${targetText} target.`;
  }
  return text;
}

function signalChart(snapshot, kind, width) {
  const signals = snapshot.signals || {};
  const t = targets(snapshot);
  const windowText = `last ${decimal.format(signals.window_minutes || 60)} min`;
  let values = [];
  let target = null;
  let format = (value) => decimal.format(value);
  let name = "";
  let targetText = "";
  if (kind === "traffic") {
    values = (signals.traffic || {}).series || [];
    format = (value) => `${formatRate(value)} req/s`;
    name = "Requests per second";
  } else if (kind === "errors") {
    values = (signals.errors || {}).series || [];
    // The error budget is the complement of the availability target the
    // collector judged against — nothing here chooses a threshold.
    target = isNumber(t.availability_pct) ? 100 - t.availability_pct : null;
    format = (value) => formatPct(value, 2);
    targetText = isNumber(target) ? formatPct(Number(target.toFixed(3))) : "";
    name = "Share of 5xx responses";
  } else {
    values = (signals.latency || {}).series_p95 || [];
    target = isNumber(t.latency_p95_ms) ? t.latency_p95_ms : null;
    format = formatMs;
    targetText = isNumber(target) ? formatMs(target) : "";
    name = "p95 latency";
  }
  const at = minuteLabels(snapshot, values.length);
  return sparkline({
    width,
    height: 58,
    values,
    target,
    zeroBased: true,
    area: kind === "traffic",
    interactive: true,
    axis: [`−${decimal.format(signals.window_minutes || 60)} min`, "now"],
    label: `${name}, ${windowText}: ${seriesSummary(values, format, target, targetText)}`,
    readout: (index) => [
      { value: isNumber(values[index]) ? format(values[index]) : "no data", label: at(index) },
      ...(isNumber(target) ? [{ label: `target ${targetText}` }] : []),
    ],
  });
}

function saturationGauges(snapshot) {
  const saturation = (snapshot.signals || {}).saturation || {};
  const t = targets(snapshot);
  const pct = (value) => (isNumber(value) ? `${decimal.format(Math.round(value))} %` : "n/a");
  const percentGauge = (name, value, limit, detail) =>
    gauge({
      name,
      value,
      max: 100,
      limit,
      valueText: pct(value),
      limitText: isNumber(limit) ? `limit ${pct(limit)}` : "",
      over: isNumber(value) && isNumber(limit) && value >= limit,
      detail,
    });
  const instances = saturation.instances;
  const most = saturation.max_instances;
  return [
    percentGauge("Cloud Run CPU", saturation.run_cpu_pct, t.run_cpu_pct, "container CPU utilisation"),
    percentGauge("Cloud Run memory", saturation.run_memory_pct, t.run_memory_pct, "container memory utilisation"),
    gauge({
      name: "Instances",
      value: instances,
      max: isNumber(most) && most > 0 ? most : isNumber(instances) ? Math.max(instances, 1) : null,
      limit: isNumber(most) ? most : null,
      valueText: isNumber(instances) ? `${decimal.format(instances)} / ${isNumber(most) ? decimal.format(most) : "?"}` : "n/a",
      limitText: isNumber(most) ? `max ${decimal.format(most)}` : "",
      over: isNumber(instances) && isNumber(most) && instances >= most,
      detail: "active container instances",
    }),
    percentGauge("Spanner CPU", saturation.spanner_cpu_pct, t.spanner_cpu_pct, "high-priority CPU utilisation"),
  ];
}

function countChart(snapshot, table, width) {
  const history = historyFor(snapshot, table);
  const values = history.map((entry) => entry.value);
  return sparkline({
    width,
    height: 22,
    values,
    zeroBased: false,
    interactive: true,
    label: `${table} rows over the last ${plural(values.length, "successful ingestion")}: ${seriesSummary(values, (value) => decimal.format(value))}`,
    readout: (index) => [
      { value: decimal.format(values[index]), label: `${formatStamp(history[index].at) || "unknown time"} UTC` },
    ],
  });
}

function drawCharts(snapshot) {
  if (!snapshot) return;
  hideTooltip();
  doc.querySelectorAll("[data-chart]").forEach((host) => {
    const width = Math.floor(host.clientWidth);
    if (width <= 0) return;
    if (Number(host.dataset.drawnWidth) === width && host.dataset.drawnFor === snapshot.generated_at) return;
    const kind = host.dataset.chart;
    let content = null;
    if (kind === "tile-latency" && snapshot.signals && snapshot.signals.latency) {
      const values = snapshot.signals.latency.series_p95 || [];
      const target = targets(snapshot).latency_p95_ms;
      content = sparkline({
        width,
        height: 34,
        values,
        target: isNumber(target) ? target : null,
        zeroBased: true,
        interactive: false,
        label: `p95 latency, last hour: ${seriesSummary(values, formatMs, isNumber(target) ? target : null, formatMs(target))}`,
      });
    } else if (kind === "tile-coverage" || kind === "coverage") {
      content = coverageBar({ width, height: kind === "coverage" ? 12 : 10, snapshot, interactive: kind === "coverage" });
    } else if (kind === "tile-timeline") {
      content = timeline({ width, height: 46, snapshot, interactive: false, compact: true });
    } else if (kind === "timeline") {
      const wrap = element("div");
      wrap.append(timeline({ width, height: 56, snapshot, interactive: true, compact: false }));
      wrap.append(timelineLegend(snapshot));
      content = wrap;
    } else if (kind === "traffic" || kind === "errors" || kind === "latency") {
      content = signalChart(snapshot, kind, width);
    } else if (kind === "gauges") {
      host.replaceChildren(...saturationGauges(snapshot));
      host.dataset.drawnWidth = String(width);
      host.dataset.drawnFor = snapshot.generated_at;
      return;
    } else if (kind === "count") {
      content = countChart(snapshot, host.dataset.table, width);
    }
    if (content) host.replaceChildren(content);
    host.dataset.drawnWidth = String(width);
    host.dataset.drawnFor = snapshot.generated_at;
  });
}

function watchWidth() {
  if (!("ResizeObserver" in window)) return;
  let pending = false;
  let lastWidth = 0;
  const observer = new ResizeObserver((entries) => {
    const width = Math.round(entries[0].contentRect.width);
    if (width === lastWidth || pending) return;
    lastWidth = width;
    pending = true;
    window.requestAnimationFrame(() => {
      pending = false;
      drawCharts(latest);
    });
  });
  observer.observe(doc.getElementById("body"));
}

/* ---------- the bar ---------- */

function renderBar(snapshot) {
  const deployment = snapshot.deployment || {};
  doc.getElementById("deployment").textContent = deploymentLabel(snapshot);
  doc.getElementById("version").textContent = deployment.dcp_version ? `v${deployment.dcp_version}` : "";
  doc.title = `${deploymentLabel(snapshot)} · ${STATUS[statusOf(snapshot.overall)].label} — Data Commons status`;
}

// The age is the one figure that changes without a fetch, so it ticks on its own.
function renderAge() {
  const clock = doc.getElementById("clock");
  const relative = doc.getElementById("relative");
  const host = doc.getElementById("age");
  const stamp = parseStamp(latest && latest.generated_at);
  if (!stamp) {
    clock.textContent = "—";
    relative.textContent = latest ? "time unknown" : doc.getElementById("readout").dataset.failed ? "no reading" : "reading";
    host.dataset.stale = "false";
    return;
  }
  const elapsed = Date.now() - stamp.getTime();
  clock.textContent = `${clockFormat.format(stamp)} UTC`;
  const partial = latest && latest.partial ? " · partial reading" : "";
  relative.textContent = `${formatAge(elapsed)}${partial}`;
  host.dataset.stale = String(elapsed > STALE_AFTER_MS);
}

/* ---------- render ---------- */

function render(snapshot) {
  latest = snapshot;
  const verdict = verdictOf(snapshot);
  setVerdict(verdict.headline, verdict.check, verdict.cause);
  renderBar(snapshot);
  renderTiles(snapshot);
  renderFindings(snapshot);
  renderDimensions(snapshot);
  renderAge();
  drawCharts(snapshot);

  const readout = doc.getElementById("readout");
  readout.dataset.state = "fresh";
  window.setTimeout(() => {
    if (readout.dataset.state === "fresh") readout.dataset.state = "settled";
  }, 400);
}

// A failure keeps the last good snapshot on screen and says so, because the
// numbers from a minute ago are more use than an empty page.
// A failure keeps the last good snapshot on screen and says so: the numbers
// from a minute ago are more use than an empty page.
function failed(headline, detail) {
  const readout = doc.getElementById("readout");
  readout.dataset.state = "settled";
  readout.dataset.failed = "true";
  const parts = [];
  if (detail) parts.push(terminate(detail));
  if (latest) {
    const stamp = parseStamp(latest.generated_at);
    parts.push(
      stamp
        ? `Showing the last snapshot that loaded, from ${clockFormat.format(stamp)} UTC.`
        : "Showing the last snapshot that loaded."
    );
  }
  setVerdict(headline, null, parts.join(" "));
  renderAge();
}

function refreshIapSession() {
  // An expired IAP session turns fetch into an opaque redirect. A hidden iframe
  // to the same origin re-establishes the cookie without a third-party hop.
  return new Promise((resolve) => {
    const frame = doc.createElement("iframe");
    frame.hidden = true;
    frame.src = "/?gcp-iap-mode=DO_SESSION_REFRESH";
    const timer = window.setTimeout(() => {
      frame.remove();
      window.location.reload();
    }, 5000);
    frame.addEventListener("load", () => {
      window.clearTimeout(timer);
      frame.remove();
      resolve();
    });
    doc.body.append(frame);
  });
}

async function errorMessage(response) {
  if (response.status === 403) {
    return { headline: "You don't have access to this panel.", detail: "Access is granted through IAP by whoever runs this deployment" };
  }
  let detail = "";
  try {
    const body = await response.json();
    detail = (body && (body.detail || body.error)) || "";
  } catch (error) {
    detail = "";
  }
  detail = String(detail);
  if (response.status >= 500) {
    return { headline: "The status service failed.", detail: detail || `It answered HTTP ${response.status}` };
  }
  return { headline: `The status service answered HTTP ${response.status}.`, detail };
}

function setBusy(busy) {
  loading = busy;
  doc.getElementById("body").setAttribute("aria-busy", String(busy));
  doc.getElementById("refresh").disabled = busy;
  const readout = doc.getElementById("readout");
  if (busy) readout.dataset.state = "loading";
}

async function load({ fresh = false, retry = true } = {}) {
  if (loading) return;
  setBusy(true);
  let response;
  try {
    response = await fetch(fresh ? `${API}?fresh=1` : API, {
      redirect: "manual",
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
  } catch (error) {
    setBusy(false);
    failed("The status service is not responding.", "The page will ask again in a minute");
    return;
  }
  lastFetchAt = Date.now();

  if (response.type === "opaqueredirect" || response.status === 0) {
    setBusy(false);
    if (retry) {
      await refreshIapSession();
      load({ fresh, retry: false });
      return;
    }
    window.location.reload();
    return;
  }

  if (!response.ok) {
    const { headline, detail } = await errorMessage(response);
    setBusy(false);
    failed(headline, detail);
    return;
  }

  let body;
  try {
    body = await response.json();
  } catch (error) {
    setBusy(false);
    failed("The status service returned something unreadable.");
    return;
  }
  setBusy(false);
  if (!body || body.schema_version !== 2) {
    failed("The status service sent a document this page cannot read.");
    return;
  }
  delete doc.getElementById("readout").dataset.failed;
  render(body);
}

/* ---------- theme ----------

   System follows the operating system through prefers-color-scheme; Light and
   Dark stamp data-theme on <html>, which the stylesheet lets win both ways.
   The choice is stored on this device only. `?theme=` previews a theme for
   one visit without storing it. */

const THEME_KEY = "dc-status-theme";
const THEMES = ["system", "light", "dark"];

function storedTheme() {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return THEMES.includes(value) ? value : "system";
  } catch (error) {
    return "system";
  }
}

function requestedTheme() {
  const value = new URLSearchParams(window.location.search).get("theme");
  return THEMES.includes(value) ? value : null;
}

function applyTheme(theme) {
  const root = doc.documentElement;
  if (theme === "light" || theme === "dark") root.dataset.theme = theme;
  else delete root.dataset.theme;
}

function wireTheme(initial) {
  const group = doc.getElementById("theme");
  group.querySelectorAll("input[name=theme]").forEach((input) => {
    input.checked = input.value === initial;
    input.addEventListener("change", () => {
      if (!input.checked) return;
      applyTheme(input.value);
      try {
        window.localStorage.setItem(THEME_KEY, input.value);
      } catch (error) {
        // Storage can be off; the choice still holds for this visit.
      }
    });
  });
}

const initialTheme = requestedTheme() || storedTheme();
applyTheme(initialTheme);

/* ---------- wiring ---------- */

function start() {
  wireTheme(initialTheme);
  doc.getElementById("refresh").addEventListener("click", () => load({ fresh: true }));

  doc.getElementById("toggle-details").addEventListener("click", (event) => {
    showEveryCheck = !showEveryCheck;
    event.currentTarget.setAttribute("aria-expanded", String(showEveryCheck));
    event.currentTarget.textContent = showEveryCheck ? "Show only findings" : "Show every check";
    if (latest) renderFindings(latest);
  });

  // Auto-refresh only while someone can see the page: a hidden tab should not
  // spend the collector's quota on readings nobody looks at.
  window.setInterval(() => {
    if (doc.visibilityState === "visible") load();
  }, REFRESH_EVERY_MS);
  doc.addEventListener("visibilitychange", () => {
    if (doc.visibilityState === "visible" && Date.now() - lastFetchAt >= REFRESH_EVERY_MS) load();
  });
  window.setInterval(renderAge, 30000);
  // A tooltip belongs to where the pointer was; scrolling moves the page out
  // from under it.
  window.addEventListener("scroll", () => hideTooltip(), { passive: true });
  watchWidth();

  load();
}

if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", start);
else start();
