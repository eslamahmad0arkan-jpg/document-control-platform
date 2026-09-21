import { apiGet, apiPost, apiPatch, apiDelete, pushPath } from "../api.js";
import { state } from "../state.js";
import {
  el, clear, toast, openModal, closeModal, confirmDialog, setPageHead, statTile,
  loadingView, fmtRel, fmtBytes, fmtDate, driveLink, avatar,
} from "../ui.js";
import { canManageProjects } from "../state.js";
import { openFilePreview, openFolderView, openProjectRootView, folderCard, fileRow as viewerFileRow } from "../viewer.js";

let polling = null;

export async function render({ id }) {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";
  stopPolling();

  const head = el("div", {});
  head.append(loadingView("Loading project…"));
  view.append(head);

  let p = null;
  try {
    p = await apiGet(`/api/projects/${id}`);
  } catch (e) {
    clear(view);
    view.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
    return;
  }
  state.currentProject = p;

  const canEdit = ["ADMIN", "PROJECT_MANAGER", "EDITOR"].includes(state.me.role)
    && (p.role === "PROJECT_MANAGER" || p.role === "EDITOR" || state.me.role === "ADMIN" || state.me.role === "PROJECT_MANAGER");
  const admin = state.me.role === "ADMIN" || p.role === "PROJECT_MANAGER";

  const actions = [
    el("a", { class: "btn", href: `#/projects/${id}/explorer` }, "Open Explorer"),
    el("a", { class: "btn", href: `#/projects/${id}/reports` }, "Reports"),
    el("a", { class: "btn", href: `#/projects/${id}/activities` }, "Activity"),
  ];
  if (admin) actions.push(el("button", { class: "btn", onclick: () => editModal(p) }, "Edit"));

  clear(head);
  head.append(setPageHead(
    el("span", {}, el("span", { class: "dot", style: `background:${p.color || "#0a84ff"}` }), p.name),
    el("span", {},
      p.description || "No description", " · ",
      el("a", { class: "link", href: driveLink(p, p.google_folder_id), target: "_blank", rel: "noopener" }, "Open in Drive ↗")),
    actions,
  ));

  const stats = el("div", { class: "stats", id: "project-stats" });
  renderStats(stats, p.stats);
  view.append(stats);

  const filesCard = el("div", { class: "card" },
    el("div", { class: "card-head" },
      el("h3", {}, "Files & Folders"),
      el("span", { class: "muted", style: "font-size:12px" }, "Click a folder to see its files, or a file to open it directly — no Google login needed."),
      el("a", { class: "link", href: `#/projects/${id}/explorer` }, "Open Explorer ↗")),
    el("div", { class: "card-body flush", id: "dashboard-files" }, loadingView("Loading files…")));
  view.append(filesCard);
  renderFilesSection(filesCard.querySelector("#dashboard-files"), p);

  const left = el("div", { class: "card", id: "sync-card" });
  const right = el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Recent activity"), el("a", { class: "link", href: `#/projects/${id}/activities` }, "View all →")),
    el("div", { class: "card-body flush", id: "mini-feed" }, loadingView("Loading…")));
  const grid = el("div", { class: "grid grid-2" }, left, right);
  view.append(grid);

  renderSyncCard(left, p);
  renderMiniFeed(right.querySelector("#mini-feed"), id);
  renderMembers(view, p);
}

function renderStats(container, stats) {
  const s = stats || {};
  clear(container);
  container.append(
    statTile("Total files", s.total_files || 0, "t-blue"),
    statTile("Folders", s.total_folders || 0),
    statTile("Added today", s.files_added_today || 0, "t-green"),
    statTile("Modified today", s.files_modified_today || 0, "t-amber"),
    statTile("Activities 24h", s.activities_24h || 0, "t-purple"),
    statTile("Active users", s.active_users || 0, "t-teal"),
    statTile("Stored size", fmtBytes(s.total_size), "t-red"),
  );
}

