from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from core.two_factor import reset_two_factor


class Command(BaseCommand):
    help = "Disable and remove 2FA configuration for one user after identity verification."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--confirm", action="store_true")

    def handle(self, *args, **options):
        if not options.get("confirm"):
            raise CommandError("Pass --confirm after verifying the user's identity.")
        username = str(options["username"] or "").strip()
        try:
            user = get_user_model().objects.get(username=username)
        except get_user_model().DoesNotExist as exc:
            raise CommandError("User not found.") from exc
        reset_two_factor(user)
        self.stdout.write(self.style.SUCCESS(f"2FA reset for {username}."))
