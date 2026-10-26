# School Display

منصة لوحة العرض المدرسية الذكية.

## التشغيل المحلي

بعد تثبيت المتطلبات وتهيئة البيئة وقاعدة البيانات، شغّل خادم ASGI لدعم
HTTP وWebSocket معًا:

```sh
python -m uvicorn config.asgi:application --reload
```

يستخدم الإنتاج Gunicorn مع Uvicorn أيضًا. لا يحتاج المشروع إلى Daphne أو
Twisted؛ ويظل أمر Django `runserver` مناسبًا لفحص HTTP فقط.

## المستودع الرسمي

المصدر الرسمي والوحيد للمشروع هو:

`https://github.com/xmansx2030-lgtm/school_display`

فرع الإنتاج هو `main`. يعمل فحص CI تلقائيًا عند الدفع إليه، بينما يبدأ النشر
إلى الإنتاج يدويًا من سير عمل **Deploy production** بعد نجاح CI.

الخادم لا يحتفظ بنسخة Git من المشروع؛ تُبنى حزمة إصدار ثابتة من الالتزام
المحدد، ثم تُرفع عبر SSH إلى `/opt/school-display/app`. راجع
[`docs/operations/deployments.md`](docs/operations/deployments.md) لإجراء النشر
والتحقق والتراجع.

ملفات البيئة وقواعد البيانات والوسائط المحلية مستثناة من Git، ولا يجوز رفع
الأسرار إلى المستودع.
