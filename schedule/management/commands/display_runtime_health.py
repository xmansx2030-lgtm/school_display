from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check liveness of a dedicated display background service."

    def add_arguments(self, parser):
        parser.add_argument(
            "component",
            choices=("snapshot-worker", "wake-scheduler"),
            help="Background component to check.",
        )

    def handle(self, *args, **options):
        component = str(options["component"])
        if component == "snapshot-worker":
            from schedule.snapshot_materializer import snapshot_worker_status

            status = snapshot_worker_status()
        else:
            from schedule.wake_broadcaster import wake_scheduler_status

            status = wake_scheduler_status()

        if not bool(status.get("alive")):
            age = status.get("age_sec")
            raise CommandError(f"{component} is not alive (age_sec={age})")

        self.stdout.write(
            self.style.SUCCESS(
                f"{component} is alive (age_sec={status.get('age_sec')}, worker_id={status.get('worker_id')})"
            )
        )
