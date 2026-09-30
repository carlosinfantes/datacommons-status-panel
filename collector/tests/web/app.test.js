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

/* The page under `node --test`: what it says about a document, and that it
   only ever builds the DOM from text. Run from the repository root with
   `node --test collector/tests/web/`. */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");

const { openPage, response } = require("./dom.js");

const DEMO = path.resolve(__dirname, "../fixtures/demo.json");

function demo() {
  return JSON.parse(fs.readFileSync(DEMO, "utf8"));
}

function healthy() {
  const snapshot = demo();
  snapshot.overall = "healthy";
  snapshot.partial = false;
  snapshot.dimensions.forEach((dimension) => (dimension.status = "healthy"));
  snapshot.probes.forEach((probe) => {
    probe.status = "healthy";
    probe.detail = "";
  });
  return snapshot;
}

function setProbe(snapshot, id, status, detail) {
  const probe = snapshot.probes.find((candidate) => candidate.id === id);
  probe.status = status;
  probe.detail = detail;
  return probe;
}

// A plain array of ids: what the page returns belongs to its own realm, which
// deepEqual tells apart from this one.
function findingIds(page, snapshot) {
  return [...page.call("findingsOf", snapshot)].map((probe) => probe.id);
}

async function loaded(answer) {
  const page = openPage({ fetch: async () => answer });
  await page.call("load");
  return page;
}

/* ---------- the verdict ---------- */

test("a document with no finding reads as healthy and names the platform version", () => {
  const snapshot = healthy();
  const verdict = openPage().call("verdictOf", snapshot);
  assert.equal(verdict.headline, `${snapshot.deployment.label} is healthy.`);
  assert.equal(verdict.check, null);
  assert.match(verdict.cause, /Every check passed\.$/);
  assert.ok(verdict.cause.includes(snapshot.deployment.dcp_version));
});

test("the verdict names the most severe finding and counts the rest", () => {
  const snapshot = healthy();
  snapshot.overall = "down";
  setProbe(snapshot, "latency", "unknown", "no latency data in the window");
  setProbe(snapshot, "ingestions", "degraded", "the latest ingestion reported FAILED");
  setProbe(snapshot, "spanner", "down", "instance CREATING");
  const verdict = openPage().call("verdictOf", snapshot);
  assert.equal(verdict.headline, `${snapshot.deployment.label} is down.`);
  assert.equal(verdict.check, "System · Spanner: ");
  assert.equal(verdict.cause, "instance CREATING. Also 2 other findings.");
});

test("a known problem outranks a check that could not be read", () => {
  const snapshot = healthy();
  snapshot.overall = "degraded";
  // dc_api comes first on the page, but not knowing is the lesser finding.
  setProbe(snapshot, "dc_api", "unknown", "timed out");
  setProbe(snapshot, "pending_uploads", "degraded", "1 source has input waiting");
  assert.deepEqual(findingIds(openPage(), snapshot), ["pending_uploads", "dc_api"]);
});

test("an unknown overall is a reading that failed, not a deployment that is unknown", () => {
  const snapshot = healthy();
  snapshot.overall = "unknown";
  setProbe(snapshot, "counts", "unknown", "");
  const verdict = openPage().call("verdictOf", snapshot);
  assert.equal(verdict.headline, `${snapshot.deployment.label} could not be fully read.`);
  assert.equal(verdict.cause, "Row counts is unknown.");
});

test("a status the page does not know is treated as unknown, never as healthy", () => {
  const page = openPage();
  assert.equal(page.call("statusOf", "on-fire"), "unknown");
  assert.equal(page.call("rankOf", undefined), page.call("rankOf", "unknown"));
  const snapshot = healthy();
  setProbe(snapshot, "schema", "on-fire", "");
  assert.deepEqual(findingIds(page, snapshot), ["schema"]);
});

test("a probe the document lists but does not carry is a finding", () => {
  const snapshot = healthy();
  snapshot.probes = snapshot.probes.filter((probe) => probe.id !== "frontend");
  assert.deepEqual(findingIds(openPage(), snapshot), ["frontend"]);
});

/* ---------- figures ---------- */

