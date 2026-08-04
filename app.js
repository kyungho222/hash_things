const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[char]));
async function call(path, url) { const response = await fetch(`${path}?url=${encodeURIComponent(url)}`); const data = await response.json(); if (!response.ok) throw new Error(data.error || "Request failed"); return data; }
function link(url) { const safe = esc(url || ""); return `<a class="url-link" href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`; }
function jsonLink(value) { return String(value).split(/(https?:\/\/[^\s"]+)/g).map((part, index) => index % 2 ? link(part) : esc(part)).join(""); }
function copyButton(value) { const encoded = btoa(unescape(encodeURIComponent(JSON.stringify(value, null, 2)))); return `<button type="button" class="copy-log" data-copy="${encoded}">Copy log</button>`; }
function payloadResponse(payload, response) { return `<details class="debug" open><summary>Payload ? Response log</summary><div class="debug-grid"><section class="debug-card payload"><h3>Payload</h3><pre>${jsonLink(JSON.stringify(payload, null, 2))}</pre></section><section class="debug-card response"><h3><span>Response</span>${copyButton(response)}</h3><pre>${jsonLink(JSON.stringify(response, null, 2))}</pre></section></div></details>`; }
function debug(response) { return `<details class="debug"><summary>Response</summary><div class="debug-grid debug-grid-single"><section class="debug-card response"><h3><span>Response</span>${copyButton(response)}</h3><pre>${jsonLink(JSON.stringify(response, null, 2))}</pre></section></div></details>`; }
document.addEventListener("click", async (event) => { const button = event.target.closest(".copy-log"); if (!button) return; const original = button.textContent; try { await navigator.clipboard.writeText(decodeURIComponent(escape(atob(button.dataset.copy)))); button.textContent = "Copied"; } catch (_) { button.textContent = "Copy failed"; } setTimeout(() => { button.textContent = original; }, 1600); });
function recordCard(record) { return `<div class="record"><div class="recordhead"><b>#${record.id ?? "-"} ? SimHash</b>${record.id ? `<button class="del" data-id="${record.id}">Delete</button>` : ""}</div><span>URL ${link(record.url)}</span><span>Hash <code>${esc(record.hash)}</code> ? saved ${esc(record.saved_at)}</span></div>`; }
function resultView(result, isStore) { const parsed = result.parsed || {}; const status = result.duplicate ? `<strong class="dup">Duplicate exists: not saved.</strong>${(result.matches || []).map(recordCard).join("")}` : isStore ? `<strong class="ok">Saved to memory DB</strong>${recordCard(result.saved || {})}` : `<strong class="ok">No duplicate</strong>`; return `<div class="parsed"><b>Parsed URL</b><span>${link(parsed.url)}</span><span>Hash <code>${esc(result.hash)}</code> ? duplicate <code>${result.duplicate}</code> ? save <code>${result.save}</code></span></div>${status}${debug(result)}`; }
async function load() { const response = await fetch("/api/records"); const data = await response.json(); $("records").innerHTML = data.records.length ? data.records.map(recordCard).join("") : "No saved records."; document.querySelectorAll(".del").forEach((button) => { button.onclick = async () => { if (confirm("Delete this record?")) { await fetch(`/api/delete?id=${button.dataset.id}`); load(); } }; }); }
async function publicAction(path, action) {
  try {
    const url = $("publicUrl").value;
    $("publicOut").textContent = `${action}...`;
    const result = await call(path, url);
    const status = result.skipped ? '<strong class="warn">Skipped</strong>' : '<strong class="ok">Done</strong>';
    const duplicate = Object.hasOwn(result, "duplicate") ? `<p>duplicate <code>${result.duplicate}</code> ? save <code>${result.save}</code></p>` : "";
    $("publicOut").innerHTML = `${status}<p>Final URL ${link(result.url || url)}</p><p>SimHash <code>${esc(result.simhash || result.hash)}</code> ? skipped <code>${result.skipped ?? false}</code></p>${duplicate}${payloadResponse({ url, action }, result)}`;
    if (path === "/api/public-store") load();
  } catch (error) { $("publicOut").textContent = `Error: ${error.message}`; }
}
$("publicBtn").onclick = () => publicAction("/api/public-store", "Parsing, checking duplicate, and saving");
$("refresh").onclick = load;
$("clear").onclick = async () => { if (confirm("Clear all memory DB records?")) { await fetch("/api/clear"); $("publicOut").textContent = "Memory DB cleared."; load(); } };
async function f1Test(action) { const payload = { action, db_name: $("f1Db").value, hash: $("f1Hash").value }; $("f1Out").textContent = "Checking F1 Dev DB bridge..."; try { const response = await fetch("/api/f1-db/test", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) }); const data = await response.json(); if (!response.ok) throw new Error(data.error || "DB bridge error"); $("f1Out").innerHTML = `<strong class="ok">Done</strong>${debug(data)}`; } catch (error) { $("f1Out").textContent = `Error: ${error.message}`; } }
$("f1Connect").onclick = () => f1Test("connection"); $("f1HashCheck").onclick = () => f1Test("hash"); load();
