(function () {
  "use strict";

  var body = document.body;
  var startUrl = body.getAttribute("data-pairing-start-url") || "/tv/pair/start/";
  var csrfInput = document.querySelector("#csrfForm input[name='csrfmiddlewaretoken']");
  var csrfToken = csrfInput ? csrfInput.value : "";
  var codeEl = document.getElementById("pairingCode");
  var timerEl = document.getElementById("pairingTimer");
  var statusEl = document.getElementById("pairingStatus");
  var statusText = statusEl ? statusEl.querySelector("span") : null;
  var qrEl = document.getElementById("pairingQr");
  var placeholderEl = document.getElementById("qrPlaceholder");
  var successEl = document.getElementById("pairingSuccess");
  var retryButton = document.getElementById("retryButton");
  var pollTimer = null;
  var countdownTimer = null;
  var deadline = 0;
  var currentSession = null;
  var DEVICE_KEY = "school_display_pairing_device_id";

  function randomDeviceId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
      if (window.crypto && typeof window.crypto.getRandomValues === "function") {
        var bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        var hex = "";
        for (var i = 0; i < bytes.length; i += 1) {
          hex += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
        }
        return "tv-" + hex;
      }
    } catch (_error) {}
    return "tv-" + String(Math.random()).slice(2) + "-" + String(Date.now());
  }

  function getDeviceId() {
    try {
      var existing = (localStorage.getItem(DEVICE_KEY) || "").trim();
      if (existing) return existing;
      var created = randomDeviceId();
      localStorage.setItem(DEVICE_KEY, created);
      return created;
    } catch (_error) {
      return randomDeviceId();
    }
  }

  function postForm(url, values, done) {
    var xhr = new XMLHttpRequest();
    var parts = ["csrfmiddlewaretoken=" + encodeURIComponent(csrfToken)];
    var key;
    for (key in values) {
      if (Object.prototype.hasOwnProperty.call(values, key)) {
        parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(values[key]));
      }
    }
    xhr.open("POST", url, true);
    xhr.setRequestHeader("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8");
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.onreadystatechange = function () {
      if (xhr.readyState !== 4) return;
      var data = {};
      try { data = JSON.parse(xhr.responseText || "{}"); } catch (_error) {}
      done(xhr.status, data);
    };
    xhr.onerror = function () { done(0, {}); };
    xhr.send(parts.join("&"));
  }

  function setStatus(message, kind) {
    if (statusText) statusText.textContent = message;
    if (!statusEl) return;
    statusEl.className = "tv-pairing-status";
    if (kind) statusEl.className += " is-" + kind;
  }

  function stopTimers() {
    if (pollTimer) window.clearTimeout(pollTimer);
    if (countdownTimer) window.clearInterval(countdownTimer);
    pollTimer = null;
    countdownTimer = null;
  }

  function showError(message) {
    stopTimers();
    setStatus(message || "تعذر الاتصال. تحقق من الإنترنت ثم حاول مجددًا.", "error");
    if (timerEl) timerEl.textContent = "الرمز غير نشط";
    if (retryButton) retryButton.hidden = false;
  }

  function renderCountdown() {
    if (!deadline || !timerEl) return;
    var remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
    var minutes = Math.floor(remaining / 60);
    var seconds = remaining % 60;
    timerEl.textContent = "صالح لمدة " + minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
    if (remaining <= 0) showError("انتهت صلاحية الرمز. أنشئ رمزًا جديدًا للمتابعة.");
  }

  function completePairing(data) {
    stopTimers();
    setStatus("تم الربط بـ " + (data.screen_name || "شاشة المدرسة") + " — سيبدأ العرض الآن", "success");
    if (timerEl) timerEl.textContent = "تم الربط بنجاح";
    if (qrEl) qrEl.hidden = true;
    if (placeholderEl) placeholderEl.hidden = true;
    if (successEl) successEl.hidden = false;
    window.setTimeout(function () {
      window.location.replace(data.display_url);
    }, 1100);
  }

  function pollStatus() {
    if (!currentSession) return;
    postForm(
      currentSession.status_url,
      { device_secret: currentSession.device_secret },
      function (status, data) {
        if (status === 200 && data.status === "approved" && data.display_url) {
          completePairing(data);
          return;
        }
        if (status === 200 && data.status === "pending") {
          setStatus("بانتظار تأكيد الربط من الجوال…", "");
          pollTimer = window.setTimeout(pollStatus, 2500);
          return;
        }
        if (status === 0 || status >= 500) {
          setStatus("الاتصال متقطع — سنحاول مجددًا تلقائيًا", "");
          pollTimer = window.setTimeout(pollStatus, 5000);
          return;
        }
        showError(data.message || "تعذر إكمال جلسة الربط.");
      }
    );
  }

  function startPairing() {
    stopTimers();
    currentSession = null;
    deadline = 0;
    if (retryButton) retryButton.hidden = true;
    if (codeEl) codeEl.textContent = "— — —";
    if (timerEl) timerEl.textContent = "جارٍ إنشاء رمز آمن…";
    if (qrEl) { qrEl.hidden = true; qrEl.removeAttribute("src"); }
    if (placeholderEl) placeholderEl.hidden = false;
    if (successEl) successEl.hidden = true;
    setStatus("جارٍ تجهيز الربط…", "");

    postForm(startUrl, { device_id: getDeviceId() }, function (status, data) {
      if (status !== 200 || !data.status_url || !data.device_secret) {
        showError(data.message || "تعذر إنشاء رمز الربط. تحقق من الإنترنت ثم حاول مجددًا.");
        return;
      }
      currentSession = data;
      deadline = Date.now() + (Number(data.expires_in || 600) * 1000);
      if (codeEl) codeEl.textContent = data.formatted_code || data.user_code;
      if (qrEl) {
        qrEl.onload = function () {
          if (placeholderEl) placeholderEl.hidden = true;
          qrEl.hidden = false;
        };
        qrEl.onerror = function () {
          setStatus("استخدم رمز الأرقام من لوحة التحكم لإكمال الربط", "");
        };
        qrEl.src = data.qr_url;
      }
      renderCountdown();
      countdownTimer = window.setInterval(renderCountdown, 1000);
      setStatus("بانتظار مسح الرمز أو إدخاله من الجوال…", "");
      pollTimer = window.setTimeout(pollStatus, 1200);
    });
  }

  if (retryButton) retryButton.onclick = startPairing;
  startPairing();
}());
