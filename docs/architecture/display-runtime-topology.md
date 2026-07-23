# Display Runtime Topology

آخر تحديث: 2026-07-23

## مصدر الحقيقة التشغيلي

الإنتاج يعمل على خادم Hetzner باستخدام Docker Compose. الملفات المعتمدة داخل المستودع هي:

- `Dockerfile`: بناء صورة التطبيق.
- `compose.production.yaml`: تعريف خدمات الإنتاج والشبكات والأحجام الدائمة.
- `deploy/Caddyfile`: TLS وتوجيه HTTP وWebSocket.
- `.env.production.example`: أسماء متغيرات البيئة المطلوبة فقط.
- `docs/operations/backups.md`: سياسة النسخ الاحتياطي والاستعادة.

ملف `.env.production` وأسرار الخادم لا تُحفظ في Git. رابط لوحة Hetzner ووضع Rescue أدوات إدارية للوصول إلى الخادم، وليسا جزءًا من إعداد التطبيق.

## الخدمات

| الخدمة | المسؤولية | الوصول |
| --- | --- | --- |
| `caddy` | HTTPS، الملفات الثابتة، وتمرير HTTP/WebSocket | المنافذ العامة 80 و443 |
| `web` | Django ASGI عبر Gunicorn وUvicorn | داخلي عبر `caddy` |
| `snapshot-worker` | بناء snapshots غير المتزامن | شبكة backend فقط |
| `wake-scheduler` | جدولة إيقاظ الشاشات | شبكة backend فقط |
| `postgres` | قاعدة البيانات الدائمة | شبكة backend فقط |
| `redis-cache` | كاش التطبيق وsnapshot بسياسة `allkeys-lru` | شبكة backend فقط |
| `redis-channels` | Channels وWebSocket queue بسياسة `noeviction` | شبكة backend فقط |
| `offsite-backup` | نسخ Restic مشفر خارج الخادم عند تفعيل profile العمليات | عند الطلب |

العاملان `snapshot-worker` و`wake-scheduler` خدمتان مستقلتان؛ لا يعملان كعمليات مضمّنة داخل خدمة `web`. لذلك يجب أن تبقى القيمتان التاليتان معطلتين في الإنتاج:

```env
DISPLAY_START_EMBEDDED_SNAPSHOT_WORKER=False
DISPLAY_START_EMBEDDED_WAKE_SCHEDULER=False
```

## تدفق الطلبات والتحديثات اللحظية

1. يستقبل `caddy` اتصال الشاشة عبر HTTPS أو WebSocket.
2. يمرر الاتصال إلى `web:8000`.
3. يقرأ التطبيق البيانات من PostgreSQL ويستخدم `redis-cache` للكاش.
4. ترسل تحديثات الشاشة عبر Channels باستخدام `redis-channels`.
5. يعالج `snapshot-worker` طلبات إعادة بناء snapshot بعيدًا عن عمليات الويب.
6. يشغل `wake-scheduler` مهام الإيقاظ الدورية بعيدًا عن عمليات الويب.

فصل Redis للكاش عن Redis للقنوات مقصود، ويمنع ضغط الكاش من التأثير مباشرة في بث WebSocket.

## التشغيل والنشر

من مجلد المشروع على خادم Hetzner:

```bash
docker compose -f compose.production.yaml pull
docker compose -f compose.production.yaml up -d --build
docker compose -f compose.production.yaml ps
```

تعرض السجلات حسب الخدمة:

```bash
docker compose -f compose.production.yaml logs --tail=200 web
docker compose -f compose.production.yaml logs --tail=200 snapshot-worker
docker compose -f compose.production.yaml logs --tail=200 wake-scheduler
docker compose -f compose.production.yaml logs --tail=200 caddy
```

كل الخدمات تستخدم Docker `local` logging driver مع تدوير افتراضي قدره
`20 MB × 5` لكل حاوية. كما تُستبعد طلبات الشاشة الدورية والملفات الثابتة من
سجل Caddy، ويُحتفظ بسجل أخطاء Gunicorn دون سجل وصول مكرر. يمكن تعديل الحدود
عبر `DOCKER_LOG_MAX_SIZE` و`DOCKER_LOG_MAX_FILES`.

يعرض Docker حالة العاملين المستقلين من خلال heartbeat مخزن في Redis:

```bash
docker compose -f compose.production.yaml ps snapshot-worker wake-scheduler
docker compose -f compose.production.yaml exec web python manage.py display_runtime_health snapshot-worker
docker compose -f compose.production.yaml exec web python manage.py display_runtime_health wake-scheduler
```

## التوسع واستهلاك الموارد

- زيادة `WEB_CONCURRENCY` ترفع قدرة معالجة HTTP/WebSocket، لكنها ترفع استهلاك الذاكرة واتصالات PostgreSQL وRedis.
- على خادم 2 vCPU يبقى `WEB_CONCURRENCY=2` ما لم يثبت اختبار الضغط الحاجة إلى غير ذلك.
- الخدمات المستقلة تمنع مهام snapshot والجدولة من مزاحمة عمليات الويب داخل العملية نفسها، لكنها تستهلك ذاكرة ثابتة إضافية.
- `compose.production.yaml` يضع حدود ذاكرة مستقلة قابلة للتعديل لكل خدمة، حتى لا تستهلك خدمة واحدة ذاكرة الخادم كاملة.
- يحتفظ `wake-scheduler` بحساب بداية اليوم في Redis حسب `schedule_revision`، ويفحص المدارس ذات الشاشات النشطة والمرتبطة فقط.
- فحص HTTP الاحتياطي للشاشة يعمل كل 60 ثانية عند سلامة WebSocket؛ التحديثات الفعلية تبقى فورية عبر WebSocket.
- صور ووسائط المدارس يجب أن تبقى في التخزين الخارجي المهيأ، لا في نظام ملفات الحاوية.
- عند زيادة المدارس، راقب CPU والذاكرة ومساحة PostgreSQL وعدد اتصالات WebSocket ومعدل Redis قبل زيادة عدد العمال.
- لا تضف نسخة ثانية من `wake-scheduler` إلا بعد توفير قفل موزع يمنع تنفيذ المهمة الدورية مرتين.

## النسخ الاحتياطي

النسخ المحلية وحدها لا تكفي لأن فقد الخادم قد يفقدها معه. اتبع `docs/operations/backups.md` لتشغيل نسخ PostgreSQL واختبار الاستعادة وتفعيل Restic إلى وجهة خارجية.
