# Dashboard architecture

The dashboard is split by security boundary and business responsibility:

- `dashboard/access.py` owns tenant resolution and system-role checks.
- `dashboard/decorators.py` is the only permission-decorator implementation.
- `dashboard/auth_views.py` owns password login, 2FA hand-off, logout and password changes.
- `dashboard/views_screens.py` owns screen lifecycle and remote screen commands.
- `dashboard/support_views.py` owns customer and system support tickets.
- `dashboard/views.py` currently owns the remaining school schedule and SaaS administration views.

`dashboard/permissions.py` and the imports exposed by `dashboard/views.py` are compatibility facades. New code must import from the owning module directly.

## Tenant rules

1. A non-superuser's `active_school` is valid only when it is also present in `profile.schools`.
2. School-owned objects must always be queried with the resolved school in the same database filter, for example `get_object_or_404(Model, pk=pk, school=school)`.
3. Support-group identities are system-level accounts and cannot pass `manager_required`, even if stale school relations exist.
4. Customer-owned support tickets must include `user=request.user` in detail/update queries.
5. New URLs should point to the owning view module; do not add forwarding wrappers to `dashboard/views.py`.

The regression tests in `dashboard/tests.py` enforce route ownership and the highest-risk tenant boundaries.
