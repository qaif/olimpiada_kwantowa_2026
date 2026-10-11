/* Podpowiedź literówek w adresie e-mail (MAIL-02 § 1.4) – „Czy chodziło Ci o …?” po opuszczeniu pola.
 *
 * Nieblokująco: skrypt niczego nie wysyła i nie wstrzymuje formularza, tylko pokazuje komunikat
 * z przyciskiem „Użyj …”. Rozstrzyga serwer (apps/email_delivery/fields.py) – ten plik jest wyłącznie
 * szybszą wersją tej samej podpowiedzi. Algorytm i obie listy są kopią apps/email_delivery/typos.py;
 * apps/email_delivery/tests/test_js.py pilnuje, że listy są identyczne, a node --test backend/js_tests
 * – że odpowiedzi na wspólnych przypadkach (tests/typo_cases.json) są te same.
 *
 * Domeny IDN: przeglądarka oddaje wartość pola type="email" z domeną w postaci ASCII (xn--…), więc
 * podpowiedź z mapy TLD pokaże domenę tak, jak ją widzi przeglądarka. Serwer podpowiada w Unicode.
 *
 * Umowa z widżetem (templates/email_delivery/widgets/email_check.html): pole ma data-email-check,
 * data-email-hint ("Czy chodziło Ci o %s?") i data-email-use ("Użyj %s") – zdania przetłumaczone
 * po stronie serwera. Pole wyboru data-email-accept (podpowiedź serwera) podmienia wartość pola
 * od razu, bez wysyłki. Bez CSP 'unsafe-inline': plik ładowany z nonce w templates/base.html.
 */