async function renderFilesSection(container, p) {
  try {
    clear(container);
    const data = await apiGet(`/api/projects/${p.id}/explorer?per_page=60`);
    const topFolders = data.folders || [];
    const rootFiles = data.files || [];

    if (!topFolders.length && !rootFiles.length) {
      container.append(el("p", { class: "muted" }, "No files or folders yet — sync in progress or folder is empty."));
      return;
    }

    if (topFolders.length) {
      const foldersGrid = el("div", { class: "folder-grid" });
      for (const fo of topFolders) {
        foldersGrid.append(folderCard(fo, (folder) => openFolderView(p, folder)));
      }
      container.append(foldersGrid);
    }

    if (rootFiles.length) {
      container.append(el("div", { class: "sub-head" }, "Root files"));
      const list = el("div", { class: "file-list" });
      for (const f of rootFiles) list.append(viewerFileRow(f, (file) => openFilePreview(p, file)));
      container.append(list);
    }

    const total = (data.total_folders || topFolders.length) + (data.total_files || rootFiles.length);
    if (total > topFolders.length + rootFiles.length) {
      container.append(el("div", { style: "margin-top:10px" },
        el("button", { class: "btn sm", onclick: () => openProjectRootView(p) },
          `View all ${total} items →`)));
    }
  } catch (e) {
    clear(container);
    container.append(el("p", { class: "muted" }, e.message));
  }
}

function renderSyncCard(card, p) {
  clear(card);
  const sync = p.sync || { scanning_now: false, phase: "idle" };
  const badge = document.createElement("span");
  const cls = sync.last_error ? "sync-err" : (sync.scanning_now ? "sync-running" : "sync-ok");
  badge.className = "chip " + cls;
  badge.textContent = sync.last_error ? "⚠ Error" : (sync.scanning_now ? "Running" : "Healthy");

  const body = el("div", { class: "card-body" },
    el("div", { class: "kv" },
      el("span", { class: "k" }, "Status"), el("span", {}, badge),
      el("span", { class: "k" }, "Phase"), el("span", {}, sync.phase || "idle"),
      el("span", { class: "k" }, "Last full scan"), el("span", {}, fmtDate(sync.last_full_scan_at)),
      el("span", { class: "k" }, "Last incremental"), el("span", {}, fmtDate(sync.last_incremental_at)),
      el("span", { class: "k" }, "Consecutive errors"), el("span", {}, sync.consecutive_errors || 0),
    ),
    el("div", { id: "sync-bar-wrap" }),
    el("div", { class: "flex between mt" },
      el("button", { class: "btn primary", onclick: () => triggerSync(card, p), id: "sync-btn" }, "Sync now"),
      el("span", { class: "muted", style: "font-size:12px" }, "Monitored by the background worker")),
  );
  card.append(el("div", { class: "card-head" }, el("h3", {}, "Sync status")), body);
}

async function triggerSync(card, p) {
  const btn = document.getElementById("sync-btn");
  btn.disabled = true;
  btn.textContent = "Starting…";
  try {
    const sync = await apiPost(`/api/projects/${p.id}/sync`);
    toast("Sync started");
    startPolling(card, p);
  } catch (e) {
    toast(e.message, "err");
    btn.disabled = false;
    btn.textContent = "Sync now";
  }
}

function startPolling(card, p) {
  stopPolling();
  const barWrap = document.getElementById("sync-bar-wrap");
  polling = setInterval(async () => {
    try {
      const r = await apiGet(`/api/projects/${p.id}/scan-progress`);
      if (barWrap) {
        clear(barWrap);
        if (r.scanning) {
          const pct = Math.round(((r.done || 0) / Math.max(1, r.total || 1)) * 100);
          barWrap.append(
            el("div", { class: "sync-bar" }, el("div", { class: "fill", style: `width:${pct}%` })),
            el("div", { class: "muted", style: "font-size:12px;margin-top:6px" },
              `${r.phase} · ${r.done}/${r.total} items${r.last_error ? " · " + r.last_error : ""}`),
          );
        } else {
          stopPolling();
          const fresh = await apiGet(`/api/projects/${p.id}`);
          renderSyncCard(card, fresh);
          const statsEl = document.getElementById("project-stats");
          if (statsEl) renderStats(statsEl, fresh.stats);
          toast("Sync complete");
        }
      }
    } catch (e) {
      stopPolling();
    }
  }, 1500);
}

function stopPolling() {
  if (polling) { clearInterval(polling); polling = null; }
}

