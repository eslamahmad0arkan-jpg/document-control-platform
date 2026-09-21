import { el, clear, fmtBytes, fmtRel, ICONS, toast } from "./ui.js";
import { apiGet, apiPost } from "./api.js";
import { state } from "./state.js";

export const contentUrl = (projectId, driveId) => `/api/projects/${projectId}/files/${driveId}/content`;
export const canManageProject = (p) => p.role === "PROJECT_MANAGER" || p.role === "EDITOR";

const PREVIEWABLE = /^(application\/pdf|image\/|text\/|video\/|audio\/|application\/json|application\/xml)/;

function canPreview(mime) {
  return !!mime && PREVIEWABLE.test(mime);
}

let activeOverlay = null;

export function closeOverlay() {
  if (activeOverlay) {
    activeOverlay.remove();
    activeOverlay = null;
    document.removeEventListener("keydown", onEsc);
  }
}

function onEsc(e) {
  if (e.key === "Escape") closeOverlay();
}

export function overlay(head, body) {
  closeOverlay();
  const panel = el("div", { class: "panel" },
    head,
    el("div", { class: "panel-body" }, body));
  const ov = el("div", { class: "overlay", onclick: (e) => { if (e.target === ov) closeOverlay(); } }, panel);
  document.getElementById("modal-root").append(ov);
  activeOverlay = ov;
  document.addEventListener("keydown", onEsc);
  return ov;
}

export function openFilePreview(p, file) {
  const url = contentUrl(p.id, file.drive_id);
  const previewable = canPreview(file.mime_type);
  const head = el("div", { class: "panel-head" },
    el("div", { class: "panel-title" },
      el("div", { class: "flex", style: "gap:8px;align-items:center" },
        el("strong", {}, file.name),
        lockChipEl(file.lock)),
      file.path ? el("div", { class: "muted", style: "font-size:11px" }, file.path) : null),
    el("div", { class: "flex", style: "gap:8px;margin-left:auto" },
      file.drive_url
        ? el("a", { class: "btn", href: file.drive_url, target: "_blank", rel: "noopener" }, "Open in Drive ↗")
        : null,
      el("a", { class: "btn", href: `${url}?download=true`, target: "_blank", rel: "noopener" }, "Download"),
      el("button", { class: "btn ghost", onclick: closeOverlay }, "×")));

  let body;
  if (previewable) {
    body = el("iframe", { class: "preview-frame", src: url, title: file.name });
  } else {
    body = el("div", { class: "preview-note" },
      el("div", { style: "font-size:38px;opacity:.4" }, "🗎"),
      el("p", { style: "font-weight:600" }, "This file type can't be previewed in the browser"),
      el("p", { class: "muted", style: "font-size:12px" },
        `${file.name} · ${fmtBytes(file.size)}${file.drive_url ? " · Open it in Google Drive or download it below." : ""}`),
      el("div", { class: "flex", style: "gap:8px;justify-content:center;margin-top:10px" },
        file.drive_url
          ? el("a", { class: "btn primary", href: file.drive_url, target: "_blank", rel: "noopener" }, "Open in Drive ↗")
          : null,
        el("a", { class: "btn primary", href: `${url}?download=true`, target: "_blank", rel: "noopener" }, "Download")));
  }

  const docPanel = docControlPanel(p, file, (lock) => updateChip(lock));
  const split = el("div", { class: "preview-split" },
    body,
    docPanel);
  overlay(head, split);
}

export function lockChipEl(lock) {
  if (!lock) return null;
  return el("span", { class: "lock-chip", title: lock.locked_by_email },
    ICONS.lock, " Locked by ", el("span", { class: "locked-name" }, lock.locked_by_name && lock.locked_by_name !== "Unknown" ? lock.locked_by_name : lock.locked_by_email));
}

// --- document-control side panel: lock + threaded comments + approvals ------

