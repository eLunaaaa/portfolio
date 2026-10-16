const openVehicleRegisterButton =
  document.getElementById(
    "open-vehicle-register-button",
  );

const vehicleRegisterModal =
  document.getElementById(
    "vehicle-register-modal",
  );

const closeVehicleRegisterButton =
  document.getElementById(
    "close-vehicle-register-button",
  );

const cancelVehicleRegisterButton =
  document.getElementById(
    "cancel-vehicle-register-button",
  );

const vehicleRegisterForm =
  document.getElementById(
    "vehicle-register-form",
  );

const vehicleLicensePlateInput =
  document.getElementById(
    "vehicle-license-plate",
  );

const vehicleModelInput =
  document.getElementById(
    "vehicle-model",
  );

const vehicleDisplayNameInput =
  document.getElementById(
    "vehicle-display-name",
  );

const vehicleManualModeInput =
  document.getElementById(
    "vehicle-manual-mode",
  );

const vehiclePhotoFieldset =
  document.getElementById(
    "vehicle-photo-fieldset",
  );

const vehicleIconFieldset =
  document.getElementById(
    "vehicle-icon-fieldset",
  );

const vehicleRegisterMessage =
  document.getElementById(
    "vehicle-register-message",
  );

const submitVehicleRegisterButton =
  document.getElementById(
    "submit-vehicle-register-button",
  );

const vehicleVerificationModal = document.getElementById("vehicle-verification-modal");
const vehicleDeleteModal = document.getElementById("vehicle-delete-modal");
const vehicleDeletePlate = document.getElementById("vehicle-delete-plate");
const vehicleIdentityStage = document.getElementById("vehicle-identity-stage");
const vehicleOwnershipStage = document.getElementById("vehicle-ownership-stage");
const vehicleVerificationMessage = document.getElementById("vehicle-verification-message");
const vehicleVerificationTitle = document.getElementById("vehicle-verification-title");
const confirmVehicleIdentityButton = document.getElementById("confirm-vehicle-identity-button");
const confirmVehicleOwnershipButton = document.getElementById("confirm-vehicle-ownership-button");
let pendingVehicleOperation = null;
let vehicleVerificationToken = "";
let editingVehicle = null;
let vehiclePendingDeletion = null;
let vehicleRegisterSession = null;

let vehicleModalScrollState = null;

// 모바일 Safari는 overflow:hidden만으로 배경 드래그를 막지 못하므로
// 블러 뒤 body는 현재 스크롤 위치 그대로 고정하고 마지막 팝업이 닫힐 때 복원함.
function syncVehicleModalScrollLock() {
  const open = [vehicleRegisterModal, vehicleVerificationModal, vehicleDeleteModal]
    .some((modal) => !modal.hidden);
  if (open && !vehicleModalScrollState) {
    const properties = ["position", "top", "left", "right", "width", "overflow", "minHeight"];
    vehicleModalScrollState = {
      x: window.scrollX, y: window.scrollY,
      styles: Object.fromEntries(properties.map((name) => [name, document.body.style[name]])),
    };
    Object.assign(document.body.style, {
      position: "fixed", top: `-${vehicleModalScrollState.y}px`,
      left: `-${vehicleModalScrollState.x}px`, right: "0", width: "100%", overflow: "hidden",
    });
    document.documentElement.classList.add("vehicle-modal-scroll-locked");
    document.body.classList.add("modal-open");
    fitFrozenModalBackground(vehicleModalScrollState.y);
  } else if (!open && vehicleModalScrollState) {
    const saved = vehicleModalScrollState;
    vehicleModalScrollState = null;
    Object.assign(document.body.style, saved.styles);
    document.documentElement.classList.remove("vehicle-modal-scroll-locked");
    document.body.classList.toggle("modal-open", Boolean(document.querySelector('.modal-backdrop:not([hidden])')));
    window.scrollTo({ left: saved.x, top: saved.y, behavior: "instant" });
  }
}

window.addEventListener("modalviewportchange", () => {
  if (vehicleModalScrollState) fitFrozenModalBackground(vehicleModalScrollState.y);
});

// 바깥 블러 영역의 터치는 배경으로 전달하지 않고 팝업 안 스크롤은 유지함.
[vehicleRegisterModal, vehicleVerificationModal, vehicleDeleteModal].forEach((modal) => {
  modal.addEventListener("touchmove", (event) => {
    if (event.target === modal) event.preventDefault();
  }, { passive: false });
});

function closeVehicleDeleteModal() {
  vehicleDeleteModal.hidden = true;
  vehiclePendingDeletion = null;
  syncVehicleModalScrollLock();
}

