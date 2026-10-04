/*
 * Błędy JavaScriptu → własny GlitchTip (OPS-02 § 5). Bez SDK i bez CDN: ~100 linii, które
 * budują minimalne zdarzenie Sentry i wysyłają je kopertą na adres z `data-endpoint`.
 *
 * Wczytywany wyłącznie przy SENTRY_BROWSER=1 (znacznik z `{% error_tracking_loader %}`), więc strona
 * bez tej funkcji nie ma tego pliku ani originu GlitchTipa w CSP.
 *
 * Prywatność – to samo, co filtr serwera (apps/monitoring/scrubbing.py):
 * - adres strony bez zapytania i fragmentu (tokeny resetu hasła, przepustki, kody listów),
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
  var PATTERNS = [
    /\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+/g,
    /[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}/g,
    /(^|[^\d])\d{11}(?!\d)/g,
    /(^|[^\w.:+\-])(\+\d[\d \-]{6,16}\d|\d{3}[ \-]?\d{3}[ \-]?\d{3})(?![\w.:\-])/g
  ];

  function stripQuery(url) {
    return String(url || "").replace(/[?#].*$/, "");
  }

  function scrub(text) {
    var value = String(text || "").slice(0, 1000);
    value = value.replace(/(https?:\/\/[^\s?#]+)[?#][^\s]*/g, "$1");
    value = value.replace(
      /([\w\-]*(?:token|key|code|secret|signature|password|sig|jwt|auth|session|csrf)[\w\-]*)(\s*[=:]\s*)([^&\s"',;)]+)/gi,
      "$1$2" + FILTERED
    );
    value = value.replace(PATTERNS[0], FILTERED).replace(PATTERNS[1], FILTERED);
    value = value.replace(PATTERNS[2], "$1" + FILTERED).replace(PATTERNS[3], "$1" + FILTERED);
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
      "request": { "url": location.origin + location.pathname },
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