function docControlPanel(p, file, onLockChange) {
  const pid = p.id;
  const did = file.drive_id;
  const isViewer = p.role === "VIEWER";
  const canManage = canManageProject(p);
  const canEdit = !isViewer;   // lock + request approval (needs EDITOR/MANAGER)
  const canComment = true;     // any project member may comment/attach

  const lockBox = el("div", { class: "docr-lock" },
    el("div", { class: "docr-lock-status" }, "Checking lock…"));

  const list = el("div", { class: "docr-comments" });
  const panel = el("div", { class: "docr-panel" },
    el("h4", { class: "docr-title" }, "Document control"),
    lockBox,
    commentsSection(),
    approvalsSection());

  async function refreshLock() {
    try {
      const lock = await apiGet(`/api/projects/${pid}/files/${did}/lock`);
      clear(lockBox);
      if (!lock) {
        lockBox.append(
          el("div", { class: "docr-lock-status" }, el("span", { class: "lock-avail" }, ICONS.unlock, " Available")),
          canEdit ? el("button", { class: "btn sm", onclick: lockNow }, ICONS.lock, " Lock for editing") : null,
        );
      } else {
        const mine = state && state.me && lock.locked_by_user_id === state.me.id;
        lockBox.append(
          el("div", { class: "docr-lock-status" }, el("span", { class: "lock-held" }, ICONS.lock, " Locked by ", el("strong", {}, lock.locked_by_name)),
            lock.comment ? el("div", { class: "muted", style: "font-size:11px;margin-top:2px" }, `"${lock.comment}"`) : null,
            el("div", { class: "muted", style: "font-size:11px" }, fmtRel(lock.created_at))),
          (mine || canManage) && canEdit
              ? el("button", { class: "btn sm", onclick: unlockNow }, ICONS.unlock, " Release lock")
              : null,
        );
      }
      onLockChange(lock);
    } catch (e) {
      clear(lockBox);
      lockBox.append(el("div", { class: "docr-lock-status muted" }, "Could not load lock state."));
    }
  }

  async function lockNow() {
    try {
      const r = await fetch(`/api/projects/${pid}/files/${did}/lock`, {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment: "" }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j && j.error && j.error.message || "Lock failed");
      }
      toast("File locked for editing", "ok");
      refreshLock();
    } catch (e) { toast(e.message, "err"); }
  }

  async function unlockNow() {
    try {
      const r = await fetch(`/api/projects/${pid}/files/${did}/unlock`, {
        method: "POST", credentials: "include",
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j && j.error && j.error.message || "Unlock failed");
      }
      toast("Lock released", "ok");
      refreshLock();
    } catch (e) { toast(e.message, "err"); }
  }

  // --- comments: threaded + mentions + attachments --------------------------

  function commentsSection() {
    const input = el("textarea", { class: "docr-input", rows: 2, placeholder: "Write a comment… use @ to mention someone" });
    const attChips = el("div", { class: "att-chips" });
    const filePicker = el("input", { type: "file", multiple: true, hidden: true });
    const attachBtn = el("button", { class: "btn sm ghost", type: "button", title: "Attach files", onclick: () => filePicker.click() }, ICONS.paperclip, " Attach");
    const sentBtn = el("button", { class: "btn primary sm", type: "button" }, "Post");
    const mentionBox = el("div", { class: "mention-box", hidden: true });
    let pendingFiles = [];
    let members = [];

    async function loadMembers() {
      try { members = await apiGet(`/api/projects/${pid}/members`) || []; } catch (e) { members = []; }
    }

    function currentMentionToken() {
      const v = input.value.slice(0, input.selectionStart || 0);
      const m = v.match(/@(\w[\w .@-]*)$/);
      return m ? m[0].slice(1).toLowerCase() : null;
    }

    function maybeShowMentions() {
      const token = currentMentionToken();
      if (!token) { mentionBox.hidden = true; return; }
      const matches = members.filter(m =>
        m.user_id !== (state.me && state.me.id) &&
        (m.email && m.email.toLowerCase().includes(token) ||
         m.name && m.name.toLowerCase().includes(token)));
      clear(mentionBox);
      if (!matches.length || !canComment) { mentionBox.hidden = true; return; }
      for (const m of matches.slice(0, 6)) {
        mentionBox.append(el("button", { class: "mention-item", type: "button",
          onclick: () => pickMention(m) },
          el("span", { class: "mention-name" }, "@" + (m.name || m.email.split("@")[0])),
          el("span", { class: "muted mention-email" }, m.email)));
      }
      mentionBox.hidden = false;
    }

    function pickMention(m) {
      const v = input.value;
      const pos = input.selectionStart || v.length;
      const before = v.slice(0, pos);
      const start = before.lastIndexOf("@");
      const after = v.slice(pos);
      input.value = before.slice(0, Math.max(0, start)) + "@" + (m.name || m.email.split("@")[0]) + " " + after;
      input.focus();
      mentionBox.hidden = true;
    }

    input.addEventListener("input", maybeShowMentions);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); postComment(); }
      if (e.key === "Escape") mentionBox.hidden = true;
    });

    filePicker.addEventListener("change", () => {
      for (const f of [...filePicker.files]) {
        if (pendingFiles.length >= 6) { toast("Up to 6 attachments per comment", "err"); break; }
        pendingFiles.push(f);
        attChips.append(chipFor(f));
      }
      filePicker.value = "";
    });

    function chipFor(f) {
      const chip = el("span", { class: "att-chip" }, `${ICONS.paperclip} ${f.name} · ${fmtBytes(f.size)}`);
      chip.appendChild(el("button", { class: "att-rm", type: "button", title: "Remove",
        onclick: () => { pendingFiles = pendingFiles.filter(x => x !== f); chip.remove(); } }, "×"));
      return chip;
    }

    function commentRow(c, renderBody) {
      const atts = (c.attachments || []).map(a =>
        el("a", { class: "att-link", href: a.download_url, target: "_blank", rel: "noopener" },
          `${ICONS.paperclip} ${a.filename} · ${fmtBytes(a.size)}`));
      const box = el("div", { class: "docr-cmt" },
        el("div", { class: "docr-cmt-head" },
          el("strong", {}, c.user_name || c.user_email),
          el("span", { class: "muted" }, fmtRel(c.created_at))),
        el("div", { class: "docr-cmt-body" }, renderBody(c.body)),
        atts.length ? el("div", { class: "docr-cmt-att"}, atts) : null);
      return box;
    }

    function highlightMentions(txt) {
      const parts = String(txt).split(/(@[A-Za-z0-9_][A-Za-z0-9_.@-]*)/g);
      return el("span", {}, ...parts.map(p =>
        p.startsWith("@") ? el("span", { class: "mention" }, p) : p));
    }

    function replyBox(c, onPosted) {
      const wrap = el("div", { class: "docr-reply" });
      const ri = el("textarea", { class: "docr-input", rows: 1, placeholder: "Reply…" });
      const rb = el("button", { class: "btn sm", type: "button" }, "Reply");
      rb.onclick = async () => {
        const body = ri.value.trim();
        if (!body) return;
        try {
          await apiPost(`/api/projects/${pid}/files/${did}/comments`, { body, parent_id: c.id });
          ri.value = ""; wrap.remove(); onPosted();
        } catch (e) { toast(e.message, "err"); }
      };
      wrap.append(ri, rb);
      return wrap;
    }

    async function renderReplies(c, holder, toggleBtn) {
      try {
        const reps = await apiGet(`/api/projects/${pid}/comments/${c.id}/replies`);
        clear(holder);
        if (!reps.length) holder.append(el("div", { class: "muted", style: "font-size:11px;padding:4px 0" }, "No replies yet."));
        for (const r of reps) {
          const row = commentRow(r, (txt) => highlightMentions(txt));
          if (canEdit) row.append(replyBox(r, () => renderReplies(c, holder)));
          holder.append(row);
        }
        if (canEdit) holder.append(replyBox(c, () => renderReplies(c, holder)));
      } catch (e) {
        holder.append(el("div", { class: "muted" }, "Could not load replies."));
      }
    }

    function topRow(c) {
      const row = commentRow(c, (txt) => highlightMentions(txt));
      const holder = el("div", { class: "docr-replies" });
      const toggle = el("button", { class: "link-btn sm", type: "button" },
        c.reply_count ? `Show replies (${c.reply_count})` : "Reply");
      toggle.onclick = async () => {
        holder.hidden = !holder.hidden;
        toggle.textContent = holder.hidden && c.reply_count ? `Show replies (${c.reply_count})` : "Hide replies";
        if (!holder.hidden && !holder.dataset.loaded) { holder.dataset.loaded = "1"; await renderReplies(c, holder); }
      };
      row.append(toggle, holder);
      return row;
    }

    async function loadComments() {
      try {
        const comments = await apiGet(`/api/projects/${pid}/files/${did}/comments`);
        clear(list);
        if (!comments.length) {
          list.append(el("div", { class: "muted", style: "font-size:12px;padding:8px 0" }, "No comments yet."));
          return;
        }
        for (const c of comments) list.append(topRow(c));
      } catch (e) {
        clear(list);
        list.append(el("div", { class: "muted" }, "Could not load comments."));
      }
    }

    async function postComment() {
      const body = input.value.trim();
      if (!body) return;
      const mention_ids = members
        .filter(m => {
          if (state && state.me && m.user_id === state.me.id) return false;
          const keys = [m.name, m.email].filter(Boolean);
          return keys.some(k => body.includes("@" + k));
        })
        .map(m => m.user_id);
      sentBtn.disabled = true;
      try {
        const c = await apiPost(`/api/projects/${pid}/files/${did}/comments`,
          { body, mention_ids });
        // upload attachments
        for (const f of pendingFiles) {
          const fd = new FormData();
          fd.append("file", f, f.name);
          const r = await fetch(`/api/projects/${pid}/comments/${c.id}/attachments`,
            { method: "POST", credentials: "include", body: fd });
          if (!r.ok) toast(`Failed to upload ${f.name}`, "err");
        }
        pendingFiles = [];
        clear(attChips);
        input.value = "";
        toast("Comment posted", "ok");
        loadComments();
      } catch (e) { toast(e.message, "err"); }
      sentBtn.disabled = false;
    }

    sentBtn.onclick = postComment;

    loadMembers();
    loadComments();

    return el("div", {},
      el("h4", { class: "docr-title", style: "margin-top:14px" }, ICONS.comment, " Comments"),
      list,
      canComment ? el("div", { class: "docr-compose" },
        mentionBox, input,
        el("div", { class: "docr-compose-bar" }, attachBtn, attChips, el("div", { style: "flex:1" }), sentBtn))
        : null);
  }

  // --- approvals ------------------------------------------------------------

  function approvalsSection() {
    const box = el("div", { class: "approv-box" });

    async function load() {
      try {
        const rows = await apiGet(`/api/projects/${pid}/files/${did}/approvals`);
        clear(box);
        if (!rows.length) box.append(el("div", { class: "muted", style: "font-size:12px" }, "No approvals requested."));
        for (const a of rows) box.append(approvalRow(a));
        if (canEdit) box.append(requestForm());
      } catch (e) {
        box.append(el("div", { class: "muted" }, "Could not load approvals."));
      }
    }

    function statusChip(status) {
      const cls = status === "APPROVED" ? "st-ok" : status === "REJECTED" ? "st-bad" : "st-wait";
      return el("span", { class: `status-chip ${cls}` }, status === "PENDING" ? "Pending" : status);
    }

    function approvalRow(a) {
      const mine = state && state.me && a.reviewer_user_id === state.me.id && a.status === "PENDING";
      const row = el("div", { class: "approv-row" },
        el("div", { class: "docr-cmt-head" },
          statusChip(a.status),
          el("span", { class: "muted" }, fmtRel(a.created_at))),
        el("div", { style: "font-size:12px;margin-top:4px" },
          el("strong", {}, a.requested_by_name || "Someone"), " requested approval from ",
          el("strong", {}, a.reviewer_name || "a reviewer")),
        a.comment ? el("div", { class: "muted docr-cmt-body" }, `"${a.comment}"`) : null,
        a.decided_comment ? el("div", { class: "docr-cmt-body" }, a.decided_comment) : null);
      if (mine) {
        const dcomment = el("input", { class: "docr-input", placeholder: "Decision comment (optional)", style: "min-height:32px" });
        const yes = el("button", { class: "btn sm", type: "button" }, "✓ Approve");
        const no = el("button", { class: "btn sm danger ghost", type: "button" }, "✕ Reject");
        const decide = async (status) => {
          try {
            const r = await fetch(`/api/projects/${pid}/approvals/${a.id}/decide`, {
              method: "POST", credentials: "include",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status, comment: dcomment.value.trim() || null }),
            });
            if (!r.ok) { const j = await r.json().catch(() => null); throw new Error(j && j.error && j.error.message || "Decision failed"); }
            toast(status === "APPROVED" ? "Approval granted" : "Request rejected", "ok");
            load();
          } catch (e) { toast(e.message, "err"); }
        };
        yes.onclick = () => decide("APPROVED");
        no.onclick = () => decide("REJECTED");
        row.append(el("div", { class: "flex docr-decide", style: "gap:6px;margin-top:6px" }, dcomment, yes, no));
      }
      return row;
    }

    function requestForm() {
      const wrap = el("div", { class: "docr-compose", style: "margin-top:8px" });
      const req = el("div", { class: "muted", style: "font-size:11px" }, "Request approval from a project member:");
      const comment = el("input", { class: "docr-input", placeholder: "Note for reviewer (optional)", style: "min-height:32px" });
      const sel = el("select", { class: "docr-select" });
      const go = el("button", { class: "btn sm primary" }, "Request");
      (async () => {
        try {
          const m = await apiGet(`/api/projects/${pid}/members`);
          for (const mm of m) {
            if (mm.user_id !== (state.me && state.me.id)) {
              sel.append(el("option", { value: mm.user_id }, (mm.name || mm.email) + (mm.email && mm.name ? ` — ${mm.email}` : "")));
            }
          }
          if (!sel.options.length) go.disabled = true;
        } catch (e) { go.disabled = true; }
      })();
      go.onclick = async () => {
        if (!sel.value) return;
        try {
          await apiPost(`/api/projects/${pid}/files/${did}/approvals`,
            { reviewer_user_id: Number(sel.value), comment: comment.value.trim() || null });
          toast("Approval requested", "ok");
          load();
        } catch (e) { toast(e.message, "err"); }
      };
      wrap.append(req, sel, comment, go);
      return wrap;
    }

    load();
    return el("div", {},
      el("h4", { class: "docr-title", style: "margin-top:14px" }, ICONS.chart, " Approvals"),
      box);
  }

  refreshLock();
  return panel;
}

