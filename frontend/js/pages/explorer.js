import { apiGet, apiPost, pushPath } from "../api.js";
import { state } from "../state.js";
import {
  el, clear, toast, openModal, closeModal, confirmDialog, setPageHead, loadingView, emptyState,
  fmtBytes, fmtRel, fmtDate, driveLink, ICONS,
} from "../ui.js";

import { contentUrl, openFilePreview, lockChipEl } from "../viewer.js";

export async function render({ id, query }) {
  const view = document.getElementById("view");
  clear(view);
  view.className = "view wide";
  let p = null;
  try { p = await apiGet(`/api/projects/${id}`); } catch (e) { return errView(view, e.message); }
  state.currentProject = p;

  const folder = query.get("folder");
  const q = query.get("q");
  const sp = query.get("sort");
  if (sp && ["name", "size", "modified_time", "extension"].includes(sp)) listState.sort = sp;
  const op = query.get("order");
  if (op === "asc" || op === "desc") listState.order = op;
  const pp = parseInt(query.get("page"), 10);
  listState.page = pp > 0 ? pp : 1;

  view.append(setPageHead(
    p.name,
    el("span", {}, "Explorer · ",
      el("a", { class: "link", href: driveLink(p, p.google_folder_id), target: "_blank", rel: "noopener" }, "Open in Drive ↗")),
    [el("a", { class: "btn", href: `#/projects/${id}` }, "Dashboard"),
     el("a", { class: "btn", href: `#/projects/${id}/reports` }, "Reports")],
  ));

  const seg = el("div", { class: "seg" },
    el("button", { class: "btn sm" + (viewMode() === "grid" ? " active" : ""), onclick: () => setView("grid") }, "Grid"),
    el("button", { class: "btn sm" + (viewMode() === "list" ? " active" : ""), onclick: () => setView("list") }, "List"),
    el("button", { class: "btn sm" + (viewMode() === "tree" ? " active" : ""), onclick: () => setView("tree"), hidden: !!(q || folder) }, "Tree"));

  function viewMode() {
    if (q || folder) return "grid";
    return listView ? "list" : (treeView ? "tree" : "grid");
  }

  const toolbar = el("div", { class: "toolbar" },
    el("button", { class: "btn primary sm", onclick: createFolder, hidden: p.role === "VIEWER" }, ICONS.folder, "New folder"),
    el("button", { class: "btn sm", onclick: upload, hidden: p.role === "VIEWER" }, "Upload file"),
    el("button", { class: "btn sm", onclick: showTrash }, ICONS.trash, "Trash"),
    el("input", { class: "input grow sm", id: "expl-search", placeholder: "Search files & folders in this project…", value: q || "", onkeydown: e => { if (e.key === "Enter") doSearch(); } }),
    el("button", { class: "btn sm", onclick: doSearch }, "Search"),
    seg);
  view.append(toolbar);

  const body = el("div", { id: "expl-body" });
  view.append(body);

  function setView(v) {
    treeView = v === "tree";
    listView = v === "list";
    sessionStorage.setItem("explorer-tree", v === "tree" ? "1" : "0");
    sessionStorage.setItem("explorer-list", v === "list" ? "1" : "0");
    render({ id, query });
  }

  async function doSearch() {
    const s = document.getElementById("expl-search").value.trim();
    const qs = new URLSearchParams();
    if (s) qs.set("q", s);
    if (folder) qs.set("folder", folder);
    listState.page = 1;
    pushPath(`/projects/${id}/explorer?${qs}`);
    location.reload();
  }

  function createFolder() {
    const inp = el("input", { class: "input", placeholder: "Folder name", id: "cf-name" });
    const bodyM = el("div", {}, el("div", { class: "field" }, el("label", {}, "Folder name"), inp));
    const foot = el("div", {},
      el("button", { class: "btn", onclick: closeModal }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        if (!inp.value.trim()) { toast("Name required", "err"); return; }
        try {
          await apiPost(`/api/projects/${id}/folders`, { parent_id: folder || null, name: inp.value.trim() });
          closeModal();
          toast("Folder created");
          render({ id, query });
        } catch (e) { toast(e.message, "err"); }
      } }, "Create"));
    openModal({ title: "New folder", body: bodyM, footer: foot });
  }

  function upload() {
    const file = el("input", { type: "file", id: "up-file" });
    const bodyM = el("div", {},
      el("div", { class: "field" }, el("label", {}, "File to upload"), file),
      el("p", { class: "muted", style: "font-size:12px" }, "Uploaded directly to the current Google Drive folder."));
    const foot = el("div", {},
      el("button", { class: "btn", onclick: closeModal }, "Cancel"),
      el("button", { class: "btn primary", onclick: async () => {
        if (!file.files.length) { toast("Choose a file", "err"); return; }
        const fd = new FormData();
        fd.append("file", file.files[0]);
        try {
          const res = await fetch(`/api/projects/${id}/upload${folder ? "?folder_id=" + encodeURIComponent(folder) : ""}`, {
            method: "POST", credentials: "include", body: fd,
          });
          if (!res.ok) {
            const j = await res.json().catch(() => ({ error: { message: res.statusText } }));
            throw new Error((j.error && j.error.message) || res.statusText);
          }
          closeModal();
          toast("Uploaded");
          render({ id, query });
        } catch (e) { toast(e.message, "err"); }
      } }, "Upload"));
    openModal({ title: "Upload file", body: bodyM, footer: foot });
  }

  async function loadBody() {
    const params = new URLSearchParams();
    if (folder) params.set("folder_id", folder);
    if (q) params.set("q", q);
    const useTree = viewMode() === "tree" && !q && !folder;
    if (useTree) {
      params.set("per_page", "500");
    } else if (viewMode() === "list") {
      params.set("per_page", String(listState.per_page));
      params.set("page", String(listState.page));
      if (listState.sort && listState.sort !== "name") params.set("sort", listState.sort);
      if (listState.order) params.set("order", listState.order);
    }
    const qs = params.toString();
    body.replaceChildren(loadingView("Loading…"));
    try {
      const data = await apiGet(`/api/projects/${id}/explorer${qs ? "?" + qs : ""}`);
      if (useTree) renderProjectTree(body, data, { id, query, role: p.role, p });
      else if (viewMode() === "list") renderList(body, data, { id, query, role: p.role, p });
      else renderBody(body, data, { id, query, role: p.role, p });
    } catch (e) {
      clear(body);
      body.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, e.message))));
    }
  }

  loadBody();
}

function errView(view, msg) {
  clear(view);
  view.append(el("div", { class: "card" }, el("div", { class: "card-body" }, el("p", { class: "muted" }, msg))));
  return view;
}

let treeView = sessionStorage.getItem("explorer-tree") === "1";
let listView = sessionStorage.getItem("explorer-list") === "1";

const listState = { sort: "name", order: "asc", page: 1, per_page: 50 };

export function renderProjectTree(container, data, ctx) {
  clear(container);
  if (!ctx.compact) {
    container.append(buildCrumbs(data, ctx));
    container.append(el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
      `${data.total_folders} folders · ${data.total_files} files · tree view`));
  }
  ctx.rootId = ctx.rootId || "";

  const byId = new Map();
  for (const f of data.folders) byId.set(f.drive_id, Object.assign({ _subs: [] }, f));
  for (const f of data.folders) {
    const pid = f.parent_folder_id;
    if (pid && byId.has(pid)) byId.get(pid)._subs.push(byId.get(f.drive_id));
  }
  const roots = [];
  for (const f of data.folders) {
    if (!f.parent_folder_id || !byId.has(f.parent_folder_id)) roots.push(byId.get(f.drive_id));
  }

  const filesBy = new Map();
  for (const f of data.files) {
    const k = f.folder_drive_id || "";
    if (!filesBy.has(k)) filesBy.set(k, []);
    filesBy.get(k).push(f);
  }
  const state = { filesBy, byId, ctx };

  const rootFiles = [...(filesBy.get("") || []),
    ...(ctx.rootId ? (filesBy.get(ctx.rootId) || []) : [])];

  const treeEl = el("div", { class: "tree" });
  for (const r of roots) treeEl.append(folderRow(state, r, 0));
  for (const f of rootFiles) treeEl.append(fileRow(state, f, 0));

  if (!roots.length && !rootFiles.length) {
    treeEl.append(emptyState(data.search_active ? "No matches" : "No folders yet",
      data.search_active ? "Try a different search term." : "Create a folder from the button above."));
  }
  container.append(treeEl);
}

function folderRow(state, folder, depth) {
  const hasChildren = folder._subs.length || (state.filesBy.get(folder.drive_id) || []).length;
  const kids = el("div", { class: "tree-kids" }, ...(folder._subs.map(s => folderRow(state, s, depth + 1))),
    ...((state.filesBy.get(folder.drive_id) || []).map(f => fileRow(state, f, depth + 1))));

  const row = el("div", { class: "tree-group" },
    el("div", { class: "tree-row t-folder" },
      el("span", { class: "tree-tw" + (hasChildren ? "" : " leaf") }, hasChildren ? "▾" : "▸"),
      el("span", { class: "tree-name", title: folder.path }, ICONS.folder, " ", folder.name),
      el("span", { class: "tree-sub" }, folder._subs.length + (state.filesBy.get(folder.drive_id) || []).length + " items"),
      el("a", { class: "link tree-open", href: `#/projects/${state.ctx.id}/explorer?folder=${folder.drive_id}`, onclick: e => e.stopPropagation() }, "open")),
    kids);

  if (hasChildren) {
    const tw = row.querySelector(".tree-tw");
    const kidsEl = row.querySelector(".tree-kids");
    tw.onclick = () => {
      const closed = kidsEl.hidden;
      kidsEl.hidden = !closed;
      tw.textContent = closed ? "▾" : "▸";
    };
  }
  return row;
}

function fileRow(state, file, depth) {
  const row = el("div", {
    class: "tree-row t-file", style: `--td:${depth}`,
    title: file.path || file.name,
    onclick: () => { openFilePreview(state.ctx.p, file); },
  },
    el("span", { class: "tree-tw leaf" }, ""),
    el("span", { class: "tree-name" }, file.name),
    file.extension ? el("span", { class: "tree-sub" }, "." + file.extension) : null,
    el("a", { class: "link tree-open", href: contentUrl(state.ctx.p.id, file.drive_id), target: "_blank", rel: "noopener", onclick: e => { e.stopPropagation(); openFilePreview(state.ctx.p, file); } }),
    el("span", { class: "tree-size" }, fmtBytes(file.size)));
  return row;
}

function buildCrumbs(data, ctx) {
  const crumbs = el("div", { class: "crumbs" },
    el("a", { href: `#/projects/${ctx.id}/explorer` }, "Root"));
  for (let i = 0; i < data.breadcrumbs.length; i++) {
    const b = data.breadcrumbs[i];
    crumbs.append(el("span", { class: "csep" }, "›"));
    if (i < data.breadcrumbs.length - 1) {
      crumbs.append(el("a", { href: `#/projects/${ctx.id}/explorer?folder=${b.drive_id}` }, b.name));
    } else {
      crumbs.append(el("span", { style: "color:var(--text);font-weight:600" }, b.name));
    }
  }
  return crumbs;
}

function renderBody(body, d, ctx) {
  clear(body);
  body.append(buildCrumbs(d, ctx));

  const summary = el("div", { class: "muted", style: "font-size:12px;margin-bottom:12px" },
    `${d.total_folders} folders · ${d.total_files} files${d.search_active ? " · search results" : ""}`);
  body.append(summary);

  const grid = el("div", { class: "node-grid" });
  if (!d.folders.length && !d.files.length) {
    grid.append(emptyState(d.search_active ? "No matches" : "Folder is empty",
      d.search_active ? "Try a different search term." : "Upload a file from the button above."));
  }

  for (const f of d.folders) {
    grid.append(nodeCard(f, null, () => pushPath(`/projects/${ctx.id}/explorer?folder=${f.drive_id}`), ctx, d));
  }
  for (const f of d.files) {
    grid.append(nodeCard(f, f.extension || f.mime_type, () => {
      openFilePreview(ctx.p, f);
    }, ctx, d));
  }
  body.append(grid);
}

