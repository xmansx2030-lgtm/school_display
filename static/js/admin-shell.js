(function () {
  "use strict";

  function initializeClock() {
    var timeElement = document.getElementById("current-time");
    var dateElement = document.getElementById("current-date");
    if (!timeElement || !dateElement) return;
    function update() {
      var now = new Date();
      timeElement.textContent = now.toLocaleTimeString("ar-SA", {
        hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: true
      });
      dateElement.textContent = now.toLocaleDateString("ar-SA", {
        weekday: "long", year: "numeric", month: "long", day: "numeric"
      });
    }
    update();
    window.setInterval(update, 1000);
  }

  function initializeSidebar() {
    var sidebar = document.getElementById("adminSidebar");
    var overlay = document.getElementById("adminSidebarOverlay");
    var openButton = document.getElementById("adminSidebarOpenBtn");
    var closeButton = document.getElementById("adminSidebarCloseBtn");
    if (!sidebar || !overlay) return;

    function isDesktop() { return window.matchMedia("(min-width: 1024px)").matches; }
    function setOpen(open) {
      var effectiveOpen = isDesktop() || Boolean(open);
      sidebar.classList.toggle("translate-x-full", !effectiveOpen);
      sidebar.classList.toggle("translate-x-0", effectiveOpen);
      overlay.classList.toggle("hidden", isDesktop() || !effectiveOpen);
      if (openButton) openButton.setAttribute("aria-expanded", String(!isDesktop() && effectiveOpen));
    }
    if (openButton) openButton.addEventListener("click", function () { setOpen(true); });
    if (closeButton) closeButton.addEventListener("click", function () { setOpen(false); });
    overlay.addEventListener("click", function () { setOpen(false); });
    document.addEventListener("keydown", function (event) { if (event.key === "Escape") setOpen(false); });
    window.addEventListener("resize", function () { setOpen(false); });
    setOpen(false);
  }

  function initializeGlobalSearch() {
    var searchInput = document.getElementById("adminGlobalSearch");
    if (!searchInput) return;
    document.addEventListener("keydown", function (event) {
      if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === "k") {
        event.preventDefault();
        searchInput.focus();
        searchInput.select();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initializeClock();
    initializeSidebar();
    initializeGlobalSearch();
    var animated = document.querySelector(".animate-fade-in");
    if (animated) animated.style.animation = "fadeIn 0.5s ease-out";
    var printButton = document.getElementById("adminPrintBtn");
    var reloadButton = document.getElementById("adminReloadBtn");
    if (printButton) printButton.addEventListener("click", function () { window.print(); });
    if (reloadButton) reloadButton.addEventListener("click", function () { window.location.reload(); });
  });
}());
