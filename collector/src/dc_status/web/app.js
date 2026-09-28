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

// Status is never encoded by colour alone: every state carries a drawn glyph and
// a word. The glyphs are authored here rather than borrowed from the text font,
// so all four share one silhouette weight at 10 px instead of inheriting
// whatever a platform decided ● ▲ ■ ◌ should look like.
const SVG_NS = "http://www.w3.org/2000/svg";

const STATUS = {
  healthy: { label: "healthy", rank: 0 },
  degraded: { label: "degraded", rank: 1 },
  unknown: { label: "unknown", rank: 1 },
  down: { label: "down", rank: 2 },
};

const PROBE_LABELS = {
  dc_api: "API endpoint",
  dc_service: "Cloud Run service",
  spanner: "Spanner",
  schema: "Schema",
  version_consistency: "Image / schema match",
  counts: "Row counts",
  ingestions: "Ingestions",
  ingestion_lock: "Ingestion lock",
  data_sources: "Data sources",
  frontend: "Frontend",
};

const PROBE_ORDER = Object.keys(PROBE_LABELS);

// The palette validates for adjacent pairs, so a source keeps its slot no matter
// how many are on screen. Colour follows the source, never its rank.
const SLOTS = 7;

// A snapshot this old is itself a finding, so it stops being quiet about it.
const STALE_AFTER_MS = 10 * 60 * 1000;

const decimal = new Intl.NumberFormat("en");
// A fixed decimal place, so a column of shares stays aligned digit for digit.
const percent = new Intl.NumberFormat("en", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const clockFormat = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});
const stampFormat = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
});

let latest = null;
let showEveryCheck = false;

