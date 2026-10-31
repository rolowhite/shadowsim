// Simulation Control Room. Reads the shared run state from the server every second.
let runId = runIdFromUrl();
let since = 0;
let events = [];
let showAudit = false;
let lastView = null;
const CATS = ["government", "investigation", "elimination", "mutation", "defection", "mentorship", "news", "succession", "state", "system"];
const catOn = lsGet("sc.cats", Object.fromEntries(CATS.map((c) => [c, c !== "mentorship"])));
const TIER_NAMES = { 0: "Tier 0 · Ruler", 1: "Tier 1 · Sub-Ruler", 2: "Tier 2 · Government and ministries", 3: "Tier 3 · Society", 4: "Tier 4 · Learners" };

function setErr(m) { $("#err").textContent = m || ""; }

async function init() {
  $("#filters").innerHTML = CATS.map((c) => `<label><input type="checkbox" data-cat="${c}" ${catOn[c] ? "checked" : ""}>${c}</label>`).join("");
  $("#filters").addEventListener("change", (e) => { catOn[e.target.dataset.cat] = e.target.checked; lsSet("sc.cats", catOn); renderTimeline(); });
  if (!runId) { const cur = await api("current").catch(() => ({})); runId = cur.id; }
  if (runId) history.replaceState(null, "", "/control?run=" + runId);
  for (const [id, cmd] of [["#bStart", "start"], ["#bPause", "pause"], ["#bResume", "resume"], ["#bStep", "step"]]) $(id).onclick = () => control(cmd);
  $("#bReset").onclick = async () => {
    if (!runId || !confirm("Start a fresh run with the same seed and settings? The current run is kept in history.")) return;
    const r = await api(`runs/${runId}/control`, { method: "POST", body: { cmd: "reset" } }).catch((e) => setErr(e.message));
    if (r) switchRun(r.id);
  };
  $("#speed").onchange = () => control(null);
  $("#openDesk").onclick = () => window.open("/desk?run=" + runId, "shadowcity-desk");
  $("#bCreate").onclick = createRun;
  $("#prov").onchange = checkModel;
  $("#auditToggle").onclick = () => { showAudit = !showAudit; $("#auditToggle").setAttribute("aria-pressed", showAudit); $("#auditToggle").textContent = showAudit ? "Hide ground truth" : "Show ground truth"; $("#audit").hidden = !showAudit; since = 0; events = []; if (showAudit) loadAudit(); };
  const cfg = await api("config");
  $("#model").value = cfg.config.llm.model;
  tick();
  setInterval(tick, 1000);
  setInterval(() => showAudit && loadAudit(), 2500);
}

async function checkModel() {
  const p = $("#prov").value;
  if (p === "fallback") { $("#modelStatus").textContent = "Decisions use the shared rule. They are labeled fallback_rule and never shown as LLM output."; return; }
  $("#modelStatus").textContent = "Checking model…";
  const info = await api("model?provider=" + p).catch((e) => ({ label: e.message }));
  $("#modelStatus").textContent = info.label + (info.detail ? ` (${info.detail})` : "");
}

async function createRun() {
  setErr("");
  try {
    const r = await api("runs", { method: "POST", body: { seed: +$("#seed").value, premise: $("#premise").value, premise_visibility: $("#pvis").value,
      overrides_text: $("#over").value, provider: $("#prov").value, model: $("#model").value } });
    switchRun(r.id);
  } catch (e) { setErr(e.message); }
}

function switchRun(id) { runId = id; since = 0; events = []; history.replaceState(null, "", "/control?run=" + id); tick(); }

async function control(cmd) {
  if (!runId) return setErr("Create a run first.");
  setErr("");
  await api(`runs/${runId}/control`, { method: "POST", body: { cmd: cmd || "speed", speed: $("#speed").value } }).catch((e) => setErr(e.message));
  tick();
}

