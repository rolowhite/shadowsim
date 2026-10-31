// Investigation Desk. Shows only what the Detective could know plus published claims.
// Ground truth is fetched from a separate endpoint, and only while Truth Audit is switched on.
let runId = runIdFromUrl();
let pinned = !!runId;
let desk = null, truth = null, notes = [], lastUpdated = null;
let truthOn = false;  // always off when the window opens

const KIND_BADGE = { rumor: ["rumor", "b-rumor"], official: ["agent claim · official", "b-claim"], report: ["agent claim", "b-claim"],
  official_finding: ["agent claim · official finding", "b-claim"], smear: ["agent claim · about a person", "b-claim"] };

async function init() {
  if (!runId) { const cur = await api("current").catch(() => ({})); runId = cur.id; }
  if (!runId) { $("#meta").innerHTML = "No run yet. Create one in the Control Room."; return; }
  for (const id of ["#fText", "#fFrom", "#fTo", "#fSource", "#fTopic", "#fStatus"]) $(id).addEventListener("input", render);
  $("#truthToggle").onclick = toggleTruth;
  $("#nConf").oninput = () => { $("#nConfV").textContent = $("#nConf").value + "%"; saveDraft(); };
  for (const id of ["#nText", "#nTags", "#nLinks"]) $(id).addEventListener("input", saveDraft);
  $("#nSave").onclick = saveNote;
  $("#nCancel").onclick = () => fillForm(null);
  setRun(runId);
  tick();
  setInterval(tick, 1500);
}

function setRun(id) {
  runId = id;
  history.replaceState(null, "", "/desk?run=" + id);
  $("#exportBtn").href = `/api/runs/${id}/export`;
  $("#controlLink").href = `/control?run=${id}`;
  desk = null; truth = null; lastUpdated = null;
  const d = lsGet("sc.draft." + id, null);
  fillForm(d && !d.id ? d : null);
  loadNotes();
}

async function toggleTruth() {
  truthOn = !truthOn;
  $("#truthToggle").setAttribute("aria-pressed", truthOn);
  $("#truthToggle").textContent = truthOn ? "Truth Audit: ON (ground truth shown)" : "Truth Audit: OFF";
  truth = truthOn ? await api(`runs/${runId}/desk/audit`).catch(() => null) : null;
  render();
}

