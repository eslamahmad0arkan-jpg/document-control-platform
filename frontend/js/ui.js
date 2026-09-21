import { state, isAdmin, setUnread, canManageProjects } from "./state.js";
import { apiGet, apiPost } from "./api.js";

export const ICONS = {
  grid: '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="2" fill="currentColor"/><rect x="14" y="3" width="7" height="7" rx="2" fill="currentColor" opacity=".55"/><rect x="3" y="14" width="7" height="7" rx="2" fill="currentColor" opacity=".55"/><rect x="14" y="14" width="7" height="7" rx="2" fill="currentColor" opacity=".3"/></svg>',
  folder: '<svg viewBox="0 0 24 24"><path d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4l2 2.5h9A1.5 1.5 0 0 1 21 9v8.5a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  file: '<svg viewBox="0 0 24 24"><path d="M6 2h8l4 4v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/><path d="M14 2v4h4" fill="none" stroke="currentColor" stroke-width="2"/></svg>',
  activity: '<svg viewBox="0 0 24 24"><path d="M3 12h4l2.5-6 4 12 2.5-6h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bell: '<svg viewBox="0 0 24 24"><path d="M12 3a6 6 0 0 0-6 6v4.3l-1.7 2.4A1 1 0 0 0 5 17h14a1 1 0 0 0 .7-1.3L18 13.3V9a6 6 0 0 0-6-6M10 19a2 2 0 0 0 4 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chart: '<svg viewBox="0 0 24 24"><path d="M4 20V10m5 10V4m5 16v-7m5 7V7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  admin: '<svg viewBox="0 0 24 24"><circle cx="12" cy="8" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 20a7 7 0 0 1 14 0" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  settings: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="2"/><path d="M19 12a7 7 0 0 0-.14-1.4l2.1-1.6-2-3.4-2.5 1a7 7 0 0 0-2.4-1.4L13.7 2h-3.4l-.4 2.6a7 7 0 0 0-2.4 1.4l-2.5-1-2 3.4 2.1 1.6A7 7 0 0 0 5 12c0 .48.05.94.14 1.4l-2.1 1.6 2 3.4 2.5-1a7 7 0 0 0 2.4 1.4l.4 2.6h3.4l.4-2.6a7 7 0 0 0 2.4-1.4l2.5 1 2-3.4-2.1-1.6c.09-.46.14-.92.14-1.4z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  login: '<svg viewBox="0 0 24 24"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4M10 17l5-5-5-5M15 12H3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  home: '<svg viewBox="0 0 24 24"><path d="M4 11.5 12 4l8 7.5V19a1.5 1.5 0 0 1-1.5 1.5h-4.5v-5h-4v5H5.5A1.5 1.5 0 0 1 4 19z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  lock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  unlock: '<svg viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="9" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 11V8a4 4 0 0 1 7.5-2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  comment: '<svg viewBox="0 0 24 24"><path d="M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H9l-4 4V6a1 1 0 0 1 1-1z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  paperclip: '<svg viewBox="0 0 24 24"><path d="M21.4 11.4 12.8 20a5.6 5.6 0 0 1-7.9-7.9l8.5-8.5a3.9 3.9 0 0 1 5.5 5.5l-8.5 8.5a2 2 0 0 1-2.8-2.8l7.8-7.8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m3 0v12a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V7m4 4v6m4-6v6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    if (typeof c === "string" && /^\s*</.test(c)) node.insertAdjacentHTML("beforeend", c);
    else node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function currentPath() {
  const h = location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  return { segs: path.split("/").filter(Boolean), query: new URLSearchParams(query || "") };
}

export const AVATAR_COLORS = ["#0a84ff", "#8b5cf6", "#2fbf71", "#f5a623", "#14b8a6", "#e5484d"];
export function avatar(name, size = 30) {
  const initials = (name || "?").split(/\s+/).map(s => s[0]).join("").slice(0, 2).toUpperCase();
  let hash = 0;
  for (const ch of (name || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  const color = AVATAR_COLORS[hash % AVATAR_COLORS.length];
  const a = el("div", { class: "avatar", style: `width:${size}px;height:${size}px;font-size:${size * 0.38}px;background:${color}` });
  a.textContent = initials;
  return a;
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return (n / Math.pow(1024, i)).toFixed(i ? 1 : 0) + " " + units[i];
}

export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtRel(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  if (s < 86400 * 30) return Math.floor(s / 86400) + "d ago";
  return d.toLocaleDateString();
}

export function toast(msg, type = "info") {
  const t = el("div", { class: `toast ${type}` }, msg);
  document.getElementById("toast-root").append(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; }, 3400);
  setTimeout(() => t.remove(), 3800);
}

export function openModal({ title, body, footer, wide }) {
  const root = document.getElementById("modal-root");
  const m = el("div", { class: "modal" + (wide ? " wide" : "") },
    el("div", { class: "modal-head" }, el("h3", {}, title),
      el("button", { class: "x", onclick: () => closeModal() }, "×")),
    el("div", { class: "modal-body" }, body),
  );
  if (footer) m.append(el("div", { class: "modal-foot" }, footer));
  const backdrop = el("div", { class: "modal-backdrop", onclick: (e) => { if (e.target === backdrop) closeModal(); } }, m);
  root.append(backdrop);
  return backdrop;
}

export function closeModal() {
  const root = document.getElementById("modal-root");
  clear(root);
}

export function confirmDialog(title, message, onYes) {
  const body = el("div", {}, el("p", { class: "muted" }, message));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: () => closeModal() }, "Cancel"),
    el("button", { class: "btn danger", onclick: () => { closeModal(); onYes(); } }, "Confirm"),
  );
  openModal({ title, body, footer: foot });
}

