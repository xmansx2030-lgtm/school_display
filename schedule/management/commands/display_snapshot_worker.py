from __future__ import annotations

import logging
import time

from django.core.management.base import BaseCommand

from schedule.snapshot_materializer import (
    acquire_snapshot_job_lock,
    complete_snapshot_job,
    dequeue_snapshot_build,
    get_cached_snapshot_revision,
    get_latest_snapshot_revision,
    get_materialized_snapshot_revision,
    get_pending_snapshot_job_id,
    materialize_snapshot_for_school,
    release_snapshot_job_lock,
    snapshot_queue_available,
    touch_snapshot_worker_heartbeat,
)


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Materialize display snapshots from the Redis-backed build queue."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued job, then exit.")
        parser.add_argument("--poll-timeout", type=int, default=5, help="BLPOP timeout in seconds.")
        parser.add_argument("--idle-sleep", type=float, default=0.5, help="Sleep duration after empty polls.")
        parser.add_argument("--max-idle-sleep", type=float, default=2.0, help="Maximum progressive idle backoff.")
        parser.add_argument(
            "--idle-log-interval",
            type=int,
            default=300,
            help="Minimum seconds between idle log messages (default 300).",
        )

    def _emit(self, event: str, **fields):
        parts = [f"event={event}"]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        message = "snapshot_worker " + " ".join(parts)
        self.stdout.write(message)

    def handle(self, *args, **options):
        once = bool(options.get("once"))
        poll_timeout = max(0, int(options.get("poll_timeout") or 0))
        idle_sleep = max(0.1, float(options.get("idle_sleep") or 0.5))
        max_idle_sleep = max(idle_sleep, float(options.get("max_idle_sleep") or 2.0))
        idle_log_interval = max(30, int(options.get("idle_log_interval") or 300))
        worker_id = f"snapshot-worker:{int(time.time())}"
        current_idle_sleep = idle_sleep
        last_idle_log_at = 0.0

        if not snapshot_queue_available():
            self.stdout.write("snapshot_queue_available=False")
            if once:
                return

        self._emit(
            "worker_started",
            worker_id=worker_id,
            poll_timeout=poll_timeout,
            idle_sleep_s=f"{idle_sleep:.3f}",
            max_idle_sleep_s=f"{max_idle_sleep:.3f}",
        )
        while True:
            touch_snapshot_worker_heartbeat(worker_id=worker_id)
            job = dequeue_snapshot_build(block_timeout=poll_timeout)
            if not job:
                if once:
                    self._emit("worker_idle", worker_id=worker_id)
                    return
                now_monotonic = time.monotonic()
                if (now_monotonic - last_idle_log_at) >= idle_log_interval:
                    self._emit(
                        "worker_idle_sleep",
                        worker_id=worker_id,
                        sleep_s=f"{current_idle_sleep:.3f}",
                        poll_timeout=poll_timeout,
                    )
                    last_idle_log_at = now_monotonic
                time.sleep(current_idle_sleep)
                current_idle_sleep = min(max_idle_sleep, max(idle_sleep, current_idle_sleep * 2.0))
                continue

            current_idle_sleep = idle_sleep
            lock_key = ""
            lock_token = ""
            lock_acquired = False
            try:
                school_id = int(job.get("school_id") or 0)
                rev = int(job.get("rev") or 0)
                day_key = str(job.get("day_key") or "")
                job_id = str(job.get("job_id") or "")
                queued_at = float(job.get("queued_at") or 0.0)
                dequeued_at = float(job.get("_dequeued_at") or 0.0)
                queue_wait_ms = int(job.get("_queue_wait_ms") or 0)
                if job.get("_skip_complete"):
                    self._emit(
                        "job_locked_or_skipped",
                        worker_id=worker_id,
                        school_id=school_id,
                        rev=rev,
                        day_key=day_key,
                        job_id=job_id or "none",
                        reason=job.get("_skip_reason") or "skipped",
                        queue_wait_ms=queue_wait_ms,
                    )
                    if once:
                        return
                    continue

                latest_rev = get_latest_snapshot_revision(school_id=school_id, day_key=day_key)
                pending_job_id = get_pending_snapshot_job_id(school_id=school_id, day_key=day_key)
                if latest_rev is not None and latest_rev > rev:
                    if pending_job_id and job_id and pending_job_id != job_id:
                        self._emit(
                            "skip_stale",
                            worker_id=worker_id,
                            school_id=school_id,
                            job_rev=rev,
                            latest_rev=latest_rev,
                            day_key=day_key,
                            job_id=job_id,
                            pending_job_id=pending_job_id,
                            queue_wait_ms=queue_wait_ms,
                        )
                        if once:
                            return
                        continue
                    rev = int(latest_rev)
                    job["rev"] = rev

                lock_acquired, lock_key, lock_token = acquire_snapshot_job_lock(
                    school_id=school_id,
                    day_key=day_key,
                    rev=rev,
                    token=job_id or None,
                )
                if not lock_acquired:
                    self._emit(
                        "job_locked_or_skipped",
                        worker_id=worker_id,
                        school_id=school_id,
                        rev=rev,
                        day_key=day_key,
                        job_id=job_id or "none",
                        reason="duplicate_lock_busy",
                        queue_wait_ms=queue_wait_ms,
                    )
                    if once:
                        return
                    continue

                materialized_rev = get_materialized_snapshot_revision(school_id=school_id, day_key=day_key)
                if materialized_rev is not None and materialized_rev >= rev:
                    self._emit(
                        "skip_cached",
                        worker_id=worker_id,
                        school_id=school_id,
                        rev=rev,
                        cached_rev=materialized_rev,
                        day_key=day_key,
                        job_id=job_id or "none",
                        queue_wait_ms=queue_wait_ms,
                    )
                    if once:
                        return
                    continue

                cached_rev = get_cached_snapshot_revision(school_id=school_id, day_key=day_key)
                if cached_rev is not None and cached_rev >= rev:
                    self._emit(
                        "skip_cached",
                        worker_id=worker_id,
                        school_id=school_id,
                        rev=rev,
                        cached_rev=cached_rev,
                        day_key=day_key,
                        job_id=job_id or "none",
                        queue_wait_ms=queue_wait_ms,
                    )
                    if once:
                        return
                    continue

                self._emit(
                    "build_started",
                    worker_id=worker_id,
                    school_id=school_id,
                    rev=rev,
                    day_key=day_key,
                    job_id=job_id or "none",
                    queue_wait_ms=queue_wait_ms,
                )
                result = materialize_snapshot_for_school(
                    school_id=school_id,
                    rev=rev,
                    day_key=day_key,
                    queued_at=queued_at,
                    dequeued_at=dequeued_at,
                    source="worker",
                )
                self._emit(
                    "build_finished",
                    worker_id=worker_id,
                    school_id=school_id,
                    rev=rev,
                    day_key=day_key,
                    job_id=job_id or "none",
                    ok=int(bool(result.get("ok"))),
                    queue_wait_ms=int(result.get("queue_wait_ms") or queue_wait_ms or 0),
                    process_ms=int(result.get("process_ms") or 0),
                    result=result.get("reason") or ("built" if result.get("ok") else "failed"),
                )
            except Exception as exc:
                self._emit(
                    "errors",
                    worker_id=worker_id,
                    school_id=int(job.get("school_id") or 0),
                    rev=int(job.get("rev") or 0),
                    day_key=str(job.get("day_key") or ""),
                    job_id=str(job.get("job_id") or "none"),
                    error=exc.__class__.__name__,
                )
                logger.exception(
                    "snapshot_worker process_failed school_id=%s rev=%s day_key=%s job_id=%s",
                    job.get("school_id"),
                    job.get("rev"),
                    job.get("day_key"),
                    job.get("job_id"),
                )
            finally:
                if lock_acquired and lock_key and lock_token:
                    release_snapshot_job_lock(lock_key=lock_key, token=lock_token)
                complete_snapshot_job(job)

            if once:
                return
