import { apiGet, pushPath } from "../api.js";
import { state } from "../state.js";
import { el, clear, setPageHead, loadingView, statTile, fmtBytes, fmtDate, emptyState } from "../ui.js";
import { bars, donut } from "../charts.js";

export async function render({ id, query }) {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view wide";

  let pid = id;
  let projects = [];
  try { projects = await apiGet("/api/projects"); } catch {}

  if (!pid) {
    if (!projects.length) {
      view.append(setPageHead("Reports", ""));
      view.append(emptyState("No projects", "Create a project to generate reports."));
      return;
    }
    if (projects.length === 1) pid = String(projects[0].id);
    else {
      renderPicker(view, projects);
      return;
    }
  }

  const sel = el("select", { class: "select", onchange: e => pushPath(`/projects/${e.target.value}/reports`) },
    projects.map(p => el("option", { value: p.id, selected: pid === String(p.id) ? true : undefined }, p.name)));

  const q = new URLSearchParams(query || {});
  const dateFrom = el("input", { type: "date", class: "input", value: q.get("date_from") || "", id: "r-from" });
  const dateTo = el("input", { type: "date", class: "input", value: q.get("date_to") || "", id: "r-to" });

  let p = null;
  try { p = await apiGet(`/api/projects/${pid}`); } catch (e) { return fail(view, e.message); }
  state.currentProject = p;

  const head = setPageHead(p.name + " — Reports", "",
    [el("div", { class: "row" },
      sel,
      el("button", { class: "btn sm", onclick: () => {
        const qs = new URLSearchParams();
        if (dateFrom.value) qs.set("date_from", dateFrom.value);
        if (dateTo.value) qs.set("date_to", dateTo.value);
        pushPath(`/projects/${pid}/reports?${qs}`);
        location.reload();
      } }, "Apply dates"),
    ),
    el("a", { class: "btn sm", href: `/api/projects/${pid}/reports/export?format=csv${dateFrom.value ? "&date_from=" + dateFrom.value : ""}${dateTo.value ? "&date_to=" + dateTo.value : ""}` }, "CSV"),
    el("a", { class: "btn sm", href: `/api/projects/${pid}/reports/export?format=xlsx${dateFrom.value ? "&date_from=" + dateFrom.value : ""}${dateTo.value ? "&date_to=" + dateTo.value : ""}` }, "Excel"),
    el("a", { class: "btn sm", href: `/api/projects/${pid}/reports/export?format=pdf${dateFrom.value ? "&date_from=" + dateFrom.value : ""}${dateTo.value ? "&date_to=" + dateTo.value : ""}` }, "PDF"),
    el("a", { class: "btn", href: `#/projects/${pid}/explorer` }, "Explorer"),
  ]);

  try {
    const r = await apiGet(`/api/projects/${pid}/reports${buildQ(dateFrom.value, dateTo.value)}`);
    renderData(view, head, sel, dateFrom, dateTo, p, r, pid);
  } catch (e) {
    clear(view);
    view.append(head);
    view.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
  }
}

function buildQ(from, to) {
  const q = new URLSearchParams();
  if (from) q.set("date_from", new Date(from).toISOString());
  if (to) q.set("date_to", new Date(new Date(to).getTime() + 86400000).toISOString());
  const s = q.toString();
  return s ? "?" + s : "";
}

function renderPicker(view, projects) {
  view.append(setPageHead("Reports", "Choose a project"));
  const grid = el("div", { class: "grid grid-4" });
  for (const p of projects) {
    grid.append(el("div", { class: "proj-card", onclick: () => pushPath(`/projects/${p.id}/reports`) },
      el("div", { class: "pc-top" }, el("div", { class: "pc-color", style: `background:${p.color || "#0a84ff"}` }), el("h3", {}, p.name)),
      el("div", { class: "pc-meta" }, el("span", { class: "chip ACTIVE" }, "Open reports →"))));
  }
  view.append(grid);
}

function fail(view, msg) {
  clear(view);
  view.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, msg))));
}