function openVehicleDeleteModal(vehicle) {
  vehiclePendingDeletion = vehicle;
  vehicleDeletePlate.textContent = vehicle.license_plate;
  vehicleDeleteModal.hidden = false;
  syncVehicleModalScrollLock();
  document.getElementById("cancel-vehicle-delete-button").focus();
}

function closeVehicleVerificationModal() {
  vehicleVerificationModal.hidden = true;
  pendingVehicleOperation = null;
  syncVehicleModalScrollLock();
}

function beginVehicleVerification(purpose, vehicle = null) {
  pendingVehicleOperation = { purpose, vehicle };
  vehicleIdentityStage.hidden = false;
  vehicleOwnershipStage.hidden = true;
  confirmVehicleOwnershipButton.disabled = false;
  vehicleVerificationTitle.textContent = {
    register: "차량 등록 확인", update: "차량 수정 확인", delete: "차량 삭제 확인",
  }[purpose];
  setMessage(vehicleVerificationMessage, "");
  vehicleVerificationModal.hidden = false;
  syncVehicleModalScrollLock();
  confirmVehicleIdentityButton.focus();
}

async function finishVehicleVerification() {
  const operation = pendingVehicleOperation;
  if (!operation || confirmVehicleOwnershipButton.disabled) return;
  confirmVehicleOwnershipButton.disabled = true;
  let deletionStarted = false;
  let deletionCompleted = false;
  try {
    const result = await apiRequest("/api/v1/portal/vehicles/verification", {
      method: "POST",
      body: JSON.stringify({
        purpose: operation.purpose,
        vehicle_id: operation.vehicle?.vehicle_id ?? null,
        identity_confirmed: true,
        vehicle_confirmed: true,
      }),
    });
    // Closing or replacing the dialog cancels this verification, even if its response arrives later.
    if (pendingVehicleOperation !== operation) return;
    if (operation.purpose === "delete") {
      deletionStarted = true;
      await apiRequest(`/api/v1/portal/vehicles/${encodeURIComponent(operation.vehicle.vehicle_id)}`, {
        method: "DELETE",
        headers: { "X-Vehicle-Verification": result.verification_token },
      });
      deletionCompleted = true;
      if (pendingVehicleOperation === operation) closeVehicleVerificationModal();
      setMessage(portalMessage, "차량을 내 목록에서 삭제했습니다. 사고 증거는 보존됩니다.", "success");
      if (await loadOverview() === false) {
        setMessage(portalMessage,
          `차량 등록 해제는 완료됐지만 목록을 새로 불러오지 못했습니다. ${portalMessage.textContent}`, "error");
      }
    } else {
      closeVehicleVerificationModal();
      openVehicleRegisterModal(result.verification_token, operation.vehicle);
    }
  } catch (error) {
    const message = getVehicleRegistrationErrorMessage(error);
    if (pendingVehicleOperation === operation) {
      setMessage(vehicleVerificationMessage, message, "error");
    } else if (deletionStarted) {
      setMessage(portalMessage, deletionCompleted
        ? `차량 등록 해제는 완료됐지만 목록을 새로 불러오지 못했습니다. ${message}`
        : message, "error");
    }
  } finally {
    if (pendingVehicleOperation === operation) confirmVehicleOwnershipButton.disabled = false;
  }
}


/*
 * 현대차 사진 목록에서 현재 선택된
 * radio 요소를 반환함.
 */
function getSelectedVehiclePhotoInput() {
  return document.querySelector(
    'input[name="vehicle-photo-key"]:checked',
  );
}


/*
 * 직접 입력 모드에서 선택한
 * 일반 차량 아이콘 키를 반환함.
 */
function getSelectedVehicleIconKey() {
  const checkedInput =
    document.querySelector(
      'input[name="vehicle-icon-key"]:checked',
    );

  return checkedInput
    ? checkedInput.value
    : "sedan";
}


/*
 * 현재 등록 모드에 맞는 화면 표시 키를 반환함.
 *
 * 기본 사진 선택:
 *     hyundai_ioniq_five
 *
 * 직접 입력:
 *     sedan
 */
function getSelectedVehicleAppearanceKey() {
  if (vehicleManualModeInput.checked) {
    return getSelectedVehicleIconKey();
  }

  const selectedPhoto =
    getSelectedVehiclePhotoInput();

  return selectedPhoto
    ? selectedPhoto.value
    : "hyundai_ioniq_five";
}


/*
 * 선택한 현대 차량 사진의 data-vehicle-model 값을
 * 차종 입력칸에 반영함.
 */
