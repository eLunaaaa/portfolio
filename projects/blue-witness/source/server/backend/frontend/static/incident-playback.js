/* 병합 파일 자체는 보존하고 브라우저의 시작 위치와 재생 속도만 바꿈. */
(() => {
  document.querySelectorAll("video").forEach((video) => {
    const buttons = Array.from(document.querySelectorAll("[data-video-target]"))
      .filter((button) => button.dataset.videoTarget === video.id);
    if (!buttons.length) return;

    let pendingMode = null;

    // 메타데이터를 읽은 뒤 이동하여 영상 로딩 중 클릭도 처리함.
    function playMode(mode) {
      if (!video.getAttribute("src")) return;
      if (video.readyState < 1) {
        pendingMode = mode;
        return;
      }
      pendingMode = null;
      const incident = mode === "incident";
      video.playbackRate = incident ? 0.25 : 1;
      // 7초 이하의 영상은 끝으로 이동하지 않고 첫 장면부터 재생함.
      video.currentTime = incident && video.duration > 7 ? 7 : 0;
      video.play().catch(() => {
        // 브라우저가 재생을 제한하면 기본 플레이어의 재생 버튼을 사용함.
      });
    }

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const apply = () => playMode(button.dataset.playbackMode);
        // 영상 보기 전환 중이면 새 영상이 준비된 뒤 같은 재생 명령을 실행함.
        const request = new CustomEvent("playbackrequest", {cancelable: true, detail: {apply}});
        if (video.dispatchEvent(request)) apply();
      });
    });
    video.addEventListener("loadedmetadata", () => {
      if (pendingMode) playMode(pendingMode);
    });
    // 아직 재생 전인 영상도 창을 닫으면 예약된 재생을 취소함.
    video.addEventListener("cancelpendingplayback", () => {
      pendingMode = null;
    });
    // 다른 사고를 열 때 이전 영상의 슬로모션 설정을 초기화함.
    video.addEventListener("emptied", () => {
      pendingMode = null;
      video.playbackRate = 1;
    });
  });
})();
