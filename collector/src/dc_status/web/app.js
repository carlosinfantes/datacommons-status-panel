"use strict";

async function load() {
  const target = document.getElementById("document");
  try {
    const response = await fetch("/api/v1/all", { cache: "no-store" });
    target.textContent = JSON.stringify(await response.json(), null, 2);
  } catch (error) {
    target.textContent = "The status service is unreachable.";
  }
}

load();
