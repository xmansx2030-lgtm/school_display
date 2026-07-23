# Content Security Policy rollout

The application sends a restrictive CSP in `Report-Only` mode by default. This records violations without breaking legacy pages that still contain inline scripts and event handlers.

## Current policy

- JavaScript is limited to same-origin static files.
- Objects are disabled and frame ancestors are same-origin only.
- Forms can submit only to the same origin.
- Camera, microphone, geolocation, payment and motion sensors are disabled through `Permissions-Policy`.
- Google Fonts, Font Awesome CSS, Cloudinary/HTTPS images and the privacy-enhanced YouTube player remain allowed where the current UI uses them.

Reports are sent to `/csp-report/`. The endpoint limits report volume, strips query strings from logged URLs and never stores the original request body.

## Enabling enforcement

1. Keep `CONTENT_SECURITY_POLICY_REPORT_ONLY=True` in production.
2. Review `csp_violation` logs for at least seven normal school days, including dashboard administration and TV display use.
3. Move remaining inline `<script>` blocks and HTML `on*` handlers into versioned files under `static/js/`.
4. Verify there are no required `script-src` violations for the supported flows.
5. Set `CONTENT_SECURITY_POLICY_REPORT_ONLY=False` and deploy to a limited school group first.
6. Revert to report-only immediately if a required interaction is blocked.

Do not add `'unsafe-eval'` to solve a third-party library problem. Prefer a local CSP-compatible build or a small native JavaScript replacement.
