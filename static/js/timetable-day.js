/**
 * Daily timetable editor.
 *
 * Three things this page could not tell you before, all of which cost a save
 * and a reload to discover:
 *
 *   1. How much of the day is actually filled in.
 *   2. That a period with a subject but no teacher is silently discarded by the
 *      server — the manager saves, the row comes back empty, and nothing says
 *      why. Those rows are flagged here, before the save.
 *   3. Whether there are unsaved edits at all.
 *
 * It also steps through classes with the arrow buttons, which is the motion you
 * make when filling a whole week.
 */
(function () {
  "use strict";

  function init() {
    initClassStepper();
    initEditor();
  }

  /* ---------------------------------------------------------------- classes */
  function initClassStepper() {
    var select = document.querySelector("[data-tt-class]");
    var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-tt-class-step]"));
    if (!buttons.length) return;

    if (!select || select.disabled || select.options.length < 2) {
      buttons.forEach(function (btn) { btn.classList.add("is-disabled"); });
      return;
    }

    function refresh() {
      buttons.forEach(function (btn) {
        var step = parseInt(btn.dataset.ttClassStep, 10);
        var next = select.selectedIndex + step;
        btn.classList.toggle("is-disabled", next < 0 || next >= select.options.length);
      });
    }

    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var step = parseInt(btn.dataset.ttClassStep, 10);
        var next = select.selectedIndex + step;
        if (next < 0 || next >= select.options.length) return;
        select.selectedIndex = next;
        // The select carries `data-autosubmit`, so a real change event is what
        // navigates — no need to duplicate the form submission here.
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
    });

    select.addEventListener("change", refresh);
    refresh();
  }

  /* ----------------------------------------------------------------- editor */
  function initEditor() {
    var form = document.querySelector("[data-tt-form]");
    if (!form) return;

    var rows = Array.prototype.slice.call(form.querySelectorAll("[data-tt-row]"));
    if (!rows.length) return;

    var bar = form.querySelector("[data-tt-bar]");
    var summary = form.querySelector("[data-tt-summary]");
    var stateLabel = form.querySelector("[data-tt-state]");
    var resetButton = form.querySelector("[data-tt-reset]");
    var saving = false;

    // Snapshot of what the server sent, so "undo" is exact and the dirty check
    // compares against the saved state rather than "did anything fire".
    var initial = rows.map(function (row) {
      return {
        subject: valueOf(row, "[data-tt-subject]"),
        teacher: valueOf(row, "[data-tt-teacher]"),
      };
    });

    function valueOf(row, selector) {
      var el = row.querySelector(selector);
      return el ? el.value : "";
    }

    function isDirty() {
      return rows.some(function (row, index) {
        return valueOf(row, "[data-tt-subject]") !== initial[index].subject
          || valueOf(row, "[data-tt-teacher]") !== initial[index].teacher;
      });
    }

    function render() {
      var filled = 0;
      var partial = 0;

      rows.forEach(function (row) {
        var subject = valueOf(row, "[data-tt-subject]");
        var teacher = valueOf(row, "[data-tt-teacher]");
        var complete = Boolean(subject) && Boolean(teacher);
        var half = Boolean(subject) !== Boolean(teacher);

        row.classList.toggle("is-filled", complete);
        row.classList.toggle("is-partial", half);
        if (complete) filled += 1;
        if (half) partial += 1;
      });

      var total = rows.length;
      if (bar) bar.style.width = total ? ((filled / total) * 100).toFixed(1) + "%" : "0%";

      if (summary) {
        var text = filled + " من " + total + " حصة مكتملة";
        if (partial) text += " · " + partial + " حصة ناقصة لن تُحفظ";
        summary.textContent = text;
      }

      if (stateLabel) {
        stateLabel.textContent = isDirty() ? "توجد تغييرات غير محفوظة" : "لا توجد تغييرات";
      }
      if (resetButton) {
        resetButton.disabled = !isDirty();
      }
    }

    form.addEventListener("change", function (event) {
      var target = event.target;
      if (!target || target.tagName !== "SELECT") return;

      // Picking a subject almost always means picking its teacher next, and
      // leaving only one of the two set is exactly the case the server drops.
      if (target.matches("[data-tt-subject]") && target.value) {
        var row = target.closest("[data-tt-row]");
        var teacher = row && row.querySelector("[data-tt-teacher]");
        if (teacher && !teacher.value) teacher.focus();
      }
      render();
    });

    form.addEventListener("click", function (event) {
      var clear = event.target.closest("[data-tt-clear]");
      if (!clear) return;
      var row = clear.closest("[data-tt-row]");
      if (!row) return;
      row.querySelectorAll("select").forEach(function (select) { select.value = ""; });
      render();
    });

    if (resetButton) {
      resetButton.addEventListener("click", function () {
        rows.forEach(function (row, index) {
          var subject = row.querySelector("[data-tt-subject]");
          var teacher = row.querySelector("[data-tt-teacher]");
          if (subject) subject.value = initial[index].subject;
          if (teacher) teacher.value = initial[index].teacher;
        });
        render();
      });
    }

    form.addEventListener("submit", function (event) {
      var partial = rows.filter(function (row) {
        return Boolean(valueOf(row, "[data-tt-subject]")) !== Boolean(valueOf(row, "[data-tt-teacher]"));
      });

      if (partial.length) {
        var proceed = window.confirm(
          partial.length + " حصة تحتوي على مادة بدون معلم (أو العكس) ولن يتم حفظها.\n\n"
          + "اضغط «إلغاء» لإكمالها الآن، أو «موافق» للحفظ وتجاهلها."
        );
        if (!proceed) {
          event.preventDefault();
          partial[0].scrollIntoView({ behavior: "smooth", block: "center" });
          return;
        }
      }
      saving = true;
    });

    window.addEventListener("beforeunload", function (event) {
      if (saving || !isDirty()) return;
      event.preventDefault();
      // Browsers show their own wording; a non-empty returnValue is the signal.
      event.returnValue = "";
    });

    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
}());
