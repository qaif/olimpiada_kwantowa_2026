/*
 * Błędy JavaScriptu → własny GlitchTip (OPS-02 § 5). Bez SDK i bez CDN: ~100 linii, które
 * budują minimalne zdarzenie Sentry i wysyłają je kopertą na adres z `data-endpoint`.
 *
 * Wczytywany wyłącznie przy SENTRY_BROWSER=1 (znacznik z `{% error_tracking_loader %}`), więc strona
 * bez tej funkcji nie ma tego pliku ani originu GlitchTipa w CSP.
 *
 * Prywatność – to samo, co filtr serwera (apps/monitoring/scrubbing.py):
 * - adres strony bez zapytania i fragmentu, z zamaskowanymi segmentami-tokenami (reset hasła,
 *   zgoda, zaproszenie, kody listów – maskPath, to samo co serwer),
 * - komunikat i ślad stosu przez `scrub()` – e-mail, PESEL, telefon, tokeny, JWT,
 * - żadnych ciasteczek, nagłówków, identyfikatora konta, treści formularzy, User-Agenta,
 * - najwyżej MAX_EVENTS zdarzeń na stronę, duplikaty pomijane – pętla błędów nie zaleje serwera.
 */
(function () {
  "use strict";

  var script = document.currentScript;
  if (!script || !window.fetch) return;
  var endpoint = script.getAttribute("data-endpoint");
  if (!endpoint) return;
  var release = script.getAttribute("data-release") || undefined;
  var environment = script.getAttribute("data-environment") || undefined;
  var competition = script.getAttribute("data-competition") || "";

  var MAX_EVENTS = 5;
  var sent = 0;
  var seen = {};

  var FILTERED = "[Filtered]";
  // Wyrażenia bez spojrzeń wstecz (starsze Safari ich nie znają – błąd składni wyłączyłby cały plik);
  // koszt ogranicza przycięcie napisu do 1000 znaków i ograniczone powtórzenia.
  var PATTERNS = [
    /\beyJ[A-Za-z0-9_\-]{1,2048}\.[A-Za-z0-9_\-]{1,2048}\.[A-Za-z0-9_\-]{0,2048}/g,
    /[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,8}/g,
    /(^|[^\d])\d{11}(?!\d)/g,
    /(^|[^\w.:+\-])(\+\d[\d \-]{6,16}\d|\d{3}[ \-]?\d{3}[ \-]?\d{3})(?![\w.:\-])/g,
    /(^|[^\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])/g
  ];
  // To samo, co apps/monitoring/scrubbing.py (mask_path): segmenty-tokeny i wszystko po słowach
  // z adresów jednorazowych (reset/<uid>/<token>/, zgoda/<token>/, visa/verify/<kod>/ …).
  var KEYWORDS = ["reset", "zgoda", "zgody", "consent", "zaproszenie", "zaproszenia", "invite",
    "invitation", "accept", "activate", "aktywacja", "unsubscribe", "wypisz", "verify", "weryfikacja",
    "dyplomy", "diplomas", "certificate", "new", "token", "confirm", "potwierdz", "bypass", "sso",
    "magic", "share"];

  function tokenLike(segment) {
    var stem = /\.html$/i.test(segment) ? segment.slice(0, -5) : segment;
    return /^[A-Za-z0-9_\-=%~:]{12,}$/.test(stem) && /[0-9A-Z_=%]/.test(stem);
  }

  function maskPath(path) {
    var after = false;
    return String(path || "").split("/").map(function (segment) {
      if (!segment || segment === FILTERED) return segment;
      var out = (after || tokenLike(segment)) ? FILTERED : segment;
      if (KEYWORDS.indexOf(segment.toLowerCase()) !== -1) after = true;
      return out;
    }).join("/");
  }

  function stripQuery(url) {
    var bare = String(url || "").replace(/[?#].*$/, "");
    var m = bare.match(/^(https?:\/\/[^\/]+)(\/.*)?$/);
    return m ? m[1] + maskPath(m[2] || "") : maskPath(bare);
  }

  function scrub(text) {
    var value = String(text || "").slice(0, 1000);
    value = value.replace(/(https?:\/\/[^\s\/?#"'<>]{1,253})(\/[^\s?#"'<>]{0,1000})?([?#][^\s"'<>]{0,1000})?/g,
      function (_, host, path) { return host + maskPath(path || ""); });
    // Sama ścieżka w tekście („GET /reset/MQ/abc/”, „next=/zgoda/…”) – bez zapytania, zamaskowana.
    value = value.replace(/(^|[\s'"(=])(\/[A-Za-z0-9_\-.%~=:\/]{1,1000})(\?[^\s"'<>]{0,1000})?/g,
      function (_, before, path) { return before + maskPath(path); });
    value = value.replace(
      /([\w\-]{0,40}?(?:token|key|code|secret|signature|password|sig|jwt|auth|session|csrf)[\w\-]{0,40})(\s{0,3}[=:]\s{0,3})([^&\s"',;)]{1,512})/gi,
      "$1$2" + FILTERED
    );
    value = value.replace(PATTERNS[0], FILTERED).replace(PATTERNS[1], FILTERED);
    value = value.replace(PATTERNS[2], "$1" + FILTERED).replace(PATTERNS[3], "$1" + FILTERED);
    value = value.replace(PATTERNS[4], "$1" + FILTERED);
    return value;
  }

  // Ślad stosu V8/SpiderMonkey/JavaScriptCore → ramki Sentry (od najstarszej do najnowszej).
  function frames(stack) {
    var out = [];
    String(stack || "").split("\n").slice(0, 30).forEach(function (line) {
      var m = line.match(/at (?:(.+?) \()?(.+?):(\d+):(\d+)\)?\s*$/) ||
        line.match(/^(?:(.*?)@)?(.+?):(\d+):(\d+)\s*$/);
      if (!m) return;
      out.push({
        "function": scrub(m[1] || "?"),
        "filename": stripQuery(m[2]),
        "abs_path": stripQuery(m[2]),
        "lineno": parseInt(m[3], 10),
        "colno": parseInt(m[4], 10),
        "in_app": m[2].indexOf(location.origin) === 0
      });
    });
    return out.reverse();
  }

  function uuid() {
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) {
      return ("0" + b.toString(16)).slice(-2);
    }).join("");
  }

  function send(type, message, stack) {
    var key = type + ":" + message;
    if (sent >= MAX_EVENTS || seen[key]) return;
    seen[key] = true;
    sent += 1;
    var eventId = uuid();
    var event = {
      "event_id": eventId,
      "timestamp": Date.now() / 1000,
      "platform": "javascript",
      "level": "error",
      "logger": "javascript",
      "release": release,
      "environment": environment,
      "request": { "url": location.origin + maskPath(location.pathname) },
      "tags": competition ? { "competition": competition } : {},
      "exception": {
        "values": [{
          "type": scrub(type),
          "value": scrub(message),
          "stacktrace": { "frames": frames(stack) }
        }]
      }
    };
    var body = JSON.stringify({ "event_id": eventId, "sent_at": new Date().toISOString() }) + "\n" +
      JSON.stringify({ "type": "event" }) + "\n" + JSON.stringify(event);
    try {
      // text/plain: „zwykłe” żądanie CORS, bez preflightu (tak samo robi oficjalne SDK).
      window.fetch(endpoint, {
        method: "POST",
        body: body,
        keepalive: true,
        credentials: "omit",
        referrerPolicy: "no-referrer",
        headers: { "Content-Type": "text/plain;charset=UTF-8" }
      }).catch(function () {});
    } catch (e) { /* zgłaszanie błędu nie może samo rzucić błędu */ }
  }

  window.addEventListener("error", function (e) {
    // Błąd ładowania zasobu (<img>, <script>) nie ma `error` ani komunikatu – pomijamy.
    if (!e || (!e.error && !e.message)) return;
    var err = e.error || {};
    send(err.name || "Error", e.message || err.message || "", err.stack ||
      (e.filename ? "at " + e.filename + ":" + e.lineno + ":" + e.colno : ""));
  });

  window.addEventListener("unhandledrejection", function (e) {
    var reason = e && e.reason;
    if (reason instanceof Error) {
      send(reason.name || "UnhandledRejection", reason.message || "", reason.stack || "");
    } else {
      send("UnhandledRejection", typeof reason === "string" ? reason : "Non-Error promise rejection", "");
    }
  });
})();