test("ages, hours and durations read in the unit a person would use", () => {
  const page = openPage();
  assert.equal(page.call("formatAge", 20 * 1000), "just now");
  assert.equal(page.call("formatAge", 59 * 60 * 1000), "59 min ago");
  assert.equal(page.call("formatAge", 5 * 3600 * 1000), "5 h ago");
  assert.equal(page.call("formatAge", 72 * 3600 * 1000), "3 d ago");

  assert.deepEqual({ ...page.call("formatHours", 0.5) }, { value: "30", unit: "min" });
  assert.deepEqual({ ...page.call("formatHours", 47) }, { value: "47", unit: "h" });
  assert.deepEqual({ ...page.call("formatHours", 72) }, { value: "3", unit: "d" });
  assert.equal(page.call("formatHours", null), null);

  assert.equal(page.call("formatDuration", 42), "42 s");
  assert.equal(page.call("formatDuration", 125), "2 m 05 s");
  assert.equal(page.call("formatDuration", 4127), "1 h 08 m");
  assert.equal(page.call("formatMs", null), "n/a");
  assert.equal(page.call("formatPct", 99.95, 2), "99.95 %");
});

test("an ingestion is a success, a failure or still running", () => {
  const page = openPage();
  assert.equal(page.call("ingestionKind", { status: "success" }), "success");
  assert.equal(page.call("ingestionKind", { status: "FAILURE" }), "failure");
  assert.equal(page.call("ingestionKind", { status: "CANCELLED" }), "failure");
  assert.equal(page.call("ingestionKind", { status: "RUNNING", failure: true }), "failure");
  assert.equal(page.call("ingestionKind", { status: "RUNNING" }), "running");
  assert.equal(page.call("ingestionKind", {}), "running");
});

/* ---------- links ---------- */

test("only an https URL becomes a link", () => {
  const page = openPage();
  assert.equal(page.call("consoleLink", "javascript:alert(1)", "Spanner"), null);
  assert.equal(page.call("consoleLink", "http://console.cloud.google.com/", "Spanner"), null);
  assert.equal(page.call("consoleLink", "not a url", "Spanner"), null);
  assert.equal(page.call("consoleLink", null, "Spanner"), null);
  const link = page.call("consoleLink", "https://console.cloud.google.com/spanner", "Spanner");
  assert.equal(link.href, "https://console.cloud.google.com/spanner");
  assert.equal(link.rel, "noopener noreferrer");

  assert.equal(page.call("httpsUrl", "javascript:alert(1)"), null);
  assert.equal(page.call("httpsUrl", "https://github.com/o/r/"), "https://github.com/o/r");
});

test("a build source that is not https is shown as text, not linked", async () => {
  const snapshot = demo();
  snapshot.panel = { version: "1.2.3", commit: "abc123", source: "javascript:alert(1)" };
  const page = await loaded(response(200, snapshot));
  const build = page.byId("build");
  assert.equal(build.textContent, "dc-status 1.2.3 · abc123");
  assert.deepEqual(build.find("footnote__link"), []);
});

/* ---------- rendering ---------- */

test("the demo document renders every part of the page", async () => {
  const snapshot = demo();
  const page = await loaded(response(200, snapshot));
  assert.equal(page.byId("headline").textContent, `${snapshot.deployment.label} is degraded.`);
  assert.equal(page.byId("deployment").textContent, snapshot.deployment.label);
  assert.equal(page.byId("tiles").children.length, 4);
  assert.equal(page.byId("dimensions").children.length, 4);
  assert.equal(page.byId("findings").children.length, page.call("findingsOf", snapshot).length);
  assert.match(page.byId("findings-count").textContent, /^3 of 16 checks$/);
  assert.ok(page.document.title.includes("degraded"));
  // Every chart host was drawn for this snapshot.
  const hosts = page.document.querySelectorAll("[data-chart]");
  assert.ok(hosts.length > 0);
  hosts.forEach((host) => assert.equal(host.dataset.drawnFor, snapshot.generated_at));
});

