from __future__ import annotations

from django.core.management.base import BaseCommand

from subscriptions.trials import close_superseded_trials


class Command(BaseCommand):
    help = (
        "Trim free trials that a paid plan has already taken over, so a "
        "school that upgraded mid-trial stops counting down two terms at once."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--school",
            type=int,
            default=None,
            help="Limit the sweep to one school id.",
        )

    def handle(self, *args, **options):
        result = close_superseded_trials(
            options["school"],
            dry_run=bool(options["dry_run"]),
        )
        prefix = "would_trim" if options["dry_run"] else "trimmed"
        self.stdout.write(
            self.style.SUCCESS(
                f"trial_closure {prefix}={result.trimmed} "
                f"expired={result.expired} schools={result.schools}"
            )
        )
