/**
 * Service worker registration for the display page.
 *
 * Lives in its own file rather than inline so the page satisfies a
 * `script-src 'self'` Content-Security-Policy with no nonce or hash needed.
 */
(function () {
    "use strict";

    if (!("serviceWorker" in navigator)) {
        return;
    }

    window.addEventListener("load", function () {
        navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {
            // A screen that cannot register the worker still displays normally.
        });
    });
})();
