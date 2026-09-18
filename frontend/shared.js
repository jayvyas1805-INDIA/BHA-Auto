/* shared.js — utilities used by both the dashboard (app.js) and the
 * upload/progress page (upload.js). Loaded before either.
 */
const API = (window.API_BASE || "") + "/api";

const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;   // textContent, never innerHTML:
  return n;                                        // filenames and extracted PDF
};                                                 // strings are untrusted input

function num(v) {
  if (v === null || v === undefined) return null;
  const f = parseFloat(String(v).replace(",", "."));
  return Number.isFinite(f) ? f : null;
}

async function api(path, options) {
  const res = await fetch(API + path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

function badge(level) {
  const b = el("span", "badge " + (level || "processing"));
  b.appendChild(el("span", "dot"));
  b.appendChild(document.createTextNode(level || "processing"));
  return b;
}
