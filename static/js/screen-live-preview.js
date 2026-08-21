(function () {
  "use strict";

  function initializeLivePreview() {
    var dialog = document.getElementById("livePreviewDialog");
    if (!dialog) return;

    var frame = dialog.querySelector("[data-live-preview-frame]");
    var stage = dialog.querySelector("[data-live-preview-stage]");
    var title = dialog.querySelector("#livePreviewTitle");
    var screenStatus = dialog.querySelector("[data-live-preview-screen-status]");
    var externalLink = dialog.querySelector("[data-live-preview-external]");
    var connection = dialog.querySelector("[data-live-preview-connection]");
    var connectionTitle = dialog.querySelector("[data-live-preview-connection-title]");
    var connectionDetail = dialog.querySelector("[data-live-preview-connection-detail]");
    var fullscreenButton = dialog.querySelector("[data-live-preview-fullscreen]");
    var currentUrl = "";
    var connectionState = "loading";

    function setConnectionState(state) {
      connectionState = state;
      connection.classList.remove("is-loading", "is-live", "is-polling");
      connection.classList.add("is-" + state);

      if (state === "live") {
        connectionTitle.textContent = "متصلة مباشرة بالتحديثات";
        connectionDetail.textContent = "أي تغيير في المحتوى سيظهر هنا فورًا";
      } else if (state === "polling") {
        connectionTitle.textContent = "المزامنة التلقائية فعّالة";
        connectionDetail.textContent = "سيتم جلب التغييرات تلقائيًا عند توفرها";
      } else {
        connectionTitle.textContent = "جارٍ الاتصال بالعرض…";
        connectionDetail.textContent = "ستظهر الشاشة بعد لحظات";
      }
    }

    function showDialog() {
      if (typeof dialog.showModal === "function") {
        if (!dialog.open) dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      document.body.classList.add("live-preview-open");
    }

    function openPreview(trigger) {
      currentUrl = trigger.getAttribute("data-preview-url") || trigger.getAttribute("href") || "";
      if (!currentUrl) return;

      title.textContent = trigger.getAttribute("data-preview-name") || "شاشة العرض";
      screenStatus.textContent = trigger.getAttribute("data-preview-status") || "جاهزة للمعاينة";
      externalLink.setAttribute("href", currentUrl);
      frame.setAttribute("title", "المعاينة الحية — " + title.textContent);
      stage.classList.add("is-loading");
      setConnectionState("loading");
      showDialog();
      frame.setAttribute("src", currentUrl);
    }

    function closePreview() {
      if (document.fullscreenElement && typeof document.exitFullscreen === "function") {
        document.exitFullscreen().catch(function () {});
      }
      if (typeof dialog.close === "function" && dialog.open) {
        dialog.close();
      } else {
        dialog.removeAttribute("open");
      }
    }

    function unloadPreview() {
      document.body.classList.remove("live-preview-open");
      currentUrl = "";
      frame.removeAttribute("src");
      stage.classList.add("is-loading");
      setConnectionState("loading");
    }

    document.querySelectorAll("[data-live-preview-open]").forEach(function (trigger) {
      trigger.addEventListener("click", function (event) {
        event.preventDefault();
        openPreview(trigger);
      });
    });

    dialog.querySelectorAll("[data-live-preview-close]").forEach(function (button) {
      button.addEventListener("click", closePreview);
    });

    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) closePreview();
    });
    dialog.addEventListener("close", unloadPreview);

    frame.addEventListener("load", function () {
      if (!frame.getAttribute("src")) return;
      stage.classList.remove("is-loading");
      if (connectionState === "loading") setConnectionState("polling");
    });

    dialog.querySelector("[data-live-preview-refresh]").addEventListener("click", function () {
      if (!currentUrl) return;
      stage.classList.add("is-loading");
      setConnectionState("loading");
      frame.removeAttribute("src");
      window.setTimeout(function () {
        if (currentUrl) frame.setAttribute("src", currentUrl);
      }, 80);
    });

    fullscreenButton.addEventListener("click", function () {
      if (document.fullscreenElement) {
        if (typeof document.exitFullscreen === "function") document.exitFullscreen().catch(function () {});
        return;
      }
      if (stage.requestFullscreen) stage.requestFullscreen().catch(function () {});
    });

    document.addEventListener("fullscreenchange", function () {
      var label = fullscreenButton.querySelector("span");
      if (label) label.textContent = document.fullscreenElement ? "إنهاء ملء الشاشة" : "ملء الشاشة";
      fullscreenButton.setAttribute(
        "title",
        document.fullscreenElement ? "إنهاء وضع ملء الشاشة" : "عرض المعاينة بملء الشاشة"
      );
    });

    window.addEventListener("message", function (event) {
      if (event.origin !== window.location.origin || event.source !== frame.contentWindow) return;
      var data = event.data || {};
      if (data.source !== "school-display-preview") return;
      if (data.state === "live") setConnectionState("live");
      if (data.state === "polling") setConnectionState("polling");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initializeLivePreview);
  } else {
    initializeLivePreview();
  }
})();