async function tick() {
  if (!runId) return;
  try {
    const s = await api(`runs/${runId}/state`);
    renderMeta(s);
    if (s.view && s.updated !== lastView) { lastView = s.updated; renderIndicators(s.view); renderRoster(s.view); }
    const evs = await api(`runs/${runId}/events?since=${since}&limit=1000&audit=${showAudit ? 1 : 0}`);
    if (evs.length) { since = evs[evs.length - 1].seq; events = events.concat(evs).slice(-3000); renderTimeline(); }
  } catch (e) { setErr(e.message); }
}

function renderMeta(s) {
  const r = s.run, v = s.view || {};
  const prov = r.provider || {};
  const llm = prov.kind && prov.kind !== "fallback";
  $("#openDesk").disabled = false;
  $("#meta").innerHTML = `<span>Run <b class="mono">${esc(r.id)}</b></span><span>Seed <b>${r.seed}</b></span>
    <span>Round <b>${r.round}</b> (year ${(r.round / 52).toFixed(1)})</span><span>Epoch <b>${v.epoch ?? 0}</b> (500 rounds each)</span>
    <span>Status <b>${esc(r.status)}</b></span>
    <span>Model ${badge(prov.label || "?", llm ? "b-ok" : "b-warn")}</span>
    ${v.final ? `<span>Outcome <b>${esc(v.final.replaceAll("_", " "))}</b></span>` : ""}
    ${r.premise?.text ? `<span title="${esc(r.premise.text)}">Premise ${badge(r.premise.visibility, "b-muted")}</span>` : ""}
    ${(r.flags || []).length ? `<span class="err" title="${esc((r.flags || []).join("\n"))}">⚠ ${r.flags.length} flag(s)</span>` : ""}
    ${s.current && s.current !== r.id ? `<span>${badge("not the current run", "b-warn")}</span>` : ""}`;
  if (s.speed !== null && s.speed !== undefined && document.activeElement !== $("#speed")) $("#speed").value = s.speed === 0 ? "max" : String(s.speed);
}

function renderIndicators(v) {
  const help = v.indicator_help || {};
  $("#indicators").innerHTML = Object.entries(v.indicators || {}).map(([k, val]) => `<div class="ind">
    <div class="top"><details><summary><b>${esc(k.replaceAll("_", " "))}</b></summary><p>${esc(help[k] || "")}</p></details><span class="mono">${(+val).toFixed(2)}</span></div>
    <div class="bar"><i style="width:${Math.round(val * 100)}%"></i></div></div>`).join("")
    + (Object.keys(v.milestones || {}).length ? `<div class="ind"><b>Milestones</b><br>${Object.entries(v.milestones).map(([k, r]) => `${esc(k.replaceAll("_", " "))} @ r${r}`).join("<br>")}</div>` : "")
    + `<div class="ind"><b>Legacy queue</b><br>${v.legacy_queue.queued} queued · ${v.legacy_queue.delivered} delivered</div>`;
}

function renderRoster(v) {
  const byTier = {};
  for (const a of v.roster) (byTier[a.tier] ||= []).push(a);
  $("#roster").innerHTML = Object.keys(byTier).sort().map((t) => `<div class="tier"><h3>${TIER_NAMES[t] || "Tier " + t}</h3>${byTier[t].map(agentRow).join("")}</div>`).join("");
}