function renderList(body, d, ctx) {
  clear(body);
  body.append(buildCrumbs(d, ctx));

  const sortHeader = (label, key, cls) => {
    const active = listState.sort === key;
    const th = el("th", { class: "tb" + (cls ? " " + cls : ""), title: "Sort by " + label });
    const btn = el("button", { class: "sortbtn" + (active ? " active" : ""), onclick: () => {
      if (active) listState.order = listState.order === "asc" ? "desc" : "asc";
      else { listState.sort = key; listState.order = "asc"; }
      listState.page = 1;
      renderListHref();
    } }, label, el("span", { class: "sarr" }, active ? (listState.order === "asc" ? "▲" : "▼") : ""));
    th.append(btn);
    return th;
  };

  const renderListHref = () => {
    const qs = new URLSearchParams();
    const cur = new URLSearchParams(window.location.hash.split("?")[1] || "");
    if (cur.has("folder")) qs.set("folder", cur.get("folder"));
    if (listState.sort && listState.sort !== "name") qs.set("sort", listState.sort);
    if (listState.order) qs.set("order", listState.order);
    if (listState.page > 1) qs.set("page", String(listState.page));
    location.hash = `#/projects/${ctx.id}/explorer?${qs.toString()}`;
    location.reload();
  };

  const table = el("table", { class: "expl-table" });
  const thead = el("thead");
  const trh = el("tr", {},
    sortHeader("Name", "name"),
    sortHeader("Type", "extension", "center"),
    sortHeader("Size", "size", "right"),
    sortHeader("Modified", "modified_time", "right"));
  thead.append(trh);
  table.append(thead);

  const tbody = el("tbody");
  for (const f of d.folders) {
    tbody.append(el("tr", { class: "row-folder", onclick: () => {
      location.hash = `#/projects/${ctx.id}/explorer?folder=${encodeURIComponent(f.drive_id)}`;
      location.reload();
    } },
      el("td", {}, el("span", { class: "tname", title: f.path || f.name }, ICONS.folder, " ", f.name)),
      el("td", { class: "center muted" }, f.child_count ? `${f.child_files} files · ${f.child_folders} sub` : "folder"),
      el("td", { class: "right muted" }, "—"),
      el("td", { class: "right muted" }, fmtRel(f.modified_time))));
  }
  for (const f of d.files) {
    tbody.append(el("tr", { class: "row-file", onclick: () => openFilePreview(ctx.p, f) },
      el("td", {}, el("span", { class: "tname", title: f.path || f.name }, f.name),
        f.lock ? lockChipEl(f.lock) : null),
      el("td", { class: "center muted" }, f.extension ? "." + f.extension : "file"),
      el("td", { class: "right muted" }, fmtBytes(f.size)),
      el("td", { class: "right muted" }, fmtRel(f.modified_time))));
  }
  table.append(tbody);
  body.append(table);

  if (!d.folders.length && !d.files.length) {
    body.append(emptyState(d.search_active ? "No matches" : "Folder is empty",
      d.search_active ? "Try a different search term." : "Upload a file from the button above."));
  }

  const per = listState.per_page;
  const sorted = listState.sort && listState.sort !== "name";
  const total = d.total_folders + d.total_files;
  const shown = sorted || listState.page > 1 ? Math.min(per * listState.page, total) : d.folders.length + d.files.length;
  const footer = el("div", { class: "listfoot" },
    el("span", { class: "muted", style: "font-size:12px" }, `${shown} of ${total} items`),
    el("div", { class: "flex", style: "gap:6px" },
      el("button", { class: "btn sm", disabled: listState.page <= 1, onclick: () => { listState.page--; renderListHref(); } }, "‹ Prev"),
      el("button", { class: "btn sm", disabled: shown >= total, onclick: () => { listState.page++; renderListHref(); } }, "Next ›")));
  body.append(footer);
}

