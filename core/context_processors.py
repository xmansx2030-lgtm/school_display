"""Template context shared across the whole project."""

from __future__ import annotations


def csp_nonce(request):
    """Expose the per-response CSP nonce to templates.

    Inline scripts that cannot be moved into a static file should carry
    ``nonce="{{ csp_nonce }}"`` so they keep working once the Content-Security
    -Policy is enforced rather than merely reported.
    """
    return {"csp_nonce": getattr(request, "csp_nonce", "")}