function applySelectedVehiclePhoto() {
  const selectedPhoto =
    getSelectedVehiclePhotoInput();

  if (!selectedPhoto) {
    vehicleModelInput.value = "";
    return;
  }

  vehicleModelInput.value =
    selectedPhoto.dataset.vehicleModel
    || "";
}


/*
 * 사진 선택 모드와 직접 입력 모드를 전환함.
 *
 * 사진 선택 모드:
 * - 현대차 사진 목록을 표시함.
 * - 차종 입력칸을 읽기 전용으로 설정함.
 * - 선택한 사진의 차종을 자동 입력함.
 *
 * 직접 입력 모드:
 * - 사진 목록을 숨김.
 * - 차종을 직접 입력할 수 있게 함.
 * - 일반 아이콘 선택 영역을 표시함.
 */
function updateVehicleRegistrationMode() {
  const isManualMode =
    vehicleManualModeInput.checked;

  vehiclePhotoFieldset.hidden =
    isManualMode;

  vehicleIconFieldset.hidden =
    !isManualMode;

  vehicleModelInput.readOnly =
    !isManualMode;

  if (isManualMode) {
    vehicleModelInput.value = "";

    vehicleModelInput.placeholder =
      "예: 기아 EV6";

    vehicleModelInput.focus();

    return;
  }

  vehicleModelInput.placeholder =
    "차량을 선택해주세요";

  applySelectedVehiclePhoto();
}


/*
 * 차량 등록 팝업을 최초 상태로 초기화함.
 *
 * 기본 선택 차량은 현대 아이오닉 5임.
 */
function resetVehicleRegisterForm() {
  vehicleRegisterForm.reset();

  const defaultVehicleInput =
    document.querySelector(
      'input[name="vehicle-photo-key"]'
      + '[value="hyundai_ioniq_five"]',
    );

  if (defaultVehicleInput) {
    defaultVehicleInput.checked = true;
  }

  vehicleManualModeInput.checked =
    false;

  updateVehicleRegistrationMode();

  setMessage(
    vehicleRegisterMessage,
    "",
  );

  submitVehicleRegisterButton.disabled =
    false;

  submitVehicleRegisterButton.textContent =
    "차량 등록";
}


/*
 * 차량 등록 팝업을 표시함.
 */
function openVehicleRegisterModal(token, vehicle = null) {
  resetVehicleRegisterForm();
  vehicleRegisterSession = { saving: false, saved: false };
  vehicleVerificationToken = token;
  editingVehicle = vehicle;
  if (vehicle) {
    vehicleLicensePlateInput.value = vehicle.license_plate;
    vehicleDisplayNameInput.value = vehicle.display_name || "";
    const photo = Array.from(document.querySelectorAll('input[name="vehicle-photo-key"]'))
      .find((input) => input.value === vehicle.icon_key);
    if (photo) {
      photo.checked = true;
      updateVehicleRegistrationMode();
      vehicleModelInput.value = vehicle.vehicle_model || photo.dataset.vehicleModel || "";
    } else {
      vehicleManualModeInput.checked = true;
      updateVehicleRegistrationMode();
      vehicleModelInput.value = vehicle.vehicle_model || "";
      const icon = document.querySelector(`input[name="vehicle-icon-key"][value="${vehicle.icon_key}"]`);
      if (icon) icon.checked = true;
    }
    submitVehicleRegisterButton.textContent = "차량 수정";
    document.getElementById("vehicle-register-title").textContent = "차량 수정";
  } else {
    document.getElementById("vehicle-register-title").textContent = "차량 등록";
  }

  vehicleRegisterModal.hidden = false;

  syncVehicleModalScrollLock();

  vehicleLicensePlateInput.focus();
}


/*
 * 차량 등록 팝업을 닫음.
 */
function closeVehicleRegisterModal() {
  vehicleRegisterSession = null;
  vehicleRegisterModal.hidden = true;

  syncVehicleModalScrollLock();

  resetVehicleRegisterForm();
  vehicleVerificationToken = "";
  editingVehicle = null;
}

/*
 * FastAPI 오류 응답을 사용자가 읽을 수 있는
 * 문자열로 변환함.
 *
 * FastAPI 422 오류의 detail은 배열이므로
 * 그냥 출력하면 [object Object]가 나타날 수 있음.
 */
