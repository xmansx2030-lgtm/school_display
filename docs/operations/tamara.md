# Tamara checkout operations

The integration uses Tamara's server-side Checkout API. A browser redirect is
never treated as proof of payment. Subscription activation happens only after
Tamara confirms the order through a signed webhook or the authenticated Order
Details API, followed by authorisation.

## Required secrets

Set these values in `.env.production` or mount them as secret files:

```dotenv
TAMARA_ENABLED=True
TAMARA_API_BASE_URL=https://api-sandbox.tamara.co
TAMARA_API_TOKEN=...
TAMARA_NOTIFICATION_TOKEN=...
TAMARA_CALLBACK_BASE_URL=https://school-display.com
TAMARA_CAPTURE_DIGITAL_ORDERS=True
```

The API token and Notification token are different credentials. The API token
may instead be supplied with `TAMARA_API_TOKEN_FILE`; the notification signing
secret supports `TAMARA_NOTIFICATION_TOKEN_FILE`. Checkout and pull
reconciliation can run with the API token alone, but the Notification token is
still strongly recommended for immediate signed updates when the customer does
not return to the site.

## Partner Portal setup

Register this HTTPS webhook in Tamara Partner Portal:

```text
https://school-display.com/subscriptions/tamara/webhook/
```

Enable at least the approved, authorised, canceled, captured, refunded,
expired, and declined order events. The reconciliation worker recovers missed
redirects/webhooks by pulling pending order status. Digital subscriptions are
captured after authorisation when `TAMARA_CAPTURE_DIGITAL_ORDERS=True`; disable
it only when Tamara provides account-level auto-capture or requires a different
fulfilment point.

## Release sequence

1. Apply migrations and deploy with `TAMARA_ENABLED=False`.
2. Add the API token. Add the Notification token and register the sandbox
   webhook as soon as Tamara provides it.
3. Enable Tamara in sandbox and complete success, decline, cancel, duplicate
   webhook, delayed webhook, and retry tests.
4. Verify that one payment operation and one invoice are created per checkout.
5. Obtain Tamara production approval, change the base URL to
   `https://api.tamara.co`, replace the credentials, and repeat a low-value
   production smoke test.

Never copy either token into Git, templates, browser JavaScript, logs, or a
support ticket.

---

## ⛔ الحالة الآن: تمارا مخفية مؤقتًا

`TAMARA_TEMPORARILY_HIDDEN=True` (الافتراضي) يجبر `TAMARA_ENABLED` على `False`
مهما كانت قيمة متغير البيئة. النتيجة:

- لا تظهر تمارا في صفحة «اشتراكي» ولا في صفحة الأسعار العامة.
- `tamara/start/` يعيد التحويل برسالة، و`tamara/webhook/` يرد `503`.
- عامل التسوية `tamara-reconciliation-worker` خارج التشغيل (profile: `tamara`).
- ملف `tamara-checkout.css` لم يعد يُحمّل على صفحات لوحة التحكم.

**لم يُحذف شيء**: الكود والنماذج والبيانات المحفوظة والاختبارات كلها في مكانها،
واختبارات تمارا ما زالت تعمل عبر `override_settings(TAMARA_ENABLED=True)`.

### إعادة التفعيل

```bash
# 1) ارفع الإخفاء واضبط المفاتيح في .env
TAMARA_TEMPORARILY_HIDDEN=False
TAMARA_ENABLED=True
TAMARA_API_TOKEN=...
TAMARA_NOTIFICATION_TOKEN=...

# 2) أعد تشغيل عامل التسوية
docker compose -f compose.production.yaml --profile tamara up -d \
  tamara-reconciliation-worker
```

يحرس هذا الإخفاءَ صفٌّ من الاختبارات في
`subscriptions.tests.TamaraIsHiddenTests`؛ إذا عادت تمارا للظهور دون قصد فسيسقط.
