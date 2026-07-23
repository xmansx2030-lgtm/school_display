(function () {
  "use strict";

  function toggleFullscreen() {
    var active = document.fullscreenElement || document.webkitFullscreenElement;
    if (!active) {
      var root = document.documentElement;
      if (root.requestFullscreen) root.requestFullscreen();
      else if (root.webkitRequestFullscreen) root.webkitRequestFullscreen();
    } else if (document.exitFullscreen) {
      document.exitFullscreen();
    } else if (document.webkitExitFullscreen) {
      document.webkitExitFullscreen();
    }
  }

  function updateFullscreenIcon() {
    var active = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
    var expand = document.getElementById("fsIconExpand");
    var collapse = document.getElementById("fsIconCollapse");
    if (expand) expand.classList.toggle("u-hidden", active);
    if (collapse) collapse.classList.toggle("u-hidden", !active);
  }

  function showOldBrowserWarning() {
    if (!window.Promise || !window.fetch || !window.WebSocket) {
      var warning = document.createElement("div");
      warning.id = "old-browser-banner";
      warning.className = "old-browser-banner";
      var title = document.createElement("p");
      title.className = "old-browser-banner__title";
      title.textContent = "المتصفح قديم جداً";
      var details = document.createElement("p");
      details.className = "old-browser-banner__details";
      details.textContent = "هذا الجهاز لا يدعم واجهة العرض الذكية. يُوصى بتحديث المتصفح أو استخدام Chromium 80+ أو Chrome 80+.";
      warning.appendChild(title);
      warning.appendChild(details);
      document.body.appendChild(warning);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    showOldBrowserWarning();
    var fullscreenButton = document.getElementById("fullscreenBtn");
    var bellButton = document.getElementById("bellEnableBtn");
    if (fullscreenButton) fullscreenButton.addEventListener("click", toggleFullscreen);
    if (bellButton) {
      bellButton.addEventListener("click", function () {
        if (window._unlockBellFromBtn) window._unlockBellFromBtn();
      });
    }
    document.addEventListener("fullscreenchange", updateFullscreenIcon);
    document.addEventListener("webkitfullscreenchange", updateFullscreenIcon);
    updateFullscreenIcon();
  });
}());
