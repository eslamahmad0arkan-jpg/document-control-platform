import { apiGet, apiPatch } from "../api.js";
import { state, isAdmin } from "../state.js";
import { el, clear, setPageHead, loadingView, toast, avatar, fmtRel, fmtDate, pagination, emptyState, statTile } from "../ui.js";

let usersCache = [];
let userMap = {};
let filterRole = "";
let filterQ = "";

export async function render() {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view wide";
  if (!isAdmin()) {
    view.append(setPageHead("Admin", ""));
    view.append(emptyState("Restricted", "Administrator access is required."));
    return;
  }

  view.append(setPageHead("Administration", "Users, roles, audit log and monitor worker status."));

  const wrap = el("div", { id: "admin-wrap" });
  view.append(wrap);
  wrap.append(loadingView("Loading…"));

  const [users, worker] = await Promise.all([
    apiGet("/api/admin/users").catch(() => []),
    apiGet("/api/admin/worker").catch(() => null),
  ]);

  usersCache = users;
  userMap = {};
  for (const u of users) userMap[u.id] = u;

  clear(wrap);

  if (worker) {
    const w = el("div", { class: "card mb" },
      el("div", { class: "card-head" }, el("h3", {}, "Monitoring worker")),
      el("div", { class: "card-body" },
        el("div", { class: "kv" },
          el("span", { class: "k" }, "Enabled"), el("span", {}, worker.enabled ? "Yes" : "No"),
          el("span", { class: "k" }, "Running"), el("span", {}, worker.running ? "Yes" : "No"),
          el("span", { class: "k" }, "Interval"), el("span", {}, worker.interval_seconds + "s"),
          el("span", { class: "k" }, "Last loop"), el("span", {}, fmtDate(worker.last_loop_at)))));
    wrap.append(w);
  }

  const roles = { ADMIN: 0, PROJECT_MANAGER: 0, EDITOR: 0, VIEWER: 0 };
  for (const u of users) roles[u.role] = (roles[u.role] || 0) + 1;
  const active = users.filter(u => u.is_active).length;
  wrap.append(el("div", { class: "stats" },
    statTile("Total users", users.length),
    statTile("Active", active, "t-green"),
    statTile("Administrators", roles.ADMIN || 0, "t-purple"),
    statTile("Managers", roles.PROJECT_MANAGER || 0, "t-teal"),
    statTile("Editors", (roles.EDITOR || 0) + (roles.VIEWER || 0), "t-amber")));

  const search = el("input", { class: "input grow sm", id: "admin-search", placeholder: "Filter by name or email…",
    oninput: () => { filterQ = search.value.trim().toLowerCase(); paint(); } });
  const chips = el("div", { class: "chips" },
    ...["", "ADMIN", "PROJECT_MANAGER", "EDITOR", "VIEWER"].map(r =>
      el("button", { class: "chip-btn" + (filterRole === r ? " active" : ""),
        onclick: () => { filterRole = r; for (const c of chips.children) c.classList.toggle("active", c.textContent === (r === "" ? "All" : r)); paint(); } },
        r === "" ? "All" : r)));

  const auditBtn = el("button", { class: "btn sm", onclick: loadAudit }, "Load audit log");
  const scope = el("div", { class: "toolbar" }, search, chips);
  const usersCard = el("div", { class: "card" },
    el("div", { class: "card-head" }, scope, auditBtn),
    el("div", { class: "card-body flush" },
      el("table", { class: "table" },
        el("thead", {}, el("tr", {},
          el("th", {}, "User"), el("th", {}, "Email"), el("th", {}, "Role"),
          el("th", {}, "Projects"), el("th", {}, "Last login"), el("th", {}, "Created"), el("th", {}, "Actions"))),
        el("tbody", { id: "users-tbody" }))));
  wrap.append(usersCard);
  wrap.append(el("div", { id: "audit-wrap", class: "mt" }));

  function paint() {
    const tb = usersCard.querySelector("#users-tbody");
    clear(tb);
    const rows = usersCache
      .filter(u => (filterRole ? u.role === filterRole : true))
      .filter(u => !filterQ || (u.email || "").toLowerCase().includes(filterQ) || (u.name || "").toLowerCase().includes(filterQ));
    if (!rows.length) {
      tb.append(el("tr", {}, el("td", { colspan: "7", class: "muted" }, "No users match.")));
      return;
    }
    for (const u of rows) tb.append(userRow(u));
  }
  paint();
}

