/* 사진은 로그인 본인 전용 API로 조회하고 파일 선택은 저장 전 로컬 미리보기만 수행함. */
(() => {
  const fallback = "/static/images/profile-default.png";
  document.querySelectorAll("[data-profile-image]").forEach((image) => {
    image.addEventListener("error", () => { image.src = fallback; }, { once: true });
  });
  const photoForm = document.querySelector("#profile-photo-form");
  if (!photoForm) return;
  let previewUrl = null;
  const fileInput = document.querySelector("#profile-file");
  const preview = document.querySelector("#profile-preview");
  const photoMessage = document.querySelector("#profile-photo-message");
  function clearPreviewUrl() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
  }
  window.addEventListener("pagehide", clearPreviewUrl);
  fileInput.addEventListener("change", () => {
    clearPreviewUrl();
    const file = fileInput.files[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      fileInput.value = "";
      setMessage(photoMessage, "5MB 이하 PNG, JPG, WEBP 사진을 선택해주세요.", "error");
      return;
    }
    previewUrl = URL.createObjectURL(file);
    preview.src = previewUrl;
    setMessage(photoMessage, "미리보기입니다. 저장 버튼을 눌러 변경해주세요.");
  });
  photoForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = photoForm.querySelector("button");
    button.disabled = true;
    setMessage(photoMessage, "사진을 저장하는 중입니다.");
    try {
      const data = new FormData(photoForm);
      const result = await apiRequest("/api/v1/auth/profile/image", { method: "POST", body: data });
      clearPreviewUrl();
      preview.src = result.image_url;
      photoForm.reset();
      setMessage(photoMessage, "프로필 사진이 변경되었습니다.", "success");
    } catch (error) {
      setMessage(photoMessage, error.message, "error");
    } finally { button.disabled = false; }
  });
  const passwordForm = document.querySelector("#account-password-form");
  const validateNewPassword = bindPasswordPolicy(document.querySelector("#new-password"));
  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = document.querySelector("#account-password-message");
    const current = document.querySelector("#current-password");
    const password = document.querySelector("#new-password");
    const confirmation = document.querySelector("#confirm-password");
    if (!validateNewPassword()) {
      password.reportValidity();
      return;
    }
    if (password.value !== confirmation.value) {
      setMessage(message, "새 비밀번호 확인이 일치하지 않습니다.", "error");
      return;
    }
    const button = passwordForm.querySelector("button");
    button.disabled = true;
    try {
      await apiRequest("/api/v1/auth/password", { method: "POST", body: JSON.stringify({
        current_password: current.value, new_password: password.value, confirm_password: confirmation.value,
      }) });
      passwordForm.reset();
      setMessage(message, "변경 완료. 새 비밀번호로 다시 로그인해주세요.", "success");
      window.setTimeout(() => window.location.replace("/login"), 1000);
    } catch (error) {
      setMessage(message, error.message, "error");
      button.disabled = false;
    }
  });
  document.querySelector("#account-logout").addEventListener("click", async () => {
    try {
      await apiRequest("/api/v1/auth/logout", { method: "POST" });
      window.location.replace("/login");
    } catch (error) { setMessage(photoMessage, error.message, "error"); }
  });
  apiRequest("/api/v1/auth/me").then((user) => {
    document.querySelector("#account-full-name").textContent = user.full_name;
    document.querySelector("#account-username").textContent = user.username;
  }).catch(() => window.location.replace("/login"));
})();
