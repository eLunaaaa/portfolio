/* 모든 보기의 시간축은 서버에서 동일하게 생성함. 전환 시 현재 시각·배속을 유지함. */
(() => {
  const states = new WeakMap();
  window.initializeVideoViews = (video, baseUrl, download = null) => {
    states.get(video)?.cancel();
    const group = document.querySelector(`[data-video-view-target="${video.id}"]`);
    if (!group) return;
    const buttons = [...group.querySelectorAll("[data-view]")];
    const message = document.getElementById(group.getAttribute("aria-describedby"));
    let selected = "all";
    const descriptions = {
      all: "전체 영상은 병합 대상인 모든 차량의 영상을 함께 보여줍니다.",
      witness: "목격 차량 영상은 사고 차량을 제외한 다른 차량들이 제공한 영상을 보여줍니다.",
      own: "내 차량 영상은 해당 사고 차량이 촬영한 영상을 보여줍니다.",
    };
    const render = () => {
      for (const button of buttons) {
        button.setAttribute("aria-pressed", String(button.dataset.view === selected));
      }
      if (message) message.textContent = descriptions[selected];
    };
    const controller = new AbortController();
    let transition = new AbortController();
    let pendingPlayback = null;
    let active = true;
    const cancel = () => {
      active = false;
      pendingPlayback = null;
      controller.abort();
      transition.abort();
    };
    states.set(video, {cancel});
    video.addEventListener("cancelpendingplayback", cancel, {signal: controller.signal});
    // 보기 전환이 끝나기 전의 재생 버튼 요청은 기존 위치 복원보다 우선함.
    video.addEventListener("playbackrequest", event => {
      if (!pendingPlayback || typeof event.detail?.apply !== "function") return;
      event.preventDefault();
      pendingPlayback = {apply: event.detail.apply};
    }, {signal: controller.signal});
    for (const button of buttons) button.disabled = true;
    render();

    const changeView = view => {
      if (!active || view === selected) return;
      selected = view;
      render();
      // 연속 전환 중에는 load()로 초기화된 값 대신 첫 전환 직전의 상태를 유지함.
      pendingPlayback ??= {
        time: video.currentTime || 0,
        rate: video.playbackRate,
        resume: !video.paused,
      };
      transition.abort();
      transition = new AbortController();
      video.pause();
      video.addEventListener("loadedmetadata", () => {
        if (!active || !pendingPlayback) return;
        const playback = pendingPlayback;
        pendingPlayback = null;
        if (playback.apply) {
          playback.apply();
          return;
        }
        video.currentTime = Math.min(playback.time, Math.max(0, video.duration - 0.05));
        video.playbackRate = playback.rate;
        if (playback.resume) video.play().catch(() => {});
      }, {once: true, signal: transition.signal});
      video.src = `${baseUrl}?view=${selected}&cache=${Date.now()}`;
      if (download) download.href = `${baseUrl}/download?view=${selected}`;
      video.load();
    };
    for (const button of buttons) button.onclick = () => {
      if (!button.disabled) changeView(button.dataset.view);
    };

    fetch(`${baseUrl}/modes`, {credentials: "include", cache: "no-store", signal: controller.signal})
      .then(response => {
        if (!response.ok) throw new Error("영상 모드를 확인할 수 없습니다.");
        return response.json();
      }).then(data => {
        if (!active) return;
        for (const button of buttons) {
          button.disabled = !data.views[button.dataset.view];
          button.title = button.disabled ? "영상이 없거나 병합 대상에서 제외된 모드입니다." : "";
        }
        render();
      }).catch(error => {
        if (!active || error.name === "AbortError") return;
        if (message) message.textContent = error.message;
      });
  };
})();
