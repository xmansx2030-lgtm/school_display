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
      config.element = ".mysr-form";
      config.language = "ar";
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
