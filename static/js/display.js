(function () {
  "use strict";

  // ===========================================================================
  // ===== Compatibility Polyfills (APIs missing on old TV browsers)
  // ===========================================================================
  // The bundle is built with `--target=es2015` (see package.json), so async
  // functions are lowered to generators and nothing here needs a syntax level
  // above ES2015. That puts the floor at const/let and URLSearchParams —
  // Chrome 49 — which covers Samsung Tizen 3.0/4.0 (Chrome 56) and LG webOS 4
  // (Chrome 53). Anything below that (webOS 3.x on Chrome 38, Tizen 2.4 on
  // Chrome 47) cannot run this bundle at all.
  //
  // The APIs below post-date Chrome 49, so they are filled in here:
  //   • Promise.prototype.finally  → added Chrome 63  (breaks ALL fetch calls)
  //   • String.prototype.padStart  → added Chrome 57  (breaks accent-color theme)
  //   • Object.entries             → added Chrome 54  (breaks Arabic digits)
  //
  // These polyfills must come first so every function below can rely on them.
  // Written in ES5 syntax intentionally to survive even older engines.
  // ===========================================================================

  // Polyfill: Promise.prototype.finally
  // Absence causes withTimeout() / safeFetchSnapshot() / safeFetchStatus() to
  // throw "is not a function" on every fetch, making the screen stuck forever.
  if (typeof Promise !== "undefined" && typeof Promise.prototype.finally !== "function") {
    Promise.prototype.finally = function finallyPolyfill(onFinally) {
      var P = this.constructor || Promise;
      return this.then(
        function (value) {
          return P.resolve(typeof onFinally === "function" ? onFinally() : undefined).then(function () {
            return value;
          });
        },
        function (reason) {
          return P.resolve(typeof onFinally === "function" ? onFinally() : undefined).then(function () {
            throw reason;
          });
        }
      );
    };
  }

  // Polyfill: String.prototype.padStart
  // Absence causes rgbToHex() to throw, breaking custom accent-color theming.
  if (typeof String.prototype.padStart !== "function") {
    String.prototype.padStart = function padStartPolyfill(targetLength, padString) {
      var str = String(this);
      var fill = padString === undefined || padString === null ? " " : String(padString);
      var needed = (targetLength | 0) - str.length;
      if (needed <= 0 || !fill) return str;
      var result = "";
      while (result.length < needed) result += fill;
      return result.slice(0, needed) + str;
    };
  }

  // Polyfill: Object.entries
  // Absence throws inside the Arabic-digit mapping, which runs on every clock
  // tick, so the board would stop updating its time on Chrome 49-53.
  if (typeof Object.entries !== "function") {
    Object.entries = function entriesPolyfill(obj) {
      var out = [];
      for (var key in obj) {
        if (Object.prototype.hasOwnProperty.call(obj, key)) out.push([key, obj[key]]);
      }
      return out;
    };
  }

  // ===========================================================================
  // ===== Helpers =====
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;

  const safeText = (x) => (x === null || x === undefined ? "" : String(x));
  const fmt2 = (n) => {
    n = Number(n) || 0;
    return n < 10 ? "0" + n : String(n);
  };
  const clamp = (n, a, b) => {
    n = Number(n) || 0;
    return Math.max(a, Math.min(b, n));
  };

  function screenSetting(name, fallback) {
    try {
      const value = document.body && document.body.dataset
        ? document.body.dataset[name]
        : undefined;
      if (value === undefined || value === null || value === "") return fallback;
      return String(value);
    } catch (e) {
      return fallback;
    }
  }

  function screenFlag(name, fallback) {
    const value = screenSetting(name, fallback ? "1" : "0").toLowerCase();
    return value !== "0" && value !== "false" && value !== "no" && value !== "off";
  }

  function setTextIfChanged(el, next) {
    if (!el) return false;
    const v = safeText(next);
    if (el.textContent !== v) {
      el.textContent = v;
      return true;
    }
    return false;
  }

  function toggleHidden(el, hidden) {
    if (!el) return;
    // Use a dedicated hide utility to avoid CSS-lint conflicts with Tailwind display classes (e.g., flex).
    // Keep backward compatibility by toggling both.
    if (hidden) {
      el.classList.add("u-hidden");
      el.classList.add("hidden");
    } else {
      el.classList.remove("u-hidden");
      el.classList.remove("hidden");
    }
  }

  function toArabicDigits(v) {
    if (v === null || v === undefined) return "";
    return String(v).replace(/\d/g, (d) => "٠١٢٣٤٥٦٧٨٩"[Number(d)]);
  }

  function normSpeed(x, def) {
    const v = Number(x);
    if (!isFinite(v) || v <= 0) return def;
    return clamp(v, 0.15, 4);
  }

  function isDebug() {
    try {
      return new URLSearchParams(window.location.search).get("debug") === "1";
    } catch (e) {
      return false;
    }
  }

  /**
   * Does this browser honour `gap` on a flex container?
   *
   * Chrome shipped `gap` for grid in 57 but only for flex in 84, and `@supports
   * (gap: 1px)` returns true across that whole range — the property exists, it
   * just does nothing in flex. So the only reliable test is to measure it: a
   * two-item column flex box with a 1px gap is 2px tall without support and 3px
   * tall with it. The result feeds `body[data-nogap="1"]`, which
   * display-legacy.css turns into margin-based spacing.
   */
  var _flexGapSupported = null;
  function supportsFlexGap() {
    if (_flexGapSupported !== null) return _flexGapSupported;
    try {
      var probe = document.createElement("div");
      probe.style.display = "flex";
      probe.style.flexDirection = "column";
      probe.style.rowGap = "1px";
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      probe.appendChild(document.createElement("div"));
      probe.appendChild(document.createElement("div"));
      document.body.appendChild(probe);
      _flexGapSupported = probe.scrollHeight === 1;
      probe.parentNode.removeChild(probe);
    } catch (e) {
      // If the probe cannot run, assume support: adding margins to a browser
      // that already honours gap would double every spacing on the board.
      _flexGapSupported = true;
    }
    return _flexGapSupported;
  }

  function isLiteMode() {
    try {
      const qs = new URLSearchParams(window.location.search);
      const v = (qs.get("lite") || qs.get("liteMode") || "").trim().toLowerCase();
      if (v === "1" || v === "true" || v === "yes") return true;
      if (v === "0" || v === "false" || v === "no") return false;
    } catch (e) {}

    // The server already resolved lite mode from the User-Agent and rendered it
    // into the body, so the heavy design never painted. Honour that decision
    // instead of re-deciding it and flipping the attribute back to "0".
    try {
      var serverLite = document.body && document.body.dataset
        ? String(document.body.dataset.lite || "").trim()
        : "";
      if (serverLite === "1") return true;
    } catch (e) {}

    // ✅ كشف شاشات التلفاز الذكية بناءً على User Agent
    try {
      const ua = String(navigator.userAgent || "").toLowerCase();
      if (
        ua.includes("smarttv") ||
        ua.includes("hbbtv") ||
        ua.includes("tizen") ||
        ua.includes("web0s") ||
        ua.includes("webos") ||
        ua.includes("netcast") ||
        ua.includes("viera") ||
        ua.includes("nettv") ||
        ua.includes("philipstv") ||
        ua.includes("googletv") ||
        ua.includes("crkey") ||
        ua.includes("firetv") ||
        ua.includes("aftm") ||
        ua.includes("aftt") ||
        ua.includes("roku") ||
        ua.includes("vidaa") ||
        ua.includes("saphi")
      ) {
        return true;
      }
    } catch (e) {}

    // ✅ كشف الأجهزة الضعيفة بناءً على عدد الأنوية والذاكرة
    try {
      const hc = Number(navigator.hardwareConcurrency || 0);
      if (hc && hc <= 2) return true;
    } catch (e) {}

    try {
      const mem = Number(navigator.deviceMemory || 0);
      if (mem && mem <= 2) return true;
    } catch (e) {}

    // ✅ كشف الشاشات الكبيرة مع معالج ضعيف (تلفازات بـ 4 أنوية لكن GPU ضعيف)
    try {
      const sw = Number(window.screen && window.screen.width || 0);
      const sh = Number(window.screen && window.screen.height || 0);
      const hc = Number(navigator.hardwareConcurrency || 0);
      const mem = Number(navigator.deviceMemory || 0);
      // شاشة كبيرة (1080p+) مع أقل من 4GB ذاكرة → غالباً تلفاز
      if (sw >= 1920 && sh >= 1080 && mem > 0 && mem <= 4) return true;
      // شاشة كبيرة مع 4 أنوية فقط وبدون ماوس → غالباً تلفاز
      if (sw >= 1920 && sh >= 1080 && hc > 0 && hc <= 4) {
        try {
          // لا يوجد pointer دقيق = ليس حاسوب عادي
          if (window.matchMedia && window.matchMedia("(pointer: coarse)").matches) return true;
          if (window.matchMedia && !window.matchMedia("(hover: hover)").matches) return true;
        } catch (e) {}
      }
    } catch (e) {}

    return false;
  }

  // ===== Compat: nullish-coalesce helper (TV browsers < Chrome 80) =====
  function _nc(a, b) { return (a !== null && a !== undefined) ? a : b; }

  // ===== DOM =====
  const dom = {};
  function bindDom() {
    // Prefer scaling the stage (if present) so fixed-position UI (e.g. fullscreen button)
    // remains truly fixed to the viewport.
    dom.fitStage = document.getElementById("fitStage");
    dom.fitViewport = document.getElementById("fitRoot");
    dom.fitRoot = dom.fitStage || dom.fitViewport;

    dom.schoolLogo = $("schoolLogo");
    dom.schoolLogoFallback = $("schoolLogoFallback");
    dom.schoolName = $("schoolName");
    dom.dateG = $("dateGregorian");
    dom.dateH = $("dateHijri");
    dom.clock = $("clock");
    dom.boardStatusText = $("boardStatusText");

    dom.alertContainer = $("alertContainer");
    dom.alertTitle = $("alertTitle");
    dom.alertDetails = $("alertDetails");
    dom.occasionThemeDecor = $("occasionThemeDecor");
    dom.occasionThemeBadge = $("occasionThemeBadge");
    dom.occasionThemeBadgeIcon = $("occasionThemeBadgeIcon");
    dom.occasionThemeBadgeLabel = $("occasionThemeBadgeLabel");
    dom.occasionThemeSymbolOne = $("occasionThemeSymbolOne");
    dom.occasionThemeSymbolTwo = $("occasionThemeSymbolTwo");
    dom.occasionHero = $("occasionHero");
    dom.occasionHeroIcon = $("occasionHeroIcon");
    dom.occasionHeroLabel = $("occasionHeroLabel");
    dom.occasionHeroTitle = $("occasionHeroTitle");
    dom.occasionHeroBody = $("occasionHeroBody");
    dom.occasionHeroTagline = $("occasionHeroTagline");

    dom.badgeKind = $("badgeKind");
    dom.heroRange = $("heroRange");
    dom.heroTitle = $("heroTitle");
    dom.activityLabel = $("activityLabel");
    dom.currentScheduleList = $("currentScheduleList");

    dom.circleProgress = $("circleProgress");
    dom.countdown = $("countdown");
    dom.countdownGlyph = $("countdownGlyph");
    dom.countdownLabel = $("countdownLabel");
    dom.progressBar = $("progressBar");

    dom.miniSchedule = $("miniSchedule");
    dom.nextLabel = $("nextLabel");
    dom.dayTimelinePanel = $("dayTimelinePanel");

    dom.exSlot = $("exSlot");
    dom.exIndex = $("exIndex");
    dom.exTotal = $("exTotal");

    dom.exCard = $("exCard");
    dom.dutyCard = $("dutyCard");
    dom.dutySlot = $("dutySlot");
    dom.dutyTrack = $("dutyTrack");
    dom.dutyTotal = $("dutyTotal");

    dom.pcCount = $("pcCount");
    dom.periodClassesTrack = $("periodClassesTrack");
    dom.periodCard = $("periodCard");

    dom.sbCount = $("sbCount");
    dom.standbyTrack = $("standbyTrack");
    dom.standbyCard = $("standbyCard");

    // Blocking overlay
    dom.blocker = $("blocker");
    dom.blockerTitle = $("blockerTitle");
    dom.blockerDetails = $("blockerDetails");
    dom.blockerLink = $("blockerLink");
    dom.blockerStatusText = $("blockerStatusText");
    dom.blockerEyebrow = $("blockerEyebrow");
    dom.blockerGuideTitle = $("blockerGuideTitle");
    dom.blockerGuideIntro = $("blockerGuideIntro");
    dom.blockerStepOne = $("blockerStepOne");
    dom.blockerStepTwo = $("blockerStepTwo");
    dom.blockerStepThree = $("blockerStepThree");
    dom.blockerFootnote = $("blockerFootnote");
    dom.blockerRetryBtn = $("blockerRetryBtn");

    if (dom.blockerRetryBtn && !dom.blockerRetryBtn.dataset.bound) {
      dom.blockerRetryBtn.dataset.bound = "1";
      dom.blockerRetryBtn.addEventListener("click", function () {
        try {
          window.location.reload();
        } catch (e) {
          window.location.href = window.location.href;
        }
      });
    }

    // Robust logo fallback (in case a provided logo URL 404s)
    if (dom.schoolLogo && !dom.schoolLogo.dataset._fallbackBound) {
      dom.schoolLogo.dataset._fallbackBound = "1";
      dom.schoolLogo.addEventListener(
        "error",
        () => {
          const fallback =
            safeText(dom.schoolLogo.getAttribute("data-fallback-src")) ||
            safeText(dom.schoolLogo.getAttribute("data-fallback")) ||
            "";
          if (!fallback) return;
          // avoid infinite loop if fallback also fails
          if (dom.schoolLogo.src && dom.schoolLogo.src.indexOf(fallback) >= 0) return;
          dom.schoolLogo.src = fallback;
        },
        listenerOpts({ passive: true })
      );
    }
  }

  // ===== Blocking overlay helpers =====
  let isBlocked = false;
  function showBlocker(title, details, kind) {
    isBlocked = true;
    var blockerKind = safeText(kind || "").toLowerCase();
    if (blockerKind !== "binding" && blockerKind !== "device" && blockerKind !== "access") {
      var blockerText = (safeText(title) + " " + safeText(details)).toLowerCase();
      blockerKind = /جهاز آخر|مربوط|مرتبطة|مفعّلة/.test(blockerText) ? "binding" : "access";
    }
    if (dom.blocker) dom.blocker.setAttribute("data-kind", blockerKind);
    if (dom.blockerTitle) dom.blockerTitle.textContent = safeText(title);
    if (dom.blockerDetails) dom.blockerDetails.textContent = safeText(details);
    if (dom.blockerLink) {
      var screenId = safeText(document.body && document.body.dataset ? document.body.dataset.screenId : "");
      dom.blockerLink.textContent = screenId ? "شاشة رقم " + screenId : "شاشة العرض الحالية";
    }

    var copy = blockerKind === "binding" ? {
      status: "الارتباط محمي",
      eyebrow: "حماية ارتباط الشاشة",
      guideTitle: "لتفعيلها على هذا الجهاز",
      guideIntro: "نفّذ الخطوات التالية من حساب مسؤول المدرسة:",
      stepOne: "افتح لوحة التحكم من هاتف أو جهاز كمبيوتر.",
      stepTwo: "انتقل إلى شاشات العرض واختر هذه الشاشة.",
      stepThree: "اضغط فك ارتباط الجهاز، ثم أعد التحقق هنا.",
      footnote: "لن تحتاج إلى إنشاء رابط جديد بعد فك الارتباط.",
    } : blockerKind === "device" ? {
      status: "الجهاز غير معرّف",
      eyebrow: "إعداد جهاز العرض",
      guideTitle: "لإكمال إعداد هذا الجهاز",
      guideIntro: "تحقق من إعداد الشاشة بهذه الخطوات:",
      stepOne: "أعد فتح رابط الشاشة الأساسي على هذا الجهاز.",
      stepTwo: "تأكد من استخدام نافذة متصفح عادية وليست خاصة.",
      stepThree: "إذا استمرت المشكلة، أعد ربط الشاشة من لوحة التحكم.",
      footnote: "سيبدأ العرض تلقائيًا بعد التحقق من الجهاز.",
    } : {
      status: "العرض متوقف مؤقتًا",
      eyebrow: "تنبيه شاشة العرض",
      guideTitle: "للتحقق من إعداد الشاشة",
      guideIntro: "يمكن لمسؤول المدرسة مراجعة ما يلي:",
      stepOne: "تحقق من فتح رابط شاشة العرض الصحيح.",
      stepTwo: "راجع حالة الشاشة من قسم شاشات العرض.",
      stepThree: "أعد تحميل الصفحة بعد تحديث الإعدادات.",
      footnote: "لن تتأثر بيانات الجدول أو محتوى المدرسة.",
    };
    if (dom.blockerStatusText) dom.blockerStatusText.textContent = copy.status;
    if (dom.blockerEyebrow) dom.blockerEyebrow.textContent = copy.eyebrow;
    if (dom.blockerGuideTitle) dom.blockerGuideTitle.textContent = copy.guideTitle;
    if (dom.blockerGuideIntro) dom.blockerGuideIntro.textContent = copy.guideIntro;
    if (dom.blockerStepOne) dom.blockerStepOne.textContent = copy.stepOne;
    if (dom.blockerStepTwo) dom.blockerStepTwo.textContent = copy.stepTwo;
    if (dom.blockerStepThree) dom.blockerStepThree.textContent = copy.stepThree;
    if (dom.blockerFootnote) dom.blockerFootnote.textContent = copy.footnote;

    if (blockerKind === "binding") {
      if (dom.blockerStepOne) dom.blockerStepOne.innerHTML = "افتح <b>لوحة التحكم</b> من هاتف أو جهاز كمبيوتر.";
      if (dom.blockerStepTwo) dom.blockerStepTwo.innerHTML = "انتقل إلى <b>شاشات العرض</b> واختر هذه الشاشة.";
      if (dom.blockerStepThree) dom.blockerStepThree.innerHTML = "اضغط <b>فك ارتباط الجهاز</b>، ثم أعد التحقق هنا.";
    }
    toggleHidden(dom.blocker, false);
  }
  function stopPolling() {
    clearNamedTimer("poll");
    if (ctrl) {
      try {
        ctrl.abort();
      } catch (e) {}
    }
  }

  // ===== Viewport var =====
  function setVhVar() {
    // Not needed anymore - using native 100vh
    return;
  }

  // ===== Professional Auto-fit for TV displays =====
  function isFitDisabled() {
    try {
      const qs = new URLSearchParams(window.location.search);
      const v = (qs.get("fit") || qs.get("autofit") || "").trim().toLowerCase();
      if (v === "0" || v === "false" || v === "no") return true;
    } catch (e) {}
    return false;
  }

  function getFitMargin() {
    // Optional safe margin for overscan TVs.
    // Default is full-fill (1.0). Use ?fitMargin=0.95 when needed.
    let m = 1.0;
    try {
      const qs = new URLSearchParams(window.location.search);
      const raw = (qs.get("fitMargin") || qs.get("margin") || "").trim();
      if (raw) {
        const v = parseFloat(raw);
        if (isFinite(v)) m = v;
      }
    } catch (e) {}
    return clamp(m, 0.7, 1);
  }

  function getViewportSize() {
    let w = Number(window.innerWidth || 0);
    let h = Number(window.innerHeight || 0);
    try {
      const vv = window.visualViewport;
      if (vv && isFinite(Number(vv.width)) && isFinite(Number(vv.height))) {
        const vw = Number(vv.width || 0);
        const vh = Number(vv.height || 0);
        // Use visualViewport only when it looks sane and beneficial.
        if (vw > 0 && vh > 0 && (Math.abs(vw - w) > 2 || Math.abs(vh - h) > 2)) {
          w = vw;
          h = vh;
        }
      }
    } catch (e) {}
    return { width: w, height: h };
  }

  function getFitMaxScale() {
    // Allow scaling UP on large TVs (e.g., 4K) to keep UI readable.
    // Can be overridden via ?fitMax=1.6
    let mx = 2;
    try {
      const qs = new URLSearchParams(window.location.search);
      const raw = (qs.get("fitMax") || qs.get("maxScale") || "").trim();
      if (raw) {
        const v = parseFloat(raw);
        if (isFinite(v)) mx = v;
      }
    } catch (e) {}
    return clamp(mx, 1, 3);
  }

  function getFitMode() {
    // ✅ الحل 2: Parameter يدوي للتحكم في وضع الملء
    // ?fitMode=contain → show all content (للشاشات غير القياسية)
    // ?fitMode=cover → fill screen (الوضع الافتراضي)
    try {
      const qs = new URLSearchParams(window.location.search);
      const v = (qs.get("fitMode") || "").trim().toLowerCase();
      if (v === "contain") return "contain";
      if (v === "cover") return "cover";
    } catch (e) {}
    return "cover"; // الافتراضي - لا يتغير السلوك الحالي
  }

  function getAspectThreshold() {
    // ✅ الحل 3: threshold للكشف التلقائي عن الشاشات غير القياسية
    // ?aspectThreshold=0.2 → أكثر تساهلاً
    // الافتراضي: 0.15 (15% فرق)
    let threshold = 0.15;
    try {
      const qs = new URLSearchParams(window.location.search);
      const raw = (qs.get("aspectThreshold") || "").trim();
      if (raw) {
        const v = parseFloat(raw);
        if (isFinite(v) && v > 0) threshold = v;
      }
    } catch (e) {}
    return clamp(threshold, 0.05, 0.5);
  }

  let fitT = null;
  function scheduleFit(ms) {
    if (fitT) clearTimeout(fitT);
    fitT = setTimeout(() => {
      fitT = null;
      applyAutoFit();
    }, Math.max(0, Number(ms) || 0));
  }

  function applyAutoFit() {
    if (!dom.fitRoot) return;

    // allow disabling via ?fit=0 for troubleshooting
    if (isFitDisabled()) {
      try {
        dom.fitRoot.style.transform = "scale(1)";
        dom.fitRoot.style.transformOrigin = "top left";
        dom.fitRoot.style.left = "0px";
        dom.fitRoot.style.top = "0px";
      } catch (e) {}
      return;
    }

    const viewport = getViewportSize();
    const viewportWidth = Number(viewport.width || 0);
    const viewportHeight = Number(viewport.height || 0);
    
    if (viewportWidth <= 0 || viewportHeight <= 0) return;

    // Design canvas dimensions (what we designed for)
    const designWidth = 1920;
    const designHeight = 1080;

    // Guarded compensation for TVs that report a half-sized CSS viewport
    // (e.g. 960x540 on a physical 1080p panel). This branch is intentionally
    // narrow to avoid impacting displays that already render correctly.
    let effectiveWidth = viewportWidth;
    let effectiveHeight = viewportHeight;
    let fitSource = "inner";
    try {
      const dpr = Number(window.devicePixelRatio || 1);
      const sw = Number(window.screen && window.screen.width ? window.screen.width : 0);
      const sh = Number(window.screen && window.screen.height ? window.screen.height : 0);
      const baseContain = Math.min(viewportWidth / designWidth, viewportHeight / designHeight);

      const looksHalfViewport =
        viewportWidth <= 1100 &&
        viewportHeight <= 650 &&
        baseContain <= 0.65 &&
        (dpr >= 1.45 || (sw >= 1600 && sh >= 900));

      if (looksHalfViewport) {
        const candidates = [];
        if (dpr > 1.1) {
          candidates.push({
            w: viewportWidth * dpr,
            h: viewportHeight * dpr,
            src: "dpr",
          });
        }
        if (sw > 0 && sh > 0) {
          candidates.push({ w: sw, h: sh, src: "screen" });
        }

        let best = null;
        for (const c of candidates) {
          if (!c || !isFinite(c.w) || !isFinite(c.h) || c.w <= 0 || c.h <= 0) continue;
          const contain = Math.min(c.w / designWidth, c.h / designHeight);
          // Accept only meaningful improvements in the expected range.
          if (contain < 0.9 || contain > 1.35) continue;
          if (!best || contain > best.contain) {
            best = { ...c, contain };
          }
        }

        if (best) {
          effectiveWidth = best.w;
          effectiveHeight = best.h;
          fitSource = best.src;
        }
      }
    } catch (e) {}

    // Optional overscan-safe margin (defaults to full fill == 1.0)
    const fitMargin = getFitMargin();
    const safeWidth = effectiveWidth * fitMargin;
    const safeHeight = effectiveHeight * fitMargin;

    // Calculate scale ratios
    const scaleX = safeWidth / designWidth;
    const scaleY = safeHeight / designHeight;
    
    // ✅ الحل 2 + 3: تحديد وضع الملء (cover أو contain)
    let fitMode = getFitMode(); // من الـ URL parameter
    
    // ✅ الحل 3: كشف تلقائي للشاشات غير القياسية
    if (fitMode === "cover") { // فقط إذا لم يحدد المستخدم contain يدوياً
      const aspectRatio = viewportWidth / viewportHeight;
      const designAspectRatio = designWidth / designHeight; // 1.778 (16:9)
      const aspectDiff = Math.abs(aspectRatio - designAspectRatio);
      const threshold = getAspectThreshold();
      
      if (aspectDiff > threshold) {
        // شاشة بنسبة مختلفة كثيراً عن 16:9 (مثل IQTouch)
        fitMode = "contain";
        if (isDebug()) console.log(`[Auto-fit] Non-standard aspect ratio detected (${aspectRatio.toFixed(3)} vs ${designAspectRatio.toFixed(3)}), switching to contain mode`);
      }
    }
    
    // تطبيق الوضع المناسب
    let scale;
    if (fitMode === "contain") {
      // CONTAIN: إظهار كل المحتوى (قد تظهر حواف سوداء)
      scale = Math.min(scaleX, scaleY);
    } else {
      // COVER: ملء الشاشة بالكامل (قد يتم قص الحواف)
      scale = Math.max(scaleX, scaleY);
    }
    
    // Allow scaling UP for large TVs, but cap at reasonable maximum
    const maxScale = getFitMaxScale();
    scale = clamp(scale, 0.5, maxScale);

    // Center the fixed 16:9 design canvas. In contain mode this distributes
    // spare space evenly; in cover mode it crops both sides symmetrically.
    const renderedWidth = designWidth * scale;
    const renderedHeight = designHeight * scale;
    const offsetX = (viewportWidth - renderedWidth) / 2;
    const offsetY = (viewportHeight - renderedHeight) / 2;
    dom.fitRoot.style.transform = `scale(${scale.toFixed(4)})`;
    dom.fitRoot.style.transformOrigin = "top left";
    dom.fitRoot.style.left = `${offsetX.toFixed(2)}px`;
    dom.fitRoot.style.top = `${offsetY.toFixed(2)}px`;

    try {
      const body = document.body || document.documentElement;
      body.dataset.uiScale = scale.toFixed(4);
      body.dataset.fitMode = fitMode; // للـ debugging
      body.dataset.fitSource = fitSource; // inner | dpr | screen
      body.dataset.fitMargin = String(fitMargin);
      
      // Keep typography locked to the 1920x1080 design canvas.
      // The stage is already scaled via transform(), so increasing body font-size
      // here creates a second enlargement that can break the fixed board layout
      // on high-DPI / very large displays.
      body.style.fontSize = "";
    } catch (e) {}
  }

  // ===== Debug overlay =====
  let dbgEl = null;
  function ensureDebugOverlay() {
    if (!isDebug()) return;
    if (dbgEl) return;
    dbgEl = document.createElement("div");
    dbgEl.style.position = "fixed";
    dbgEl.style.bottom = "12px";
    dbgEl.style.right = "12px";
    dbgEl.style.zIndex = "99999";
    dbgEl.style.padding = "10px 12px";
    dbgEl.style.borderRadius = "12px";
    dbgEl.style.background = "rgba(0,0,0,0.55)";
    dbgEl.style.border = "1px solid rgba(255,255,255,0.18)";
    dbgEl.style.backdropFilter = "blur(8px)";
    dbgEl.style.fontFamily = "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace";
    dbgEl.style.fontSize = "12px";
    dbgEl.style.direction = "ltr";
    dbgEl.style.color = "#fff";
    dbgEl.textContent = "debug…";
    document.body.appendChild(dbgEl);
  }
  function setDebugText(txt) {
    if (dbgEl) dbgEl.textContent = safeText(txt);
  }

  // ===== Config =====
  const cfg = {
    REFRESH_EVERY: 30, // conservative default for large fleets; can be overridden from server
    WS_FALLBACK_POLL_EVERY: 180, // when WS is healthy, keep only a sparse safety poll
    WS_LIVE_STATUS_CHECK_SEC: 60, // sparse safety net when WS/cache misses cross-process updates
    STANDBY_SPEED: 0.8,
    PERIODS_SPEED: 0.5,
    MEDIA_PREFIX: "/media/",
    BELL_SOUND_URL: "",
    SNAPSHOT_URL: "",
    SERVER_TOKEN: "",
    SCHOOL_TYPE: "",
  };

  const DEFAULT_DISPLAY_COPY = {
    beforeBadge: "أهلا بكم",
    beforeTitle: "استعدوا لبداية يوم دراسي جميل",
    afterBadge: "أحسنتم",
    afterTitle: "أحسنتم اليوم، ونلقاكم غدا بإذن الله",
    afterHolidayBadge: "إجازة",
    afterHolidayTitle: "أحسنتم اليوم، نتمنى لكم إجازة سعيدة",
    holidayBadge: "إجازة",
    holidayTitle: "اليوم إجازة، نتمنى لكم وقتًا سعيدًا",
    afterEmpty: "أحسنتم اليوم",
  };

  const DISPLAY_COPY = { ...DEFAULT_DISPLAY_COPY };

  function applyDisplayCopySettings(settings) {
    const nextBeforeBadge = safeText(screenSetting("screenDisplayBeforeBadge", settings.display_before_badge || DEFAULT_DISPLAY_COPY.beforeBadge));
    const nextBeforeTitle = safeText(screenSetting("screenDisplayBeforeTitle", settings.display_before_title || DEFAULT_DISPLAY_COPY.beforeTitle));
    const nextAfterBadge = safeText(screenSetting("screenDisplayAfterBadge", settings.display_after_badge || DEFAULT_DISPLAY_COPY.afterBadge));
    const nextAfterTitle = safeText(screenSetting("screenDisplayAfterTitle", settings.display_after_title || DEFAULT_DISPLAY_COPY.afterTitle));
    const nextAfterHolidayBadge = safeText(screenSetting("screenDisplayAfterHolidayBadge", settings.display_after_holiday_badge || DEFAULT_DISPLAY_COPY.afterHolidayBadge));
    const nextAfterHolidayTitle = safeText(screenSetting("screenDisplayAfterHolidayTitle", settings.display_after_holiday_title || DEFAULT_DISPLAY_COPY.afterHolidayTitle));
    const nextHolidayBadge = safeText(screenSetting("screenDisplayHolidayBadge", settings.display_holiday_badge || DEFAULT_DISPLAY_COPY.holidayBadge));
    const nextHolidayTitle = safeText(screenSetting("screenDisplayHolidayTitle", settings.display_holiday_title || DEFAULT_DISPLAY_COPY.holidayTitle));

    DISPLAY_COPY.beforeBadge = nextBeforeBadge || DEFAULT_DISPLAY_COPY.beforeBadge;
    DISPLAY_COPY.beforeTitle = nextBeforeTitle || DEFAULT_DISPLAY_COPY.beforeTitle;
    DISPLAY_COPY.afterBadge = nextAfterBadge || DEFAULT_DISPLAY_COPY.afterBadge;
    DISPLAY_COPY.afterTitle = nextAfterTitle || DEFAULT_DISPLAY_COPY.afterTitle;
    DISPLAY_COPY.afterHolidayBadge = nextAfterHolidayBadge || DEFAULT_DISPLAY_COPY.afterHolidayBadge;
    DISPLAY_COPY.afterHolidayTitle = nextAfterHolidayTitle || DEFAULT_DISPLAY_COPY.afterHolidayTitle;
    DISPLAY_COPY.holidayBadge = nextHolidayBadge || DEFAULT_DISPLAY_COPY.holidayBadge;
    DISPLAY_COPY.holidayTitle = nextHolidayTitle || DEFAULT_DISPLAY_COPY.holidayTitle;
    DISPLAY_COPY.afterEmpty = DISPLAY_COPY.afterBadge || DEFAULT_DISPLAY_COPY.afterEmpty;
  }

  function normalizeDisplayStateType(rawType) {
    const t = safeText(rawType || "").toLowerCase();
    if (t === "before_school") return "before";
    if (t === "off_hours") return "off";
    if (t === "no_schedule_today") return "holiday";
    return t;
  }

  function getStateReason(state) {
    return safeText((state && state.reason) || "").toLowerCase();
  }

  function getAfterDayOverCopy() {
    const meta = (lastPayloadForFiltering && lastPayloadForFiltering.meta) || {};
    const nextSchoolDay = meta.next_school_day || {};
    const daysAhead = Number(nextSchoolDay.days_ahead || 0);
    if (daysAhead === 1) {
      return { title: DISPLAY_COPY.afterTitle, badge: DISPLAY_COPY.afterBadge };
    }
    return { title: DISPLAY_COPY.afterHolidayTitle, badge: DISPLAY_COPY.afterHolidayBadge };
  }

  function teacherLabelText() {
    const t = (cfg.SCHOOL_TYPE || "").toString().trim().toLowerCase();
    if (t === "boys") return "المعلم";
    if (t === "girls") return "المعلمة";
    return "المعلم/ـة";
  }

  // ===== Runtime (فلترة الانتظار/نهاية الدوام) =====
  const rt = {
    activePeriodIndex: null, // رقم الحصة الحالية/التالية
    activeFromHM: null, // وقت بداية النشاط الحالي/التالي
    activeToHM: null, // وقت نهاية النشاط الحالي (للحساب الدقيق)
    activeTargetHM: null, // الوقت المستهدف للعد التنازلي (from في before, to في period/break)
    activeTargetMs: null, // epoch ms للحد الفاصل الفعلي حتى لا يسبق الانتقال وقتَه
    activeStateType: null, // نوع الحالة الحالية (period/break/before)
    dayOver: false, // انتهاء الدوام
    refreshJitterFrac: 0, // jitter نسبي ثابت لكل شاشة لتفريق الحمل
    statusEverySec: 0, // adaptive polling interval for status-first mode
    status304Streak: 0, // consecutive status 304 streak
    scheduleRevision: 0, // last known schedule_revision (numeric truth for /status?v=...)
    transitionUntilTs: 0, // while > Date.now(): force snapshot fetch to cross 00:00 boundaries
    transitionBackoffSec: 2.0, // bounded backoff during transition window
    pollStateLastLogTs: 0, // debug-only: last time we logged poll state
    
    // ===== Unified Operational Mode =====
    // Single source of truth for display state. All decisions flow from this.
    // Valid modes: "init" | "active" | "ws-live" | "sleeping" | "waking" | "fallback-poll" | "blocked"
    mode: "init",
    modePrev: "",
    modeReason: "",
    modeChangedAt: 0,
    bindingLost: false,
    terminalReason: "",
    terminalDetails: null,
    bindingLostAt: 0,
    bindingConflictSource: "",

    // Schedule meta from server (authoritative)
    isSchoolDay: true,
    isActiveWindow: true,
    activeWindowStartMs: null,
    activeWindowEndMs: null,
    nextWakeMs: null,

    // WebSocket state
    ws: null,
    wsConnected: false,
    wsRetryCount: 0,
    pendingRev: null,
    forceFetchSnapshot: false,
    invalidateGeneration: 0,
    wsEnabled: false,
    wsMaxRetries: 10,
    wsEverConnected: false,
    wsSuppressedUntilTs: 0,
    wsOpenedAt: 0, // timestamp of last ws.onopen — used for fetch dedup cooldown
    wsLastMessageAt: 0,
    wsHeartbeatTimeoutMs: 75000,
    wsLiveStatusGen: 0,

    // Sleep/Wake
    sleepReason: "", // "before_hours" | "after_hours" | "holiday"

    // Adaptive Fallback Polling (improvement #2)
    // Tracks escalation level when WS is down and mode is fallback-poll.
    // Level 0 = fastest (20s), escalates through tiers, resets on WS recovery.
    fallbackLevel: 0,
  };

  // ===========================================================================
  // ===== Unified Timer Manager ===============================================
  // ===========================================================================
  // Single registry for ALL named timers. Prevents duplicates and leaks.
  // Rules:
  //   - Every setTimeout/setInterval must go through setNamedTimer/setNamedInterval.
  //   - clear-before-set is enforced automatically.
  //   - Each name maps to exactly one active timer.
  // ===========================================================================

  const _timers = Object.create(null); // name → {id, type:"timeout"|"interval", label}
  let _listenerAbortController = null;
  try {
    _listenerAbortController = new AbortController();
  } catch (e) {}

  function listenerOpts(opts) {
    var next = opts ? Object.assign({}, opts) : {};
    if (_listenerAbortController) next.signal = _listenerAbortController.signal;
    return next;
  }

  const BLOCKED_RECOVERY_DELAY_MS = 5 * 60 * 1000;
  const BLOCKED_RECOVERY_RELOAD_COOLDOWN_MS = 2 * 60 * 1000;

  function setNamedTimer(name, fn, delayMs, label) {
    clearNamedTimer(name);
    var id = setTimeout(function _namedTimerFire() {
      delete _timers[name];
      _log("timer_fire", { name: name, label: label || name });
      try { fn(); } catch (e) {
        if (isDebug()) console.error("[timer] " + name + " error:", e);
      }
    }, Math.max(0, delayMs));
    _timers[name] = { id: id, type: "timeout", label: label || name };
    _log("timer_set", { name: name, delay: Math.round(delayMs), label: label || name });
    return id;
  }

  function setNamedInterval(name, fn, intervalMs, label) {
    clearNamedTimer(name);
    var id = setInterval(function _namedIntervalFire() {
      _log("timer_fire", { name: name, label: label || name });
      try { fn(); } catch (e) {
        if (isDebug()) console.error("[interval] " + name + " error:", e);
      }
    }, Math.max(100, intervalMs));
    _timers[name] = { id: id, type: "interval", label: label || name };
    _log("timer_set", { name: name, interval: Math.round(intervalMs), label: label || name });
    return id;
  }

  function clearNamedTimer(name) {
    var t = _timers[name];
    if (!t) return;
    if (t.type === "interval") {
      clearInterval(t.id);
    } else {
      clearTimeout(t.id);
    }
    delete _timers[name];
    _log("timer_clear", { name: name });
  }

  function clearAllRuntimeTimers(exclusions) {
    var excl = exclusions || [];
    var names = Object.keys(_timers);
    for (var i = 0; i < names.length; i++) {
      if (excl.indexOf(names[i]) === -1) {
        clearNamedTimer(names[i]);
      }
    }
  }

  function cleanupPageRuntime(reason) {
    clearAllRuntimeTimers();
    try {
      _stopWakeLockKeepalive();
    } catch (e) {}
    try {
      if (periodsScroller) periodsScroller.stop();
      if (standbyScroller) standbyScroller.stop();
      if (dutyScroller) dutyScroller.stop();
    } catch (e) {}
    try {
      if (rt.ws) {
        rt.ws.onopen = null;
        rt.ws.onmessage = null;
        rt.ws.onerror = null;
        rt.ws.onclose = null;
        rt.ws.close(1000, safeText(reason || "page_cleanup") || "page_cleanup");
      }
    } catch (e) {}
    rt.ws = null;
    rt.wsConnected = false;
    if (_listenerAbortController) {
      try {
        _listenerAbortController.abort();
      } catch (e) {}
      _listenerAbortController = null;
    }
  }

  // ===========================================================================
  // ===== Unified Structured Logger ===========================================
  // ===========================================================================
  // All instrumentation flows through _log(event, data).
  // In debug mode: console.log with structured object.
  // In production: silent (zero overhead).
  // ===========================================================================

  function _log(event, data) {
    if (!isDebug()) return;
    try {
      var obj = { event: event, mode: rt.mode, ts: new Date().toISOString() };
      if (data) {
        var keys = Object.keys(data);
        for (var i = 0; i < keys.length; i++) obj[keys[i]] = data[keys[i]];
      }
      // Serialize so TV consoles and remote support tools retain the fields
      // instead of reducing every record to an opaque "Object" message.
      console.log("[display] " + JSON.stringify(obj));
    } catch (e) {}
  }

  function _logContext(extra) {
    var out = {
      mode: rt.mode,
      reason: rt.terminalReason || rt.modeReason || "",
      wsConnected: !!rt.wsConnected,
      bindingLost: !!rt.bindingLost,
    };
    try {
      out.deviceKey = getOrCreateDeviceId();
    } catch (e) {
      out.deviceKey = "";
    }
    if (extra) {
      var keys = Object.keys(extra);
      for (var i = 0; i < keys.length; i++) out[keys[i]] = extra[keys[i]];
    }
    return out;
  }

  function isTerminalBlockedMode() {
    return !!(rt.bindingLost || rt.mode === "blocked");
  }

  function _trimLogText(v, maxLen) {
    var s = safeText(v);
    if (!s) return "";
    var limit = Math.max(16, Number(maxLen) || 200);
    return s.length > limit ? s.slice(0, limit - 1) + "…" : s;
  }

  function _normSignal(v) {
    return safeText(v)
      .trim()
      .toLowerCase()
      .replace(/[\s\-]+/g, "_")
      .replace(/[^a-z0-9_]/g, "");
  }

  var _BINDING_CONFLICT_CODES = {
    "screen_bound": 1,
    "device_mismatch": 1,
    "device_binding_reject": 1,
    "another_device_active": 1,
    "screen_active_elsewhere": 1,
    "binding_conflict": 1,
    "binding_lost": 1,
    "device_revoked": 1,
    "screen_revoked": 1,
    "screen_rebound": 1,
    "display_reassigned": 1,
  };

  var _BINDING_WS_TYPES = {
    "device_binding_reject": 1,
    "another_device_active": 1,
    "screen_active_elsewhere": 1,
    "binding_lost": 1,
    "device_revoked": 1,
  };

  var _BINDING_DEVICE_REQUIRED_CODES = {
    "missing_device_id": 1,
    "device_required": 1,
  };

  var _AUTH_REFRESHABLE_CODES = {
    "token_expired": 1,
    "auth_expired": 1,
    "invalid_token": 1,
    "access_denied": 1,
    "token_invalid": 1,
    "authentication_failed": 1,
    "not_authenticated": 1,
    "permission_denied": 1,
  };

  var _BINDING_CONFLICT_TEXT_PATTERNS = [
    "this screen is already active on another device",
    "already active on another device",
    "screen is already active on another device",
    "screen active elsewhere",
    "active on another device",
    "bound to another device",
    "another_device_active",
    "device_binding_reject",
    "screen_active_elsewhere",
    "device mismatch",
    "binding conflict",
    "screen_bound",
    "device_mismatch",
    "مرتبطة بجهاز آخر",
    "جهاز آخر",
    "شاشة أخرى أصبحت نشطة",
    "نشطة على جهاز آخر",
  ];

  var _AUTH_REFRESHABLE_TEXT_PATTERNS = [
    "token expired",
    "invalid token",
    "not authenticated",
    "authentication credentials were not provided",
    "unauthorized",
    "auth expired",
    "session expired",
    "jwt expired",
  ];

  function _containsAny(haystack, patterns) {
    var h = safeText(haystack).toLowerCase();
    if (!h) return false;
    for (var i = 0; i < patterns.length; i++) {
      if (h.indexOf(patterns[i]) >= 0) return true;
    }
    return false;
  }

  function _isBindingConflictText(text) {
    return _containsAny(text, _BINDING_CONFLICT_TEXT_PATTERNS);
  }

  function _isAuthRefreshableText(text) {
    return _containsAny(text, _AUTH_REFRESHABLE_TEXT_PATTERNS);
  }

  function _extractSignalBag(body, rawText) {
    var b = body && typeof body === "object" ? body : null;
    var code = _normSignal(
      (b && (b.code || b.error_code || b.error || b.reason_code)) ||
      ""
    );
    var type = _normSignal(
      (b && (b.type || b.event || b.action)) ||
      ""
    );
    var detail = safeText(
      (b && (b.detail || b.message || b.reason || b.error_description || b.error_message || b.title)) ||
      rawText ||
      (typeof body === "string" ? body : "")
    );
    var textParts = [];
    if (code) textParts.push(code);
    if (type) textParts.push(type);
    if (detail) textParts.push(detail);
    if (rawText) textParts.push(rawText);
    return {
      code: code,
      type: type,
      detail: detail,
      text: textParts.join(" | ").toLowerCase(),
    };
  }

  function _bindingUiPayload(info) {
    var code = _normSignal((info && info.code) || (info && info.type) || "");
    var msg = _trimLogText((info && info.message) || (info && info.detail) || "", 220);
    if (_BINDING_DEVICE_REQUIRED_CODES[code]) {
      return {
        title: "تعذر تعريف الجهاز",
        details: msg || "لا يمكن متابعة العرض حتى يتم تعريف هذا الجهاز وربطه بالشاشة من لوحة التحكم.",
        kind: "device",
      };
    }
    return {
      title: "هذه الشاشة مفعّلة على جهاز آخر",
      details:
        msg ||
        "حرصًا على استقرار العرض وحماية بيانات المدرسة، يمكن تشغيل كل شاشة عرض على جهاز واحد فقط في الوقت نفسه.",
      kind: "binding",
    };
  }

  function isBindingConflictResponse(resp, body, rawText, sourcePath) {
    var status = resp && typeof resp.status === "number" ? resp.status : 0;
    var bag = _extractSignalBag(body, rawText);
    var code = bag.code;
    var type = bag.type;
    var text = bag.text;

    var statusBindingCandidate = status === 403 || status === 409;
    var byCode = !!(_BINDING_CONFLICT_CODES[code] || _BINDING_CONFLICT_CODES[type]);
    var byMessage = _isBindingConflictText(text);
    var byDeviceRequired = !!(_BINDING_DEVICE_REQUIRED_CODES[code] || _BINDING_DEVICE_REQUIRED_CODES[type]);
    var authRefreshable = !!(
      _AUTH_REFRESHABLE_CODES[code] ||
      _AUTH_REFRESHABLE_CODES[type] ||
      _isAuthRefreshableText(text)
    );

    var matched = false;
    var classification = "none";
    if (!authRefreshable) {
      if (statusBindingCandidate && (byCode || byMessage || byDeviceRequired)) {
        matched = true;
        classification = byCode ? "status_code_conflict" : byDeviceRequired ? "status_device_required" : "status_text_conflict";
      } else if (byCode || byDeviceRequired) {
        matched = true;
        classification = byCode ? "payload_code_conflict" : "payload_device_required";
      } else if (statusBindingCandidate && byMessage) {
        matched = true;
        classification = "payload_text_conflict";
      }
    }

    return {
      matched: matched,
      source: safeText(sourcePath || ""),
      status: status || null,
      code: code || type || "",
      message: bag.detail || "",
      classification: classification,
      text: _trimLogText(rawText || bag.text, 240),
      authRefreshable: authRefreshable,
    };
  }

  function isBindingConflictWsMessage(msg) {
    var obj = msg && typeof msg === "object" ? msg : null;
    if (!obj) {
      return { matched: false };
    }

    var nested = obj.data && typeof obj.data === "object" ? obj.data : null;

    var type = _normSignal(obj.type || obj.event || obj.action || (nested && (nested.type || nested.event || nested.action)) || "");
    var code = _normSignal(obj.code || obj.error_code || obj.error || obj.reason_code || (nested && (nested.code || nested.error_code || nested.error || nested.reason_code)) || "");
    var msgText = safeText(
      obj.message ||
      obj.detail ||
      obj.reason ||
      obj.error_description ||
      (nested && (nested.message || nested.detail || nested.reason || nested.error_description)) ||
      ""
    );
    var bag = _extractSignalBag(nested || obj, msgText);
    var mergedText = [bag.text, type, code].join(" | ");

    var byType = !!_BINDING_WS_TYPES[type];
    var byCode = !!(_BINDING_CONFLICT_CODES[code] || _BINDING_CONFLICT_CODES[bag.code] || _BINDING_CONFLICT_CODES[bag.type]);
    var byMessage = _isBindingConflictText(mergedText);

    var matched = byType || byCode || byMessage;
    return {
      matched: matched,
      source: "ws",
      status: null,
      code: code || bag.code || type || bag.type || "",
      message: msgText || bag.detail || "",
      classification: byType ? "ws_type_conflict" : byCode ? "ws_code_conflict" : byMessage ? "ws_text_conflict" : "none",
      text: _trimLogText(mergedText, 240),
    };
  }

  function isBindingConflictWsClose(event) {
    var code = event && typeof event.code === "number" ? event.code : 0;
    var rawReason = safeText(event && event.reason ? event.reason : "");
    var parsed = null;
    if (rawReason) {
      try {
        parsed = JSON.parse(rawReason);
      } catch (e) {
        parsed = null;
      }
    }
    var bag = _extractSignalBag(parsed, rawReason);
    var text = [bag.text, String(code || "")].join(" | ");
    var byCode = !!(_BINDING_CONFLICT_CODES[bag.code] || _BINDING_CONFLICT_CODES[bag.type]);
    var byText = _isBindingConflictText(text);
    var matched = byCode || byText;
    return {
      matched: matched,
      source: "ws_close",
      status: code || null,
      code: bag.code || bag.type || "",
      message: rawReason || bag.detail || "",
      classification: byCode ? "ws_close_code_conflict" : byText ? "ws_close_text_conflict" : "none",
      text: _trimLogText(text, 240),
    };
  }

  function enterBindingLostState(reason, details) {
    var info = details || {};
    var terminalReason = safeText(reason || info.classification || info.code || "binding_lost");
    var sourcePath = safeText(info.sourcePath || info.source || "unknown");

    if (isTerminalBlockedMode()) {
      _log("terminal_state_entered", _logContext({
        reason: terminalReason,
        sourcePath: sourcePath,
        duplicate: true,
      }));
      return;
    }

    var timersBefore = Object.keys(_timers);
    var wsObj = rt.ws;

    rt.bindingLost = true;
    rt.bindingLostAt = Date.now();
    rt.terminalReason = terminalReason;
    rt.bindingConflictSource = sourcePath;
    rt.terminalDetails = info;

    _log("binding_conflict_detected", _logContext({
      reason: terminalReason,
      sourcePath: sourcePath,
      status: typeof info.status === "number" ? info.status : null,
      responseClassification: safeText(info.classification || ""),
      responseCode: safeText(info.code || ""),
      responseMessage: _trimLogText(info.message || info.text || "", 220),
    }));
    _log("binding_conflict_source", _logContext({
      sourcePath: sourcePath,
      status: typeof info.status === "number" ? info.status : null,
      responseClassification: safeText(info.classification || ""),
    }));

    setMode("blocked", "binding_lost:" + terminalReason);

    rt.pendingRev = null;
    rt.forceFetchSnapshot = false;
    rt.status304Streak = 0;
    rt.statusEverySec = 0;
    rt.transitionUntilTs = 0;
    rt.transitionBackoffSec = 2.0;
    rt.wsEnabled = false;
    rt.wsConnected = false;
    rt.wsRetryCount = rt.wsMaxRetries;
    rt.wsSuppressedUntilTs = Date.now() + BLOCKED_RECOVERY_DELAY_MS;
    failStreak = 0;
    isFetching = false;
    forceRefreshInProgress = false;

    _stopWsHeartbeat("binding_lost_terminal");
    _heartbeatGen++;

    clearAllRuntimeTimers();
    _log("timers_cleared_due_to_binding_loss", _logContext({
      sourcePath: sourcePath,
      timerNames: timersBefore.join(","),
      timerCount: timersBefore.length,
    }));

    if (ctrl) {
      try { ctrl.abort(); } catch (e) {}
    }
    if (ctrlStatus) {
      try { ctrlStatus.abort(); } catch (e) {}
    }
    ctrl = null;
    ctrlStatus = null;
    inflight = null;
    inflightStatus = null;

    if (wsObj) {
      try { wsObj.onopen = null; } catch (e) {}
      try { wsObj.onmessage = null; } catch (e) {}
      try { wsObj.onerror = null; } catch (e) {}
      try { wsObj.onclose = null; } catch (e) {}
      try { wsObj.close(4403, "binding_lost"); } catch (e) {}
    }
    rt.ws = null;
    _log("ws_closed_due_to_binding_loss", _logContext({
      sourcePath: sourcePath,
      hadSocket: !!wsObj,
    }));

    var ui = _bindingUiPayload(info);
    showBlocker(ui.title, ui.details, ui.kind);
    setNamedTimer("blocked_recovery_reload", function () {
      var now = Date.now();
      var reloadCooldownKey = "display_blocked_recovery_reload_ts";
      var lastReloadTs = 0;
      try { lastReloadTs = Number(sessionStorage.getItem(reloadCooldownKey) || 0); } catch (e) {}
      if (now - lastReloadTs < BLOCKED_RECOVERY_RELOAD_COOLDOWN_MS) return;
      try { sessionStorage.setItem(reloadCooldownKey, String(now)); } catch (e) {}
      _log("blocked_recovery_reload", _logContext({
        sourcePath: sourcePath,
        delayMs: BLOCKED_RECOVERY_DELAY_MS,
      }));
      try {
        window.location.reload();
      } catch (e) {
        window.location.href = window.location.href;
      }
    }, BLOCKED_RECOVERY_DELAY_MS, "blocked_recovery_reload");

    _log("terminal_state_entered", _logContext({
      reason: terminalReason,
      sourcePath: sourcePath,
      status: typeof info.status === "number" ? info.status : null,
      responseClassification: safeText(info.classification || ""),
      wsConnected: !!rt.wsConnected,
    }));
  }

  // ===========================================================================
  // ===== Mode State Machine ==================================================
  // ===========================================================================
  // setMode() is the ONLY way to change rt.mode. All transitions are validated.
  // Invalid transitions are rejected with a warning.
  //
  //  init → active | sleeping | fallback-poll | blocked
  //  active → ws-live | sleeping | fallback-poll | blocked
  //  ws-live → active | sleeping | fallback-poll | blocked
  //  sleeping → waking | blocked
  //  waking → active | ws-live | sleeping | fallback-poll | blocked
  //  fallback-poll → active | ws-live | sleeping | blocked
  //  blocked → (terminal, no automatic exit)
  //
  //  "init" is only valid at startup before first snapshot.
  // ===========================================================================

  var _validTransitions = {
    "init":          ["active", "sleeping", "fallback-poll", "blocked"],
    "active":        ["ws-live", "sleeping", "fallback-poll", "blocked"],
    "ws-live":       ["active", "sleeping", "fallback-poll", "blocked"],
    "sleeping":      ["waking", "blocked"],
    "waking":        ["active", "ws-live", "sleeping", "fallback-poll", "blocked"],
    "fallback-poll": ["active", "ws-live", "sleeping", "blocked"],
    "blocked":       [],
  };

  function setMode(next, reason) {
    var prev = rt.mode;
    if (prev === next) return; // no-op for same mode

    // Terminal mode is one-way for this session.
    if (prev === "blocked" && next !== "blocked") {
      _log("mode_rejected", { from: prev, to: next, reason: reason, terminal: true });
      return;
    }

    // Validate transition
    var allowed = _validTransitions[prev];
    if (!allowed || allowed.indexOf(next) === -1) {
      _log("mode_rejected", { from: prev, to: next, reason: reason });
      if (isDebug()) console.warn("[mode] REJECTED: " + prev + " → " + next + " (" + reason + ")");
      return;
    }

    rt.modePrev = prev;
    rt.mode = next;
    rt.modeReason = reason || "";
    rt.modeChangedAt = Date.now();

    _log("mode_change", { from: prev, to: next, reason: reason });

    // Side effects on mode entry
    _onModeEnter(next, prev, reason);
  }

  function _onModeEnter(mode, prev, reason) {
    if (mode === "blocked") {
      clearNamedTimer("poll");
      clearNamedTimer("ws_snapshot");
      _stopWsLiveStatusSafeguard("entered_blocked");
      clearNamedTimer("sleep_reconnect");
      clearNamedTimer("wake");
      clearNamedTimer("wake_chunk");
      clearNamedTimer("safety_check");
      clearNamedTimer("ws_reconnect");
      _stopWsHeartbeat("entered_blocked");
      _log("poll_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_onModeEnter",
        reason: reason,
      }));
      return;
    }

    if (mode === "sleeping") {
      // Cancel polling timer — sleep engine manages wake
      clearNamedTimer("poll");
      clearNamedTimer("ws_snapshot"); // cancel any pending WS-triggered fetch
      _stopWsLiveStatusSafeguard("entered_sleep");
      _closeWsForSleep("entered_sleep");
      _log("polling_stopped", { reason: "entered_sleep" });
    }

    if (mode === "waking") {
      // Transient state: immediately transition to active or ws-live
      clearNamedTimer("wake");
      clearNamedTimer("safety_check");
      clearNamedTimer("sleep_reconnect");  // cancel sleep reconnect
      clearNamedTimer("ws_snapshot");       // cancel stale WS snapshot fetch
      _stopWsLiveStatusSafeguard("waking");
      _stopWsHeartbeat("waking");
      rt.status304Streak = 0;
      rt.statusEverySec = 0;
      resetFallbackPollLevel("waking");

      if (rt.wsEnabled && !rt.wsConnected) {
        initWebSocket();
      }

      // Determine target mode
      var target = (rt.wsConnected) ? "ws-live" : "active";
      _log("wake_started", { target: target, reason: reason, wsConnected: rt.wsConnected });

      // Force a one-shot snapshot fetch on wake so the screen gets fresh data.
      // Critical for ws-live target: without this, refreshLoop's ws-live guard
      // would block the wake fetch (rt.forceFetchSnapshot was false).
      rt.forceFetchSnapshot = true;

      setMode(target, "wake_complete_" + reason);
      _log(target === "ws-live" ? "wake_completed_to_ws_live" : "wake_completed_to_fallback", {
        reason: reason,
        pollTimer: !!_timers["poll"],
        heartbeatTimer: !!_timers["ws_heartbeat"],
      });
      // Schedule immediate fetch (forced — bypass mode gate for one-shot)
      scheduleNext(0.3, "wake");
      return;
    }

    if (mode === "ws-live") {
      // KEY FIX: stop ALL polling — heartbeat takes over
      clearNamedTimer("poll");
      clearNamedTimer("ws_snapshot");
      clearNamedTimer("sleep_reconnect");
      resetFallbackPollLevel("ws_live");
      _startWsHeartbeat("ws_live_entered");
      _startWsLiveStatusSafeguard("ws_live_entered");
      _log("ws_live_entered", {
        reason: reason,
        wsConnected: rt.wsConnected,
      });
    }

    if (mode === "active") {
      // Resume polling if not already scheduled
      _stopWsLiveStatusSafeguard("entered_active");
      _stopWsHeartbeat("entered_active");
      resetFallbackPollLevel("entered_active");
      if (!_timers["poll"]) {
        scheduleNext(0.3);
      }
    }

    if (mode === "fallback-poll") {
      // Enter adaptive fallback — start at current level
      _stopWsLiveStatusSafeguard("entered_fallback");
      _stopWsHeartbeat("entered_fallback");
      _log("fallback_poll_entered", {
        level: rt.fallbackLevel,
        intervalSec: getFallbackPollDelay(),
        reason: reason,
      });
      if (!_timers["poll"]) {
        scheduleNext(getFallbackPollDelay());
      }
    }
  }

  // ===========================================================================
  // ===== validateScheduleMeta ================================================
  // ===========================================================================
  // Validates server-supplied meta before any sleep/mode decision.
  // Returns { valid: bool, meta: {…}, reason: string }
  // If invalid: returns valid=false — caller MUST NOT sleep.
  // ===========================================================================

  function validateScheduleMeta(payload) {
    var result = { valid: false, meta: null, reason: "no_payload" };
    if (!payload || !payload.meta) return result;

    var m = payload.meta;
    result.meta = m;

    // Required boolean flags
    if (typeof m.is_active_window !== "boolean") {
      result.reason = "missing_is_active_window";
      _log("meta_invalid", result);
      return result;
    }
    if (typeof m.is_school_day !== "boolean") {
      result.reason = "missing_is_school_day";
      _log("meta_invalid", result);
      return result;
    }

    // If not active window, we need either a complete active_window or a next_wake_at.
    if (!m.is_active_window) {
      var hasCompleteWindow = !!(m.active_window && m.active_window.start && m.active_window.end);
      var hasNextWake = !!m.next_wake_at;
      if (!hasCompleteWindow && !hasNextWake) {
        result.reason = "missing_sleep_wake_meta";
        _log("meta_invalid", result);
        return result;
      }
    }

    result.valid = true;
    result.reason = "ok";
    return result;
  }

  // ===========================================================================
  // ===== Jitter for Thundering Herd Prevention ===============================
  // ===========================================================================
  // Deterministic per-device jitter (0–60s) for scheduled wakes.
  // NOT applied on WS-push wakes (instant response needed).
  // ===========================================================================

  var WAKE_JITTER_MAX_MS = 60 * 1000; // 60 seconds max jitter

  function _wakeJitterMs() {
    // Use the stable per-device jitter fraction (0.0–0.5)
    // Scale to 0..WAKE_JITTER_MAX_MS
    return Math.abs(rt.refreshJitterFrac) * 2 * WAKE_JITTER_MAX_MS;
  }

  // ===========================================================================
  // ===== Improvement #1: Sleep Reconnect Jitter ==============================
  // ===========================================================================
  // When WS drops during sleep, thousands of devices could try to reconnect
  // simultaneously. This helper adds a bounded, deterministic-ish per-device
  // jitter (5–30s) specifically for sleep-mode reconnects.
  //
  // Range rationale:
  //   - 5s minimum: avoids instant stampede, gives server breathing room
  //   - 30s maximum: keeps reconnect responsive enough that WS recovers quickly
  //   - 25s spread across N devices distributes load evenly
  //   - Uses refreshJitterFrac (stable per page load) + small random component
  //     so devices don't cluster on the same deterministic offset
  // ===========================================================================

  var SLEEP_RECONNECT_MIN_MS = 5 * 1000;   // 5s floor
  var SLEEP_RECONNECT_MAX_MS = 30 * 1000;  // 30s ceiling

  function _sleepReconnectJitterMs() {
    // Deterministic base from per-device fraction (0..25s spread)
    var base = Math.abs(rt.refreshJitterFrac) * 2 * (SLEEP_RECONNECT_MAX_MS - SLEEP_RECONNECT_MIN_MS);
    // Small random perturbation (±3s) to break ties between devices with similar fractions
    var perturbation = (Math.random() - 0.5) * 6000;
    var jitter = SLEEP_RECONNECT_MIN_MS + base + perturbation;
    // Clamp to [min, max]
    return Math.max(SLEEP_RECONNECT_MIN_MS, Math.min(SLEEP_RECONNECT_MAX_MS, Math.round(jitter)));
  }

  /**
   * Schedule a WS reconnect attempt during sleep mode.
   * Uses dedicated jitter and named timer to prevent reconnect storms.
   * If WS comes back before the timer fires, it will be safely cancelled by
   * clearNamedTimer("sleep_reconnect") in the WS onopen handler.
   */
  function _scheduleSleepReconnect() {
    if (isTerminalBlockedMode()) {
      _log("reconnect_blocked_due_to_binding_loss", _logContext({
        sourcePath: "sleep_reconnect",
      }));
      return;
    }
    // Guard: only during sleep
    if (rt.mode !== "sleeping") return;
    // Guard: don't schedule if WS already connected
    if (rt.wsConnected) return;
    // Guard: don't schedule if WS is disabled
    if (!rt.wsEnabled) return;

    var jitter = _sleepReconnectJitterMs();
    _log("sleep_reconnect_scheduled", {
      jitterMs: jitter,
      jitterSec: (jitter / 1000).toFixed(1),
    });

    setNamedTimer("sleep_reconnect", function () {
      if (isTerminalBlockedMode()) {
        _log("reconnect_blocked_due_to_binding_loss", _logContext({
          sourcePath: "sleep_reconnect_timer",
        }));
        return;
      }
      // Re-check mode at fire time — mode may have changed
      if (rt.mode !== "sleeping") {
        _log("sleep_reconnect_skipped", { reason: "mode_changed", mode: rt.mode });
        return;
      }
      if (rt.wsConnected) {
        _log("sleep_reconnect_skipped", { reason: "ws_already_connected" });
        return;
      }
      _log("sleep_reconnect_fired", {});
      initWebSocket();
    }, jitter, "sleep_ws_reconnect");
  }

  // ===========================================================================
  // ===== Improvement #2: Adaptive Fallback Polling ===========================
  // ===========================================================================
  // When WS fails and mode is fallback-poll, polling starts sparse then
  // gradually slows to reduce server load. Resets on WS recovery or successful
  // data fetch that restores confidence.
  //
  // Tier rationale:
  //   Level 0: 60s  — rare fallback only after WS loss
  //   Level 1: 90s  — first cooldown
  //   Level 2: 180s — 3 minutes, significant load reduction
  //   Level 3: 300s — 5 minutes, steady state for long outages
  //
  // At 30K screens:
  //   Level 0 (60s) → 500 req/s during outage
  //   Level 3 (300s) → 100 req/s steady state
  // ===========================================================================

  var _FALLBACK_TIERS = [60, 90, 180, 300]; // seconds per level

  function getFallbackPollDelay() {
    var level = Math.min(rt.fallbackLevel, _FALLBACK_TIERS.length - 1);
    return _FALLBACK_TIERS[level];
  }

  function advanceFallbackPollLevel() {
    if (rt.mode !== "fallback-poll") return;
    var prev = rt.fallbackLevel;
    rt.fallbackLevel = Math.min(rt.fallbackLevel + 1, _FALLBACK_TIERS.length - 1);
    if (rt.fallbackLevel !== prev) {
      _log("fallback_level_advanced", {
        from: prev,
        to: rt.fallbackLevel,
        intervalSec: getFallbackPollDelay(),
      });
    }
  }

  function resetFallbackPollLevel(reason) {
    if (rt.fallbackLevel === 0) return;
    var prev = rt.fallbackLevel;
    rt.fallbackLevel = 0;
    _log("fallback_level_reset", { from: prev, reason: reason });
  }

  // ===========================================================================
  // ===== Improvement #3: WS Snapshot Fetch Jitter ============================
  // ===========================================================================
  // When a WS invalidate/broadcast reaches thousands of screens, they all
  // request a snapshot simultaneously → snapshot storm. This adds a small
  // jitter (0–200ms) to spread requests.
  //
  // Range rationale:
  //   - 200ms max: imperceptible to humans (<250ms reaction threshold)
  //   - At 30K screens over 200ms → ~150K req/s becomes ~150K spread over 200ms
  //     which reduces peak by ~5x vs instant burst
  //   - 0ms floor: some devices go first for fastest visible update
  //   - Coalescing: if multiple invalidates arrive rapidly, only the last one
  //     results in a fetch (overwrite pattern via named timer)
  // ===========================================================================

  var WS_SNAPSHOT_JITTER_MAX_MS = 1200; // wider spread to avoid post-invalidate stampedes

  function _wsSnapshotJitterMs(revision) {
    var cap = Math.max(200, Number(cfg.WS_SNAPSHOT_JITTER_MAX_MS) || WS_SNAPSHOT_JITTER_MAX_MS);
    var floor = Math.min(250, Math.round(cap * 0.2));
    var seed = (getDeviceId() || window.location.pathname || "display") + "|" + String(revision || 0);
    var fraction = _stableUnitInterval("ws_snapshot:" + seed);
    return floor + Math.round(fraction * Math.max(0, cap - floor));
  }

  /**
   * Schedule a snapshot refresh after WS invalidate, with jitter + coalescing.
   * If called again before the timer fires, the previous timer is replaced
   * (coalesced) — only one fetch executes per burst of invalidates.
   */
  function scheduleWsSnapshotRefresh(revision) {
    if (isTerminalBlockedMode()) {
      _log("snapshot_blocked_due_to_binding_loss", _logContext({
        sourcePath: "scheduleWsSnapshotRefresh",
        revision: revision,
      }));
      return;
    }
    var jitter = _wsSnapshotJitterMs(revision);

    _log("ws_snapshot_scheduled", {
      revision: revision,
      jitterMs: jitter,
      coalesced: !!_timers["ws_snapshot"],
    });

    setNamedTimer("ws_snapshot", function () {
      if (isTerminalBlockedMode()) {
        _log("snapshot_blocked_due_to_binding_loss", _logContext({
          sourcePath: "ws_snapshot_timer",
          revision: revision,
        }));
        return;
      }
      // Guard: don't fetch if mode changed to sleeping
      if (rt.mode === "sleeping") {
        _log("ws_snapshot_skipped", { reason: "sleeping" });
        return;
      }
      // Guard: if another fetch completed and revision already consumed
      if (rt.pendingRev === null && !rt.forceFetchSnapshot) {
        _log("ws_snapshot_skipped", { reason: "already_consumed" });
        return;
      }
      _log("ws_snapshot_fired", { revision: revision });
      rt.status304Streak = 0;
      rt.statusEverySec = basePollEverySec();
      // Pause heartbeat — one-shot fetch replaces it temporarily
      _stopWsHeartbeat("ws_invalidate_fetch");
      scheduleNext(0.1, "ws_invalidate"); // forced — bypass mode gate
    }, jitter, "ws_invalidate_fetch");
  }


  // ===========================================================================
  // ===== WS-Live Heartbeat System ============================================
  // ===========================================================================
  // When in ws-live mode, polling is STOPPED. Heartbeat is WS-only: server
  // heartbeat frames and client ping/pong detect dead sockets without HTTP.
  //
  // Guarantees:
  //   1. Heartbeat timer ("ws_heartbeat") is independent of poll timer ("poll").
  //   2. Only runs when rt.mode === "ws-live".
  //   3. If the socket goes silent, close it and let fallback-poll take over.
  //   4. Entering any mode other than ws-live stops heartbeat immediately.
  //   5. No fast polling (8-20s) can occur during ws-live.
  // ===========================================================================

  // --- forceReason allowlist: only these values may bypass mode gates ---
  var _ALLOWED_FORCE_REASONS = { "ws_invalidate": 1, "wake": 1 };

  function _isAllowedForceReason(reason) {
    return !!(reason && _ALLOWED_FORCE_REASONS[reason]);
  }

  var _WS_HEARTBEAT_SEC = 30; // local stale-socket monitor, not an HTTP poll
  var _heartbeatGen = 0; // generation counter — invalidates stale callbacks

  function _markWsHeartbeatSeen(kind) {
    rt.wsLastMessageAt = Date.now();
    if (kind) _log("ws_heartbeat_seen", { kind: kind });
  }

  function _startWsHeartbeat(reason) {
    if (isTerminalBlockedMode()) {
      _log("heartbeat_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_startWsHeartbeat",
        heartbeatReason: reason || "",
      }));
      return;
    }
    clearNamedTimer("poll"); // ensure no concurrent poll timer
    _heartbeatGen++; // new generation — stale callbacks from previous cycle will bail
    setNamedTimer("ws_heartbeat", _wsHeartbeatTick, _WS_HEARTBEAT_SEC * 1000, "heartbeat_" + (reason || "start"));
    _log("heartbeat_started", {
      intervalSec: _WS_HEARTBEAT_SEC,
      reason: reason,
      mode: rt.mode,
      wsConnected: rt.wsConnected,
      gen: _heartbeatGen,
    });
  }

  function _stopWsHeartbeat(reason) {
    if (!_timers["ws_heartbeat"]) return; // idempotent
    clearNamedTimer("ws_heartbeat");
    _log("heartbeat_stopped", { reason: reason, mode: rt.mode });
  }

  function _ensureWsHeartbeat(reason) {
    if (isTerminalBlockedMode()) {
      _log("heartbeat_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_ensureWsHeartbeat",
        heartbeatReason: reason || "",
      }));
      return;
    }
    if (rt.mode !== "ws-live") return;
    if (_timers["ws_heartbeat"]) return; // already scheduled
    _startWsHeartbeat(reason);
  }

  function _wsHeartbeatTick() {
    if (isTerminalBlockedMode()) {
      _log("heartbeat_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_wsHeartbeatTick",
      }));
      return;
    }
    // Mode guard: only execute heartbeat in ws-live
    if (rt.mode !== "ws-live") {
      _log("heartbeat_tick_skipped", { reason: "not_ws_live", mode: rt.mode });
      return;
    }

    var gen = _heartbeatGen;
    var wsOpen = false;
    try { wsOpen = !!(rt.ws && rt.ws.readyState === WebSocket.OPEN); } catch (e) {}
    var ageMs = Date.now() - (Number(rt.wsLastMessageAt || rt.wsOpenedAt) || Date.now());

    _log("heartbeat_tick", {
      mode: rt.mode,
      wsConnected: rt.wsConnected,
      wsOpen: wsOpen,
      ageMs: ageMs,
      gen: gen,
    });

    if (!wsOpen || ageMs > rt.wsHeartbeatTimeoutMs) {
      _log("ws_heartbeat_timeout", { ageMs: ageMs, timeoutMs: rt.wsHeartbeatTimeoutMs, wsOpen: wsOpen });
      try {
        if (rt.ws) rt.ws.close(4000, "heartbeat_timeout");
      } catch (e) {
        rt.wsConnected = false;
        setMode("fallback-poll", "heartbeat_timeout");
      }
      return;
    }

    if (gen === _heartbeatGen && rt.mode === "ws-live") {
      _startWsHeartbeat("tick_continue");
    }
  }

  function _wsLiveStatusIntervalMs() {
    var sec = Number(cfg.WS_LIVE_STATUS_CHECK_SEC) || 45;
    sec = Math.max(5, Math.min(60, sec));
    try {
      var jf = Math.abs(Number(rt.refreshJitterFrac) || 0);
      sec += Math.round(jf * 3);
    } catch (e) {}
    return sec * 1000;
  }

  function _startWsLiveStatusSafeguard(reason) {
    if (isTerminalBlockedMode()) {
      _log("ws_live_status_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_startWsLiveStatusSafeguard",
        reason: reason || "",
      }));
      return;
    }
    if (rt.mode !== "ws-live") return;

    rt.wsLiveStatusGen++;
    var gen = rt.wsLiveStatusGen;
    var delayMs = _wsLiveStatusIntervalMs();

    setNamedTimer("ws_live_status", function () {
      _wsLiveStatusSafeguardTick(gen);
    }, delayMs, "ws_live_status_" + (reason || "start"));

    _log("ws_live_status_started", {
      intervalSec: Math.round(delayMs / 1000),
      reason: reason || "",
      gen: gen,
    });
  }

  function _stopWsLiveStatusSafeguard(reason) {
    if (!_timers["ws_live_status"]) return;
    clearNamedTimer("ws_live_status");
    rt.wsLiveStatusGen++;
    _log("ws_live_status_stopped", { reason: reason || "", mode: rt.mode });
  }

  async function _wsLiveStatusSafeguardTick(gen) {
    if (isTerminalBlockedMode()) return;
    if (rt.mode !== "ws-live") return;
    if (gen !== rt.wsLiveStatusGen) return;

    if (isFetching) {
      _startWsLiveStatusSafeguard("fetch_in_progress");
      return;
    }

    try {
      var st = await safeFetchStatus();
      if (isTerminalBlockedMode() || (st && st._blocked)) return;

      if (st && st.reload === true) {
        _log("ws_live_status_reload", {});
        try { window.location.reload(); } catch (e) { window.location.href = window.location.href; }
        return;
      }

      var serverRev = st && st.schedule_revision !== undefined ? parseInt(st.schedule_revision, 10) : 0;
      var localRev = Number(rt.scheduleRevision || 0);
      var needsFetch = !!(st && st.fetch_required === true);
      if (!needsFetch && serverRev && serverRev > localRev) needsFetch = true;

      _log("ws_live_status_tick", {
        fetchRequired: needsFetch,
        serverRev: serverRev || null,
        localRev: localRev || null,
      });

      if (needsFetch) {
        rt.pendingRev = serverRev || rt.pendingRev || null;
        rt.forceFetchSnapshot = true;
        scheduleNext(0.1, "ws_invalidate");
        return;
      }
    } catch (e) {
      _log("ws_live_status_error", { error: String(e && e.message ? e.message : e) });
    }

    _startWsLiveStatusSafeguard("tick_continue");
  }

  function maybeLogPollState(activeWindow, nextPollSec) {
    if (!isDebug()) return;
    const now = Date.now();
    const last = Number(rt.pollStateLastLogTs) || 0;
    if (last && (now - last) < 10 * 60 * 1000) return; // 10 minutes
    rt.pollStateLastLogTs = now;
    try {
      console.log(
        "[display] poll-state",
        {
          activeWindow: !!activeWindow,
          nextPollSec: Number(nextPollSec) || 0,
          status304Streak: Number(rt.status304Streak) || 0,
          statusEverySec: Number(rt.statusEverySec) || 0,
          scheduleRevision: Number(rt.scheduleRevision) || 0,
          at: new Date().toISOString(),
        }
      );
    } catch (e) {}
  }

  function revStorageKey() {
    // Scope by token to avoid collisions when multiple screens are opened on the same browser.
    const t = (getToken() || "").toString();
    const safe = encodeURIComponent(t).slice(0, 80);
    return "display_rev:" + safe;
  }

  function _stableUnitInterval(seed) {
    try {
      var s = String(seed || "");
      if (!s) return Math.random();
      var h = 2166136261;
      for (var i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i);
        h += (h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24);
      }
      return ((h >>> 0) % 1000000) / 1000000;
    } catch (e) {
      return Math.random();
    }
  }

  (function initRevision() {
    try {
      const raw = localStorage.getItem(revStorageKey()) || "";
      const n = parseInt(raw, 10);
      rt.scheduleRevision = isNaN(n) ? 0 : Math.max(0, n);
    } catch (e) {
      rt.scheduleRevision = 0;
    }
  })();

  // Pick a stable per-device jitter: ~±25% لتوزيع أفضل للحمل مع عدد كبير من الشاشات
  (function initRefreshJitter() {
    try {
      const seed = getOrCreateDeviceId() || window.location.pathname || "display";
      const v = (_stableUnitInterval("poll:" + seed) * 0.5) - 0.25; // -0.25..+0.25
      rt.refreshJitterFrac = Math.abs(v) < 0.01 ? 0.20 : v;
    } catch (e) {
      rt.refreshJitterFrac = 0.20;
    }
  })();

  // ===========================================================================
  // ===== Client-Side Schedule Engine (day_path) ==============================
  // ===========================================================================
  // Uses the full day timeline received from the server to handle ALL period/break
  // transitions locally. This eliminates the need for server polling on time-based
  // transitions — the server is only contacted when data changes (via WebSocket push).
  // ===========================================================================

  const _dayEngine = {
    blocks: [],       // sorted [{kind,label,from,to,fromMs,toMs,index}, ...]
    lastDayKey: "",   // signature to detect changes
    ringPoints: [],   // sorted epoch-ms boundaries to ring on
  };

  /**
   * Load day_path from a snapshot payload.
   * Call once per snapshot render (idempotent if data unchanged).
   */
  function dayEngineLoad(payload) {
    if (!payload) return;
    var dp = payload.day_path;
    if (!Array.isArray(dp) || !dp.length) return;

    // Build a signature to avoid re-processing identical data.
    var sig = "";
    try { sig = JSON.stringify(dp); } catch (e) { sig = String(dp.length); }
    if (sig === _dayEngine.lastDayKey) return;
    _dayEngine.lastDayKey = sig;

    var base = nowMs();
    var blocks = [];
    for (var i = 0; i < dp.length; i++) {
      var b = dp[i];
      if (!b || !b.from || !b.to) continue;
      var fMs = hmToMs(b.from, base);
      var tMs = hmToMs(b.to, base);
      if (!fMs || !tMs || tMs <= fMs) continue;
      blocks.push({
        kind: safeText(b.kind || b.type || "period").toLowerCase(),
        label: safeText(b.label || ""),
        class: safeText(b["class"] || b.class_name || ""),
        teacher: safeText(b.teacher || ""),
        from: safeText(b.from),
        to: safeText(b.to),
        fromMs: fMs,
        toMs: tMs,
        index: b.index || null,
      });
    }
    blocks.sort(function (a, b) { return a.fromMs - b.fromMs; });
    _dayEngine.blocks = blocks;

    // Build unique bell boundaries (start/end of period/break blocks).
    // Using time boundaries directly keeps bell behavior independent from UI counters.
    var ringMap = Object.create(null);
    for (var j = 0; j < blocks.length; j++) {
      var rb = blocks[j];
      if (!rb) continue;
      var knd = safeText(rb.kind || "").toLowerCase();
      if (knd !== "period" && knd !== "break") continue;
      if (rb.fromMs && isFinite(Number(rb.fromMs))) ringMap[String(Math.floor(Number(rb.fromMs)))] = 1;
      if (rb.toMs && isFinite(Number(rb.toMs))) ringMap[String(Math.floor(Number(rb.toMs)))] = 1;
    }
    var points = Object.keys(ringMap)
      .map(function (x) { return parseInt(x, 10); })
      .filter(function (x) { return isFinite(x) && x > 0; })
      .sort(function (a, b) { return a - b; });
    _dayEngine.ringPoints = points;

    // New day/schedule signature: reset boundary ring memory so future boundaries can ring.
    lastBellBoundaryCheckMs = nowMs();
    lastBellBoundaryDaySig = sig;
    for (var rk in bellBoundarySeen) {
      if (Object.prototype.hasOwnProperty.call(bellBoundarySeen, rk)) delete bellBoundarySeen[rk];
    }
    scheduleNextBoundaryBell();
  }

  /**
   * Find the active block and the one after it, purely from local time.
   * Returns { current: block|null, next: block|null, dayOver: bool }
   */
  function dayEngineFindNow() {
    var blocks = _dayEngine.blocks;
    if (!blocks.length) return { current: null, next: null, dayOver: false };

    var n = nowMs();
    var current = null;
    var next = null;

    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (n >= b.fromMs && n < b.toMs) {
        current = b;
        next = (i + 1 < blocks.length) ? blocks[i + 1] : null;
        return { current: current, next: next, dayOver: false };
      }
    }

    // Not inside any block — check if before first or after last.
    if (n < blocks[0].fromMs) {
      // Before first block: "before" state, next = first block
      return { current: null, next: blocks[0], dayOver: false };
    }

    if (n >= blocks[blocks.length - 1].toMs) {
      // After last block: day over
      return { current: null, next: null, dayOver: true };
    }

    // In a gap between blocks (shouldn't happen with well-formed data)
    for (var j = 0; j < blocks.length - 1; j++) {
      if (n >= blocks[j].toMs && n < blocks[j + 1].fromMs) {
        return { current: null, next: blocks[j + 1], dayOver: false };
      }
    }

    return { current: null, next: null, dayOver: false };
  }

  function dayEngineResolvePeriodIndex(block) {
    if (!block) return null;

    var direct = getPeriodIndex(block);
    if (direct && Number(direct) > 0) return Number(direct);

    var blocks = _dayEngine.blocks || [];
    if (!blocks.length) return null;

    var ord = 0;
    var bf = safeText(block.from || "");
    var bt = safeText(block.to || "");
    var bl = safeText(block.label || "");

    for (var i = 0; i < blocks.length; i++) {
      var b = blocks[i];
      if (safeText(b.kind || "").toLowerCase() !== "period") continue;
      ord += 1;

      var idx = getPeriodIndex(b);
      var candidate = idx && Number(idx) > 0 ? Number(idx) : ord;

      if (b === block) return candidate;

      var sameWindow = safeText(b.from || "") === bf && safeText(b.to || "") === bt;
      var sameLabel = bl && safeText(b.label || "") === bl;
      if (sameWindow || sameLabel) return candidate;
    }

    return null;
  }

  function dayEngineReferencePeriodIndex() {
    var runtime = Number(rt && rt.activePeriodIndex);
    if (isFinite(runtime) && runtime > 0) return Math.floor(runtime);

    if (!_dayEngine.blocks.length) return null;

    var res = dayEngineFindNow();
    if (!res || res.dayOver) return null;

    if (res.current) {
      var kind = safeText(res.current.kind || "").toLowerCase();
      if (kind === "period") {
        var curIdx = dayEngineResolvePeriodIndex(res.current);
        if (curIdx && curIdx > 0) return curIdx;
      }
      if (kind === "break") {
        var nextIdx = dayEngineResolvePeriodIndex(res.next);
        if (nextIdx && nextIdx > 0) return nextIdx;
      }
    }

    if (res.next) {
      var idx = dayEngineResolvePeriodIndex(res.next);
      if (idx && idx > 0) return idx;
    }

    return null;
  }

  /**
   * Advance the display to a specific block from the day engine.
   * This replaces optimisticAdvanceToNextBlock for day_path-based transitions.
   */
  function dayEngineApplyBlock(block, stateType, nextBlock) {
    if (!block) return false;

    var stType = stateType || block.kind || "period";
    var badge = "حالة اليوم";
    var title = "";
    var stateObj = { from: block.from, to: block.to, label: block.label || "" };
    var currentObj = block;

    if (stType === "period") {
      badge = "درس";
      title = formatPeriodTitle(block);
      stateObj.label = block.label || title;
    } else if (stType === "break") {
      badge = "استراحة";
      title = block.label || "استراحة";
      stateObj.label = title;
    } else if (stType === "before") {
      badge = DISPLAY_COPY.beforeBadge;
      title = DISPLAY_COPY.beforeTitle;
      stateObj.label = title;
      currentObj = null;
    }

    var range = fmtTimeRange(block.from, block.to);

    setTextIfChanged(dom.heroTitle, title);
    setTextIfChanged(dom.heroRange, range);
    setTextIfChanged(dom.badgeKind, badge);

    var nowMsVal = nowMs();
    var targetMs = (stType === "before") ? block.fromMs : block.toMs;
    var rem = Math.max(0, Math.floor((targetMs - nowMsVal) / 1000));

    countdownSeconds = rem;
    hasActiveCountdown = true;
    progressRange = { start: block.fromMs, end: block.toMs };

    var blockPeriodIdx = dayEngineResolvePeriodIndex(block);
    var nextPeriodIdx = dayEngineResolvePeriodIndex(nextBlock);

    if (stType === "period") {
      rt.activePeriodIndex = blockPeriodIdx || rt.activePeriodIndex;
      rt.activeFromHM = block.from;
    } else if (stType === "break") {
      // During break, use the upcoming period index so ended standby items disappear immediately.
      rt.activePeriodIndex = nextPeriodIdx || rt.activePeriodIndex;
      rt.activeFromHM = block.from;
    } else if (stType === "before") {
      // Before first period (or in gaps), keep filter aligned with the next period.
      rt.activePeriodIndex = nextPeriodIdx || rt.activePeriodIndex;
      rt.activeFromHM = block.from;
    }
    rt.activeToHM = block.to;
    rt.activeStateType = stType;
    rt.activeTargetHM = (stType === "before") ? block.from : block.to;
    rt.activeTargetMs = isFinite(Number(targetMs)) ? Number(targetMs) : null;
    rt.dayOver = false;
    applyBoardStatePresentation(stType, rem > 0);

    // Record the currently rendered core state, but do not mark countdown-zero
    // as handled yet. Zero should only be consumed when a boundary transition
    // actually fires, otherwise TVs that land directly on 00:00 can get stuck.
    lastStateCoreSig = stType + "||" + safeText(title || "") + "||" + safeText(block.from || "") + "||" + safeText(block.to || "");

    // Re-filter standby list.
    try {
      if (lastPayloadForFiltering) renderStandby(lastPayloadForFiltering.standby || []);
    } catch (e) {}

    // Re-render mini schedule.
    try {
      if (lastPayloadForFiltering) renderMiniSchedule(lastPayloadForFiltering, nowMsVal);
    } catch (e) {}

    try {
      renderCurrentChips(stType, stateObj, currentObj);
    } catch (e) {}

    try {
      renderNextLabel(nextBlock || null);
    } catch (e) {}

    try {
      renderPeriodClasses(stType === "period" ? pickRuntimePeriodClasses(lastPayloadForFiltering) : []);
    } catch (e) {}

    return true;
  }

  /**
   * Handle countdown reaching zero using the day engine.
   * Chains through all blocks locally without needing a server fetch.
   * Returns true if it handled the transition locally.
   */
  function dayEngineOnZero() {
    if (!_dayEngine.blocks.length) return false;

    var res = dayEngineFindNow();

    if (res.dayOver) {
      // Day ended — show day-over state, no need to fetch.
      return dayEngineApplyDayOver();
    }

    if (res.current) {
      // We're inside a block — show it.
      checkStateTransitionBell(res.current.kind);
      return dayEngineApplyBlock(res.current, res.current.kind, res.next);
    }

    if (res.next) {
      // We're between blocks or before the first — show "waiting for next".
      checkStateTransitionBell("before");
      return dayEngineApplyBlock(res.next, "before", res.next);
    }

    return false;
  }

  function dayEngineRuntimeSlotSig() {
    var type = safeText(rt && rt.activeStateType ? rt.activeStateType : "").toLowerCase();
    if (rt && rt.dayOver) type = "after";
    return type + "||" + safeText(rt && rt.activeFromHM ? rt.activeFromHM : "") + "||" + safeText(rt && rt.activeToHM ? rt.activeToHM : "");
  }

  function dayEngineDesiredSlotSig(res) {
    if (!res || typeof res !== "object") return "";
    if (res.dayOver) return "after||||";
    if (res.current) {
      var k = safeText(res.current.kind || "period").toLowerCase();
      return k + "||" + safeText(res.current.from || "") + "||" + safeText(res.current.to || "");
    }
    if (res.next) {
      return "before||" + safeText(res.next.from || "") + "||" + safeText(res.next.to || "");
    }
    return "";
  }

  function dayEngineApplyDayOver() {
    const afterCopy = getAfterDayOverCopy();
    rt.dayOver = true;
    rt.activePeriodIndex = null;
    rt.activeStateType = "after";
    rt.activeFromHM = null;
    rt.activeToHM = null;
    rt.activeTargetHM = null;
    rt.activeTargetMs = null;
    setTextIfChanged(dom.heroTitle, afterCopy.title);
    setTextIfChanged(dom.heroRange, "");
    setTextIfChanged(dom.badgeKind, afterCopy.badge);
    countdownSeconds = null;
    hasActiveCountdown = false;
    progressRange = { start: null, end: null };
    applyBoardStatePresentation("after", false);
    lastStateCoreSig = "after||" + afterCopy.title + "||||";
    lastZeroHandledCoreSig = lastStateCoreSig;
    try { renderCurrentChips("after", { label: afterCopy.title, from: null, to: null }, null); } catch (e) {}
    try { renderNextLabel(null); } catch (e) {}
    // Clear every day-scoped panel at the moment the day ends, not on the next
    // payload. This path fires from the local clock, so without it the board
    // could keep showing standby rows and the honour board for minutes after
    // the last bell — up to a full poll interval out of hours.
    try { renderPeriodClasses([]); } catch (e) {}
    try { renderStandby([]); } catch (e) {}
    try { renderFeaturedPanel(lastPayloadForFiltering); } catch (e) {}
    return true;
  }

  /**
   * تفريغ محرك اليوم.
   *
   * ``dayEngineLoad`` يتجاهل الجداول الفارغة عمدًا حتى لا تمحو استجابةٌ ناقصة
   * جدولًا سليمًا. لكن عند الانتقال إلى يوم بلا دوام يجب أن تختفي كتل الأمس
   * فعليًا، وإلا استمر المحرك في تحريك حصص لا وجود لها اليوم.
   */
  function dayEngineClear() {
    _dayEngine.blocks = [];
    _dayEngine.ringPoints = [];
    _dayEngine.lastDayKey = "";
    lastBellBoundaryDaySig = "";
    for (var key in bellBoundarySeen) {
      if (Object.prototype.hasOwnProperty.call(bellBoundarySeen, key)) delete bellBoundarySeen[key];
    }
  }

  function dayEngineSyncToLocalNow() {
    if (!_dayEngine.blocks.length) return false;
    var res = dayEngineFindNow();
    var want = dayEngineDesiredSlotSig(res);
    if (!want) return false;

    var have = dayEngineRuntimeSlotSig();
    if (want === have) return false;

    var nextType = "before";
    if (res.dayOver) nextType = "after";
    else if (res.current) nextType = safeText(res.current.kind || "period").toLowerCase();

    try { checkStateTransitionBell(nextType); } catch (e) {}

    if (res.dayOver) return dayEngineApplyDayOver();
    if (res.current) return dayEngineApplyBlock(res.current, nextType, res.next);
    if (res.next) return dayEngineApplyBlock(res.next, "before", res.next);
    return false;
  }

  function pickTokenFromUrl() {
    try {
      const qs = new URLSearchParams(window.location.search);
      return (qs.get("token") || qs.get("t") || "").trim();
    } catch (e) {
      return "";
    }
  }

  function pickTokenFromSnapshotUrl() {
    try {
      const raw = (cfg.SNAPSHOT_URL || "").toString().trim();
      if (!raw) return "";
      const u = new URL(raw, window.location.origin);
      const path = (u.pathname || "").toString();
      // Supports: /api/display/snapshot/<token>/  (and aliases today/live)
      const m = path.match(/\/api\/display\/(?:snapshot|today|live)\/([^\/]+)\/?$/i);
      if (!m || !m[1]) return "";
      return decodeURIComponent(m[1]).trim();
    } catch (e) {
      return "";
    }
  }

  function getToken() {
    return (cfg.SERVER_TOKEN || pickTokenFromUrl() || pickTokenFromSnapshotUrl() || "").trim();
  }

  function resolveSnapshotUrl() {
    if (cfg.SNAPSHOT_URL) return cfg.SNAPSHOT_URL;
    const t = getToken();
    if (t) return "/api/display/snapshot/" + encodeURIComponent(t) + "/";
    return "/api/display/snapshot/";
  }

  function resolveStatusUrl() {
    // Lightweight endpoint; prefer token in path when available (same pattern as snapshot).
    const t = getToken();
    if (t) return "/api/display/status/" + encodeURIComponent(t) + "/";
    return "/api/display/status/";
  }

  // ===== Farewell beacon =====================================================
  // A television being switched off and a school losing its internet look
  // identical from the server: the heartbeat simply stops. One beacon on the
  // way out is what lets the offline alert name the cause instead of listing
  // every possibility. Fire-and-forget — it never blocks teardown or retries.
  var _goodbyeSent = false;

  function sendGoodbyeBeacon(reason) {
    if (_goodbyeSent) return;
    var t = "";
    try {
      t = (getToken() || "").toString().trim();
    } catch (e) {
      t = "";
    }
    if (!t) return;
    _goodbyeSent = true;

    var url = "/api/display/goodbye/" + encodeURIComponent(t) + "/";
    var online = null;
    try {
      if (typeof navigator !== "undefined" && typeof navigator.onLine === "boolean") online = navigator.onLine;
    } catch (e) {}
    var payload = JSON.stringify({ reason: String(reason || "pagehide"), online: online });

    // text/plain keeps this a simple request: no preflight, no cookies needed.
    try {
      if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
        if (navigator.sendBeacon(url, new Blob([payload], { type: "text/plain;charset=UTF-8" }))) return;
      }
    } catch (e) {}
    try {
      fetch(url, {
        method: "POST",
        body: payload,
        keepalive: true,
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
      }).catch(function () {});
    } catch (e) {}
  }

  function resolveImageURL(raw) {
    if (!raw) return "";
    let s = String(raw).trim();
    if (!s) return "";
    if (/^data:image\//i.test(s) || /^blob:/i.test(s)) return s;
    if (/^https?:\/\//i.test(s)) return s.replace(/^https?:\/\//i, "//");
    if (s.charAt(0) === "/") return s;
    let pref = cfg.MEDIA_PREFIX || "/media/";
    if (pref.charAt(pref.length - 1) !== "/") pref += "/";
    return pref + s.replace(/^\.?\/*/, "");
  }

  // ===== Time helpers =====
  // serverOffsetMs: difference between server time and local time
  // NOT persisted to localStorage to avoid stale values causing wrong period display
  let serverOffsetMs = 0;
  let hasServerClockSync = false;
  
  // ✅ CLOCK DRIFT DETECTION: مراقبة تغيرات التوقيت المحلي
  let lastLocalTime = Date.now();
  let lastCheckTime = Date.now();
  let clockDriftDetected = false;
  
  // ✅ THROTTLING: منع الطلبات الزائدة على السيرفر
  let lastReSyncTime = 0;
  const RE_SYNC_COOLDOWN = 30000; // 30 ثانية بين كل re-sync
  let lastServerSyncAt = 0; // آخر وقت وصلنا فيه وقت خادم صالح (status/snapshot)
  let lastServerHeaderSyncAt = 0; // وقت آخر تزامن من ترويسة غير مخزنة مؤقتًا
  
  let serverTzOffsetMin = null;
  let serverLocalDateStr = null; // YYYY-MM-DD
  let serverDayStartMs = null; // epoch ms for server local day start
  
  function nowMs() {
    return Date.now() + serverOffsetMs;
  }

  function applyServerNowMs(serverNowMs, source) {
    const n = Number(serverNowMs);
    if (!isFinite(n) || n <= 0) return;
    const measured = n - Date.now();
    const delta = measured - serverOffsetMs;
    // Snap hard on the first sync, on detected clock jumps, or while still off
    // by at least one second so the bell/countdown stay aligned to the server.
    if (!hasServerClockSync || clockDriftDetected || Math.abs(delta) >= 1000) {
      serverOffsetMs = measured;
    } else {
      // Only smooth small jitter once we are already close to server time.
      serverOffsetMs = Math.round(serverOffsetMs * 0.5 + measured * 0.5);
    }
    hasServerClockSync = true;
    lastServerSyncAt = Date.now();
    if (source === "header") lastServerHeaderSyncAt = lastServerSyncAt;
    _log("clock_sync", {
      source: source || "unknown",
      offsetMs: Math.round(serverOffsetMs),
      sampleAgeMs: Math.max(0, Math.round(Date.now() - n)),
    });
    
    // ✅ تحديث آخر وقت معروف للمراقبة
    lastLocalTime = Date.now();
    lastCheckTime = Date.now();
    clockDriftDetected = false;

    // A corrected offset changes every epoch boundary derived from HH:MM.
    // Re-arm the precise bell timer so it remains aligned after clock sync.
    try { scheduleNextBoundaryBell(); } catch (e) {}
  }

  // ✅ CLOCK DRIFT DETECTION: كشف تغييرات التوقيت المحلي الفجائية
  // ⚠️ IMPORTANT: هذه الدالة لا ترسل أي request، فقط تكشف التغيير
  function detectClockDrift() {
    const now = Date.now();
    const expectedElapsed = now - lastCheckTime;
    
    // إذا مر أقل من 100ms، تجاهل (قريب جداً)
    if (expectedElapsed < 100) return false;
    
    // إذا مر أكثر من 3 ثواني، قد يكون tab sleep - تحقق بحذر
    if (expectedElapsed > 3000) {
      lastCheckTime = now;
      lastLocalTime = now;
      return true; // نطلب re-sync لأننا كنا inactive
    }
    
    // حساب الفرق بين الوقت الفعلي والمتوقع
    const actualElapsed = now - lastLocalTime;
    const drift = Math.abs(actualElapsed - expectedElapsed);
    
    // إذا كان الفرق أكثر من ثانية واحدة = تغيير في الوقت المحلي
    if (drift > 1000) {
      lastCheckTime = now;
      lastLocalTime = now;
      clockDriftDetected = true;
      return true;
    }
    
    lastCheckTime = now;
    lastLocalTime = now;
    return false;
  }

  // ✅ THROTTLED RE-SYNC: طلب re-sync مع حماية من الطلبات الزائدة
  // ⚠️ COST PROTECTION: لا يرسل أكثر من request واحد كل 30 ثانية
  function requestReSyncIfNeeded() {
    if (isTerminalBlockedMode()) {
      _log("wake_blocked_due_to_binding_loss", _logContext({
        sourcePath: "requestReSyncIfNeeded",
        action: "resync_skipped",
      }));
      return;
    }
    if (isBlocked) return;
    if (rt && rt.wsConnected) {
      _log("resync_skipped", { reason: "ws_connected" });
      return;
    }
    if (rt && rt.mode === "sleeping") {
      _log("resync_skipped", { reason: "sleeping" });
      return;
    }
    const now = Date.now();

    // We already receive server time on normal status/snapshot responses.
    // If sync is still fresh, skip extra re-sync requests completely.
    const pollEveryMs = Math.max(1000, Math.round((Number(rt && rt.statusEverySec) || Number(cfg.REFRESH_EVERY) || 20) * 1000));
    const freshSyncWindowMs = Math.max(30000, Math.round(pollEveryMs * 1.5));
    if (lastServerSyncAt > 0 && now - lastServerSyncAt < freshSyncWindowMs) return;

    // Avoid piling re-sync while a status request is already in-flight.
    if (inflightStatus) return;

    const timeSinceLastSync = now - lastReSyncTime;
    
    // ✅ COOLDOWN: إذا كان آخر re-sync قبل أقل من 5 ثوانٍ، تجاهل
    if (timeSinceLastSync < RE_SYNC_COOLDOWN) {
      if (isDebug()) setDebugText(`Re-sync cooldown: ${(RE_SYNC_COOLDOWN - timeSinceLastSync) / 1000}s remaining`);
      return;
    }
    
    // ✅ UPDATE: نسجل وقت آخر re-sync
    lastReSyncTime = now;
    
    // ✅ SEND: الآن فقط نرسل الطلب
    _log("resync_requested", { mode: rt.mode, timeSinceLastSync: timeSinceLastSync });
    if (isDebug()) setDebugText("Clock drift detected - re-syncing...");
    safeFetchStatus().catch(() => {});
  }

  function _parseTzOffsetMinFromIso(iso) {
    const s = (iso || "").toString().trim();
    if (!s) return null;
    // Match trailing timezone: Z or ±HH:MM
    const m = s.match(/(Z|[+-]\d{2}:?\d{2})\s*$/i);
    if (!m || !m[1]) return null;
    const z = m[1].toUpperCase();
    if (z === "Z") return 0;
    const mm = z.match(/^([+-])(\d{2}):?(\d{2})$/);
    if (!mm) return null;
    const sign = mm[1] === "-" ? -1 : 1;
    const hh = parseInt(mm[2], 10);
    const mn = parseInt(mm[3], 10);
    if (!isFinite(hh) || !isFinite(mn)) return null;
    return sign * (hh * 60 + mn);
  }

  function _computeDayStartMs(localDateStr, tzOffsetMin) {
    const ds = (localDateStr || "").toString().trim();
    if (!ds) return null;
    const m = ds.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    const y = parseInt(m[1], 10);
    const mo = parseInt(m[2], 10);
    const d = parseInt(m[3], 10);
    if (!isFinite(y) || !isFinite(mo) || !isFinite(d)) return null;
    const off = Number(tzOffsetMin);
    if (!isFinite(off)) return null;
    // Day start in server timezone => UTC midnight minus offset.
    return Date.UTC(y, mo - 1, d, 0, 0, 0) - off * 60 * 1000;
  }

  function applyServerCalendar(localDateStr, tzOffsetMin) {
    if (!localDateStr) return;
    const off = Number(tzOffsetMin);
    if (!isFinite(off)) return;
    serverLocalDateStr = String(localDateStr);
    serverTzOffsetMin = off;
    serverDayStartMs = _computeDayStartMs(serverLocalDateStr, serverTzOffsetMin);
  }

  function _serverDayStartMsForBase(baseMs) {
    const off = Number(serverTzOffsetMin);
    if (!isFinite(off)) return null;
    const b = Number(baseMs || nowMs());
    if (!isFinite(b) || b <= 0) return null;
    // Convert to "server local wall clock" by shifting with offset, then use UTC getters.
    const wall = new Date(b + off * 60 * 1000);
    const y = wall.getUTCFullYear();
    const mo = wall.getUTCMonth();
    const d = wall.getUTCDate();
    // Day start in server timezone => UTC midnight minus offset.
    return Date.UTC(y, mo, d, 0, 0, 0) - off * 60 * 1000;
  }

  function serverWallNowDate() {
    // Shift by tz offset and interpret via UTC getters/formatting.
    const off = isFinite(Number(serverTzOffsetMin)) ? Number(serverTzOffsetMin) : 0;
    return new Date(nowMs() + off * 60 * 1000);
  }

  // Clock display follows the school's local timezone as reported by the server.
  function localWallNowDate() {
    return serverWallNowDate();
  }

  function fmtLocal12HM(dateObj) {
    const d = dateObj instanceof Date ? dateObj : localWallNowDate();
    const h24 = d.getUTCHours();
    const h12 = h24 % 12 || 12;
    const meridiem = h24 >= 12 ? "م" : "ص";
    return fmt2(h12) + ":" + fmt2(d.getUTCMinutes()) + " " + meridiem;
  }

  function fmtTimeRange(fromHM, toHM) {
    // Bidi-safe: isolate as LTR so times don't visually flip in RTL UI.
    // U+2066 LRI ... U+2069 PDI
    return "\u2066" + toTimeStr(fromHM) + " → " + toTimeStr(toHM) + "\u2069";
  }

  function toTimeStr(t) {
    if (!t) return "--:--";
    const parts = String(t).split(":");
    if (parts.length < 2) return "--:--";
    return fmt2(parts[0]) + ":" + fmt2(parts[1]);
  }

  function hmToMs(hm, baseMs) {
    if (!hm) return null;
    const parts = String(hm).split(":");
    if (parts.length < 2) return null;
    const h = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    if (isNaN(h) || isNaN(m)) return null;

    // Use the caller's reference time when available so countdown logic stays
    // deterministic within the same render/tick. Fallback to nowMs() only when
    // no base was provided.
    const b = isFinite(Number(baseMs)) ? Number(baseMs) : nowMs();
    
    // Prefer server-derived day start to avoid relying on the device timezone.
    // We compute it from server tz offset + base time to stay correct across midnight
    // even if we don't fetch a new snapshot yet.
    const dayStart = _serverDayStartMsForBase(b) || (serverDayStartMs && isFinite(serverDayStartMs) ? Number(serverDayStartMs) : null);
    if (dayStart && isFinite(dayStart)) {
      let t = dayStart + (h * 60 + m) * 60 * 1000;
      // Handle midnight rollover (e.g., base near 23:55 and target 00:05 should be next day).
      const diff = t - b;
      const dayMs = 24 * 60 * 60 * 1000;
      if (diff < -18 * 60 * 60 * 1000) t += dayMs;
      else if (diff > 18 * 60 * 60 * 1000) t -= dayMs;
      return t;
    }

    // Fallback: device timezone (last resort).
    const d = new Date(b);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m, 0).getTime();
  }

  function isEnded(endHM, baseMs) {
    const end = hmToMs(endHM, baseMs);
    if (!end) return false;
    // ✅ FIX: استخدام nowMs() دائماً للحصول على الوقت الصحيح المتزامن
    return nowMs() >= end;
  }

  function isBoundaryReached(targetHM, targetMs, refMs) {
    const n = isFinite(Number(refMs)) ? Number(refMs) : nowMs();
    let t = isFinite(Number(targetMs)) ? Number(targetMs) : null;
    if (!t && targetHM) t = hmToMs(targetHM, n);
    if (!t || !isFinite(t)) return false;
    return n >= t;
  }

  function isNowBetween(startHM, endHM, baseMs) {
    const s = hmToMs(startHM, baseMs);
    const e = hmToMs(endHM, baseMs);
    if (!s || !e) return false;
    // ✅ FIX: استخدام nowMs() دائماً للحصول على الوقت الصحيح المتزامن
    const n = nowMs();
    return n >= s && n < e;
  }

  // ===== Period index/title =====
  function getPeriodIndex(periodObj) {
    if (!periodObj || typeof periodObj !== "object") return null;
    const raw =
      _nc(periodObj.index,
      _nc(periodObj.period_index,
      _nc(periodObj.current_period_index,
      _nc(periodObj.current_idx,
      _nc(periodObj.idx,
      _nc(periodObj.period,
      _nc(periodObj.period_no,
      _nc(periodObj.current_period_no,
      _nc(periodObj.period_number,
      _nc(periodObj.periodNum,
      _nc(periodObj.slot_index,
      periodObj.order)))))))))));

    if (raw === null || raw === undefined || raw === "") return null;
    
    // محاولة تحويل لرقم مباشرة
    const n = parseInt(String(raw), 10);
    if (isFinite(n) && n > 0) return n;
    
    // ✅ FIX: استخراج الرقم من النص العربي (مثل "الحصة الثانية" → 2)
    const rawStr = String(raw).trim();
    const arabicMap = {
      "الأولى": 1, "الاولى": 1, "أولى": 1, "اولى": 1,
      "الثانية": 2, "ثانية": 2,
      "الثالثة": 3, "ثالثة": 3,
      "الرابعة": 4, "رابعة": 4,
      "الخامسة": 5, "خامسة": 5,
      "السادسة": 6, "سادسة": 6,
      "السابعة": 7, "سابعة": 7,
      "الثامنة": 8, "ثامنة": 8,
      "التاسعة": 9, "تاسعة": 9,
      "العاشرة": 10, "عاشرة": 10,
      "الحادية عشرة": 11, "حادية عشرة": 11,
      "الثانية عشرة": 12, "ثانية عشرة": 12,
      "الثالثة عشرة": 13, "ثالثة عشرة": 13,
      "الرابعة عشرة": 14, "رابعة عشرة": 14,
      "الخامسة عشرة": 15, "خامسة عشرة": 15,
    };
    
    // البحث عن الرقم في النص
    for (const [key, value] of Object.entries(arabicMap)) {
      if (rawStr.includes(key)) return value;
    }
    
    return null;
  }

  function arabicOrdinalFeminine(n) {
    const idx = parseInt(String(n), 10);
    if (!idx || isNaN(idx) || idx <= 0) return null;

    const ord = [
      "الأولى",
      "الثانية",
      "الثالثة",
      "الرابعة",
      "الخامسة",
      "السادسة",
      "السابعة",
      "الثامنة",
      "التاسعة",
      "العاشرة",
      "الحادية عشرة",
      "الثانية عشرة",
      "الثالثة عشرة",
      "الرابعة عشرة",
      "الخامسة عشرة",
    ];

    if (idx <= ord.length) return ord[idx - 1];
    return null;
  }

  function formatPeriodTitle(p) {
    const idx = getPeriodIndex(p);
    if (!idx) return "الحصة";

    const ord = arabicOrdinalFeminine(idx);
    if (ord) return "الحصة " + ord;

    // fallback for uncommon indices
    return "الحصة رقم " + toArabicDigits(idx);
  }

  function getCurrentActivityLabel(stType, currentObj, stateObj) {
    const cur = currentObj || {};
    const st = stateObj || {};
    if (stType === "period") {
      return safeText(cur.label || cur.subject || st.label || "");
    }
    return safeText(cur.label || st.label || "");
  }

  function renderNextLabel(nextObj) {
    if (!dom.nextLabel) return;

    if (!nextObj) {
      setTextIfChanged(dom.nextLabel, "—");
      return;
    }

    const kind = safeText(nextObj.kind || nextObj.type || "").trim().toLowerCase();
    const from = toTimeStr(nextObj.from || nextObj.starts_at);
    let title = "";

    if (kind === "period") {
      const periodTitle = formatPeriodTitle(nextObj);
      const activity = safeText(nextObj.label || "");
      title = activity && activity !== periodTitle ? periodTitle + " - " + activity : periodTitle;
    } else if (kind === "break") {
      title = safeText(nextObj.label || "استراحة");
    } else {
      title = safeText(nextObj.label || "");
    }

    setTextIfChanged(dom.nextLabel, title ? (from !== "--:--" ? title + " (" + from + ")" : title) : "—");
  }

  function getCurrentPeriodIdxFromPayload(payload) {
    if (!payload) return null;
    const cur = payload.current_period || null;
    const n = getPeriodIndex(cur);
    if (n) return n;

    const st = payload.state || {};
    const stCur = st && typeof st === "object" ? st.current || null : null;
    const stNext = st && typeof st === "object" ? st.next || null : null;
    const n2 =
      getPeriodIndex(st) ||
      getPeriodIndex(stCur) ||
      getPeriodIndex(stNext) ||
      (st && typeof st.current_period_index !== "undefined" ? parseInt(st.current_period_index, 10) : NaN) ||
      (st && typeof st.period_index !== "undefined" ? parseInt(st.period_index, 10) : NaN);

    return isNaN(n2) ? null : n2;
  }

  // ===== Day over + standby filter =====
  function computeDayOver(payload, baseMs) {
    const s = (payload && payload.state) || {};
    const stType = normalizeDisplayStateType(s.type || "");
    const stateReason = getStateReason(s);

    if (stType === "holiday" || stateReason === "holiday" || stateReason === "before_hours") return false;
    if (stType === "off" || stType === "after" || stateReason === "after_hours") return true;

    if (Array.isArray(payload && payload.day_path) && payload.day_path.length) {
      const anyNotEnded = payload.day_path.some((x) => x && !isEnded(x.to, baseMs));
      if (!anyNotEnded) return true;
    }

    if (!payload.next_period && s.to && isEnded(s.to, baseMs)) return true;

    return false;
  }

  function shouldKeepStandbyItem(x, baseMs) {
    if (!x) return false;

    // ✅ FIX: إخفاء حصص الانتظار التي مضى وقتها
    // حصة الانتظار للحصة X تظهر طوال الحصة X وتختفي عند بداية الحصة X+1
    // مثال: حصة انتظار للحصة 2 تظهر أثناء الحصة 2، وتختفي عند بداية الحصة 3
    
    // 1) فلترة بالأرقام (الأفضل) - نبحث في جميع الحقول الممكنة
    const idx = getPeriodIndex(x);
    const refIdx = dayEngineReferencePeriodIndex() || (rt.activePeriodIndex ? Number(rt.activePeriodIndex) : null);

    if (idx && refIdx) {
      // نعرض الحصص التي >= الحصة الحالية (أي الحصة الحالية والحصص القادمة)
      // مثال: إذا الحصة الحالية = 2، نعرض حصة انتظار 2، 3، 4...
      // عند بداية الحصة 3، حصة انتظار الحصة 2 ستختفي
      const keep = Number(idx) >= Number(refIdx);
      return keep;
    }

    // 2) fallback بالوقت إذا ما فيه رقم (نادر لكن للتوافقية)
    const from = x.from || x.start || x.starts_at;
    if (rt.activeFromHM && from) {
      const a = hmToMs(rt.activeFromHM, baseMs);
      const b = hmToMs(from, baseMs);
      // نعرض فقط الحصص التي وقتها في المستقبل (> وليس >=)
      if (a && b) return b > a;
    }

    // 3) إذا ما نقدر نحدد بالرقم أو الوقت، نعرضه
    // (هذا احتياطي فقط - لا يجب الوصول له في الظروف العادية)
    return true;
  }

  // ===== Adaptive board presentation =====
  function setBoardData(name, value) {
    try {
      if (!document.body || !name) return;
      document.body.dataset[name] = String(value);
    } catch (e) {}
  }

  function refreshAdaptiveLayout() {
    if (!document.body) return;

    const periodEmpty = document.body.dataset.periodEmpty === "1";
    const standbyEmpty = document.body.dataset.standbyEmpty === "1";
    const featuredEmpty = document.body.dataset.featuredEmpty === "1";
    const periodCount = Math.max(0, parseInt(document.body.dataset.periodCount || "0", 10) || 0);
    const standbyCount = Math.max(0, parseInt(document.body.dataset.standbyCount || "0", 10) || 0);
    const stateType = safeText(document.body.dataset.displayState || "");
    const sideEmpty = periodEmpty && standbyEmpty;
    const quietState =
      stateType === "after" ||
      stateType === "holiday" ||
      stateType === "off";
    const sideMode = sideEmpty
      ? "empty"
      : (periodEmpty ? "standby-only" : (standbyEmpty ? "period-only" : "both"));
    const singleCount = periodEmpty ? standbyCount : periodCount;
    const singleCardHeight = clamp(165 + Math.max(1, singleCount) * 132, 320, 780);

    setBoardData("sideEmpty", sideEmpty ? "1" : "0");
    setBoardData("sideMode", sideMode);
    try {
      document.body.style.setProperty("--side-single-height", singleCardHeight + "px");
    } catch (e) {}
    setBoardData(
      "layoutMode",
      sideEmpty ? (featuredEmpty || quietState ? "ambient" : "wide") : "standard"
    );

    try { scheduleFit(0); } catch (e) {}
  }

  const BOARD_STATE_PRESENTATION = {
    period: {
      status: "حصة جارية الآن",
      activity: "تفاصيل النشاط",
      countdownLabel: "المتبقي",
      glyph: "◉",
    },
    break: {
      status: "وقت الاستراحة",
      activity: "حالة الاستراحة",
      countdownLabel: "تنتهي خلال",
      glyph: "◇",
    },
    before: {
      status: "الاستعداد لليوم",
      activity: "الاستعداد الآن",
      countdownLabel: "يبدأ خلال",
      glyph: "☀",
    },
    after: {
      status: "اكتمل اليوم الدراسي",
      activity: "رسالة اليوم",
      countdownLabel: "يوم مكتمل",
      glyph: "✓",
    },
    holiday: {
      status: "إجازة مدرسية",
      activity: "رسالة المدرسة",
      countdownLabel: "إجازة سعيدة",
      glyph: "✦",
    },
    off: {
      status: "خارج وقت الدوام",
      activity: "رسالة المدرسة",
      countdownLabel: "الشاشة جاهزة",
      glyph: "✦",
    },
    day: {
      status: "اليوم الدراسي",
      activity: "النشاط الحالي",
      countdownLabel: "المتبقي",
      glyph: "◆",
    },
  };

  function applyBoardStatePresentation(stateType, countdownActive) {
    const normalized = normalizeDisplayStateType(stateType || "") || "day";
    const copy = BOARD_STATE_PRESENTATION[normalized] || BOARD_STATE_PRESENTATION.day;

    setBoardData("displayState", normalized);
    setBoardData("countdownActive", countdownActive ? "1" : "0");
    setTextIfChanged(dom.boardStatusText, copy.status);
    setTextIfChanged(dom.activityLabel, copy.activity);
    setTextIfChanged(dom.countdownLabel, copy.countdownLabel);
    setTextIfChanged(dom.countdownGlyph, copy.glyph);
    refreshAdaptiveLayout();
    try { refreshOccasionPresentation(); } catch (e) {}
  }

  function makeBoardEmptyState(message) {
    const msg = document.createElement("div");
    msg.className = "board-empty-state";
    msg.textContent = safeText(message || "لا توجد بيانات حالياً");
    msg.setAttribute("role", "status");
    return msg;
  }

  // ===== Render: Alert =====
  function normalizeAnnouncementLevel(level) {
    const raw = safeText(level || "").trim().toLowerCase();
    if (raw === "danger" || raw === "error" || raw === "critical" || raw === "emergency" || raw === "urgent") return "danger";
    if (raw === "warn" || raw === "warning" || raw === "alert") return "warning";
    if (raw === "success" || raw === "ok") return "success";
    return "info";
  }

  function renderAlert(title, details, level) {
    setBoardData("announcementEmpty", "0");
    toggleHidden(dom.alertContainer, false);
    if (dom.alertContainer) dom.alertContainer.dataset.alertLevel = normalizeAnnouncementLevel(level);
    setTextIfChanged(dom.alertTitle, title || "تنبيه");
    setTextIfChanged(dom.alertDetails, details || "—");
  }

  // ===== Render: Brand/Theme =====
  var lastAppliedThemeSig = "";

  function forceThemeRepaint() {
    try {
      if (document.body) {
        // Some TV browsers do not repaint CSS variables immediately after
        // changing data-theme. Toggling a harmless attribute and reading layout
        // nudges a repaint without reloading the page.
        document.body.setAttribute("data-theme-refresh", String(Date.now()));
        void document.body.offsetHeight;
      }
    } catch (e) {}

    try { scheduleFit(0); } catch (e) {}
    try {
      setNamedTimer("theme_refit", function () {
        try { scheduleFit(0); } catch (e) {}
      }, 120, "theme_refit");
    } catch (e) {}
  }

  function applyTheme(name) {
    let n = (name || "").toString().trim().toLowerCase();
    if (!n) n = "indigo";
    document.body.setAttribute("data-theme", n);
    try {
      document.documentElement.setAttribute("data-display-theme", n);
    } catch (e) {}
  }

  // ===== Custom colors (optional) =====
  function hexToRgb(hex) {
    const s = (hex || "").toString().trim();
    const m = s.match(/^#([0-9a-fA-F]{6})$/);
    if (!m) return null;
    const v = m[1];
    return {
      r: parseInt(v.slice(0, 2), 16),
      g: parseInt(v.slice(2, 4), 16),
      b: parseInt(v.slice(4, 6), 16),
    };
  }

  function clamp01(x) {
    const n = Number(x);
    if (!isFinite(n)) return 0;
    return Math.max(0, Math.min(1, n));
  }

  function mixRgb(a, b, t) {
    const k = clamp01(t);
    return {
      r: Math.round(a.r + (b.r - a.r) * k),
      g: Math.round(a.g + (b.g - a.g) * k),
      b: Math.round(a.b + (b.b - a.b) * k),
    };
  }

  function rgbToHex(rgb) {
    const to2 = (n) => {
      const x = Math.max(0, Math.min(255, Number(n) || 0));
      return x.toString(16).padStart(2, "0").toUpperCase();
    };
    return "#" + to2(rgb.r) + to2(rgb.g) + to2(rgb.b);
  }

  function rgba(rgb, a) {
    return "rgba(" + [rgb.r, rgb.g, rgb.b, clamp01(a)].join(",") + ")";
  }

  /**
   * The school's chosen accent colour.
   *
   * These four properties MUST be written to <body>, not to <html>. app.css
   * declares them on `body.display-board` — unconditionally for the defaults and
   * again for every named theme — and a declaration on an element always beats
   * an inherited one. Written to <html> they were shadowed by body's own values
   * and the chosen colour reached nothing on the board. An inline style on body
   * outranks those rules, so the colour wins for the whole subtree.
   */
  function accentColorTarget() {
    return document.body || root;
  }

  function applyAccentColor(hex) {
    const target = accentColorTarget();
    const rgb = hexToRgb(hex);
    if (!rgb) {
      // رجوع للألوان الافتراضية من app.css
      target.style.removeProperty("--accent-main");
      target.style.removeProperty("--accent-sub");
      target.style.removeProperty("--mesh1");
      target.style.removeProperty("--mesh2");
      return;
    }

    const sub = rgbToHex(mixRgb(rgb, { r: 255, g: 255, b: 255 }, 0.30));
    target.style.setProperty("--accent-main", rgbToHex(rgb));
    target.style.setProperty("--accent-sub", sub);
    // Mesh: لمسة خفيفة من نفس اللون
    target.style.setProperty("--mesh1", rgba(rgb, 0.18));
    target.style.setProperty("--mesh2", rgba(rgb, 0.12));
  }

  const last = {
    brandSig: "",
    stateSig: "",
    currentSig: "",
    miniSig: "",
    annSig: "",
    exSig: "",
    pcSig: "",
    sbSig: "",
    nextSig: "",
  };

  function hydrateBrand(payload) {
    const settings = (payload && payload.settings) || {};
    const name = safeText(settings.name || payload.school_name || "");
    const logo = resolveImageURL(screenSetting("screenLogoUrl", "") || settings.logo_url || payload.logo_url || "");
    const theme = safeText(screenSetting("screenTheme", "") || settings.theme || "");
    const schoolType = safeText(settings.school_type || "");
    const accent = safeText(screenSetting("screenAccentColor", "") || settings.display_accent_color || "");

    const previewLock = (document.body && document.body.dataset && document.body.dataset.previewLock === '1');

    const sig = name + "||" + logo + "||" + theme + "||" + schoolType + "||" + accent;
    if (sig === last.brandSig) return;
    const previousThemeSig = lastAppliedThemeSig;
    last.brandSig = sig;

    if (!previewLock) {
      if (theme) applyTheme(theme);
      applyAccentColor(accent);
      const nextThemeSig = theme + "||" + accent;
      if (nextThemeSig !== previousThemeSig) {
        lastAppliedThemeSig = nextThemeSig;
        forceThemeRepaint();
      }
    }

    if (name) {
      document.title = name + " — لوحة العرض الذكية";
      setTextIfChanged(dom.schoolName, name);
    }

    if (dom.schoolLogo) {
      const fallback =
        safeText(dom.schoolLogo.getAttribute("data-fallback-src")) ||
        safeText(dom.schoolLogo.getAttribute("data-fallback")) ||
        "";

      const nextSrc = logo || fallback || dom.schoolLogo.src;
      if (nextSrc && dom.schoolLogo.src !== nextSrc) dom.schoolLogo.src = nextSrc;
      toggleHidden(dom.schoolLogo, false);
      if (dom.schoolLogoFallback) toggleHidden(dom.schoolLogoFallback, true);
    }

    if (schoolType) cfg.SCHOOL_TYPE = schoolType;
  }

  // ===== Clock / Date =====
  let cachedDateInfo = null;
  function tickClock(dateInfo) {
    const wall = serverWallNowDate();
    if (dom.clock) {
      setTextIfChanged(dom.clock, fmtLocal12HM(wall));
    }
    if (dateInfo) cachedDateInfo = dateInfo;

    try {
      const arWeek = new Intl.DateTimeFormat("ar-SA", { weekday: "long", timeZone: "UTC" }).format(wall);
      if (cachedDateInfo && (dom.dateG || dom.dateH)) {
        const g = cachedDateInfo.gregorian || {};
        const h = cachedDateInfo.hijri || {};
        if (dom.dateG) {
          setTextIfChanged(
            dom.dateG,
            arWeek +
              " ، " +
              (g.day || wall.getUTCDate()) +
              " " +
              (g.month_name || g.month || "") +
              " " +
              (g.year || wall.getUTCFullYear()) +
              "م"
          );
        }
        if (dom.dateH) {
          setTextIfChanged(
            dom.dateH,
            arWeek +
              " ، " +
              (h.day || "") +
              " " +
              (h.month_name || h.month || "") +
              " " +
              (h.year || "") +
              "هـ"
          );
        }
        return;
      }
      if (dom.dateG)
        setTextIfChanged(dom.dateG, arWeek + " ، " + wall.getUTCDate() + " / " + (wall.getUTCMonth() + 1) + " / " + wall.getUTCFullYear() + "م");
    } catch (e) {
      try {
        if (dom.dateG) setTextIfChanged(dom.dateG, new Intl.DateTimeFormat("ar-SA", { timeZone: "UTC" }).format(wall));
      } catch (e2) {
        if (dom.dateG) setTextIfChanged(dom.dateG, new Date(nowMs()).toLocaleDateString("ar-SA"));
      }
    }
  }

  // ===== Progress ring / bar =====
  const CIRC_TOTAL = 339.292;
  let countdownSeconds = null;
  let progressRange = { start: null, end: null };
  let hasActiveCountdown = false;
  let lastStateCoreSig = "";

  let lastZeroHandledCoreSig = "";

  let lastCountdownZeroAt = 0;

  // ===== Bell Sound (MP3) =====
  let bellAudio = null;
  let bellUnlocked = false;
  let lastBellPlayedAt = 0;
  let lastBellStateKey = "";
  let lastBoundaryBellStateKey = "";
  let lastBoundaryBellAt = 0;
  let lastBellBoundaryCheckMs = 0;
  let lastBellBoundaryDaySig = "";
  const bellBoundarySeen = Object.create(null);

  function ensureBellAudio() {
    if (bellAudio) return bellAudio;
    try {
      var url = cfg.BELL_SOUND_URL || "/static/sounds/bell.mp3";
      bellAudio = new Audio(url);
      bellAudio.preload = "auto";
      bellAudio.volume = 0.7;
    } catch (e) {}
    return bellAudio;
  }

  // Unlock audio on first user interaction (browser autoplay policy)
  function unlockBellAudio() {
    if (bellUnlocked) return;
    var audio = ensureBellAudio();
    if (!audio) return;
    // Play+pause immediately to unlock
    try {
      var p = audio.play();
      if (p && p.then) {
        p.then(function () {
          audio.pause();
          audio.currentTime = 0;
        }).catch(function () {});
      } else {
        audio.pause();
        audio.currentTime = 0;
      }
    } catch (e) {}
    bellUnlocked = true;
    // Hide the bell-enable button and show "sound enabled" feedback
    hideBellEnableBtn();
  }

  function hideBellEnableBtn() {
    try {
      var btn = document.getElementById("bellEnableBtn");
      if (!btn) return;
      // Brief green flash to confirm
      btn.style.animation = "none";
      btn.style.background = "rgba(34,197,94,0.25)";
      btn.style.color = "rgba(34,197,94,0.9)";
      btn.style.opacity = "1";
      setTimeout(function () {
        btn.style.transition = "opacity 0.8s";
        btn.style.opacity = "0";
        setTimeout(function () { btn.style.display = "none"; }, 900);
      }, 1200);
    } catch (e) {}
  }

  // Expose for the HTML onclick handler
  window._unlockBellFromBtn = function () {
    unlockBellAudio();
    // Play the bell so user knows it works
    try { playBellSound(); } catch (e) {}
  };

  ["click", "touchstart", "keydown"].forEach(function (evt) {
    document.addEventListener(evt, unlockBellAudio, listenerOpts({ once: true, passive: true }));
  });

  // Also unlock when entering/exiting fullscreen (common first action on TVs)
  ["fullscreenchange", "webkitfullscreenchange"].forEach(function (evt) {
    document.addEventListener(evt, unlockBellAudio, listenerOpts({ once: true }));
  });

  /**
   * Play the school bell MP3 sound.
   * Debounced: won't play more than once every 3 seconds.
   */
  function playBellSound() {
    var now = Date.now();
    if (now - lastBellPlayedAt < 3000) return;
    lastBellPlayedAt = now;
    var audio = ensureBellAudio();
    if (!audio) return;
    _log("bell_play_attempt", {
      unlocked: !!bellUnlocked,
      readyState: Number(audio.readyState || 0),
    });
    try {
      audio.pause();
      audio.currentTime = 0;
      // Keep the preloaded buffer. Calling load() here discards it and makes the
      // bell wait for another network read exactly when the boundary is reached.
      audio.play().catch(function () {
        // Retry with a fresh element only when the buffered element genuinely fails.
        try {
          bellAudio = null;
          var fresh = ensureBellAudio();
          if (fresh) fresh.play().catch(function () {});
        } catch (e2) {}
      });
    } catch (e) {
      // Last resort: recreate and try once more
      try {
        bellAudio = null;
        var fresh2 = ensureBellAudio();
        if (fresh2) fresh2.play().catch(function () {});
      } catch (e2) {}
    }
  }

  /**
   * Check if the state type changed and play bell.
   * يشتغل الجرس عند:
   *  - بداية الدوام (before -> period)
   *  - بداية حصة بعد استراحة (break -> period)
   *  - بداية استراحة بعد حصة (period -> break)
   */
  function checkStateTransitionBell(newStateType) {
    var key = safeText(newStateType);
    if (!key || !lastBellStateKey) {
      lastBellStateKey = key;
      return;
    }
    if (key === lastBellStateKey) return;
    var prev = lastBellStateKey;
    lastBellStateKey = key;

    // With day_path loaded, bell timing is governed by boundary timestamps.
    if (_dayEngine && Array.isArray(_dayEngine.blocks) && _dayEngine.blocks.length) return;

    const boundaryBellJustPlayed =
      prev &&
      prev === lastBoundaryBellStateKey &&
      Date.now() - lastBoundaryBellAt < 10000 &&
      ((prev === "period" && key === "break") || (prev === "break" && key === "period"));
    if (boundaryBellJustPlayed) return;

    // بداية الدوام
    if (prev === "before" && key === "period") {
      try { playBellSound(); } catch (e) {}
    }
    // بداية حصة بعد استراحة
    else if (prev === "break" && key === "period") {
      try { playBellSound(); } catch (e) {}
    }
    // بداية استراحة بعد حصة
    else if (prev === "period" && key === "break") {
      try { playBellSound(); } catch (e) {}
    }
  }

  function optimisticAdvanceToNextBlock() {
    try {
      if (!lastPayloadForFiltering) return false;
      const snap = lastPayloadForFiltering;
      const prevActiveIdx = rt && rt.activePeriodIndex ? Number(rt.activePeriodIndex) : null;
      const nextP = snap.next_period || null;
      if (!nextP || typeof nextP !== "object") return false;

      const kind = safeText(nextP.kind || nextP.type || "").trim().toLowerCase();
      const fromHM = safeText(nextP.from || "");
      const toHM = safeText(nextP.to || "");
      if (!fromHM || !toHM) return false;

      const baseMs = nowMs();
      const startMs = hmToMs(fromHM, baseMs);
      const endMs = hmToMs(toHM, baseMs);
      if (!startMs || !endMs || endMs <= startMs) return false;

      const n = baseMs;
      const isBefore = n < startMs;
      const isActive = n >= startMs && n < endMs;

      // إذا السيرفر يقول الحالة "before" (انتظار) والوقت المحلي يقول إن الحصة لم تبدأ (isBefore)، لا تقفز للحصة
      // السيرفر هو مصدر الحقيقة — انتظر السنابشوت التالي
      if (isBefore && rt.activeStateType === "before") return false;

      let stType = kind || "period";
      if (isBefore) stType = "before";

      let badge = "حالة اليوم";
      let title = "";

      // Always prefer showing what's next (reduces the feeling of being "stuck" on transitions).
      let nextTitle = "";
      try {
        if ((kind || "").toLowerCase() === "period") nextTitle = formatPeriodTitle(nextP);
        else if ((kind || "").toLowerCase() === "break") nextTitle = safeText(nextP.label || "استراحة");
        else nextTitle = safeText(nextP.label || "");
      } catch (e) {
        nextTitle = safeText(nextP.label || "");
      }

      if (stType === "period") {
        badge = "درس";
        title = nextTitle || formatPeriodTitle(nextP);
      } else if (stType === "break") {
        badge = "استراحة";
        title = nextTitle || safeText(nextP.label || "استراحة");
      } else if (stType === "before") {
        badge = DISPLAY_COPY.beforeBadge;
        title = DISPLAY_COPY.beforeTitle;
      }

      const range = fmtTimeRange(fromHM, toHM);

      // Update headline immediately.
      setTextIfChanged(dom.heroTitle, title);
      setTextIfChanged(dom.heroRange, range);
      setTextIfChanged(dom.badgeKind, badge);

      // Update countdown/progress.
      let rem;
      // استخدام nowMs() للحصول على الوقت المتزامن مع السيرفر
      const nowMsValue = nowMs();
      if (isBefore) rem = Math.max(0, Math.floor((startMs - nowMsValue) / 1000));
      else rem = Math.max(0, Math.floor((endMs - nowMsValue) / 1000));

      countdownSeconds = rem;
      hasActiveCountdown = true;
      progressRange = { start: startMs, end: endMs };

      // Keep runtime in sync so list filtering doesn't lag.
      if (stType === "period") {
        rt.activePeriodIndex = getPeriodIndex(nextP) || rt.activePeriodIndex;
        rt.activeFromHM = fromHM;
      } else if (stType === "break") {
        // If we just finished a period and entered a break, standby for the finished period must disappear.
        // We don't always have the next period index during breaks, so we advance optimistically.
        if (prevActiveIdx && isFinite(prevActiveIdx) && prevActiveIdx > 0) {
          rt.activePeriodIndex = prevActiveIdx + 1;
        }
        rt.activeFromHM = fromHM;
      } else if (stType === "before") {
        // If we're waiting for a known next block, keep runtime aligned with its start.
        rt.activeFromHM = fromHM;
      }
      // تحديث بيانات الوقت المستهدف للعد التنازلي الدقيق
      rt.activeToHM = toHM;
      rt.activeStateType = stType;
      rt.activeTargetHM = (stType === "before" ? fromHM : toHM) || null;
      rt.activeTargetMs = isBefore ? startMs : endMs;
      rt.dayOver = false;
      applyBoardStatePresentation(stType, rem > 0);

      // Immediately re-filter standby list (removes ended standby without waiting for server cache/ETag).
      try {
        renderStandby((snap && snap.standby) || []);
      } catch (e) {}

      try {
        renderCurrentChips(
          stType,
          { label: stType === "before" ? title : safeText(nextP.label || title), from: fromHM, to: toHM },
          stType === "before" ? null : nextP
        );
      } catch (e) {}

      // Keep the rendered core signature in sync, but do not pre-consume the
      // zero-boundary for the newly applied state.
      lastStateCoreSig = stType + "||" + safeText(title || "") + "||" + safeText(fromHM || "") + "||" + safeText(toHM || "");

      return true;
    } catch (e) {
      return false;
    }
  }

  function onCountdownZero() {
    if (isBlocked && !isTerminalBlockedMode()) return;
    if (isTerminalBlockedMode()) {
      _log("snapshot_blocked_due_to_binding_loss", _logContext({
        sourcePath: "onCountdownZero",
      }));
      return;
    }
    const now = nowMs();
    if (now - lastCountdownZeroAt < 2000) return;
    lastCountdownZeroAt = now;

    // Bell is intentionally not triggered here. Ringing is driven by local-time
    // boundary transitions (dayEngineSyncToLocalNow / checkStateTransitionBell),
    // not by the visual countdown reaching 00:00.

    // ── Push-only architecture: use dayEngine for ALL time-based transitions ──
    // dayEngineOnZero() uses the cached day_path to figure out what block
    // we are in now and advance the UI entirely client-side.
    // Only if it can't handle it do we fall back to server fetch.
    try {
      if (dayEngineOnZero()) {
        // Day engine handled the transition locally — no server call needed.
        // Schedule a lightweight refresh after the block duration to catch
        // the next boundary, and keep the normal loop running slowly.
        if (isDebug()) console.log("[dayEngine] handled countdown zero locally");
        return;
      }
    } catch (e) {
      if (isDebug()) console.warn("[dayEngine] onZero error:", e);
    }

    // ── Fallback: dayEngine couldn't handle it (no data / edge case) ──
    // UX guarantee: if we already know what's next (next_period), show it immediately.
    try {
      optimisticAdvanceToNextBlock();
    } catch (e) {}

    // Time-based transitions (period/break) don't bump schedule_revision, so /status may stay 304.
    // Enter a short window where we fetch snapshots directly until the UI advances.
    try {
      rt.transitionUntilTs = nowMs() + 20000; // 20s window for transition
      rt.transitionBackoffSec = 2.0;
    } catch (e) {}

    // Optional (heavier) behavior: full page reload if explicitly requested.
    try {
      const qs = new URLSearchParams(window.location.search);
      if ((qs.get("reload_on_zero") || "").trim() === "1") {
        try {
          const k = "display_reload_on_zero_ts";
          const prev = Number(sessionStorage.getItem(k) || 0);
          if (prev && now - prev < 60000) return;
          sessionStorage.setItem(k, String(now));
        } catch (e) {}
        try {
          window.location.reload();
        } catch (e) {}
        return;
      }
    } catch (e) {}

    // Default fallback: force-refresh data ASAP.
    try {
      const baseJitter = 200 + Math.floor(Math.random() * 1800);
      setTimeout(() => {
        if (isTerminalBlockedMode()) {
          _log("snapshot_blocked_due_to_binding_loss", _logContext({
            sourcePath: "countdown_zero_delayed_force_refresh",
          }));
          return;
        }
        try {
          forceRefreshNow("countdown_zero");
        } catch (e) {
          try { scheduleNext(0.2); } catch (e2) {}
        }
      }, baseJitter);
    } catch (e) {
      try { scheduleNext(0.2); } catch (e2) {}
    }
  }

  let forceRefreshInProgress = false;
  let reloadFallbackTs = 0;
  async function forceRefreshNow(reason) {
    if (isBlocked && !isTerminalBlockedMode()) return;
    if (isTerminalBlockedMode()) {
      _log("snapshot_blocked_due_to_binding_loss", _logContext({
        sourcePath: "forceRefreshNow",
        forceReason: reason || "",
      }));
      return;
    }
    if (forceRefreshInProgress) return;
    forceRefreshInProgress = true;

    try {
      // Force a fresh snapshot build on the server to avoid waiting for the server cache TTL.
      const snap = await safeFetchSnapshot({
        force: true,
        bypassEtag: true,
        bypassServerCache: true,
        transition: true,
        reason: reason || "manual",
      });

      if (!snap || (snap && snap._notModified)) {
        return;
      }

      // If we got rate-limited exactly at the boundary, retry via the normal loop with backoff.
      if (snap && snap._rateLimited) {
        try {
          const base = Number(rt.transitionBackoffSec) || 2.0;
          rt.transitionBackoffSec = Math.min(10, Math.max(2.0, base * 1.7));
        } catch (e) {}
        return;
      }

      try {
        // Same render path as refreshLoop
        failStreak = 0;
        renderState(snap);
        renderAnnouncements(snap.announcements || []);
        renderFeaturedPanel(snap);
        renderStandby(snap.standby || []);
        renderPeriodClasses(pickRuntimePeriodClasses(snap));
      } catch (e) {
        renderAlert("حدث خطأ أثناء العرض", "افتح ?debug=1 لمزيد من التفاصيل.");
        ensureDebugOverlay();
        if (isDebug()) setDebugText("force render error: " + (e && e.message ? e.message : String(e)));
      }

      // If we successfully crossed into a new block with a fresh countdown, exit transition window.
      try {
        const sOk = (snap && snap.state) || {};
        const remOk = typeof sOk.remaining_seconds === "number" ? Math.max(0, Math.floor(sOk.remaining_seconds)) : null;
        if (remOk !== null && remOk > 0) {
          rt.transitionUntilTs = 0;
          rt.transitionBackoffSec = 2.0;
        }
      } catch (e) {}

      // If we forced a refresh at countdown==0 but the state still didn't advance,
      // do one bounded retry, then a bounded hard reload as a last resort.
      try {
        const s = (snap && snap.state) || {};
        const stType = safeText(s.type || "");
        const coreSig =
          stType + "||" + safeText(s.label || "") + "||" + safeText(s.from || "") + "||" + safeText(s.to || "");
        const rem = typeof s.remaining_seconds === "number" ? Math.max(0, Math.floor(s.remaining_seconds)) : null;

        if ((stType === "period" || stType === "break" || stType === "before") && rem === 0) {
          // One quick retry (server might still be computing the transition)
          setTimeout(() => {
            if (isTerminalBlockedMode()) {
              _log("snapshot_blocked_due_to_binding_loss", _logContext({
                sourcePath: "countdown_zero_retry_timer",
              }));
              return;
            }
            try {
              safeFetchSnapshot({ force: true, bypassEtag: true, bypassServerCache: true, transition: true, reason: "countdown_zero_retry" })
                .then((snap2) => {
                  if (!snap2 || (snap2 && snap2._notModified)) return;
                  try {
                    renderState(snap2);
                    renderAnnouncements(snap2.announcements || []);
                    renderFeaturedPanel(snap2);
                    renderStandby(snap2.standby || []);
                    renderPeriodClasses(pickRuntimePeriodClasses(snap2));
                  } catch (e) {}

                  try {
                    const s2 = (snap2 && snap2.state) || {};
                    const st2 = safeText(s2.type || "");
                    const core2 =
                      st2 + "||" + safeText(s2.label || "") + "||" + safeText(s2.from || "") + "||" + safeText(s2.to || "");
                    const rem2 = typeof s2.remaining_seconds === "number" ? Math.max(0, Math.floor(s2.remaining_seconds)) : null;
                    if ((st2 === "period" || st2 === "break" || st2 === "before") && rem2 === 0 && core2 === coreSig) {
                      const now2 = nowMs();
                      // Use sessionStorage to prevent reload loops across page reloads.
                      // Without this, each reload resets the in-memory cooldown, creating an
                      // infinite reload cycle that triggers the server anti-loop guard.
                      var reloadCooldownKey = "display_reload_cooldown_ts";
                      var lastReloadTs = 0;
                      try { lastReloadTs = Number(sessionStorage.getItem(reloadCooldownKey) || 0); } catch (e) {}
                      if (now2 - lastReloadTs > 60000) {
                        try { sessionStorage.setItem(reloadCooldownKey, String(now2)); } catch (e) {}
                        try {
                          window.location.reload();
                        } catch (e) {}
                      }
                    }
                  } catch (e) {}
                })
                .catch(() => {});
            } catch (e) {}
          }, 800);
        }
      } catch (e) {}
    } catch (e) {
      // Fallback to normal loop on unexpected errors.
      try {
        scheduleNext(0.2);
      } catch (e2) {}
    } finally {
      forceRefreshInProgress = false;
      try {
        const inTrans = nowMs() < (Number(rt.transitionUntilTs) || 0);
        if (inTrans) {
          scheduleNext(Number(rt.transitionBackoffSec) || 2.0);
        } else {
          scheduleNext(basePollEverySec());
        }
      } catch (e) {}
    }
  }

  function setRing(pct) {
    if (!dom.circleProgress) return;
    const clamped = clamp(pct, 0, 100);
    const off = CIRC_TOTAL * (1 - clamped / 100);
    dom.circleProgress.style.strokeDashoffset = String(off);
  }

  // ===== Current chips =====
  function clearNode(el) {
    if (!el) return;
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function makeChip(text, extraClass) {
    const s = document.createElement("span");
    s.className = "chip" + (extraClass ? " " + extraClass : "");
    s.textContent = safeText(text || "—");
    s.setAttribute("role", "listitem");
    return s;
  }

  function renderCurrentChips(stType, stateObj, currentObj) {
    if (!dom.currentScheduleList) return;

    const st = stateObj || {};
    const cur = currentObj || {};
    const cls = safeText(cur["class"] || cur.class_name || cur.classroom || "");
    const periodTitle = stType === "period" ? formatPeriodTitle(cur) : "";
    const activity =
      stType === "after" || stType === "off" || stType === "holiday" || stType === "day"
        ? ""
        : getCurrentActivityLabel(stType, cur, st);
    const range =
      stType === "after" || stType === "off" || stType === "holiday" || stType === "day"
        ? "--:--"
        : fmtTimeRange(cur.from || st.from, cur.to || st.to);

    const sig = cls + "||" + periodTitle + "||" + activity + "||" + range + "||" + stType;
    if (sig === last.currentSig) return;
    last.currentSig = sig;

    clearNode(dom.currentScheduleList);

    if (!cls && !periodTitle && !activity && range.indexOf("--:--") >= 0) {
      const msg = document.createElement("div");
      const emptyMessages = {
        before: "نستعد الآن لانطلاق أولى فعاليات اليوم الدراسي",
        after: "شكرًا لكل من أسهم في نجاح هذا اليوم",
        holiday: "نتمنى لكم إجازة هادئة وسعيدة",
        off: "سنعود تلقائيًا مع بداية الدوام القادم",
        day: "تظهر تفاصيل النشاط هنا عند بدء الجدول",
      };
      msg.className = "hero-empty-message";
      msg.textContent = emptyMessages[stType] || "لا توجد حصة جارية الآن";
      msg.setAttribute("role", "status");
      dom.currentScheduleList.appendChild(msg);
      return;
    }

    if (periodTitle) dom.currentScheduleList.appendChild(makeChip(periodTitle));
    if (activity && activity !== periodTitle) dom.currentScheduleList.appendChild(makeChip(activity));
    if (cls) dom.currentScheduleList.appendChild(makeChip(cls));
    dom.currentScheduleList.appendChild(makeChip(range, "num-font"));
  }

  // ===== Mini schedule =====
  let miniItems = [];
  function renderMiniSchedule(payload, baseMs) {
    if (!dom.miniSchedule) return;

    const timeline = [];
    if (Array.isArray(payload.day_path)) {
      payload.day_path.forEach((x) => {
        if (!x) return;
        timeline.push({ start: x.from, end: x.to, label: x.label || "" });
      });
    }

    timeline.sort((a, b) => (hmToMs(a.start, baseMs) || 0) - (hmToMs(b.start, baseMs) || 0));
    const shown = timeline.filter((x) => !isEnded(x.end, baseMs));
    setBoardData("timelineEmpty", shown.length ? "0" : "1");

    const sig = JSON.stringify(shown.map((x) => [x.start, x.end, x.label]));
    if (sig !== last.miniSig) {
      last.miniSig = sig;
      miniItems = [];
      clearNode(dom.miniSchedule);

      if (!shown.length) {
        const msg = document.createElement("div");
        msg.style.opacity = "0.75";
        msg.textContent = "لا يوجد جدول اليوم";
        msg.setAttribute("role", "status");
        dom.miniSchedule.appendChild(msg);
      } else {
        shown.forEach((x) => {
          const box = document.createElement("div");
          box.setAttribute("role", "listitem");
          box.className = "mini-schedule-item";
          box.style.flex = "0 0 auto";
          box.style.borderRadius = "14px";
          box.style.border = "1px solid rgba(255,255,255,0.10)";
          box.style.background = "rgba(255,255,255,0.05)";
          box.style.padding = "10px 12px";
          box.style.minWidth = "74px";
          box.style.display = "flex";
          box.style.flexDirection = "column";
          box.style.alignItems = "center";
          box.style.justifyContent = "center";
          box.style.gap = "6px";
          box.style.transition = "opacity .2s ease";

          const t = document.createElement("span");
          t.className = "num-font mini-schedule-item__time";
          t.style.fontSize = "12px";
          t.style.opacity = "0.75";
          t.textContent = toTimeStr(x.start);

          const l = document.createElement("span");
          l.className = "mini-schedule-item__label";
          l.style.fontWeight = "1000";
          l.style.fontSize = "16px";
          l.style.lineHeight = "1";
          l.textContent = safeText(x.label || "—");

          box.setAttribute("aria-label", "من " + safeText(toTimeStr(x.start)) + " إلى " + safeText(toTimeStr(x.end)) + "، " + safeText(x.label || "—"));

          box.appendChild(t);
          box.appendChild(l);
          dom.miniSchedule.appendChild(box);
          miniItems.push({ start: x.start, end: x.end, label: x.label, el: box });
        });
      }
    }

    miniItems.forEach((it) => {
      if (!it.el) return;
      const active = isNowBetween(it.start, it.end, baseMs);
      it.el.style.opacity = active ? "1" : "0.65";
      try { it.el.classList.toggle("is-active", active); } catch (e) {}
    });
  }

  // ===== Announcements =====
  let annTimer = null;
  let annPtr = 0;
  let annList = [];
  const ANN_INT = 6500;
  // بيانات المناسبات تأتي من سجل واحد في الخادم (core/occasions.py) تُحقنه
  // الصفحة في كتلة JSON. لا تُعرَّف هنا: أي تكرار سينحرف عن ألوان CSS وعن
  // معاينة لوحة المدير عند أول تعديل.
  const OCCASION_THEME_META = (function readOccasionThemeMeta() {
    try {
      const node = document.getElementById("occasionThemeMeta");
      if (!node) return {};
      const parsed = JSON.parse(node.textContent || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (e) {
      // صفحة قديمة مخزَّنة في Service Worker بلا الكتلة: تعمل الشاشة بلا
      // مناسبات بدل أن تتوقف.
      return {};
    }
  })();
  let occasionThemeSignature = "";
  let activeOccasionAnnouncement = null;

  function refreshOccasionPresentation() {
    const selected = activeOccasionAnnouncement;
    const key = safeText(selected && selected.occasion_theme);
    const meta = OCCASION_THEME_META[key];
    const stateType = safeText(document.body && document.body.dataset.displayState).toLowerCase();
    const ambientState =
      stateType === "before" ||
      stateType === "after" ||
      stateType === "holiday" ||
      stateType === "off";
    const showHero = !!(selected && meta && ambientState);

    if (!showHero) {
      if (document.body) document.body.removeAttribute("data-occasion-ambient");
      toggleHidden(dom.occasionHero, true);
      return;
    }

    if (document.body) document.body.setAttribute("data-occasion-ambient", "1");
    if (dom.occasionHero) {
      dom.occasionHero.setAttribute("data-mark-text", meta.markIsText ? "1" : "0");
    }
    setTextIfChanged(dom.occasionHeroIcon, meta.mark || meta.badgeIcon);
    setTextIfChanged(
      dom.occasionHeroLabel,
      safeText(selected.occasion_theme_label || meta.label)
    );
    setTextIfChanged(dom.occasionHeroTitle, safeText(selected.title || meta.label));
    setTextIfChanged(
      dom.occasionHeroBody,
      safeText(selected.body || selected.details || selected.text || "")
    );
    setTextIfChanged(dom.occasionHeroTagline, meta.tagline);
    toggleHidden(dom.occasionHero, false);
  }

  function annSignature(arr) {
    const a = Array.isArray(arr) ? arr : [];
    return JSON.stringify(
      a.map((x) => {
        x = x || {};
        const title = safeText(x.title || x.heading || "");
        const body = safeText(x.body || x.details || x.text || x.message || "");
        const id = safeText(x.id || x.pk || "");
        const occasionTheme = safeText(x.occasion_theme || "");
        return [id, title, body, occasionTheme];
      })
    );
  }

  function applyOccasionTheme(items) {
    const list = Array.isArray(items) ? items : [];
    let selected = null;
    const screenOccasion = screenSetting("screenOccasionTheme", "auto");
    if (screenOccasion !== "off") {
      for (let i = 0; i < list.length; i++) {
        const themeKey = safeText((list[i] || {}).occasion_theme || "");
        if (
          OCCASION_THEME_META[themeKey] &&
          (screenOccasion === "auto" || themeKey === screenOccasion)
        ) {
          selected = list[i] || {};
          break;
        }
      }
      if (!selected && OCCASION_THEME_META[screenOccasion]) {
        selected = {
          id: "screen-fixed-occasion",
          occasion_theme: screenOccasion,
          occasion_theme_label: OCCASION_THEME_META[screenOccasion].label,
          title: OCCASION_THEME_META[screenOccasion].label,
        };
      }
    }
    const key = selected ? safeText(selected.occasion_theme || "") : "";
    const signature = screenOccasion + "||" + key + "||" + safeText(selected && (selected.id || selected.pk || ""));
    activeOccasionAnnouncement = selected;
    if (signature === occasionThemeSignature) {
      refreshOccasionPresentation();
      return;
    }
    occasionThemeSignature = signature;

    if (!key || !OCCASION_THEME_META[key]) {
      activeOccasionAnnouncement = null;
      document.body.removeAttribute("data-occasion-theme");
      document.body.removeAttribute("data-occasion-ambient");
      toggleHidden(dom.occasionThemeDecor, true);
      toggleHidden(dom.occasionThemeBadge, true);
      toggleHidden(dom.occasionHero, true);
      return;
    }

    const meta = OCCASION_THEME_META[key];
    document.body.setAttribute("data-occasion-theme", key);
    setTextIfChanged(dom.occasionThemeBadgeIcon, meta.badgeIcon);
    setTextIfChanged(
      dom.occasionThemeBadgeLabel,
      safeText(selected.occasion_theme_label || meta.label)
    );
    setTextIfChanged(dom.occasionThemeSymbolOne, meta.symbols[0]);
    setTextIfChanged(dom.occasionThemeSymbolTwo, meta.symbols[1]);
    toggleHidden(dom.occasionThemeDecor, false);
    toggleHidden(dom.occasionThemeBadge, false);
    refreshOccasionPresentation();
    forceThemeRepaint();
  }

  function showAnnouncement(i) {
    if (!annList.length) return;
    annPtr = (i + annList.length) % annList.length;
    const a = annList[annPtr] || {};
    renderAlert(
      safeText(a.title || a.heading || "تنبيه"),
      safeText(a.body || a.details || a.text || a.message || "—"),
      safeText(a.level || a.kind || a.type || "info")
    );
  }

  function renderAnnouncements(arr) {
    const screenId = parseInt(document.body.getAttribute("data-screen-id") || "0", 10);
    const currentMs = nowMs();
    const sourceList = (Array.isArray(arr) ? arr.slice() : []).filter((item) => {
      item = item || {};
      const targets = Array.isArray(item.screen_ids) ? item.screen_ids.map(Number) : [];
      if (targets.length && targets.indexOf(screenId) < 0) return false;

      // النافذة الزمنية تُفرض هنا أيضًا، لا على الخادم وحده. النسخة المحفوظة
      // قد تبقى قيد العرض ساعات بلا اتصال، فبدون هذا الفحص يظل تنبيه
      // "اجتماع اليوم الساعة ٩" معروضًا بعد انتهائه بأيام.
      const startsMs = item.starts_at ? Date.parse(String(item.starts_at)) : NaN;
      const expiresMs = item.expires_at ? Date.parse(String(item.expires_at)) : NaN;
      if (!isNaN(startsMs) && startsMs > currentMs) return false;
      if (!isNaN(expiresMs) && expiresMs <= currentMs) return false;
      return true;
    });
    const showAnnouncements = screenFlag("screenShowAnnouncements", true);
    const sig = annSignature(sourceList) + "||" + (showAnnouncements ? "1" : "0");
    if (sig && sig === last.annSig) {
      refreshOccasionPresentation();
      return;
    }
    last.annSig = sig;

    annList = showAnnouncements ? sourceList : [];
    // شاشة أُخفيت عنها التنبيهات يجب ألا ترث قالب مناسبةٍ مصدرُه تنبيهٌ لا يظهر
    // عليها أصلًا؛ وإلا تغيّر مظهرها بسبب محتوى غير مرئي فيها.
    applyOccasionTheme(annList);
    if (annTimer) {
      clearInterval(annTimer);
      annTimer = null;
    }

    if (!annList.length) {
      annPtr = 0;
      setBoardData("announcementEmpty", "1");
      toggleHidden(dom.alertContainer, true);
      return;
    }

    setBoardData("announcementEmpty", "0");
    annPtr = clamp(annPtr, 0, Math.max(0, annList.length - 1));
    showAnnouncement(annPtr);
    if (annList.length > 1) annTimer = setInterval(() => showAnnouncement(annPtr + 1), ANN_INT);
  }

  // ===== Independent Auto-Scroller (لكل كرت لوحده) =====
  function findViewportForTrack(trackEl) {
    if (!trackEl) return null;
    let vp = trackEl.parentElement;
    while (
      vp &&
      !(vp.classList && (vp.classList.contains("standby-viewport") || vp.classList.contains("list-viewport")))
    ) {
      vp = vp.parentElement;
    }
    return vp || trackEl.parentElement || null;
  }

  function createScroller(trackEl, getSpeed, opts) {
    const st = {
      raf: null,
      y: 0,
      lastTs: 0,
      contentH: 0,
      viewH: 0,
      cloneCount: 0,
      lastSig: "",
      running: false,
    };

    const maxFps = opts && opts.maxFps ? Number(opts.maxFps) : 0;
    const minFrameMs = maxFps > 0 ? 1000 / Math.max(1, maxFps) : 0;

    function markScrollState(active) {
      const value = active ? "1" : "0";
      try { trackEl.dataset.scrolling = value; } catch (e) {}
      try {
        const vp = findViewportForTrack(trackEl);
        if (vp) vp.dataset.scrolling = value;
      } catch (e) {}
    }

    function stop() {
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = null;
      st.running = false;
      st.lastTs = 0;
      markScrollState(false);
    }

    function trimClones(maxClones) {
      const maxChildren = 1 + Math.max(0, maxClones | 0);
      while (trackEl.children.length > maxChildren) trackEl.removeChild(trackEl.lastElementChild);
    }

    function removeClones() {
      while (trackEl.children.length > 1) trackEl.removeChild(trackEl.lastElementChild);
      st.cloneCount = 0;
    }

    function needScroll(forceScroll) {
      // ✅ افتراضيًا: التمرير فقط إذا امتلأ الكرت فعلاً
      if (forceScroll) return st.contentH > 0 && st.viewH > 0;
      return st.contentH > st.viewH + 4;
    }

    function recalc() {
      const vp = findViewportForTrack(trackEl);
      const content = trackEl.firstElementChild;
      if (!vp || !content) return;

      st.viewH = vp.offsetHeight || 0;
      st.contentH = content.offsetHeight || 0;

      const forceScroll =
        !!(content && content.dataset && content.dataset.forceScroll === "1");

      // لو ما عندنا قياسات صحيحة
      if (!st.viewH || !st.contentH) {
        stop();
        removeClones();
        st.y = 0;
        trackEl.style.transform = "translateY(0)";
        return;
      }

      if (!needScroll(forceScroll)) {
        stop();
        removeClones();
        st.y = 0;
        trackEl.style.transform = "translateY(0)";
        return;
      }

      markScrollState(true);

      // ✅ نضيف نسخ كافية حتى لا يظهر فراغ أثناء التمرير (خصوصًا عند forceScroll)
      // نحتاج إجمالي ارتفاع >= viewH + contentH لكي يظل هناك محتوى يغطي الشاشة أثناء الحركة
      const needTotal = st.viewH + st.contentH + 8;
      let guard = 0;
      while ((trackEl.offsetHeight || 0) < needTotal && guard < 8) {
        const clone = content.cloneNode(true);
        clone.setAttribute("aria-hidden", "true");
        trackEl.appendChild(clone);
        st.cloneCount += 1;
        guard += 1;
      }
      trimClones(Math.max(1, st.cloneCount));

      if (st.contentH > 0) st.y = st.y % st.contentH;
      if (!st.running) start();
    }

    function loop(ts) {
      // ✅ إيقاف حلقة RAF تماماً إذا كان التاب مخفي بدلاً من الاستمرار
      if (document.hidden) {
        st.running = false;
        st.raf = null;
        st.lastTs = 0;
        markScrollState(false);
        return;
      }

      // ✅ إيقاف RAF إذا لم يعد هناك حاجة للتمرير
      if (!st.running) {
        st.raf = null;
        return;
      }

      if (!st.lastTs) st.lastTs = ts;
      let dt = ts - st.lastTs;
      // ✅ في وضع Lite: تحديد 30fps بدلاً من 60fps لتوفير موارد CPU
      var effectiveMinFrame = minFrameMs;
      if (effectiveMinFrame <= 0 && document.body && document.body.dataset.lite === "1") {
        effectiveMinFrame = 33; // ~30fps
      }
      if (effectiveMinFrame > 0 && dt < effectiveMinFrame) {
        st.raf = requestAnimationFrame(loop);
        return;
      }
      dt = Math.min(120, dt);
      st.lastTs = ts;

      const v = Number(getSpeed()) || 0.5;
      const pxPerSec = clamp(v, 0.15, 4) * 60;
      const step = (pxPerSec * dt) / 1000;

      st.y += step;
      if (st.contentH > 0 && st.y >= st.contentH) st.y = st.y % st.contentH;

      trackEl.style.transform = "translateY(-" + st.y.toFixed(2) + "px)";
      st.raf = requestAnimationFrame(loop);
    }

    function start() {
      if (st.running) return;
      st.running = true;
      st.lastTs = 0;
      markScrollState(true);
      if (st.raf) cancelAnimationFrame(st.raf);
      st.raf = requestAnimationFrame(loop);
    }

    function render(signature, contentBuilderFn) {
      if (signature && signature === st.lastSig) {
        recalc();
        return;
      }

      st.lastSig = signature || "";

      // لا نصفر y — نحافظ على موضع التمرير قدر الإمكان
      stop();
      st.cloneCount = 0;
      trackEl.style.transform = "translateY(-" + st.y.toFixed(2) + "px)";

      while (trackEl.firstChild) trackEl.removeChild(trackEl.firstChild);
      const content = contentBuilderFn();
      trackEl.appendChild(content);

      requestAnimationFrame(() => {
        recalc();
      });
    }

    return {
      render,
      recalc,
      stop,
      getState: () => ({
        y: st.y,
        running: st.running,
        contentH: st.contentH,
        viewH: st.viewH,
        overflowing: st.contentH > st.viewH + 4,
      }),
    };
  }

  // ===== Slot builder =====
  function buildSlotItem({ clsName, subj, teacher, badgeText, badgeKind, showChip }) {
    const item = document.createElement("div");
    item.className = "slot-item";
    item.setAttribute("role", "listitem");

    const top = document.createElement("div");
    top.className = "slot-top";

    const badges = document.createElement("div");
    badges.className = "slot-badges";

    const cls = document.createElement("span");
    cls.className = "slot-class";
    cls.textContent = safeText(clsName || "—");

    const subject = document.createElement("span");
    subject.className = "slot-subject";
    subject.textContent = safeText(subj || "—");

    badges.appendChild(cls);
    badges.appendChild(subject);

    top.appendChild(badges);

    const chipEnabled = showChip !== false;
    if (chipEnabled) {
      const chip = document.createElement("span");
      chip.className = "chip num-font " + (badgeKind === "warn" ? "chip-warn" : "chip-ok");
      chip.textContent = safeText(badgeText || "حصة");
      top.appendChild(chip);
    }

    const teacherRow = document.createElement("div");
    teacherRow.className = "slot-teacher";

    const lbl = document.createElement("span");
    lbl.className = "label";
    lbl.textContent = teacherLabelText() + ":";

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = safeText(teacher || "—");

    teacherRow.appendChild(lbl);
    teacherRow.appendChild(name);

    item.appendChild(top);
    item.appendChild(teacherRow);

    const summary = [
      badgeText ? "الفترة " + safeText(badgeText) : "",
      clsName ? "الفصل " + safeText(clsName) : "",
      subj ? "المادة " + safeText(subj) : "",
      teacher ? teacherLabelText() + " " + safeText(teacher) : "",
    ].filter(Boolean).join("، ");
    if (summary) item.setAttribute("aria-label", summary);

    return item;
  }

  function listSignature(items, kind) {
    const arr = Array.isArray(items) ? items : [];
    return JSON.stringify(
      arr.map((x) => {
        x = x || {};
        const cls = safeText(x.class_name || x["class"] || x.classroom || "");
        const subj = safeText(x.subject_name || x.subject || x.label || "");
        const teacher = safeText(x.teacher_name || x.teacher || x.teacher_full_name || "");
        const pidx = getPeriodIndex(x) || "";
        const extra = kind === "standby" ? safeText(x.reason || x.note || "") : "";
        return [cls, subj, teacher, pidx, extra];
      })
    );
  }

  // ===== Scrollers instances =====
  let periodsScroller = null;
  let standbyScroller = null;
  let dutyScroller = null;

  let lastPayloadForFiltering = null;
  const periodClassesByIndex = Object.create(null);

  function _normalizePeriodClassRow(item, fallbackIdx) {
    const x = item || {};
    const cls = safeText(x.class_name || x["class"] || x.classroom || "");
    const subj = safeText(x.subject_name || x.subject || x.label || "");
    const teacher = safeText(x.teacher_name || x.teacher || x.teacher_full_name || "");
    const idxRaw = getPeriodIndex(x) || fallbackIdx;
    const idx = Number(idxRaw);
    return {
      class_name: cls,
      subject_name: subj,
      teacher_name: teacher,
      class: cls,
      subject: subj,
      teacher: teacher,
      period_index: isFinite(idx) && idx > 0 ? idx : null,
    };
  }

  function _cachePeriodClassesForIndex(index, items, overwrite) {
    const idx = Number(index);
    if (!isFinite(idx) || idx <= 0) return;
    const key = String(Math.floor(idx));
    if (!overwrite && Array.isArray(periodClassesByIndex[key]) && periodClassesByIndex[key].length) return;
    const rows = Array.isArray(items) ? items : [];
    const seen = new Set();
    const norm = [];
    rows.forEach((it) => {
      const r = _normalizePeriodClassRow(it, idx);
      if (!r.class_name && !r.subject_name && !r.teacher_name) return;
      const sig = safeText(r.class_name) + "||" + safeText(r.subject_name) + "||" + safeText(r.teacher_name);
      if (seen.has(sig)) return;
      seen.add(sig);
      norm.push(r);
    });
    periodClassesByIndex[key] = norm;
  }

  function _getCachedPeriodClassesForIndex(index) {
    const idx = Number(index);
    if (!isFinite(idx) || idx <= 0) return [];
    const key = String(Math.floor(idx));
    const arr = periodClassesByIndex[key];
    return Array.isArray(arr) ? arr.slice() : [];
  }

  function ingestPeriodClassesFromPayload(payload) {
    if (!payload || typeof payload !== "object") return;

    const map = payload.period_classes_map;
    if (map && typeof map === "object" && !Array.isArray(map)) {
      Object.keys(map).forEach((k) => {
        _cachePeriodClassesForIndex(k, map[k], true);
      });
    }

    const curIdx =
      getPeriodIndex((payload && payload.current_period) || null) ||
      getPeriodIndex((payload && payload.state) || null) ||
      Number(rt && rt.activePeriodIndex);
    if (curIdx && Array.isArray(payload.period_classes) && payload.period_classes.length) {
      _cachePeriodClassesForIndex(curIdx, payload.period_classes, true);
    }

    // Minimal fallback from day_path in case map is missing (single-row per period).
    if (Array.isArray(payload.day_path)) {
      payload.day_path.forEach((b) => {
        if (!b) return;
        const kind = safeText(b.kind || b.type || "").toLowerCase();
        if (kind !== "period") return;
        const idx = getPeriodIndex(b);
        if (!idx) return;
        const row = {
          class_name: safeText(b["class"] || b.class_name || ""),
          subject_name: safeText(b.label || ""),
          teacher_name: safeText(b.teacher || ""),
          period_index: idx,
        };
        _cachePeriodClassesForIndex(idx, [row], false);
      });
    }
  }

  function tickBoundaryBell() {
    if (!_dayEngine.blocks.length || !_dayEngine.ringPoints.length) {
      lastBellBoundaryCheckMs = nowMs();
      return;
    }

    const now = nowMs();
    let prev = Number(lastBellBoundaryCheckMs || 0);
    if (!isFinite(prev) || prev <= 0) prev = now;
    if (prev > now) prev = now;

    // Ring once per tick at most (latest crossed boundary), to avoid bursts
    // when the tab/device wakes up after a long pause.
    let crossedTs = null;
    for (let i = 0; i < _dayEngine.ringPoints.length; i++) {
      const ts = Number(_dayEngine.ringPoints[i] || 0);
      if (!isFinite(ts) || ts <= 0) continue;
      if (ts >= prev && ts <= now) crossedTs = ts;
    }

    if (crossedTs !== null) {
      const daySig = _dayEngine.lastDayKey || "";
      if (lastBellBoundaryDaySig && daySig && lastBellBoundaryDaySig !== daySig) {
        for (const rk in bellBoundarySeen) {
          if (Object.prototype.hasOwnProperty.call(bellBoundarySeen, rk)) delete bellBoundarySeen[rk];
        }
      }
      lastBellBoundaryDaySig = daySig;

      const key = daySig + "|" + String(Math.floor(crossedTs));
      if (!bellBoundarySeen[key]) {
        bellBoundarySeen[key] = 1;
        lastBoundaryBellStateKey = "boundary";
        lastBoundaryBellAt = Date.now();
        _log("bell_boundary_crossed", {
          boundaryMs: Math.floor(crossedTs),
          latenessMs: Math.max(0, Math.round(now - crossedTs)),
        });
        try { playBellSound(); } catch (e) {}
      }
    }

    lastBellBoundaryCheckMs = now;
  }

  /**
   * Arm a one-shot timer for the next exact schedule boundary.
   * The regular ticker remains a safety net for suspended/older TV runtimes,
   * but this timer avoids the previous 0–2 second polling delay.
   */
  function scheduleNextBoundaryBell() {
    clearNamedTimer("bell_boundary");
    if (!_dayEngine.blocks.length || !_dayEngine.ringPoints.length) return;

    const now = nowMs();
    let nextTs = null;
    for (let i = 0; i < _dayEngine.ringPoints.length; i++) {
      const ts = Number(_dayEngine.ringPoints[i] || 0);
      if (!isFinite(ts) || ts <= now) continue;
      nextTs = ts;
      break;
    }
    if (nextTs === null) return;

    const delayMs = Math.max(20, nextTs - now);
    setNamedTimer("bell_boundary", function () {
      try { tickBoundaryBell(); } catch (e) {}
      scheduleNextBoundaryBell();
    }, delayMs, "bell_boundary_exact");
  }

  function pickRuntimePeriodClasses(payload) {
    const src = payload || lastPayloadForFiltering || null;
    ingestPeriodClassesFromPayload(src);

    const stType = safeText((rt && rt.activeStateType) || ((src && src.state && src.state.type) || "")).toLowerCase();
    if (stType !== "period") return [];

    const idx =
      Number((rt && rt.activePeriodIndex) || 0) ||
      Number(getPeriodIndex(src && src.current_period)) ||
      Number(getPeriodIndex(src && src.state));
    if (!isFinite(idx) || idx <= 0) return [];

    let arr = _getCachedPeriodClassesForIndex(idx);
    // Guard against stale carry-over: keep only rows that match the active period index
    // (or rows without explicit index from legacy payloads).
    if (Array.isArray(arr) && arr.length) {
      arr = arr.filter((row) => {
        const rowIdx = Number(getPeriodIndex(row) || 0);
        return !rowIdx || rowIdx === idx;
      });
      if (!arr.length) _cachePeriodClassesForIndex(idx, [], true);
    }

    if ((!arr || !arr.length) && src && Array.isArray(src.period_classes) && src.period_classes.length) {
      const srcCur = (src && src.current_period) || null;
      const srcState = (src && src.state) || {};
      const srcIdx = Number(getPeriodIndex(srcCur) || getPeriodIndex(srcState) || 0);
      const srcFrom = safeText((srcCur && (srcCur.from || srcCur.starts_at)) || srcState.from || "");
      const srcTo = safeText((srcCur && (srcCur.to || srcCur.ends_at)) || srcState.to || "");
      const rtFrom = safeText((rt && rt.activeFromHM) || "");
      const rtTo = safeText((rt && rt.activeToHM) || "");

      // Only trust snapshot period_classes when it clearly belongs to this same period slot.
      const sameIdx = srcIdx > 0 ? srcIdx === idx : false;
      const sameSlot = !!(srcFrom && srcTo && srcFrom === rtFrom && srcTo === rtTo);
      const allowFallback = sameIdx || sameSlot;

      if (allowFallback) {
        const fallbackRows = src.period_classes.filter((row) => {
          const rowIdx = Number(getPeriodIndex(row) || 0);
          return !rowIdx || rowIdx === idx;
        });
        if (fallbackRows.length) {
          arr = fallbackRows;
          _cachePeriodClassesForIndex(idx, arr, true);
        }
      }
    }
    return Array.isArray(arr) ? arr : [];
  }

  function renderPeriodClasses(items) {
    const raw = screenFlag("screenShowPeriodClasses", true) && Array.isArray(items)
      ? items.slice()
      : [];
    const baseMs = nowMs();

    let arr = raw;

    // ✅ بعد نهاية الدوام: فاضي
    if (rt.dayOver) arr = [];

    // ✅ فلترة القديم (لو أرسل السيرفر عناصر أقل من الحصة الحالية)
    if (!rt.dayOver && rt.activePeriodIndex) {
      arr = arr.filter((x) => {
        const idx = getPeriodIndex(x);
        return !idx || idx >= rt.activePeriodIndex;
      });
    }

    if (dom.pcCount) setTextIfChanged(dom.pcCount, String(arr.length));
    setBoardData("periodCount", String(arr.length));
    setBoardData("periodEmpty", arr.length ? "0" : "1");
    refreshAdaptiveLayout();
    if (!dom.periodClassesTrack || !periodsScroller) return;

    // Include runtime state in signature so empty-state message updates correctly
    // when switching between "day over" and "no ongoing classes".
    const sig = (rt.dayOver ? "1" : "0") + "|" + safeText((rt && rt.activeStateType) || "") + "|" + listSignature(arr, "periods");
    periodsScroller.render(sig, () => {
      if (!arr.length) {
        const emptyText = !rt.isSchoolDay
          ? "لا توجد حصص في يوم الإجازة"
          : (rt.dayOver ? "اكتمل جدول اليوم الدراسي" : "لا توجد حصة جارية الآن");
        return makeBoardEmptyState(emptyText);
      }

      const list = document.createElement("div");
      list.style.display = "flex";
      list.style.flexDirection = "column";
      list.style.gap = "10px";
      list.style.paddingBottom = "10px";
      list.setAttribute("role", "list");
      list.setAttribute("aria-label", "الحصص الجارية");

      arr.forEach((x) => {
        x = x || {};
        list.appendChild(
          buildSlotItem({
            clsName: x.class_name || x["class"] || x.classroom || "—",
            subj: x.subject_name || x.subject || x.label || "—",
            teacher: x.teacher_name || x.teacher || "",
            badgeText: formatPeriodTitle(x),
            badgeKind: "ok",
            showChip: false,
          })
        );
      });

      return list;
    });
  }

  function renderStandby(items) {
    const raw = screenFlag("screenShowStandby", true) && Array.isArray(items)
      ? items.slice()
      : [];
    const baseMs = nowMs();

    let arr = raw;

    // ✅ إذا انتهى الدوام: فاضي
    if (rt.dayOver) arr = [];

    // ✅ اخفاء أي انتظار قبل الحصة الحالية/التالية
    if (!rt.dayOver && (rt.activePeriodIndex || rt.activeFromHM)) {
      arr = arr.filter((x) => shouldKeepStandbyItem(x, baseMs));
    }

    if (dom.sbCount) setTextIfChanged(dom.sbCount, String(arr.length));
    setBoardData("standbyCount", String(arr.length));
    setBoardData("standbyEmpty", arr.length ? "0" : "1");
    refreshAdaptiveLayout();
    if (!dom.standbyTrack || !standbyScroller) return;

    const sig = listSignature(arr, "standby");
    standbyScroller.render(sig, () => {
      if (!arr.length) {
        const emptyText = !rt.isSchoolDay
          ? "لا توجد حصص انتظار في يوم الإجازة"
          : (rt.dayOver ? "اكتملت تكليفات الانتظار لهذا اليوم" : "لا توجد حصص انتظار حالياً");
        return makeBoardEmptyState(emptyText);
      }

      const list = document.createElement("div");
      list.style.display = "flex";
      list.style.flexDirection = "column";
      list.style.gap = "10px";
      list.style.paddingBottom = "10px";
      list.setAttribute("role", "list");
      list.setAttribute("aria-label", "حصص الانتظار");

      arr.forEach((x) => {
        x = x || {};
        list.appendChild(
          buildSlotItem({
            clsName: x.class_name || x["class"] || x.classroom || "—",
            subj: x.subject_name || x.subject || x.label || "—",
            teacher: x.teacher_name || x.teacher || x.teacher_full_name || "—",
            badgeText: formatPeriodTitle(x),
            badgeKind: "warn",
            showChip: true,
          })
        );
      });

      return list;
    });
  }

  // ===== Excellence =====
  let exTimer = null;
  let exPtr = 0;
  let exList = [];
  const EX_INT = 7000;

  function exSignature(arr) {
    const a = Array.isArray(arr) ? arr : [];
    return JSON.stringify(
      a.map((e) => {
        e = e || {};
        const student = e.student || {};
        const teacher = e.teacher || {};
        const name = safeText(
          e.name ||
            e.student_name ||
            e.teacher_name ||
            student.name ||
            teacher.name ||
            e.full_name ||
            e.display_name ||
            ""
        );
        const reason = safeText(e.reason || e.note || e.message || e.title || "");
        const img = safeText(
          e.image_src ||
            e.photo_url ||
            e.image_url ||
            e.photo ||
            e.image ||
            e.avatar ||
            student.photo_url ||
            student.image_url ||
            student.photo ||
            student.image ||
            teacher.photo_url ||
            teacher.image_url ||
            teacher.photo ||
            teacher.image ||
            ""
        );
        return [name, reason, img];
      })
    );
  }

  // ✅ Cache لصور لوحة الشرف - تحميل مسبق للصورة التالية
  const _exImageCache = new Map();
  const _EX_CACHE_MAX = 10;

  function _preloadExImage(idx) {
    if (!exList.length) return;
    const nextIdx = (idx + exList.length) % exList.length;
    const e = exList[nextIdx] || {};
    const student = e.student || {};
    const teacher = e.teacher || {};
    const rawSrc =
      e.image_src || e.photo_url || e.image_url || e.photo || e.image || e.avatar ||
      student.photo_url || student.image_url || student.photo || student.image ||
      teacher.photo_url || teacher.image_url || teacher.photo || teacher.image;
    const src = resolveImageURL(rawSrc);
    if (!src || _exImageCache.has(src)) return;
    // تنظيف الكاش إذا وصل للحد
    if (_exImageCache.size >= _EX_CACHE_MAX) {
      const first = _exImageCache.keys().next().value;
      _exImageCache.delete(first);
    }
    var preImg = new Image();
    preImg.crossOrigin = "anonymous";
    preImg.src = src;
    _exImageCache.set(src, preImg);
  }

  function showExcellence(i) {
    if (!exList.length || !dom.exSlot) return;

    exPtr = (i + exList.length) % exList.length;
    if (dom.exIndex) setTextIfChanged(dom.exIndex, String(exPtr + 1));

    const e = exList[exPtr] || {};
    const student = e.student || {};
    const teacher = e.teacher || {};

    const name =
      e.name ||
      e.student_name ||
      e.teacher_name ||
      student.name ||
      teacher.name ||
      e.full_name ||
      e.display_name ||
      "—";

    let reason = safeText(e.reason || e.note || e.message || e.title || "");
    if (reason.length > 180) reason = reason.slice(0, 177) + "…";

    const rawSrc =
      e.image_src ||
      e.photo_url ||
      e.image_url ||
      e.photo ||
      e.image ||
      e.avatar ||
      student.photo_url ||
      student.image_url ||
      student.photo ||
      student.image ||
      teacher.photo_url ||
      teacher.image_url ||
      teacher.photo ||
      teacher.image;

    const src = resolveImageURL(rawSrc);

    dom.exSlot.style.opacity = "0";
    setTimeout(() => {
      clearNode(dom.exSlot);

      const wrap = document.createElement("div");
      wrap.className = "honor-wrap";
      wrap.setAttribute("role", "group");
      wrap.setAttribute("aria-label", "متميز: " + safeText(name) + (reason ? "، سبب التكريم: " + safeText(reason) : ""));

      const img = document.createElement("img");
      img.alt = "صورة المتميز " + safeText(name);
      img.loading = "eager";
      img.crossOrigin = "anonymous";
      img.className = "honor-avatar";
      
      const fallbackSvg =
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E%3Cdefs%3E%3ClinearGradient id='bg' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%230f172a'/%3E%3Cstop offset='1' stop-color='%231e293b'/%3E%3C/linearGradient%3E%3C/defs%3E%3Crect width='200' height='200' rx='24' fill='url(%23bg)'/%3E%3Ccircle cx='100' cy='78' r='34' fill='%2394a3b8' opacity='.82'/%3E%3Crect x='46' y='116' width='108' height='58' rx='28' fill='%2364748b' opacity='.86'/%3E%3Ccircle cx='152' cy='150' r='20' fill='%23fbbf24'/%3E%3Cpath d='M152 139l3.8 7.7 8.5 1.2-6.1 5.9 1.4 8.5-7.6-4-7.6 4 1.4-8.5-6.1-5.9 8.5-1.2 3.8-7.7z' fill='%23fff'/%3E%3C/svg%3E";
      
      img.src = src || fallbackSvg;

      // Fallback: if Cloudinary/remote image 404s, swap to SVG placeholder
      img.addEventListener("error", function() {
        if (img.src !== fallbackSvg) img.src = fallbackSvg;
      }, { once: true, passive: true });

      const imgContainer = document.createElement("div");
      imgContainer.className = "honor-avatar-wrap";
      imgContainer.appendChild(img);


      const meta = document.createElement("div");
      meta.className = "honor-meta";

      const nm = document.createElement("div");
      nm.className = "honor-name";
      nm.textContent = name;

      const rs = document.createElement("div");
      rs.className = "honor-reason";
      rs.textContent = reason || "—";

      meta.appendChild(nm);
      meta.appendChild(rs);

      wrap.appendChild(imgContainer);
      wrap.appendChild(meta);

      dom.exSlot.appendChild(wrap);
      dom.exSlot.style.opacity = "1";

      // ✅ تحميل مسبق للصورة التالية أثناء عرض الحالية
      try { _preloadExImage(exPtr + 1); } catch (e) {}
    }, 220);
  }

  function renderExcellence(items) {
    const sig = exSignature(items);
    if (sig && sig === last.exSig) return;
    last.exSig = sig;

    const nextList = Array.isArray(items) ? items.slice() : [];
    exList = nextList.filter(
      (x) =>
        x &&
        (x.name ||
          x.student_name ||
          x.teacher_name ||
          x.full_name ||
          x.display_name ||
          (x.student && x.student.name) ||
          (x.teacher && x.teacher.name))
    );

    if (dom.exTotal) setTextIfChanged(dom.exTotal, String(exList.length || 0));

    if (exTimer) {
      clearInterval(exTimer);
      exTimer = null;
    }
    if (!dom.exSlot) return;

    if (!exList.length) {
      if (dom.exIndex) setTextIfChanged(dom.exIndex, "0");
      dom.exSlot.innerHTML = "";
      const msg = document.createElement("div");
      msg.className = "honor-empty-state";
      msg.textContent = "لا يوجد متميزون حالياً";
      msg.setAttribute("role", "status");
      dom.exSlot.appendChild(msg);
      return;
    }

    exPtr = clamp(exPtr, 0, Math.max(0, exList.length - 1));
    showExcellence(exPtr);
    if (exList.length > 1) exTimer = setInterval(() => showExcellence(exPtr + 1), EX_INT);
  }

  // ===== Duty / Supervision =====
  function dutySignature(list) {
    const arr = Array.isArray(list) ? list : [];
    return JSON.stringify(
      arr.map((x) => {
        x = x || {};
        return [
          safeText(x.teacher_name || ""),
          safeText(x.duty_type || ""),
          safeText(x.duty_label || ""),
          safeText(x.location || ""),
          safeText(x.priority || ""),
        ];
      })
    );
  }

  function buildDutyRow(it) {
    it = it || {};
    const teacher = safeText(it.teacher_name || "");
    const dutyType = safeText(it.duty_type || "");
    const dutyLabel = safeText(it.duty_label || (dutyType === "supervision" ? "إشراف" : "مناوبة"));
    const location = safeText(it.location || "");
    const isSup = dutyType === "supervision";

    // Luxury container - Deep Glass Theme
    const row = document.createElement("div");
    row.className =
      "duty-item relative flex items-center justify-between gap-4 px-6 py-4 rounded-xl " +
      "border border-white/10 backdrop-blur-md transition-all duration-300 " +
      "hover:border-white/20 hover:shadow-[0_8px_30px_rgba(0,0,0,0.3)] " +
      "bg-gradient-to-r from-[#0f172a]/60 to-[#1e293b]/60 group overflow-hidden shadow-lg";
    row.setAttribute("role", "listitem");
    row.setAttribute("aria-label", [
      teacher ? teacherLabelText() + " " + teacher : "",
      dutyLabel ? "النوع " + dutyLabel : "",
      location ? "الموقع " + location : "",
    ].filter(Boolean).join("، "));

    // 1. Accent line on the right (RTL start)
    const accent = document.createElement("div");
    accent.className = "absolute right-0 top-0 bottom-0 w-[5px] " + 
        (isSup 
          ? "bg-gradient-to-b from-emerald-400 to-emerald-600 shadow-[0_0_12px_rgba(16,185,129,0.6)]" 
          : "bg-gradient-to-b from-amber-400 to-amber-600 shadow-[0_0_12px_rgba(245,158,11,0.6)]");
    row.appendChild(accent);

    // 2. Info Section (Avatar + Name)
    const infoSection = document.createElement("div");
    infoSection.className = "flex items-center gap-5 min-w-0 z-10 pr-3";

    // Avatar
    const avatar = document.createElement("div");
    avatar.className =
      "relative w-14 h-14 rounded-2xl flex items-center justify-center text-xl font-bold text-white shadow-[inset_0_2px_4px_rgba(255,255,255,0.1)] shrink-0 " +
      "border border-white/10 " + 
      (isSup 
        ? "bg-gradient-to-br from-emerald-500/20 to-emerald-900/30 ring-1 ring-emerald-500/30" 
        : "bg-gradient-to-br from-amber-500/20 to-amber-900/30 ring-1 ring-amber-500/30");
    
    const avatarInner = document.createElement("span");
    avatarInner.textContent = teacher ? teacher.slice(0, 1) : "—";
    avatarInner.className = "drop-shadow-md opacity-90";
    avatarInner.setAttribute("aria-hidden", "true");
    avatar.appendChild(avatarInner);

    // Meta (Name + Loc)
    const meta = document.createElement("div");
    meta.className = "min-w-0 flex flex-col justify-center gap-1.5";

    const nm = document.createElement("div");
    nm.className = "duty-name text-white font-bold leading-none truncate flex items-center gap-3"; 
    
    const nameSpan = document.createElement("span");
    nameSpan.textContent = teacher || "—";
    nm.appendChild(nameSpan);

    if (location) {
      const locSpan = document.createElement("span");
      // Modern Glass Pill
      locSpan.className = 
        "flex items-center gap-1.5 px-3 py-1 rounded-md text-[0.55em] font-bold tracking-wider " +
        "bg-white/5 border border-white/10 text-indigo-100/90 shadow-sm whitespace-nowrap";
      
      const pin = document.createElement("span");
      pin.textContent = "📍";
      pin.className = "text-[1em] opacity-80";
      pin.setAttribute("aria-hidden", "true");
      
      const txt = document.createElement("span");
      txt.textContent = location;

      locSpan.appendChild(pin);
      locSpan.appendChild(txt);
      nm.appendChild(locSpan);
    }

    meta.appendChild(nm);
    infoSection.appendChild(avatar);
    infoSection.appendChild(meta);

    // 3. Badge (Left Side)
    const badge = document.createElement("div");
    badge.className = "relative z-10 shrink-0";
    
    // Glow effect behind badge
    const badgeGlow = document.createElement("div");
    badgeGlow.className = "absolute inset-0 blur-xl opacity-40 " + (isSup ? "bg-emerald-500" : "bg-amber-500");
    badge.appendChild(badgeGlow);

    const badgeInner = document.createElement("div");
    badgeInner.className =
      "relative duty-role px-4 py-2 rounded-lg text-sm font-black tracking-wide border shadow-2xl flex items-center gap-2 " +
      (isSup
        ? "bg-[#064e3b]/40 text-emerald-200 border-emerald-500/30"
        : "bg-[#78350f]/40 text-amber-200 border-amber-500/30");
    
    const badgeIcon = document.createElement("span");
    badgeIcon.textContent = isSup ? "🛡️" : "⭐"; 
    badgeIcon.className = "opacity-90 grayscale-[0.2]"; 
    badgeIcon.setAttribute("aria-hidden", "true");
    
    const badgeText = document.createElement("span");
    badgeText.textContent = dutyLabel;
    
    badgeInner.appendChild(badgeIcon);
    badgeInner.appendChild(badgeText);
    badge.appendChild(badgeInner);

    row.appendChild(infoSection);
    row.appendChild(badge);
    return row;
  }

  function renderDuty(items) {
    const raw = items && items.items ? items.items : Array.isArray(items) ? items : [];
    const list = Array.isArray(raw) ? raw : [];

    if (dom.dutyTotal) setTextIfChanged(dom.dutyTotal, String(list.length || 0));
    if (!dom.dutyTrack || !dutyScroller) return;

    const sig = dutySignature(list);
    dutyScroller.render(sig, () => {
      if (!list.length) {
        const msg = document.createElement("div");
        msg.style.textAlign = "center";
        msg.style.opacity = "0.75";
        msg.style.padding = "30px 12px";
        msg.textContent = "لا توجد تكليفات إشراف/مناوبة لليوم";
        msg.setAttribute("role", "status");
        return msg;
      }

      const wrap = document.createElement("div");
      wrap.style.display = "flex";
      wrap.style.flexDirection = "column";
      wrap.style.gap = "12px";
      wrap.style.paddingBottom = "12px";
      // نفس سلوك كرت الحصص الجارية
      wrap.dataset.forceScroll = list.length >= 4 ? "1" : "0";
      wrap.setAttribute("role", "list");
      wrap.setAttribute("aria-label", "تكليفات الإشراف والمناوبة");

      list.forEach((it) => {
        wrap.appendChild(buildDutyRow(it));
      });

      return wrap;
    });
  }

  // ===== Featured panel toggle =====
  function renderFeaturedPanel(snap) {
    const s = (snap && snap.settings) || {};
    // Once the school day is over, the honour board and the duty/supervision
    // board describe a day that has finished. The standby and period lists
    // already empty themselves on rt.dayOver; these two never did, so they were
    // the only panels still lit on an out-of-hours screen. Treating day-over
    // like the per-screen flags being off keeps the adaptive layout in charge:
    // featuredEmpty flips to "1" and the board falls back to its ambient mode
    // instead of leaving a hole where the card used to be.
    const dayOver = !!(rt && rt.dayOver);
    const showDutyPanel = !dayOver && screenFlag("screenShowDuty", true);
    const showExcellencePanel = !dayOver && screenFlag("screenShowExcellence", true);
    let mode = safeText(screenSetting("screenFeaturedPanel", "") || s.featured_panel || "excellence");
    if (mode === "duty" && !showDutyPanel && showExcellencePanel) mode = "excellence";
    if (mode !== "duty" && !showExcellencePanel && showDutyPanel) mode = "duty";
    const showDuty = mode === "duty";
    const dutyRaw =
      showDutyPanel && snap && snap.duty && Array.isArray(snap.duty.items)
        ? snap.duty.items
        : (showDutyPanel && snap && Array.isArray(snap.duty) ? snap.duty : []);
    const excellenceRaw = showExcellencePanel && snap && Array.isArray(snap.excellence) ? snap.excellence : [];
    const excellenceHasItems = excellenceRaw.some((x) => {
      x = x || {};
      return !!(
        x.name ||
        x.student_name ||
        x.teacher_name ||
        x.full_name ||
        x.display_name ||
        (x.student && x.student.name) ||
        (x.teacher && x.teacher.name)
      );
    });
    const hasFeatured = (showDutyPanel || showExcellencePanel) && (showDuty ? dutyRaw.length > 0 : excellenceHasItems);

    setBoardData("featuredMode", showDuty ? "duty" : "excellence");
    setBoardData("featuredEmpty", hasFeatured ? "0" : "1");

    toggleHidden(dom.exCard, showDuty || !hasFeatured);
    toggleHidden(dom.dutyCard, !showDuty || !hasFeatured);

    // Feed the already-gated lists rather than the raw payload: a hidden
    // honour board would otherwise keep its rotation interval running all
    // night against a card nobody can see.
    if (showDuty) {
      renderDuty({ items: dutyRaw });
    } else {
      renderExcellence(excellenceRaw);
    }
    refreshAdaptiveLayout();
  }

  // ===== Main state render =====

  function renderEmergencyAlerts(alerts) {
    const overlay = document.getElementById("emergencyOverlay");
    if (!overlay) return;
    clearNamedTimer("emergency_boundary");

    const screenId = parseInt(document.body.getAttribute("data-screen-id") || "0", 10);
    const list = Array.isArray(alerts) ? alerts : [];
    const currentMs = nowMs();
    let selected = null;
    let nextBoundaryMs = null;

    for (let i = 0; i < list.length; i++) {
      const item = list[i] || {};
      const targets = Array.isArray(item.screen_ids) ? item.screen_ids.map(Number) : [];
      if (targets.length && targets.indexOf(screenId) < 0) continue;

      // A snapshot can remain cached after an emergency alert expires. Enforce
      // its time window in the display too, so the overlay never depends on a
      // later HTTP poll or WebSocket event to disappear.
      const startsMs = item.starts_at ? Date.parse(String(item.starts_at)) : NaN;
      const expiresMs = item.expires_at ? Date.parse(String(item.expires_at)) : NaN;

      if (!isNaN(startsMs) && startsMs > currentMs) {
        if (nextBoundaryMs === null || startsMs < nextBoundaryMs) nextBoundaryMs = startsMs;
        continue;
      }
      if (!isNaN(expiresMs)) {
        if (expiresMs <= currentMs) continue;
        if (nextBoundaryMs === null || expiresMs < nextBoundaryMs) nextBoundaryMs = expiresMs;
      }

      if (!selected) selected = item;
    }

    if (nextBoundaryMs !== null) {
      // The small cushion avoids re-running just before the exact millisecond
      // because of timer rounding. Cap long waits at the browser timeout limit;
      // re-evaluation also promotes the next alert.
      setNamedTimer("emergency_boundary", function () {
        renderEmergencyAlerts(list);
      }, Math.min(2147483000, Math.max(50, nextBoundaryMs - currentMs + 50)), "emergency_time_boundary");
    }

    if (!selected) {
      overlay.classList.add("u-hidden");
      overlay.removeAttribute("data-kind");
      return;
    }
    const symbols = {
      evacuation: "🚪",
      fire: "🔥",
      weather: "🌧️",
      suspension: "⛔",
      urgent: "⚠️",
    };
    overlay.setAttribute("data-kind", safeText(selected.kind || "urgent"));
    setTextIfChanged(document.getElementById("emergencySymbol"), symbols[selected.kind] || "⚠️");
    setTextIfChanged(document.getElementById("emergencyTitle"), selected.title || "تنبيه طارئ");
    setTextIfChanged(document.getElementById("emergencyMessage"), selected.message || "");
    setTextIfChanged(document.getElementById("emergencyKind"), selected.kind_label || "رسالة عاجلة");
    overlay.classList.remove("u-hidden");
  }

  function _formatOfflineAge(ageMs) {
    if (!ageMs || !isFinite(ageMs)) return "";
    const minutes = Math.floor(ageMs / 60000);
    if (minutes < 60) return "قبل " + Math.max(1, minutes) + " دقيقة";
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return "قبل " + hours + " ساعة";
    const days = Math.floor(hours / 24);
    return "قبل " + days + " يوم";
  }

  /**
   * شارة عدم الاتصال بثلاث درجات بدل ثنائية متصل/غير متصل:
   *   • أقل من ١٥ دقيقة → لا شيء (انقطاع قصير طبيعي)
   *   • بيانات اليوم نفسه  → عمر آخر تحديث
   *   • بعد تغيّر التاريخ  → تحذير صريح بأن الجدول محلي وغير مؤكد
   */
  function renderOfflineMode(payload) {
    const badge = document.getElementById("offlineModeBadge");
    if (!badge) return;

    const isCached = !!(payload && payload._offlineCached);
    if (!isCached) {
      badge.classList.add("u-hidden");
      badge.removeAttribute("data-severity");
      return;
    }

    const ageMs = Number(payload._offlineAgeMs) || 0;
    const dayShifted = !!payload._offlineDayShifted;

    if (!dayShifted && ageMs > 0 && ageMs < OFFLINE_QUIET_MS) {
      badge.classList.add("u-hidden");
      badge.removeAttribute("data-severity");
      return;
    }

    let text;
    let severity;
    if (dayShifted && !payload._offlineHasDayPlan) {
      // لا نعرف جدول اليوم إطلاقًا: أعلى درجات التحذير.
      severity = "critical";
      text = "غير متصلة — تعذّر تحديد جدول اليوم";
    } else if (dayShifted) {
      // نعرض جدولًا أو إجازة من خطة الأسبوع المحفوظة: صحيح غالبًا، لكنه لم
      // يُؤكَّد من الخادم منذ أمس.
      severity = "warning";
      text = "غير متصلة — الجدول من النسخة المحفوظة";
    } else {
      severity = "notice";
      const age = _formatOfflineAge(ageMs);
      text = age ? "غير متصلة — آخر تحديث " + age : "غير متصلة — آخر بيانات محفوظة";
    }

    setTextIfChanged(badge, text);
    badge.setAttribute("data-severity", severity);
    badge.classList.remove("u-hidden");
  }


  function renderState(payload) {
    if (!payload) return;

    lastPayloadForFiltering = payload;
    renderEmergencyAlerts(payload.emergency_alerts || []);
    renderOfflineMode(payload);

    // Legacy/back-compat: payload.now is only a fallback. Snapshot bodies are cached,
    // while X-Server-Time-MS is generated per response. Re-applying cached payload.now
    // after the fresh header would move the display clock backwards by the cache age,
    // delaying period transitions and the bell by minutes.
    if (payload.now) {
      const serverMs = new Date(payload.now).getTime();
      const hasFreshHeaderSync = lastServerHeaderSyncAt > 0 && Date.now() - lastServerHeaderSyncAt < 5000;
      if (!isNaN(serverMs) && !hasFreshHeaderSync) applyServerNowMs(serverMs, "payload");

      // Learn server timezone offset + local date (stable even when payload is cached).
      try {
        const offMin = _parseTzOffsetMinFromIso(payload.now);
        if (offMin !== null) {
          // Prefer YYYY-MM-DD from payload.now itself (most reliable across clients and schemas).
          let dstr = null;
          const s = String(payload.now || "").trim();
          const m = s.match(/^(\d{4}-\d{2}-\d{2})/);
          if (m && m[1]) dstr = m[1];
          if (!dstr) {
            const meta0 = payload.meta || {};
            dstr = meta0.date || meta0.local_date || meta0.day || null;
          }
          if (dstr) applyServerCalendar(String(dstr), offMin);
        }
      } catch (e) {}
    }

    const baseMs = nowMs();
    hydrateBrand(payload);

    // Load day_path into client-side schedule engine for local transitions.
    try { dayEngineLoad(payload); } catch (e) {}

    const settings = payload.settings || {};
    const meta = payload.meta || {};

    applyDisplayCopySettings(settings);

    // Persist schedule revision so /status?v=... remains authoritative.
    try {
      const rawRev = _nc(meta.schedule_revision, _nc(meta.scheduleRevision, meta.rev));
      const n = parseInt(String(rawRev), 10);
      if (!isNaN(n) && n >= 0) {
        rt.scheduleRevision = n;
        try {
          localStorage.setItem(revStorageKey(), String(n));
        } catch (e) {}
      }
    } catch (e) {}

    if (settings.school_type) {
      cfg.SCHOOL_TYPE = settings.school_type;
    }

    if (typeof settings.refresh_interval_sec === "number" && settings.refresh_interval_sec > 0) {
      let nInt = clamp(settings.refresh_interval_sec, 5, 864000);
      // Add a small *percentage* jitter always to reduce stampedes (especially at scale).
      // Keep it bounded and never below 5s.
      const jf = Number(rt.refreshJitterFrac) || 0;
      if (isFinite(jf) && jf !== 0) {
        nInt = clamp(Math.round(nInt * (1 + jf)), 5, 864000);
      }
      if (Math.abs(nInt - cfg.REFRESH_EVERY) > 0.001) cfg.REFRESH_EVERY = nInt;

      // Keep status-first polling in sync by default.
      if (!rt.statusEverySec || rt.statusEverySec < 1) rt.statusEverySec = cfg.REFRESH_EVERY;
    }

    const screenStandbySpeed = Number(screenSetting("standby", ""));
    const effectiveStandbySpeed = screenStandbySpeed > 0 ? screenStandbySpeed : settings.standby_scroll_speed;
    if (typeof effectiveStandbySpeed === "number" && effectiveStandbySpeed > 0) {
      cfg.STANDBY_SPEED = normSpeed(effectiveStandbySpeed, cfg.STANDBY_SPEED);
    }
    const screenPeriodsSpeed = Number(screenSetting("periodsSpeed", ""));
    const effectivePeriodsSpeed = screenPeriodsSpeed > 0 ? screenPeriodsSpeed : settings.periods_scroll_speed;
    if (typeof effectivePeriodsSpeed === "number" && effectivePeriodsSpeed > 0) {
      cfg.PERIODS_SPEED = normSpeed(effectivePeriodsSpeed, cfg.PERIODS_SPEED);
    }

    if (periodsScroller) periodsScroller.recalc();
    if (standbyScroller) standbyScroller.recalc();
    if (dutyScroller) dutyScroller.recalc();

    tickClock(payload.date_info || null);

    const s = payload.state || {};
    const stType = normalizeDisplayStateType(s.type || "");
    const stateReason = getStateReason(s);
    const stateBadge = safeText(s.badge || "");
    const stateCurrent = s && typeof s === "object" ? s.current || null : null;
    const stateNext = s && typeof s === "object" ? s.next || null : null;
    const current = payload.current_period || stateCurrent || null;
    const nextP = payload.next_period || stateNext || null;
    const stateFrom =
      s.from ||
      (current && (current.from || current.starts_at)) ||
      null;
    const stateTo =
      s.to ||
      (current && (current.to || current.ends_at)) ||
      null;
    const nextFrom = (nextP && (nextP.from || nextP.starts_at)) || null;
    const nextTo = (nextP && (nextP.to || nextP.ends_at)) || null;

    // Snapshot responses are server-cached for a few seconds; keep countdown monotonic for the same state.
    const prevCountdown = hasActiveCountdown && typeof countdownSeconds === "number" ? countdownSeconds : null;
    const prevCoreSig = lastStateCoreSig;
    const nextCoreSig =
      stType + "||" + safeText(s.label || "") + "||" + safeText(s.from || "") + "||" + safeText(s.to || "");
    lastStateCoreSig = nextCoreSig;

    // ===== تشغيل الجرس عند تغيّر نوع النشاط =====
    try { checkStateTransitionBell(stType); } catch (e) {}

    // ===== تحديث runtime =====
    rt.activePeriodIndex = getPeriodIndex(current) || getPeriodIndex(nextP) || getCurrentPeriodIdxFromPayload(payload) || null;
    rt.activeFromHM =
      (stType === "period"
        ? stateFrom
        : (nextFrom || stateFrom)) || null;
    rt.activeToHM = stateTo || null;
    rt.activeStateType = stType || null;
    rt.activeTargetHM = (stType === "before" ? stateFrom : stateTo) || null;
    rt.activeTargetMs = null;
    rt.dayOver = computeDayOver(payload, baseMs);
    try { ingestPeriodClassesFromPayload(payload); } catch (e) {}

    countdownSeconds = null;
    progressRange = { start: null, end: null };
    hasActiveCountdown = false;

    if (stType === "period" || stType === "break" || stType === "before") {
      let localCalc = null;
      // ✅ FIX: استخدام nowMs() المباشر لضمان التزامن الدقيق مع الساعة المعروضة
      // نحسب الوقت لحظياً لتجنب أي تأخير في المعالجة
      const currentMs = nowMs();
      const targetHM = stType === "before" ? stateFrom : stateTo;
      let targetMs = null;
      if (targetHM) {
        const tMs = hmToMs(targetHM, currentMs);
        // الحساب: وقت الهدف - الوقت الحالي = الوقت المتبقي
        if (tMs) {
          targetMs = tMs;
          localCalc = Math.floor((tMs - currentMs) / 1000);
        }
      }
      rt.activeTargetMs = isFinite(Number(targetMs)) ? Number(targetMs) : null;

      const serverRem =
        typeof s.remaining_seconds === "number"
          ? Math.max(0, Math.floor(s.remaining_seconds))
          : null;

      // Use local calculation if valid (sanity check: result is between -12h and +24h)
      if (localCalc !== null && localCalc > -43200 && localCalc < 86400) {
        countdownSeconds = Math.max(0, localCalc);
      } else if (serverRem !== null) {
        countdownSeconds = serverRem;
      }

      if (countdownSeconds !== null) hasActiveCountdown = true;
    }

    // If the server says 0 (or we clamped to 0) and we haven't handled this core state yet,
    // trigger the countdown-zero refresh even if we didn't observe a local 1->0 transition.
    if (hasActiveCountdown && typeof countdownSeconds === "number" && countdownSeconds === 0) {
      const zeroBoundaryReached = isBoundaryReached(rt.activeTargetHM, rt.activeTargetMs, nowMs());
      if (zeroBoundaryReached && nextCoreSig && nextCoreSig !== lastZeroHandledCoreSig) {
        lastZeroHandledCoreSig = nextCoreSig;
        onCountdownZero();
      }
    }

    applyBoardStatePresentation(
      stType,
      hasActiveCountdown && typeof countdownSeconds === "number" && countdownSeconds > 0
    );

    if ((stType === "period" || stType === "break") && stateFrom && stateTo) {
      const start = hmToMs(stateFrom, baseMs);
      const end = hmToMs(stateTo, baseMs);
      if (start && end && end > start) {
        progressRange.start = start;
        progressRange.end = end;
      }
    }

    let title = safeText(s.label || "لوحة العرض المدرسية");
    let range = (stateFrom || stateTo) ? fmtTimeRange(stateFrom, stateTo) : fmtTimeRange(null, null);
    let badge = "حالة اليوم";

    if (stType === "period") {
      badge = "درس";
      title = formatPeriodTitle(current);
    } else if (stType === "break") {
      badge = "استراحة";
      title = safeText(s.label || "استراحة");
    } else if (stType === "before") {
      badge = DISPLAY_COPY.beforeBadge;
      title = DISPLAY_COPY.beforeTitle;
    } else if (stType === "after") {
      const afterCopy = getAfterDayOverCopy();
      badge = afterCopy.badge;
      title = afterCopy.title;
    } else if (stType === "holiday") {
      badge = DISPLAY_COPY.holidayBadge;
      title = DISPLAY_COPY.holidayTitle;
      range = "--:--";
    } else if (stType === "day") {
      badge = "اليوم الدراسي";
      title = safeText(s.label || "اليوم الدراسي");
    } else if (stType === "off") {
      badge = stateReason === "after_hours" ? DISPLAY_COPY.afterHolidayBadge : DISPLAY_COPY.holidayBadge;
      title = stateReason === "after_hours" ? DISPLAY_COPY.afterHolidayTitle : DISPLAY_COPY.holidayTitle;
      range = "--:--";
    }

    const stateSig =
      stType +
      "||" +
      title +
      "||" +
      range +
      "||" +
      safeText(stateFrom) +
      "||" +
      safeText(stateTo) +
      "||" +
      safeText(s.remaining_seconds);

    if (stateSig !== last.stateSig) {
      last.stateSig = stateSig;
      setTextIfChanged(dom.heroTitle, title);
      setTextIfChanged(dom.heroRange, range);
      setTextIfChanged(dom.badgeKind, badge);
    }

    if (dom.nextLabel) {
      const nextSig =
        nextP && (nextFrom || nextTo || nextP.label || nextP.index || nextP.period_index)
          ? safeText(nextP.kind || nextP.type || "") +
            "||" +
            safeText(nextFrom) +
            "||" +
            safeText(nextTo) +
            "||" +
            safeText(nextP.label) +
            "||" +
            safeText(getPeriodIndex(nextP) || "")
          : "none";

      if (nextSig !== last.nextSig) {
        last.nextSig = nextSig;
        renderNextLabel(nextSig === "none" ? null : nextP);
      }
    }

    renderCurrentChips(stType, s, current);
    renderMiniSchedule(payload, baseMs);

    // Local-time authority: if cached payload lags behind real boundary, correct UI
    // immediately from day_path without any server request.
    try { dayEngineSyncToLocalNow(); } catch (e) {}

    // Smart Sleep/Wake: evaluate whether to enter sleep mode based on new payload.
    try { sleepEvaluate(); } catch (e) {
      if (isDebug()) console.warn("[sleep] evaluate error:", e);
    }
  }

  // ===== Ticker 1s =====
  let tickerId = null;
  let _tickCount = 0; // ✅ عداد لتوزيع العمليات الثقيلة على فترات مختلفة
  function startTicker() {
    if (tickerId) return;
    tickerId = setInterval(() => {
      _tickCount++;
      
      // ✅ CLOCK DRIFT DETECTION: فحص تغييرات التوقيت كل 3 ثوانٍ بدلاً من كل ثانية
      if (_tickCount % 3 === 0) {
        if (detectClockDrift()) {
          requestReSyncIfNeeded();
        }
      }
      
      tickClock();

      // ✅ Keep period/break/before transitions — كل 2 ثانية بدلاً من كل ثانية
      if (_tickCount % 2 === 0) {
        try { dayEngineSyncToLocalNow(); } catch (e) {}
      }

      // Safety net for browsers that suspend exact timers. The dedicated
      // one-shot boundary timer handles the normal path with sub-second timing.
      try { tickBoundaryBell(); } catch (e) {}

      if (hasActiveCountdown && typeof countdownSeconds === "number") {
        const prev = countdownSeconds;
        const tickNowMs = nowMs();
        let boundaryReached = false;

        // إعادة حساب الوقت المتبقي من الساعة المتزامنة مع السيرفر بدل الإنقاص -1
        // هذا يمنع أي انحراف تراكمي من setInterval غير الدقيق
        var recalcOk = false;
        if (rt.activeTargetHM) {
          try {
            var tgtMs = rt.activeTargetMs;
            if (!tgtMs) tgtMs = hmToMs(rt.activeTargetHM, tickNowMs);
            if (tgtMs) {
              rt.activeTargetMs = tgtMs;
              boundaryReached = tickNowMs >= tgtMs;
              var freshRem = Math.floor((tgtMs - tickNowMs) / 1000);
              if (freshRem > -43200 && freshRem < 86400) {
                countdownSeconds = Math.max(0, freshRem);
                recalcOk = true;
              }
            }
          } catch (e) {}
        }
        // Fallback: إنقاص -1 إذا فشل الحساب
        if (!recalcOk) {
          if (countdownSeconds > 0) countdownSeconds -= 1;
        }

        if (prev > 0 && countdownSeconds === 0 && boundaryReached) onCountdownZero();

        // Handle cases where countdown starts at 0 (server rounding/caching) without a local 1->0 transition.
        if (countdownSeconds === 0 && boundaryReached && lastStateCoreSig && lastStateCoreSig !== lastZeroHandledCoreSig) {
          lastZeroHandledCoreSig = lastStateCoreSig;
          onCountdownZero();
        }
      }

      if (dom.countdown) {
        if (hasActiveCountdown && typeof countdownSeconds === "number") {
          const hh = Math.floor(countdownSeconds / 3600);
          const mm = Math.floor((countdownSeconds % 3600) / 60);
          const ss = countdownSeconds % 60;
          
          // عرض HH:MM:SS إذا كان الوقت أكثر من ساعة، وإلا MM:SS
          const timeDisplay = hh > 0 
            ? fmt2(hh) + ":" + fmt2(mm) + ":" + fmt2(ss)
            : fmt2(mm) + ":" + fmt2(ss);
          
          setTextIfChanged(dom.countdown, timeDisplay);
        } else {
          setTextIfChanged(dom.countdown, "--:--");
        }
      }

      if (dom.progressBar) {
        if (progressRange.start && progressRange.end && progressRange.end > progressRange.start) {
          const n = nowMs();
          let pct = ((n - progressRange.start) / (progressRange.end - progressRange.start)) * 100;
          pct = clamp(pct, 0, 100);
          dom.progressBar.style.width = pct.toFixed(1) + "%";
          setRing(pct);
        } else {
          dom.progressBar.style.width = "0%";
          setRing(0);
        }
      }
    }, 1000);
  }

  // ===== Fetch with timeout + ETag/304 =====
  let inflight = null;
  let ctrl = null;
  let inflightStatus = null;
  let ctrlStatus = null;
  // ETag key scoped per token for SaaS multi-tenancy
  function _etagKey() {
    var t = (getToken() || "").toString().trim();
    var base = "display_etag_" + (location.pathname || "/");
    if (t) return base + ":" + encodeURIComponent(t).slice(0, 80);
    return base;
  }

  // Client-side request budget: prevents hammering the server with snapshot requests.
  // Tracks requests in a rolling 60-second window. If over budget, delays the request.
  const _snapshotTimestamps = [];
  const _SNAPSHOT_BUDGET_PER_MIN = 40; // max snapshot requests per 60s window

  function _snapshotBudgetOk() {
    var now = Date.now();
    // Purge entries older than 60s
    while (_snapshotTimestamps.length > 0 && now - _snapshotTimestamps[0] > 60000) {
      _snapshotTimestamps.shift();
    }
    if (_snapshotTimestamps.length >= _SNAPSHOT_BUDGET_PER_MIN) {
      return false;
    }
    _snapshotTimestamps.push(now);
    return true;
  }

  // Device ID (stable per browser/device, scoped per token for SaaS multi-tenancy)
  // تحقق سريع: localStorage.getItem("school_display_device_id_<token>")
  function _deviceIdKey() {
    var t = (getToken() || "").toString().trim();
    if (t) return "school_display_device_id_" + encodeURIComponent(t).slice(0, 80);
    return "school_display_device_id";
  }
  let memDeviceId = "";

  function _consumePairingDeviceId() {
    var hash = "";
    try { hash = (window.location.hash || "").replace(/^#/, ""); } catch (e) {}
    if (!hash) return "";

    var pieces = hash.split("&");
    var paired = "";
    for (var i = 0; i < pieces.length; i += 1) {
      var part = pieces[i].split("=");
      if (part[0] === "pair") {
        try { paired = decodeURIComponent(part.slice(1).join("=")); } catch (e) { paired = ""; }
        break;
      }
    }
    if (!/^[A-Za-z0-9._:-]{16,64}$/.test(paired)) return "";

    try {
      if (window.history && typeof window.history.replaceState === "function") {
        window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
      }
    } catch (e) {}
    return paired;
  }

  // migrate legacy global key → scoped key (one-time)
  function _migrateDeviceId() {
    var scopedKey = _deviceIdKey();
    if (scopedKey === "school_display_device_id") return ""; // no token yet
    var paired = _consumePairingDeviceId();
    if (paired) {
      memDeviceId = paired;
      try {
        localStorage.setItem(scopedKey, paired);
        localStorage.removeItem("school_display_pairing_device_id");
      } catch (e) {}
      return paired;
    }
    try {
      var existing = (localStorage.getItem(scopedKey) || "").trim();
      if (existing) return; // already migrated
      var legacy = (localStorage.getItem("school_display_device_id") || "").trim();
      if (legacy) localStorage.setItem(scopedKey, legacy);
    } catch (e) {}
    return "";
  }

  function fallbackDeviceId() {
    // No cookies. Prefer crypto, fall back to a random-ish stable value.
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
    } catch (e) {}

    try {
      if (window.crypto && typeof window.crypto.getRandomValues === "function") {
        const buf = new Uint8Array(16);
        window.crypto.getRandomValues(buf);
        const hex = Array.from(buf)
          .map((b) => (b < 16 ? "0" : "") + b.toString(16))
          .join("");
        return "d-" + hex;
      }
    } catch (e) {}

    return "d-" + String(Math.random()).slice(2) + "-" + String(Date.now());
  }

  function getOrCreateDeviceId() {
    if (memDeviceId) return memDeviceId;
    try {
      var pairingDeviceId = _migrateDeviceId();
      if (pairingDeviceId) return pairingDeviceId;
      var key = _deviceIdKey();
      const existing = (localStorage.getItem(key) || "").trim();
      if (existing) {
        memDeviceId = existing;
        return memDeviceId;
      }
      const created = fallbackDeviceId();
      localStorage.setItem(key, created);
      memDeviceId = created;
      return memDeviceId;
    } catch (e) {
      // If localStorage is blocked, fall back to in-memory per page load.
      memDeviceId = fallbackDeviceId();
      return memDeviceId;
    }
  }

  function withTimeout(promise, ms, onTimeout) {
    let t = null;
    const timeout = new Promise((_, rej) => {
      t = setTimeout(() => {
        try {
          if (onTimeout) onTimeout();
        } catch (e) {}
        rej(new Error("timeout"));
      }, ms);
    });
    return Promise.race([promise, timeout]).finally(() => {
      if (t) clearTimeout(t);
    });
  }

  function _offlineSnapshotKey() {
    const token = getToken() || "display";
    return "display:offline-snapshot:v2:" + token.slice(0, 20);
  }

  function persistOfflineSnapshot(payload) {
    if (!payload || payload._offlineCached || payload._rateLimited || payload._notModified) return;
    try {
      const savedAt = Date.now();
      localStorage.setItem(
        _offlineSnapshotKey(),
        // dayKey يُحفظ صراحةً حتى نكتشف عبور منتصف الليل حتى لو انحرفت ساعة الجهاز.
        JSON.stringify({savedAt: savedAt, dayKey: _localDateKey(savedAt), payload: payload})
      );
    } catch (e) {
      try {
        localStorage.removeItem(_offlineSnapshotKey());
      } catch (_) {}
    }
  }

  // ===== Offline resilience =================================================
  // النسخة المحفوظة تُبقي الشاشة تعمل بلا إنترنت، لكن الثقة بها تتناقص مع
  // الوقت. القاعدة: نعرض ما نعرف أنه ما زال صحيحًا، ونُسقط ما لا نستطيع
  // التحقق منه — شاشة تقول "بانتظار الاتصال" أفضل من شاشة تعرض جدول الأمس
  // بثقة كاملة.
  // ==========================================================================

  // أقل من هذا العمر لا يستحق إزعاجًا بصريًا: الانقطاعات القصيرة طبيعية.
  const OFFLINE_QUIET_MS = 15 * 60 * 1000;

  function _localDateKey(ms) {
    const d = new Date(typeof ms === "number" ? ms : nowMs());
    const pad = (n) => (n < 10 ? "0" + n : String(n));
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  /** ترقيم قاعدة البيانات لليوم: الاثنين=1 .. الأحد=7 */
  function _dbWeekday(ms) {
    const jsDay = new Date(typeof ms === "number" ? ms : nowMs()).getDay(); // الأحد=0
    return jsDay === 0 ? 7 : jsDay;
  }

  /**
   * تكييف نسخة محفوظة عبرت منتصف الليل مع اليوم الحالي.
   *
   * جدول اليوم الجديد يُستبدل من `meta.week_plan` (أوقات محلية مستقلة عن
   * التاريخ)، ويُسقط كل محتوى مرتبط بيوم بعينه لأنه لم يعد قابلًا للتحقق:
   * حصص الانتظار والإشراف وفصول الحصة الجارية تخص يوم أمس، لا اليوم.
   */
  function _rehydrateForNewDay(payload) {
    const weekPlan = (payload.meta && payload.meta.week_plan) || null;
    const todayPlan = weekPlan ? weekPlan[String(_dbWeekday())] : null;

    payload._offlineDayShifted = true;
    payload.day_path = Array.isArray(todayPlan) ? todayPlan.slice() : [];
    payload._offlineHasDayPlan = Array.isArray(todayPlan);

    // محتوى اليوم السابق: إسقاطه أصدق من عرضه.
    payload.period_classes = [];
    payload.standby = { items: [] };
    payload.duty = { items: [] };
    payload.current_period = null;
    payload.next_period = null;

    // اسمح لمحرك اليوم بإعادة البناء بدل الإبقاء على توقيع الأمس. اليوم بلا
    // جدول يُفرَّغ صراحةً لأن ``dayEngineLoad`` يتجاهل الجداول الفارغة.
    if (payload.day_path.length) {
      _dayEngine.lastDayKey = "";
    } else {
      dayEngineClear();
    }

    // التاريخ يُعاد حسابه من الساعة المحلية. مسح الذاكرة المؤقتة ضروري: بدونه
    // يظل `tickClock` يعرض تاريخ الأمس المحفوظ حتى لو أفرغنا الحمولة.
    payload.date_info = null;
    cachedDateInfo = null;

    // ثلاث حالات مختلفة، ولكل واحدة رسالتها الصادقة:
    const settings = payload.settings || {};
    if (!payload._offlineHasDayPlan) {
      // لا خطة أسبوع أصلًا (نسخة قديمة) → لا نعرف شيئًا عن اليوم.
      payload.state = {
        type: "off",
        reason: "offline_unknown_day",
        label: "بانتظار الاتصال لتحديث جدول اليوم",
        badge: "غير متصلة",
      };
    } else if (!payload.day_path.length) {
      // إجازة معروفة من خطة الأسبوع → رسالة الإجازة المعتادة للمدرسة.
      payload.state = {
        type: "off",
        reason: "holiday",
        label: safeText(settings.display_holiday_title || "") || "إجازة",
        badge: safeText(settings.display_holiday_badge || "") || "إجازة",
      };
    } else {
      // يوم دراسي معروف → المحرك المحلي يتولى حساب الحالة من الجدول.
      payload.state = { type: "off", reason: "offline_recomputed" };
    }
    return payload;
  }

  function loadOfflineSnapshot() {
    try {
      const raw = localStorage.getItem(_offlineSnapshotKey());
      if (!raw) return null;
      const stored = JSON.parse(raw);
      if (!stored || !stored.payload) return null;

      const payload = stored.payload;
      const savedAt = Number(stored.savedAt) || 0;
      payload._offlineCached = true;
      payload._offlineSavedAt = savedAt || null;
      payload._offlineAgeMs = savedAt ? Math.max(0, nowMs() - savedAt) : null;

      const savedDayKey = String(stored.dayKey || (savedAt ? _localDateKey(savedAt) : ""));
      if (savedDayKey && savedDayKey !== _localDateKey()) {
        return _rehydrateForNewDay(payload);
      }
      payload._offlineDayShifted = false;
      return payload;
    } catch (e) {
      return null;
    }
  }

  async function safeFetchSnapshot(opts) {
    opts = opts || {};
    if (isBlocked && !isTerminalBlockedMode()) return null;
    if (isTerminalBlockedMode()) {
      _log("snapshot_blocked_due_to_binding_loss", _logContext({
        sourcePath: "safeFetchSnapshot_preflight",
      }));
      return null;
    }
    if (inflight && !opts.force) return inflight;

    // Client-side budget guard: prevent exceeding server anti-loop limits.
    if (!_snapshotBudgetOk()) {
      if (isDebug()) setDebugText("snapshot skipped (client budget) | " + new Date().toLocaleTimeString());
      return { _rateLimited: true };
    }

    const token = getToken();
    const baseUrl = resolveSnapshotUrl();

    const u = new URL(baseUrl, window.location.origin);
    // Always provide a stable device identifier.
    // Some CDNs/proxies may strip non-standard request headers, so also include it in the query.
    const deviceId = getOrCreateDeviceId();
    u.searchParams.set("dk", deviceId);

    // Versioned cache key: ensure CDNs (or misconfigured cache rules) do not keep serving
    // an older snapshot across revisions. This is NOT a random cache-buster; it changes only
    // when the school's revision changes.
    try {
      u.searchParams.set("rev", String(rt.scheduleRevision || 0));
    } catch (e) {}

    // Production-safe transition refresh: used at countdown==0 to force a fresh snapshot build
    // without requiring `?nocache=1` (which is intentionally blocked in production).
    // Server-side is expected to apply stampede protection (shared lock/wait).
    if (opts.transition) {
      try {
        u.searchParams.set("transition", "1");
      } catch (e) {}
    }

    // IMPORTANT (Production): never add cache-busting params to snapshot requests.
    // Allow it only in explicit debug mode (?debug=1).
    if (isDebug()) {
      // Cache-busting is *opt-in* even in debug mode: require `?nocache=1` explicitly.
      // This prevents accidental `?debug=1` deployments from spamming the snapshot API.
      let pageNoCache = false;
      try {
        const qs = new URLSearchParams(window.location.search);
        pageNoCache = (qs.get("nocache") || "").trim() === "1";
        if (pageNoCache) u.searchParams.set("nocache", "1");
      } catch (e) {}

      if (opts.bypassServerCache && pageNoCache) {
        u.searchParams.set("nocache", "1");
        u.searchParams.set("_t", String(Date.now()));
        u.searchParams.set("_cb", String(Date.now()));
      }
    }

    // Old TV browser fix (Tizen ≤3 / WebOS ≤3 / generic SmartTV / school proxies):
    // `cache: "no-store"` and the `If-None-Match` opt-out alone do NOT defeat
    // intermediate caches reliably. When we explicitly bypass ETag (force_refresh,
    // WS push, transition, cold load) we MUST also vary the URL so any cached
    // 200 response is bypassed. We use `_ts` (mirroring /status) which is bounded
    // because it only fires on bypassEtag — not on every poll — so rate-limiting
    // (token+device based) is unaffected.
    if (opts.bypassEtag) {
      try {
        u.searchParams.set("_ts", String(Date.now()));
      } catch (e) {}
    }

    if (ctrl) {
      try {
        ctrl.abort();
      } catch (e) {}
    }
    ctrl = window.AbortController ? new AbortController() : null;

    const headers = {
      Accept: "application/json",
      "X-Display-Token": token || "",
      "X-Display-Device": deviceId,
      // HTTP/1.0 caches and many TV browsers honor Pragma over Cache-Control.
      "Pragma": "no-cache",
      "Cache-Control": "no-cache, no-store, max-age=0",
    };

    if (!opts.bypassEtag) {
      try {
        // Important: on a cold page load, we may not have any in-memory payload yet.
        // If we send If-None-Match and get 304, the UI can get stuck showing "جاري التحميل".
        // So only enable 304 optimization after we've rendered at least one payload.
        const canUse304 = !!lastPayloadForFiltering;
        if (canUse304) {
          const prev = localStorage.getItem(_etagKey()) || "";
          if (prev) headers["If-None-Match"] = prev;
        }
      } catch (e) {}
    } else {
      try {
        localStorage.removeItem(_etagKey());
      } catch (e) {}
    }

    const fetchPromise = fetch(u.toString(), {
      method: "GET",
      headers,
      cache: "no-store",
      // Never send cookies on snapshot.
      credentials: "omit",
      signal: ctrl ? ctrl.signal : undefined,
    }).then(async (r) => {
      if (isTerminalBlockedMode()) {
        _log("snapshot_blocked_due_to_binding_loss", _logContext({
          sourcePath: "safeFetchSnapshot_response",
          status: r.status,
        }));
        return null;
      }
      // Server clock sync: works even when response body is cached or 304.
      try {
        const h = r.headers.get("X-Server-Time-MS");
        if (h) applyServerNowMs(h, "header");
      } catch (e) {}

      if (r.status === 304) return { _notModified: true };

      if (r.status === 429) {
        // Rate-limited: don't hammer the server with fast retries.
        return { _rateLimited: true };
      }

      if (!r.ok) {
        // Diagnostics: log status + response body (if possible)
        let raw = "";
        try {
          raw = await r.text();
        } catch (e) {
          raw = "";
        }
        if (raw) {
          console.warn("[snapshot] HTTP", r.status, raw);
        } else {
          console.warn("[snapshot] HTTP", r.status);
        }

        let body = null;
        try {
          body = raw ? JSON.parse(raw) : null;
        } catch (e) {
          body = null;
        }

        var bindingInfo = isBindingConflictResponse(r, body, raw, "snapshot");
        if (bindingInfo.matched) {
          enterBindingLostState("snapshot_binding_conflict", {
            sourcePath: "snapshot",
            status: r.status,
            classification: bindingInfo.classification,
            code: bindingInfo.code,
            message: bindingInfo.message || raw,
            text: bindingInfo.text,
          });
          return null;
        }

        // 403 عادة تعني: الشاشة مرتبطة بجهاز آخر أو لا يوجد معرف جهاز
        if (r.status === 403) {
          const err = body && (body.error || body.code || body.detail);
          const msg = body && (body.message || body.detail);

          if (err === "screen_bound") {
            showBlocker(
              "هذه الشاشة مفعّلة على جهاز آخر",
              msg || "حرصًا على استقرار العرض وحماية بيانات المدرسة، يمكن تشغيل كل شاشة عرض على جهاز واحد فقط في الوقت نفسه.",
              "binding"
            );
            stopPolling();
            return null;
          }

          if (err === "missing_device_id" || err === "device_required") {
            showBlocker(
              "تعذر تعريف الجهاز",
              msg || "أعد فتح رابط الشاشة من المتصفح ثم انتظر ثوانٍ ليتم تفعيل العرض.",
              "device"
            );
            stopPolling();
            return null;
          }

          if (err === "device_mismatch") {
            showBlocker(
              "هذه الشاشة مفعّلة على جهاز آخر",
              msg || "هذه الشاشة تعمل حاليًا على الجهاز المرتبط بها. لفك الارتباط وتفعيلها هنا، استخدم لوحة التحكم.",
              "binding"
            );
            stopPolling();
            return null;
          }

          // 403 أخرى
          showBlocker(
            "لا يمكن عرض الشاشة",
            msg || "تم رفض الوصول. تحقق من الرابط أو راجع إدارة النظام.",
            "access"
          );
          stopPolling();
          return null;
        }

        // Non-403 errors: do not crash; keep polling.
        // If body is JSON with detail, include it in the error message.
        let msg = "";
        try {
          const obj = raw ? JSON.parse(raw) : null;
          msg = obj && (obj.detail || obj.message) ? String(obj.detail || obj.message) : "";
        } catch (e) {}

        throw new Error("HTTP " + r.status + (msg ? " | " + msg : ""));
      }

      const et = r.headers && r.headers.get ? (r.headers.get("ETag") || "") : "";
      if (et) {
        try {
          localStorage.setItem(_etagKey(), et);
        } catch (e) {}
      }

      var payload = await r.json().catch(() => null);
      if (isTerminalBlockedMode()) {
        _log("snapshot_blocked_due_to_binding_loss", _logContext({
          sourcePath: "safeFetchSnapshot_payload",
        }));
        return null;
      }
      persistOfflineSnapshot(payload);
      return payload;
    });

    // Dynamic timeout: 15s for first load, 9s for subsequent refreshes
    // First load may need more time for cache building
    const timeoutMs = lastPayloadForFiltering ? 9000 : 15000;
    
    inflight = withTimeout(fetchPromise, timeoutMs, () => {
      if (ctrl) {
        try {
          ctrl.abort();
        } catch (e) {}
      }
    })
      .catch((e) => {
        if (isTerminalBlockedMode()) return null;
        const cachedPayload = loadOfflineSnapshot();
        if (cachedPayload) {
          if (isDebug()) setDebugText("offline snapshot | " + (e && e.message ? e.message : String(e)));
          return cachedPayload;
        }
        renderAlert("تعذر جلب البيانات", "لا توجد نسخة محفوظة بعد. تحقق من اتصال الإنترنت.");
        ensureDebugOverlay();
        if (isDebug()) setDebugText("fetch error: " + (e && e.message ? e.message : String(e)));
        return null;
      })
      .finally(() => {
        inflight = null;
      });

    return await inflight;
  }

  async function safeFetchStatus(opts) {
    opts = opts || {};
    if (isBlocked && !isTerminalBlockedMode()) return { fetch_required: false, _blocked: true };
    if (isTerminalBlockedMode()) {
      _log("poll_blocked_due_to_binding_loss", _logContext({
        sourcePath: "safeFetchStatus_preflight",
      }));
      return { fetch_required: false, _blocked: true };
    }
    if (inflightStatus && !opts.force) return inflightStatus;

    const token = getToken();
    const baseUrl = resolveStatusUrl();

    const u = new URL(baseUrl, window.location.origin);
    // Keep device id consistent across endpoints (useful for server-side binding/diagnostics).
    const deviceId = getOrCreateDeviceId();
    u.searchParams.set("dk", deviceId);

    // Source of truth: numeric revision comparison.
    u.searchParams.set("v", String(rt.scheduleRevision || 0));

    // Defensive: /status must never be cached.
    // If a CDN cache rule accidentally includes /api/display/status/*, this guarantees each poll
    // is unique and reaches origin.
    try {
      u.searchParams.set("_ts", String(Date.now()));
    } catch (e) {}

    if (ctrlStatus) {
      try {
        ctrlStatus.abort();
      } catch (e) {}
    }
    ctrlStatus = window.AbortController ? new AbortController() : null;

    const headers = {
      Accept: "application/json",
      "X-Display-Token": token || "",
      "X-Display-Device": deviceId,
      "X-Display-Fallback": rt.wsConnected ? "0" : "1",
      // TV/proxy cache hardening (Tizen ≤3, WebOS ≤3, school proxies).
      "Pragma": "no-cache",
      "Cache-Control": "no-cache, no-store, max-age=0",
    };

    // Intentionally do NOT send If-None-Match for /status.

    const fetchPromise = fetch(u.toString(), {
      method: "GET",
      headers,
      cache: "no-store",
      credentials: "omit",
      signal: ctrlStatus ? ctrlStatus.signal : undefined,
    }).then(async (r) => {
      if (isTerminalBlockedMode()) {
        _log("poll_blocked_due_to_binding_loss", _logContext({
          sourcePath: "safeFetchStatus_response",
          status: r.status,
        }));
        return { fetch_required: false, _blocked: true };
      }
      // Server clock sync: keep countdown/clock aligned even when we only poll /status.
      try {
        const h = r.headers.get("X-Server-Time-MS");
        if (h) applyServerNowMs(h, "header");
      } catch (e) {}

      if (r.status === 304) {
        // If server provides revision in headers, learn it even on 304.
        try {
          const revH = r.headers.get("X-Schedule-Revision");
          if (revH) {
            const n = parseInt(String(revH), 10);
            if (!isNaN(n) && n >= 0) {
              rt.scheduleRevision = n;
              try {
                localStorage.setItem(revStorageKey(), String(n));
              } catch (e) {}
            }
          }
        } catch (e) {}
        return { _notModified: true };
      }

      if (!r.ok) {
        var raw = "";
        try {
          raw = await r.text();
        } catch (e) {
          raw = "";
        }
        var bodyErr = null;
        try {
          bodyErr = raw ? JSON.parse(raw) : null;
        } catch (e) {
          bodyErr = null;
        }

        var bindingInfo = isBindingConflictResponse(r, bodyErr, raw, "status");
        if (bindingInfo.matched) {
          enterBindingLostState("status_binding_conflict", {
            sourcePath: "status",
            status: r.status,
            classification: bindingInfo.classification,
            code: bindingInfo.code,
            message: bindingInfo.message || raw,
            text: bindingInfo.text,
          });
          return { fetch_required: false, _blocked: true };
        }

        // Non-binding status errors: fall back to snapshot.
        return { fetch_required: true };
      }

      const body = await r.json().catch(() => null);
      if (isTerminalBlockedMode()) {
        _log("poll_blocked_due_to_binding_loss", _logContext({
          sourcePath: "safeFetchStatus_payload",
        }));
        return { fetch_required: false, _blocked: true };
      }

      var okBindingInfo = isBindingConflictResponse(r, body, "", "status_payload");
      if (okBindingInfo.matched) {
        enterBindingLostState("status_payload_binding_conflict", {
          sourcePath: "status_payload",
          status: r.status,
          classification: okBindingInfo.classification,
          code: okBindingInfo.code,
          message: okBindingInfo.message,
          text: okBindingInfo.text,
        });
        return { fetch_required: false, _blocked: true };
      }

      // Prefer header revision if present (authoritative even if body is missing).
      try {
        const revH = r.headers.get("X-Schedule-Revision");
        if (revH) {
          const n = parseInt(String(revH), 10);
          if (!isNaN(n) && n >= 0) {
            rt.scheduleRevision = n;
            try {
              localStorage.setItem(revStorageKey(), String(n));
            } catch (e) {}
          }
        }
      } catch (e) {}

      // Keep local revision in sync if server provides it.
      if (body && typeof body === "object" && typeof body.schedule_revision !== "undefined") {
        const n = parseInt(String(body.schedule_revision), 10);
        if (!isNaN(n) && n >= 0) {
          rt.scheduleRevision = n;
          try {
            localStorage.setItem(revStorageKey(), String(n));
          } catch (e) {}
        }
      }

      return body || { fetch_required: true };
    });

    inflightStatus = withTimeout(fetchPromise, 6000, () => {
      if (ctrlStatus) {
        try {
          ctrlStatus.abort();
        } catch (e) {}
      }
    })
      .catch(() => {
        if (isTerminalBlockedMode()) {
          return { fetch_required: false, _blocked: true };
        }
        // Silent fallback.
        return { fetch_required: true };
      })
      .finally(() => {
        inflightStatus = null;
      });

    return await inflightStatus;
  }

  // ===== Refresh loop =====
  let failStreak = 0;
  let isFetching = false;

  // Page Visibility reliability tracking:
  // Some embedded/TV browsers can misreport `document.hidden=true` even while displayed.
  // If we pause polling based on a false hidden state, the screen may never update.
  const vis = {
    sawChange: false,
    everVisible: false,
  };
  try {
    vis.everVisible = !document.hidden;
    document.addEventListener(
      "visibilitychange",
      () => {
        vis.sawChange = true;
        if (!document.hidden) vis.everVisible = true;
      },
      listenerOpts({ passive: true })
    );
  } catch (e) {}

  function shouldPauseWhenHidden() {
    // Override switches (useful for TVs/embedded browsers that misreport visibility):
    // - ?nopause=1  -> never pause polling
    // - ?pause=1    -> always allow pausing when hidden
    try {
      const qs = new URLSearchParams(window.location.search);
      const noPause = (qs.get("nopause") || "").trim();
      if (noPause === "1" || noPause.toLowerCase() === "true" || noPause.toLowerCase() === "yes") return false;
      const forcePause = (qs.get("pause") || "").trim();
      if (forcePause === "1" || forcePause.toLowerCase() === "true" || forcePause.toLowerCase() === "yes") return true;
    } catch (e) {}

    // Many embedded/TV browsers misreport Page Visibility as hidden even while displayed.
    // In those cases, pausing polling makes the screen appear "خامله" and prevents wake-up.
    const ua = (navigator && navigator.userAgent) ? String(navigator.userAgent) : "";
    const isTvUa = /SmartTV|NetCast|Web0S|webOS|Tizen|HbbTV|Viera|BRAVIA|Roku|MiTV|Android TV|AFTB|AFTS|CrKey/i.test(ua);

    // Extra safety: only pause if we've observed a real visibility transition.
    // If `document.hidden` is stuck true and no events fire, treat it as unreliable.
    try {
      if (document.hidden && !vis.sawChange) return false;
    } catch (e) {}

    return !isTvUa;
  }

  function scheduleNext(sec, forceReason) {
    var mode = rt.mode;

    if (isBlocked && !isTerminalBlockedMode()) return;

    if (isTerminalBlockedMode()) {
      if (forceReason) {
        _log("force_reason_blocked_due_to_binding_loss", _logContext({
          sourcePath: "scheduleNext",
          forceReason: forceReason,
        }));
      }
      _log("poll_blocked_due_to_binding_loss", _logContext({
        sourcePath: "scheduleNext",
        delaySec: Math.max(0.2, Number(sec) || 0),
      }));
      return;
    }

    // --- forceReason allowlist enforcement ---
    if (forceReason && !_isAllowedForceReason(forceReason)) {
      _log("force_reason_rejected", { reason: forceReason, mode: mode });
      forceReason = null; // downgrade to unforced — mode gate will apply
    }
    if (forceReason) {
      _log("force_reason_used", { reason: forceReason, mode: mode });
    }

    // --- Mode gates (bypassed when forceReason is truthy) ---
    if (!forceReason) {
      // sleeping: never poll (wake system handles)
      if (mode === "sleeping" && !!lastPayloadForFiltering) {
        _log("poll_blocked", { reason: "sleeping", mode: mode });
        return;
      }
      // ws-live: redirect to heartbeat, no poll
      if (mode === "ws-live") {
        _log("poll_blocked_in_ws_live", { sec: sec, mode: mode });
        _ensureWsHeartbeat("poll_redirect");
        return;
      }
    }

    var delaySec = Math.max(0.2, sec);
    setNamedTimer("poll", refreshLoop, delaySec * 1000, "poll_" + (forceReason || "loop"));
    _log("poll_scheduled", {
      delaySec: Math.round(delaySec * 10) / 10,
      mode: mode,
      forced: !!forceReason,
      forceReason: forceReason || null,
      wsConnected: rt.wsConnected,
      fallbackLevel: rt.fallbackLevel,
    });
  }

  async function refreshLoop() {
    if (isBlocked && !isTerminalBlockedMode()) return;
    if (isTerminalBlockedMode()) {
      _log("poll_blocked_due_to_binding_loss", _logContext({
        sourcePath: "refreshLoop_preflight",
      }));
      return;
    }
    if (isFetching) return; // Prevent overlapping loops

    // Mode-based gate: sleeping displays do not poll.
    // Wake-up timer or WS invalidate will resume.
    if (rt.mode === "sleeping" && !!lastPayloadForFiltering) {
      _log("poll_skipped", { reason: "sleeping", sleepReason: rt.sleepReason });
      return; // no scheduleNext — wake-up system handles resumption
    }

    // Defense-in-depth: if poll fires in ws-live without a forced fetch pending,
    // redirect to heartbeat and bail — prevents residual timers from polling.
    if (rt.mode === "ws-live" && !rt.forceFetchSnapshot) {
      _log("poll_skipped", { reason: "ws_live_guard", mode: rt.mode });
      _ensureWsHeartbeat("poll_guard");
      return;
    }

    // Never skip the very first snapshot fetch because that can leave the UI blank.
    // Also, only pause when hidden on browsers that reliably support it.
    if (document.hidden && !!lastPayloadForFiltering && shouldPauseWhenHidden()) {
      scheduleNext(basePollEverySec());
      return;
    }

    const invalidateGenAtStart = Number(rt.invalidateGeneration) || 0;
    let consumingForcedRefresh = false;
    let renderedSuccessfully = false;
    isFetching = true;
    let snap = null;

    try {
      // Status-first polling: ask the cheap endpoint first, and only fetch snapshot when needed.
      // Initial load still fetches snapshot directly.
      if (!lastPayloadForFiltering) {
        // Critical: on first load, always fetch a full body.
        // If we send If-None-Match and get 304 while we have no payload in memory,
        // the UI can remain stuck on the loading state.
        snap = await safeFetchSnapshot({ bypassEtag: true });
      } else {
        const inTrans = nowMs() < (Number(rt.transitionUntilTs) || 0);
        if (inTrans) {
          // Cross boundary reliably: revision may not change, so don't rely on /status.
          snap = await safeFetchSnapshot({ bypassEtag: true });
        } else if (rt.forceFetchSnapshot) {
          // WS invalidate (or manual force-refresh) should fetch snapshot even if revision didn't bump.
          consumingForcedRefresh = true;
          rt.forceFetchSnapshot = false;
          rt.status304Streak = 0;
          rt.statusEverySec = basePollEverySec();
          snap = await safeFetchSnapshot({ bypassEtag: true });
        } else {
          const st = await safeFetchStatus();
          if (isTerminalBlockedMode() || (st && st._blocked)) {
            isFetching = false;
            _log("poll_blocked_due_to_binding_loss", _logContext({
              sourcePath: "refreshLoop_after_status",
            }));
            return;
          }
          // Server can request a full reload (dashboard per-screen reload).
          if (st && st.reload === true) {
            isFetching = false;
            try {
              if (isDebug()) {
                ensureDebugOverlay();
                setDebugText("reload requested | " + new Date().toLocaleTimeString());
              }
            } catch (e) {}
            try {
              setTimeout(() => {
                try {
                  window.location.reload();
                } catch (e) {
                  window.location.href = window.location.href;
                }
              }, 200);
            } catch (e) {
              try {
                window.location.reload();
              } catch (e2) {
                window.location.href = window.location.href;
              }
            }
            return;
          }
          if (st && st._notModified) {
            rt.status304Streak = (Number(rt.status304Streak) || 0) + 1;

            // ws-live: 304 in a forced one-shot fetch — skip backoff, return to heartbeat
            if (rt.mode === "ws-live") {
              _log("poll_304_ws_live", { streak: rt.status304Streak });
              isFetching = false;
              failStreak = 0;
              scheduleNext(0); // mode gate → heartbeat
              return;
            }

            // Backoff on 304 to reduce polling load when nothing changes.
            // Tuned for old TV browsers without WebSocket: keep updates near-real-time
            // during the active window so theme/standby/announcements/honor changes
            // appear within ~15-25s instead of being capped at 60s.
            const base = Math.max(2, basePollEverySec());
            const isActiveWin = !!(lastPayloadForFiltering && lastPayloadForFiltering.meta && lastPayloadForFiltering.meta.is_active_window);
            // Lite/TV mode (Tizen/WebOS/SmartTV) gets the tightest interval since WS
            // often fails silently behind school proxies and TV browsers ignore
            // `cache: "no-store"`. Without this, settings changes can take >60s to land.
            const isLite = !!(document.body && document.body.dataset && document.body.dataset.lite === "1");
            const minEvery = isActiveWin ? (isLite ? 12 : 15) : 60;
            const maxEvery = isActiveWin ? (isLite ? 20 : 30) : 300;

            const backoffFactor = isActiveWin ? 1.4 : 2.0;

            const streak = Math.max(1, Number(rt.status304Streak) || 1);
            const pow = Math.pow(backoffFactor, Math.min(10, Math.max(0, streak - 1)));
            const jitter = 1 + (Number(rt.refreshJitterFrac) || 0);
            let nextEvery = (minEvery * pow) * jitter;
            nextEvery = Math.min(maxEvery, Math.max(minEvery, nextEvery));
            // Round to 0.1s to avoid noisy drift.
            rt.statusEverySec = Math.round(nextEvery * 10) / 10;

            maybeLogPollState(isActiveWin, rt.statusEverySec || cfg.REFRESH_EVERY);

            isFetching = false;
            failStreak = 0;
            scheduleNext(rt.statusEverySec || cfg.REFRESH_EVERY);
            if (isDebug()) {
              ensureDebugOverlay();
              setDebugText(
                "status 304 not-modified | streak=" + String(streak) + " | every=" + String(rt.statusEverySec || cfg.REFRESH_EVERY) + "s | " + new Date().toLocaleTimeString()
              );
            }
            return;
          }

          // Default behavior: fetch snapshot unless server says it's not required.
          const need = !st || st.fetch_required !== false;
          if (need) {
            rt.status304Streak = 0;
            rt.statusEverySec = basePollEverySec();
            snap = await safeFetchSnapshot();
          } else {
            rt.status304Streak = 0;
            rt.statusEverySec = basePollEverySec();
            isFetching = false;
            failStreak = 0;
            scheduleNext(rt.statusEverySec || cfg.REFRESH_EVERY);
            if (isDebug()) {
              ensureDebugOverlay();
              setDebugText("status says no-fetch | " + new Date().toLocaleTimeString());
            }
            return;
          }
        }
      }
    } catch (e) {
        // Should be caught inside safeFetchSnapshot but just in case
        snap = null;
    }

    if (isTerminalBlockedMode()) {
      isFetching = false;
      _log("poll_blocked_due_to_binding_loss", _logContext({
        sourcePath: "refreshLoop_post_fetch",
      }));
      return;
    }

    if (snap && snap._rateLimited) {
      // Stronger backoff on 429 to avoid bursts.
      isFetching = false;
      failStreak = 0;
      const inTrans = nowMs() < (Number(rt.transitionUntilTs) || 0);
      let wait;
      if (inTrans) {
        const base = Number(rt.transitionBackoffSec) || 2.0;
        wait = Math.min(10, Math.max(2.0, base * 1.7));
        rt.transitionBackoffSec = wait;
      } else {
        const base = Math.max(2, basePollEverySec());
        wait = Math.min(120, Math.max(15, base * 2));
      }
      if (consumingForcedRefresh) rt.forceFetchSnapshot = true;
      scheduleNext(wait, consumingForcedRefresh ? "ws_invalidate" : null);
      if (isDebug()) {
        ensureDebugOverlay();
        setDebugText("snapshot 429 rate-limited | backoff=" + String(wait) + "s | " + new Date().toLocaleTimeString());
      }
      return;
    }

    if (!snap) {
      isFetching = false;
      failStreak += 1;
      // INITIAL LOAD FAST RETRY WITH EXPONENTIAL BACKOFF:
      // If we haven't successfully loaded data yet, retry with exponential backoff
      // to avoid overwhelming the server. This helps with cold starts while preventing
      // thundering herd when multiple screens fail simultaneously.
      let backoff;
      if (!lastPayloadForFiltering) {
          // Exponential backoff: 2s → 3s → 4.5s → 6.7s → 10s → 15s → 22.5s → 30s (max)
          const maxRetries = 8;
          const retryCount = Math.min(failStreak, maxRetries);
          const baseBackoff = Math.min(30, 2 * Math.pow(1.5, retryCount));
          
          // Add ±25% jitter to distribute load across time
          const jitterFactor = 0.75 + Math.random() * 0.5; // 0.75 to 1.25
          backoff = baseBackoff * jitterFactor;
      } else {
          backoff = Math.min(60, basePollEverySec() + failStreak * 5);
      }
      if (consumingForcedRefresh) rt.forceFetchSnapshot = true;
      scheduleNext(backoff, consumingForcedRefresh ? "ws_invalidate" : null);
      return;
    }

    if (snap && snap._notModified) {
      // Recovery: if a 304 arrives before any successful render, force a full fetch.
      if (!lastPayloadForFiltering) {
        isFetching = false;
        scheduleNext(0.5);
        try {
          snap = await safeFetchSnapshot({ force: true, bypassEtag: true });
        } catch (e) {
          // fall through to retry path below
        }
        if (snap && !snap._notModified) {
          // continue to render below
        } else {
          return;
        }
      }

      isFetching = false;
      failStreak = 0;
      scheduleNext(basePollEverySec());
      if (isDebug()) {
        ensureDebugOverlay();
        setDebugText("304 not-modified | " + new Date().toLocaleTimeString());
      }
      return;
    }

    try {
      failStreak = 0;

      renderState(snap);
      renderAnnouncements(snap.announcements || []);
      renderFeaturedPanel(snap);

      renderStandby(snap.standby || []);
      renderPeriodClasses(pickRuntimePeriodClasses(snap));

      // Phase 2: Initialize WebSocket (feature flag from server)
      try {
        const wsEnabledFromServer = !!(snap && snap.meta && snap.meta.ws_enabled);
        if (wsEnabledFromServer && !rt.wsEnabled) {
          // Feature enabled by server, init WS
          rt.wsEnabled = true;
          _log("ws_feature", { enabled: true });
          initWebSocket();
        } else if (!wsEnabledFromServer && rt.wsEnabled) {
          // Feature disabled by server, close WS
          rt.wsEnabled = false;
          if (rt.ws) {
            _log("ws_feature", { enabled: false });
            try {
              rt.ws.close();
            } catch (e) {}
            rt.ws = null;
          }
          clearNamedTimer("ws_ping");
          clearNamedTimer("ws_reconnect");
          // Transition mode from ws-live to active if needed
          if (rt.mode === "ws-live") {
            setMode("active", "ws_disabled_by_server");
          }
        }
      } catch (e) {
        if (isDebug()) console.warn("[WS] feature flag check error:", e);
      }

      // If we crossed into a new block with a fresh countdown, exit transition window.
      try {
        const sOk = (snap && snap.state) || {};
        const remOk = typeof sOk.remaining_seconds === "number" ? Math.max(0, Math.floor(sOk.remaining_seconds)) : null;
        if (remOk !== null && remOk > 0) {
          rt.transitionUntilTs = 0;
          rt.transitionBackoffSec = 2.0;
        }
      } catch (e) {}

      // After DOM updates (cards/items counts change), re-fit to avoid clipping/scroll.
      scheduleFit(0);

      ensureDebugOverlay();
      if (isDebug()) {
        const pS = periodsScroller ? periodsScroller.getState() : {};
        const sS = standbyScroller ? standbyScroller.getState() : {};
        setDebugText(
          "ok " +
            new Date().toLocaleTimeString() +
            " | idx=" +
            (rt.activePeriodIndex || "-") +
            " dayOver=" +
            (rt.dayOver ? 1 : 0) +
            " | sb=" +
            (Array.isArray(snap.standby) ? snap.standby.length : 0) +
            " pc=" +
            (Array.isArray(snap.period_classes) ? snap.period_classes.length : 0) +
            " | spd(sb)=" +
            cfg.STANDBY_SPEED +
            " spd(pc)=" +
            cfg.PERIODS_SPEED +
            " | run(sb)=" +
            (sS.running ? 1 : 0) +
            " run(pc)=" +
            (pS.running ? 1 : 0)
        );
      }
      renderedSuccessfully = true;
    } catch (e) {
      renderAlert("حدث خطأ أثناء العرض", "افتح ?debug=1 لمزيد من التفاصيل.");
      ensureDebugOverlay();
      if (isDebug()) setDebugText("render error: " + (e && e.message ? e.message : String(e)));
    } finally {
        isFetching = false;
    }

    // A WS invalidation can arrive while a snapshot request is in flight.
    // Preserve and immediately consume that newer event instead of returning
    // to ws-live heartbeat mode and waiting for the sparse safety check.
    const invalidateGenAfterFetch = Number(rt.invalidateGeneration) || 0;
    if (renderedSuccessfully && consumingForcedRefresh && invalidateGenAfterFetch === invalidateGenAtStart) {
      rt.pendingRev = null;
    }
    if (rt.forceFetchSnapshot && invalidateGenAfterFetch > invalidateGenAtStart) {
      _log("ws_invalidate_replayed_after_fetch", {
        startedAtGeneration: invalidateGenAtStart,
        currentGeneration: invalidateGenAfterFetch,
      });
      scheduleNext(0.1, "ws_invalidate");
      return;
    }

    // Mode transition: after first successful render, leave "init"
    if (rt.mode === "init") {
      setMode("active", "first_render_complete");
    }

    {
      const inTrans = nowMs() < (Number(rt.transitionUntilTs) || 0);
      var nextSec = inTrans ? (Number(rt.transitionBackoffSec) || 2.0) : basePollEverySec();
      scheduleNext(nextSec);

      // Improvement #2: escalate fallback level each cycle in fallback-poll mode
      if (rt.mode === "fallback-poll" && !inTrans) {
        advanceFallbackPollLevel();
      }
    }
  }



  // ===========================================================================
  // ===== Unified Sleep/Wake Engine ==========================================
  // ===========================================================================
  // Mode-based sleep/wake driven by validated server meta.
  //
  // Guarantees:
  //  1. Zero HTTP requests while sleeping.
  //  2. WebSocket is closed during sleep and reopened on wake.
  //  3. Scheduled wakes use the authoritative server-provided next_wake_at.
  //  4. WS invalidate wakes immediately only while the screen is awake.
  //  5. First page load always fetches (never sleeps before first payload).
  //  6. If meta validation fails, display stays active (safe default).
  // ===========================================================================

  const SLEEP_CHECK_MS = 15 * 60 * 1000; // coarse local safety re-check every 15 min

  function _parseIsoToMs(iso) {
    if (!iso) return null;
    try {
      var ms = new Date(String(iso)).getTime();
      return isFinite(ms) && ms > 0 ? ms : null;
    } catch (e) {
      return null;
    }
  }

  /**
   * Evaluate whether the display should sleep or wake based on latest snapshot meta.
   * Called after every successful renderState().
   */
  function sleepEvaluate() {
    if (isTerminalBlockedMode()) {
      _log("wake_blocked_due_to_binding_loss", _logContext({
        sourcePath: "sleepEvaluate",
      }));
      return;
    }
    if (!lastPayloadForFiltering) return; // never sleep before first render

    // Validate meta before any decision
    var v = validateScheduleMeta(lastPayloadForFiltering);
    if (!v.valid) {
      // Invalid/missing meta — stay active (safe default)
      if (rt.mode === "sleeping") {
        setMode("waking", "meta_invalid_" + v.reason);
      }
      return;
    }

    var meta = v.meta;
    var state = lastPayloadForFiltering.state || {};

    // Update authoritative server fields
    rt.isSchoolDay = meta.is_school_day !== false;
    rt.isActiveWindow = !!meta.is_active_window;

    var aw = meta.active_window || null;
    if (aw) {
      rt.activeWindowStartMs = _parseIsoToMs(aw.start);
      rt.activeWindowEndMs = _parseIsoToMs(aw.end);
    } else {
      rt.activeWindowStartMs = null;
      rt.activeWindowEndMs = null;
    }
    rt.nextWakeMs = _parseIsoToMs(meta.next_wake_at || null);
    if (!rt.nextWakeMs && rt.activeWindowStartMs && !rt.isActiveWindow) {
      rt.nextWakeMs = rt.activeWindowStartMs;
    }

    // Determine sleep reason
    var stateType = normalizeDisplayStateType(state.type || "");
    var stateReason = getStateReason(state);
    var sleepReason = "";

    if (!rt.isSchoolDay) {
      sleepReason = "holiday";
    } else if (!rt.isActiveWindow && (stateType === "off" || stateReason === "before_hours" || stateReason === "after_hours")) {
      var now = nowMs();
      if (stateReason === "before_hours" || (rt.activeWindowStartMs && now < rt.activeWindowStartMs)) {
        sleepReason = "before_hours";
      } else {
        sleepReason = "after_hours";
      }
    }

    if (sleepReason) {
      sleepEnter(sleepReason);
    } else if (rt.mode === "sleeping") {
      setMode("waking", "active_window_entered");
    }
  }

  // Keep the WebSocket alive during sleep so the server can push `reload` /
  // `snapshot_refresh` events to wake the screen ahead of the school day.
  // Only the client-side keepalive ping and the in-app heartbeat watchdog are
  // paused — the socket stays open and `onmessage` continues to fire.
  function _closeWsForSleep(reason) {
    clearNamedTimer("ws_reconnect");
    clearNamedTimer("sleep_reconnect");
    clearNamedTimer("ws_ping");
    _stopWsHeartbeat("sleep_pause");

    var wsAlive = false;
    try {
      wsAlive = !!(rt.ws && rt.ws.readyState === WebSocket.OPEN);
    } catch (e) {
      wsAlive = false;
    }

    if (wsAlive) {
      _log("ws_sleep_kept_alive", { reason: reason || "sleep_pause" });
      return;
    }

    if (rt.ws) {
      try { rt.ws.onopen = null; } catch (e) {}
      try { rt.ws.onmessage = null; } catch (e) {}
      try { rt.ws.onerror = null; } catch (e) {}
      try { rt.ws.onclose = null; } catch (e) {}
      try { rt.ws.close(1000, safeText(reason || "sleep_pause") || "sleep_pause"); } catch (e) {}
    }

    rt.ws = null;
    rt.wsConnected = false;
    _log("ws_sleep_paused", { reason: reason || "sleep_pause" });
  }

  /**
   * Enter sleep mode — stop polling, schedule wake-up with jitter.
   */
  function sleepEnter(reason) {
    if (isTerminalBlockedMode()) {
      _log("wake_blocked_due_to_binding_loss", _logContext({
        sourcePath: "sleepEnter",
        sleepReason: reason || "",
      }));
      return;
    }
    if (rt.mode === "sleeping" && rt.sleepReason === reason) return; // idempotent

    rt.sleepReason = reason;

    // Transition mode (if not already sleeping)
    if (rt.mode !== "sleeping") {
      // setMode will clear poll timer via _onModeEnter
      setMode("sleeping", reason);
    }

    // Clear previous wake timers
    clearNamedTimer("wake");
    clearNamedTimer("wake_chunk");

    var now = nowMs();
    var wakeMs = Number(rt.nextWakeMs) || null;

    if (wakeMs && wakeMs <= now) {
      setMode("waking", "already_near_active");
      return;
    }

    if (wakeMs && wakeMs > now) {
      // Add per-device jitter to prevent thundering herd
      var delayMs = (wakeMs - now) + _wakeJitterMs();
      _scheduleCappedWake(delayMs);

      _log("sleep_entered", {
        reason: reason,
        wakeInMin: Math.round(delayMs / 60000),
        jitterMs: Math.round(_wakeJitterMs()),
      });
    } else {
      _log("sleep_entered", { reason: reason, wakeTarget: "none" });
    }

    // Start safety check interval
    setNamedInterval("safety_check", sleepSafetyCheck, SLEEP_CHECK_MS, "sleep_safety");
  }

  /**
   * Schedule a wake-up, splitting long delays into 2-hour chunks.
   */
  function _scheduleCappedWake(delayMs) {
    if (isTerminalBlockedMode()) {
      _log("wake_blocked_due_to_binding_loss", _logContext({
        sourcePath: "_scheduleCappedWake",
        delayMs: Math.round(Number(delayMs) || 0),
      }));
      return;
    }
    var MAX_CHUNK = 5 * 60 * 1000; // 5 minutes — short chunks recover fast from TV-side timer drift.

    if (delayMs <= MAX_CHUNK) {
      setNamedTimer("wake", function () {
        if (isTerminalBlockedMode()) {
          _log("wake_blocked_due_to_binding_loss", _logContext({
            sourcePath: "wake_timer",
          }));
          return;
        }
        if (rt.mode === "sleeping") {
          setMode("waking", "timer");
        }
      }, delayMs, "scheduled_wake");
    } else {
      // Schedule intermediate chunk, then re-evaluate
      setNamedTimer("wake_chunk", function () {
        if (isTerminalBlockedMode()) {
          _log("wake_blocked_due_to_binding_loss", _logContext({
            sourcePath: "wake_chunk_timer",
          }));
          return;
        }
        if (rt.mode !== "sleeping") return;
        sleepSafetyCheck();
      }, MAX_CHUNK, "wake_chunk_reeval");
    }
  }

  /**
   * Safety check — runs every 15 min during sleep.
   * Guards against setTimeout drift or tab freeze.
   */
  function sleepSafetyCheck() {
    if (isTerminalBlockedMode()) {
      _log("wake_blocked_due_to_binding_loss", _logContext({
        sourcePath: "sleepSafetyCheck",
      }));
      return;
    }
    if (rt.mode !== "sleeping") return;

    // Old TV browsers (e.g. Samsung Tizen 3.x) can silently kill setInterval callbacks
    // during TV standby. Restart the safety-check interval here if it was lost so the
    // next call from visibilitychange / focus / pageshow re-establishes the heartbeat.
    if (!_timers["safety_check"]) {
      setNamedInterval("safety_check", sleepSafetyCheck, SLEEP_CHECK_MS, "sleep_safety");
    }

    var now = nowMs();
    var wakeTarget = Number(rt.nextWakeMs) || null;

    if (wakeTarget) {
      if (now >= wakeTarget) {
        setMode("waking", "safety_check_near_active");
        return;
      }
      // Reschedule precise timer for remaining time
      var remaining = wakeTarget - now + _wakeJitterMs();
      clearNamedTimer("wake");
      clearNamedTimer("wake_chunk");
      _scheduleCappedWake(remaining);
    }

    _log("safety_check_passed", { sleepReason: rt.sleepReason });
  }

  /**
   * Calculate next midnight in server-synchronized time.
   */
  function _nextMidnightMs(fromMs) {
    var d = new Date(fromMs);
    if (serverTzOffsetMin !== null) {
      var utcMs = fromMs;
      var serverLocalMs = utcMs + (serverTzOffsetMin * 60 * 1000);
      var serverLocal = new Date(serverLocalMs);
      var nextDay = new Date(Date.UTC(
        serverLocal.getUTCFullYear(),
        serverLocal.getUTCMonth(),
        serverLocal.getUTCDate() + 1,
        0, 0, 0, 0
      ));
      return nextDay.getTime() - (serverTzOffsetMin * 60 * 1000);
    }
    var nextLocal = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1, 0, 0, 0, 0);
    return nextLocal.getTime();
  }


  // ===== WebSocket: Realtime Push Invalidate =====
  
  function getDeviceId() {
    return getOrCreateDeviceId();
  }

  /**
   * Mode-aware polling interval calculator.
   * Returns seconds until next poll based on current mode.
   */
  function basePollEverySec() {
    if (isTerminalBlockedMode()) return 86400;
    // Sleeping: no poll (wake system handles resumption)
    if (rt.mode === "sleeping") return 86400;

    try {
      var wsOpen = !!(rt.ws && rt.ws.readyState === WebSocket.OPEN);
      if (wsOpen) {
        // Off-hours with WS: 30-min heartbeat
        if (!rt.isActiveWindow) return 1800;
        // ws-live with dayEngine: 5-min heartbeat
        if (_dayEngine.blocks.length > 0) return 300;
        return Number(cfg.WS_FALLBACK_POLL_EVERY) || 90;
      }
    } catch (e) {}

    // Adaptive fallback polling (improvement #2)
    if (rt.mode === "fallback-poll") {
      // Off-hours in fallback: slow heartbeat regardless of level
      if (!rt.isActiveWindow) return 1800;
      return getFallbackPollDelay();
    }

    // active mode without WS
    if (!rt.isActiveWindow) return 1800;
    return Number(cfg.REFRESH_EVERY) || 20;
  }

  function _wsReconnectDelaySec(attempt, closeCode) {
    var n = Math.max(1, parseInt(attempt, 10) || 1);
    var code = parseInt(closeCode, 10) || 0;
    var exp = Math.min(4, Math.max(0, n - 1));
    var baseDelay;
    var jitterMin = 0.75;
    var jitterMax = 1.35;

    // Restart / overload close codes should reconnect more gently to avoid
    // a fleet-wide rush immediately after deploy or worker restart.
    if (code === 1012 || code === 1013) {
      baseDelay = Math.min(90, 5 * Math.pow(2, exp));
      jitterMin = 1.0;
      jitterMax = 2.0;
    } else if (code === 1001) {
      baseDelay = Math.min(45, 3 * Math.pow(1.8, exp));
      jitterMin = 0.9;
      jitterMax = 1.8;
    } else if (code === 1006 || code === 0) {
      // Abnormal closures are common on weak TV networks; retry gently to avoid loops.
      baseDelay = Math.min(45, 4 * Math.pow(1.8, exp));
      jitterMin = 1.0;
      jitterMax = 1.9;
    } else {
      baseDelay = Math.min(60, Math.pow(2, Math.min(5, n - 1)));
    }

    return Math.max(1, baseDelay * (jitterMin + (Math.random() * (jitterMax - jitterMin))));
  }

  function _browserOffline() {
    try {
      return typeof navigator !== "undefined" && navigator.onLine === false;
    } catch (e) {
      return false;
    }
  }

  const WS_MAX_MESSAGE_CHARS = 12000;
  const WS_MAX_MESSAGES_PER_WINDOW = 24;
  const WS_MESSAGE_WINDOW_MS = 5000;
  const _wsInboundTimestamps = [];

  function allowInboundWsMessage(rawLen) {
    var now = Date.now();
    while (_wsInboundTimestamps.length > 0 && now - _wsInboundTimestamps[0] > WS_MESSAGE_WINDOW_MS) {
      _wsInboundTimestamps.shift();
    }
    if (_wsInboundTimestamps.length >= WS_MAX_MESSAGES_PER_WINDOW) {
      _log("ws_message_rate_limited", {
        rawLen: Number(rawLen) || 0,
        count: _wsInboundTimestamps.length,
        windowMs: WS_MESSAGE_WINDOW_MS,
      });
      return false;
    }
    _wsInboundTimestamps.push(now);
    return true;
  }

  function initWebSocket() {
    if (isTerminalBlockedMode()) {
      _log("reconnect_blocked_due_to_binding_loss", _logContext({
        sourcePath: "initWebSocket",
      }));
      return;
    }
    if (rt.mode === "sleeping") {
      _log("ws_connect_skipped", { reason: "sleeping" });
      return;
    }
    if (!rt.wsEnabled) {
      _log("ws_disabled", { reason: "feature_flag" });
      return;
    }

    if (_browserOffline()) {
      _log("ws_offline_skip", { retryCount: rt.wsRetryCount, mode: rt.mode });
      if (rt.mode === "ws-live") {
        setMode("fallback-poll", "browser_offline");
      }
      setNamedTimer("ws_reconnect", function () {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_offline_retry",
          }));
          return;
        }
        initWebSocket();
      }, 15000, "ws_offline_retry");
      return;
    }

    // If WS repeatedly fails before any successful open, cool down
    var nowTs = Date.now();
    if (rt.wsSuppressedUntilTs && nowTs < rt.wsSuppressedUntilTs) {
      var waitMs = Math.max(1000, rt.wsSuppressedUntilTs - nowTs);
      _log("ws_suppressed", { waitSec: Math.round(waitMs / 1000) });
      setNamedTimer("ws_reconnect", function () {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_suppressed_retry",
          }));
          return;
        }
        initWebSocket();
      }, waitMs, "ws_suppressed_retry");
      return;
    }
    
    // After many failures, slow retry
    if (rt.wsRetryCount >= rt.wsMaxRetries) {
      var longDelayMs = 120 * 1000;
      _log("ws_max_retries", { delay: 120 });
      setNamedTimer("ws_reconnect", function () {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_slow_retry",
          }));
          return;
        }
        initWebSocket();
      }, longDelayMs, "ws_slow_retry");
      return;
    }
    
    // Close existing connection
    if (rt.ws) {
      try { rt.ws.close(); } catch (e) {}
      rt.ws = null;
    }
    
    clearNamedTimer("ws_reconnect");
    
    var token = getToken();
    var deviceId = getDeviceId();
    
    if (!token || !deviceId) {
      _log("ws_no_credentials", {});
      return;
    }
    
    var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    var host = window.location.host;
    var wsUrl = proto + "//" + host + "/ws/display/?token=" + encodeURIComponent(token) + "&dk=" + encodeURIComponent(deviceId);
    
    try {
      if (rt.wsRetryCount > 0) {
        _log("ws_reconnect_attempt", {
          attempt: rt.wsRetryCount,
          mode: rt.mode,
        });
      }
      _log("ws_connecting", { url: wsUrl.substring(0, 80) });
      
      rt.ws = new WebSocket(wsUrl);
      
      rt.ws.onopen = function() {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_onopen",
          }));
          try { rt.ws.close(4403, "binding_lost"); } catch (e) {}
          return;
        }
        rt.wsConnected = true;
        rt.wsRetryCount = 0;
        rt.wsEverConnected = true;
        rt.wsSuppressedUntilTs = 0;
        rt.wsOpenedAt = Date.now(); // cooldown reference for fetch dedup
        rt.wsLastMessageAt = rt.wsOpenedAt;

        _log("ws_connected", { mode: rt.mode, openedAt: rt.wsOpenedAt });
        _log("ws_reconnect_succeeded", { mode: rt.mode });

        // Cancel any pending sleep reconnect timer (improvement #1)
        clearNamedTimer("sleep_reconnect");

        // Reset adaptive fallback (improvement #2)
        resetFallbackPollLevel("ws_connected");

        // Clear polling state — heartbeat takes over in ws-live
        rt.status304Streak = 0;

        // Transition mode to ws-live if currently active/fallback-poll
        // _onModeEnter("ws-live") will clear "poll" timer and start heartbeat
        // _startWsHeartbeat increments _heartbeatGen → stale callbacks from
        // the previous heartbeat cycle will see gen mismatch and bail.
        if (rt.mode === "active" || rt.mode === "fallback-poll") {
          setMode("ws-live", "ws_connected");
        }
        
        _log("ws_connected_poll_cancelled", {
          mode: rt.mode,
          pollTimerActive: !!_timers["poll"],
          heartbeatActive: !!_timers["ws_heartbeat"],
          heartbeatGen: _heartbeatGen,
        });
        
        // Start keepalive ping (every 30s) via named interval
        setNamedInterval("ws_ping", function () {
          if (isTerminalBlockedMode()) {
            _log("heartbeat_blocked_due_to_binding_loss", _logContext({
              sourcePath: "ws_ping_interval",
            }));
            clearNamedTimer("ws_ping");
            return;
          }
          if (rt.ws && rt.ws.readyState === WebSocket.OPEN) {
            try {
              rt.ws.send(JSON.stringify({ type: "ping" }));
            } catch (e) {
              _log("ws_ping_failed", { error: String(e) });
            }
          }
        }, 20000, "ws_keepalive");
      };
      
      rt.ws.onmessage = function(event) {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_onmessage_ignored",
          }));
          return;
        }
        try {
          var raw = typeof event.data === "string" ? event.data : "";
          if (!raw) {
            _log("ws_message_ignored", { reason: "non_string_payload" });
            return;
          }
          if (raw.length > WS_MAX_MESSAGE_CHARS) {
            _log("ws_message_ignored", { reason: "payload_too_large", rawLen: raw.length });
            return;
          }
          if (!allowInboundWsMessage(raw.length)) return;

          var msg = JSON.parse(raw);
          if (!msg || typeof msg !== "object") return;
          _markWsHeartbeatSeen(safeText(msg.type || "message"));
          
          if (msg.type === "pong" || msg.type === "heartbeat") {
            return;
          }

          if (msg.type === "ping") {
            try {
              if (rt.ws && rt.ws.readyState === WebSocket.OPEN) {
                rt.ws.send(JSON.stringify({ type: "pong" }));
              }
            } catch (e) {}
            return;
          }

          var wsBinding = isBindingConflictWsMessage(msg);
          if (wsBinding.matched) {
            enterBindingLostState("ws_binding_conflict", {
              sourcePath: "ws_message",
              status: null,
              classification: wsBinding.classification,
              code: wsBinding.code,
              message: wsBinding.message || wsBinding.text,
              text: wsBinding.text,
            });
            return;
          }

          if (msg.type === "reload") {
            _log("ws_reload", {});
            setNamedTimer("ws_reload", function () {
              try { window.location.reload(); } catch (e) { window.location.href = window.location.href; }
            }, 200, "ws_reload_delay");
            return;
          }
          
          if (msg.type === "snapshot_refresh" || msg.type === "invalidate") {
            var newRev = parseInt(msg.revision, 10);
            if (isNaN(newRev)) return;
            
            _log("ws_revision_received", { revision: newRev, mode: rt.mode });
            _log("ws_snapshot_refresh", { revision: newRev, eventType: msg.type, reason: safeText(msg.reason || "") });
            
            rt.invalidateGeneration = (Number(rt.invalidateGeneration) || 0) + 1;
            rt.pendingRev = newRev;
            rt.forceFetchSnapshot = true;

            // Reset adaptive fallback on valid WS data (improvement #2)
            resetFallbackPollLevel("ws_invalidate");

            // Wake from sleep immediately — NO jitter for WS invalidate wake
            if (rt.mode === "sleeping") {
              setMode("waking", "ws_invalidate");
              // waking→active/ws-live already calls scheduleNext(0.3)
              // No snapshot jitter needed — wake path handles it
            } else if (!isFetching) {
              // Improvement #3: jittered snapshot fetch to prevent storm
              scheduleWsSnapshotRefresh(newRev);
            } else {
              _log("ws_invalidate_deferred", { reason: "fetch_in_progress" });
            }
            return;
          }

          if (msg.type === "patch") {
            _markWsHeartbeatSeen("patch");
            _log("ws_patch", { revision: msg.revision || null });
            if (msg.revision !== undefined && msg.revision !== null) {
              var patchRev = parseInt(msg.revision, 10);
              if (!isNaN(patchRev) && patchRev > Number(rt.scheduleRevision || 0)) {
                rt.pendingRev = patchRev;
                rt.forceFetchSnapshot = true;
                scheduleWsSnapshotRefresh(patchRev);
              }
            }
          }
        } catch (e) {
          _log("ws_message_error", { error: String(e) });
        }
      };
      
      rt.ws.onerror = function(err) {
        _log("ws_error", {});
      };
      
      rt.ws.onclose = function(event) {
        if (isTerminalBlockedMode()) {
          rt.wsConnected = false;
          clearNamedTimer("ws_ping");
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_onclose_terminal",
            code: event && event.code ? event.code : 0,
          }));
          return;
        }
        rt.wsConnected = false;
        rt.ws = null;
        clearNamedTimer("ws_ping");
        _stopWsHeartbeat("ws_closed"); // stop heartbeat — mode transition starts poll
        
        var code = event.code;
        var reason = event.reason || "";
        
        _log("ws_disconnected", { code: code, reason: reason, mode: rt.mode, attempt: rt.wsRetryCount + 1 });
        _log("ws_closed", { code: code, reason: reason, mode: rt.mode });

        var wsCloseBinding = isBindingConflictWsClose(event);
        if (wsCloseBinding.matched) {
          enterBindingLostState("ws_close_binding_conflict", {
            sourcePath: "ws_close",
            status: code || null,
            classification: wsCloseBinding.classification,
            code: wsCloseBinding.code,
            message: wsCloseBinding.message || wsCloseBinding.text,
            text: wsCloseBinding.text,
          });
          return;
        }

        // Transition mode: if was ws-live, degrade to adaptive fallback
        if (rt.mode === "ws-live") {
          setMode("fallback-poll", "ws_closed_code_" + code);
        }
        // Advance fallback escalation on each WS close (improvement #2)
        advanceFallbackPollLevel();
        
        // Don't reconnect on auth failures
        if (code === 4400 || code === 4403 || code === 4408) {
          _log("ws_auth_failure", { code: code });
          rt.wsEnabled = false;
          rt.status304Streak = 0;
          // Ensure we're in a polling mode (not ws-live with broken auth)
          if (rt.mode === "ws-live") {
            setMode("fallback-poll", "ws_auth_failure");
          }
          if (!_timers["poll"]) {
            scheduleNext(getFallbackPollDelay());
          }
          return;
        }
        
        rt.wsRetryCount++;

        // Suppress if endpoint seems unavailable
        if (!rt.wsEverConnected && rt.wsRetryCount >= 3) {
          rt.wsSuppressedUntilTs = Date.now() + (10 * 60 * 1000);
          _log("ws_suppressed_no_connection", { durationMin: 10 });
          return;
        }

        var delay = _wsReconnectDelaySec(rt.wsRetryCount, code);
        
        if (rt.wsRetryCount >= rt.wsMaxRetries) {
          var longDelaySec = 120;
          _log("ws_max_retries_reconnect", { delay: longDelaySec });
          setNamedTimer("ws_reconnect", function () {
            if (isTerminalBlockedMode()) {
              _log("reconnect_blocked_due_to_binding_loss", _logContext({
                sourcePath: "ws_max_retries_reconnect_timer",
              }));
              return;
            }
            initWebSocket();
          }, longDelaySec * 1000, "ws_slow_retry");
          return;
        }

        _log("ws_reconnecting", { delaySec: delay.toFixed(1), attempt: rt.wsRetryCount });
        setNamedTimer("ws_reconnect", function () {
          if (isTerminalBlockedMode()) {
            _log("reconnect_blocked_due_to_binding_loss", _logContext({
              sourcePath: "ws_retry_timer",
            }));
            return;
          }
          initWebSocket();
        }, delay * 1000, "ws_retry");
      };
      
    } catch (e) {
      if (isTerminalBlockedMode()) {
        _log("reconnect_blocked_due_to_binding_loss", _logContext({
          sourcePath: "ws_init_error_terminal",
        }));
        return;
      }
      _log("ws_init_error", { error: String(e) });
      rt.wsRetryCount++;
      
      var retryDelay = (rt.wsRetryCount < rt.wsMaxRetries)
        ? _wsReconnectDelaySec(rt.wsRetryCount, 0)
        : 120;
      setNamedTimer("ws_reconnect", function () {
        if (isTerminalBlockedMode()) {
          _log("reconnect_blocked_due_to_binding_loss", _logContext({
            sourcePath: "ws_error_retry_timer",
          }));
          return;
        }
        initWebSocket();
      }, retryDelay * 1000, "ws_error_retry");
    }
  }



  // ===== Resize: فقط إعادة القياس =====
  window.addEventListener(
    "resize",
    function () {
      setNamedTimer("resize", function () {
        setVhVar();
        applyAutoFit();
        if (periodsScroller) periodsScroller.recalc();
        if (standbyScroller) standbyScroller.recalc();
        if (dutyScroller) dutyScroller.recalc();
      }, 160, "resize_debounce");
    },
    listenerOpts({ passive: true })
  );

  window.addEventListener(
    "orientationchange",
    () => {
      scheduleFit(120);
    },
    listenerOpts({ passive: true })
  );

  window.addEventListener(
    "pageshow",
    () => {
      scheduleFit(0);
      // Old TV browsers fire pageshow when the TV resumes from standby (BF-cache restore).
      // visibilitychange may not fire on old Tizen/WebOS, so check sleep state here too.
      if (rt.mode === "sleeping") {
        try { sleepSafetyCheck(); } catch (e) {}
      }
    },
    listenerOpts({ passive: true })
  );

  document.addEventListener("fullscreenchange", () => scheduleFit(0), listenerOpts());
  document.addEventListener("webkitfullscreenchange", () => scheduleFit(0), listenerOpts());

  try {
    if (window.visualViewport) {
      window.visualViewport.addEventListener(
        "resize",
        () => {
          scheduleFit(80);
        },
        listenerOpts({ passive: true })
      );
      window.visualViewport.addEventListener(
        "scroll",
        () => {
          scheduleFit(80);
        },
        listenerOpts({ passive: true })
      );
    }
  } catch (e) {}

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      if (isTerminalBlockedMode()) {
        if (Date.now() >= (Number(rt.wsSuppressedUntilTs) || 0)) {
          try {
            window.location.reload();
          } catch (e) {
            window.location.href = window.location.href;
          }
          return;
        }
        _log("visibility_resume_blocked_due_to_binding_loss", _logContext({
          sourcePath: "visibilitychange",
        }));
        return;
      }
      detectClockDrift();
      requestReSyncIfNeeded();
      
      if (periodsScroller) periodsScroller.recalc();
      if (standbyScroller) standbyScroller.recalc();
      if (dutyScroller) dutyScroller.recalc();
      scheduleFit(0);

      // Mode-based: sleeping → safety check, ws-live → heartbeat only, else → poll
      if (rt.mode === "sleeping") {
        try { sleepSafetyCheck(); } catch (e) {}
      } else if (rt.mode === "ws-live") {
        // ws-live: heartbeat handles freshness, no poll needed
        _ensureWsHeartbeat("visibility_resume");
        _log("visibility_resume_handled", { mode: "ws-live", action: "heartbeat_ensured" });
      } else {
        scheduleNext(0.25);
      }
    }
  }, listenerOpts());

  // ✅ CLOCK SYNC: عند focus على النافذة، نتحقق من التزامن
  // ⚠️ THROTTLED: محمي بـ cooldown لمنع الطلبات الزائدة
  window.addEventListener("focus", () => {
    if (isTerminalBlockedMode()) {
      if (Date.now() >= (Number(rt.wsSuppressedUntilTs) || 0)) {
        try {
          window.location.reload();
        } catch (e) {
          window.location.href = window.location.href;
        }
      }
      return;
    }
    detectClockDrift();
    requestReSyncIfNeeded(); // throttled request (skipped if sleeping or WS connected)
    // Old TV browsers (Tizen/WebOS) fire focus when the TV screen comes back on, but may
    // not fire visibilitychange. Check sleep state here so the screen wakes on TV resume.
    if (rt.mode === "sleeping") {
      try { sleepSafetyCheck(); } catch (e) {}
    }
  }, listenerOpts({ passive: true }));

  window.addEventListener("online", function () {
    if (isTerminalBlockedMode()) {
      if (Date.now() >= (Number(rt.wsSuppressedUntilTs) || 0)) {
        try {
          window.location.reload();
        } catch (e) {
          window.location.href = window.location.href;
        }
      }
      return;
    }
    _log("browser_online", { mode: rt.mode, wsEnabled: rt.wsEnabled, wsConnected: rt.wsConnected });
    if (rt.mode === "sleeping") {
      try { sleepSafetyCheck(); } catch (e) {}
      return;
    }
    if (rt.wsEnabled && !rt.wsConnected) {
      clearNamedTimer("ws_reconnect");
      initWebSocket();
    }
    if (rt.mode !== "ws-live") {
      scheduleNext(0.25);
    }
  }, listenerOpts({ passive: true }));

  window.addEventListener("offline", function () {
    _log("browser_offline", { mode: rt.mode, wsConnected: rt.wsConnected });
    if (rt.mode === "ws-live") {
      setMode("fallback-poll", "browser_offline");
    }
  }, listenerOpts({ passive: true }));

  // ===== Global errors =====
  window.addEventListener("error", (ev) => {
    ensureDebugOverlay();
    if (isDebug()) setDebugText("window error: " + safeText(ev && ev.message));
  }, listenerOpts());
  window.addEventListener("unhandledrejection", (ev) => {
    ensureDebugOverlay();
    if (isDebug())
      setDebugText(
        "promise rej: " + safeText(ev && ev.reason && ev.reason.message ? ev.reason.message : ev.reason)
      );
  }, listenerOpts());

  // ===========================================================================
  // ===== TV Anti-Throttle: Wake Lock + Silent Audio Loop =====================
  // ===========================================================================
  // Keeps the TV browser process and screen alive so the scheduled wake fires.
  //
  //  - Screen Wake Lock API (Chromium 84+, Tizen 6+, WebOS 6+) prevents the
  //    panel from entering deep sleep that suspends the JS runtime.
  //  - A muted, silent looping audio track discourages background-tab
  //    throttling on browsers that aggressively pause inactive timers.
  // Both features are best-effort; failures are silently ignored on TVs that
  // do not support them, since the server-side wake broadcaster remains the
  // primary recovery path.
  // ===========================================================================

  var _wakeLockSentinel = null;
  var _wakeLockReacquireTimer = null;
  var _wakeLockRequestPending = false;
  var _wakeLockKeepaliveStopped = false;

  function _scheduleWakeLockRetry(delayMs) {
    if (_wakeLockKeepaliveStopped || document.visibilityState === "hidden") return;
    if (_wakeLockReacquireTimer) clearTimeout(_wakeLockReacquireTimer);
    _wakeLockReacquireTimer = setTimeout(function () {
      _wakeLockReacquireTimer = null;
      _requestScreenWakeLock();
    }, Math.max(1000, Number(delayMs) || 1000));
  }

  function _requestScreenWakeLock() {
    try {
      if (!("wakeLock" in navigator) || !navigator.wakeLock) return;
      if (document.visibilityState === "hidden") return;
      if (_wakeLockKeepaliveStopped || _wakeLockSentinel || _wakeLockRequestPending) return;
      _wakeLockRequestPending = true;
      navigator.wakeLock.request("screen").then(function (sentinel) {
        _wakeLockRequestPending = false;
        if (_wakeLockKeepaliveStopped) {
          try { sentinel.release(); } catch (e) {}
          return;
        }
        _wakeLockSentinel = sentinel;
        try {
          sentinel.addEventListener("release", function () {
            if (_wakeLockSentinel === sentinel) _wakeLockSentinel = null;
            _log("wake_lock_released", {});
            _scheduleWakeLockRetry(1000);
          });
        } catch (e) {}
        _log("wake_lock_acquired", {});
      }).catch(function (err) {
        _wakeLockRequestPending = false;
        _log("wake_lock_failed", { error: String(err && err.message || err) });
        // Permission and transient browser failures may clear after focus or a
        // user interaction. Retry gently without creating a busy loop.
        _scheduleWakeLockRetry(30000);
      });
    } catch (e) {
      _wakeLockRequestPending = false;
    }
  }

  function _initWakeLockKeepalive() {
    _wakeLockKeepaliveStopped = false;
    _requestScreenWakeLock();
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        if (_wakeLockReacquireTimer) clearTimeout(_wakeLockReacquireTimer);
        _wakeLockReacquireTimer = null;
      } else if (!_wakeLockSentinel) {
        _requestScreenWakeLock();
      }
    }, listenerOpts({ passive: true }));
  }

  function _stopWakeLockKeepalive() {
    _wakeLockKeepaliveStopped = true;
    if (_wakeLockReacquireTimer) clearTimeout(_wakeLockReacquireTimer);
    _wakeLockReacquireTimer = null;
    var sentinel = _wakeLockSentinel;
    _wakeLockSentinel = null;
    if (sentinel && typeof sentinel.release === "function") {
      try { sentinel.release(); } catch (e) {}
    }
  }

  var _silentAudioStarted = false;
  function _initSilentAudioAntiThrottle() {
    if (_silentAudioStarted) return;
    try {
      var AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;

      var unlock = function () {
        if (_silentAudioStarted) return;
        try {
          var ctx = new AudioCtx();
          var osc = ctx.createOscillator();
          var gain = ctx.createGain();
          gain.gain.value = 0.0001; // effectively silent
          osc.frequency.value = 1;
          osc.connect(gain).connect(ctx.destination);
          osc.start();
          if (ctx.resume) { try { ctx.resume(); } catch (e) {} }
          _silentAudioStarted = true;
          _log("silent_audio_started", {});
        } catch (e) {
          _log("silent_audio_failed", { error: String(e && e.message || e) });
        }
        try {
          window.removeEventListener("click", unlock, true);
          window.removeEventListener("touchstart", unlock, true);
          window.removeEventListener("keydown", unlock, true);
        } catch (e) {}
      };

      // Try immediately; many TV browsers do not require a user gesture for
      // muted audio. Otherwise, attach one-shot unlockers for the first input.
      unlock();
      if (!_silentAudioStarted) {
        window.addEventListener("click", unlock, true);
        window.addEventListener("touchstart", unlock, true);
        window.addEventListener("keydown", unlock, true);
      }
    } catch (e) {}
  }

  // ===== Boot =====
  document.addEventListener("DOMContentLoaded", () => {
    bindDom();
    setVhVar();
    scheduleFit(0);

    // TVs often load web fonts late; re-fit when font metrics are final.
    try {
      if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === "function") {
        document.fonts.ready.then(() => scheduleFit(0)).catch(() => {});
      }
    } catch (e) {}

    // Also re-fit after full load (images/layout settle)
    try {
      window.addEventListener(
        "load",
        () => {
          scheduleFit(0);
        },
        listenerOpts({ passive: true, once: true })
      );
    } catch (e) {}

    const body = document.body || document.documentElement;

    const lite = isLiteMode();
    try {
      body.dataset.lite = lite ? "1" : "0";
    } catch (e) {}

    try {
      if (!supportsFlexGap()) body.dataset.nogap = "1";
    } catch (e) {}

    cfg.REFRESH_EVERY = clamp(parseFloat(body.dataset.refresh || "30") || 30, 5, 120);
    cfg.WS_FALLBACK_POLL_EVERY = clamp(parseFloat(body.dataset.wsFallbackPoll || "180") || 180, 30, 600);
    cfg.WS_LIVE_STATUS_CHECK_SEC = clamp(
      parseFloat(body.dataset.wsLiveStatusCheck || "45") || 45,
      15,
      300
    );
    cfg.STANDBY_SPEED = normSpeed(body.dataset.standby || "0.8", 0.8);
    cfg.PERIODS_SPEED = normSpeed(body.dataset.periodsSpeed || "0.5", 0.5);

    cfg.MEDIA_PREFIX = (body.dataset.mediaPrefix || "/media/").toString().trim();
    cfg.BELL_SOUND_URL = (body.dataset.bellSoundUrl || "/static/sounds/bell.mp3").toString().trim();
    cfg.SNAPSHOT_URL = (body.dataset.snapshotUrl || "").toString().trim();
    cfg.SERVER_TOKEN = (body.dataset.apiToken || body.dataset.token || "").toString().trim();
    cfg.SCHOOL_TYPE = (body.dataset.schoolType || "").toString().trim();

    // Warm the bell file during page startup so the first scheduled ring does
    // not pay a network/decode delay. This does not attempt autoplay.
    try {
      var preloadedBell = ensureBellAudio();
      if (preloadedBell && typeof preloadedBell.load === "function") preloadedBell.load();
    } catch (e) {}

    try {
      const initTheme = (body.dataset.theme || "").trim();
      if (initTheme) applyTheme(initTheme);
    } catch (e) {}

    try {
      const initAccent = (body.dataset.accentColor || "").trim();
      if (initAccent) applyAccentColor(initAccent);
    } catch (e) {}

    ensureDebugOverlay();
    tickClock();
    startTicker();
    try { _initWakeLockKeepalive(); } catch (e) {}
    try { _initSilentAudioAntiThrottle(); } catch (e) {}

    // init scrollers (مستقلين)
    // For low-end TVs: cap FPS to reduce paint cost.
    const scrollerOpts = lite ? { maxFps: 20 } : undefined;
    periodsScroller = dom.periodClassesTrack ? createScroller(dom.periodClassesTrack, () => cfg.PERIODS_SPEED, scrollerOpts) : null;
    standbyScroller = dom.standbyTrack ? createScroller(dom.standbyTrack, () => cfg.STANDBY_SPEED, scrollerOpts) : null;
    dutyScroller = dom.dutyTrack ? createScroller(dom.dutyTrack, () => cfg.PERIODS_SPEED, scrollerOpts) : null;

    renderAlert("جاري التحميل…", "يتم الآن جلب البيانات من الخادم.");
    
    // Push-first bootstrap: the first HTTP request is the full snapshot. After
    // that, WS drives freshness and /status is reserved for WS outage fallback.
    scheduleNext(0.2);
  }, listenerOpts({ once: true }));

  try {
    // pagehide is the reliable teardown signal on TV browsers, where
    // beforeunload is often skipped entirely.
    window.addEventListener("pagehide", () => {
      sendGoodbyeBeacon("pagehide");
      cleanupPageRuntime("pagehide");
    }, listenerOpts({ once: true }));
  } catch (e) {}

  try {
    window.addEventListener("beforeunload", () => {
      sendGoodbyeBeacon("beforeunload");
      cleanupPageRuntime("beforeunload");
    }, listenerOpts({ once: true }));
  } catch (e) {}
})();
