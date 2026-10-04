/*
 * Webinary (zadanie WEB-01): dopisuje do godzin w strefie konkursu czas lokalny przeglądarki.
 *
 * Serwer pokazuje termin w strefie konkursu (``Competition.time_zone``, atrybut ``data-zone``
 * kontenera) – to jest godzina z ogłoszenia. Uczestnik z innej strefy dostaje obok
 * „(u Ciebie: …)”, liczone przez ``Intl`` z atrybutu ``datetime`` (UTC, ISO 8601). Gdy strefy są
 * te same albo przeglądarka nie zna ``Intl``, nic się nie zmienia – strona jest kompletna bez
 * skryptu. Tekst wstawiany wyłącznie przez ``textContent``.
 */
(function () {
  "use strict";

  function init() {
    var root = document.querySelector("[data-webinars-zone]");
    if (!root || typeof Intl === "undefined" || !Intl.DateTimeFormat) {
      return;
    }
    var browserZone;
    var format;
    try {
      browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      format = new Intl.DateTimeFormat(document.documentElement.lang || undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (err) {
      return;
    }
    if (!browserZone || browserZone === root.getAttribute("data-webinars-zone")) {
      return;
    }
    var label = root.getAttribute("data-local-label") || "";
    root.querySelectorAll("time[data-local-time]").forEach(function (node) {
      var moment = new Date(node.getAttribute("datetime"));
      if (isNaN(moment.getTime())) {
        return;
      }
      var extra = document.createElement("span");
      extra.className = "hint";
      extra.textContent = " (" + label + " " + format.format(moment) + ")";
      node.after(extra);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
