/* 사고 상세 창과 인증 후 영상의 부드러운 등장 효과를 관리합니다. */
(() => {
  const viewers = new Map();
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const pendingReveals = new WeakMap();
  let backgroundState = null;

  // 블러 뒤 페이지는 팝업을 열기 직전의 스크롤 위치 그대로 고정함.
  // 가운데 팝업의 위치·내부 스크롤·등장 효과와는 별개임.
  function syncViewerScrollLock(opening = false) {
    const open = opening || [...viewers.values()].some((viewer) => !viewer.overlay.hidden);
    if (open && !backgroundState) {
      const properties = ["position", "top", "left", "right", "width", "overflow", "minHeight"];
      backgroundState = {
        x: window.scrollX, y: window.scrollY,
        styles: Object.fromEntries(properties.map((name) => [name, document.body.style[name]])),
      };
      Object.assign(document.body.style, {
        position: "fixed", top: `-${backgroundState.y}px`, left: `-${backgroundState.x}px`,
        right: "0", width: "100%", overflow: "hidden",
      });
      document.documentElement.classList.add("evidence-background-locked");
      fitFrozenModalBackground(backgroundState.y);
    } else if (!open && backgroundState) {
      const saved = backgroundState;
      backgroundState = null;
      Object.assign(document.body.style, saved.styles);
      document.documentElement.classList.remove("evidence-background-locked");
      window.scrollTo({ left: saved.x, top: saved.y, behavior: "instant" });
    }
  }

  window.addEventListener("modalviewportchange", () => {
    if (backgroundState) fitFrozenModalBackground(backgroundState.y);
  });

  ["event-detail-panel", "admin-video-panel"].forEach((id) => {
    const panel = document.getElementById(id);
    if (!panel) return;
    const overlay = document.createElement("div");
    overlay.className = "evidence-viewer-overlay";
    overlay.hidden = true;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", "사고 영상 확인");
    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "evidence-viewer-close";
    closeButton.setAttribute("aria-label", "영상 창 닫기");
    closeButton.textContent = "×";
    panel.classList.add("evidence-viewer-card");
    const content = document.createElement("div");
    content.className = "evidence-viewer-content";
    while (panel.firstChild) content.append(panel.firstChild);
    panel.append(closeButton, content);
    overlay.append(panel);
    document.body.append(overlay);
    viewers.set(id, { panel, overlay, closeButton, source: null, animation: null });
    closeButton.addEventListener("click", () => window.closeEvidenceViewer(id));
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) window.closeEvidenceViewer(id);
    });
  });

  // 창을 닫으면 실제 클릭한 버튼으로 포커스를 돌려줍니다.
  window.openEvidenceViewer = (id, source) => {
    const viewer = viewers.get(id);
    if (!viewer) return;
    viewer.source = source instanceof HTMLElement ? source : document.activeElement;
    // 표시 후 html overflow 규칙이 적용되기 전에 원래 위치를 저장함.
    syncViewerScrollLock(true);
    viewer.overlay.hidden = false;
    viewer.panel.classList.remove("hidden");
    viewer.panel.querySelector(".evidence-viewer-content").scrollTop = 0;
    // 공통 CSS 효과를 사용함. 큰 확대와 배경 전체 합성은 피함.
    viewer.closeButton.focus({ preventScroll: true });
  };

  // 접근 인증 완료 후 영상이 부드럽게 확대되며 나타나도록 함.
  window.revealEvidenceVideo = (area) => {
    const content = area.closest(".evidence-viewer-content");
    if (!content) return;
    const video = area.querySelector("video");
    pendingReveals.get(area)?.();
    area.getAnimations().forEach((animation) => animation.cancel());
    // 디코딩과 등장 효과가 겹치지 않게 첫 프레임 준비를 기다림.
    area.style.opacity = "0";
    area.style.willChange = "transform, opacity";
    let completed = false;
    let timer;
    const cleanup = () => {
      clearTimeout(timer);
      video?.removeEventListener("loadeddata", reveal);
      video?.removeEventListener("error", reveal);
      pendingReveals.delete(area);
    };
    const cancel = () => {
      completed = true;
      cleanup();
      area.style.opacity = "";
      area.style.willChange = "";
    };
    const reveal = () => {
      if (completed) return;
      completed = true;
      cleanup();
      if (area.closest(".evidence-viewer-overlay")?.hidden) {
        area.style.opacity = "";
        area.style.willChange = "";
        return;
      }
      content.scrollTop += area.getBoundingClientRect().top
        - content.getBoundingClientRect().top - 20;
      requestAnimationFrame(() => requestAnimationFrame(() => {
        // 프레임 준비 중 창을 닫았다면 등장 효과를 시작하지 않습니다.
        if (area.closest(".evidence-viewer-overlay")?.hidden) {
          area.style.opacity = "";
          area.style.willChange = "";
          return;
        }
        area.style.opacity = "";
        if (reducedMotion.matches) {
          area.style.willChange = "";
          return;
        }
        const animation = area.animate([
          { opacity: 0, transform: "translate3d(0, 24px, 0) scale(.985)" },
          { opacity: 1, transform: "translate3d(0, 0, 0) scale(1)" },
        ], { duration: 900, easing: "cubic-bezier(.16,1,.3,1)" });
        animation.finished.catch(() => {}).finally(() => {
          area.style.willChange = "";
        });
      }));
    };
    pendingReveals.set(area, cancel);
    video?.addEventListener("loadeddata", reveal, { once: true });
    video?.addEventListener("error", reveal, { once: true });
    // 연결이 느려도 플레이어를 사용할 수 있도록 준비 대기는 최대 4초로 제한합니다.
    timer = setTimeout(reveal, 4000);
    if (!video || video.readyState >= 2) reveal();
  };

  window.closeEvidenceViewer = (id) => {
    const viewer = viewers.get(id);
    if (!viewer || viewer.overlay.hidden) return;
    viewer.animation?.cancel();
    const videoArea = viewer.panel.querySelector("#video-area");
    if (videoArea) pendingReveals.get(videoArea)?.();
    viewer.panel.querySelectorAll("video").forEach((video) => {
      video.dispatchEvent(new Event("cancelpendingplayback"));
      video.pause();
    });
    viewer.overlay.hidden = true;
    viewer.panel.classList.add("hidden");
    syncViewerScrollLock();
    viewer.source?.focus({ preventScroll: true });
  };

  // 인증 팝업이 열려 있는 동안에는 해당 팝업의 키보드 조작을 우선함.
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented) return;
    if (document.querySelector('.modal-backdrop:not([hidden])')) return;
    for (const [id, viewer] of viewers) {
      if (viewer.overlay.hidden) continue;
      if (event.key === "Escape") window.closeEvidenceViewer(id);
      if (event.key !== "Tab") continue;
      const focusable = Array.from(viewer.panel.querySelectorAll(
        'button:not([disabled]), a[href], input:not([disabled]), video[controls], [tabindex="0"]'
      )).filter((element) => element.getClientRects().length);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault(); first?.focus();
      }
    }
  });
})();
