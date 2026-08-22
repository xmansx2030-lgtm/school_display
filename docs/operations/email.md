# تشغيل البريد عبر Resend

يستخدم المشروع النطاق الفرعي `mail.school-display.com` لعزل سمعة البريد عن
النطاق الرئيسي. عنوان الإرسال الافتراضي هو
`no-reply@mail.school-display.com`، وعنوان الرد والاستقبال هو
`support@mail.school-display.com`.

## الخدمات التي تعتمد على البريد

- فاتورة الاشتراك وتفاصيل الخطة بعد تفعيل الاشتراك.
- تنبيهات قرب انتهاء الاشتراك.
- التحقق من ملكية بريد العميل قبل الدفع.
- استعادة كلمة المرور.
- الرسائل اليدوية من مركز البريد في لوحة مدير المنصة.

رسائل الفواتير والاشتراكات تمر عبر صف
`SubscriptionEmailNotification` ويعالجها العامل `email-notification-worker`.
أما حالات التسليم والاستقبال فتصل من Resend إلى:

```text
https://school-display.com/api/mail/resend/webhook/
```

## إعداد Resend وDNS

يجب أن تكون سجلات DKIM وSPF وMX الخاصة بالإرسال بحالة Verified في Resend.
للاستقبال، أضف سجل MX التالي إلى Cloudflare مع تعطيل البروكسي:

```text
Type: MX
Name: mail
Priority: 10
Target: inbound-smtp.ap-northeast-1.amazonaws.com
```

اشترك في أحداث Webhook التالية:

```text
email.sent
email.delivered
email.delivery_delayed
email.bounced
email.failed
email.complained
email.suppressed
email.received
```

أنشئ مفتاحين منفصلين:

1. مفتاح Sending access مقيّد بالنطاق `mail.school-display.com`، ويستخدم
   كلمة مرور SMTP فقط.
2. مفتاح Full access للخادم، ويستخدم لاسترجاع محتوى الرسائل الواردة من API.

لا تضف المفاتيح إلى Git. خزّنها في `.env.production` على الخادم أو في ملفات
أسرار مقروءة بواسطة الحاويات فقط.

## متغيرات الإنتاج

القيم المطلوبة موثقة في `.env.production.example`. أهمها:

```text
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.resend.com
EMAIL_PORT=587
EMAIL_HOST_USER=resend
EMAIL_HOST_PASSWORD=<domain-restricted-sending-key>
EMAIL_USE_TLS=True
DEFAULT_FROM_EMAIL=لوحة العرض الذكية <no-reply@mail.school-display.com>
EMAIL_REPLY_TO=دعم لوحة العرض الذكية <support@mail.school-display.com>
RESEND_API_KEY=<separate-full-access-key>
RESEND_WEBHOOK_SECRET=<signing-secret-from-resend>
RESEND_INBOUND_ADDRESS=support@mail.school-display.com
RESEND_INBOUND_ENABLED=True
TRANSACTIONAL_EMAIL_ENABLED=True
```

## النشر والتحقق

بعد تحديث الأسرار ونشر الكود:

```bash
python manage.py migrate
python manage.py check --deploy --tag mailcenter
python manage.py test mailcenter subscriptions.tests.SubscriptionEmailNotificationTests dashboard.tests.PasswordResetTests
```

تحقق أيضاً من تشغيل عامل البريد ومن استجابة Webhook دون إعادة توجيه:

```bash
docker compose -f compose.production.yaml ps email-notification-worker
curl -i -X POST https://school-display.com/api/mail/resend/webhook/
```

يجب أن يعيد الطلب غير الموقّع `403`، وهذا يؤكد أن المسار متاح وأن التحقق من
توقيع Svix مفعل.

## اختبار القبول

1. أرسل رسالة من مركز البريد إلى عنوان يمكن الوصول إليه.
2. تأكد من انتقال الحالة من «تم الإرسال» إلى «تم التسليم» في لوحة المدير.
3. أرسل رسالة إلى `support@mail.school-display.com` وتأكد من ظهورها في الوارد.
4. اطلب استعادة كلمة المرور وتأكد من وصول الرسالة، ومن ظهور الحدث في المركز
   دون حفظ رابط الاستعادة أو محتوى الرسالة الأمنية.
5. فعّل اشتراكاً اختبارياً وتأكد من وصول الفاتورة وتفاصيل الخطة والمرفق.

## معالجة الأعطال

- `mailcenter.E001`: إعداد SMTP غير صحيح.
- `mailcenter.E002`: سر توقيع Webhook غير موجود.
- `mailcenter.E003`: الاستقبال مفعّل دون مفتاح Resend كامل الصلاحية.
- الرسائل الفاشلة في صف الاشتراكات يمكن إعادتها من تبويب الصادر في مركز
  البريد بعد معالجة السبب.
- لا تعِد إرسال رسالة فاشلة قبل مراجعة سبب الفشل أو الارتداد حتى لا تتكرر
  محاولات إلى عنوان غير صالح.