async function renderMiniFeed(feedWrap, id) {
  try {
    const page = await apiGet(`/api/projects/${id}/activities?per_page=7`);
    clear(feedWrap);
    const ul = el("ul", { class: "feed" });
    if (!page.items.length) ul.append(el("li", { class: "empty" }, "No activity recorded yet. Trigger a sync."));
    for (const a of page.items) {
      ul.append(el("li", {},
        el("span", { class: "f-ico" }, actorEmoji(a.action)),
        el("div", { class: "f-main" },
          el("div", { class: "f-title" },
            el("strong", {}, a.actor_name || a.actor_email || "System"), " ", actionVerb(a.action),
            " ", el("strong", {}, a.target_name)),
          el("div", { class: "f-sub" }, a.file_path || a.folder_path || "")),
        el("span", { class: "f-time" }, fmtRel(a.detected_at))));
    }
    feedWrap.append(ul);
  } catch {
    feedWrap.append(el("p", { class: "muted" }, "Could not load activity."));
  }
}

function renderMembers(view, p) {
  const card = el("div", { class: "card mt" },
    el("div", { class: "card-head" }, el("h3", {}, `Members (${(p.members || []).length})`),
      el("div", { class: "row" },
        p.role === "VIEWER" ? null : el("button", { class: "btn sm", onclick: () => memberModal(p) }, "Add member"),
        p.role === "VIEWER" ? null : el("button", { class: "btn sm", onclick: () => syncModal(p) }, "Pick Drive user"))));
  const table = el("table", { class: "table" },
    el("thead", {}, el("tr", {},
      el("th", {}, "User"), el("th", {}, "Email"), el("th", {}, "Role"),
      el("th", {}, "Contribution"), el("th", {}, "Last active"), el("th", {}))));
  const tbody = el("tbody");
  for (const m of p.members || []) {
    const tr = el("tr", {},
      el("td", {}, el("div", { class: "flex" }, avatar(m.email || m.name), m.name)),
      el("td", { class: "muted" }, m.email),
      el("td", {}, el("span", { class: "chip role" }, m.role)),
      el("td", { class: "muted", "data-contrib": m.user_id }, "…"),
      el("td", { class: "muted", "data-lastseen": m.user_id }, "…"),
      el("td", { class: "actions" },
        p.role === "VIEWER" ? null : el("button", { class: "btn sm ghost", onclick: () => changeRole(p, m) }, "Change"),
        p.role === "VIEWER" ? null : el("button", { class: "btn sm danger ghost", onclick: () => removeMember(p, m) }, "Remove")));
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(el("div", { class: "card-body flush" }, table));
  view.append(card);
  fillMemberStats(p);
}

async function fillMemberStats(p) {
  let stats;
  try { stats = await apiGet(`/api/projects/${p.id}/members/stats`); } catch { return; }
  for (const s of stats) {
    const contrib = document.querySelector(`[data-contrib="${s.user_id}"]`);
    if (contrib) {
      const parts = [];
      if (s.activities) parts.push(`${s.activities} activities`);
      if (s.uploads) parts.push(`${s.uploads} uploads`);
      if (s.comments) parts.push(`${s.comments} comments`);
      if (s.locks_active) parts.push(`🔒 ${s.locks_active} locks`);
      clear(contrib);
      if (!parts.length) contrib.append(el("span", {}, "—"));
      else for (const part of parts) contrib.append(el("span", { class: "contrib-chip" }, part));
    }
    const last = document.querySelector(`[data-lastseen="${s.user_id}"]`);
    if (last) last.textContent = s.last_activity_at ? fmtRel(s.last_activity_at) : "—";
  }
}

export function actionVerb(a) {
  return { CREATED: "created", MODIFIED: "modified", MOVED: "moved", RENAMED: "renamed",
    TRASHED: "trashed", UNTRASHED: "restored", CREATE_FOLDER: "created folder", UPLOAD_FILE: "uploaded" }[a] || a;
}
export function actorEmoji(a) {
  return { CREATED: "✨", MODIFIED: "✏️", MOVED: "⇄", RENAMED: "✎", TRASHED: "🗑", UNTRASHED: "↩", CREATE_FOLDER: "📁", UPLOAD_FILE: "⬆" }[a] || "◆";
}

function editModal(p) {
  const name = el("input", { class: "input", value: p.name, id: "ep-name" });
  const desc = el("textarea", { class: "input", rows: 2, id: "ep-desc" }, p.description || "");
  const color = el("input", { class: "input", type: "color", value: p.color || "#0a84ff", id: "ep-color", style: "height:38px;width:80px;padding:2px" });
  const status = el("select", { class: "select", id: "ep-status" },
    el("option", { value: "ACTIVE" }, "Active"),
    el("option", { value: "ARCHIVED" }, "Archived"));
  status.value = p.status || "ACTIVE";

  const body = el("div", {},
    el("div", { class: "field" }, el("label", {}, "Name"), name),
    el("div", { class: "field" }, el("label", {}, "Description"), desc),
    el("div", { class: "field" }, el("label", {}, "Color"), color),
    el("div", { class: "field" }, el("label", {}, "Status"), status));

  const foot = el("div", {},
    el("button", { class: "btn danger", onclick: () => confirmDialog("Delete project", "This only removes the project monitoring (Google Drive files are untouched). Continue?", async () => {
      try {
        await apiDelete(`/api/projects/${p.id}`);
        closeModal();
        toast("Project deleted");
        pushPath("/projects");
      } catch (e) { toast(e.message, "err"); }
    }) }, "Delete"),
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      try {
        await apiPatch(`/api/projects/${p.id}`, {
          name: name.value.trim(), description: desc.value.trim() || null,
          color: color.value, status: status.value,
        });
        closeModal();
        toast("Saved");
        render({ id: p.id });
      } catch (e) { toast(e.message, "err"); }
    } }, "Save"));
  openModal({ title: "Edit project", body, footer: foot, wide: true });
}

