import { apiGet, apiPost, pushPath } from "../api.js";
import { state, canManageProjects } from "../state.js";
import { el, clear, toast, openModal, closeModal, setPageHead, loadingView, emptyState, ICONS, fmtRel, driveLink } from "../ui.js";

export async function render() {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";
  state.currentProject = null;

  const newBtn = canManageProjects()
    ? el("button", { class: "btn primary", onclick: openCreateModal }, ICONS.folder, "New Project")
    : null;
  view.append(setPageHead("Projects", "Connect Google Drive folders and monitor activity, versions and documents.", newBtn ? [newBtn] : []));

  const grid = el("div", { class: "grid grid-4", id: "proj-grid" });
  view.append(grid);
  grid.append(loadingView("Loading projects…"));

  let items = [];
  try {
    items = await apiGet("/api/projects");
  } catch (e) {
    clear(view);
    view.append(setPageHead("Projects", ""));
    view.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
    return;
  }

  clear(grid);
  if (!items.length) {
    grid.append(emptyState("No projects yet", "Create a project and connect a Google Drive folder to start monitoring."));
    return;
  }

  const detail = await Promise.all(items.map(p => apiGet(`/api/projects/${p.id}`).then(d => ({ ...p, ...d })).catch(() => p)));
  for (const p of detail) await renderCard(grid, p);
}

function statusChip(p) {
  if (!p.sync) return el("span", { class: "chip " + (p.status || "ACTIVE") }, p.status || "ACTIVE");
  if (p.sync.last_error) return el("span", { class: "chip sync-err", title: p.sync.last_error }, "⚠ Sync error");
  if (p.sync.scanning_now) return el("span", { class: "chip sync-running" }, "Syncing…");
  return el("span", { class: "chip sync-ok" },
    p.sync.last_incremental_at ? "Synced " + fmtRel(p.sync.last_incremental_at) : "Synced");
}

async function renderCard(grid, p) {
  const meta = el("div", { class: "pc-meta" },
    statusChip(p),
    el("span", {}, p.stats ? `${p.stats.total_files} files` : "—"));
  const card = el("div", { class: "proj-card", onclick: () => pushPath(`/projects/${p.id}`) },
    el("div", { class: "pc-top" },
      el("div", { class: "pc-color", style: `background:${p.color || "#0a84ff"}` }),
      el("h3", {}, p.name),
    ),
    el("div", { class: "desc" }, p.description || el("span", { class: "muted" }, "No description")),
    meta,
  );
  grid.append(card);
}

function openCreateModal() {
  const name = el("input", { class: "input", placeholder: "e.g. Al-Alexandria Tower", id: "np-name" });
  const folderId = el("input", { class: "input", placeholder: "Google Drive Folder ID", id: "np-folder" });
  const desc = el("textarea", { class: "input", rows: 2, placeholder: "Optional description", id: "np-desc" });
  const color = el("input", { class: "input", type: "color", value: "#0a84ff", id: "np-color", style: "height:38px;width:80px;padding:2px" });

  const body = el("div", {},
    el("div", { class: "field" }, el("label", {}, "Project name"), name),
    el("div", { class: "field" },
      el("label", {}, "Google Drive folder ID"),
      folderId,
      el("p", { class: "muted", style: "font-size:11px;margin-top:5px" },
        "Open the folder in Google Drive and copy the ID from the URL (letters/digits after /folders/).")),
    el("div", { class: "field" }, el("label", {}, "Description"), desc),
    el("div", { class: "field" }, el("label", {}, "Color"), el("div", { class: "row" }, color)),
  );
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: submit }, "Create project"),
  );

  async function submit() {
    if (!name.value.trim() || !folderId.value.trim()) {
      toast("Name and folder ID are required", "err");
      return;
    }
    try {
      await apiPost("/api/projects", {
        name: name.value.trim(),
        google_folder_id: folderId.value.trim(),
        description: desc.value.trim() || null,
        color: color.value,
      });
      closeModal();
      toast("Project created");
      render();
    } catch (e) {
      toast(e.message, "err");
    }
  }
  openModal({ title: "New project", body, footer: foot, wide: true });
}