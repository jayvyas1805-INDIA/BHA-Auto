/* app.js — dashboard: browse processed documents. Uses API/el/num/api/badge
 * from shared.js (loaded first, see index.html).
 */
const state = {
  documents: [],
  selectedId: null,
  detail: null,          // full document record for selectedId
  activeWellbore: 0,
  polling: new Set(),    // document ids currently being polled
};

/* ---------------- left rail ---------------- */

function renderDocList() {
  const list = document.getElementById("doc-list");
  list.replaceChildren();

  if (state.documents.length === 0) {
    list.appendChild(el("div", "empty", "no documents yet — upload a PDF"));
    return;
  }

  for (const doc of state.documents) {
    const btn = el("button", "doc" + (doc.id === state.selectedId ? " active" : ""));
    btn.appendChild(el("div", "doc-name", doc.filename));

    const meta = el("div", "doc-meta");
    // status drives the badge until processing finishes; then confidence does
    meta.appendChild(badge(doc.status === "done" ? doc.confidence : doc.status));
    if (doc.report_date) meta.appendChild(el("span", "doc-count", doc.report_date));
    btn.appendChild(meta);

    btn.onclick = () => selectDocument(doc.id);
    list.appendChild(btn);
  }
}

/* ---------------- stack diagram ---------------- */

function stackDiagram(table) {
  const rows = table.filter((r) => num(r.od_in) !== null || num(r.length_m) !== null);
  const wrap = el("div", "stack");
  if (rows.length === 0) return wrap;

  const maxOd = Math.max(...rows.map((r) => num(r.od_in) || 0), 1);
  const lengths = rows.map((r) => num(r.length_m) || 0.3);
  const total = lengths.reduce((a, b) => a + b, 0) || 1;

  // Height tracks the table beside it: a 1-row BHA shouldn't reserve the
  // same 220px as a 20-row one, or the card is mostly dead space. ~26px
  // per row matches the table's row height, clamped so a very long string
  // still fits on screen.
  const H = Math.min(Math.max(rows.length * 26, 52), 260);
  const MAXW = 64;

  wrap.appendChild(el("div", "stack-cap", "BIT"));
  const body = el("div", "stack-body");
  body.style.height = H + "px";

  let cursor = 0;
  rows.forEach((r, i) => {
    const od = num(r.od_in) || 0;
    const len = lengths[i];
    const h = Math.max((len / total) * H, 3);
    const w = Math.max((od / maxOd) * MAXW, 8);
    const seg = el("div", "seg");
    seg.style.top = (cursor / total) * H + "px";
    seg.style.left = (MAXW - w) / 2 + 10 + "px";
    seg.style.width = w + "px";
    seg.style.height = h + "px";
    seg.style.background = `hsl(${200 - (i / rows.length) * 40}, 28%, ${34 + (i % 2) * 6}%)`;
    seg.title = `${r.string_component || r.component || ""} · OD ${r.od_in ?? "—"} · ${r.length_m ?? "—"} m`;
    body.appendChild(seg);
    cursor += len;
  });

  wrap.appendChild(body);
  wrap.appendChild(el("div", "stack-cap", "SURFACE"));
  return wrap;
}

/* ---------------- run card ---------------- */

const COLS = [
  ["string_component", "COMPONENT"],
  ["od_in", "OD (in)"],
  ["id_in", "ID (in)"],
  ["length_m", "LENGTH (m)"],
  ["acc_length_m", "ACC. LENGTH (m)"],
];

