const accountBody = document.body;
const cloudpaymentsEnabled = accountBody.dataset.cloudpaymentsEnabled === "true";
const buyButton = document.getElementById("subscription-buy-btn");
const statusNode = document.getElementById("subscription-status");
const planModal = document.getElementById("plan-modal");
const planModalCloseButton = document.getElementById("plan-modal-close");
const planModalCancelButton = document.getElementById("plan-modal-cancel");
const planModalSubmitButton = document.getElementById("plan-modal-submit");
const paymentHistoryToggle = document.getElementById("payment-history-toggle");
const paymentHistoryList = document.getElementById("payment-history-list");
const legalConsentBlock = document.getElementById("legal-consent-block");
const legalConsentCheckbox = document.getElementById("legal-consent-checkbox");
const recurringConsentBlock = document.getElementById("recurring-consent-block");
const recurringConsentCheckbox = document.getElementById("recurring-consent-checkbox");
const recurringConsentHint = document.getElementById("recurring-consent-hint");
const paymentFrequencyText = document.getElementById("payment-frequency-text");
const recurringConsentShortText = document.getElementById("recurring-consent-short-text");
const recurringConsentFullText = document.getElementById("recurring-consent-full-text");
const cancelSubscriptionButton = document.getElementById("subscription-cancel-btn");
const resumeSubscriptionButton = document.getElementById("subscription-resume-btn");

function selectedPlanInput() {
  return document.querySelector('input[name="pricing_plan"]:checked');
}

function selectedPlanCode() {
  return selectedPlanInput()?.value || "";
}

function selectedPlanMeta() {
  const input = selectedPlanInput();
  if (!input) {
    return null;
  }

  const periodDaysValue = Number.parseInt(input.dataset.planPeriodDays || "", 10);
  return {
    code: input.value || "",
    kind: input.dataset.planKind || "",
    name: input.dataset.planName || "",
    price: input.dataset.planPrice || "",
    currency: input.dataset.planCurrency || "",
    periodDays: Number.isFinite(periodDaysValue) ? periodDaysValue : 0,
  };
}

function planRequiresRecurringConsent(plan) {
  return Boolean(plan && plan.kind === "unlimited");
}

function formatRuDate(date) {
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
  }).format(date);
}

function getRecurringSchedule(plan) {
  const periodDays = plan?.periodDays > 0 ? plan.periodDays : 7;
  const firstChargeDate = new Date();
  const nextChargeDate = new Date(firstChargeDate);
  nextChargeDate.setDate(firstChargeDate.getDate() + periodDays);
  return {
    periodDays,
    firstChargeLabel: formatRuDate(firstChargeDate),
    nextChargeLabel: formatRuDate(nextChargeDate),
  };
}

function formatRecurringTerms(plan) {
  if (!plan) {
    return "Выберите тариф, чтобы увидеть условия списаний.";
  }

  if (!planRequiresRecurringConsent(plan)) {
    return `Тариф «${plan.name}» оплачивается разово без автоматических списаний.`;
  }

  const schedule = getRecurringSchedule(plan);
  return `Подписка «${plan.name}»: ${plan.price} ${plan.currency}, автосписание каждые ${schedule.periodDays} дней. Следующее списание — ${schedule.nextChargeLabel}.`;
}

function formatRecurringConsentShort(plan) {
  if (!plan || !planRequiresRecurringConsent(plan)) {
    return "Даю согласие на автоматические списания по условиям подписки.";
  }
  const schedule = getRecurringSchedule(plan);
  return `Даю согласие на автоматические списания ${plan.price} ${plan.currency} каждые ${schedule.periodDays} дней до отмены подписки.`;
}

function formatRecurringConsentFull(plan) {
  if (!plan || !planRequiresRecurringConsent(plan)) {
    return "Даю согласие на безакцептные рекуррентные списания с банковской карты, привязанной при оплате, в размере стоимости выбранного тарифа, с периодичностью выбранной подписки, начиная с даты первой оплаты, до отмены подписки. Подписку можно отменить до следующего списания в личном кабинете или через поддержку.";
  }

  const schedule = getRecurringSchedule(plan);
  return `Даю согласие на безакцептные рекуррентные списания: ${plan.price} ${plan.currency} 1 (один) раз в ${schedule.periodDays} календарных дней с банковской карты, привязанной при оплате, начиная с ${schedule.firstChargeLabel}, до отмены подписки. Следующее списание — ${schedule.nextChargeLabel}. Подписку можно отменить до следующего списания в личном кабинете или через поддержку.`;
}