async function tick() {
  try {
    const d = await api(`runs/${runId}/desk`);
    const r = d.run;
    $("#meta").innerHTML = `<span>Run <b class="mono">${esc(r.id)}</b></span><span>Round <b>${r.round}</b> (year ${(r.round / 52).toFixed(1)})</span>
      <span>Status <b>${esc(r.status)}</b></span><span>Model ${badge(r.provider?.label || "?", r.provider?.kind === "fallback" ? "b-warn" : "b-ok")}</span>
      <span class="muted">synced ${new Date().toLocaleTimeString()}</span>`;
    const b = $("#banner");
    if (d.current && d.current !== runId) {
      b.hidden = false;
      b.innerHTML = `The Control Room is now on run <b class="mono">${esc(d.current)}</b>. This desk stays on <b class="mono">${esc(runId)}</b>. <button id="follow">Switch to the new run</button>`;
      $("#follow").onclick = () => { pinned = false; setRun(d.current); tick(); };
    } else b.hidden = true;
    if (d.updated !== lastUpdated && d.view) {
      lastUpdated = d.updated; desk = d.view;
      if (truthOn) truth = await api(`runs/${runId}/desk/audit`).catch(() => truth);
      refreshFilters(); render();
    }
  } catch (e) { $("#meta").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

function refreshFilters() {
  const srcs = [...new Set(desk.news.map((n) => n.source).concat(desk.casebook.map((c) => c.source.split(" ")[0])))].sort();
  const keep = (sel, opts, first) => { const v = $(sel).value; $(sel).innerHTML = first + opts.map((o) => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join(""); };
  keep("#fSource", srcs, '<option value="">any</option>');
  keep("#fTopic", desk.incidents.map((i) => i.case), '<option value="">any case</option>');
  const ids = desk.news.map((n) => n.id).concat(desk.incidents.map((i) => i.case), desk.casebook.flatMap((c) => c.evidence), desk.agents);
  $("#linkIds").innerHTML = [...new Set(ids)].slice(-800).map((i) => `<option value="${esc(i)}">`).join("");
  $("#detName").textContent = desk.detective ? `current Detective: ${desk.detective}` : "no Detective in office (assessments frozen)";
}

function passes(round, source, topic, status, text) {
  const q = $("#fText").value.toLowerCase(), f = +$("#fFrom").value, t = +$("#fTo").value;
  if (q && !text.toLowerCase().includes(q)) return false;
  if ($("#fFrom").value && round < f) return false;
  if ($("#fTo").value && round > t) return false;
  if ($("#fSource").value && !String(source).startsWith($("#fSource").value)) return false;
  if ($("#fTopic").value && topic !== $("#fTopic").value) return false;
  if ($("#fStatus").value && status !== $("#fStatus").value) return false;
  return true;
}

function truthLine(caseId, claimId) {
  if (!truthOn || !truth) return "";
  const c = caseId ? truth.cases[caseId] : null, cl = claimId ? truth.claims[claimId] : null;
  const parts = [];
  if (cl) parts.push(`claim is <b>${cl.truth ? "TRUE" : "FALSE"}</b>`);
  if (c) parts.push(`${esc(caseId)} true cause: <b>${esc(c.true_cause.replaceAll("_", " "))}</b>`);
  return parts.length ? `<div class="truth">TRUTH AUDIT (ground truth, unknown to characters): ${parts.join(" · ")}</div>` : "";
}

function render() {
  if (!desk) return;
  const cb = desk.casebook.filter((e) => passes(e.round, e.source, e.case, e.status, `${e.id} ${e.finding} ${e.case} ${e.source} ${e.evidence.join(" ")}`));
  $("#casebook").innerHTML = cb.slice(-400).reverse().map((e) => `<div class="item" id="${e.id}">
      <div class="hd"><span class="mono">${e.id} · r${e.round}</span>${e.basis === "verified" ? badge("Detective verified", "b-verified") : badge("Detective inference", "b-infer")}
      ${statusBadge(e.status)} <span>confidence ${Math.round(e.confidence * 100)}%</span></div>
      <div class="tx">${esc(e.finding)}</div>
      <div class="hd">source: ${esc(e.source)}${e.evidence.length ? ` · evidence: <span class="mono">${esc(e.evidence.join(", "))}</span>` : ""}${e.event ? ` · <span class="mono">${e.event}</span>` : ""}</div>
      ${truthLine(e.case, e.claim)}</div>`).join("") || '<p class="muted">Nothing yet.</p>';
  const items = desk.incidents.map((i) => ({ fact: true, ...i })).concat(desk.news);
  const nf = items.filter((n) => n.fact ? passes(n.round, "fact", n.case, "", `${n.case} incident ${n.official_cause}`)
    : passes(n.round, n.source, n.case, n.status, `${n.id} ${n.text} ${n.publisher} ${n.case}`));
  nf.sort((a, b) => b.round - a.round);
  const more = nf.length > 400 ? `<p class="muted">Showing the newest 400 of ${nf.length}. Use the filters to see older items.</p>` : "";
  $("#news").innerHTML = more + nf.slice(0, 400).map((n) => n.fact ? `<div class="item"><div class="hd"><span class="mono">${esc(n.case)} · r${n.round}</span>${badge("fact", "b-fact")}</div>
      <div class="tx">Incident ${esc(n.case)} occurred. Official cause given: ${esc(n.official_cause.replaceAll("_", " "))}.</div>${truthLine(n.case, null)}</div>`
    : `<div class="item" id="${n.id}"><div class="hd"><span class="mono">${n.id} · r${n.round}</span>${badge(...(KIND_BADGE[n.kind] || ["agent claim", "b-claim"]))}
      ${statusBadge(n.status)} <span>by ${esc(n.publisher)} (${esc(n.source.replaceAll("_", " "))})</span></div>
      <div class="tx">${esc(n.text)}</div>
      <div class="hd">${n.case ? `topic ${esc(n.case)} · ` : ""}evidence level: ${esc(n.level)}${n.cited.length ? ` · cites <span class="mono">${esc(n.cited.join(", "))}</span>` : " · cites nothing"} · reach ${Math.round(n.reach * 100)}% · repeated ${n.repeats}×</div>
      ${truthLine(n.case, n.id)}</div>`).join("") || '<p class="muted">Nothing yet.</p>';
  renderNotes();
}

async function loadNotes() { notes = await api(`runs/${runId}/notes`).catch(() => []); renderNotes(); }

function renderNotes() {
  const q = $("#fText").value.toLowerCase();
  $("#notes").innerHTML = notes.filter((n) => !q || `${n.text} ${n.tags.join(" ")} ${n.links.join(" ")}`.toLowerCase().includes(q)).map((n) => `<div class="item">
    <div class="hd">${badge("your speculation", "b-spec")}<span>${new Date(n.created * 1000).toLocaleString()}${n.updated !== n.created ? " · edited " + new Date(n.updated * 1000).toLocaleString() : ""}</span>
    <span>confidence ${Math.round(n.confidence * 100)}%</span></div>
    <div class="tx">${esc(n.text)}</div>
    <div class="hd">${n.tags.map((t) => `<span class="chip">#${esc(t)}</span>`).join(" ")} ${n.links.map((l) => `<a class="mono" href="#${esc(l)}">${esc(l)}</a>`).join(" ")}</div>
    <div class="row"><button data-edit="${n.id}">Edit</button><button data-del="${n.id}">Delete</button></div></div>`).join("") || '<p class="muted">No notes yet.</p>';
  $("#notes").querySelectorAll("[data-edit]").forEach((b) => (b.onclick = () => fillForm(notes.find((n) => n.id === +b.dataset.edit))));
  $("#notes").querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Delete this note?")) return;
    await api(`runs/${runId}/notes/${b.dataset.del}`, { method: "DELETE" }); loadNotes();
  }));
}

const split = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);
function fillForm(n) {
  $("#nId").value = n?.id || ""; $("#nText").value = n?.text || "";
  $("#nConf").value = Math.round((n?.confidence ?? 0.5) * 100); $("#nConfV").textContent = $("#nConf").value + "%";
  $("#nTags").value = (n?.tags || []).join(", "); $("#nLinks").value = (n?.links || []).join(", ");
  $("#nSave").textContent = n?.id ? "Update note" : "Save note";
}
function saveDraft() { if (!$("#nId").value) lsSet("sc.draft." + runId, { text: $("#nText").value, confidence: $("#nConf").value / 100, tags: split($("#nTags").value), links: split($("#nLinks").value) }); }

async function saveNote() {
  $("#nErr").textContent = "";
  const body = { text: $("#nText").value.trim(), confidence: $("#nConf").value / 100, tags: split($("#nTags").value), links: split($("#nLinks").value) };
  if (!body.text) { $("#nErr").textContent = "Write something first."; return; }
  try {
    const id = $("#nId").value;
    await api(`runs/${runId}/notes${id ? "/" + id : ""}`, { method: id ? "PUT" : "POST", body });
    lsSet("sc.draft." + runId, null); fillForm(null); loadNotes();
  } catch (e) { $("#nErr").textContent = e.message; }
}

init();
