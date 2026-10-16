const loginTab =
  document.querySelector("#login-tab");

const registerTab =
  document.querySelector("#register-tab");

const loginForm =
  document.querySelector("#login-form");

const registerForm =
  document.querySelector("#register-form");

const authMessage =
  document.querySelector("#auth-message");

const registerPasswordInput =
  document.getElementById(
    "register-password",
  );

const registerPasswordConfirmInput =
  document.getElementById(
    "register-password-confirm",
  );

const registerPasswordMessage =
  document.getElementById(
    "register-password-message",
  );

bindPasswordPolicy(registerPasswordInput);


/*
 * 로그인 화면을 표시하는 함수임.
 *
 * 로그인 폼은 보여주고 회원가입 폼은 숨김.
 * 상단 탭의 활성화 상태도 함께 변경함.
 */
function showLogin() {
  loginForm.classList.remove("hidden");
  registerForm.classList.add("hidden");

  loginTab.classList.add("active");
  registerTab.classList.remove("active");

  setMessage(
    authMessage,
    "",
  );
}


/*
 * 회원가입 화면을 표시하는 함수임.
 *
 * 회원가입 폼은 보여주고 로그인 폼은 숨김.
 */
function showRegister() {
  registerForm.classList.remove("hidden");
  loginForm.classList.add("hidden");

  registerTab.classList.add("active");
  loginTab.classList.remove("active");

  setMessage(
    authMessage,
    "",
  );
}


/*
 * 회원가입 비밀번호와 비밀번호 확인값이
 * 같은지 검사하는 함수임.
 *
 * 반환값:
 * true  -> 두 비밀번호가 같음.
 * false -> 비어 있거나 서로 다름.
 */
function validateRegisterPasswords() {
  const password =
    registerPasswordInput.value;

  const confirmation =
    registerPasswordConfirmInput.value;

  if (confirmation.length === 0) {
    registerPasswordConfirmInput
      .setCustomValidity(
        "비밀번호를 한 번 더 입력해주세요.",
      );

    registerPasswordMessage.textContent =
      "비밀번호를 한 번 더 입력해주세요.";

    registerPasswordMessage.classList.remove(
      "is-valid",
    );

    registerPasswordMessage.classList.add(
      "is-invalid",
    );

    return false;
  }

  if (password !== confirmation) {
    registerPasswordConfirmInput
      .setCustomValidity(
        "입력한 비밀번호가 서로 다릅니다.",
      );

    registerPasswordMessage.textContent =
      "입력한 비밀번호가 서로 다릅니다.";

    registerPasswordMessage.classList.remove(
      "is-valid",
    );

    registerPasswordMessage.classList.add(
      "is-invalid",
    );

    return false;
  }

  registerPasswordConfirmInput
    .setCustomValidity("");

  registerPasswordMessage.textContent =
    "비밀번호가 일치합니다.";

  registerPasswordMessage.classList.remove(
    "is-invalid",
  );

  registerPasswordMessage.classList.add(
    "is-valid",
  );

  return true;
}


/*
 * 로그인한 계정의 권한에 따라
 * 관리자 화면 또는 사용자 화면으로 이동함.
 *
 * 주소 뒤의 login 값은 현재 시각임.
 * 매번 다른 주소를 사용하여 브라우저가
 * 과거 화면을 캐시에서 복원하는 것을 방지함.
 */
function redirectAfterLogin(account) {
  if (!account) {
    throw new Error(
      "로그인 응답에 계정 정보가 없습니다.",
    );
  }

  const role =
    String(account.role || "")
      .toUpperCase();

  const destination =
    role === "ADMIN"
      ? "/admin"
      : "/portal";

  const targetUrl =
    `${destination}?login=${Date.now()}`;

  console.log(
    "로그인 성공 계정:",
    account,
  );

  console.log(
    "이동할 주소:",
    targetUrl,
  );

  /*
   * assign()은 targetUrl을 새로운 페이지로 요청함.
   *
   * 주소에 Date.now()가 포함되므로
   * 기존 /portal 화면과 다른 URL로 인식됨.
   */
  window.location.assign(
    targetUrl,
  );
}