function syncPlanPaymentTerms() {
  const plan = selectedPlanMeta();
  const requiresConsent = planRequiresRecurringConsent(plan);

  if (paymentFrequencyText) {
    paymentFrequencyText.textContent = formatRecurringTerms(plan);
  }
  if (legalConsentBlock) {
    legalConsentBlock.hidden = !requiresConsent;
  }
  if (recurringConsentBlock) {
    recurringConsentBlock.hidden = !requiresConsent;
  }
  if (recurringConsentShortText) {
    recurringConsentShortText.textContent = formatRecurringConsentShort(plan);
  }
  if (recurringConsentFullText) {
    recurringConsentFullText.textContent = formatRecurringConsentFull(plan);
  }
  if (recurringConsentHint) {
    recurringConsentHint.textContent = requiresConsent
      ? "Отменить автопродление можно по запросу на info@itd.dev или по телефону 89240254453 до следующего периода списания."
      : "Для разовых пакетов минут автоматические списания не применяются.";
  }
  if (legalConsentCheckbox) {
    legalConsentCheckbox.disabled = !requiresConsent;
  }
  if (!requiresConsent && legalConsentCheckbox) {
    legalConsentCheckbox.checked = false;
  }
  if (recurringConsentCheckbox) {
    recurringConsentCheckbox.disabled = !requiresConsent;
  }
  if (!requiresConsent && recurringConsentCheckbox) {
    recurringConsentCheckbox.checked = false;
  }
  if (planModalSubmitButton) {
    const legalAccepted = Boolean(legalConsentCheckbox?.checked);
    const recurringAccepted = Boolean(recurringConsentCheckbox?.checked);
    planModalSubmitButton.disabled = !plan || (requiresConsent && (!legalAccepted || !recurringAccepted));
  }
}

function setPaymentStatus(text, tone = "muted") {
  if (!statusNode) {
    return;
  }
  statusNode.textContent = text;
  statusNode.dataset.tone = tone;
}

async function postJson(url, payload = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    body: JSON.stringify(payload),
    credentials: "same-origin",
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    throw new Error(data.error || "Не удалось выполнить запрос.");
  }
  return data;
}

function openPlanModal() {
  if (!planModal) {
    return;
  }
  planModal.hidden = false;
  document.body.classList.add("modal-open");
}

function closePlanModal() {
  if (!planModal) {
    return;
  }
  planModal.hidden = true;
  document.body.classList.remove("modal-open");
}

function syncPaymentHistoryToggle() {
  if (!paymentHistoryToggle || !paymentHistoryList) {
    return;
  }
  paymentHistoryToggle.textContent = paymentHistoryList.hidden ? "История платежей" : "Скрыть историю платежей";
}

async function confirmSubscriptionPayment(invoiceId) {
  const attempts = 5;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    setPaymentStatus(
      attempt === 1
        ? "Проверяем статус платежа…"
        : "Ждём подтверждение подписки от CloudPayments…",
    );
    const data = await postJson("/api/account/subscription/confirm", { invoiceId });
    const purchase = data.purchase || {};
    const hasRecurringBinding = Boolean(purchase.cloudpayments_subscription_id || purchase.cloudpayments_token);
    if (purchase.status === "paid" && hasRecurringBinding) {
      setPaymentStatus("Оплата подтверждена, обновляем кабинет…", "success");
      window.setTimeout(() => window.location.reload(), 700);
      return;
    }
    if (purchase.status !== "paid") {
      setPaymentStatus(`Платёж пока в статусе: ${purchase.status || "unknown"}.`, "warning");
      return;
    }
    if (attempt < attempts) {
      await new Promise((resolve) => window.setTimeout(resolve, 1500));
    }
  }

  setPaymentStatus("Оплата подтверждена. Подписка появится в кабинете после обновления страницы.", "success");
  window.setTimeout(() => window.location.reload(), 1200);
}

