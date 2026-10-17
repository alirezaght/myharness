"use strict";

// P2-05: comments on diagram elements, built with createElement and textContent only.
// The selected element of the current diagram version gets a form; a typed comment lives in
// commentDrafts, so live re-renders rebuild the identical form. A submit keeps its request id
// across retries, so a retried request never adds a second comment.
// Loaded after diagrams.js and before app.js; their functions are only called after load.

const COMMENTS_API = "/api/comments";
const COMMENT_ATTEMPTS = 3;
const COMMENT_BACKOFF_MS = 500;
const COMMENT_KINDS = ["clarification", "correction", "redesign"];
const COMMENT_FOCUS_IDS = new Set(["comment-kind", "comment-text", "comment-submit"]);
const commentDrafts = new Map();
const commentState = { taskId: null, items: [], kinds: COMMENT_KINDS, current: null };

function draftKey(task, version, element) {
  return `${task} ${version} ${element}`;
}

function newDraft(task, version, element) {
  return { task, version, element, kind: COMMENT_KINDS[0], text: "", requestId: null,
    sending: false, error: null, reload: false, retry: false };
}

function ensureDraft(key, task, version, element) {
  if (!commentDrafts.has(key)) commentDrafts.set(key, newDraft(task, version, element));
  return commentDrafts.get(key);
}

function newRequestId() {
  if (window.crypto && typeof window.crypto.randomUUID === "function") {
    return window.crypto.randomUUID();
  }
  const bytes = window.crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function elementIds(view) {
  const ids = new Set();
  if (view) for (const scene of view.diagrams) for (const item of scene.items) ids.add(item.id);
  return ids;
}

// A new current version takes over the drafts of the previous one whose element it still has.
// A draft being sent moves too and keeps its request id until its response arrives.
function carryDrafts(taskId, from, to, ids) {
  const prefix = `${taskId} ${from} `;
  for (const [key, draft] of [...commentDrafts]) {
    if (!key.startsWith(prefix) || !ids.has(draft.element)) continue;
    commentDrafts.delete(key);
    const moved = draftKey(taskId, to, draft.element);
    if (commentDrafts.has(moved)) continue;
    draft.version = to;
    if (!draft.sending) Object.assign(draft, { requestId: null, retry: false });
    commentDrafts.set(moved, draft);
  }
}

function keyOf(draft) {
  for (const [key, value] of commentDrafts) if (value === draft) return key;
  return null;
}

function rememberComments(task) {
  const comments = task.comments || { items: [], kinds: COMMENT_KINDS };
  const cur = task.diagrams && task.diagrams.current;
  const now = cur ? cur.version : null;
  if (commentState.taskId === task.id && commentState.current !== null && now !== null
      && now !== commentState.current) {
    carryDrafts(task.id, commentState.current, now, elementIds(cur));
  }
  commentState.taskId = task.id;
  commentState.items = comments.items;
  commentState.kinds = comments.kinds && comments.kinds.length ? comments.kinds : COMMENT_KINDS;
  commentState.current = now;
}

function commentLine(comment) {
  const item = el("li");
  item.dataset.status = comment.status;
  item.append(el("p", `#${comment.id} · ${comment.status} · ${comment.kind}`));
  const text = el("p", comment.text);
  text.className = "comment-text";
  item.append(text);
  return item;
}

function commentButton(id, text, action) {
  const button = el("button", text);
  button.type = "button";
  button.id = id;
  button.addEventListener("click", action);
  return button;
}

function commentStatus(key, draft) {
  const box = el("div");
  box.className = "comment-status";
  if (draft.sending) {
    const note = el("p", "sending…");
    note.setAttribute("role", "status");
    box.append(note);
    return box;
  }
  if (draft.error) box.append(alertText(draft.error));
  if (draft.reload) box.append(commentButton("comment-reload", "Reload", () => reloadDraft(key)));
  if (draft.retry) box.append(commentButton("comment-retry", "Retry", () => startSubmit(key)));
  return box;
}

function commentForm(key, task, version, element) {
  const draft = commentDrafts.get(key) || newDraft(task, version, element);
  const form = el("form");
  form.id = "comment-form";
  const kindLabel = el("label", "Kind");
  kindLabel.htmlFor = "comment-kind";
  const kind = el("select");
  kind.id = "comment-kind";
  for (const name of commentState.kinds) {
    const option = el("option", name);
    option.value = name;
    kind.append(option);
  }
  kind.value = draft.kind;
  kind.addEventListener("change", () => {
    ensureDraft(key, task, version, element).kind = kind.value;
  });
  const textLabel = el("label", "Comment");
  textLabel.htmlFor = "comment-text";
  const text = el("textarea");
  text.id = "comment-text";
  text.rows = 4;
  text.value = draft.text;
  text.addEventListener("input", () => {
    ensureDraft(key, task, version, element).text = text.value;
  });
  const submit = el("button", "Add comment");
  submit.type = "submit";
  submit.id = "comment-submit";
  for (const control of [kind, text, submit]) control.disabled = draft.sending;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const live = ensureDraft(key, task, version, element);
    live.kind = kind.value;
    live.text = text.value;
    startSubmit(key);
  });
  form.append(kindLabel, kind, textLabel, text, submit, commentStatus(key, draft));
  return form;
}

