/**
 * Day-at-a-time date filter, shared by the standby and duty pages.
 *
 * Both pages show one day's assignments and both used to need three actions to
 * move a day: open the picker, choose, press "apply". Stepping is the motion
 * people actually make, so it gets its own buttons, and any change submits on
 * the spot. The "apply" button stays in the markup for the no-JS case and is
 * hidden once this file has taken over.
 *
 * Markup contract:
 *   [data-date-filter]              the <form method="get">
 *     [data-date-input]             <input type="date" name="date">
 *     [data-date-step="-1|1"]       step one day
 *     [data-date-today]             jump to today
 *     [data-date-apply]             fallback submit button
 *     [data-date-label]             optional live "today / tomorrow / …" text
 */
(function () {
  "use strict";

  var DAY_MS = 86400000;

  /** Parse yyyy-mm-dd as a *local* date; `new Date(str)` would read it as UTC
   *  and land on the previous day for anyone east of Greenwich. */
  function parseISO(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec((value || "").trim());
    if (!match) return null;
    var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return isNaN(date.getTime()) ? null : date;
  }

  function formatISO(date) {
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return date.getFullYear() + "-" + month + "-" + day;
  }

  function startOfToday() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function relativeLabel(value) {
    var date = parseISO(value);
    if (!date) return "";
    var diff = Math.round((date - startOfToday()) / DAY_MS);
    if (diff === 0) return "اليوم";
    if (diff === 1) return "غدًا";
    if (diff === -1) return "أمس";
    if (diff > 1) return "بعد " + diff + " يوم";
    return "قبل " + Math.abs(diff) + " يوم";
  }

  function initForm(form) {
    var input = form.querySelector("[data-date-input]");
    if (!input) return;

    var label = form.querySelector("[data-date-label]");
    var apply = form.querySelector("[data-date-apply]");
    var todayButton = form.querySelector("[data-date-today]");

    function syncLabel() {
      if (label) label.textContent = relativeLabel(input.value);
      if (todayButton) {
        var isToday = input.value === formatISO(startOfToday());
        todayButton.classList.toggle("is-active", isToday);
        todayButton.disabled = isToday;
      }
    }

    function go(value) {
      // Re-submitting the day already on screen is a pointless round trip.
      if (!value || value === input.value) return;
      input.value = value;
      form.submit();
    }

    input.addEventListener("change", function () {
      if (input.value) form.submit();
    });

    form.addEventListener("click", function (event) {
      var step = event.target.closest("[data-date-step]");
      if (step) {
        event.preventDefault();
        var current = parseISO(input.value) || startOfToday();
        current.setDate(current.getDate() + parseInt(step.dataset.dateStep, 10));
        go(formatISO(current));
        return;
      }

      if (event.target.closest("[data-date-today]")) {
        event.preventDefault();
        go(formatISO(startOfToday()));
      }
    });

    // Only now that stepping works is the manual submit redundant.
    if (apply) apply.hidden = true;

    syncLabel();
  }

  function init() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-date-filter]"),
      initForm
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