async function startSubscriptionCheckout(planCode) {
  const plan = selectedPlanMeta();

  if (!cloudpaymentsEnabled) {
    setPaymentStatus("CloudPayments пока не настроен.", "danger");
    return;
  }

  if (!planCode) {
    setPaymentStatus("Сначала выберите тариф.", "danger");
    return;
  }

  if (planRequiresRecurringConsent(plan) && !legalConsentCheckbox?.checked) {
    setPaymentStatus("Подтвердите согласие с правовыми документами.", "danger");
    return;
  }

  if (planRequiresRecurringConsent(plan) && !recurringConsentCheckbox?.checked) {
    setPaymentStatus("Подтвердите согласие на условия продления подписки.", "danger");
    return;
  }

  if (!window.cp?.CloudPayments) {
    setPaymentStatus("Не удалось загрузить виджет CloudPayments.", "danger");
    return;
  }

  if (buyButton) {
    buyButton.disabled = true;
  }
  if (planModalSubmitButton) {
    planModalSubmitButton.disabled = true;
  }
  setPaymentStatus("Готовим форму оплаты…");

  try {
    const data = await postJson("/api/account/subscription/checkout", {
      plan_code: planCode,
      legal_consent: Boolean(legalConsentCheckbox?.checked),
      recurring_terms_consent: Boolean(recurringConsentCheckbox?.checked),
    });
    const checkout = data.checkout || {};
    const checkoutPayload = { ...checkout };
    if (checkout.recurrent) {
      checkoutPayload.recurrent = { ...checkout.recurrent };
      if (checkoutPayload.recurrent.startDateIso) {
        checkoutPayload.recurrent.startDate = new Date(checkoutPayload.recurrent.startDateIso);
        delete checkoutPayload.recurrent.startDateIso;
      }
    }
    const widget = new window.cp.CloudPayments({ language: "ru-RU" });
    closePlanModal();
    let paymentConfirmed = false;
    const confirmPaymentOnce = async () => {
      if (paymentConfirmed) {
        return;
      }
      paymentConfirmed = true;
      await confirmSubscriptionPayment(checkout.invoiceId);
    };

    widget.pay(
      "charge",
      checkoutPayload,
      {
        onSuccess: async () => {
          try {
            await confirmPaymentOnce();
          } catch (error) {
            setPaymentStatus(error.message, "danger");
          }
        },
        onFail: (reason) => {
          setPaymentStatus(reason || "Оплата не прошла или была отменена.", "danger");
        },
        onComplete: (paymentResult) => {
          if (paymentResult && paymentResult.success === true) {
            void confirmPaymentOnce().catch((error) => {
              setPaymentStatus(error.message, "danger");
            });
          } else if (paymentResult && paymentResult.success === false) {
            setPaymentStatus(paymentResult.message || "CloudPayments вернул отрицательный результат.", "danger");
          }
        },
      },
    );
  } catch (error) {
    setPaymentStatus(error.message, "danger");
  } finally {
    if (buyButton) {
      buyButton.disabled = false;
    }
    if (planModalSubmitButton) {
      planModalSubmitButton.disabled = false;
    }
  }
}

if (buyButton) {
  buyButton.addEventListener("click", () => {
    openPlanModal();
  });
}

planModalCloseButton?.addEventListener("click", closePlanModal);
planModalCancelButton?.addEventListener("click", closePlanModal);
planModal?.addEventListener("click", (event) => {
  if (event.target === planModal) {
    closePlanModal();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closePlanModal();
  }
});

planModalSubmitButton?.addEventListener("click", () => {
  void startSubscriptionCheckout(selectedPlanCode());
});

document.querySelectorAll('input[name="pricing_plan"]').forEach((input) => {
  input.addEventListener("change", syncPlanPaymentTerms);
});

recurringConsentCheckbox?.addEventListener("change", syncPlanPaymentTerms);
legalConsentCheckbox?.addEventListener("change", syncPlanPaymentTerms);

paymentHistoryToggle?.addEventListener("click", () => {
  if (!paymentHistoryList) {
    return;
  }
  paymentHistoryList.hidden = !paymentHistoryList.hidden;
  syncPaymentHistoryToggle();
});

cancelSubscriptionButton?.addEventListener("click", async () => {
  cancelSubscriptionButton.disabled = true;
  setPaymentStatus("Отключаем автопродление…");
  try {
    const data = await postJson("/api/account/subscription/cancel");
    setPaymentStatus(data.message || "Автопродление отключено.", "success");
    window.setTimeout(() => window.location.reload(), 1200);
  } catch (error) {
    setPaymentStatus(error.message, "danger");
  } finally {
    cancelSubscriptionButton.disabled = false;
  }
});

resumeSubscriptionButton?.addEventListener("click", async () => {
  resumeSubscriptionButton.disabled = true;
  setPaymentStatus("Возобновляем автопродление…");
  try {
    const data = await postJson("/api/account/subscription/resume");
    setPaymentStatus(data.message || "Автопродление снова включено.", "success");
    window.setTimeout(() => window.location.reload(), 1200);
  } catch (error) {
    setPaymentStatus(error.message, "danger");
  } finally {
    resumeSubscriptionButton.disabled = false;
  }
});

syncPaymentHistoryToggle();
syncPlanPaymentTerms();
