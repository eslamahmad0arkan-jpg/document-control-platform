import { el, clear, fmtBytes, fmtRel, ICONS } from "./ui.js";
import { apiGet } from "./api.js";
import { openFilePreview } from "./viewer.js";

let active = null;
let input = null;
let results = null;
let items = [];
let hl = -1;
let timer = null;

function close() {
  if (!active) return;
  active.remove();
  active = null;
  document.removeEventListener("keydown", onKey);
}

function onKey(e) {
  if (e.key === "Escape") { close(); return; }
  if (e.key === "ArrowDown") { e.preventDefault(); move(1); return; }
  if (e.key === "ArrowUp") { e.preventDefault(); move(-1); return; }
  if (e.key === "Enter" && hl >= 0 && items[hl]) { e.preventDefault(); pick(items[hl]); }
}

function move(dir) {
  if (!items.length) return;
  hl = (hl + dir + items.length) % items.length;
  renderResults();
}

function pick(it) {
  close();
  if (it.kind === "PROJECT") {
    location.hash = `#/projects/${it.project_id}`;
  } else if (it.kind === "FOLDER") {
    location.hash = `#/projects/${it.project_id}/explorer?folder=${encodeURIComponent(it.drive_id)}`;
    document.querySelector("#view").scrollIntoView({ top: 0 });
  } else if (it.kind === "FILE") {
    openFilePreview({ id: it.project_id }, {
      drive_id: it.drive_id, name: it.name, path: it.path,
      extension: it.extension, mime_type: it.mime_type || "",
      size: it.size, modified_time: it.modified_time, drive_url: it.drive_url,
    });
  }
  window.dispatchEvent(new Event("hashchange"));
}

async function run() {
  const q = input.value.trim();
  clear(results);
  items = [];
  hl = -1;
  if (!q) {
    results.append(el("div", { class: "pal-hint" },
      "Type to search across every project — folders, files, and projects will appear here."));
    return;
  }
  results.append(el("div", { class: "muted", style: "padding:14px" }, "Searching…"));
  try {
    const d = await apiGet(`/api/search?q=${encodeURIComponent(q)}`);
    clear(results);
    items = d.items || [];
    renderResults();
  } catch (e) {
    clear(results);
    results.append(el("div", { class: "pal-hint" }, e.message));
  }
}

function renderResults() {
  clear(results);
  if (!items.length) {
    results.append(el("div", { class: "pal-hint" }, "No matches found."));
    return;
  }

  let lastProj = null;
  items.forEach((it, i) => {
    if (it.project_name !== lastProj) {
      lastProj = it.project_name;
      clear(results);
      results.append(el("div", { class: "pal-group" }, lastProj));
    }
    const isFile = it.kind === "FILE";
    const row = el("div", {
      class: "pal-item" + (i === hl ? " hl" : ""),
      onmouseenter: () => { hl = i; },
      onclick: () => pick(it),
    },
      el("span", { class: "pal-ico" }, it.kind === "PROJECT" ? ICONS.grid
        : it.kind === "FOLDER" ? ICONS.folder : ICONS.file),
      el("span", { class: "pal-name" }, it.name),
      el("span", { class: "pal-meta" },
        it.kind === "FILE" && it.extension ? "." + it.extension + " · " + fmtBytes(it.size) : it.kind));
    if (it.kind === "FILE") {
      row.append(el("span", { class: "kbd" }, "↵"));
    }
    results.append(row);
  });
  const footer = el("div", { class: "pal-foot" },
    el("span", {}, "↑↓ navigate · ↵ open · esc close"));
  results.append(footer);
}

export function togglePalette() {
  if (active) { close(); return; }

  const inputEl = el("input", {
    class: "pal-input", placeholder: "Search projects, folders, files…  (Ctrl K)",
    autocomplete: "off", spellcheck: "false",
    oninput: () => { clearTimeout(timer); timer = setTimeout(run, 250); },
  });
  const resultsEl = el("div", { class: "pal-results" });
  const panel = el("div", { class: "pal-panel" },
    el("div", { class: "pal-head" },
      el("span", { class: "pal-ico big" }, ICONS.grid),
      inputEl,
      el("span", { class: "kbd" }, "ESC")),
    resultsEl);
  const ov = el("div", { class: "pal-backdrop", onclick: (e) => { if (e.target === ov) close(); } }, panel);

  input = inputEl;
  results = resultsEl;
  active = ov;
  document.getElementById("modal-root").append(ov);
  document.addEventListener("keydown", onKey);
  inputEl.focus();
  run();
}

export function registerPalette() {
  window.addEventListener("keydown", (e) => {
    if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      togglePalette();
    } else if (e.key === "/" && !/input|textarea|select/i.test((e.target.tagName || ""))) {
      e.preventDefault();
      togglePalette();
    }
  });
}

export function openPalette() {
  if (!active) togglePalette();
  else { close(); togglePalette(); }
}