export function renderNav(navEl) {
  const items = [
    { href: "#/projects", icon: "grid", label: "Projects" },
    { href: "#/activities", icon: "activity", label: "Activity" },
    { href: "#/notifications", icon: "bell", label: "Notifications", badge: () => state.unread },
    { href: "#/reports", icon: "chart", label: "Reports" },
  ];
  if (isAdmin()) items.push({ href: "#/admin", icon: "admin", label: "Admin" });
  items.push({ href: "#/settings", icon: "settings", label: "Settings" });

  clear(navEl);
  const path = location.hash.split("?")[0] || "#/";
  for (const it of items) {
    const a = el("a", { href: it.href, class: path === it.href ? "active" : "" }, ICONS[it.icon], it.label);
    if (it.badge && it.badge() > 0) a.append(el("span", { class: "badge" }, it.badge()));
    navEl.append(a);
  }
}

export function renderMe(navEl) {
  if (!state.me) return;
  clear(navEl);
  navEl.append(avatar(state.me.email, 32),
    el("div", { class: "who" }, el("strong", {}, state.me.name || state.me.email), el("span", { class: "role" }, state.me.role)),
    el("button", { class: "logout", title: "Sign out", onclick: logout }, "⏻"));
}

async function logout() {
  try { await apiPost("/api/auth/logout"); } catch {}
  window.dispatchEvent(new CustomEvent("app:unauthorized"));
}

export function updateNotifyBadge() {
  const b = document.getElementById("notif-badge");
  if (b) {
    b.classList.toggle("hidden", !(state.unread > 0));
    b.textContent = state.unread > 99 ? "99+" : state.unread;
  }
}

export function setPageHead(title, sub, actions = []) {
  return el("div", { class: "page-head" },
    el("div", {}, el("h1", { class: "page-title" }, title), sub ? el("div", { class: "sub" }, sub) : null),
    actions.length ? el("div", { class: "head-actions" }, actions) : null,
  );
}

export function statTile(label, value, cls = "t-blue") {
  return el("div", { class: `stat ${cls}` }, el("div", { class: "k" }, label), el("div", { class: "v" }, value));
}

export function loadingView(text = "Loading…") {
  return el("div", { class: "empty-state" }, el("div", { class: "spinner", style: "margin:0 auto 12px" }), el("p", {}, text));
}

export function emptyState(title, sub) {
  return el("div", { class: "empty-state" }, el("div", { class: "big" }, "🗁"), el("p", { style: "font-weight:600" }, title), sub ? el("p", { class: "muted", style: "margin-top:4px" }, sub) : null);
}

export function pagination(total, perPage, page, onPage) {
  if (!total) return null;
  const pages = Math.ceil(total / perPage);
  if (pages <= 1) return null;
  const wrap = el("div", { class: "flex between", style: "margin-top:14px" });
  const prev = el("button", { class: "btn sm", disabled: page <= 1, onclick: () => onPage(page - 1) }, "‹ Prev");
  const next = el("button", { class: "btn sm", disabled: page >= pages, onclick: () => onPage(page + 1) }, "Next ›");
  wrap.append(prev, el("span", { class: "muted" }, `Page ${page} of ${pages} · ${total} total`), next);
  return wrap;
}

export function driveLink(project, driveId) {
  return `https://drive.google.com/drive/folders/${driveId}`;
}