function componentTable(table) {
  if (!table || table.length === 0) {
    return el("div", "no-rows", "no component rows extracted for this run");
  }
  const t = el("table");
  const thead = el("thead");
  const hr = el("tr");
  COLS.forEach(([, label]) => hr.appendChild(el("th", null, label)));
  thead.appendChild(hr);
  t.appendChild(thead);

  const tb = el("tbody");
  table.forEach((row) => {
    const tr = el("tr");
    COLS.forEach(([key]) => {
      const v = row[key];
      tr.appendChild(el("td", null, v === null || v === undefined || v === "" ? "—" : String(v)));
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  return t;
}

function runCard(run) {
  const card = el("div", "run");

  const head = el("div", "run-head");
  const left = el("div");
  const idRow = el("div", "run-id");
  idRow.appendChild(el("span", "run-no", `BHA NO. ${run.bha_no ?? "—"}`));
  if (run.name) idRow.appendChild(el("span", "run-tag", `RUN NAME ${run.name}`));
  if (run.kind) idRow.appendChild(el("span", "run-tag", `· ${run.kind}`));
  left.appendChild(idRow);
  if (run.description) left.appendChild(el("div", "run-desc", run.description));
  head.appendChild(left);

  const table = run.table || [];
  head.appendChild(el("div", "run-count", `${table.length} components`));
  card.appendChild(head);

  const body = el("div", "run-body");
  const tw = el("div", "table-wrap");
  tw.appendChild(componentTable(table));
  body.appendChild(tw);
  body.appendChild(stackDiagram(table));
  card.appendChild(body);

  return card;
}

/* ---------------- main pane ---------------- */

function renderMain() {
  const main = document.getElementById("main");
  main.replaceChildren();

  const doc = state.detail;
  if (!doc) {
    main.appendChild(el("div", "empty-main", "Select a report"));
    return;
  }

  if (doc.status !== "done") {
    const head = el("div", "doc-head");
    head.appendChild(el("div", "doc-title", doc.filename));
    const s = el("div", "doc-sub",
      doc.status === "failed"
        ? `extraction failed: ${doc.error || "unknown error"}`
        : "processing… this page refreshes automatically");
    head.appendChild(s);
    main.appendChild(head);
    return;
  }

  const data = doc.raw_json || { wellbores: [] };
  const wellbores = data.wellbores || [];

  const head = el("div", "doc-head");
  const row = el("div", "doc-head-row");
  const l = el("div");
  l.appendChild(el("div", "doc-date", `REPORT DATE ${data.date || "—"}`));
  l.appendChild(el("div", "doc-title", doc.filename));
  const totalRuns = wellbores.reduce((n, w) => n + (w.bha_list || []).length, 0);
  l.appendChild(el("div", "doc-sub",
    `chapter pages ${doc.chapter_start_page ?? "?"}–${doc.chapter_end_page ?? "?"} · ` +
    `${wellbores.length} wellbores · ${totalRuns} BHA runs`));
  row.appendChild(l);
  row.appendChild(badge(doc.confidence));
  head.appendChild(row);

  const warnings = doc.warnings_json || [];
  if (warnings.length) {
    const w = el("div", "warnings");
    warnings.forEach((x) => w.appendChild(el("div", null, "⚠ " + x)));
    head.appendChild(w);
  }

  if (state.activeWellbore >= wellbores.length) state.activeWellbore = 0;

  const tabs = el("div", "tabs");
  wellbores.forEach((wb, i) => {
    const t = el("button", "tab" + (i === state.activeWellbore ? " active" : ""),
      `${wb.wellbore || "(unlabeled)"} · ${(wb.bha_list || []).length}`);
    t.onclick = () => { state.activeWellbore = i; renderMain(); };
    tabs.appendChild(t);
  });
  head.appendChild(tabs);
  main.appendChild(head);

  const runs = el("div", "runs");
  const wb = wellbores[state.activeWellbore];
  if (wb) (wb.bha_list || []).forEach((run) => runs.appendChild(runCard(run)));
  main.appendChild(runs);
}

/* ---------------- data loading ---------------- */

async function loadDocuments() {
  try {
    state.documents = await api("/documents");
  } catch (e) {
    document.getElementById("doc-list").replaceChildren(
      el("div", "empty", "could not reach the API — is uvicorn running?"));
    return;
  }
  renderDocList();

  // Anything still processing gets polled until it settles.
  state.documents
    .filter((d) => d.status === "processing")
    .forEach((d) => pollDocument(d.id));
}

async function selectDocument(id) {
  state.selectedId = id;
  state.activeWellbore = 0;
  renderDocList();
  try {
    state.detail = await api(`/documents/${id}`);
  } catch (e) {
    state.detail = null;
  }
  renderMain();
}

function pollDocument(id) {
  if (state.polling.has(id)) return;   // don't stack timers on the same doc
  state.polling.add(id);

  const tick = async () => {
    let s;
    try {
      s = await api(`/documents/${id}/status`);
    } catch (e) {
      state.polling.delete(id);
      return;
    }
    if (s.status === "processing") {
      setTimeout(tick, 2000);
      return;
    }
    state.polling.delete(id);
    await loadDocuments();
    if (state.selectedId === id) await selectDocument(id);
  };
  setTimeout(tick, 2000);
}

/* ---------------- init ---------------- */

loadDocuments().then(() => {
  const params = new URLSearchParams(window.location.search);
  const docParam = parseInt(params.get("doc"), 10);
  if (docParam && state.documents.some((d) => d.id === docParam)) {
    selectDocument(docParam);
  } else if (state.documents.length && state.selectedId === null) {
    selectDocument(state.documents[0].id);
  }
});
