/**
 * Delegated handlers replacing inline event attributes.
 *
 * A Content-Security-Policy of `script-src 'self'` blocks inline handlers
 * (onclick=, onchange=, ...) even when a nonce is present — nonces only cover
 * <script> blocks. Markup declares intent through data- attributes instead,
 * and everything is wired up here from a single external file.
 *
 * Supported markup:
 *   data-confirm="message"            confirm before activating / submitting
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

    // ----- confirm-before-acting -------------------------------------------
    document.addEventListener(
        "click",
        function (event) {
            var el = closestWithAttr(event.target, "data-confirm");
            if (!el) {
                return;
            }
            if (!window.confirm(el.getAttribute("data-confirm"))) {
                event.preventDefault();
                event.stopPropagation();
            }
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