function updateChip(lock) {
  // The panel title chip is updated by the overlay caller after lock changes.
  const chip = document.querySelector(".panel-title .lock-chip");
  if (chip) chip.remove();
  const title = document.querySelector(".panel-title .flex");
  if (title) {
    const fresh = lockChipEl(lock);
    if (fresh) title.appendChild(fresh);
  }
}

export function fileRow(f, openFn) {
  return el("div", { class: "file-item", title: f.path || f.name, onclick: () => openFn(f) },
    el("span", { class: "ext" }, (f.extension || "?").toUpperCase().slice(0, 5)),
    el("span", { class: "fi-name" }, f.name),
    f.lock ? lockChipEl(f.lock) : null,
    f.extension ? el("span", { class: "fi-meta" }, "." + f.extension) : null,
    el("span", { class: "fi-meta" }, fmtBytes(f.size)),
    el("span", { class: "fi-meta" }, fmtRel(f.modified_time)));
}

export function folderCard(folder, onOpen) {
  const files = folder.child_files || 0;
  const subs = folder.child_folders || 0;
  const items = folder.child_count || files + subs;
  return el("div", { class: "folder-card", onclick: () => onOpen(folder) },
    el("div", { class: "fc-ico" }, ICONS.folder),
    el("div", { class: "fc-name", title: folder.name }, folder.name),
    el("div", { class: "fc-count" },
      `${items} item${items === 1 ? "" : "s"}` +
      (files ? ` · ${files} file${files === 1 ? "" : "s"}` : "") +
      (subs ? ` · ${subs} subfolder${subs === 1 ? "" : "s"}` : "")));
}

