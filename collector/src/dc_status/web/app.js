"use strict";

// Status is never encoded by colour alone: every chip carries a glyph and a word,
// which is also what makes the two sub-3:1 status hues legible on the light surface.
const STATUS = {
  healthy: { glyph: "●", label: "Healthy" },
  degraded: { glyph: "▲", label: "Degraded" },
  down: { glyph: "■", label: "Down" },
  unknown: { glyph: "◌", label: "Unknown" },
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

// The palette validates for adjacent pairs, so a source keeps its slot no matter
// how many are on screen. Colour follows the source, never its rank.
const SLOTS = 7;

const decimal = new Intl.NumberFormat("en");
const percent = new Intl.NumberFormat("en", { maximumFractionDigits: 1 });

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function statusOf(value) {
  return STATUS[value] ? value : "unknown";
}

function chip(value) {
  const key = statusOf(value);
  const node = element("span", `chip chip--${key}`);
  node.append(element("span", "chip__glyph", STATUS[key].glyph));
  node.append(element("span", null, STATUS[key].label));
  return node;
}

function section(title, ...children) {
  const wrapper = element("section", "section");
  wrapper.append(element("h3", "section__title", title));
  children.forEach((child) => child && wrapper.append(child));
  return wrapper;
}

function count(value) {
  if (value === null || value === undefined) {
    return { text: "n/a", absent: true };
  }
  return { text: decimal.format(value), absent: false };
}

function renderTiles(counts) {
  const names = Object.keys(counts).sort();
  if (!names.length) return element("p", "empty", "No table counts available.");
  const grid = element("div", "tiles");
  names.forEach((name) => {
    const tile = element("div", "tile");
    tile.append(element("span", "tile__label", name));
    const { text, absent } = count(counts[name]);
    tile.append(element("span", `tile__value${absent ? " tile__value--absent" : ""}`, text));
    grid.append(tile);
  });
  return grid;
}

function renderProbes(probes) {
  const list = element("ul", "probes");
  probes.forEach((probe) => {
    const item = element("li", "probe");
    item.append(element("span", "probe__name", PROBE_LABELS[probe.id] || probe.id));
    item.append(chip(probe.status));
    item.append(element("span", "probe__timing", `${probe.elapsed_ms} ms`));
    if (probe.detail) item.append(element("p", "probe__detail", probe.detail));
    list.append(item);
  });
  return list;
}

function renderTable(headers, rows, emptyMessage) {
  if (!rows.length) return element("p", "empty", emptyMessage);
  const wrapper = element("div", "table-wrapper");
  const table = element("table");
  const head = element("thead");
  const headRow = element("tr");
  headers.forEach((header) => {
    const cell = element("th", header.numeric ? "numeric" : null, header.label);
    headRow.append(cell);
  });
  head.append(headRow);
  table.append(head);
  const body = element("tbody");
  rows.forEach((cells) => {
    const row = element("tr");
    cells.forEach((cell, index) => {
      const classes = [];
      if (headers[index] && headers[index].numeric) classes.push("numeric");
      if (cell.absent) classes.push("absent");
      row.append(element("td", classes.join(" ") || null, cell.text));
    });
    body.append(row);
  });
  table.append(body);
  wrapper.append(table);
  return wrapper;
}

function renderCoverage(sources) {
  // Only sources whose served rows are known can carry a proportion. If none are,
  // the bar is omitted rather than drawn from zeros — an empty bar would read as
  // "no data served" when the truth is "we could not ask".
  const known = sources
    .map((source, index) => ({ ...source, slot: (index % SLOTS) + 1 }))
    .filter((source) => typeof source.rows === "number" && source.rows > 0);
  if (!known.length) return null;

  const total = known.reduce((sum, source) => sum + source.rows, 0);
  const bar = element("div", "coverage");
  const legend = element("ul", "legend");

  known.forEach((source) => {
    const share = (source.rows / total) * 100;
    const segment = element("div", "coverage__segment");
    segment.dataset.slot = String(source.slot);
    segment.style.flex = `${source.rows} 0 auto`;
    segment.title = `${source.prefix}: ${decimal.format(source.rows)} rows (${percent.format(share)}%)`;
    bar.append(segment);

    const item = element("li", "legend__item");
    item.dataset.slot = String(source.slot);
    item.append(element("span", "legend__swatch"));
    item.append(element("span", null, source.prefix));
    item.append(element("span", "legend__value", `${percent.format(share)}%`));
    legend.append(item);
  });

  const wrapper = element("div");
  wrapper.append(bar);
  wrapper.append(legend);
  return wrapper;
}

function renderEnvironment(environment) {
  const status = statusOf(environment.overall);
  const card = element("section", `card card--${status}`);

  const header = element("header", "card__header");
  header.append(element("h2", null, environment.label || environment.id));
  header.append(chip(environment.overall));
  if (environment.dcp_version) {
    header.append(element("span", "card__version", `v${environment.dcp_version}`));
  }
  card.append(header);

  if (environment.reachable === false) {
    card.append(
      element(
        "p",
        "card__unreachable",
        environment.detail || "This environment could not be reached."
      )
    );
    return card;
  }

  card.append(section("Rows per table", renderTiles(environment.counts || {})));
  card.append(section("Checks", renderProbes(environment.probes || [])));

  const sources = environment.data_sources || [];
  card.append(
    section(
      "Data sources",
      renderCoverage(sources),
      renderTable(
        [
          { label: "Source" },
          { label: "Files", numeric: true },
          { label: "Last upload" },
          { label: "Rows served", numeric: true },
        ],
        sources.map((source) => [
          { text: source.prefix },
          count(source.files),
          { text: source.last_updated || "—", absent: !source.last_updated },
          count(source.rows),
        ]),
        "No data sources configured."
      )
    )
  );

  const ingestions = environment.ingestions || [];
  card.append(
    section(
      "Recent ingestions",
      renderTable(
        [
          { label: "Started" },
          { label: "Status" },
          { label: "Stage" },
          { label: "Workflow" },
          { label: "Duration", numeric: true },
        ],
        ingestions.map((entry) => [
          { text: entry.creation || "—", absent: !entry.creation },
          { text: entry.status || "—", absent: !entry.status },
          { text: entry.stage || "—", absent: !entry.stage },
          { text: entry.workflow_state || "—", absent: !entry.workflow_state },
          entry.execution_seconds === null || entry.execution_seconds === undefined
            ? { text: "—", absent: true }
            : { text: `${decimal.format(entry.execution_seconds)} s`, absent: false },
        ]),
        "No ingestion history yet."
      )
    )
  );

  return card;
}

function renderBanner(document_) {
  const banner = window.document.getElementById("banner");
  const versions = [
    ...new Set(document_.environments.map((environment) => environment.dcp_version).filter(Boolean)),
  ];
  if (versions.length > 1) {
    banner.textContent = `Environments are running different platform versions: ${versions.join(", ")}. An image newer than its database is the failure this panel exists to catch.`;
    banner.hidden = false;
    return;
  }
  banner.hidden = true;
}

function render(document_) {
  const environments = document_.environments || [];
  window.document
    .getElementById("environments")
    .replaceChildren(...environments.map(renderEnvironment));

  const stamp = document_.generated_at || "unknown time";
  const partial = document_.partial ? " · partial: some checks did not answer" : "";
  window.document.getElementById("generated").textContent = `Generated ${stamp}${partial}`;
  renderBanner(document_);
}

function refreshIapSession() {
  // An expired IAP session turns fetch into an opaque redirect. A hidden iframe to
  // the same origin re-establishes the cookie without a third-party hop, which a
  // cross-origin request could not do at all.
  return new Promise((resolve) => {
    const frame = window.document.createElement("iframe");
    frame.style.display = "none";
    frame.src = "/?gcp-iap-mode=DO_SESSION_REFRESH";
    frame.addEventListener("load", () => {
      frame.remove();
      resolve();
    });
    window.setTimeout(() => {
      frame.remove();
      window.location.reload();
    }, 5000);
    window.document.body.append(frame);
  });
}

async function load(retry = true) {
  const generated = window.document.getElementById("generated");
  let response;
  try {
    response = await fetch("/api/v1/all", { redirect: "manual", cache: "no-store" });
  } catch (error) {
    generated.textContent = "The status service is unreachable.";
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
    generated.textContent = "The status service returned something unreadable.";
  }
}

window.document.getElementById("refresh").addEventListener("click", () => load());
load();
