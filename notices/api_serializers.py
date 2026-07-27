# notices/api_serializers.py
from django.conf import settings
from rest_framework import serializers

def _abs_url(request, path: str) -> str:
    """يبني رابطًا مطلقًا لمسار يبدأ بـ / أو مسار ساكن/وسائط."""
    if not path:
        return ""
    if path.startswith("http://") or path.startswith("https://") or path.startswith("//"):
        return path
    # يضمن وجود شرطة مبدئية
    if not path.startswith("/"):
        path = "/" + path
    return request.build_absolute_uri(path)

def _safe_file_url(request, val) -> str:
    """
    يحوّل قيمة صورة (FieldFile أو نص) إلى رابط مطلق آمن.
    - FieldFile: يعيد url إذا كان name موجودًا.
    - نص: يدعم http/https أو مسار نسبي يُركّب على MEDIA_URL.
    """
    if not val:
        return ""
    # FieldFile (ImageField/FileField)
    if hasattr(val, "url"):
        # إذا لا يوجد ملف مرتبط سيغيب name
        if getattr(val, "name", None):
            return request.build_absolute_uri(val.url)
        return ""
    # نص/مسار
    s = str(val).strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://") or s.startswith("//"):
        return s
    media = getattr(settings, "MEDIA_URL", "/media/")
    if not media.startswith("/"):
        media = "/" + media
    if not media.endswith("/"):
        media += "/"
    s = s.lstrip("./").lstrip("/")
    return request.build_absolute_uri(media + s)

def _default_avatar_url(request) -> str:
    """
    يعيد صورة افتراضية ثابتة. ضع ملفًا في static/img/teacher-placeholder.png
    إذا لم يوجد، سيستخدم SVG مضمّنًا كحل احتياطي.
    """
    static_url = getattr(settings, "STATIC_URL", "/static/")
    if not static_url.startswith("/"):
        static_url = "/" + static_url
    if not static_url.endswith("/"):
        static_url += "/"
    candidate = static_url + "img/teacher-placeholder.png"
    try:
        return request.build_absolute_uri(candidate)
    except Exception:
        # SVG خفيف افتراضي
        return (
            "data:image/svg+xml;utf8,"
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 128 128'>"
            "<defs><linearGradient id='g' x1='0' x2='1'><stop stop-color='%23b4c3ff'/>"
            "<stop offset='1' stop-color='%238aa2ff'/></linearGradient></defs>"
            "<rect width='128' height='128' fill='url(%23g)'/>"
            "<circle cx='64' cy='48' r='26' fill='white' fill-opacity='.9'/>"
            "<rect x='22' y='78' width='84' height='34' rx='14' fill='white' fill-opacity='.9'/>"
            "</svg>"
        )

class AnnouncementSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    level = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField(allow_blank=True, required=False)
    occasion_theme = serializers.CharField(allow_blank=True, required=False)
    starts_at = serializers.DateTimeField(required=False)
    ends_at = serializers.DateTimeField(required=False)

class ExcellenceSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    reason = serializers.CharField(allow_blank=True, required=False)
    teacher_name = serializers.SerializerMethodField()
    photo_url = serializers.SerializerMethodField()

    def _teacher(self, obj):
        return getattr(obj, "teacher", None)

    def get_teacher_name(self, obj):
        t = self._teacher(obj)
        if t and getattr(t, "name", None):
            return t.name
        for attr in ("teacher_name", "name"):
            v = getattr(obj, attr, None)
            if v:
                return v
        return ""

    def get_photo_url(self, obj):
        """
        أولوية الربط:
        1) teacher.photo / teacher.image / teacher.image_url / teacher.photo_url
        2) حقول الكائن نفسه (image, photo, image_url, photo_url, avatar)
        3) صورة افتراضية
        """
        request = self.context.get("request")

        # 1) من المعلم/ـة المرتبط (الأولوية القصوى لضمان التطابق مع الاسم)
        t = self._teacher(obj)
        if t:
            for attr in ("photo", "image", "image_url", "photo_url"):
                url = _safe_file_url(request, getattr(t, attr, None))
                if url:
                    return url

        # 2) من الكائن نفسه
        for attr in ("image", "photo", "image_url", "photo_url", "avatar"):
            url = _safe_file_url(request, getattr(obj, attr, None))
            if url:
                return url

        # 3) افتراضي
        return _default_avatar_url(request)
