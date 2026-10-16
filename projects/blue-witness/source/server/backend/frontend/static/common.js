async function apiRequest(path, options = {}) {
  const requestOptions = {
    credentials: "include",
    ...options,
  };

  requestOptions.headers = {
    Accept: "application/json",
    ...(options.headers || {}),
  };

  if (
    requestOptions.body &&
    !(requestOptions.body instanceof FormData)
  ) {
    requestOptions.headers["Content-Type"] =
      "application/json";
  }

  const response = await fetch(
    path,
    requestOptions,
  );

  const contentType =
    response.headers.get("content-type") || "";

  let data = null;

  if (contentType.includes("application/json")) {
    data = await response.json();
  } else {
    data = await response.text();
  }

  if (!response.ok) {
    const detail =
      data && typeof data === "object"
        ? data.detail
        : data;

    const message = Array.isArray(detail)
      ? detail.map((item) => item.msg || JSON.stringify(item)).join("\n")
      : typeof detail === "string"
        ? detail
        : detail && typeof detail === "object"
          ? JSON.stringify(detail)
          : `HTTP ${response.status}`;
    throw new Error(message);
  }

  return data;
}


function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}


function formatDate(value) {
  if (!value) {
    return "-";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleString("ko-KR", {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}


function setMessage(
  element,
  message,
  type = "",
) {
  element.textContent = message;
  element.className = `message ${type}`;
}


async function logoutAndRedirect() {
  try {
    await apiRequest(
      "/api/v1/auth/logout",
      {
        method: "POST",
      },
    );
  } finally {
    window.location.href = "/login";
  }
}

/* 고정 배경의 하단이 실제 화면보다 위에서 끝나지 않도록 위치만 보정함.
 * 페이지에 없는 빈 높이를 추가하지 않으며 원래 위치는 호출자가 별도로 보관함.
 */
function fitFrozenModalBackground(scrollY) {
  const viewport = window.visualViewport;
  const visibleBottom = (viewport?.height || window.innerHeight) + (viewport?.offsetTop || 0);
  const maximumScroll = Math.max(0, document.body.offsetHeight - visibleBottom);
  const fittedScroll = Math.min(Math.max(0, scrollY), maximumScroll);
  document.body.style.top = `-${fittedScroll}px`;
  return fittedScroll;
}

/* 모바일 주소창·키보드·회전에 따라 바뀌는 실제 표시 영역을 팝업에 전달함.
 * resize/scroll 이벤트는 프레임당 한 번만 반영하며 기존 팝업 내부 스크롤은 건드리지 않음.
 */
(() => {
  const root = document.documentElement;
  let scheduled = false;
  function updateViewport() {
    scheduled = false;
    const viewport = window.visualViewport;
    const height = viewport?.height || window.innerHeight;
    const width = viewport?.width || window.innerWidth;
    root.style.setProperty("--modal-viewport-height", `${height}px`);
    root.style.setProperty("--modal-viewport-width", `${width}px`);
    root.style.setProperty("--modal-viewport-top", `${viewport?.offsetTop || 0}px`);
    root.style.setProperty("--modal-viewport-left", `${viewport?.offsetLeft || 0}px`);
    window.dispatchEvent(new Event("modalviewportchange"));
  }
  function scheduleUpdate() {
    if (scheduled) return;
    scheduled = true;
    window.requestAnimationFrame(updateViewport);
  }
  updateViewport();
  window.addEventListener("resize", scheduleUpdate, { passive: true });
  window.addEventListener("orientationchange", scheduleUpdate, { passive: true });
  window.addEventListener("pageshow", scheduleUpdate);
  window.visualViewport?.addEventListener("resize", scheduleUpdate, { passive: true });
  window.visualViewport?.addEventListener("scroll", scheduleUpdate, { passive: true });
})();

/* 블러 배경에서 시작한 제스처만 차단함.
 * 팝업 카드 안의 드래그·영상 조작·텍스트 선택에는 개입하지 않음.
 * overflow:hidden만으로 막히지 않는 iOS의 문서 바운스를 방지하기 위한 처리임.
 */
(() => {
  let backgroundGesture = false;
  const selector = ".modal-backdrop, .evidence-viewer-overlay";
  function isBackdrop(target) {
    return target instanceof Element && target.matches(selector) && !target.hidden;
  }
  document.addEventListener("touchstart", (event) => {
    backgroundGesture = isBackdrop(event.target);
  }, { passive: true, capture: true });
  document.addEventListener("touchmove", (event) => {
    if (backgroundGesture && event.cancelable) event.preventDefault();
  }, { passive: false, capture: true });
  ["touchend", "touchcancel"].forEach((name) => {
    document.addEventListener(name, () => { backgroundGesture = false; }, { passive: true, capture: true });
  });
  document.addEventListener("wheel", (event) => {
    if (isBackdrop(event.target) && event.cancelable) event.preventDefault();
  }, { passive: false, capture: true });
})();