function renderElementComments(view, item, writable) {
  const box = el("div");
  box.className = "element-comments";
  box.append(el("h5", `Comments on this element in version ${view.version}`));
  const mine = commentState.items.filter((c) => c.element === item.id && c.version === view.version);
  if (mine.length) {
    const list = el("ol");
    for (const comment of mine) list.append(commentLine(comment));
    box.append(list);
  } else {
    box.append(el("p", "no comments on this element yet"));
  }
  if (writable) {
    const key = draftKey(lastTaskId, view.version, item.id);
    box.append(commentForm(key, lastTaskId, view.version, item.id));
  } else {
    const cur = lastDiagrams && lastDiagrams.current;
    const old = !cur || view.version !== cur.version;
    const note = el("p", old ? "comments can be added only on the current version"
      : "the design changed; the new diagram version appears after a reload");
    note.className = "comment-note";
    box.append(note);
  }
  return box;
}

function repaintSelection() {
  withCommentFocus(() => refreshSelection(readHash().element));
}

function startSubmit(key) {
  const draft = commentDrafts.get(key);
  if (!draft || draft.sending) return;
  if (!draft.text.trim()) {
    Object.assign(draft, { error: "write a comment first", reload: false, retry: false });
    repaintSelection();
    return;
  }
  draft.requestId = draft.requestId || newRequestId();
  Object.assign(draft, { sending: true, error: null, reload: false, retry: false });
  repaintSelection();
  submitComment(draft, {
    task: draft.task, version: draft.version, element: draft.element, kind: draft.kind,
    text: draft.text, request_id: draft.requestId,
  });
}

function pause(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function postComment(payload) {
  let res = null;
  let data = {};
  let netErr = null;
  for (let attempt = 0; attempt < COMMENT_ATTEMPTS; attempt += 1) {
    if (attempt) await pause(COMMENT_BACKOFF_MS * 2 ** (attempt - 1));
    try {
      res = await fetch(COMMENTS_API, {
        method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      data = await res.json().catch(() => ({}));
      netErr = null;
      if (res.status !== 503) break;
    } catch (error) {
      res = null;
      netErr = error.message;
    }
  }
  return { res, data, netErr };
}

async function submitComment(draft, payload) {
  const { res, data, netErr } = await postComment(payload);
  draft.sending = false;
  if (res && (res.status === 201 || res.status === 200)) {
    const key = keyOf(draft);
    if (key !== null) commentDrafts.delete(key);
    repaintSelection();
    await refreshDashboard();
    return;
  }
  if (!res || res.status === 503) {
    draft.retry = true;
    draft.error = `could not save the comment (${netErr || data.error}); your text is kept`;
    if (draft.version !== payload.version) draft.requestId = null;
  } else {
    draft.error = data.error || res.statusText;
    draft.reload = res.status === 409 && data.reload === true;
    draft.requestId = null;
  }
  repaintSelection();
  if (res && res.status === 409) await refreshDashboard();
}

function reloadDraft(key) {
  const draft = commentDrafts.get(key);
  if (draft) Object.assign(draft, { reload: false, error: null });
  refreshDashboard();
}

function captureCommentFocus() {
  const active = document.activeElement;
  if (!active || !COMMENT_FOCUS_IDS.has(active.id)) return null;
  const text = active.id === "comment-text";
  return { id: active.id, start: text ? active.selectionStart : null,
    end: text ? active.selectionEnd : null, scroll: active.scrollTop };
}

function restoreCommentFocus(saved) {
  if (!saved) return;
  const node = document.getElementById(saved.id);
  if (!node || node.disabled) return;
  node.focus({ preventScroll: true });
  if (saved.start !== null) node.setSelectionRange(saved.start, saved.end);
  node.scrollTop = saved.scroll;
}

function withCommentFocus(fn) {
  const saved = captureCommentFocus();
  const result = fn();
  restoreCommentFocus(saved);
  return result;
}

function commentSummary(comment, current) {
  const element = comment.element_kind
    ? `${comment.element_kind} ${comment.element_label} (${comment.element})` : comment.element;
  const stale = current === comment.version ? "" : " (not current)";
  return `#${comment.id} · ${comment.status} · ${comment.kind} · ${element} · version `
    + `${comment.version}${stale}`;
}

function renderCommentsSection(comments, diagrams) {
  const section = el("section");
  section.id = "comments";
  const counts = comments.counts;
  section.append(el("h3", `Comments (open ${counts.open} · addressed ${counts.addressed}`
    + ` · resolved ${counts.resolved})`));
  if (!comments.items.length) {
    section.append(el("p", "no comments yet"));
    return section;
  }
  const current = diagrams && diagrams.current ? diagrams.current.version : null;
  const list = el("ol");
  for (const comment of comments.items) {
    const item = el("li");
    item.dataset.status = comment.status;
    item.append(el("p", commentSummary(comment, current)));
    const text = el("p", comment.text);
    text.className = "comment-text";
    const link = el("a", "show in diagram");
    const element = `element=${encodeURIComponent(comment.element)}`;
    link.href = current === comment.version ? `#${element}` : `#version=${comment.version}&${element}`;
    item.append(text, link);
    list.append(item);
  }
  section.append(list);
  return section;
}