// --- lazy, breadcrumbed drill-down ---------------------------------------

function buildCrumbs(p, crumbs) {
  const bc = el("div", { class: "ovc-crumbs" },
    el("a", { href: "#", onclick: (e) => { e.preventDefault(); openProjectRootView(p); } },
      ICONS.home, " ", "Home"));
  for (let i = 0; i < (crumbs || []).length; i++) {
    const c = crumbs[i];
    bc.append(el("span", { class: "csep" }, "›"));
    if (i < crumbs.length - 1) {
      bc.append(el("a", { href: "#", onclick: (e) => { e.preventDefault(); openFolderView(p, { drive_id: c.drive_id, name: c.name }); } }, c.name));
    } else {
      bc.append(el("span", { class: "cur" }, c.name));
    }
  }
  return bc;
}

export function openProjectRootView(p) {
  const head = el("div", { class: "panel-head" },
    el("div", { class: "panel-title" },
      el("strong", {}, ICONS.folder, " ", p.name),
      el("div", { class: "muted", style: "font-size:11px" }, "Project root")),
    el("div", { class: "flex", style: "gap:8px;margin-left:auto" },
      el("a", { class: "btn", href: `#/projects/${p.id}/explorer` }, "Open explorer"),
      el("button", { class: "btn ghost", onclick: closeOverlay }, "×")));

  const box = contentBox(p, null);
  overlay(head, box);
}