function nodeCard(n, extLabel, onclick, ctx, d) {
  const menu = ctx.role === "VIEWER" ? null : el("div", { class: "node-menu" },
    el("button", { class: "btn sm ghost", onclick: (e) => { e.stopPropagation(); rename(n, ctx, d); } }, "Rename"),
    el("button", { class: "btn sm ghost", onclick: (e) => { e.stopPropagation(); move(n, ctx, d); } }, "Move"),
    el("button", { class: "btn sm ghost danger", onclick: (e) => { e.stopPropagation(); trash(n, ctx); } }, "Trash"));

  let thumb;
  if (n.type === "FOLDER") {
    thumb = el("div", { class: "thumb" }, ICONS.folder);
  } else {
    thumb = el("div", { class: "thumb" }, el("span", { class: "ext" }, (n.extension || "?").toUpperCase().slice(0, 5)));
  }
  return el("div", { class: "node", onclick },
    thumb,
    el("div", { class: "nname", title: n.name }, n.name),
    el("div", { class: "nmeta" },
      el("span", {}, n.type === "FOLDER" ? (n.child_count ? n.child_count + " items" : "folder") : fmtBytes(n.size)),
      el("span", {}, fmtRel(n.modified_time))),
    n.type === "FILE" && n.lock ? el("div", { class: "nmeta", style: "margin-top:4px" }, lockChipEl(n.lock)) : null,
    menu);
}

function rename(n, ctx, d) {
  const inp = el("input", { class: "input", value: n.name, id: "rn-name" });
  const body = el("div", {}, el("div", { class: "field" }, el("label", {}, "New name"), inp));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      if (!inp.value.trim()) { toast("Name required", "err"); return; }
      try {
        await apiPost(`/api/projects/${ctx.id}/rename`, { drive_id: n.drive_id, name: inp.value.trim() });
        closeModal();
        toast("Renamed");
        render({ id: ctx.id, query: ctx.query });
      } catch (e) { toast(e.message, "err"); }
    } }, "Save"));
  openModal({ title: "Rename", body, footer: foot });
}

function move(n, ctx, d) {
  const options = [el("option", { value: "" }, "Project root")];
  for (const f of d.folders) options.push(el("option", { value: f.drive_id }, f.name));
  const sel = el("select", { class: "select" }, options);
  const body = el("div", {}, el("div", { class: "field" }, el("label", {}, "Move to folder"), sel));
  const foot = el("div", {},
    el("button", { class: "btn", onclick: closeModal }, "Cancel"),
    el("button", { class: "btn primary", onclick: async () => {
      try {
        await apiPost(`/api/projects/${ctx.id}/move`, { drive_id: n.drive_id, parent_id: sel.value || null });
        closeModal();
        toast("Moved");
        render({ id: ctx.id, query: ctx.query });
      } catch (e) { toast(e.message, "err"); }
    } }, "Move"));
  openModal({ title: `Move "${n.name}"`, body, footer: foot });
}

function trash(n, ctx) {
  confirmDialog("Trash in Drive", `Move "${n.name}" to Google Drive trash? This cannot be undone from DriveDoc.`, async () => {
    try {
      await apiPost(`/api/projects/${ctx.id}/trash`, { drive_id: n.drive_id });
      toast("Trashed");
      render({ id: ctx.id, query: ctx.query });
    } catch (e) { toast(e.message, "err"); }
  });
}

async function showTrash() {
  const list = el("div", { class: "trash-list" });
  const modal = openModal({ title: "Trash", body: list });
  const spinner = el("div", { class: "muted" }, "Loading…");
  list.append(spinner);
  try {
    const d = await apiGet(`/api/projects/${id}/trash?per_page=200`);
    clear(list);
    const folders = d.folders || [];
    const files = d.files || [];
    if (!folders.length && !files.length) {
      list.append(el("div", { class: "muted" }, "Trash is empty."));
      return;
    }
    for (const f of folders) {
      list.append(trashRow(f, true));
    }
    for (const f of files) {
      list.append(trashRow(f, false));
    }
  } catch (e) {
    clear(list);
    list.append(el("div", { class: "muted" }, "Could not load trash: " + e.message));
  }

  function trashRow(f, isFolder) {
    const row = el("div", { class: "trash-row" },
      el("span", { class: "tname", title: f.path || f.drive_id }, isFolder ? ICONS.folder : ICONS.file, " ", f.name),
      el("span", { class: "muted", style: "font-size:11px" }, isFolder ? "Folder · " : "File" + (f.size ? " · " + fmtBytes(f.size) : "") + " · ", fmtRel(f.trashed_at)),
      el("button", { class: "btn sm primary", onclick: async () => {
        try {
          await apiPost(`/api/projects/${id}/files/${f.drive_id}/restore`);
          toast("Restored from trash");
          closeModal();
          render({ id, query });
        } catch (e) { toast(e.message, "err"); }
      } }, "Restore"));
    return row;
  }
}