from __future__ import annotations

import re

from django import forms
from django.core.validators import validate_email


class ComposeMailForm(forms.Form):
    recipients = forms.CharField(
        label="إلى",
        max_length=2000,
        help_text="يمكن الفصل بين أكثر من عنوان بفاصلة.",
        widget=forms.TextInput(attrs={"placeholder": "customer@example.com", "dir": "ltr"}),
    )
    subject = forms.CharField(label="الموضوع", max_length=998)
    body = forms.CharField(
        label="نص الرسالة",
        max_length=20000,
        widget=forms.Textarea(attrs={"rows": 12}),
    )

    def clean_recipients(self) -> list[str]:
        raw = self.cleaned_data["recipients"]
        values = [item.strip().casefold() for item in re.split(r"[,;\n]+", raw) if item.strip()]
        if not values:
            raise forms.ValidationError("أدخل عنوان بريد واحدًا على الأقل.")
        unique: list[str] = []
        for value in values:
            try:
                validate_email(value)
            except forms.ValidationError as exc:
                raise forms.ValidationError(f"عنوان البريد غير صالح: {value}") from exc
            if value not in unique:
                unique.append(value)
        if len(unique) > 20:
            raise forms.ValidationError("الحد الأقصى 20 مستلمًا في الرسالة الواحدة.")
        return unique
