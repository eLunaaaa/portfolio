/* 관리자 통신 기록을 자연어로 표시합니다. body나 인증정보는 서버 API에 포함되지 않습니다.
 * 5초마다 새 기록을 조회하며 브라우저 시간차를 보정해 30초 온라인 상태를 표시합니다. */
const logKinds = {health: "서버 상태 확인", heartbeat: "차량 연결 알림",
  evidence_upload: "사고 영상 업로드", evidence_status: "영상 저장 여부 확인", time_sync: "서버 시간 확인"};
let logLoading = false;
let logPollingEnabled = true;
let logRefreshPending = false;

function communicationTime(value) {
  // ISO 문자열 대신 한국 시간의 날짜·시·분·초·밀리초로 읽기 쉽게 표시합니다.
  if (!value) return "기록 없음";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시각 확인 불가";
  return new Intl.DateTimeFormat("ko-KR", {timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hour12: false}).format(date);
}

function renderCommunicationPeers(data) {
  const now = Date.parse(data.server_time);
  document.querySelector("#communication-peers").innerHTML = data.peers.length ? data.peers.map(peer => {
    const seconds = Math.max(0, Math.floor((now - Date.parse(peer.last_seen_at)) / 1000));
    const online = seconds <= data.online_window_seconds;
    return `<article class="communication-peer">
      <p class="eyebrow">${escapeHtml(peer.node_id || "미인증 연결")}</p>
      <h3>${escapeHtml(peer.vehicle_id || "차량 미확인")}</h3>
      <p>IP · ${escapeHtml(peer.client_ip || "확인 불가")}</p>
      <p>마지막 통신 · ${seconds}초 전</p>
      <p>서버 상태 확인 · ${escapeHtml(communicationTime(peer.last_health_at))}</p>
      <div class="peer-recent-logs"><h4>최신 요청</h4>${peer.recent_requests.map(row =>
        `<p>${escapeHtml(communicationTime(row.received_at))}<br>${escapeHtml(logKinds[row.kind])} · ${escapeHtml(row.result)}</p>`).join("")}</div>
      ${peer.vehicle_id ? `<button class="small-button" type="button" data-log-vehicle="${escapeHtml(peer.vehicle_id)}">이 차량 로그</button>` : ""}
      <span class="connection-indicator ${online ? "is-online" : "is-offline"}">
        <i aria-hidden="true"></i>${online ? "온라인" : "최근 통신 없음"}</span>
    </article>`;
  }).join("") : "<p>아직 받은 통신이 없습니다.</p>";
}

function renderCommunicationEntries(data) {
  document.querySelector("#log-count").textContent = `${data.retained_count}건 보관 · 조건에 맞는 ${data.matching_count}건 중 ${data.entries.length}건 표시`;
  document.querySelector("#communication-log-body").innerHTML = data.entries.length ? data.entries.map(row => {
    const failed = row.status_code >= 400 || !row.completed;
    const sender = row.authenticated ? `${row.vehicle_id} · ${row.node_id}` : "차량 미확인";
    const declared = !row.authenticated && row.declared_node_id ? `<small>주장한 노드 · ${escapeHtml(row.declared_node_id)} (미인증)</small>` : "";
    return `<tr><td>${escapeHtml(communicationTime(row.received_at))}</td>
      <td>${escapeHtml(sender)}<small>IP · ${escapeHtml(row.client_ip || "확인 불가")}</small>${declared}</td>
      <td>${escapeHtml(logKinds[row.kind] || "차량 통신")}${row.event_id ? `<small>사고 · ${escapeHtml(row.event_id)}</small>` : ""}</td>
      <td><span class="status-badge ${failed ? "offline" : "online"}">${escapeHtml(row.result)}</span>
        <small>응답 ${row.status_code}${row.authenticated ? " · 노드 인증됨" : " · 노드 미인증"}</small></td>
      <td>${row.duration_ms.toLocaleString("ko-KR")} ms</td></tr>`;
  }).join("") : '<tr><td colspan="5">조건에 맞는 요청이 없습니다.</td></tr>';
}

async function loadCommunicationLogs() {
  // 요청은 하나씩 보내고, 대기 중 바뀐 조건은 완료 직후 최신 값으로 조회합니다.
  if (!logPollingEnabled) return;
  if (logLoading) {
    logRefreshPending = true;
    return;
  }
  logLoading = true;
  try {
    const failuresOnly = document.querySelector("#failures-only").checked;
    const params = new URLSearchParams({limit: "200", failures_only: failuresOnly});
    const kind = document.querySelector("#log-kind").value;
    if (kind) params.set("kind", kind);
    const vehicle = document.querySelector("#log-vehicle").value;
    if (vehicle) params.set("vehicle_id", vehicle);
    const response = await fetch(`/api/v1/admin/communication-logs?${params}`, {credentials: "include", cache: "no-store"});
    if (response.status === 401 || response.status === 403) {
      logPollingEnabled = false;
      setMessage(document.querySelector("#admin-message"), "관리자 로그인이 필요합니다. 로그인 화면으로 이동합니다.", "error");
      window.location.assign("/login");
      return;
    }
    if (!response.ok) throw new Error(`통신 기록을 가져오지 못했습니다. 응답 ${response.status}`);
    const data = await response.json();
    const select = document.querySelector("#log-vehicle");
    if (select.value !== vehicle || document.querySelector("#log-kind").value !== kind
        || document.querySelector("#failures-only").checked !== failuresOnly) {
      logRefreshPending = true;
      return;
    }
    const vehicles = [...new Set(data.peers.map(peer => peer.vehicle_id).filter(Boolean))].sort();
    if (vehicle && !vehicles.includes(vehicle)) vehicles.push(vehicle);
    select.innerHTML = '<option value="">전체 차량</option>' + vehicles.map(id => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`).join("");
    select.value = vehicle;
    renderCommunicationPeers(data);
    renderCommunicationEntries(data);
    setMessage(document.querySelector("#admin-message"), `갱신 완료 · ${communicationTime(data.server_time)}`, "success");
  } catch (error) {
    setMessage(document.querySelector("#admin-message"), error.message, "error");
  } finally {
    logLoading = false;
    if (logRefreshPending) {
      logRefreshPending = false;
      await loadCommunicationLogs();
    }
  }
}
document.querySelector("#refresh-button").addEventListener("click", loadCommunicationLogs);
document.querySelector("#log-kind").addEventListener("change", loadCommunicationLogs);
document.querySelector("#log-vehicle").addEventListener("change", loadCommunicationLogs);
document.querySelector("#communication-peers").addEventListener("click", event => {
  const button = event.target.closest("[data-log-vehicle]");
  if (!button) return;
  document.querySelector("#log-vehicle").value = button.dataset.logVehicle;
  loadCommunicationLogs();
});
document.querySelector("#all-logs-button").addEventListener("click", () => {
  document.querySelector("#log-vehicle").value = "";
  document.querySelector("#log-kind").value = "";
  document.querySelector("#failures-only").checked = false;
  loadCommunicationLogs();
});
document.querySelector("#failures-only").addEventListener("change", loadCommunicationLogs);
document.querySelector("#logout-button").addEventListener("click", logoutAndRedirect);
loadCommunicationLogs();
setInterval(() => { if (!document.hidden) loadCommunicationLogs(); }, 5000);
