/**
 * Dashboard home — live progress for the "now" block.
 *
 * The card shows which period is running and its window. What it could not show
 * is how far through it we are, which is the thing a manager actually glances
 * for. The bar fills from the server-supplied `remaining_seconds` rather than
 * from the browser clock: a phone in another timezone (or with a drifted clock)
 * would otherwise draw a confidently wrong bar.
 *
 * With no usable numbers the bar stays collapsed — an empty rail reads as
 * "nothing running", which is exactly the truth in that case.
 */
(function () {
  "use strict";

  var TICK_MS = 15000;

  function parseHhMm(value) {
    var match = /^(\d{1,2}):(\d{2})$/.exec((value || "").trim());
    if (!match) return null;
    var hours = parseInt(match[1], 10);
    var minutes = parseInt(match[2], 10);
    if (hours > 23 || minutes > 59) return null;
    return hours * 60 + minutes;
  }

  /** Length of the block in seconds, tolerating a window that crosses midnight. */
  function blockSeconds(root) {
    var from = parseHhMm(root.dataset.from);
    var to = parseHhMm(root.dataset.to);
    if (from === null || to === null) return 0;
    var minutes = to - from;
    if (minutes <= 0) minutes += 24 * 60;
    return minutes * 60;
  }

  function init() {
    var root = document.querySelector("[data-home-now]");
    if (!root) return;
    var bar = root.querySelector("[data-home-now-bar]");
    if (!bar) return;

    var total = blockSeconds(root);
    var remaining = parseInt(root.dataset.remaining || "", 10);
    if (!total || !isFinite(remaining) || remaining <= 0) return;

    // A remaining count larger than the block means the two numbers describe
    // different things; drawing anything from them would be a guess.
    if (remaining > total) return;

    var startedAt = Date.now();

    function render() {
      var elapsedSinceLoad = (Date.now() - startedAt) / 1000;
      var left = Math.max(0, remaining - elapsedSinceLoad);
      var percent = Math.min(100, Math.max(0, ((total - left) / total) * 100));
      bar.style.width = percent.toFixed(1) + "%";
      if (left <= 0 && timer) {
        window.clearInterval(timer);
        timer = null;
      }
    }

    var timer = window.setInterval(render, TICK_MS);
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
