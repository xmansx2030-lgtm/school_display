from __future__ import annotations

from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from core.models import SystemEmployeeProfile
from core.system_access import ROLE_SUPPORT, role_permissions

from .setup_support_role import SUPPORT_GROUP_NAME


class Command(BaseCommand):
    help = "Create a support staff user and add them to the Support group."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username for the support user")
        parser.add_argument("--email", default="", help="Email (optional)")
        parser.add_argument("--first-name", dest="first_name", default="", help="First name (optional)")
        parser.add_argument("--last-name", dest="last_name", default="", help="Last name (optional)")
        parser.add_argument(
            "--password",
            default=None,
            help="Password (optional). If omitted, you will be prompted.",
        )

    def handle(self, *args, **options):
        username: str = (options.get("username") or "").strip()
        if not username:
            raise CommandError("username is required")

        password = options.get("password")
        if password is None:
            p1 = getpass("Password: ")
            p2 = getpass("Password (again): ")
            if not p1:
                raise CommandError("Password cannot be empty")
            if p1 != p2:
                raise CommandError("Passwords do not match")
            password = p1

        UserModel = get_user_model()
        if UserModel.objects.filter(username=username).exists():
            raise CommandError(f"User '{username}' already exists")

        user = UserModel.objects.create_user(
            username=username,
            email=(options.get("email") or "").strip(),
            first_name=(options.get("first_name") or "").strip(),
            last_name=(options.get("last_name") or "").strip(),
            is_active=True,
            is_staff=True,
            is_superuser=False,
        )
        user.set_password(password)
        user.save(update_fields=["password"])

        group, _ = Group.objects.get_or_create(name=SUPPORT_GROUP_NAME)
        user.groups.add(group)
        platform_group, _ = Group.objects.get_or_create(name="Platform Staff")
        user.groups.add(platform_group)
        SystemEmployeeProfile.objects.create(
            user=user,
            role=ROLE_SUPPORT,
            permission_keys=role_permissions(ROLE_SUPPORT),
        )

        self.stdout.write(self.style.SUCCESS(f"Created support user: {username}"))
        self.stdout.write(self.style.SUCCESS(f"Added to group: {SUPPORT_GROUP_NAME}"))
        self.stdout.write(self.style.SUCCESS("Applied the support role and platform permissions."))
