# Billing operations runbook

آخر تحديث: 2026-08-04

يغطي هذا المستند ضوابط الجانب التجاري: بوابة الدفع، التسوية، انتهاء
الاشتراكات، الاستردادات، وسجل التدقيق.

## 1. بوابات الدفع (ميسر وتمارا)

### قائمة تحقق قبل فتح المبيعات

```dotenv
MOYASAR_ENABLED=True
MOYASAR_LIVE_MODE=True                 # بدونها الدفع متاح للمدير الخارق فقط
MOYASAR_PUBLISHABLE_KEY=pk_live_...
MOYASAR_SECRET_KEY=sk_live_...
MOYASAR_WEBHOOK_SECRET=...             # إلزامي
MOYASAR_CALLBACK_BASE_URL=https://school-display.com
TRANSACTIONAL_EMAIL_ENABLED=True       # وإلا لن تصل الفواتير

TAMARA_ENABLED=True
TAMARA_API_BASE_URL=https://api.tamara.co
TAMARA_API_TOKEN=...
TAMARA_NOTIFICATION_TOKEN=...
TAMARA_CALLBACK_BASE_URL=https://school-display.com
```

`manage.py check --deploy` يرفض الإقلاع أو ينبّه عند أي نقص:

| المعرّف | المعنى |
|---|---|
| `subscriptions.E001` | ميسر مفعّلة بدون `MOYASAR_WEBHOOK_SECRET` |
| `subscriptions.E004` | تمارا مفعّلة بدون مفتاح API |
| `subscriptions.E005` | تمارا مفعّلة بدون مفتاح الإشعارات |
| `subscriptions.W001` | ميسر مفعّلة لكنها في وضع الاختبار — العملاء لا يستطيعون الدفع |
| `subscriptions.W003` | Google Pay مفعّل لكن `MOYASAR_GOOGLE_PAY_MERCHANT_ID` مفقود |
| `subscriptions.W002` | بوابة دفع مفعّلة والبريد المعاملاتي متوقف — الفواتير لن تُسلَّم |

### وسائل الدفع

`MOYASAR_PAYMENT_METHODS=creditcard,applepay,googlepay`

STC Pay مُخفاة حاليًا. لإعادتها أضف `stcpay` إلى القائمة في `.env.production` وأعد تشغيل `web` — لا يلزم تعديل كود، فهي ما تزال ضمن الوسائل المدعومة.

`MOYASAR_LIVE_MODE=True`
`MOYASAR_GOOGLE_PAY_MERCHANT_ID=...`

Google Pay يحتاج `MOYASAR_GOOGLE_PAY_MERCHANT_ID` قبل ظهوره في نموذج Moyasar.

Apple Pay needs the Moyasar dashboard domain registration and certificate to be
completed before it will actually validate in the browser.

> Apple Pay يتطلب توثيق النطاق من لوحة تحكم ميسر قبل أن يظهر الزر.

### إعداد webhook في تمارا

سجّل الرابط التالي من بوابة شركاء تمارا، واختر أحداث الطلب، وبالأخص
`order_approved`:

`https://school-display.com/subscriptions/tamara/webhook/`

الخادم يتحقق من JWT بمفتاح `TAMARA_NOTIFICATION_TOKEN`، ثم يؤكد الطلب
ويلتقط قيمة الاشتراك الرقمي. عامل التسوية يعالج أيضًا الحالات التي لم يصل
فيها webhook أو أغلق العميل المتصفح قبل صفحة العودة.

## 2. تسوية المدفوعات

**المشكلة التي تحلها:** عميل يدفع ثم يغلق المتصفح قبل صفحة العودة، ويفشل
الـ webhook أيضًا. الدفعة نجحت لدى ميسر ولم يُفعَّل الاشتراك لدينا.

الحاوية `moyasar-reconciliation-worker` تعمل كل دقيقة و:

1. تعيد جلب كل عملية لها `payment_id` وتتحقق منها.
2. تمسح دفعات ميسر وتطابقها بـ `metadata.merchant_reference` للعمليات التي لا
   تحمل `payment_id`. نطاق المسح يمتد ليغطي أقدم محاولة مفتوحة، فلا تفوت
   العامل دفعةٌ حدثت أثناء توقفه.
3. تُلغي المحاولات المهجورة بعد `MOYASAR_RECONCILIATION_LOOKBACK_HOURS`،
   **وفقط** بعد مسح مكتمل لسجل ميسر أثبت عدم وجود دفعة مقابلة. إذا فشل
   الاتصال بميسر لا يُلغى شيء، وتبقى المحاولة مفتوحة للدورة التالية.