test("a document with nothing readable still renders", async () => {
  const bare = {
    schema_version: 2,
    generated_at: "2026-08-05T18:00:00Z",
    partial: true,
    overall: "unknown",
    deployment: { id: "prod" },
    dimensions: [],
    probes: [],
    signals: null,
  };
  const page = await loaded(response(200, bare));
  assert.equal(page.byId("headline").textContent, "prod could not be fully read.");
  assert.equal(page.byId("tiles").children.length, 4);
  assert.match(page.byId("relative").textContent, /partial reading$/);
});

test("markup in the document's data arrives as text", async () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const snapshot = demo();
  snapshot.deployment.label = hostile;
  snapshot.probes.forEach((probe) => {
    probe.status = "degraded";
    probe.detail = hostile;
  });
  snapshot.data_sources.forEach((source) => (source.prefix = hostile));
  snapshot.unmatched_provenances = [{ provenance: hostile, rows: 1 }];
  // The test DOM throws on innerHTML, outerHTML and insertAdjacentHTML, so
  // getting here at all means none of them was used.
  const page = await loaded(response(200, snapshot));
  assert.equal(page.byId("deployment").textContent, hostile);
  const details = page.byId("findings").find("finding__detail");
  assert.equal(details.length, snapshot.probes.length);
  details.forEach((detail) => assert.equal(detail.textContent, hostile));
});

test("show every check adds the passing checks to the findings", async () => {
  const snapshot = demo();
  const page = await loaded(response(200, snapshot));
  page.start();
  const before = page.byId("findings").children.length;
  page.byId("toggle-details").dispatch("click");
  assert.equal(page.byId("findings").children.length, snapshot.probes.length);
  assert.ok(before < snapshot.probes.length);
  assert.equal(page.byId("toggle-details").getAttribute("aria-expanded"), "true");
});

/* ---------- loading ---------- */

test("a refusal says who grants access and nothing about why", async () => {
  const page = await loaded(response(403, { error: "forbidden" }));
  assert.equal(page.byId("headline").textContent, "You don't have access to this panel.");
  assert.equal(page.byId("readout").dataset.failed, "true");
  assert.equal(page.byId("relative").textContent, "no reading");
});

test("a collector failure shows the detail it was given", async () => {
  const page = await loaded(response(500, { error: "unavailable", detail: "Spanner said no" }));
  assert.equal(page.byId("headline").textContent, "The status service failed.");
  assert.equal(page.byId("cause").textContent, "Spanner said no.");
});

test("a document of another schema version is refused, not half-drawn", async () => {
  const page = await loaded(response(200, { ...demo(), schema_version: 3 }));
  assert.equal(page.byId("headline").textContent, "The status service sent a document this page cannot read.");
  assert.equal(page.byId("tiles").children.length, 0);
});

test("a body that is not JSON is reported as unreadable", async () => {
  const page = await loaded(response(200, undefined));
  assert.equal(page.byId("headline").textContent, "The status service returned something unreadable.");
});

test("a failure after a good reading keeps the snapshot on screen and says so", async () => {
  const snapshot = demo();
  const answers = [response(200, snapshot), null];
  const page = openPage({
    fetch: async () => {
      const answer = answers.shift();
      if (!answer) throw new TypeError("network down");
      return answer;
    },
  });
  await page.call("load");
  await page.call("load");
  assert.equal(page.byId("headline").textContent, "The status service is not responding.");
  assert.match(page.byId("cause").textContent, /Showing the last snapshot that loaded, from \d\d:\d\d UTC\.$/);
  assert.equal(page.byId("tiles").children.length, 4);
  assert.equal(page.byId("refresh").disabled, false);
});

test("the refresh button asks for a fresh document and automatic loads do not", async () => {
  const asked = [];
  const page = openPage({
    fetch: async (url) => {
      asked.push(url);
      return response(200, demo());
    },
  });
  page.start();
  await new Promise((resolve) => setImmediate(resolve));
  page.byId("refresh").dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(asked, ["/api/v1/status", "/api/v1/status?fresh=1"]);
});

test("the theme in the query string is applied only when it is a known one", () => {
  assert.equal(openPage({ search: "?theme=dark" }).document.documentElement.dataset.theme, "dark");
  assert.equal(openPage({ search: "?theme=neon" }).document.documentElement.dataset.theme, undefined);
});
