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
const STATUS = {
  healthy: { label: "healthy", rank: 0 },
  degraded: { label: "degraded", rank: 1 },
  unknown: { label: "unknown", rank: 1 },
  down: { label: "down", rank: 2 },
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
    .sort((a, b) => rankOf(b.probe.status) - rankOf(a.probe.status) || a.index - b.index)
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
        "They come from Cloud Monitoring, which did not answer. The collector's service account needs roles/monitoring.viewer on the project. The checks below say what happened."
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
    relative.textContent = latest ? "time unknown" : "reading";
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

  const readout = doc.getElementById("readout");
  readout.dataset.state = "fresh";
  window.setTimeout(() => {
    if (readout.dataset.state === "fresh") readout.dataset.state = "settled";
  }, 400);
}

// A failure keeps the last good snapshot on screen and says so, because the
// numbers from a minute ago are more use than an empty page.
function failed(message) {
  const readout = doc.getElementById("readout");
  readout.dataset.state = "settled";
  if (latest) {
    const stamp = parseStamp(latest.generated_at);
    setVerdict(
      message,
      null,
      stamp ? `Showing the last snapshot that loaded, from ${clockFormat.format(stamp)} UTC.` : "Showing the last snapshot that loaded."
    );
    readout.dataset.failed = "true";
    return;
  }
  setVerdict(message, null, "");
  readout.dataset.failed = "true";
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
  if (response.status === 403) return "You don't have access to this panel.";
  let detail = "";
  try {
    const body = await response.json();
    detail = (body && (body.detail || body.error)) || "";
  } catch (error) {
    detail = "";
  }
  if (response.status >= 500) {
    return detail
      ? `The status service failed: ${terminate(String(detail))}`
      : `The status service failed (HTTP ${response.status}).`;
  }
  return `The status service answered HTTP ${response.status}${detail ? `: ${terminate(String(detail))}` : "."}`;
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
    failed("The status service is not responding.");
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
    const message = await errorMessage(response);
    setBusy(false);
    failed(message);
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

/* ---------- wiring ---------- */

function start() {
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

  load();
}

start();