(function () {
  "use strict";

  var KNOWN_DOMAINS = [
    "gmail.com", "wp.pl", "o2.pl", "onet.pl", "interia.pl", "op.pl", "outlook.com", "hotmail.com",
    "yahoo.com", "icloud.com", "gazeta.pl", "tlen.pl", "poczta.fm", "vp.pl", "onet.eu", "go2.pl",
    "interia.eu", "interia.com", "poczta.onet.pl", "spoko.pl", "autograf.pl", "buziaczek.pl",
    "live.com", "msn.com", "outlook.de", "outlook.fr", "outlook.es", "outlook.it", "hotmail.co.uk",
    "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.es", "live.fr", "googlemail.com", "me.com",
    "mac.com", "aol.com", "protonmail.com", "proton.me", "gmx.com", "gmx.net", "gmx.de", "web.de",
    "t-online.de", "mail.com", "email.com", "zoho.com", "yahoo.co.uk", "yahoo.co.in", "yahoo.co.jp",
    "yahoo.fr", "yahoo.de", "yahoo.es", "yahoo.it", "yahoo.com.br", "ymail.com", "qq.com", "163.com",
    "126.com", "sina.com", "foxmail.com", "yeah.net", "aliyun.com", "naver.com", "daum.net",
    "hanmail.net", "mail.ru", "bk.ru", "list.ru", "inbox.ru", "yandex.ru", "yandex.com", "ya.ru",
    "rambler.ru", "ukr.net", "rediffmail.com", "orange.fr", "free.fr", "laposte.net", "libero.it",
    "seznam.cz", "uol.com.br", "bol.com.br"
  ];

  var TLD_TYPOS = {
    "plo": "pl", "pll": "pl", "ppl": "pl", "lp": "pl", "pol": "pl",
    "con": "com", "cmo": "com", "ocm": "com", "cpm": "com", "comm": "com", "coom": "com",
    "copm": "com", "comn": "com", "vom": "com", "xom": "com", "cim": "com", "cm": "com", "om": "com",
    "nte": "net", "ner": "net", "nett": "net",
    "ogr": "org", "orgg": "org", "rog": "org"
  };

  var SHORT_DOMAIN_LENGTH = 6;

  function distance(left, right) {
    var rows = left.length + 1;
    var cols = right.length + 1;
    var table = [];
    var i;
    var j;
    for (i = 0; i < rows; i++) {
      table.push(new Array(cols).fill(0));
      table[i][0] = i;
    }
    for (j = 0; j < cols; j++) { table[0][j] = j; }
    for (i = 1; i < rows; i++) {
      for (j = 1; j < cols; j++) {
        var cost = left[i - 1] === right[j - 1] ? 0 : 1;
        table[i][j] = Math.min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + cost);
        if (i > 1 && j > 1 && left[i - 1] === right[j - 2] && left[i - 2] === right[j - 1]) {
          table[i][j] = Math.min(table[i][j], table[i - 2][j - 2] + 1);
        }
      }
    }
    return table[rows - 1][cols - 1];
  }

  function suggestDomain(domain) {
    domain = String(domain || "").trim().replace(/\.+$/, "").toLowerCase();
    if (!domain || domain.indexOf(".") === -1 || KNOWN_DOMAINS.indexOf(domain) !== -1) { return null; }
    var limit = domain.length <= SHORT_DOMAIN_LENGTH ? 1 : 2;
    var best = null;
    var bestDistance = limit + 1;
    for (var k = 0; k < KNOWN_DOMAINS.length; k++) {
      var known = KNOWN_DOMAINS[k];
      if (Math.abs(known.length - domain.length) > limit) { continue; }
      var current = distance(domain, known);
      if (current < bestDistance) { best = known; bestDistance = current; }
    }
    if (best !== null) { return best; }
    var dot = domain.lastIndexOf(".");
    var head = domain.slice(0, dot);
    var fixed = Object.prototype.hasOwnProperty.call(TLD_TYPOS, domain.slice(dot + 1))
      ? TLD_TYPOS[domain.slice(dot + 1)] : null;
    return fixed && head ? head + "." + fixed : null;
  }

  function suggest(address) {
    address = String(address || "").trim();
    var at = address.lastIndexOf("@");
    if (at <= 0) { return null; }
    var fixed = suggestDomain(address.slice(at + 1));
    return fixed ? address.slice(0, at) + "@" + fixed : null;
  }

  function format(template, value) {
    return String(template || "%s").replace("%s", value);
  }

  function hideHint(input) {
    var hint = document.getElementById(input.id + "_hint");
    if (hint) { hint.parentNode.removeChild(hint); }
  }

  function showHint(input, suggestion) {
    hideHint(input);
    var hint = document.createElement("span");
    hint.className = "email-suggestion";
    hint.id = input.id + "_hint";
    hint.setAttribute("role", "status");
    hint.appendChild(document.createTextNode(format(input.getAttribute("data-email-hint"), suggestion) + " "));
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn--small btn--secondary";
    button.textContent = format(input.getAttribute("data-email-use"), suggestion);
    button.addEventListener("click", function () {
      input.value = suggestion;
      hideHint(input);
      input.focus();
    });
    hint.appendChild(button);
    input.parentNode.insertBefore(hint, input.nextSibling);
  }

  function bind(input) {
    if (!input.id) { return; }
    input.addEventListener("blur", function () {
      // Podpowiedź serwera (pole wyboru) już stoi przy polu – druga, z przeglądarki, by ją dublowała.
      if (document.getElementById(input.id + "_suggestion")) { return; }
      var suggestion = suggest(input.value);
      if (suggestion && suggestion !== input.value) { showHint(input, suggestion); } else { hideHint(input); }
    });
    var accept = document.getElementById(input.id + "_accept");
    if (accept) {
      var typed = input.value;
      accept.addEventListener("change", function () { input.value = accept.checked ? accept.value : typed; });
    }
  }

  var api = { suggest: suggest, suggestDomain: suggestDomain, distance: distance,
    KNOWN_DOMAINS: KNOWN_DOMAINS, TLD_TYPOS: TLD_TYPOS };
  if (typeof window !== "undefined") { window.OlimpiadaEmailCheck = api; }

  if (typeof document !== "undefined" && document.querySelectorAll) {
    var start = function () {
      var inputs = document.querySelectorAll("input[data-email-check]");
      for (var n = 0; n < inputs.length; n++) { bind(inputs[n]); }
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", start);
    } else {
      start();
    }
  }
})();
