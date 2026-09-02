# Billing operations runbook

آخر تحديث: 2026-08-22

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
TAMARA_ENVIRONMENT=production
TAMARA_API_BASE_URL=https://api.tamara.co
TAMARA_API_TOKEN=...
TAMARA_NOTIFICATION_TOKEN=...
TAMARA_CALLBACK_BASE_URL=https://school-display.com
```

لاستخدام حساب اختبار تمارا بشكل صريح:

```dotenv
TAMARA_ENVIRONMENT=sandbox
TAMARA_API_BASE_URL=https://api-sandbox.tamara.co
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

### إعداد webhook في ميسر

سجّل الرابط التالي في لوحة ميسر بطريقة `POST`:

`https://school-display.com/subscriptions/moyasar/webhook/`

اشترك على الأقل في `payment_paid` و`payment_captured` و`payment_failed`
و`payment_refunded` و`payment_voided`. يجب أن تطابق قيمة **Secret Token**
في لوحة ميسر قيمة `MOYASAR_WEBHOOK_SECRET` حرفيًا.

نموذج الدفع يحفظ رقم دفعة ميسر ويتحقق منها في `on_completed` قبل
الانتقال إلى 3DS. رابط العودة وwebhook وعامل التسوية تبقى قنوات
تأكيد مستقلة.

### إعداد webhook في تمارا

سجّل الرابط التالي من بوابة شركاء تمارا:

`https://school-display.com/subscriptions/tamara/webhook/`

اشترك في جميع أحداث الدورة: `order_approved`، `order_authorised`،
`order_captured`، `order_canceled`، `order_refunded`، `order_expired`
و`order_declined`.

الخادم يتحقق من JWT بمفتاح `TAMARA_NOTIFICATION_TOKEN`، ثم يؤكد الطلب
ويلتقط قيمة الاشتراك الرقمي. عامل التسوية يعالج أيضًا الحالات التي لم يصل
فيها webhook أو أغلق العميل المتصفح قبل صفحة العودة. العامل لا يسقط الطلبات
القديمة، ويدوّر الطلبات المفتوحة حتى لا يمنع طلب مهجور مراجعة دفعة حديثة.

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

### 3.1 التجربة المجانية بعد الترقية

شراء باقة مدفوعة يُنشئ صف اشتراك **جديدًا** ولا يعدّل التجربة المجانية الجارية،
فتبقى المدرسة تملك مدّتين ساريتين حتى نهاية التجربة. هذا كان يُنتج تنبيهات
«اقترب انتهاء اشتراكك» لعميل دفع فعلًا.

المعالجة على طبقتين:

1. **عند التفعيل المدفوع** (ميسر وتمارا) تُقصّر التجربة لتنتهي في اليوم السابق
   لبداية الباقة المدفوعة — بلا فقدان يوم واحد من التغطية — وتُغلق بسبب
   `upgraded` حتى لا تُحسب ضمن أسباب فقدان العملاء.
2. **عند توليد التنبيهات** لا يُرسل تنبيه قرب الانتهاء إذا كانت المدرسة تملك
   اشتراكًا آخر يغطي ما بعد ذلك التاريخ. هذا يشمل التجديد المبكر أيضًا، ويُحصّن
   الحالات القديمة الموجودة في قاعدة البيانات.

التجربة التي تحمل شاشات إضافية مدفوعة لا تُقصّر آليًا حتى لا تُلغى شاشات دفع
العميل ثمنها؛ تُترك للمراجعة اليدوية مع بقاء التنبيه صامتًا.

المسح يعمل يوميًا داخل `email-notification-worker`، ويمكن تشغيله يدويًا لمعالجة
الحالات المتراكمة:

```bash
# معاينة بدون تغيير
docker compose -f compose.production.yaml run --rm web   python manage.py close_superseded_trials --dry-run

docker compose -f compose.production.yaml run --rm web   python manage.py close_superseded_trials

# مدرسة واحدة فقط
docker compose -f compose.production.yaml run --rm web   python manage.py close_superseded_trials --school 42
```

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
