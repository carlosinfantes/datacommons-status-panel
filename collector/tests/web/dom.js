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

/* Just enough of a DOM to run the page under `node --test`, with no
   dependency to install. It builds a tree and records what was set on it;
   it lays nothing out, so every box is the same fixed size.

   There is no way to parse markup here, on purpose: the page promises to build
   everything with createElement and textContent, and a test that renders
   through this file fails the moment that stops being true. */

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const WEB = path.resolve(__dirname, "../../src/dc_status/web");
const BOX = { left: 0, top: 0, right: 640, bottom: 120, width: 640, height: 120 };

function refuseMarkup() {
  throw new Error("the page must never parse data as markup");
}

class FakeText {
  constructor(text) {
    this.textContent = String(text);
    this.parent = null;
  }
}

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.childNodes = [];
    this.parent = null;
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.className = "";
    this.clientWidth = BOX.width;
    this.classList = {
      add: (name) => {
        this.className = `${this.className} ${name}`.trim();
      },
    };
  }

  get children() {
    return this.childNodes.filter((node) => node instanceof FakeElement);
  }

  get lastChild() {
    return this.childNodes[this.childNodes.length - 1] || null;
  }

  get textContent() {
    return this.childNodes.map((node) => node.textContent).join("");
  }

  set textContent(value) {
    this.replaceChildren();
    if (value !== "" && value !== null && value !== undefined) this.append(String(value));
  }

  set innerHTML(_value) {
    refuseMarkup();
  }

  set outerHTML(_value) {
    refuseMarkup();
  }

  insertAdjacentHTML() {
    refuseMarkup();
  }

  append(...nodes) {
    nodes.forEach((node) => {
      const child = typeof node === "string" ? new FakeText(node) : node;
      if (child.parent) child.remove();
      child.parent = this;
      this.childNodes.push(child);
    });
  }

  replaceChildren(...nodes) {
    this.childNodes.forEach((node) => {
      node.parent = null;
    });
    this.childNodes = [];
    this.append(...nodes);
  }

  remove() {
    if (!this.parent) return;
    this.parent.childNodes = this.parent.childNodes.filter((node) => node !== this);
    this.parent = null;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }

  addEventListener(type, listener) {
    (this.listeners[type] = this.listeners[type] || []).push(listener);
  }

  dispatch(type, event) {
    (this.listeners[type] || []).forEach((listener) => listener(event || { currentTarget: this }));
  }

  getBoundingClientRect() {
    return BOX;
  }

  descendants() {
    return this.children.flatMap((child) => [child, ...child.descendants()]);
  }

  // The three selector shapes the page uses: .class, [data-name], tag[attr=value].
  querySelectorAll(selector) {
    let test;
    if (selector.startsWith(".")) {
      const wanted = selector.slice(1);
      test = (node) => node.className.split(/\s+/).includes(wanted);
    } else if (/^\[data-[a-z-]+\]$/.test(selector)) {
      const key = selector.slice(6, -1);
      test = (node) => key in node.dataset;
    } else {
      const match = /^([a-z]+)\[([a-z]+)=([a-z]+)\]$/.exec(selector);
      if (!match) throw new Error(`selector not supported by the test DOM: ${selector}`);
      test = (node) => node.tagName === match[1] && node[match[2]] === match[3];
    }
    return this.descendants().filter(test);
  }

  find(className) {
    return this.querySelectorAll(`.${className}`);
  }
}

FakeText.prototype.remove = FakeElement.prototype.remove;

/* The ids index.html declares, and nothing else: a getElementById for an id the
   markup does not carry returns null, as it would in the browser. */
function staticIds() {
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8");
  return [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => match[1]);
}

/* Load app.js into a fresh page. `fetch` answers the status request; the page's
   start-up is held back (readyState "loading") until `page.start()`. */
function openPage({ fetch, search = "" } = {}) {
  const body = new FakeElement("body");
  staticIds().forEach((id) => {
    const node = new FakeElement("div");
    node.id = id;
    body.append(node);
  });

  const ready = [];
  const document = {
    readyState: "loading",
    visibilityState: "visible",
    title: "",
    body,
    documentElement: new FakeElement("html"),
    createElement: (tag) => new FakeElement(tag),
    createElementNS: (_namespace, tag) => new FakeElement(tag),
    createTextNode: (text) => new FakeText(text),
    getElementById: (id) => body.descendants().find((node) => node.id === id) || null,
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    addEventListener: (type, listener) => {
      if (type === "DOMContentLoaded") ready.push(listener);
    },
  };
  const page = { reloads: 0 };
  const window = {
    document,
    innerWidth: 1280,
    location: { search, reload: () => (page.reloads += 1) },
    localStorage: { getItem: () => null, setItem: () => {} },
    // Timers never fire: a test drives the page by calling it.
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    addEventListener: () => {},
  };

  const context = vm.createContext({ window, fetch, URL, URLSearchParams, console });
  vm.runInContext(fs.readFileSync(path.join(WEB, "app.js"), "utf8"), context, { filename: "app.js" });

  return Object.assign(page, {
    document,
    byId: (id) => document.getElementById(id),
    // Any top-level function of the script, by name.
    call: (name, ...args) => context[name](...args),
    start: () => ready.forEach((listener) => listener()),
  });
}

function response(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    type: "basic",
    json: async () => {
      if (body === undefined) throw new SyntaxError("not JSON");
      return body;
    },
  };
}

module.exports = { FakeElement, openPage, response };