function element(tag, className, text) {
  const node = window.document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(attributes, ...children) {
  const node = window.document.createElementNS(SVG_NS, "svg");
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  children.forEach((child) => node.append(child));
  return node;
}

function shape(tag, attributes) {
  const node = window.document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}

function statusIcon(key) {
  const frame = {
    class: "state__icon",
    viewBox: "0 0 12 12",
    "aria-hidden": "true",
    focusable: "false",
    fill: "currentColor",
  };
  if (key === "healthy") {
    return svg(frame, shape("circle", { cx: 6, cy: 6, r: 4 }));
  }
  if (key === "degraded") {
    return svg(
      frame,
      shape("path", {
        d: "M6 1.6 10.4 9.9H1.6Z",
        "stroke-linejoin": "round",
        stroke: "currentColor",
        "stroke-width": 1.1,
      })
    );
  }
  if (key === "down") {
    return svg(frame, shape("rect", { x: 2, y: 2, width: 8, height: 8, rx: 1.2 }));
  }
  return svg(
    frame,
    shape("circle", {
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

function statusOf(value) {
  return STATUS[value] ? value : "unknown";
}

function rankOf(value) {
  return STATUS[statusOf(value)].rank;
}

// An environment that did not answer is unreachable, which is a more useful word
// than `unknown` — the difference is whether we failed to ask or failed to judge.
function stateWord(environment) {
  if (environment.reachable === false) return "unreachable";
  return STATUS[statusOf(environment.overall)].label;
}

function state(value, word) {
  const key = statusOf(value);
  const node = element("span", `state state--${key}`);
  node.append(statusIcon(key));
  node.append(element("span", null, word || STATUS[key].label));
  return node;
}

/* ---------- formatting ---------- */

function figure(value) {
  if (value === null || value === undefined) return { text: "n/a", absent: true };
  return { text: decimal.format(value), absent: false };
}

function parseStamp(value) {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatStamp(value) {
  const parsed = parseStamp(value);
  if (!parsed) return { text: "—", absent: true };
  return { text: `${stampFormat.format(parsed)}`, absent: false };
}

function formatAge(milliseconds) {
  const minutes = Math.round(milliseconds / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

// Ingestions run for hours, and "4127 s" is a number you have to do arithmetic on
// before it means anything.
function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return { text: "—", absent: true };
  if (seconds < 60) return { text: `${decimal.format(seconds)} s`, absent: false };
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return { text: `${minutes} m ${String(seconds % 60).padStart(2, "0")} s`, absent: false };
  const hours = Math.floor(minutes / 60);
  return { text: `${hours} h ${String(minutes % 60).padStart(2, "0")} m`, absent: false };
}

function sentenceList(items) {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

// Probe details are written as fragments, so the page supplies the full stop it
// needs to sit beside another sentence.
function terminate(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return "";
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`;
}

function labelOf(environment) {
  return environment.label || environment.id;
}

/* ---------- the verdict: what the page is for ---------- */

function worstProbe(environment) {
  const probes = environment.probes || [];
  let worst = null;
  probes.forEach((probe) => {
    if (!worst || rankOf(probe.status) > rankOf(worst.status)) worst = probe;
  });
  return worst && rankOf(worst.status) > 0 ? worst : null;
}

function verdictOf(doc) {
  const environments = doc.environments || [];
  if (!environments.length) {
    return { headline: "No environments are configured.", cause: [] };
  }

  const worstRank = Math.max(...environments.map((environment) => rankOf(environment.overall)));

  if (worstRank === 0) {
    const versions = [...new Set(environments.map((e) => e.dcp_version).filter(Boolean))];
    const subject =
      environments.length === 1
        ? `${labelOf(environments[0])} is healthy`
        : `All ${environments.length} environments are healthy`;
    const headline = versions.length === 1 ? `${subject}, serving ${versions[0]}.` : `${subject}.`;
    return { headline, cause: [] };
  }

  const affected = environments.filter((e) => rankOf(e.overall) === worstRank);
  const subject = affected[0];
  const headline = `${labelOf(subject)} is ${stateWord(subject)}.`;

  const cause = [];
  const probe = worstProbe(subject);
  if (probe && probe.detail) {
    cause.push({ check: PROBE_LABELS[probe.id] || probe.id, detail: terminate(probe.detail) });
  } else if (subject.detail) {
    cause.push({ check: null, detail: terminate(subject.detail) });
  }

  const others = environments.filter((e) => e !== subject);
  if (others.length) {
    const healthy = others.filter((e) => rankOf(e.overall) === 0).map(labelOf);
    const troubled = others.filter((e) => rankOf(e.overall) > 0);
    const clauses = troubled.map((e) => `${labelOf(e)} is ${stateWord(e)}`);
    if (healthy.length) {
      clauses.push(`${sentenceList(healthy)} ${healthy.length === 1 ? "is" : "are"} healthy`);
    }
    if (clauses.length) cause.push({ check: null, detail: `${sentenceList(clauses)}.` });
  }

  return { headline, cause };
}

function renderVerdict(doc) {
  const { headline, cause } = verdictOf(doc);
  window.document.getElementById("headline").textContent = headline;

  const node = window.document.getElementById("cause");
  node.replaceChildren();
  cause.forEach((part, index) => {
    if (index) node.append(" ");
    if (part.check) {
      node.append(element("span", "verdict__check", `${part.check}: `));
    }
    node.append(window.document.createTextNode(part.detail));
  });
}

/* ---------- the matrix: measures down, environments across ---------- */

function countOrder(environments) {
  const names = new Set();
  environments.forEach((environment) =>
    Object.keys(environment.counts || {}).forEach((name) => names.add(name))
  );
  const magnitude = (name) =>
    Math.max(
      ...environments.map((environment) => {
        const value = (environment.counts || {})[name];
        return typeof value === "number" ? value : -1;
      })
    );
  // Biggest table first: which table dominates a deployment is information, and
  // alphabetical order throws it away.
  return [...names].sort((a, b) => magnitude(b) - magnitude(a) || a.localeCompare(b));
}

function headerCell(environment) {
  const cell = element("th", null);
  cell.scope = "col";
  cell.append(element("span", null, labelOf(environment)));
  if (environment.id && environment.id !== labelOf(environment)) {
    cell.append(element("span", "matrix__env-id", environment.id));
  }
  return cell;
}

function groupRow(title, span) {
  const row = element("tr", "matrix__group");
  const cell = element("th", null, title);
  cell.scope = "row";
  row.append(cell);
  for (let index = 0; index < span; index += 1) row.append(element("td"));
  return row;
}

function measureRow(label, cells) {
  const row = element("tr");
  const head = element("th", null, label);
  head.scope = "row";
  row.append(head);
  cells.forEach((content) => {
    const cell = element("td", content.absent ? "absent" : null);
    if (content.node) cell.append(content.node);
    else cell.textContent = content.text;
    row.append(cell);
  });
  return row;
}

function renderMatrix(environments) {
  const table = window.document.getElementById("matrix");
  table.replaceChildren();

  const head = element("thead");
  const headRow = element("tr");
  // The corner is blank by design, but a header cell with no text is unreadable
  // to a screen reader walking the row.
  const corner = element("th");
  corner.scope = "col";
  corner.append(element("span", "visually-hidden", "Measure"));
  headRow.append(corner);
  environments.forEach((environment) => headRow.append(headerCell(environment)));
  head.append(headRow);
  table.append(head);

  const body = element("tbody");
  const span = environments.length;

  body.append(groupRow("Deployment", span));
  body.append(
    measureRow(
      "Overall",
      environments.map((environment) => ({
        node: state(environment.overall, stateWord(environment)),
      }))
    )
  );
  body.append(
    measureRow(
      "Platform version",
      environments.map((environment) =>
        environment.dcp_version
          ? { text: environment.dcp_version }
          : { text: "—", absent: true }
      )
    )
  );

  body.append(groupRow("Checks", span));
  PROBE_ORDER.forEach((id) => {
    body.append(
      measureRow(
        PROBE_LABELS[id],
        environments.map((environment) => {
          const probe = (environment.probes || []).find((entry) => entry.id === id);
          if (!probe) return { text: "—", absent: true };
          return { node: state(probe.status) };
        })
      )
    );
  });

  const names = countOrder(environments);
  if (names.length) {
    body.append(groupRow("Rows per table", span));
    names.forEach((name) => {
      body.append(
        measureRow(
          name,
          environments.map((environment) => {
            const counts = environment.counts || {};
            if (!(name in counts)) return { text: "—", absent: true };
            return figure(counts[name]);
          })
        )
      );
    });
  }

  table.append(body);
  window.document.getElementById("matrix-block").hidden = false;
}

/* ---------- findings: the checks that need words ---------- */

// Share of its deadline at which a check stops being merely slow. A probe past
// the second threshold is close enough to being dropped that the reading is about
// to become "unknown" rather than "slow".
const BUDGET_WARN = 0.5;
const BUDGET_CRITICAL = 0.85;

// `402 ms` says nothing about whether that is comfortable. Against a budget it
// does. The scale is linear because the question is literally "how much of the
// allowance did this consume", and a log scale would misstate that.
function timing(elapsed, budget) {
  const value = element("span", "timing__value", `${decimal.format(elapsed)} ms`);
  // A peer may be running an older collector that does not report a budget, so
  // the figure has to stand on its own when the denominator is missing.
  if (typeof budget !== "number" || budget <= 0) {
    const bare = element("span", "timing");
    bare.append(value);
    return bare;
  }

  const share = Math.min(elapsed / budget, 1);
  const level = share >= BUDGET_CRITICAL ? "critical" : share >= BUDGET_WARN ? "warning" : "calm";

  const node = element("span", `timing timing--${level}`);
  node.append(value);
  const track = element("span", "timing__track");
  const fill = element("span", "timing__fill");
  // Floored so a genuinely fast check still reads as a mark rather than as an
  // empty track, which would look like missing data.
  fill.style.width = `max(2px, ${(share * 100).toFixed(1)}%)`;
  track.append(fill);
  node.append(track);

  node.setAttribute("role", "img");
  node.setAttribute(
    "aria-label",
    `${decimal.format(elapsed)} ms, ${percent.format(share * 100)}% of its ${decimal.format(
      budget
    )} ms budget`
  );
  return node;
}

function finding({ environment, check, status, detail, elapsed, budget, revealed }) {
  const node = element("div", "finding");
  if (revealed) node.dataset.revealed = "true";

  const head = element("div", "finding__head");
  head.append(element("span", "finding__env", environment));
  if (check) head.append(element("span", "finding__check", check));
  head.append(state(status, status === "unreachable" ? "unreachable" : undefined));
  if (elapsed !== null && elapsed !== undefined) {
    head.append(timing(elapsed, budget));
  }
  node.append(head);

  if (detail) node.append(element("p", "finding__detail", detail));
  return node;
}

function renderFindings(environments) {
  const host = window.document.getElementById("findings");
  const rows = [];

  environments.forEach((environment) => {
    if (environment.reachable === false) {
      rows.push(
        finding({
          environment: labelOf(environment),
          check: null,
          status: "unknown",
          detail: environment.detail || "This environment could not be reached.",
          elapsed: null,
        })
      );
      return;
    }
    const probes = [...(environment.probes || [])].sort(
      (a, b) => rankOf(b.status) - rankOf(a.status)
    );
    probes.forEach((probe) => {
      const troubled = rankOf(probe.status) > 0;
      if (!troubled && !showEveryCheck) return;
      rows.push(
        finding({
          environment: labelOf(environment),
          check: PROBE_LABELS[probe.id] || probe.id,
          status: probe.status,
          detail: probe.detail,
          elapsed: probe.elapsed_ms,
          budget: probe.budget_ms,
          revealed: !troubled,
        })
      );
    });
  });

  if (!rows.length) {
    host.replaceChildren(element("p", "empty", "Every check passed."));
  } else {
    host.replaceChildren(...rows);
  }
  window.document.getElementById("findings-block").hidden = false;
}

/* ---------- per-environment detail ---------- */

function dataTable(headers, rows, emptyMessage) {
  if (!rows.length) return element("p", "empty", emptyMessage);
  const table = element("table", "data");
  const head = element("thead");
  const headRow = element("tr");
  headers.forEach((header) => {
    const cell = element("th", header.numeric ? "numeric" : null, header.label);
    cell.scope = "col";
    headRow.append(cell);
  });
  head.append(headRow);
  table.append(head);

  const body = element("tbody");
  rows.forEach((cells) => {
    const row = element("tr");
    cells.forEach((content, index) => {
      const classes = [];
      if (headers[index] && headers[index].numeric) classes.push("numeric");
      if (content.absent) classes.push("absent");
      const cell = element("td", classes.join(" ") || null);
      if (content.node) cell.append(content.node);
      else cell.textContent = content.text;
      row.append(cell);
    });
    body.append(row);
  });
  table.append(body);

  const scroller = element("div", "scroller");
  scroller.append(table);
  return scroller;
}

function renderCoverage(sources) {
  // Only sources whose served rows are known can carry a proportion. If none are,
  // the bar is omitted rather than drawn from zeros — an empty bar would read as
  // "no data served" when the truth is "we could not ask".
  const known = sources.filter((source) => typeof source.rows === "number" && source.rows > 0);
  if (known.length < 2) return null;

  const total = known.reduce((sum, source) => sum + source.rows, 0);
  const bar = element("div", "coverage");
  bar.setAttribute("role", "img");
  bar.setAttribute(
    "aria-label",
    `Share of rows served per source: ${known
      .map((source) => `${source.prefix} ${percent.format((source.rows / total) * 100)}%`)
      .join(", ")}.`
  );
  known.forEach((source) => {
    const segment = element("div", "coverage__segment");
    segment.dataset.slot = String(source.slot);
    segment.style.flex = `${source.rows} 0 auto`;
    bar.append(segment);
  });
  return bar;
}

function renderEnvironment(environment) {
  const node = element("section", "env");

  const name = element("h3", "env__name");
  name.append(element("span", null, labelOf(environment)));
  name.append(state(environment.overall, stateWord(environment)));
  if (environment.dcp_version) {
    name.append(element("span", "env__version", environment.dcp_version));
  }
  node.append(name);

  if (environment.reachable === false) {
    node.append(
      element("p", "empty", environment.detail || "This environment could not be reached.")
    );
    return node;
  }

  const sources = (environment.data_sources || []).map((source, index) => ({
    ...source,
    slot: (index % SLOTS) + 1,
  }));
  const known = sources.filter((source) => typeof source.rows === "number" && source.rows > 0);
  const total = known.reduce((sum, source) => sum + source.rows, 0);

  node.append(element("h4", "env__sub", "Data sources"));
  const coverage = renderCoverage(sources);
  if (coverage) node.append(coverage);

  // The swatch lives in the table, so the colour key and the numbers are one
  // object instead of a legend repeating the same list beside them.
  const sourceRows = sources.map((source) => {
    const proportion =
      total > 0 && typeof source.rows === "number" && source.rows > 0
        ? { text: `${percent.format((source.rows / total) * 100)}%`, absent: false }
        : { text: "—", absent: true };
    const label = element("span");
    const swatch = element("span", "swatch");
    swatch.dataset.slot = known.includes(source) ? String(source.slot) : "none";
    label.append(swatch);
    label.append(window.document.createTextNode(source.prefix));
    return [
      { node: label },
      figure(source.files),
      formatStamp(source.last_updated),
      figure(source.rows),
      proportion,
    ];
  });

  (environment.unmatched_provenances || []).forEach((entry) => {
    const label = element("span");
    const swatch = element("span", "swatch");
    swatch.dataset.slot = "none";
    label.append(swatch);
    label.append(window.document.createTextNode(`${entry.provenance} — no configured prefix`));
    sourceRows.push([
      { node: label, absent: true },
      { text: "—", absent: true },
      { text: "—", absent: true },
      figure(entry.rows),
      { text: "—", absent: true },
    ]);
  });

  node.append(
    dataTable(
      [
        { label: "Source" },
        { label: "Files", numeric: true },
        { label: "Last upload" },
        { label: "Rows served", numeric: true },
        { label: "Share", numeric: true },
      ],
      sourceRows,
      "No data sources configured."
    )
  );

  node.append(element("h4", "env__sub", "Recent ingestions"));
  node.append(
    dataTable(
      [
        { label: "Started" },
        { label: "Result" },
        { label: "Stage" },
        { label: "Workflow" },
        { label: "Duration", numeric: true },
      ],
      (environment.ingestions || []).map((entry) => [
        formatStamp(entry.creation),
        { text: entry.status || "—", absent: !entry.status },
        { text: entry.stage || "—", absent: !entry.stage },
        { text: entry.workflow_state || "—", absent: !entry.workflow_state },
        formatDuration(entry.execution_seconds),
      ]),
      "No ingestion history yet."
    )
  );

  return node;
}

function renderDetails(environments) {
  const host = window.document.getElementById("details");
  if (!environments.length) {
    host.replaceChildren();
    return;
  }
  const grid = element("div", "envs");
  environments.forEach((environment) => grid.append(renderEnvironment(environment)));
  host.replaceChildren(grid);
}

/* ---------- the snapshot's age, which keeps counting after the fetch ---------- */

function renderAge() {
  const clock = window.document.getElementById("clock");
  const relative = window.document.getElementById("relative");
  const host = window.document.getElementById("age");

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

function render(doc) {
  latest = doc;
  const environments = doc.environments || [];
  renderVerdict(doc);
  renderMatrix(environments);
  renderFindings(environments);
  renderDetails(environments);
  renderAge();

  const readout = window.document.getElementById("readout");
  readout.dataset.state = "fresh";
  // Retire the entrance so the next refresh can replay it.
  window.setTimeout(() => {
    if (readout.dataset.state === "fresh") readout.dataset.state = "settled";
  }, 400);
}

function failed(message) {
  window.document.getElementById("headline").textContent = message;
  window.document.getElementById("cause").replaceChildren();
  window.document.getElementById("readout").dataset.state = "settled";
}

function refreshIapSession() {
  // An expired IAP session turns fetch into an opaque redirect. A hidden iframe to
  // the same origin re-establishes the cookie without a third-party hop, which a
  // cross-origin request could not do at all.
  return new Promise((resolve) => {
    const frame = window.document.createElement("iframe");
    frame.style.display = "none";
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
    window.document.body.append(frame);
  });
}

async function load(retry = true) {
  const button = window.document.getElementById("refresh");
  const readout = window.document.getElementById("readout");
  readout.dataset.state = "loading";
  button.disabled = true;

  let response;
  try {
    response = await fetch("/api/v1/all", { redirect: "manual", cache: "no-store" });
  } catch (error) {
    failed("The status service is unreachable.");
    button.disabled = false;
    return;
  }
  if (response.type === "opaqueredirect" || response.status === 0) {
    if (retry) {
      await refreshIapSession();
      return load(false);
    }
    window.location.reload();
    return;
  }
  try {
    render(await response.json());
  } catch (error) {
    failed("The status service returned something unreadable.");
  } finally {
    button.disabled = false;
  }
}

window.document.getElementById("refresh").addEventListener("click", () => load());

window.document.getElementById("toggle-details").addEventListener("click", (event) => {
  showEveryCheck = !showEveryCheck;
  event.currentTarget.setAttribute("aria-expanded", String(showEveryCheck));
  event.currentTarget.textContent = showEveryCheck ? "Show only findings" : "Show every check";
  if (latest) renderFindings(latest.environments || []);
});

// The age is the one figure that changes without a fetch, so it ticks on its own.
window.setInterval(renderAge, 30000);

load();
