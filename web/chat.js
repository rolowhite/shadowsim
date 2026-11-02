// Story Chat: conversations and story beats for one run, synced from the shared database.
let runId = runIdFromUrl();
let since = 0;
let items = [];
let verdict = [];
const people = new Set();
const ACT_LABEL = { advise: "advises", reply: "replies", confide: "confides", believe: "", doubt: "", thought: "thinks", proposal: "proposes",
  interrogate: "questions", offer: "offers", coerce: "threatens", suppress: "warns", smear: "orders a smear", leak: "leaks", warn: "warns", flag: "reports",
  dms: "orders", infiltrate: "thinks", mentor: "mentors" };

async function init() {
  if (!runId) { const cur = await api("current").catch(() => ({})); runId = cur.id; }
  if (!runId) { $("#meta").textContent = "No run yet. Create one in the Control Room."; return; }
  history.replaceState(null, "", "/chat?run=" + runId);
  $("#controlLink").href = "/control?run=" + runId;
  for (const id of ["#fPrivate", "#fPublic", "#fNarr", "#fMentor", "#fWho", "#fText", "#fFrom", "#fTo"]) $(id).addEventListener("input", () => render(true));
  $("#toBottom").onclick = () => window.scrollTo(0, document.body.scrollHeight);
  window.addEventListener("scroll", () => { $("#toBottom").hidden = atBottom(); });
  await poll();
  setInterval(poll, 1500);
}

const atBottom = () => window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;

async function poll() {
  try {
    let more = true;
    while (more) {
      const r = await api(`runs/${runId}/chat?since=${since}`);
      const run = r.run;
      $("#meta").innerHTML = `<span>Run <b class="mono">${esc(run.id)}</b></span><span>Round <b>${run.round}</b> (year ${(run.round / 52).toFixed(1)})</span>
        <span>Status <b>${esc(run.status)}</b></span><span>Model ${badge(run.provider?.label || "?", run.provider?.kind === "fallback" ? "b-warn" : "b-ok")}</span>`;
      more = r.next - since >= 4999;  // a full batch: keep catching up
      if (r.next === since) break;
      since = r.next;
      const stick = atBottom();
      for (const l of r.lines) {
        if (l.kind === "verdict") verdict.push(l);
        else items.push(l);
        if (l.speaker_label && l.kind === "line") people.add(l.speaker_label.split(" [")[0]);
      }
      if (items.length > 60000) items = items.slice(-60000);
      refreshPeople();
      render(false, stick);
    }
  } catch (e) { $("#meta").innerHTML = `<span class="err">${esc(e.message)}</span>`; }
}

function refreshPeople() {
  const sel = $("#fWho"), v = sel.value;
  if (sel.options.length - 1 === people.size) return;
  sel.innerHTML = '<option value="">anyone</option>' + [...people].sort().map((p) => `<option ${p === v ? "selected" : ""}>${esc(p)}</option>`).join("");
}

function keep(l) {
  if (l.kind === "line") {
    if (l.act === "mentor" && !$("#fMentor").checked) return false;
    if (l.public ? !$("#fPublic").checked : !$("#fPrivate").checked) return false;
  } else if (l.kind === "claim") { if (!$("#fPublic").checked) return false; }
  else if (!$("#fNarr").checked) return false;
  if (l.kind === "narration" && l.private && !$("#fPrivate").checked) return false;
  const who = $("#fWho").value;
  if (who && !(`${l.speaker_label || ""} ${(l.to || []).join(" ")} ${l.text}`.includes(who))) return false;
  const q = $("#fText").value.toLowerCase();
  if (q && !`${l.text} ${l.speaker_label || ""} ${l.id}`.toLowerCase().includes(q)) return false;
  if ($("#fFrom").value && l.round < +$("#fFrom").value) return false;
  if ($("#fTo").value && l.round > +$("#fTo").value) return false;
  return true;
}

function bubble(l) {
  if (l.kind === "narration") return `<div class="narr ${l.private ? "is-private" : ""}"><span class="mono">r${l.round}</span> ${l.private ? badge("private", "b-bad") : ""} ${esc(l.text)}</div>`;
  if (l.kind === "claim") return `<div class="msg public"><div class="who"><b>${esc(l.speaker_label)}</b> → the city ${badge(l.claim_kind === "rumor" ? "rumor" : "published claim", l.claim_kind === "rumor" ? "b-rumor" : "b-claim")}<span class="mono"> r${l.round} · ${l.id}</span></div><div class="said">${esc(l.text)}</div></div>`;
  const verb = ACT_LABEL[l.act] ?? "";
  const to = l.public ? "→ the city" : l.to.length ? "→ " + l.to.map(esc).join(", ") : "(to themself)";
  return `<div class="msg ${l.public ? "public" : "private"} act-${esc(l.act)}">
    <div class="who"><b>${esc(l.speaker_label)}</b> ${verb ? `<span class="muted">${verb}</span>` : ""} ${to}
      ${l.public ? "" : badge("private", "b-bad")} ${badge(l.source === "llm" ? "LLM" : "scripted", l.source === "llm" ? "b-ok" : "b-muted")}<span class="mono"> r${l.round} · ${l.id}</span></div>
    <div class="said">“${esc(l.text)}”</div></div>`;
}

function render(fromFilter, stick) {
  const shown = items.filter(keep);
  $("#count").textContent = `${shown.length} of ${items.length} shown`;
  const MAX = 1500;
  const slice = shown.slice(-MAX);
  let html = (shown.length > MAX ? `<p class="muted">Showing the latest ${MAX}. Narrow with filters or a round range to read earlier parts.</p>` : "")
    + slice.map(bubble).join("");
  if (verdict.length) html += `<section class="verdict"><h2>Verdict</h2>${verdict.map((v, i) => `<p class="${v.text.startsWith("•") ? "term" : ""}">${esc(v.text)}</p>`).join("")}</section>`;
  $("#chat").innerHTML = html || '<p class="muted">Nothing yet. Start the run in the Control Room.</p>';
  if (stick || (!fromFilter && verdict.length)) window.scrollTo(0, document.body.scrollHeight);
  $("#toBottom").hidden = atBottom();
}

init();
