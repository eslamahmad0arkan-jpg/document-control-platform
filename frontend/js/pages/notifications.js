import { apiGet, apiPost, pushPath } from "../api.js";
import { state, setUnread } from "../state.js";
import { el, clear, setPageHead, loadingView, fmtRel, emptyState, toast, pagination } from "../ui.js";

let projectNames = new Map();

async function loadProjects() {
  if (projectNames.size) return;
  try {
    const projects = await apiGet("/api/projects");
    for (const p of projects) projectNames.set(p.id, p.name);
  } catch {}
}

export async function render({ query }) {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";

  const unreadOnly = query.get("unread") === "1";
  await loadProjects();

  view.append(setPageHead("Notifications", "Changes detected by the monitor across your projects.",
    [el("button", { class: "btn", onclick: readAll }, "Mark all as read")]));

  const chips = el("div", { class: "chips mt" },
    el("button", { class: "chip-btn" + (!unreadOnly ? " active" : ""), onclick: () => pushPath("/notifications") }, "All"),
    el("button", { class: "chip-btn" + (unreadOnly ? " active" : ""), onclick: () => pushPath("/notifications?unread=1") }, "Unread"));
  view.append(chips);

  const wrap = el("div", { id: "notif-list" });
  view.append(wrap);
  wrap.append(loadingView("Loading…"));

  const page = Number(query.get("page") || 1);
  try {
    const qs = new URLSearchParams({ sort: "created_at", order: "desc", per_page: "25", page: String(page) });
    if (unreadOnly) qs.set("unread_only", "true");
    const data = await apiGet(`/api/notifications?${qs}`);
    renderList(wrap, data, page, unreadOnly);
  } catch (e) {
    clear(wrap);
    wrap.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
  }

  async function readAll() {
    try {
      await apiPost("/api/notifications/read-all");
      setUnread(0);
      render({ query: new URLSearchParams() });
    } catch (e) { toast(e.message, "err"); }
  }
}

function renderList(wrap, data, page, unreadOnly) {
  clear(wrap);
  if (!data.total) {
    wrap.append(emptyState("All caught up",
      unreadOnly ? "You have no unread notifications." : "New file changes will appear here as the monitor detects them."));
    return;
  }
  const card = el("div", { class: "card" });
  const ul = el("ul", { class: "feed" });
  for (const n of data.items) {
    const pname = projectNames.get(n.project_id);
    const row = el("li", { style: n.is_read ? "opacity:.66" : "background:color-mix(in srgb, var(--accent) 6%, transparent)",
      onclick: () => markRead(n, render) },
      el("span", { class: "f-ico" }, n.is_read ? "✔" : "●"),
      el("div", { class: "f-main", style: "cursor:pointer" },
        el("div", { class: "f-title" }, el("strong", {}, n.title)),
        n.description ? el("div", { class: "f-sub" }, n.description) : null,
        el("div", { class: "f-sub" },
          pname ? el("span", { class: "chip sync-ok" }, pname) : null,
          n.type ? el("span", { class: "muted" }, n.type) : null,
          el("a", { class: "link", href: `#/projects/${n.project_id}/explorer` }, "open project ↗"))),
      n.is_read ? null : el("button", { class: "btn sm ghost", onclick: (e) => { e.stopPropagation(); markRead(n, render); } }, "Mark read"),
      el("span", { class: "f-time" }, fmtRel(n.created_at)));
    ul.append(row);
  }
  card.append(el("div", { class: "card-body flush" }, ul));
  wrap.append(card);
  wrap.append(pagination(data.total, data.per_page, page,
    p => pushPath(`/notifications?page=${p}${unreadOnly ? "&unread=1" : ""}`)));

  async function markRead(n, reread) {
    if (n.is_read) return;
    try {
      await apiPost(`/api/notifications/${n.id}/read`);
      setUnread(Math.max(0, state.unread - 1));
      reread({ query: new URLSearchParams(location.hash.split("?")[1] || "") });
    } catch {}
  }
}