> الإلغاء نهائي — الحالة `voided` لا تُراجَع مرة أخرى — لذلك لا يجوز إغلاق
> محاولة لم يُسأل عنها المزوّد أصلًا.

التحقق من الحالة:

```bash
docker compose -f compose.production.yaml exec moyasar-reconciliation-worker \
  python manage.py moyasar_reconciliation_worker --healthcheck

# تشغيل دورة واحدة يدويًا
docker compose -f compose.production.yaml run --rm web \
  python manage.py moyasar_reconciliation_worker --once
```

**التحقق من المبلغ إلزامي:** أي اختلاف في المبلغ أو العملة أو المرجع أو البيئة
يمنع التفعيل ويسجّل `reconcile_mismatch` على العملية للمراجعة اليدوية.

## 3. انتهاء الاشتراكات

الاشتراك لا ينتقل إلى `expired` من تلقاء نفسه عند مرور `ends_at`. المهمة
اليومية داخل `email-notification-worker` تتولى ذلك، ويمكن تشغيلها يدويًا:

```bash
# معاينة بدون تغيير
docker compose -f compose.production.yaml run --rm web \
  python manage.py expire_subscriptions --dry-run

docker compose -f compose.production.yaml run --rm web \
  python manage.py expire_subscriptions
```

ما ينفّذه:

1. `status` → `expired` مع ضبط `closed_at`.
2. مزامنة `School.is_active`.
3. تطبيق حد الشاشات (كل الشاشات تتوقف عند غياب اشتراك ساري).
4. إبطال كاش صلاحية الوصول.
5. كتابة سجل تدقيق لكل اشتراك.

## 4. بوابة العرض

`DISPLAY_REQUIRE_ACTIVE_SUBSCRIPTION=True` (الافتراضي) تمنع الشاشات من عرض
المحتوى بعد انتهاء الاشتراك:

- `/api/display/*` يرجع **402** مع `{"error": "subscription_inactive"}`.
- صفحة الشاشة تعرض قالب تجديد أنيق، وتُعيد المحاولة تلقائيًا كل ~5 دقائق.

الفحص مخزَّن مؤقتًا لكل مدرسة (`SUBSCRIPTION_ACCESS_CACHE_TTL=300`) ويُبطَل فورًا
عند أي تغيير في الاشتراك، فالتجديد يعيد التشغيل خلال دقائق دون تدخل.

> عند تشخيص عطل، يمكن تعطيلها مؤقتًا بـ
> `DISPLAY_REQUIRE_ACTIVE_SUBSCRIPTION=False` — لكنها تعني منح الخدمة مجانًا.

## 5. الاستردادات

من لوحة الإدارة: **استردادات الاشتراكات → إضافة**.

- لا يمكن أن يتجاوز مجموع الاستردادات قيمة عملية الدفع (مفروض على مستوى النموذج).
- الاستردادات بحالة `failed` لا تستهلك الرصيد القابل للاسترداد.
- `revokes_access=True` يُلغي الاشتراك المرتبط ويُبطل الكاش فورًا؛ اتركه فارغًا
  للاسترداد الجزئي أو بادرة حسن النية.
- حركة المال نفسها تُنفَّذ من لوحة ميسر؛ سجّل مرجعها في `gateway_reference`.

## 6. سجل التدقيق

`SubscriptionAuditLog` سجل **للقراءة فقط** (لا إضافة ولا تعديل ولا حذف من
اللوحة) يوثّق: من نفّذ، ماذا، بأي مبلغ، ومن أي عنوان IP.

الأحداث المسجّلة: `payment_recorded`، `payment_reconciled`،
`subscription_expired`، `refund_recorded`، `refund_completed`.

اسم المنفّذ محفوظ نصيًا أيضًا (`actor_label`) حتى يبقى السجل مفهومًا بعد حذف
الحساب.

## 7. توثيق البريد الإلكتروني

- يُرسل رابط موقّع عند التسجيل عبر نفس صندوق الصادر الدائم (لا يعطّل التسجيل
  إذا كان SMTP بطيئًا).
- **التوثيق مطلوب قبل الدفع** لضمان وصول الفاتورة.
- صفحة "اشتراكي" تعرض تنبيهًا وزر إعادة إرسال (مقيّد بدقيقتين).
- الرابط يبطل تلقائيًا إذا غيّر المستخدم بريده قبل فتحه.
