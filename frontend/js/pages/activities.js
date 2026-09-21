import { apiGet, pushPath } from "../api.js";
import { state } from "../state.js";
import {
  el, clear, setPageHead, loadingView, fmtRel, fmtDate, pagination, avatar, emptyState,
} from "../ui.js";
import { actionVerb, actorEmoji } from "./project-dashboard.js";

export async function render({ query }) {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";

  let projects = [];
  try { projects = await apiGet("/api/projects"); } catch {}
  const pid = query.get("project");

  const sel = el("select", { class: "select", id: "act-project",
    onchange: () => {
      const v = document.getElementById("act-project").value;
      pushPath(v ? `/projects/${v}/activities` : `/activities`);
    } },
    el("option", { value: "" }, "All projects"));
  for (const p of projects) sel.append(el("option", { value: p.id, selected: pid === String(p.id) ? true : undefined }, p.name));
  if (!pid) sel.value = "";

  const q = el("input", { class: "input grow", id: "act-q", placeholder: "Search name/path…", value: query.get("q") || "",
    onkeydown: e => { if (e.key === "Enter") go(); } });
  const action = el("select", { class: "select", id: "act-action" },
    el("option", { value: "" }, "All actions"),
    ...["CREATED", "MODIFIED", "MOVED", "RENAMED", "TRASHED", "UNTRASHED", "CREATE_FOLDER", "UPLOAD_FILE"]
      .map(a => el("option", { value: a, selected: query.get("action") === a ? true : undefined }, a)));

  const topright = el("div", {},
    el("button", { class: "btn primary", onclick: go, disabled: !pid }, "Apply"));
  view.append(setPageHead("Activity timeline",
    pid ? "Project activity log — every detected Google Drive change" : "Pick a project to view its activity log",
    [el("button", { class: "btn", onclick: () => pushPath("/projects") }, "Projects")]));

  const filterBar = el("div", { class: "filter-bar" },
    sel, action, q, topright);
  view.append(filterBar);

  const listWrap = el("div", { id: "act-list" });
  view.append(listWrap);
  listWrap.append(loadingView("Loading…"));

  if (!pid) {
    clear(listWrap);
    const grid = el("div", { class: "grid grid-4" });
    if (!projects.length) grid.append(emptyState("No projects", "Create a project to start tracking activity."));
    else for (const p of projects) {
      grid.append(el("div", { class: "proj-card", onclick: () => pushPath(`/projects/${p.id}/activities`) },
        el("div", { class: "pc-top" }, el("div", { class: "pc-color", style: `background:${p.color || "#0a84ff"}` }), el("h3", {}, p.name)),
        el("div", { class: "pc-meta" }, el("span", { class: "chip ACTIVE" }, "Open log →"))));
    }
    listWrap.append(grid);
    return;
  }

  const page = Number(query.get("page") || 1);

  function go() {
    const p = new URLSearchParams();
    if (document.getElementById("act-action").value) p.set("action", document.getElementById("act-action").value);
    if (document.getElementById("act-q").value.trim()) p.set("q", document.getElementById("act-q").value.trim());
    pushPath(`/projects/${document.getElementById("act-project").value}/activities?${p}`);
  }

  const params = new URLSearchParams();
  params.set("per_page", "25");
  params.set("page", String(page));
  for (const k of ["q", "action"]) { const v = query.get(k); if (v) params.set(k, v); }

  try {
    const data = await apiGet(`/api/projects/${pid}/activities?${params}`);
    renderList(listWrap, data, { pid, page });
  } catch (e) {
    clear(listWrap);
    listWrap.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
  }
}

function renderList(wrap, data, { pid, page }) {
  clear(wrap);
  if (!data.total) {
    wrap.append(emptyState("No activity found", "Try clearing filters, or trigger a sync from the project dashboard."));
    return;
  }
  const card = el("div", { class: "card" });
  const ul = el("ul", { class: "feed" });
  for (const a of data.items) {
    const link = a.drive_id ? `?folder=${a.drive_id}` : "";
    const li = el("li", {},
      el("span", { class: "f-ico" }, actorEmoji(a.action)),
      el("div", { class: "f-main" },
        el("div", { class: "f-title" },
          avatar(a.actor_name || a.actor_email, 16), " ",
          el("strong", {}, a.actor_name || a.actor_email || "System"),
          " ", actionVerb(a.action), " ",
          el("strong", {}, a.target_name)),
        el("div", { class: "f-sub" },
          a.file_path || a.folder_path || "",
          " · ", a.source,
          " · ", el("a", { class: "link", href: `#/projects/${pid}/explorer${link}` }, "open")),
        el("div", { class: "f-sub muted" }, fmtDate(a.detected_at))),
      el("span", { class: "f-time" }, fmtRel(a.detected_at)));
    ul.append(li);
  }
  card.append(el("div", { class: "card-body flush" }, ul));
  wrap.append(card);
  wrap.append(pagination(data.total, data.per_page, page,
    p => {
      const params = new URLSearchParams();
      params.set("page", String(p));
      for (const k of ["q", "action"]) { const v = location.hash.includes(k) ? new URLSearchParams(location.hash.split("?")[1] || "").get(k) : null; if (v) params.set(k, v); }
      pushPath(`/projects/${pid}/activities?${params}`);
    }));
}