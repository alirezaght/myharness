"use strict";

// P2-06: the developer resolves or re-opens a comment; addressing is the harness's job and is
// never offered here. A move in flight disables the comment's buttons and survives live
// re-renders (commentMoves); a failed move keeps a Retry button. Built with textContent only.
// Loaded after comments.js and before compare.js and app.js; their functions are only called
// after load.

const commentMoves = new Map();
const MOVE_VERBS = { resolve: "resolve", reopen: "re-open" };

function moveKey(taskId, id) {
  return `${taskId} ${id}`;
}

function commentActions(taskId, comment, addressedIn) {
  const box = el("div");
  box.className = "comment-actions";
  box.id = `comment-${comment.id}-actions`;
  const state = commentMoves.get(moveKey(taskId, comment.id));
  if (addressedIn) {
    box.append(el("p", `addressed in version ${addressedIn}`));
    if (addressedIn > comment.version) {
      box.append(commentButton(`comment-${comment.id}-compare`,
        `compare version ${comment.version} → ${addressedIn}`,
        () => showComparison(comment.version, addressedIn)));
    }
  }
  const buttons = [];
  if (comment.status === "addressed") {
    buttons.push(commentButton(`comment-${comment.id}-resolve`, "Resolve",
      () => moveComment(taskId, comment.id, "resolve")));
  }
  if (comment.status === "addressed" || comment.status === "resolved") {
    buttons.push(commentButton(`comment-${comment.id}-reopen`, "Re-open",
      () => moveComment(taskId, comment.id, "reopen")));
  }
  if (comment.status === "open") {
    const note = el("p", "open: waiting for the harness to address it");
    note.className = "comment-note";
    box.append(note);
  }
  for (const button of buttons) button.disabled = !!(state && state.sending);
  box.append(...buttons);
  if (state && state.sending) {
    const note = el("p", "saving…");
    note.setAttribute("role", "status");
    box.append(note);
  }
  if (state && state.error) box.append(alertText(state.error));
  if (state && state.retry) {
    box.append(commentButton(`comment-${comment.id}-retry`, "Retry",
      () => moveComment(taskId, comment.id, state.action)));
  }
  return box;
}

function repaintComments() {
  const node = document.getElementById("comments");
  if (!node || !commentState.comments) return;
  withCommentFocus(() => node.replaceWith(renderCommentsSection(commentState.comments,
    lastDiagrams)));
}

function focusActions(id) {
  const next = document.querySelector(`#comment-${id}-actions button:not(:disabled)`);
  if (next) next.focus({ preventScroll: true });
}

async function moveComment(taskId, id, action) {
  const key = moveKey(taskId, id);
  const prior = commentMoves.get(key);
  if (prior && prior.sending) return;
  const active = document.activeElement;
  const hadFocus = !!active && !!active.id && active.id.startsWith(`comment-${id}-`);
  commentMoves.set(key, { action, sending: true, error: null, retry: false });
  repaintComments();
  try {
    const { res, data, netErr } = await postJson(`${COMMENTS_API}/${action}`,
      { task: taskId, comment: id });
    if (res && res.status === 200) {
      commentMoves.delete(key);
      await refreshDashboard();
      return;
    }
    if (!res || res.status === 503) {
      commentMoves.set(key, { action, sending: false, retry: true,
        error: `could not ${MOVE_VERBS[action]} comment #${id} (${netErr || data.error}); try again` });
      repaintComments();
      return;
    }
    commentMoves.set(key, { action, sending: false, retry: false,
      error: data.error || res.statusText });
    repaintComments();
    if (res.status === 409 || res.status === 404) await refreshDashboard();
  } finally {
    if (hadFocus) focusActions(id);
  }
}
