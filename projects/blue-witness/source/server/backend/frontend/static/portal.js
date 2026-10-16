const userName =
  document.querySelector("#user-name");

const vehicleCount =
  document.querySelector("#vehicle-count");

const eventCount =
  document.querySelector("#event-count");

const vehicleList =
  document.querySelector("#vehicle-list");

// 현재 화면의 차량 정보는 카드 버튼에서 ID로 다시 찾아 사용함.
let currentPortalVehicles = [];

// 조회는 이미 권한 확인된 목록 안에서 수행하며 최근 사고 카드에는 적용하지 않음.
let currentPortalEvents = [];
let visibleIncidentCount = 5;
let incidentHistoryQuery = { start: "", end: "", sort: "newest", vehicle: "" };

function selectIncidentHistory(events, query) {
  const dayFormat = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
  });
  return events.filter((event) => {
    if (query.vehicle && event.accident_vehicle_id !== query.vehicle) return false;
    const date = new Date(event.event_time_kst);
    if (Number.isNaN(date.getTime())) return !query.start && !query.end;
    const parts = Object.fromEntries(dayFormat.formatToParts(date).map((part) => [part.type, part.value]));
    const day = `${parts.year}-${parts.month}-${parts.day}`;
    return (!query.start || day >= query.start) && (!query.end || day <= query.end);
  }).sort((a, b) => {
    const difference = (Date.parse(a.event_time_kst) || 0) - (Date.parse(b.event_time_kst) || 0);
    return query.sort === "oldest" ? difference : -difference;
  });
}

const eventTableBody =
  document.querySelector("#event-table-body");

let viewedEventIds = new Set();
let viewedEventsStorageKey = "";

// 계정별로 브라우저에 상세 확인 상태를 보관함.
function loadViewedEvents(username) {
  viewedEventsStorageKey = `viewed-events:${username}`;
  try {
    const saved = JSON.parse(localStorage.getItem(viewedEventsStorageKey) || "[]");
    viewedEventIds = new Set(Array.isArray(saved) ? saved : []);
  } catch { viewedEventIds = new Set(); }
}

function unreadEventBadge(eventId) {
  return viewedEventIds.has(eventId) ? "" :
    `<span class="unread-incident-badge" data-unread-event="${escapeHtml(eventId)}" aria-label="미확인 사고" title="미확인 사고">!</span>`;
}

function markEventViewed(eventId) {
  viewedEventIds.add(eventId);
  try { localStorage.setItem(viewedEventsStorageKey, JSON.stringify([...viewedEventIds])); }
  catch { /* 브라우저 저장 제한 시 현재 화면에서만 유지함. */ }
  document.querySelectorAll("[data-unread-event]").forEach((badge) => {
    if (badge.dataset.unreadEvent === eventId) badge.remove();
  });
}

