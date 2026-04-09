const registerForm = document.querySelector(".account-form");
const emailInput = document.getElementById("register-email");
const phoneInput = document.getElementById("register-phone");
const statusNode = document.getElementById("register-status");
let previousPhoneDigits = normalizePhone(phoneInput?.value || "");
let isBackspacePressed = false;

function setRegisterStatus(message, isError = false) {
  if (!statusNode) {
    return;
  }
  statusNode.textContent = message;
  statusNode.classList.toggle("is-error", isError);
}

function normalizePhone(value) {
  let digits = String(value || "").replace(/\D/g, "");
  if (digits.length === 10) {
    digits = `7${digits}`;
  }
  if (digits.startsWith("8") && digits.length === 11) {
    digits = `7${digits.slice(1)}`;
  }
  return digits.slice(0, 11);
}

function formatPhone(value) {
  const digits = normalizePhone(value);
  if (!digits) {
    return "";
  }

  let result = `+${digits.slice(0, 1)}`;
  if (digits.length > 1) {
    result += ` (${digits.slice(1, 4)}`;
  }
  if (digits.length >= 4) {
    result += ")";
  }
  if (digits.length > 4) {
    result += ` ${digits.slice(4, 7)}`;
  }
  if (digits.length > 7) {
    result += `-${digits.slice(7, 9)}`;
  }
  if (digits.length > 9) {
    result += `-${digits.slice(9, 11)}`;
  }
  return result;
}

function validateEmail(value) {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(String(value || "").trim());
}

phoneInput?.addEventListener("input", () => {
  let digits = normalizePhone(phoneInput.value);

  if (isBackspacePressed && digits === previousPhoneDigits) {
    digits = previousPhoneDigits.slice(0, -1);
  }

  phoneInput.value = formatPhone(digits);
  previousPhoneDigits = normalizePhone(phoneInput.value);
  isBackspacePressed = false;

  if (previousPhoneDigits.length > 0 && previousPhoneDigits.length < 11) {
    setRegisterStatus("Телефон заполняется только цифрами и автоматически форматируется.", false);
  }
});

phoneInput?.addEventListener("keydown", (event) => {
  isBackspacePressed = event.key === "Backspace";
});

emailInput?.addEventListener("input", () => {
  const value = emailInput.value.trim();
  if (!value) {
    setRegisterStatus("Заполните данные для регистрации.", false);
    return;
  }
  if (!validateEmail(value)) {
    setRegisterStatus("Укажите корректную электронную почту.", true);
    return;
  }
  setRegisterStatus("Данные выглядят хорошо.", false);
});

registerForm?.addEventListener("submit", (event) => {
  const email = emailInput?.value.trim() || "";
  const phoneDigits = normalizePhone(phoneInput?.value || "");

  if (!validateEmail(email)) {
    event.preventDefault();
    setRegisterStatus("Укажите корректную электронную почту.", true);
    emailInput?.focus();
    return;
  }

  if (phoneDigits.length !== 11) {
    event.preventDefault();
    setRegisterStatus("Укажите корректный телефон. Допустимы только цифры.", true);
    phoneInput?.focus();
    return;
  }

  phoneInput.value = formatPhone(phoneDigits);
});
