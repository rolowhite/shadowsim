// Shared helpers for both windows. No framework; each window has its own script.
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch("/api/" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function badge(text, cls) { return `<span class="badge ${cls}">${esc(text)}</span>`; }

const STATUS_BADGE = {
  verified: ["verified (Detective)", "b-verified"],
  contradicted: ["contradicted (Detective)", "b-bad"],
  under_investigation: ["under investigation", "b-warn"],
  unverified: ["unverified", "b-muted"],
};
function statusBadge(st) { const [t, c] = STATUS_BADGE[st] || [st, "b-muted"]; return badge(t, c); }

function visBadge(v) {
  if (v === "public") return badge("public", "b-fact");
  if (v === "state") return badge("State channel", "b-warn");
  if (v === "civil") return badge("public-side only", "b-infer");
  if (v === "omniscient") return badge("omniscient", "b-bad");
  if (Array.isArray(v)) return badge("known to " + v.length, "b-muted");
  return "";
}

function fmtRound(r, perYear = 52) { return `r${r} · yr ${(r / perYear).toFixed(1)}`; }

function runIdFromUrl() { return new URLSearchParams(location.search).get("run"); }

function safeStore(kind) { try { return window[kind]; } catch { return null; } }
function lsGet(k, d) { try { const v = safeStore("localStorage")?.getItem(k); return v === null || v === undefined ? d : JSON.parse(v); } catch { return d; } }
function lsSet(k, v) { try { safeStore("localStorage")?.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } }
