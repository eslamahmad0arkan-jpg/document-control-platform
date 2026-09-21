import { apiGet, apiPost, pushPath } from "./api.js";
import { state, setMe, setUnread, isAdmin } from "./state.js";
import { renderNav, renderMe, updateNotifyBadge, clear, el } from "./ui.js";
import * as loginPage from "./pages/login.js";
import * as projectsPage from "./pages/projects.js";
import * as dashboardPage from "./pages/project-dashboard.js";
import * as explorerPage from "./pages/explorer.js";
import * as activitiesPage from "./pages/activities.js";
import * as notificationsPage from "./pages/notifications.js";
import * as reportsPage from "./pages/reports.js";
import * as adminPage from "./pages/admin.js";
import * as settingsPage from "./pages/settings.js";
import { registerPalette, openPalette } from "./palette.js";

const pageModuleMap = {
  "projects": projectsPage,
  "projects/:id": dashboardPage,
  "projects/:id/explorer": explorerPage,
  "projects/:id/reports": reportsPage,
  "activities": activitiesPage,
  "notifications": notificationsPage,
  "reports": reportsPage,
  "admin": adminPage,
  "settings": settingsPage,
  "login": loginPage,
};

function parse(hash) {
  const h = hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  return {
    segs: path.split("/").filter(Boolean),
    query: new URLSearchParams(query || ""),
  };
}

function match(segs) {
  const key = segs.join("/");
  const candidates = ["projects/:id/reports", "projects/:id/explorer", "projects/:id", key];
  for (const k of candidates) {
    const ks = k.split("/");
    if (ks.length !== segs.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < ks.length; i++) {
      if (ks[i].startsWith(":")) params[ks[i].slice(1)] = segs[i];
      else if (ks[i] !== segs[i]) { ok = false; break; }
    }
    if (ok) return { page: pageModuleMap[k] ? k : k, params };
  }
  return { page: null, params: {} };
}

let viewEl = null;

async function route() {
  const { segs, query } = parse(location.hash || "#/");

  if (!segs.length) {
    if (!state.me) { pushPath("/login"); return; }
    pushPath("/projects");
    return;
  }
  const matched = match(segs);

  if (segs[0] === "login") {
    if (state.me) { pushPath("/projects"); return; }
    renderLoginShell();
    try { await loginPage.render(); } catch (e) { handleError(e); }
    return;
  }

  if (!state.me) {
    pushPath("/login");
    return;
  }
  renderAppShell();
  updateNotifyBadge();

  viewEl = document.getElementById("view");
  clear(viewEl);

  const mod = pageModuleMap[matched.page];
  if (!mod) {
    viewEl.append(el("div", { class: "empty-state" }, el("div", { class: "big" }, "404"), el("p", { style: "font-weight:600" }, "Page not found"), el("a", { href: "#/projects", class: "link" }, "Go to projects")));
    return;
  }
  try {
    await mod.render({ ...matched.params, query });
  } catch (e) {
    handleError(e);
  }
}

function handleError(e) {
  clear(viewEl || document.body);
  viewEl.append(el("div", { class: "empty-state" },
    el("div", { class: "big" }, "⚠"),
    el("p", { style: "font-weight:600" }, e.message || "Something went wrong."),
    el("button", { class: "btn mt", onclick: () => location.reload() }, "Reload")));
}

function renderLoginShell() {
  const shell = document.getElementById("shell");
  shell.hidden = false;
  const sb = document.getElementById("sidebar");
  sb.hidden = true;
  sb.classList.add("hidden");
  document.getElementById("topbar").hidden = true;
  const mainCol = document.querySelector(".main-col");
  if (mainCol) mainCol.style.marginLeft = "0";
}

function renderAppShell() {
  const shell = document.getElementById("shell");
  shell.hidden = false;
  const sb = document.getElementById("sidebar");
  sb.hidden = false;
  sb.classList.remove("hidden");
  document.getElementById("topbar").hidden = false;
  const mainCol = document.querySelector(".main-col");
  if (mainCol) mainCol.style.marginLeft = "";
  renderNav(document.getElementById("nav"));
  renderMe(document.getElementById("me-badge"));
  setCrumb();
}

function setCrumb() {
  const c = document.getElementById("crumb");
  const { segs } = parse(location.hash || "#/");
  const parts = [];
  if (segs.length === 0) { parts.push(el("span", { class: "current" }, "Dashboard")); }
  const labels = { projects: "Projects", activities: "Activity", notifications: "Notifications", reports: "Reports", admin: "Admin", settings: "Settings" };
  let acc = "";
  segs.forEach((s, i) => {
    const label = s.startsWith("p=") ? "" : (labels[s] || (state.currentProject && s === String(state.currentProject.id) ? state.currentProject.name : s)) || s;
    if (i < segs.length - 1) {
      acc += "/" + s;
      parts.push(el("a", { href: "#" + acc }, label), el("span", { class: "sep" }, "›"));
    } else {
      parts.push(el("span", { class: "current" }, label));
    }
  });
  clear(c);
  c.append(...parts);
}

async function refreshUnread() {
  try {
    const r = await apiGet("/api/notifications/unread-count");
    setUnread(r.total || 0);
    updateNotifyBadge();
    renderNav(document.getElementById("nav"));
  } catch {}
}

async function ensureTheme() {
  try {
    const r = await apiGet("/api/settings");
    applyTheme(r.theme || "dark");
  } catch {}
}

export function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  const btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.innerHTML = t === "dark"
      ? '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'
      : '<svg viewBox="0 0 24 24"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  }
}

async function init() {
  window.addEventListener("hashchange", route);
  window.addEventListener("app:unauthorized", () => {
    state.me = null;
    setUnread(0);
    pushPath("/login");
    location.reload();
  });
  document.getElementById("theme-toggle").addEventListener("click", async () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { await apiPatch("/api/settings", { theme: next }); } catch {}
  });
  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("hidden");
  });

  const gsBtn = document.getElementById("global-search-btn");
  if (gsBtn) gsBtn.addEventListener("click", openPalette);
  registerPalette();

  try {
    const me = await apiGet("/api/auth/me");
    setMe(me.user);
    setUnread(me.unread_notifications || 0);
  } catch {
    setMe(null);
  }
  await ensureTheme();
  route();
  if (state.me) setInterval(refreshUnread, 15000);
}

init();