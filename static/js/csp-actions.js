/**
 * Delegated handlers replacing inline event attributes.
 *
 * A Content-Security-Policy of `script-src 'self'` blocks inline handlers
 * (onclick=, onchange=, ...) even when a nonce is present — nonces only cover
 * <script> blocks. Markup declares intent through data- attributes instead,
 * and everything is wired up here from a single external file.
 *
 * Supported markup:
 *   data-confirm="message"            show an accessible confirmation dialog
 *   data-confirm-title="title"         optional dialog title
 *   data-confirm-action="label"        optional confirmation button label
 *   data-autosubmit                   submit the owning form on change
 *   data-action="print"               window.print()
 *   data-action="back"                history.back()
 *   data-action="scroll-to"           smooth-scroll to data-target selector
 *   data-action="click-target"        forward the click to data-target
 *   data-fallback="selector"          on <img> error: hide, reveal sibling
 */
(function () {
    "use strict";

    function closestWithAttr(node, attr) {
        var el = node;
        while (el && el.nodeType === 1) {
            if (el.hasAttribute && el.hasAttribute(attr)) {
                return el;
            }
            el = el.parentElement;
        }
        return null;
    }

    // ----- accessible confirm-before-acting -------------------------------
    var pendingConfirmation = null;
    var confirmedForms = new WeakSet();
    var bypassElements = new WeakSet();
    var confirmationDialog = null;
    var confirmationTitle = null;
    var confirmationMessage = null;
    var confirmationAction = null;

    function inferConfirmationCopy(el, message) {
        var explicitTitle = el.getAttribute("data-confirm-title");
        var explicitAction = el.getAttribute("data-confirm-action");
        var text = (message || "") + " " + (el.textContent || "");
        if (explicitTitle || explicitAction) {
            return {
                title: explicitTitle || "تأكيد الإجراء",
                action: explicitAction || "تأكيد",
                dangerous: /حذف|إلغاء|فصل|تراجع/.test(explicitAction || text)
            };
        }
        if (/حذف/.test(text)) {
            return { title: "تأكيد الحذف", action: "حذف نهائيًا", dangerous: true };
        }
        if (/إلغاء/.test(text)) {
            return { title: "تأكيد الإلغاء", action: "تأكيد الإلغاء", dangerous: true };
        }
        if (/فصل|فك ارتباط/.test(text)) {
            return { title: "فك ارتباط الجهاز", action: "فك الارتباط", dangerous: true };
        }
        return { title: "تأكيد الإجراء", action: "متابعة", dangerous: false };
    }

    function closeConfirmation() {
        if (!confirmationDialog) {
            return;
        }
        if (typeof confirmationDialog.close === "function" && confirmationDialog.open) {
            confirmationDialog.close();
        } else {
            confirmationDialog.removeAttribute("open");
        }
        var restoreFocus = pendingConfirmation && pendingConfirmation.focus;
        pendingConfirmation = null;
        if (restoreFocus && typeof restoreFocus.focus === "function") {
            restoreFocus.focus();
        }
    }

    function ensureConfirmationDialog() {
        if (confirmationDialog) {
            return confirmationDialog;
        }
        confirmationDialog = document.createElement("dialog");
        confirmationDialog.className = "app-confirm-dialog";
        confirmationDialog.setAttribute("aria-labelledby", "appConfirmTitle");
        confirmationDialog.setAttribute("aria-describedby", "appConfirmMessage");
        confirmationDialog.innerHTML = [
            '<section class="app-confirm-card">',
            '  <div class="app-confirm-icon" aria-hidden="true">!</div>',
            '  <div class="app-confirm-copy">',
            '    <span class="app-confirm-eyebrow">مراجعة قبل التنفيذ</span>',
            '    <h2 id="appConfirmTitle"></h2>',
            '    <p id="appConfirmMessage"></p>',
            '  </div>',
            '  <div class="app-confirm-actions">',
            '    <button type="button" class="app-confirm-cancel" data-confirm-cancel>تراجع</button>',
            '    <button type="button" class="app-confirm-submit" data-confirm-submit></button>',
            '  </div>',
            '</section>'
        ].join("");
        document.body.appendChild(confirmationDialog);
        confirmationTitle = confirmationDialog.querySelector("#appConfirmTitle");
        confirmationMessage = confirmationDialog.querySelector("#appConfirmMessage");
        confirmationAction = confirmationDialog.querySelector("[data-confirm-submit]");

        confirmationDialog.querySelector("[data-confirm-cancel]").addEventListener("click", closeConfirmation);
        confirmationDialog.addEventListener("cancel", function (event) {
            event.preventDefault();
            closeConfirmation();
        });
        confirmationDialog.addEventListener("click", function (event) {
            if (event.target === confirmationDialog) {
                closeConfirmation();
            }
        });
        confirmationAction.addEventListener("click", function () {
            var pending = pendingConfirmation;
            if (!pending) {
                return;
            }
            pendingConfirmation = null;
            if (typeof confirmationDialog.close === "function" && confirmationDialog.open) {
                confirmationDialog.close();
            } else {
                confirmationDialog.removeAttribute("open");
            }

            if (pending.form) {
                confirmedForms.add(pending.form);
                if (typeof pending.form.requestSubmit === "function") {
                    pending.form.requestSubmit(pending.submitter || undefined);
                } else {
                    pending.form.submit();
                }
                return;
            }
            if (pending.target && pending.target.tagName === "A") {
                window.location.assign(pending.target.href);
                return;
            }
            if (pending.target) {
                bypassElements.add(pending.target);
                pending.target.click();
            }
        });
        return confirmationDialog;
    }

    function openConfirmation(el, submitter) {
        var dialog = ensureConfirmationDialog();
        var message = el.getAttribute("data-confirm") || "هل تريد تنفيذ هذا الإجراء؟";
        var copy = inferConfirmationCopy(el, message);
        var form = el.tagName === "FORM" ? el : (el.form || el.closest("form"));
        pendingConfirmation = {
            target: el,
            form: form,
            submitter: submitter && submitter.form === form ? submitter : null,
            focus: submitter || el
        };
        confirmationTitle.textContent = copy.title;
        confirmationMessage.textContent = message;
        confirmationAction.textContent = copy.action;
        confirmationAction.classList.toggle("is-danger", copy.dangerous);
        if (typeof dialog.showModal === "function") {
            dialog.showModal();
        } else {
            dialog.setAttribute("open", "open");
        }
        confirmationAction.focus();
    }

    document.addEventListener(
        "click",
        function (event) {
            var el = closestWithAttr(event.target, "data-confirm");
            if (!el) {
                return;
            }
            if (bypassElements.has(el)) {
                bypassElements.delete(el);
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            var submitter = event.target.closest
                ? event.target.closest('button, input[type="submit"], input[type="image"]')
                : null;
            openConfirmation(el, submitter);
        },
        true
    );

    // Keyboard-based form submission does not always emit a click. Catch it
    // here so the confirmation cannot be bypassed by pressing Enter.
    document.addEventListener(
        "submit",
        function (event) {
            var form = event.target;
            if (confirmedForms.has(form)) {
                confirmedForms.delete(form);
                return;
            }
            var submitter = event.submitter || null;
            if (!submitter) {
                var confirmSubmitters = form.querySelectorAll(
                    'button[data-confirm], input[type="submit"][data-confirm], input[type="image"][data-confirm]'
                );
                if (confirmSubmitters.length === 1) {
                    submitter = confirmSubmitters[0];
                }
            }
            var el = submitter && submitter.hasAttribute("data-confirm")
                ? submitter
                : (form.hasAttribute("data-confirm") ? form : null);
            if (!el) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            openConfirmation(el, submitter);
        },
        true
    );

    // ----- declarative actions ---------------------------------------------
    document.addEventListener("click", function (event) {
        var el = closestWithAttr(event.target, "data-action");
        if (!el) {
            return;
        }
        var action = el.getAttribute("data-action");

        if (action === "print") {
            event.preventDefault();
            window.print();
            return;
        }

        if (action === "back") {
            event.preventDefault();
            window.history.back();
            return;
        }

        if (action === "scroll-to") {
            event.preventDefault();
            var target = document.querySelector(el.getAttribute("data-target") || "");
            if (target) {
                target.scrollIntoView({ behavior: "smooth" });
            }
            return;
        }

        if (action === "click-target") {
            event.preventDefault();
            var proxy = document.querySelector(el.getAttribute("data-target") || "");
            if (proxy) {
                proxy.click();
            }
        }
    });

    // ----- auto-submitting selects -----------------------------------------
    document.addEventListener("change", function (event) {
        var el = closestWithAttr(event.target, "data-autosubmit");
        if (el && el.form) {
            el.form.submit();
        }
    });

    // ----- image fallbacks --------------------------------------------------
    // Errors do not bubble, so this must be captured on the way down.
    document.addEventListener(
        "error",
        function (event) {
            var img = event.target;
            if (!img || img.tagName !== "IMG" || !img.hasAttribute("data-fallback")) {
                return;
            }
            img.style.display = "none";
            var sibling = img.nextElementSibling;
            if (sibling) {
                sibling.style.display = img.getAttribute("data-fallback") || "grid";
            }
        },
        true
    );
})();
