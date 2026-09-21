import { apiGet, apiPost } from "../api.js";
import { state } from "../state.js";
import { el, clear, setPageHead, loadingView, toast, fmtDate } from "../ui.js";

export async function render() {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view";
  view.append(setPageHead("Settings", "Preferences and Google account connection."));

  const wrap = el("div", { class: "grid grid-2" });
  view.append(wrap);

  const theme = document.documentElement.getAttribute("data-theme") || "dark";

  const prefs = el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Appearance")),
    el("div", { class: "card-body" },
      el("div", { class: "field" }, el("label", {}, "Theme"),
        el("div", { class: "row" },
          ["dark", "light"].map(t => el("button", { class: "btn" + (theme === t ? " primary" : ""), onclick: () => setTheme(t) }, t[0].toUpperCase() + t.slice(1)))))));

  const connWrap = el("div", { class: "card" },
    el("div", { class: "card-head" }, el("h3", {}, "Google connection")),
    el("div", { class: "card-body", id: "conn-body" }, loadingView("Checking…")));

  const notifCard = el("div", { class: "card", style: "grid-column:1/-1" },
    el("div", { class: "card-head" }, el("h3", {}, "Project notifications")),
    el("div", { class: "card-body", id: "notif-body" }, loadingView("Loading…")));

  wrap.append(prefs, connWrap, notifCard);

  (async () => {
    const body = document.getElementById("notif-body");
    let data;
    try { data = await apiGet("/api/notifications/prefs"); } catch { clear(body); body.append(el("p",{class:"muted"},"Could not load preferences.")); return; }
    clear(body);
    if (!data.items.length) { body.append(el("p",{class:"muted"},"You are not a member of any project.")); return; }
    data.items.sort((a,b)=>a.project_name.localeCompare(b.project_name));
    for (const item of data.items) {
      const sw = el("label", { class: "switch" },
        el("input", { type: "checkbox", checked: item.enabled, onchange: async (e) => {
          try {
            const r = await fetch(`/api/notifications/prefs/${item.project_id}`, {
              method: "PATCH", credentials: "include",
              headers: {"Content-Type":"application/json"},
              body: JSON.stringify({ enabled: e.target.checked })
            });
            if (!r.ok) throw new Error("Failed");
            toast(`Notifications ${e.target.checked ? "enabled" : "disabled"} for ${item.project_name}`, "ok");
          } catch (err) { e.target.checked = !e.target.checked; toast(err.message,"err"); }
        }}),
        el("span", { class: "slider" })
      );
      body.append(
        el("div", { class: "kv", style: "align-items:center" },
          el("span", {}, item.project_name), sw)
      );
    }
  })();

  async function setTheme(t) {
    try {
      await patchTheme(t);
      document.documentElement.setAttribute("data-theme", t);
      render();
    } catch (e) { toast(e.message, "err"); }
  }

  const conn = await apiGet("/api/auth/connection").catch(() => null);
  const connBody = document.getElementById("conn-body");
  if (!conn) {
    clear(connBody);
    connBody.append(el("p", { class: "muted" }, "Could not load connection info."));
  } else {
    clear(connBody);
    connBody.append(
      el("div", { class: "kv" },
        el("span", { class: "k" }, "Status"), el("span", {}, el("span", { class: "chip " + (conn.connected ? "sync-ok" : "sync-err") }, conn.connected ? "Connected" : "Not connected")),
        el("span", { class: "k" }, "Google account"), el("span", {}, conn.profile_email || conn.email || "—"),
        el("span", { class: "k" }, "Token expires"), el("span", {}, conn.expires_at ? fmtDate(conn.expires_at) : "—")),
      el("div", { class: "mt" },
        el("p", { class: "muted", style: "font-size:12px;margin-bottom:6px" }, "Scopes"),
        el("code", { style: "font-size:11px" }, (conn.scopes || []).join(", "))),
    );
    if (!conn.connected) {
      connBody.append(el("div", { class: "mt" },
        el("button", { class: "btn primary", onclick: async () => {
          try { const r = await apiGet("/api/auth/login"); location.assign(r.url); }
          catch (e) { toast(e.message, "err"); }
        } }, "Connect Google")));
    }
  }
}

async function patchTheme(t) {
  const res = await fetch("/api/settings", {
    method: "PATCH", credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ theme: t }),
  });
  if (!res.ok) { const j = await res.json().catch(() => null); throw new Error((j && j.error && j.error.message) || "Failed to save"); }
}