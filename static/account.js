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

function selectedPlanCode() {
  return document.querySelector('input[name="pricing_plan"]:checked')?.value || "";
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
  setPaymentStatus("Проверяем статус платежа…");
  const data = await postJson("/api/account/subscription/confirm", { invoiceId });
  const purchase = data.purchase || {};
  if (purchase.status === "paid") {
    setPaymentStatus("Оплата подтверждена. Обновите страницу, чтобы увидеть активный тариф.", "success");
    return;
  }

  setPaymentStatus(`Платёж пока в статусе: ${purchase.status || "unknown"}.`, "warning");
}

async function startSubscriptionCheckout(planCode) {
  if (!cloudpaymentsEnabled) {
    setPaymentStatus("CloudPayments пока не настроен.", "danger");
    return;
  }

  if (!planCode) {
    setPaymentStatus("Сначала выберите тариф.", "danger");
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
    const data = await postJson("/api/account/subscription/checkout", { plan_code: planCode });
    const checkout = data.checkout || {};
    const widget = new window.cp.CloudPayments({ language: "ru-RU" });
    closePlanModal();

    widget.pay(
      "charge",
      checkout,
      {
        onSuccess: async () => {
          try {
            await confirmSubscriptionPayment(checkout.invoiceId);
          } catch (error) {
            setPaymentStatus(error.message, "danger");
          }
        },
        onFail: (reason) => {
          setPaymentStatus(reason || "Оплата не прошла или была отменена.", "danger");
        },
        onComplete: (paymentResult) => {
          if (paymentResult && paymentResult.success === false) {
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

paymentHistoryToggle?.addEventListener("click", () => {
  if (!paymentHistoryList) {
    return;
  }
  paymentHistoryList.hidden = !paymentHistoryList.hidden;
  syncPaymentHistoryToggle();
});

syncPaymentHistoryToggle();