function agentRow(a) {
  const tr = a.traits;
  const alive = a.status === "active" || a.status === "hiding";
  const life = a.ends !== null ? `born r${a.born} · ends r${a.ends}` : `born r${a.born} · no fixed lifespan`;
  const beliefs = a.beliefs.map((b) => `<span class="chip" title="Agent-held belief: P(State behind it) ≈ ${b.p_state}">${badge("belief", "b-infer")} ${esc(b.case)}: ${esc(b.evidence)}${b.linked_to ? " → " + esc(b.linked_to.replaceAll("_", " ")) : ""}</span>`).join("");
  return `<div class="agent ${alive ? "" : "gone"}">
    <div><div class="name">${esc(a.label)}</div><div class="muted">${esc(a.role_name)}${a.alias ? ` · cover: ${esc(a.alias.replaceAll("_", " "))}` : ""} · ${badge(a.status, alive ? "b-ok" : "b-muted")}</div></div>
    <div class="mono">Int/Ana ${tr.int}/${tr.ana} · Sec/Man ${tr.sec}/${tr.man} · Cur/Greed ${tr.cur}/${tr.greed}</div>
    <div class="muted">loyalty ${a.loyalty} · fear ${a.fear}<br>${life}</div>
    ${beliefs ? `<div class="beliefs">${beliefs}</div>` : ""}</div>`;
}

function renderTimeline() {
  const shown = events.filter((e) => catOn[e.category] || (showAudit && e.category === "decision"));
  $("#evCount").textContent = `${shown.length} shown`;
  $("#timeline").innerHTML = shown.slice(-400).reverse().map((e) => `<div class="ev"><span class="r">${e.id} · r${e.round}</span>${badge(e.category, "b-muted")} ${visBadge(e.visibility)}
    ${e.decided_by ? badge(e.decided_by.startsWith("llm:") ? "LLM" : e.decided_by, e.decided_by.startsWith("llm:") ? "b-ok" : "b-muted") : ""} ${esc(e.text)}</div>`).join("");
}

async function loadAudit() {
  if (!runId) return;
  const a = await api(`runs/${runId}/audit`).catch(() => null);
  if (!a) return;
  const t = (rows, cols) => `<div class="scroll"><table><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr>${rows.map((r) => `<tr>${cols.map((c) => `<td>${esc(typeof r[c] === "object" ? JSON.stringify(r[c]) : r[c])}</td>`).join("")}</tr>`).join("")}</table></div>`;
  $("#audit").innerHTML = `<p>Decisions by maker: ${esc(JSON.stringify(a.decided_by))} · realized genuine-incident share: ${a.genuine_share} · regime: ${esc(a.regime)} · government competence: ${a.competence}</p>
    <h3>Incidents (true causes)</h3>${t(a.cases.map((c) => ({ id: c.id, round: c.round, kind: c.kind, official_story: c.official_story, true_cause: c.decree ? "decree " + c.decree : c.subtype, ministry: c.ministry, muddy: c.muddy, publicly_verified: c.publicly_verified })), ["id", "round", "kind", "official_story", "true_cause", "ministry", "muddy", "publicly_verified"])}
    <h3>Signed decrees and orders (immutable)</h3>${t(a.decrees, ["id", "round", "case", "ministry", "issued_by", "executor", "handler", "scope", "witnesses", "execution_error", "cover_quality", "signature_valid"])}
    ${t(a.orders, ["id", "round", "kind", "issued_by", "target", "case"])}
    <h3>Traces</h3>${t(a.traces, ["id", "case", "kind", "visibility", "base_visibility", "floor", "attribution"])}
    <h3>Legacy Evidence Packet queue</h3>${t(a.legacy_queue.map((e) => ({ packet: e.packet.id, decree: e.packet.decree_id, status: e.status, holder: e.holder, history: e.history, interference: e.interference })), ["packet", "decree", "status", "holder", "history", "interference"])}
    <h3>Secret flags and hidden attributes</h3>${t(a.secret_flags, ["id", "status", "corrupted", "compromised", "double_agent", "flagged", "hidden", "unmasked"])}
    <h3>Ruling family (off-roster)</h3>${t(a.hidden_family, ["id", "traits"])}
    <h3>State surveillance file</h3>${t(Object.entries(a.state_file).map(([k, v]) => ({ person: k, ...v })), ["person", "threat", "believed_loyal", "records"])}
    <p class="muted">Timeline now includes omniscient engine records (decisions with scores, true causes of deaths, milestones).</p>`;
}

init();