function getVehicleRegistrationErrorMessage(
  error,
) {
  if (
    error === null
    || error === undefined
  ) {
    return (
      "차량 등록 중 알 수 없는 "
      + "오류가 발생했습니다."
    );
  }

  /*
   * 일반 JavaScript Error임.
   */
  if (
    error instanceof Error
    && typeof error.message === "string"
    && error.message
    && error.message !== "[object Object]"
  ) {
    return error.message;
  }

  /*
   * FastAPI의 detail 값을 찾음.
   */
  const detail =
    error.detail
    ?? error.message
    ?? error;

  if (typeof detail === "string") {
    return detail;
  }

  /*
   * FastAPI 422 검증 오류 형식임.
   *
   * 예:
   * [
   *   {
   *     "loc": ["body", "icon_key"],
   *     "msg": "String should match pattern ..."
   *   }
   * ]
   */
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const location =
          Array.isArray(item.loc)
            ? item.loc.join(" → ")
            : "";

        const message =
          item.msg
          ?? "입력값이 올바르지 않습니다.";

        if (!location) {
          return message;
        }

        return (
          `${location}: ${message}`
        );
      })
      .join("\n");
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
      return (
        "차량 등록 요청을 "
        + "처리하지 못했습니다."
      );
    }
  }

  return String(detail);
}

/*
 * 차량번호·차종·별명·화면 표시 키를
 * 서버에 전송함.
 *
 * 기존 API 구조를 그대로 사용함.
 *
 * icon_key에는 다음 중 하나가 저장됨.
 *
 * 사진 차량:
 *     hyundai_ioniq_five
 *
 * 직접 입력 차량:
 *     sedan
 */
async function submitVehicleRegistration(
  event,
) {
  event.preventDefault();

  const session = vehicleRegisterSession;
  if (!session || session.saving || session.saved) return;
  const vehicle = editingVehicle;
  const verificationToken = vehicleVerificationToken;

  const licensePlate =
    vehicleLicensePlateInput
      .value
      .trim();

  const vehicleModel =
    vehicleModelInput
      .value
      .trim();

  const displayName =
    vehicleDisplayNameInput
      .value
      .trim();

  const appearanceKey =
    getSelectedVehicleAppearanceKey();

  if (
    !licensePlate
    || !vehicleModel
    || !displayName
  ) {
    setMessage(
      vehicleRegisterMessage,
      "차량번호, 차종, 별명을 모두 입력해주세요.",
      "error",
    );

    return;
  }

  submitVehicleRegisterButton.disabled =
    true;
  session.saving = true;
  submitVehicleRegisterButton.textContent = vehicle ? "수정 중..." : "등록 중...";

  try {
    const vehicleUrl = vehicle
      ? `/api/v1/portal/vehicles/${encodeURIComponent(vehicle.vehicle_id)}`
      : "/api/v1/portal/vehicles";
    const savedVehicle = await apiRequest(vehicleUrl, {
      method: vehicle ? "PATCH" : "POST",
      headers: { "X-Vehicle-Verification": verificationToken },
      body: JSON.stringify({
        license_plate: licensePlate,
        vehicle_model: vehicleModel,
        display_name: displayName,
        icon_key: appearanceKey,
      }),
    });
    session.saved = true;
    if (vehicleRegisterSession === session) {
      vehicleVerificationToken = "";
      setMessage(
        vehicleRegisterMessage,
        `${savedVehicle.license_plate} 차량을 ${vehicle ? "수정" : "등록"}했습니다.`,
        "success",
      );
      window.setTimeout(() => {
        if (vehicleRegisterSession === session) closeVehicleRegisterModal();
      }, 700);
    }

    /*
     * 등록 완료 후 포털 정보를 다시 조회하여
     * 새로 등록한 차량 사진을 즉시 표시함.
     */
    if (await loadOverview() === false) {
      setMessage(portalMessage,
        `차량 정보는 저장됐지만 목록을 새로 불러오지 못했습니다. ${portalMessage.textContent}`, "error");
    }
  } catch (error) {
    console.error(
      "[VEHICLE REGISTER] 저장 또는 목록 갱신 실패:",
      error,
    );
    const message = getVehicleRegistrationErrorMessage(error);
    if (session.saved) {
      setMessage(portalMessage, `차량 정보는 저장됐지만 목록을 새로 불러오지 못했습니다. ${message}`, "error");
    } else {
      setMessage(vehicleRegisterSession === session ? vehicleRegisterMessage : portalMessage, message, "error");
    }
  } finally {
    session.saving = false;
    if (vehicleRegisterSession === session) {
      submitVehicleRegisterButton.disabled = session.saved;
      submitVehicleRegisterButton.textContent = session.saved ? "저장 완료" : vehicle ? "차량 수정" : "차량 등록";
    }
  }
}


/*
 * 현대차 사진 선택이 바뀌면
 * 선택한 차종을 입력칸에 반영함.
 */
