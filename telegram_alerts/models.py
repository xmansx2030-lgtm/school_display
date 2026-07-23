from __future__ import annotations

from django.db import models
from django.utils import timezone


class TelegramAlert(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "بانتظار الإرسال"
        PROCESSING = "processing", "قيد الإرسال"
        SENT = "sent", "تم الإرسال"
        FAILED = "failed", "فشل نهائيًا"

    event_type = models.CharField("نوع الحدث", max_length=64, db_index=True)
    dedupe_key = models.CharField(
        "مفتاح منع التكرار",
        max_length=255,
        unique=True,
    )
    message = models.TextField("نص التنبيه")
    action_url = models.URLField("رابط الإجراء", max_length=1000, blank=True)
    action_label = models.CharField("نص زر الإجراء", max_length=80, blank=True)
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    attempts = models.PositiveSmallIntegerField("عدد المحاولات", default=0)
    available_at = models.DateTimeField(
        "موعد المحاولة التالية",
        default=timezone.now,
        db_index=True,
    )
    locked_at = models.DateTimeField("وقت حجز الرسالة", null=True, blank=True)
    sent_at = models.DateTimeField("وقت الإرسال", null=True, blank=True)
    telegram_message_id = models.BigIntegerField(
        "معرّف رسالة تيليجرام",
        null=True,
        blank=True,
    )
    last_error = models.TextField("آخر خطأ", blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("آخر تحديث", auto_now=True)

    class Meta:
        verbose_name = "تنبيه تيليجرام"
        verbose_name_plural = "تنبيهات تيليجرام"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("status", "available_at"),
                name="tg_alert_status_due_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} ({self.get_status_display()})"
