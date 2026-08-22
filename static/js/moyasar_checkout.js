(function () {
  "use strict";

  function showLoadError() {
    var error = document.getElementById("moyasar-load-error");
    if (error) error.classList.remove("hidden");
  }

  function initializeMoyasar() {
    var configElement = document.getElementById("moyasar-config");
    if (!configElement || !window.Moyasar || typeof window.Moyasar.init !== "function") {
      showLoadError();
      return;
    }
    try {
      var config = JSON.parse(configElement.textContent || "{}");
      var paymentSyncUrl = config.payment_sync_url;
      delete config.payment_sync_url;
      config.element = ".mysr-form";
      config.language = "ar";
      if (paymentSyncUrl) {
        config.on_completed = async function (payment) {
          if (!payment || !payment.id) return;
          var csrfInput = document.querySelector('input[name="csrfmiddlewaretoken"]');
          try {
            await window.fetch(paymentSyncUrl, {
              method: "POST",
              credentials: "same-origin",
              headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfInput ? csrfInput.value : ""
              },
              body: JSON.stringify({ id: payment.id })
            });
          } catch (error) {
            // The return URL, webhook, and reconciliation worker remain
            // independent fallbacks. Never prevent Moyasar's redirect here.
          }
        };
      }
      window.Moyasar.init(config);
    } catch (error) {
      showLoadError();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeMoyasar, { once: true });
  } else {
    initializeMoyasar();
  }
})();