function userRow(u) {
  const me = state.me && state.me.id === u.id;
  const sel = el("select", { class: "select", style: "width:auto", disabled: me,
    onchange: e => changeRole(u, e.target.value) },
    ["ADMIN", "PROJECT_MANAGER", "EDITOR", "VIEWER"].map(r =>
      el("option", { value: r, selected: u.role === r ? true : undefined }, r)));
  return el("tr", {},
    el("td", {}, el("div", { class: "flex" }, avatar(u.email || u.name, 26), u.name || "—")),
    el("td", { class: "muted" }, u.email),
    el("td", {}, sel),
    el("td", { class: "muted" }, u.projects_count != null ? u.projects_count : "—"),
    el("td", { class: "muted" }, u.last_login_at ? fmtRel(u.last_login_at) : "—"),
    el("td", { class: "muted" }, fmtDate(u.created_at)),
    el("td", { class: "actions" },
      el("button", { class: "btn sm ghost" + (u.is_active ? " danger" : ""), disabled: me,
        onclick: () => toggleActive(u) }, u.is_active ? "Deactivate" : "Activate")));
}

async function changeRole(u, role) {
  if (state.me && state.me.id === u.id) { toast("You can't change your own role", "err"); return; }
  try {
    await apiPatch(`/api/admin/users/${u.id}`, { role });
    toast("Role updated");
    u.role = role;
    document.getElementById("view").replaceChildren();
    render();
  } catch (e) { toast(e.message, "err"); }
}

async function toggleActive(u) {
  if (state.me && state.me.id === u.id) { toast("You can't deactivate yourself", "err"); return; }
  try {
    await apiPatch(`/api/admin/users/${u.id}`, { is_active: !u.is_active });
    toast("User updated");
    u.is_active = !u.is_active;
    document.getElementById("view").replaceChildren();
    render();
  } catch (e) { toast(e.message, "err"); }
}

async function loadAudit() {
  const wrap = document.getElementById("audit-wrap");
  clear(wrap);
  wrap.append(loadingView("Loading audit log…"));
  try {
    const data = await apiGet("/api/admin/audit?per_page=30");
    renderAudit(wrap, data, 1);
  } catch (e) {
    clear(wrap);
    wrap.append(el("p", { class: "muted" }, e.message));
  }
}

function username(u) {
  if (!u) return "System";
  return u.name || u.email || "User #" + u.id;
}

function renderAudit(wrap, data, page) {
  clear(wrap);
  if (!data.total) {
    wrap.append(el("p", { class: "muted" }, "No audit entries yet."));
    return;
  }
  const card = el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Audit log")),
    el("div", { class: "card-body flush" },
      el("table", { class: "table" },
        el("thead", {}, el("tr", {}, el("th", {}, "When"), el("th", {}, "User"), el("th", {}, "Action"), el("th", {}, "Resource"))),
        el("tbody", {}, data.items.map(a => {
          const u = a.user_id ? userMap[a.user_id] : null;
          const cell = u
            ? el("span", { title: u.email }, avatar(u.email || u.name, 22), " ", u.name || u.email)
            : el("span", { class: "muted" }, "System");
          return el("tr", {},
            el("td", { class: "muted" }, fmtDate(a.created_at)),
            el("td", {}, cell),
            el("td", {}, el("code", {}, a.action)),
            el("td", { class: "muted" }, a.resource_type ? `${a.resource_type} ${a.resource_id || ""}` : "—"));
        })))));
  wrap.append(card);
  wrap.append(pagination(data.total, data.per_page, page, p => loadAuditPage(p)));
}

async function loadAuditPage(page) {
  const wrap = document.getElementById("audit-wrap");
  try {
    const data = await apiGet(`/api/admin/audit?per_page=30&page=${page}`);
    renderAudit(wrap, data, page);
  } catch (e) { toast(e.message, "err"); }
}