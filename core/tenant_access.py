from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


def authorized_active_school(profile, *, user=None, clear_invalid: bool = True):
    """Return the active school only when the profile is authorized for it.

    Superusers keep global access. For every other user the active school must
    also exist in the profile's ``schools`` relation. This closes the gap where
    the two relations can drift after an admin or data migration change.
    """
    if profile is None:
        return None

    active_school_id = getattr(profile, "active_school_id", None)
    if not active_school_id:
        return None

    if bool(getattr(user, "is_superuser", False)):
        return getattr(profile, "active_school", None)

    try:
        allowed = profile.schools.filter(pk=active_school_id).exists()
    except Exception:
        logger.exception(
            "tenant_access membership_check_failed user_id=%s school_id=%s",
            getattr(user, "pk", None),
            active_school_id,
        )
        return None

    if allowed:
        return getattr(profile, "active_school", None)

    logger.warning(
        "tenant_access invalid_active_school_cleared user_id=%s school_id=%s",
        getattr(user, "pk", None),
        active_school_id,
    )
    if clear_invalid:
        try:
            profile.active_school = None
            profile.save(update_fields=["active_school"])
        except Exception:
            logger.exception(
                "tenant_access invalid_active_school_clear_failed user_id=%s school_id=%s",
                getattr(user, "pk", None),
                active_school_id,
            )
    return None
