/* upload.js — dedicated upload page: POST the file, then poll
 * GET /api/documents/{id}/status and render a live step tracker.
 * Uses API/el/num/api/badge from shared.js (loaded first).
 */

// Mirrors bha/db.py's STAGES (minus the terminal 'done'/'failed', which are
// rendered as the job's overall state rather than a step in the list).
// If a stage isn't reached because it's skipped (e.g. 'normalizing' when
// USE_LLM_NORMALIZATION is off), it's simply marked 'done' the instant the
// server reports a later stage -- see renderSteps' index comparison.
const VISIBLE_STAGES = [
  ["queued", "Queued"],
  ["reading_pdf", "Reading PDF"],
  ["locating_chapter", "Locating BHA chapter (via table of contents)"],
  ["parsing", "Parsing metadata & component tables"],
  ["normalizing", "Normalizing labels with LLM"],
  ["validating", "Validating & scoring confidence"],
  ["saving", "Saving to database"],
];

const jobState = {
  documentId: null,
  filename: null,
  queue: [],                  // {id, filename, status, confidence} for this browser session only
};

function renderSteps(stage, status) {
  const list = document.getElementById("steps");
  list.replaceChildren();

  // `stage` is authoritative even when status is 'failed': mark_failed()
  // deliberately leaves it at whatever the last successful set_stage()
  // call set it to, instead of overwriting it to a generic 'failed' value.
  // That's what lets this render the exact step a failure happened on,
  // including failures fast enough that the client never polls mid-stage.
  const currentIndex = VISIBLE_STAGES.findIndex(([key]) => key === stage);

  VISIBLE_STAGES.forEach(([key, label], i) => {
    let cls = "step";
    if (status === "failed") {
      if (i < currentIndex) cls += " done";
      else if (i === currentIndex) cls += " failed";
    } else if (status === "done") {
      cls += " done";
    } else if (i < currentIndex) {
      cls += " done";
    } else if (i === currentIndex) {
      cls += " active";
    }
    const li = el("li", cls);
    li.appendChild(el("span", "step-dot"));
    li.appendChild(el("span", "step-label", label));
    list.appendChild(li);
  });
}

function renderQueue() {
  const box = document.getElementById("queue-list");
  box.replaceChildren();
  if (jobState.queue.length === 0) {
    box.appendChild(el("div", "empty", "no jobs yet this session"));
    return;
  }
  jobState.queue.slice().reverse().forEach((j) => {
    const item = el("div", "queue-item");
    item.appendChild(el("div", "queue-name", j.filename));
    item.appendChild(badge(j.status === "done" ? j.confidence : j.status));
    box.appendChild(item);
  });
}

function upsertQueue(job) {
  const i = jobState.queue.findIndex((q) => q.id === job.id);
  if (i === -1) jobState.queue.push(job);
  else jobState.queue[i] = job;
  renderQueue();
}

function showJobPanel(filename) {
  document.getElementById("dropzone").classList.add("hidden");
  document.getElementById("job").classList.remove("hidden");
  document.getElementById("job-filename").textContent = filename;
  document.getElementById("job-sub").textContent = "starting…";
  document.getElementById("job-error").classList.add("hidden");
  document.getElementById("job-actions").classList.add("hidden");
  const b = document.getElementById("job-badge");
  b.className = "badge processing";
  b.innerHTML = "";
  b.appendChild(el("span", "dot"));
  b.appendChild(document.createTextNode("queued"));
  renderSteps("queued", "processing");
}

function resetToDropzone() {
  document.getElementById("job").classList.add("hidden");
  document.getElementById("dropzone").classList.remove("hidden");
  jobState.documentId = null;
  jobState.filename = null;
}

async function pollJob(documentId) {
  let s;
  try {
    s = await api(`/documents/${documentId}/status`);
  } catch (e) {
    document.getElementById("job-sub").textContent = "lost connection to the API — retrying…";
    setTimeout(() => pollJob(documentId), 2500);
    return;
  }

  document.getElementById("job-sub").textContent =
    s.status === "processing" ? `stage: ${s.stage}` : s.status;

  const b = document.getElementById("job-badge");
  const level = s.status === "done" ? s.confidence : s.status;
  b.className = "badge " + (level || "processing");
  b.innerHTML = "";
  b.appendChild(el("span", "dot"));
  b.appendChild(document.createTextNode(level || "processing"));

  renderSteps(s.stage, s.status);
  upsertQueue({ id: documentId, filename: jobState.filename, status: s.status, confidence: s.confidence });

  if (s.status === "processing") {
    setTimeout(() => pollJob(documentId), 1200);
    return;
  }

  if (s.status === "done") {
    const actions = document.getElementById("job-actions");
    actions.classList.remove("hidden");
    document.getElementById("job-view-link").href = `/?doc=${documentId}`;
  } else if (s.status === "failed") {
    const errBox = document.getElementById("job-error");
    errBox.textContent = s.error || "extraction failed with no error message recorded";
    errBox.classList.remove("hidden");
    document.getElementById("job-actions").classList.remove("hidden");
    document.getElementById("job-view-link").classList.add("hidden");
  }
}

async function handleUpload(file) {
  jobState.filename = file.name;
  showJobPanel(file.name);

  try {
    const fd = new FormData();
    fd.append("file", file);
    const res = await api("/documents", { method: "POST", body: fd });
    jobState.documentId = res.document_id;

    if (res.status === "already_processed") {
      document.getElementById("job-sub").textContent = "already processed";
      renderSteps("saving", "done");
      const actions = document.getElementById("job-actions");
      actions.classList.remove("hidden");
      document.getElementById("job-view-link").href = `/?doc=${res.document_id}`;
      upsertQueue({ id: res.document_id, filename: file.name, status: "done", confidence: "high" });
      return;
    }

    pollJob(res.document_id);
  } catch (e) {
    document.getElementById("job-sub").textContent = "upload failed";
    const errBox = document.getElementById("job-error");
    errBox.textContent = String(e.message || e);
    errBox.classList.remove("hidden");
    document.getElementById("job-actions").classList.remove("hidden");
    document.getElementById("job-view-link").classList.add("hidden");
  }
}

/* ---------------- wiring ---------------- */

const dropzone = document.getElementById("dropzone");
const fileInput = document.getElementById("file-input");

fileInput.addEventListener("change", (e) => {
  const f = e.target.files[0];
  if (f) handleUpload(f);
  e.target.value = "";
});

["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.add("drag"); })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => { e.preventDefault(); dropzone.classList.remove("drag"); })
);
dropzone.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0];
  if (f) handleUpload(f);
});

document.getElementById("job-another").addEventListener("click", resetToDropzone);

renderQueue();
