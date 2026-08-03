const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "—").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
}[char]));

async function call(path, url) {
  const response = await fetch(`${path}?url=${encodeURIComponent(url)}`);
  const json = await response.json();
  if (!response.ok) throw new Error(json.error || "처리 오류");
  return json;
}

function link(url) {
  const safe = esc(url);
  return `<a class="url-link" href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`;
}

function jsonLink(value) {
  return String(value).split(/(https?:\/\/[^\s"]+)/g).map((part, index) => {
    if (index % 2 === 1) {
      const safe = esc(part);
      return `<a class="url-link" href="${safe}" target="_blank" rel="noopener noreferrer">${safe}</a>`;
    }
    return esc(part);
  }).join("");
}
function recordCard(record) {
  return `<div class="record">
    <div class="recordhead">
      <b>#${record.id ?? "검사"} · 저장 Hash</b>
      ${record.id ? `<button class="del" data-id="${record.id}">삭제</button>` : ""}
    </div>
    <span>URL ${link(record.url)}</span>
    <span>Hash <code>${esc(record.hash)}</code> · 저장일 ${esc(record.saved_at)}</span>
  </div>`;
}

function debug(response) {
  return `<details class="debug">
    <summary>Response 보기</summary>
    <div class="debug-grid debug-grid-single">
      <section class="debug-card response">
        <h3>Response</h3>
        <pre>${jsonLink(JSON.stringify(response, null, 2))}</pre>
      </section>
    </div>
  </details>`;
}

function resultView(result, isStore) {
  const parsed = result.parsed;
  const status = result.duplicate
    ? `<strong class="dup">중복존재 — 저장하지 않았습니다.</strong><p>메모리 DB의 <code>hash</code> 컬럼에 동일한 값이 있습니다.</p>${result.matches.map(recordCard).join("")}`
    : isStore
      ? `<strong class="ok">메모리 DB 저장 완료</strong><p>동일한 <code>hash</code>가 없어 신규 저장이 가능합니다.</p>${recordCard(result.saved)}`
      : `<strong class="ok">중복 없음</strong><p>메모리 DB의 <code>hash</code> 컬럼에 동일한 값이 없습니다.</p>`;

  return `<div class="parsed">
    <b>파싱 URL</b>
    <span>${link(parsed.url)}</span>
    <span>생성 Hash <code>${esc(result.hash)}</code> · duplicate <code>${result.duplicate}</code> · save <code>${result.save}</code></span>
  </div>${status}${debug(result)}`;
}
async function load() {
  const response = await fetch("/api/records");
  const json = await response.json();
  $("records").innerHTML = json.records.length ? json.records.map(recordCard).join("") : "저장된 데이터가 없습니다.";
  document.querySelectorAll(".del").forEach((button) => {
    button.onclick = async () => {
      if (confirm("이 저장 데이터를 삭제할까요?")) {
        await fetch(`/api/delete?id=${button.dataset.id}`);
        load();
      }
    };
  });
}

$("storeBtn").onclick = async () => {
  try {
    const url = $("storeUrl").value;
    $("storeOut").textContent = "URL을 파싱하고 subject·content 기반 SimHash를 생성 중…";
    const result = await call("/api/store", url);
    $("storeOut").innerHTML = resultView(result, true);
    load();
  } catch (error) {
    $("storeOut").textContent = `오류: ${error.message}`;
  }
};

$("testBtn").onclick = async () => {
  try {
    const url = $("testUrl").value;
    $("testOut").textContent = "URL을 파싱하고 생성 Hash를 메모리 DB에서 exact 비교 중…";
    const result = await call("/api/check", url);
    $("testOut").innerHTML = resultView(result, false);
  } catch (error) {
    $("testOut").textContent = `오류: ${error.message}`;
  }
};

$("refresh").onclick = load;
$("clear").onclick = async () => {
  if (confirm("메모리 DB의 모든 저장 데이터를 삭제할까요?")) {
    await fetch("/api/clear");
    $("storeOut").textContent = "메모리 DB를 비웠습니다.";
    $("testOut").textContent = "";
    load();
  }
};

async function f1Test(action) {
  const payload = { action, db_name: $("f1Db").value, hash: $("f1Hash").value };
  $("f1Out").textContent = "F1 Dev 읽기 전용 DB 브리지 조회 중…";
  try {
    const response = await fetch("/api/f1-db/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const json = await response.json();
    if (!response.ok) throw new Error(json.error || "DB 브리지 오류");
    $("f1Out").innerHTML = `<strong class="ok">조회 완료</strong>${debug(json)}`;
  } catch (error) { $("f1Out").textContent = `오류: ${error.message}`; }
}
$("f1Connect").onclick = () => f1Test("connection");
$("f1HashCheck").onclick = () => f1Test("hash");
load();