function memberModal(p) {
  const email = el("input", { class: "input", placeholder: "user@example.com", id: "mm-email" });
  const role = el("select", { class: "select", id: "mm-role" },
    el("option", { value: "EDITOR" }, "Editor"),
    el("option", { value: "VIEWER" }, "Viewer"),
    el("option", { value: "PROJECT_MANAGER" }, "Project manager"));
  const body = el("div", {},
    el("p", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      "The user must be a registered DriveDoc user (sign-in with Google creates the account automatically)."),
    el("div", { class: "field" }, el("label", {}, "Email"), email),
    el("div", { class: "field" }, el("label", {}, "Role"), role));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      if (!email.value.trim()) { toast("Email required", "err"); return; }
      try {
        await apiPost(`/api/projects/${p.id}/members`, { email: email.value.trim(), role: role.value });
        closeModal();
        toast("Member added");
        render({ id: p.id });
      } catch (e) { toast(e.message, "err"); }
    } }, "Add"));
  openModal({ title: "Add member", body, footer: foot });
}

function syncModal(p) {
  const email = el("input", { class: "input", placeholder: "person@google.com (via Google sidebar tools)", id: "sm-email" });
  const body = el("div", {},
    el("p", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      "Optional: record the Google user responsible for Drive changes. Not required for monitoring."),
    el("div", { class: "field" }, el("label", {}, "Google identity"), email));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      try {
        await apiPost(`/api/projects/${p.id}/sync`, { actor: email.value.trim() || null });
        closeModal();
        toast("Identity saved");
      } catch (e) { toast(e.message, "err"); }
    } }, "Save"));
  openModal({ title: "Sync identity", body, footer: foot });
}

function changeRole(p, m) {
  const role = el("select", { class: "select" },
    el("option", { value: "PROJECT_MANAGER" }, "Project manager"),
    el("option", { value: "EDITOR" }, "Editor"),
    el("option", { value: "VIEWER" }, "Viewer"));
  role.value = m.role;
  const body = el("div", {}, el("div", { class: "field" }, el("label", {}, `Role for ${m.email}`), role));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      try {
        await apiPatch(`/api/projects/${p.id}/members/${m.user_id}`, { role: role.value });
        closeModal();
        toast("Role updated");
        render({ id: p.id });
      } catch (e) { toast(e.message, "err"); }
    } }, "Save"));
  openModal({ title: "Change role", body, footer: foot });
}

function removeMember(p, m) {
  confirmDialog("Remove member", `Remove ${m.email} from this project? Their Drive access is managed by Google, not DriveDoc.`, async () => {
    try {
      await apiDelete(`/api/projects/${p.id}/members/${m.user_id}`);
      toast("Member removed");
      render({ id: p.id });
    } catch (e) { toast(e.message, "err"); }
  });
}