/**
 * Client-side filter for an already-rendered list.
 *
 * These pages show one day at a time, so the whole list is on the page already
 * and a round trip to the server to find one name would be pure latency. The
 * filter matches on a `data-search` blob so a card can be found by any of the
 * fields it shows, and folds the Arabic spelling variants people type
 * interchangeably (أ/إ/ا, ى/ي, ة/ه, plus diacritics).
 *
 * Markup contract — `key` ties the pieces together:
 *   [data-list-search="key"]   the <input type="search">
 *   [data-list="key"]          the container
 *   [data-list-item]           each row/card, with data-search="…"
 *   [data-list-empty="key"]    shown when nothing matches (optional)
 *   [data-list-count="key"]    live "N من M" counter (optional)
 */
(function () {
  "use strict";

  var DIACRITICS = /[ً-ٰٟ]/g;
  var ALEF_FORMS = /[أإآ]/g;
  var ALEF_MAQSURA = /ى/g;
  var TEH_MARBUTA = /ة/g;

  function normalize(value) {
    return String(value || "")
      .replace(DIACRITICS, "")
      .replace(ALEF_FORMS, "ا")
      .replace(ALEF_MAQSURA, "ي")
      .replace(TEH_MARBUTA, "ه")
      .replace(/\s+/g, " ")
      .trim()
      .toLowerCase();
  }

  function initSearch(input) {
    var key = input.dataset.listSearch;
    if (!key) return;

    var list = document.querySelector('[data-list="' + CSS.escape(key) + '"]');
    if (!list) return;

    var items = Array.prototype.slice.call(list.querySelectorAll("[data-list-item]"));
    var empty = document.querySelector('[data-list-empty="' + CSS.escape(key) + '"]');
    var count = document.querySelector('[data-list-count="' + CSS.escape(key) + '"]');

    function apply() {
      var query = normalize(input.value);
      var visible = 0;

      items.forEach(function (item) {
        var haystack = normalize(item.dataset.search || item.textContent);
        var match = !query || haystack.indexOf(query) !== -1;
        item.hidden = !match;
        if (match) visible += 1;
      });

      if (empty) empty.hidden = visible !== 0 || items.length === 0;
      if (count) count.textContent = query ? visible + " من " + items.length : "";
    }

    input.addEventListener("input", apply);
    input.addEventListener("search", apply);
    apply();
  }

  function init() {
    if (typeof CSS === "undefined" || typeof CSS.escape !== "function") return;
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-list-search]"),
      initSearch
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
