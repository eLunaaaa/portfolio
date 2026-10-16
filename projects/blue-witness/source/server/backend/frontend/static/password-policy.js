/* 신규 비밀번호만 검사함. 서버와 동일한 ASCII 조합 조건을 입력 중 표시함. */
function bindPasswordPolicy(input) {
  const rules = document.createElement("ul");
  rules.className = "password-rules";
  const requirements = [
    ["10~128자", (value) => [...value].length >= 10 && [...value].length <= 128],
    ["영문 대문자 포함", (value) => /[A-Z]/.test(value)],
    ["영문 소문자 포함", (value) => /[a-z]/.test(value)],
    ["숫자 포함", (value) => /[0-9]/.test(value)],
    ["특수문자 포함 (예: ! @ # $)", (value) => /[!"#$%&'()*+,\-./:;<=>?@[\]\\^_`{|}~]/.test(value)],
  ];
  input.closest("label").after(rules);
  function update() {
    const missing = requirements.filter(([, check]) => !check(input.value));
    rules.replaceChildren(...requirements.map(([label, check]) => {
      const item = document.createElement("li");
      const valid = check(input.value);
      item.textContent = `${valid ? "✓" : "○"} ${label}`;
      item.className = valid ? "is-valid" : (input.value ? "is-invalid" : "");
      return item;
    }));
    input.setCustomValidity(missing.length ? "비밀번호 조건을 모두 충족해주세요: " + missing.map(([label]) => label).join(", ") : "");
    return missing.length === 0;
  }
  input.addEventListener("input", update);
  input.form.addEventListener("reset", () => window.setTimeout(update, 0));
  update();
  return update;
}