// 최근 사고는 내부 식별자 대신 소유 차량 이름과 한국 시각으로 안내함.
function latestIncidentTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "발생 시각 확인 중";
  const options = { timeZone: "Asia/Seoul" };
  const parts = Object.fromEntries(new Intl.DateTimeFormat("ko-KR", {
    ...options, year: "numeric", month: "numeric", day: "numeric",
    hour: "numeric", minute: "numeric", second: "numeric", hour12: true,
  }).formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.year}년 ${Number(parts.month)}월 ${Number(parts.day)}일 · ${parts.dayPeriod} ${Number(parts.hour)}시 ${Number(parts.minute)}분 ${Number(parts.second)}초`;
}

function renderLatestIncident(events) {
  const container = document.querySelector("#latest-incident-content");
  if (!events.length) {
    container.innerHTML = "<p>아직 등록된 사고가 없습니다.</p>";
    return;
  }
  const latest = [...events].sort((a, b) =>
    Date.parse(b.event_time_kst) - Date.parse(a.event_time_kst))[0];
  const vehicle = currentPortalVehicles.find((item) =>
    item.vehicle_id === latest.accident_vehicle_id);
  const vehicleName = vehicle?.display_name?.trim()
    || vehicle?.license_plate?.trim() || "등록 차량";
  const statusLabel = {
    MERGED: "병합 완료", READY_TO_MERGE: "병합 대기",
    COLLECTING: "영상 수집 중", REGISTERED: "사고 접수",
  }[latest.merge_status] || "처리 중";
  // 영상 개수를 재사용하지 않고 차량 ID를 중복 제거하여 제공 차량 수를 셈.
  const providerCount = latest.merged_evidence_count;
  container.innerHTML = `
    <div class="latest-incident-card">
      <div class="latest-incident-copy">
        <strong>${unreadEventBadge(latest.event_id)} ${escapeHtml(vehicleName)} 차량 사고</strong>
        <p>${escapeHtml(latestIncidentTime(latest.event_time_kst))}</p>
        <span class="latest-incident-count">${providerCount == null ? "병합 영상 수 미확인" : `차량 ${providerCount}대 영상 병합`} ·</span>
        <span class="status-badge">${escapeHtml(statusLabel)}</span>
      </div>
      <button type="button" class="primary-button" data-event-id="${escapeHtml(latest.event_id)}">사고 확인</button>
    </div>`;
  const button = container.querySelector("[data-event-id]");
  button.addEventListener("click", () => selectEvent(latest.event_id, button));
}

const detailPanel =
  document.querySelector(
    "#event-detail-panel",
  );

// 상세 정보는 읽기 쉬운 앞면과 원본 값을 보존한 뒷면으로 구분함.
function preciseIncidentTimeMarkup(value) {
  const parts = String(value).match(/^(\d{4}-\d{2}-\d{2})T(.+)$/);
  return parts
    ? `<span class="detail-precise-date">${escapeHtml(parts[1])}</span><br><span class="detail-precise-clock">${escapeHtml(parts[2])}</span>`
    : escapeHtml(value);
}

function setDetailFlip(id, friendly, raw, isTime = false) {
  const button = document.getElementById(id);
  button.classList.remove("is-flipped");
  button.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-label", `${friendly}. 눌러 상세 값 확인`);
  button.dataset.friendlyLabel = friendly;
  button.dataset.rawLabel = raw;
  const front = button.querySelector(".detail-flip-front");
  const back = button.querySelector(".detail-flip-back");
  front.textContent = friendly;
  back.innerHTML = isTime ? preciseIncidentTimeMarkup(raw) : escapeHtml(raw);
  front.setAttribute("aria-hidden", "false");
  back.setAttribute("aria-hidden", "true");
}

detailPanel.querySelectorAll("[data-detail-flip-card]").forEach((card) => {
  card.addEventListener("click", () => {
    const button = card.querySelector(".detail-flip-button");
    const flipped = button.classList.toggle("is-flipped");
    button.setAttribute("aria-pressed", String(flipped));
    button.querySelector(".detail-flip-front").setAttribute("aria-hidden", String(flipped));
    button.querySelector(".detail-flip-back").setAttribute("aria-hidden", String(!flipped));
    button.setAttribute("aria-label", flipped
      ? `${button.dataset.rawLabel}. 눌러 기본 정보 보기`
      : `${button.dataset.friendlyLabel}. 눌러 상세 값 확인`);
  });
});

const portalMessage =
  document.querySelector("#portal-message");

const unlockArea =
  document.querySelector("#unlock-area");

const videoArea =
  document.querySelector("#video-area");

const mergedVideo =
  document.querySelector("#merged-video");

const downloadMergedVideoButton =
  document.querySelector(
    "#download-merged-video-button",
  );

const accessCodeForm =
  document.querySelector(
    "#access-code-form",
  );

const accessCodeInput =
  document.getElementById(
    "access-code-input",
  )
  ?? document.getElementById(
    "access-code",
  );

const logoutButton =
  document.querySelector(
    "#logout-button",
  );

const openAccessCodeModalButton =
  document.getElementById(
    "open-access-code-modal-button",
  );

const accessCodeModal =
  document.getElementById(
    "access-code-modal",
  );

const closeAccessCodeModalButton =
  document.getElementById(
    "close-access-code-modal-button",
  );

const cancelIdentityVerificationButton =
  document.getElementById(
    "cancel-identity-verification-button",
  );

const confirmIdentityVerificationButton =
  document.getElementById(
    "confirm-identity-verification-button",
  );

const identityVerificationStage =
  document.getElementById(
    "identity-verification-stage",
  );

const issuedCodeStage =
  document.getElementById(
    "issued-code-stage",
  );

const popupAccessCode =
  document.getElementById(
    "popup-access-code",
  );

const popupAccessCodeCountdown =
  document.getElementById(
    "popup-access-code-countdown",
  );

const popupAccessCodeStatus =
  document.getElementById(
    "popup-access-code-status",
  );

const copyPopupAccessCodeButton =
  document.getElementById(
    "copy-popup-access-code-button",
  );


/*
 * 사용자가 현재 선택한 사고 ID임.
 */
let selectedEventId = null;


/*
 * 서버가 발급한 접근 코드를
 * 현재 페이지 메모리에 임시 보관함.
 */
let issuedAccessCodeInMemory = "";


/*
 * 실행 중인 카운트다운 타이머 ID임.
 */
let accessCodeCountdownTimer = null;


/*
 * 접근 코드가 만료되는 절대 시각임.
 * JavaScript의 밀리초 Timestamp를 사용함.
 */
let accessCodeExpiresAtMilliseconds = 0;


/*
 * FastAPI의 다양한 오류 응답을
 * 사용자가 읽을 수 있는 문자열로 변환함.
 */
function getApiErrorMessage(
  result,
  fallbackMessage =
    "요청 처리 중 오류가 발생했습니다.",
) {
  if (!result) {
    return fallbackMessage;
  }

  const detail = result.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages =
      detail.map((item) => {
        if (
          item
          && typeof item.msg === "string"
        ) {
          const location =
            Array.isArray(item.loc)
              ? item.loc.join(" → ")
              : "";

          if (location) {
            return (
              `${location}: ${item.msg}`
            );
          }

          return item.msg;
        }

        try {
          return JSON.stringify(item);
        } catch {
          return String(item);
        }
      });

    return messages.join("\n");
  }

  if (
    detail
    && typeof detail === "object"
  ) {
    if (
      typeof detail.message === "string"
    ) {
      return detail.message;
    }

    try {
      return JSON.stringify(
        detail,
        null,
        2,
      );
    } catch {
      return fallbackMessage;
    }
  }

  if (
    typeof result.message === "string"
  ) {
    return result.message;
  }

  return fallbackMessage;
}


/*
 * JavaScript Error 또는 일반 객체에서
 * 오류 문자열을 가져옴.
 */
function getErrorMessage(error) {
  if (
    error === null
    || error === undefined
  ) {
    return "알 수 없는 오류가 발생했습니다.";
  }

  if (typeof error === "string") {
    return error;
  }

  if (
    error instanceof Error
    && error.message
  ) {
    return error.message;
  }

  return getApiErrorMessage(
    error,
    "알 수 없는 오류가 발생했습니다.",
  );
}
/*
 * 현대차 사진 키와 실제 정적 이미지 경로를 연결함.
 *
 * 서버에서 전달된 문자열을 직접 파일 경로에
 * 넣지 않고 허용된 목록에서만 찾음.
 */
const VEHICLE_PHOTO_PATHS =
  Object.freeze({
    hyundai_solati:
      "/static/images/vehicles/hyundai/solati.png",

    hyundai_porter_two:
      "/static/images/vehicles/hyundai/porter2.png",

    hyundai_nexo:
      "/static/images/vehicles/hyundai/nexo.png",

    hyundai_ioniq_five:
      "/static/images/vehicles/hyundai/ioniq5.png",

    hyundai_staria:
      "/static/images/vehicles/hyundai/staria.png",

    hyundai_palisade:
      "/static/images/vehicles/hyundai/palisade.png",

    hyundai_santafe:
      "/static/images/vehicles/hyundai/santafe.png",

    hyundai_tucson:
      "/static/images/vehicles/hyundai/tucson.png",

    hyundai_kona_electric:
      "/static/images/vehicles/hyundai/kona-electric.png",

    hyundai_kona:
      "/static/images/vehicles/hyundai/kona.png",

    hyundai_venue:
      "/static/images/vehicles/hyundai/venue.png",

    hyundai_casper:
      "/static/images/vehicles/hyundai/casper.png",

    hyundai_ioniq_six:
      "/static/images/vehicles/hyundai/ioniq6.png",

    hyundai_grandeur:
      "/static/images/vehicles/hyundai/grandeur.png",

    hyundai_sonata:
      "/static/images/vehicles/hyundai/sonata.png",

    hyundai_avante:
      "/static/images/vehicles/hyundai/avante.png",
  });


/*
 * 기존 일반 아이콘 키를 이모지로 변환함.
 */
function getVehicleIcon(
  iconKey,
) {
  const icons = {
    sedan: "🚗",
    suv: "🚙",
    van: "🚐",
    pickup: "🛻",
    truck: "🚚",
  };

  return icons[iconKey] || "🚗";
}


/*
 * 차량의 icon_key를 확인하여
 * 사진 또는 일반 이모지 HTML을 생성함.
 */
function renderVehicleVisual(
  vehicle,
) {
  const photoPath =
    VEHICLE_PHOTO_PATHS[
      vehicle.icon_key
    ];

  if (photoPath) {
    return `
      <img
        class="vehicle-card-photo"
        src="${photoPath}"
        alt="${
          escapeHtml(
            vehicle.vehicle_model
            || "등록 차량",
          )
        }"
        loading="lazy"
      >
    `;
  }

  return `
    <span
      class="vehicle-card-emoji"
      aria-hidden="true"
    >
      ${
        getVehicleIcon(
          vehicle.icon_key,
        )
      }
    </span>
  `;
}


/*
 * 사용자에게 등록된 차량 목록을 표시함.
 */
function renderVehicles(vehicles) {
  currentPortalVehicles = vehicles;
  if (incidentHistoryQuery.vehicle && !vehicles.some((vehicle) => vehicle.vehicle_id === incidentHistoryQuery.vehicle)) {
    incidentHistoryQuery.vehicle = "";
    visibleIncidentCount = 5;
  }
  if (vehicles.length === 0) {
    vehicleList.innerHTML = `
      <div class="empty-vehicle-state">
        <span class="empty-vehicle-icon">
          🚘
        </span>

        <h3>
          등록된 차량이 없습니다
        </h3>

        <p>
          오른쪽 위의 차량 등록 버튼을 눌러
          첫 차량을 등록해주세요.
        </p>
      </div>
    `;

    return;
  }

  vehicleList.innerHTML =
    vehicles
      .map(
        (vehicle) => `
          <article
            class="
              item-card
              vehicle-item-card
            "
            data-history-vehicle="${escapeHtml(vehicle.vehicle_id)}"
            tabindex="0"
            aria-label="${escapeHtml(vehicle.display_name || vehicle.license_plate)} 사고 기록 선택"
          >
            <div class="vehicle-card-main">
            <div class="vehicle-card-icon">
              ${
                renderVehicleVisual(
                  vehicle,
                )
              }
            </div>

            <div class="vehicle-card-content">
              <span class="eyebrow">
                ${
                  escapeHtml(
                    vehicle.vehicle_id,
                  )
                }
              </span>

              <h3>
                ${
                  escapeHtml(
                    vehicle.license_plate,
                  )
                }
              </h3>

              <p class="vehicle-model-text">
                ${
                  escapeHtml(
                    vehicle.vehicle_model
                    || "차종 미등록",
                  )
                }
              </p>

              <button class="vehicle-display-name vehicle-nickname-button"
                type="button" title="클릭하여 별명 수정"
                data-vehicle-action="nickname"
                data-vehicle-id="${escapeHtml(vehicle.vehicle_id)}">${escapeHtml(vehicle.display_name || "내 차량")}</button>
            </div>
            </div>
            <div class="vehicle-card-actions">
              <button class="secondary-button" type="button"
                data-vehicle-action="edit"
                data-vehicle-id="${escapeHtml(vehicle.vehicle_id)}">차량 수정</button>
              <button class="danger-button outline" type="button"
                data-vehicle-action="delete"
                data-vehicle-id="${escapeHtml(vehicle.vehicle_id)}">삭제</button>
            </div>
          </article>
        `,
      )
      .join("");
  updateVehicleHistorySelection();
}

// 수정·삭제·별명 편집과 구분하여 카드의 빈 영역/이미지를 선택 동작으로 사용함.
function updateVehicleHistorySelection() {
  vehicleList.querySelectorAll("[data-history-vehicle]").forEach((card) => {
    const selected = card.dataset.historyVehicle === incidentHistoryQuery.vehicle;
    card.classList.toggle("is-history-selected", selected);
    const vehicle = currentPortalVehicles.find((item) => item.vehicle_id === card.dataset.historyVehicle);
    const name = vehicle?.display_name || vehicle?.license_plate || card.dataset.historyVehicle;
    card.setAttribute("aria-label", `${name} 사고 기록 ${selected ? "선택됨, 다시 눌러 해제" : "선택"}`);
  });
}

function toggleVehicleHistory(vehicleId) {
  incidentHistoryQuery.vehicle = incidentHistoryQuery.vehicle === vehicleId ? "" : vehicleId;
  visibleIncidentCount = 5;
  updateVehicleHistorySelection();
  renderEvents(currentPortalEvents);
}

vehicleList.addEventListener("click", (event) => {
  if (event.target.closest("button, input, select, textarea, a")) return;
  const card = event.target.closest("[data-history-vehicle]");
  if (card) toggleVehicleHistory(card.dataset.historyVehicle);
});
vehicleList.addEventListener("keydown", (event) => {
  if (event.target.matches("[data-history-vehicle]") && ["Enter", " "].includes(event.key)) {
    event.preventDefault();
    toggleVehicleHistory(event.target.dataset.historyVehicle);
  }
});

/*
 * 사용자의 사고 목록을 테이블에 표시함.
 */
function renderEvents(events) {
  currentPortalEvents = events;
  renderLatestIncident(events);
  events = selectIncidentHistory(events, incidentHistoryQuery);
  const showId = document.querySelector("#incident-show-id").checked;
  const detailedTime = document.querySelector("#incident-detailed-time").checked;
  document.querySelector("#incident-id-heading").hidden = !showId;
  const shown = Math.min(visibleIncidentCount, events.length);
  const selectedCar = currentPortalVehicles.find((vehicle) => vehicle.vehicle_id === incidentHistoryQuery.vehicle);
  const scopeLabel = incidentHistoryQuery.vehicle
    ? (selectedCar?.display_name || selectedCar?.license_plate || incidentHistoryQuery.vehicle)
    : "전체 차량";
  document.querySelector("#incident-history-summary").textContent = `${scopeLabel} · 조회 결과 ${events.length}건 · ${shown}건 표시`;
  document.querySelector("#incident-show-more").hidden = shown >= events.length;
  document.querySelector("#incident-collapse").hidden = visibleIncidentCount <= 5 || !events.length;
  if (events.length === 0) {
    eventTableBody.innerHTML = `
      <tr>
        <td colspan="${showId ? 6 : 5}">
          조회 조건에 맞는 사고 기록이 없습니다.
        </td>
      </tr>
    `;

    return;
  }

  eventTableBody.innerHTML =
    events.slice(0, visibleIncidentCount)
      .map(
        (event) => `
          <tr class="event-record-row">
            <td data-label="사고 ID" ${showId ? "" : "hidden"}>
              ${
                escapeHtml(
                  event.event_id,
                )
              }
            </td>

            <td data-label="사고 차량">
              ${unreadEventBadge(event.event_id)}
              ${
                escapeHtml(
                  event.accident_vehicle_id,
                )
              }
            </td>

            <td data-label="발생 시각">
              ${
                escapeHtml(
                  detailedTime ? event.event_time_kst : latestIncidentTime(event.event_time_kst),
                )
              }
            </td>

            <td data-label="증거 차량">
              ${event.merged_evidence_count ?? "미확인"}
            </td>

            <td data-label="병합 상태">
              <span class="status-badge">
                ${
                  escapeHtml(
                    event.merge_status,
                  )
                }
              </span>
            </td>

            <td data-label="사고 확인">
              <button
                class="small-button"
                data-event-id="${
                  escapeHtml(
                    event.event_id,
                  )
                }"
                type="button"
              >
                확인
              </button>
            </td>
          </tr>
        `,
      )
      .join("");

  document
    .querySelectorAll(
      "#event-table-body [data-event-id]",
    )
    .forEach((button) => {
      button.addEventListener(
        "click",
        () => {
          selectEvent(
            button.dataset.eventId,
            button,
          );
        },
      );
    });
}


/*
 * 로그인 사용자의 이름, 차량, 사고 목록을
 * 서버에서 조회함.
 */
async function loadOverview() {
  try {
    const overview =
      await apiRequest(
        "/api/v1/portal/overview",
      );

    userName.textContent =
      overview.full_name
      || overview.username;

    loadViewedEvents(overview.username);

    vehicleCount.textContent =
      overview.vehicles.length;

    eventCount.textContent =
      overview.events.length;

    renderVehicles(
      overview.vehicles,
    );

    renderEvents(
      overview.events,
    );
    return true;
  } catch (error) {
    const message =
      getErrorMessage(error);

    if (
      message.includes("인증")
      || message.includes("로그인")
      || message.includes("세션")
    ) {
      window.location.replace(
        `/login?expired=${Date.now()}`,
      );

      return false;
    }

    setMessage(
      portalMessage,
      message,
      "error",
    );
    return false;
  }
}


/*
 * 초 단위 시간을 MM:SS 형식으로 변환함.
 *
 * 예:
 * 300 → 05:00
 * 299 → 04:59
 * 67  → 01:07
 */
function formatCountdown(
  totalSeconds,
) {
  const normalizedSeconds =
    Math.max(
      0,
      Math.floor(
        Number(totalSeconds) || 0,
      ),
    );

  const minutes =
    Math.floor(
      normalizedSeconds / 60,
    );

  const seconds =
    normalizedSeconds % 60;

  return (
    `${String(minutes).padStart(2, "0")}:`
    + `${String(seconds).padStart(2, "0")}`
  );
}


/*
 * 현재 실행 중인 카운트다운을 종료함.
 */
function stopAccessCodeCountdown() {
  if (
    accessCodeCountdownTimer !== null
  ) {
    window.clearInterval(
      accessCodeCountdownTimer,
    );

    accessCodeCountdownTimer = null;
  }
}


/*
 * 서버가 반환한 유효시간을 기준으로
 * 접근 코드 카운트다운을 시작함.
 */
function startAccessCodeCountdown(
  validForSeconds,
) {
  stopAccessCodeCountdown();

  const parsedSeconds =
    Number(validForSeconds);

  const durationSeconds =
    Number.isFinite(parsedSeconds)
    && parsedSeconds > 0
      ? Math.floor(parsedSeconds)
      : 300;

  /*
   * 단순히 1씩 감소시키지 않고
   * 만료 시각과 현재 시각의 차이를 사용함.
   *
   * 브라우저 탭이 잠시 느려져도
   * 누적 오차가 생기지 않음.
   */
  accessCodeExpiresAtMilliseconds =
    Date.now()
    + durationSeconds * 1000;

  function updateCountdown() {
    const remainingMilliseconds =
      accessCodeExpiresAtMilliseconds
      - Date.now();

    const remainingSeconds =
      Math.max(
        0,
        Math.ceil(
          remainingMilliseconds / 1000,
        ),
      );

    popupAccessCodeCountdown.textContent =
      formatCountdown(
        remainingSeconds,
      );

    if (remainingSeconds <= 0) {
      stopAccessCodeCountdown();

      issuedAccessCodeInMemory = "";

      popupAccessCode.textContent =
        "만료됨";

      popupAccessCodeCountdown.textContent =
        "00:00";

      popupAccessCodeStatus.textContent =
        "접근 코드가 만료되었습니다. "
        + "팝업을 닫고 다시 발급해주세요.";

      copyPopupAccessCodeButton.disabled =
        true;

      copyPopupAccessCodeButton.textContent =
        "코드 만료";
    }
  }

  /*
   * 발급 직후 바로 05:00을 표시함.
   */
  updateCountdown();

  /*
   * 이후 1초마다 남은 시간을 갱신함.
   */
  accessCodeCountdownTimer =
    window.setInterval(
      updateCountdown,
      1000,
    );
}


/*
 * 접근 코드 팝업을 발급 전 상태로 초기화함.
 */
function resetAccessCodeModal() {
  stopAccessCodeCountdown();

  issuedAccessCodeInMemory = "";

  accessCodeExpiresAtMilliseconds = 0;

  popupAccessCode.textContent = "-";

  popupAccessCodeCountdown.textContent =
    "05:00";

  popupAccessCodeStatus.textContent =
    "코드를 복사한 후 X 버튼으로 "
    + "닫을 수 있습니다.";

  copyPopupAccessCodeButton.disabled =
    false;

  copyPopupAccessCodeButton.textContent =
    "접근 코드 복사";

  confirmIdentityVerificationButton
    .disabled = false;

  confirmIdentityVerificationButton
    .textContent =
    "본인인증 완료로 가정하고 확인";

  identityVerificationStage.hidden =
    false;

  issuedCodeStage.hidden =
    true;
}


/*
 * 접근 코드 발급 팝업을 표시함.
 */
function openAccessCodeModal() {
  if (!selectedEventId) {
    window.alert(
      "먼저 확인할 사고 기록을 선택해주세요.",
    );

    return;
  }

  resetAccessCodeModal();

  accessCodeModal.hidden = false;

  document.body.classList.add(
    "modal-open",
  );

  confirmIdentityVerificationButton
    .focus();
}



/*
 * 접근 코드 발급 팝업을 닫음.
 */
function closeAccessCodeModal() {
  stopAccessCodeCountdown();

  issuedAccessCodeInMemory = "";

  accessCodeExpiresAtMilliseconds = 0;

  popupAccessCode.textContent = "-";

  accessCodeModal.hidden = true;

  document.body.classList.remove(
    "modal-open",
  );

  openAccessCodeModalButton.disabled =
    false;

  openAccessCodeModalButton.textContent =
    "접근 코드 확인";
}
/*
 * 현재 재생 중인 영상과 다운로드 링크를
 * 모두 초기화함.
 *
 * 사용자가 다른 사고를 선택했을 때
 * 이전 사고 영상 URL이 남는 것을 방지함.
 */
function resetMergedVideoAccess() {
  mergedVideo.pause();

  mergedVideo.removeAttribute(
    "src",
  );

  mergedVideo.load();

  downloadMergedVideoButton
    .removeAttribute("href");

  downloadMergedVideoButton
    .setAttribute(
      "aria-disabled",
      "true",
    );

  downloadMergedVideoButton
    .classList.add(
      "is-disabled",
    );
}


/*
 * 현재 선택한 사고의 다운로드 주소를
 * 버튼에 설정함.
 *
 * 실제 권한 검사는 프론트엔드가 아니라
 * 다운로드 API가 다시 수행함.
 */
function enableMergedVideoDownload() {
  if (!selectedEventId) {
    resetMergedVideoAccess();
    return;
  }

  const encodedEventId =
    encodeURIComponent(
      selectedEventId,
    );

  downloadMergedVideoButton.href =
    `/api/v1/portal/events/${
      encodedEventId
    }/video/download`;

  downloadMergedVideoButton.download =
    `${selectedEventId}_merged.mp4`;

  downloadMergedVideoButton
    .setAttribute(
      "aria-disabled",
      "false",
    );

  downloadMergedVideoButton
    .classList.remove(
      "is-disabled",
    );
}

/*
 * 이전에 발급된 사고 접근 세션을 제거함.
 *
 * 사용자 로그인 쿠키인 user_session은
 * 삭제하지 않음.
 *
 * 사고 접근용 event_access_session 쿠키만 삭제하므로
 * 로그인 상태는 유지되고 영상만 다시 잠김.
 */
async function clearAccidentAccessSession() {
  const response = await fetch(
    "/api/v1/access/logout",
    {
      method: "POST",
      credentials: "same-origin",
    },
  );

  /*
   * 204는 정상적으로 쿠키가 삭제되었다는 의미임.
   */
  if (
    !response.ok
    && response.status !== 204
  ) {
    throw new Error(
      "기존 사고 접근 인증을 초기화하지 못했습니다.",
    );
  }
}
/*
 * 선택한 사고의 상세 정보를 표시함.
 *
 * 사고를 선택할 때 이전 사고 접근 세션을
 * 먼저 삭제하므로 본인인증 절차부터 다시 시작함.
 */
async function selectEvent(eventId, sourceButton) {
  selectedEventId = eventId;

  closeAccessCodeModal();

  /*
   * 이전 사고의 영상과 다운로드 주소를 제거함.
   */
  resetMergedVideoAccess();

  accessCodeInput.value = "";

  /*
   * 상세 정보 조회가 끝나기 전까지
   * 영상과 접근 코드 영역을 모두 숨김.
   */
  unlockArea.classList.add(
    "hidden",
  );

  videoArea.classList.add(
    "hidden",
  );

  setMessage(
    portalMessage,
    "사고 정보를 확인하는 중...",
  );

  try {
    /*
     * 중요:
     * 이전에 발급된 event_access_session 쿠키를 삭제함.
     *
     * 이 호출이 없으면 이전에 잠금 해제한 영상이
     * 다시 사고를 선택했을 때 바로 표시됨.
     */
    await clearAccidentAccessSession();

    /*
     * 기존 사고 인증을 삭제한 다음
     * 상세 정보와 새로운 잠금 상태를 조회함.
     */
    const [detail, accessStatus] =
      await Promise.all([
        apiRequest(
          `/api/v1/portal/events/${
            encodeURIComponent(eventId)
          }`,
        ),

        apiRequest(
          `/api/v1/portal/events/${
            encodeURIComponent(eventId)
          }/access-status`,
        ),
      ]);

    detailPanel.classList.remove(
      "hidden",
    );

    document.querySelector(
      "#detail-event-id",
    ).textContent =
      detail.event_id;

    const accidentVehicle = currentPortalVehicles.find((vehicle) => vehicle.vehicle_id === detail.accident_vehicle_id);
    setDetailFlip("detail-vehicle-flip",
      accidentVehicle?.display_name?.trim() || accidentVehicle?.license_plate || "등록 차량",
      detail.accident_vehicle_id);
    setDetailFlip("detail-time-flip",
      latestIncidentTime(detail.event_time_kst).replace(" · ", "\n"),
      detail.event_time_kst, true);

    document.querySelector(
      "#detail-evidence-count",
    ).textContent =
      detail.merged_evidence_count ?? "미확인";

    document.querySelector(
      "#detail-status",
    ).textContent =
      detail.merge_status;

    /*
     * clearAccidentAccessSession()이 기존 인증을
     * 제거했으므로 accessStatus.unlocked는
     * false여야 함.
     *
     * updateVideoAccess()는 영상을 숨기고
     * 본인인증·접근 코드 영역을 표시함.
     */
    updateVideoAccess(
      accessStatus,
    );

    window.openEvidenceViewer?.("event-detail-panel", sourceButton);
    markEventViewed(eventId);

  } catch (error) {
    resetMergedVideoAccess();

    unlockArea.classList.add(
      "hidden",
    );

    videoArea.classList.add(
      "hidden",
    );

    setMessage(
      portalMessage,
      getErrorMessage(error),
      "error",
    );
  }
}


/*
 * 시연용 본인인증을 완료한 것으로 가정하고
 * 서버에 5분 접근 코드 발급을 요청함.
 */
async function confirmMockIdentityVerification() {
  if (!selectedEventId) {
    window.alert(
      "선택된 사고가 없습니다.",
    );

    closeAccessCodeModal();

    return;
  }

  confirmIdentityVerificationButton
    .disabled = true;

  confirmIdentityVerificationButton
    .textContent = "확인 중...";

  try {
    const requestUrl =
      `/api/v1/portal/events/${
        encodeURIComponent(
          selectedEventId,
        )
      }/access-code`;

    console.log(
      "[ACCESS CODE] 요청 주소:",
      requestUrl,
    );

    /*
     * 현재 로그인 사용자는
     * user_session 쿠키로 확인함.
     *
     * 별도의 사용자 정보나 password_hash를
     * 요청 Body로 전송하지 않음.
     */
    const response =
      await fetch(
        requestUrl,
        {
          method: "POST",
          credentials: "same-origin",
        },
      );

    let result;

    try {
      result = await response.json();
    } catch {
      result = {
        detail:
          "서버 응답을 JSON으로 읽을 수 없습니다.",
      };
    }

    console.log(
      "[ACCESS CODE] HTTP 상태:",
      response.status,
    );

    console.log(
      "[ACCESS CODE] 응답:",
      result,
    );

    if (!response.ok) {
      throw new Error(
        getApiErrorMessage(
          result,
          "접근 코드 발급에 실패했습니다.",
        ),
      );
    }

    if (
      !result.access_code
      || typeof result.access_code
        !== "string"
    ) {
      throw new Error(
        "서버 응답에 access_code가 없습니다.",
      );
    }

    issuedAccessCodeInMemory =
      result.access_code;

    popupAccessCode.textContent =
      result.access_code;

    identityVerificationStage.hidden =
      true;

    issuedCodeStage.hidden =
      false;

    copyPopupAccessCodeButton.disabled =
      false;

    copyPopupAccessCodeButton.textContent =
      "접근 코드 복사";

    const validForSeconds =
      Number(
        result.valid_for_seconds
        ?? 300,
      );

    startAccessCodeCountdown(
      validForSeconds,
    );

    copyPopupAccessCodeButton.focus();
  } catch (error) {
    console.error(
      "[ACCESS CODE] 발급 실패:",
      error,
    );

    window.alert(
      getErrorMessage(error),
    );
  } finally {
    confirmIdentityVerificationButton
      .disabled = false;

    confirmIdentityVerificationButton
      .textContent =
      "본인인증 완료로 가정하고 확인";
  }
}


/*
 * Clipboard API를 사용할 수 없는 HTTP 주소에서도
 * 임시 textarea를 이용해 텍스트 복사를 시도함.
 *
 * 0.0.0.0이나 일부 LAN HTTP 주소는 브라우저가
 * secure context로 인정하지 않아
 * navigator.clipboard가 없거나 거부될 수 있음.
 */
function copyTextWithTemporaryInput(text) {
  const previouslyFocusedElement =
    document.activeElement;

  const currentSelection =
    window.getSelection();

  const previousRanges = [];

  if (currentSelection) {
    for (
      let index = 0;
      index < currentSelection.rangeCount;
      index += 1
    ) {
      previousRanges.push(
        currentSelection.getRangeAt(index),
      );
    }
  }

  const temporaryInput =
    document.createElement("textarea");

  temporaryInput.value = text;
  temporaryInput.setAttribute(
    "aria-hidden",
    "true",
  );

  temporaryInput.style.position = "fixed";
  temporaryInput.style.left = "0";
  temporaryInput.style.top = "0";
  temporaryInput.style.width = "1px";
  temporaryInput.style.height = "1px";
  temporaryInput.style.opacity = "0";
  temporaryInput.style.pointerEvents = "none";

  document.body.appendChild(
    temporaryInput,
  );

  temporaryInput.focus();
  temporaryInput.select();
  temporaryInput.setSelectionRange(
    0,
    temporaryInput.value.length,
  );

  let copied = false;

  try {
    copied = document.execCommand("copy");
  } finally {
    temporaryInput.remove();

    if (
      previouslyFocusedElement
      && typeof previouslyFocusedElement.focus
        === "function"
    ) {
      previouslyFocusedElement.focus();
    }

    if (currentSelection) {
      currentSelection.removeAllRanges();

      previousRanges.forEach((range) => {
        currentSelection.addRange(range);
      });
    }
  }

  return copied;
}


/*
 * 팝업에 표시된 접근 코드를
 * 시스템 클립보드에 복사함.
 */
async function copyPopupAccessCode() {
  const accessCode =
    (
      issuedAccessCodeInMemory
      || popupAccessCode.textContent
      || ""
    ).trim();

  if (!accessCode) {
    window.alert(
      "복사할 사고 접근 코드가 없습니다.",
    );

    return;
  }

  /*
   * 0.0.0.0 같은 비보안 HTTP 주소에서는
   * 브라우저가 클립보드 API를 차단할 수 있음.
   *
   * 따라서 복사 성공 여부와 관계없이
   * 접근 코드 입력란에는 반드시 자동 입력함.
   */
  accessCodeInput.value = accessCode;

  accessCodeInput.dispatchEvent(
    new Event(
      "input",
      { bubbles: true },
    ),
  );

  let copied = false;

  if (
    window.isSecureContext
    && navigator.clipboard
    && navigator.clipboard.writeText
  ) {
    try {
      await navigator.clipboard.writeText(
        accessCode,
      );

      copied = true;
    } catch (error) {
      console.warn(
        "[ACCESS CODE] Clipboard API 실패, "
        + "대체 복사를 시도함:",
        error,
      );
    }
  }

  if (!copied) {
    copied = copyTextWithTemporaryInput(
      accessCode,
    );
  }

  copyPopupAccessCodeButton.textContent =
    copied
      ? "복사·입력 완료"
      : "입력 완료";

  setMessage(
    portalMessage,
    copied
      ? "접근 코드를 복사하고 입력란에 자동으로 채웠습니다."
      : "클립보드가 차단되어 접근 코드 입력란에 자동으로 채웠습니다.",
    "success",
  );

  /*
   * 결과를 잠시 보여준 뒤 팝업을 닫고
   * 영상 잠금 해제 입력란으로 이동함.
   */
  window.setTimeout(
    () => {
      closeAccessCodeModal();
      accessCodeInput.focus();
      accessCodeInput.select();
    },
    450,
  );
}


/*
 * 사고 영상 접근 상태에 따라
 * 코드 입력 영역 또는 영상 영역을 표시함.
 */
/*
 * 사고 영상 접근 상태에 따라
 * 코드 입력 영역 또는 영상 영역을 표시함.
 */
function updateVideoAccess(status) {
  /*
   * 병합 영상이 생성되지 않은 경우임.
   */
  if (!status.merged_video_available) {
    resetMergedVideoAccess();

    unlockArea.classList.add(
      "hidden",
    );

    videoArea.classList.add(
      "hidden",
    );

    setMessage(
      portalMessage,
      "병합 영상이 아직 준비되지 않았습니다.",
    );

    return;
  }

  /*
   * 병합 영상은 있지만 접근 코드 인증을
   * 통과하지 않은 상태임.
   */
  if (!status.unlocked) {
    resetMergedVideoAccess();

    unlockArea.classList.remove(
      "hidden",
    );

    videoArea.classList.add(
      "hidden",
    );

    setMessage(
      portalMessage,
      "사고 접근 코드를 입력해주세요.",
    );

    return;
  }

  /*
   * 로그인, 차량 소유권, 사고 접근 인증을
   * 모두 통과한 상태임.
   */
  unlockArea.classList.add(
    "hidden",
  );

  videoArea.classList.remove(
    "hidden",
  );

  const encodedEventId =
    encodeURIComponent(
      selectedEventId,
    );

  /*
   * 영상 재생 API 주소를 설정함.
   *
   * cache 값을 붙여 브라우저가 과거의
   * 403 또는 500 응답을 재사용하지 않게 함.
   */
  mergedVideo.preload = "auto";
  mergedVideo.src =
    `/api/v1/portal/events/${
      encodedEventId
    }/video?cache=${Date.now()}`;

  mergedVideo.load();

  /*
   * 인증된 사고의 다운로드 링크를 활성화함.
   */
  enableMergedVideoDownload();
  window.initializeVideoViews?.(mergedVideo,
    `/api/v1/portal/events/${encodedEventId}/video`, downloadMergedVideoButton);
  window.revealEvidenceVideo?.(videoArea);

    setMessage(
    portalMessage,
    "",
    );
}


/*
 * 사고 접근 코드 입력 폼 처리임.
 */
accessCodeForm.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

    if (!selectedEventId) {
      setMessage(
        portalMessage,
        "먼저 사고 기록을 선택해주세요.",
        "error",
      );

      return;
    }

    const accessCode =
      accessCodeInput.value.trim();

    if (!accessCode) {
      setMessage(
        portalMessage,
        "사고 접근 코드를 입력해주세요.",
        "error",
      );

      accessCodeInput.focus();

      return;
    }

    try {
      /*
       * 접근 코드 검증 API를 호출함.
       * 성공하면 event_access_session 쿠키가 발급됨.
       */
      await apiRequest(
        "/api/v1/access/verify",
        {
          method: "POST",

          body: JSON.stringify({
            event_id:
              selectedEventId,

            access_code:
              accessCode,
          }),
        },
      );

      /*
       * 새로 발급된 event_access_session 쿠키를 기준으로
       * 영상 접근 상태를 다시 조회함.
       */
      const accessStatus =
        await apiRequest(
          `/api/v1/portal/events/${
            encodeURIComponent(
              selectedEventId,
            )
          }/access-status`,
        );

      updateVideoAccess(
        accessStatus,
      );
    } catch (error) {
      console.error(
        "[ACCESS VERIFY] 검증 실패:",
        error,
      );

      setMessage(
        portalMessage,
        getErrorMessage(error),
        "error",
      );
    }
  },
);

/*
 * 잠겨 있는 다운로드 버튼이
 * 키보드 등의 방식으로 실행되는 것을 방지함.
 *
 * 실제 다운로드 권한은 서버도 다시 검사함.
 */
downloadMergedVideoButton.addEventListener(
  "click",
  (event) => {
    const isDisabled =
      downloadMergedVideoButton
        .getAttribute(
          "aria-disabled",
        )
      === "true";

    if (!isDisabled) {
      return;
    }

    event.preventDefault();

    setMessage(
      portalMessage,
      "사고 접근 코드 인증 후 다운로드할 수 있습니다.",
      "error",
    );
  },
);

/*
 * 로그아웃 버튼 처리임.
 */
logoutButton.addEventListener(
  "click",
  logoutAndRedirect,
);


/*
 * 접근 코드 팝업 열기 버튼임.
 */
openAccessCodeModalButton
  .addEventListener(
    "click",
    openAccessCodeModal,
  );


/*
 * 팝업 오른쪽 위 X 버튼임.
 */
closeAccessCodeModalButton
  .addEventListener(
    "click",
    closeAccessCodeModal,
  );


/*
 * 본인인증 화면의 취소 버튼임.
 */
cancelIdentityVerificationButton
  .addEventListener(
    "click",
    closeAccessCodeModal,
  );


/*
 * 시연용 본인인증 확인 버튼임.
 */
confirmIdentityVerificationButton
  .addEventListener(
    "click",
    confirmMockIdentityVerification,
  );


/*
 * 발급된 접근 코드 복사 버튼임.
 */
copyPopupAccessCodeButton
  .addEventListener(
    "click",
    copyPopupAccessCode,
  );


/*
 * ESC 키로 팝업을 닫음.
 */
document.addEventListener(
  "keydown",
  (event) => {
    if (
      event.key === "Escape"
      && !accessCodeModal.hidden
    ) {
      event.preventDefault();
      closeAccessCodeModal();
    }
  },
);


/*
 * 팝업 카드 바깥의 어두운 배경을 누르면
 * 팝업을 닫음.
 */
accessCodeModal.addEventListener(
  "click",
  (event) => {
    if (
      event.target === accessCodeModal
    ) {
      closeAccessCodeModal();
    }
  },
);


/*
 * 페이지를 처음 열면 사용자·차량·사고 정보를
 * 서버에서 조회함.
 */
// 한국 시간 기준으로 시작일·종료일을 모두 포함하며 역전된 범위는 적용하지 않음.
document.querySelector("#incident-history-filters").addEventListener("submit", (event) => {
  event.preventDefault();
  const startInput = document.querySelector("#incident-start-date");
  const endInput = document.querySelector("#incident-end-date");
  endInput.setCustomValidity("");
  if (startInput.value && endInput.value && startInput.value > endInput.value) {
    endInput.setCustomValidity("종료일은 시작일 이후로 선택해주세요.");
    endInput.reportValidity();
    return;
  }
  incidentHistoryQuery = { start: startInput.value, end: endInput.value,
    sort: document.querySelector("#incident-sort").value,
    vehicle: incidentHistoryQuery.vehicle };
  visibleIncidentCount = 5;
  renderEvents(currentPortalEvents);
});
document.querySelector("#incident-end-date").addEventListener("input", (event) => event.target.setCustomValidity(""));
document.querySelector("#incident-start-date").addEventListener("input", () => document.querySelector("#incident-end-date").setCustomValidity(""));
document.querySelector("#incident-sort").addEventListener("change", (event) => {
  incidentHistoryQuery.sort = event.target.value;
  visibleIncidentCount = 5;
  renderEvents(currentPortalEvents);
});
document.querySelector("#incident-reset").addEventListener("click", () => {
  document.querySelector("#incident-history-filters").reset();
  document.querySelector("#incident-end-date").setCustomValidity("");
  incidentHistoryQuery = { start: "", end: "", sort: "newest", vehicle: "" };
  visibleIncidentCount = 5;
  updateVehicleHistorySelection();
  renderEvents(currentPortalEvents);
});
// 내부 ID와 정밀 시각은 사용자가 표시 옵션을 선택했을 때만 노출함.
document.querySelectorAll("#incident-show-id, #incident-detailed-time").forEach((input) => {
  input.addEventListener("change", () => renderEvents(currentPortalEvents));
});
document.querySelector("#incident-show-more").addEventListener("click", () => {
  visibleIncidentCount += 5;
  renderEvents(currentPortalEvents);
});
document.querySelector("#incident-collapse").addEventListener("click", () => {
  visibleIncidentCount = 5;
  renderEvents(currentPortalEvents);
});

loadOverview().catch((error) => {
  console.error(
    "[PORTAL] 초기화 실패:",
    error,
  );

  setMessage(
    portalMessage,
    getErrorMessage(error),
    "error",
  );
});


/*
 * Chrome이 BFCache에서 과거 포털 화면을
 * 복원한 경우 데이터를 다시 조회함.
 */
window.addEventListener(
  "pageshow",
  (event) => {
    if (!event.persisted) {
      return;
    }

    loadOverview().catch((error) => {
      console.error(
        "[PORTAL] 캐시 복원 조회 실패:",
        error,
      );

      setMessage(
        portalMessage,
        getErrorMessage(error),
        "error",
      );
    });
  },
);