document
  .querySelectorAll(
    'input[name="vehicle-photo-key"]',
  )
  .forEach((input) => {
    input.addEventListener(
      "change",
      () => {
        if (
          !vehicleManualModeInput.checked
        ) {
          applySelectedVehiclePhoto();
        }
      },
    );
  });


/*
 * 직접 입력 체크박스 상태에 따라
 * 등록 화면을 전환함.
 */
vehicleManualModeInput.addEventListener(
  "change",
  updateVehicleRegistrationMode,
);


openVehicleRegisterButton.addEventListener(
  "click",
  () => beginVehicleVerification("register"),
);

confirmVehicleIdentityButton.addEventListener("click", () => {
  vehicleIdentityStage.hidden = true;
  vehicleOwnershipStage.hidden = false;
  confirmVehicleOwnershipButton.focus();
});
confirmVehicleOwnershipButton.addEventListener("click", finishVehicleVerification);
document.getElementById("close-vehicle-verification-button")
  .addEventListener("click", closeVehicleVerificationModal);
vehicleVerificationModal.addEventListener("click", (event) => {
  if (event.target === vehicleVerificationModal) closeVehicleVerificationModal();
});
document.getElementById("close-vehicle-delete-button")
  .addEventListener("click", closeVehicleDeleteModal);
document.getElementById("cancel-vehicle-delete-button")
  .addEventListener("click", closeVehicleDeleteModal);
document.getElementById("confirm-vehicle-delete-button")
  .addEventListener("click", () => {
    const vehicle = vehiclePendingDeletion;
    closeVehicleDeleteModal();
    if (vehicle) beginVehicleVerification("delete", vehicle);
  });
vehicleDeleteModal.addEventListener("click", (event) => {
  if (event.target === vehicleDeleteModal) closeVehicleDeleteModal();
});

/* 파일 이름 변경처럼 별명을 누르면 카드 안의 텍스트만 입력칸으로 바꿈.
 * Enter 또는 바깥 클릭으로 저장하고 Escape로 취소함. */
function editVehicleNicknameInline(button, vehicle) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "vehicle-nickname-input";
  input.maxLength = 80;
  input.value = vehicle.display_name || "";
  input.setAttribute("aria-label", "차량 별명 수정");
  button.replaceWith(input);
  input.focus();
  input.select();

  let finished = false;
  function cancel() {
    if (finished) return;
    finished = true;
    input.replaceWith(button);
  }
  async function save() {
    if (finished) return;
    const name = input.value.trim();
    if (!name) {
      setMessage(portalMessage, "차량 별명을 입력해주세요.", "error");
      input.focus();
      return;
    }
    if (name === (vehicle.display_name || "")) {
      cancel();
      return;
    }
    finished = true;
    input.disabled = true;
    try {
      await apiRequest(`/api/v1/portal/vehicles/${encodeURIComponent(vehicle.vehicle_id)}/nickname`, {
        method: "PATCH", body: JSON.stringify({ display_name: name }),
      });
      await loadOverview();
    } catch (error) {
      input.replaceWith(button);
      setMessage(portalMessage, getVehicleRegistrationErrorMessage(error), "error");
    }
  }
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
  });
  input.addEventListener("blur", save);
}

vehicleList.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-vehicle-action]");
  if (!button) return;
  const vehicle = currentPortalVehicles.find(
    (item) => item.vehicle_id === button.dataset.vehicleId,
  );
  if (!vehicle) return;
  const action = button.dataset.vehicleAction;
  if (action === "nickname") {
    editVehicleNicknameInline(button, vehicle);
  } else if (action === "edit") {
    beginVehicleVerification("update", vehicle);
  } else if (action === "delete") {
    openVehicleDeleteModal(vehicle);
  }
});


closeVehicleRegisterButton.addEventListener(
  "click",
  closeVehicleRegisterModal,
);


cancelVehicleRegisterButton.addEventListener(
  "click",
  closeVehicleRegisterModal,
);


vehicleRegisterForm.addEventListener(
  "submit",
  submitVehicleRegistration,
);


vehicleRegisterModal.addEventListener(
  "click",
  (event) => {
    if (
      event.target === vehicleRegisterModal
    ) {
      closeVehicleRegisterModal();
    }
  },
);


document.addEventListener(
  "keydown",
  (event) => {
    if (
      event.key === "Escape"
      && !vehicleRegisterModal.hidden
    ) {
      closeVehicleRegisterModal();
    }
    if (event.key === "Escape" && !vehicleVerificationModal.hidden) {
      closeVehicleVerificationModal();
    }
    if (event.key === "Escape" && !vehicleDeleteModal.hidden) {
      closeVehicleDeleteModal();
    }
  },
);
