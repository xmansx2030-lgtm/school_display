from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from subscriptions.expiry import expire_due_subscriptions


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Transition subscriptions past their end date to 'expired', resync school "
        "activation, re-apply screen limits and drop cached access answers."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        result = expire_due_subscriptions(dry_run=dry_run)

        prefix = "would_expire" if dry_run else "expired"
        self.stdout.write(
            self.style.SUCCESS(
                f"subscription_expiry {prefix}={result.expired} "
                f"schools_synced={result.schools_synced} "
                f"screens_disabled={result.screens_disabled}"
            )
        )
