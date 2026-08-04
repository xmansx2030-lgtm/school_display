/**
 * Subscription-inactive screen: unattended recheck.
 *
 * The TV showing this page has nobody standing in front of it. Reload on a
 * slow cadence so the normal display returns by itself once the school renews,
 * without a technician having to visit the screen.
 */
(function () {
    "use strict";

    var RECHECK_MINUTES = 5;
    var JITTER_SECONDS = 45;

    // Spread reloads across a fleet so a whole school does not hit the origin
    // in the same second after a renewal.
    var jitterMs = Math.floor(Math.random() * JITTER_SECONDS * 1000);
    var delayMs = RECHECK_MINUTES * 60 * 1000 + jitterMs;

    setTimeout(function () {
        window.location.reload();
    }, delayMs);
})();
