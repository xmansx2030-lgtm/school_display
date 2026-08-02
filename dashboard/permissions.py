"""Backward-compatible permission imports.

New code should import from :mod:`dashboard.decorators`. This module remains so
older application modules do not silently fall back to plain ``login_required``.
"""

from .decorators import (
    manager_or_system_permission_required,
    manager_required,
    superuser_required,
    system_permission_required,
    system_staff_required,
)


superadmin_required = superuser_required

__all__ = [
    "manager_required",
    "manager_or_system_permission_required",
    "superadmin_required",
    "superuser_required",
    "system_permission_required",
    "system_staff_required",
]