function renderData(view, head, sel, dateFrom, dateTo, p, r, pid) {
  clear(view);
  view.append(head);
  const o = r.overview || {};
  const stats = el("div", { class: "stats" });
  stats.append(
    statTile("Total files", o.total_files || 0, "t-blue"),
    statTile("Folders", o.total_folders || 0),
    statTile("New in period", o.new_files || 0, "t-green"),
    statTile("Modified", o.modified_files || 0, "t-amber"),
    statTile("Trashed", o.trashed_files || 0, "t-red"),
    statTile("Activities", o.total_activities || 0, "t-purple"),
    statTile("Active users", o.active_users || 0, "t-teal"),
    statTile("Total size", fmtBytes(o.total_size), "t-red"),
  );
  view.append(stats);

  const col2 = el("div", { class: "grid grid-2" },
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Activity by day")),
      el("div", { class: "card-body", id: "ch-day" })),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Activity by user")),
      el("div", { class: "card-body", id: "ch-user" })),
  );
  const col3 = el("div", { class: "grid grid-3" },
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "By action")),
      el("div", { class: "card-body", id: "ch-action" })),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "Files by extension")),
      el("div", { class: "card-body", id: "ch-ext" })),
    el("div", { class: "card" },
      el("div", { class: "card-head" }, el("h3", {}, "By folder")),
      el("div", { class: "card-body", id: "ch-folder" })),
  );
  view.append(col2, col3);

  const dayData = (r.activity && r.activity.by_day || []).map(c => ({
    label: (c.label || "").split("T")[0].slice(5) || "—", value: c.count }));
  bars(document.getElementById("ch-day"), dayData, { color: "#0a84ff" });

  const userData = top((r.activity && r.activity.by_user || []).map(c => ({ label: c.label, value: c.count })), 6);
  bars(document.getElementById("ch-user"), userData, { color: "#2fbf71" });

  const actionData = (r.activity && r.activity.by_action || []).map(c => ({ label: c.label, value: c.count }));
  elDonut("ch-action", actionData, "Actions");

  const extData = top((r.files && r.files.by_extension || []).map(c => ({ label: c.label, value: c.count })), 6);
  elDonut("ch-ext", extData, "Types");

  const folderData = top((r.files && r.files.by_folder || []).map(c => ({ label: c.label, value: c.count })), 6);
  elDonut("ch-folder", folderData, "Folders");

  const recent = el("div", { class: "card mt" },
    el("div", { class: "card-head" }, el("h3", {}, "Recent files"), el("span", { class: "muted", style: "font-size:12px" }, fmtDate(o.date_from) + " → " + fmtDate(o.date_to))),
    el("div", { class: "card-body flush", id: "recent-table" }));
  view.append(recent);
  renderRecent(document.getElementById("recent-table"), (r.files && r.files.recent_files || []), pid);
}

function top(items, n) {
  return items.slice(0, n);
}

function elDonut(id, items, center) {
  donut(document.getElementById(id), items, { centerLabel: center });
}

function renderRecent(wrap, files, pid) {
  if (!files.length) { wrap.append(el("p", { class: "muted", style: "padding:14px" }, "No files in this period.")); return; }
  const t = el("table", { class: "table" },
    el("thead", {}, el("tr", {}, el("th", {}, "Name"), el("th", {}, "Path"), el("th", {}, "Modified"), el("th", {}, "Size"))));
  const tb = el("tbody");
  for (const f of files) {
    tb.append(el("tr", {},
      el("td", {}, el("a", { class: "link", href: `#/projects/${pid}/explorer?folder=${f.drive_id || ""}`, onclick: e => { e.preventDefault(); location.hash = `/projects/${pid}/explorer${f.type === "FOLDER" ? "?folder=" + f.drive_id : ""}`; } }, f.name)),
      el("td", { class: "muted" }, f.path || ""),
      el("td", { class: "muted" }, fmtDate(f.modified_time)),
      el("td", { class: "muted" }, fmtBytes(f.size))));
  }
  t.append(tb);
  wrap.append(t);
}