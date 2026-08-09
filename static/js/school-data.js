/**
 * School data — classes, subjects, teachers.
 *
 * Setting up a school is a long typing session, so the page is tuned for that:
 *
 *   - The add field is a textarea styled as an input. Enter submits (fast
 *     single entry, unchanged), Shift+Enter adds a line, and a column pasted
 *     out of a spreadsheet posts as one batch instead of one reload per name.
 *   - After a save the server redirects back with ?added=<panel>, so the caret
 *     lands back in the field you were using rather than at the top of the page.
 *   - Long lists get a client-side filter; a school with sixty teachers should
 *     not have to scroll to check whether a name is already there.
 *   - Typing a name that already exists says so before you submit.
 */
(function () {
  "use strict";

  // The ?added= marker comes off the query string, so it is only ever compared
  // against this list — never interpolated straight into a selector.
  var PANELS = ["classes", "subjects", "teachers"];

  var DIACRITICS = /[ً-ٰٟ]/g;
  var ALEF_FORMS = /[أإآ]/g;   // أ إ آ
  var ALEF_MAQSURA = /ى/g;               // ى
  var TEH_MARBUTA = /ة/g;                // ة

  /** Fold the spelling variants Arabic typists use interchangeably. */
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

  function autoGrow(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 192) + "px";
  }

  function initAddField(panel) {
    var form = panel.querySelector("[data-sd-add]");
    var input = panel.querySelector("[data-sd-input]");
    if (!form || !input) return;

    var hint = form.querySelector(".sd-add__hint");
    var hintDefault = hint ? hint.innerHTML : "";

    var existing = Array.prototype.map.call(
      panel.querySelectorAll("[data-sd-item]"),
      function (item) { return normalize(item.dataset.name); }
    );

    function checkDuplicate() {
      if (!hint) return;
      var lines = input.value.split("\n");
      // Only worth warning about single-name entry; a pasted batch is
      // deduplicated server-side and reported back in the messages.
      var candidate = lines.length === 1 ? normalize(lines[0]) : "";
      if (candidate && existing.indexOf(candidate) !== -1) {
        hint.textContent = "هذا الاسم مضاف بالفعل — لن يتكرر عند الحفظ.";
        hint.style.color = "#a15c07";
      } else {
        hint.innerHTML = hintDefault;
        hint.style.color = "";
      }
    }

    input.addEventListener("input", function () {
      autoGrow(input);
      checkDuplicate();
    });

    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      if (!input.value.trim()) return;
      if (typeof form.requestSubmit === "function") {
        form.requestSubmit();
      } else {
        form.submit();
      }
    });

    autoGrow(input);
  }

  function initFilter(panel) {
    var search = panel.querySelector("[data-sd-search]");
    var list = panel.querySelector("[data-sd-list]");
    if (!search || !list) return;

    var items = Array.prototype.slice.call(list.querySelectorAll("[data-sd-item]"));
    var empty = list.querySelector("[data-sd-empty]");
    var count = panel.querySelector("[data-sd-count]");

    function apply() {
      var query = normalize(search.value);
      var visible = 0;
      items.forEach(function (item) {
        var match = !query || normalize(item.dataset.name).indexOf(query) !== -1;
        item.hidden = !match;
        if (match) visible += 1;
      });
      if (empty) empty.hidden = visible !== 0 || !items.length;
      if (count) {
        count.textContent = query
          ? visible + " من " + items.length          // "N من M"
          : items.length + " مُدخل";  // "M مُدخل"
      }
    }

    search.addEventListener("input", apply);
    apply();
  }

  function focusAddedPanel(root) {
    var added = root.dataset.added;
    if (!added || PANELS.indexOf(added) === -1) return;

    var panel = root.querySelector('[data-sd-panel="' + added + '"]');
    if (!panel) return;

    var input = panel.querySelector("[data-sd-input]");
    if (input) input.focus({ preventScroll: true });
    panel.scrollIntoView({ behavior: "smooth", block: "center" });

    panel.classList.add("is-highlighted");
    window.setTimeout(function () { panel.classList.remove("is-highlighted"); }, 1600);

    // Drop the marker so a refresh or a back-navigation does not re-run this.
    if (window.history.replaceState) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }

  function init() {
    var root = document.querySelector("[data-school-data]");
    if (!root) return;

    Array.prototype.forEach.call(root.querySelectorAll("[data-sd-panel]"), function (panel) {
      initAddField(panel);
      initFilter(panel);
    });

    focusAddedPanel(root);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