/*
 * 로그인 탭 클릭 처리임.
 */
loginTab.addEventListener(
  "click",
  showLogin,
);


/*
 * 회원가입 탭 클릭 처리임.
 */
registerTab.addEventListener(
  "click",
  showRegister,
);


/*
 * 로그인 폼 제출 처리임.
 *
 * 1. 브라우저의 기본 폼 제출을 막음.
 * 2. 입력된 아이디와 비밀번호를 읽음.
 * 3. 로그인 API에 JSON으로 전송함.
 * 4. 서버가 발급한 로그인 쿠키를 저장함.
 * 5. 계정 권한에 따라 화면을 이동함.
 */
loginForm.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

    const username =
      document
        .querySelector("#login-username")
        .value
        .trim();

    const password =
      document
        .querySelector("#login-password")
        .value;

    try {
      setMessage(
        authMessage,
        "로그인 중...",
      );

      const result =
        await apiRequest(
          "/api/v1/auth/login",
          {
            method: "POST",

            /*
             * 로그인 요청과 응답 과정에서
             * 브라우저 쿠키를 사용하도록 명시함.
             *
             * 같은 주소에 요청할 때는 기본적으로
             * 쿠키를 사용하지만, 의도를 명확히 함.
             */
            credentials: "same-origin",

            body: JSON.stringify({
              username,
              password,
            }),
          },
        );

      if (
        !result ||
        result.authenticated !== true
      ) {
        throw new Error(
          "서버에서 로그인 성공을 확인하지 못했습니다.",
        );
      }

      setMessage(
        authMessage,
        "로그인 성공. 화면을 이동합니다.",
        "success",
      );

      redirectAfterLogin(
        result.account,
      );

      /*
       * redirectAfterLogin() 호출 이후
       * 현재 submit 함수에서 더 진행하지 않도록 함.
       */
      return;
    } catch (error) {
      console.error(
        "로그인 처리 실패:",
        error,
      );

      setMessage(
        authMessage,
        error.message,
        "error",
      );
    }
  },
);


/*
 * 회원가입 폼 제출 처리임.
 *
 * 비밀번호 일치 여부를 먼저 확인한 뒤
 * 회원가입 API를 호출함.
 */
registerForm.addEventListener(
  "submit",
  async (event) => {
    event.preventDefault();

    if (!validateRegisterPasswords()) {
      registerPasswordConfirmInput.focus();
      return;
    }

    const fullName =
      document
        .querySelector("#register-full-name")
        .value
        .trim();

    const username =
      document
        .querySelector("#register-username")
        .value
        .trim();

    const password =
      document
        .querySelector("#register-password")
        .value;

    try {
      setMessage(
        authMessage,
        "계정을 생성하는 중...",
      );

      await apiRequest(
        "/api/v1/auth/register",
        {
          method: "POST",
          credentials: "same-origin",

          body: JSON.stringify({
            username,
            password,
            full_name: fullName,
          }),
        },
      );

      setMessage(
        authMessage,
        "회원가입이 완료되었습니다. 로그인해주세요.",
        "success",
      );

      showLogin();

      document.querySelector(
        "#login-username",
      ).value = username;

      document.querySelector(
        "#login-password",
      ).focus();

      registerForm.reset();

      registerPasswordMessage.textContent =
        "";

      registerPasswordMessage.classList.remove(
        "is-valid",
        "is-invalid",
      );

      registerPasswordConfirmInput
        .setCustomValidity("");
    } catch (error) {
      console.error(
        "회원가입 처리 실패:",
        error,
      );

      setMessage(
        authMessage,
        error.message,
        "error",
      );
    }
  },
);


/*
 * 비밀번호 입력값이 바뀔 때마다
 * 비밀번호 확인 상태를 다시 검사함.
 */
registerPasswordInput.addEventListener(
  "input",
  validateRegisterPasswords,
);

registerPasswordConfirmInput.addEventListener(
  "input",
  validateRegisterPasswords,
);