export function openFolderView(p, folder) {
  const trail = el("div", { class: "ovc-trail" });
  const head = el("div", { class: "panel-head" },
    el("div", { class: "panel-title" },
      el("strong", {}, ICONS.folder, " ", folder.name),
      trail,
      el("div", { class: "muted", style: "font-size:11px" }, folder.path || "")),
    el("div", { class: "flex", style: "gap:8px;margin-left:auto" },
      el("button", { class: "btn", onclick: () => openProjectRootView(p) }, ICONS.home, " Home"),
      el("button", { class: "btn ghost", onclick: closeOverlay }, "×")));
  const box = contentBox(p, folder.drive_id, head, trail);
  overlay(head, box);
}

// A content box that lazily pages through a folder's children.
function contentBox(p, driveId, head, trail) {
  const note = el("div", { class: "muted", style: "font-size:11px;margin:2px 0" }, "Loading…");
  const sHead = el("div", { class: "sub-head", hidden: true }, "Subfolders");
  const grid = el("div", { class: "folder-grid" });
  const fHead = el("div", { class: "sub-head", hidden: true }, "Files");
  const list = el("div", { class: "file-list" });
  const more = el("button", { class: "btn sm", hidden: true }, "Load more");

  const wrapper = el("div", { class: "folder-view" }, note, sHead, grid, fHead, list, more);

  let page = 1, busy = false, finished = false, seen = 0, total = Infinity, first = true;
  let crumbs = [];

  function renderCounts(d) {
    if (!trail) return;
    clear(trail);
    trail.append(buildCrumbs(p, crumbs));
    trail.append(el("span", { class: "csep" }, "·"));
    trail.append(el("span", {}, `${d.total_folders} folders · ${d.total_files} files`));
  }

  async function loadPage() {
    if (busy || finished) return;
    busy = true;
    more.disabled = true;
    more.textContent = "Loading…";
    try {
      const qs = new URLSearchParams({ per_page: "60", page: String(page) });
      if (driveId) qs.set("folder_id", driveId);
      const d = await apiGet(`/api/projects/${p.id}/explorer?${qs}`);
      if (first) {
        first = false;
        crumbs = d.breadcrumbs || [];
        renderCounts(d);
      }
      const folders = d.folders || [];
      const files = d.files || [];
      total = (d.total_folders || 0) + (d.total_files || 0);
      seen += folders.length + files.length;

      if (folders.length) {
        sHead.hidden = false;
        for (const fo of folders) {
          grid.append(folderCard(fo, (sub) => openFolderView(p, sub)));
        }
      }
      if (files.length) {
        fHead.hidden = false;
        for (const f of files) list.append(fileRow(f, (file) => openFilePreview(p, file)));
      }
      if (total === 0 || (page === 1 && !folders.length && !files.length)) {
        note.textContent = "This folder is empty.";
        finished = true;
      } else {
        note.textContent = `${Math.min(seen, total)} of ${total} items`;
        finished = seen >= total;
      }
      page++;
    } catch (e) {
      note.textContent = e.message || "Failed to load";
      finished = true;
    }
    busy = false;
    more.disabled = false;
    more.hidden = finished;
    more.textContent = "Load more";
  }

  more.onclick = loadPage;
  loadPage();
  return wrapper;
}