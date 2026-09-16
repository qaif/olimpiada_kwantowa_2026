/* Linia czasu edycji w nagłówku (templates/cms/_timeline_strip.html): głowica i warstwa „pomiaru”.
 *
 * Czego ten skrypt **nie** robi: nie rysuje paska i nie rozwija panelu. Pasek, znaczniki wydarzeń,
 * legenda i położenie głowicy są w kodzie strony (apps/cms/timeline.py), a panel rozwija czysty
 * CSS (``:hover`` i ``:focus-within`` na pasku). Strona z wyłączonym JavaScriptem ma więc
 * kompletny kalendarz i działające rozwijanie – nic tu nie jest warunkiem działania.
 *
 * Skrypt dokłada trzy rzeczy, których serwer i arkusz dać nie mogą:
 *
 * 1. **Przesuwa głowicę w karcie zostawionej otwartej.** Strona wyrenderowana w poniedziałek
 *    pokazywałaby poniedziałkowy stan także w czwartek. Co minutę liczymy dzisiejszy ułamek osi
 *    z dat w ``data-axis-start``/``data-axis-end`` i – jeżeli głowica ma stanąć gdzie indziej –
 *    podmieniamy **tekst** w gotowych komórkach. Tekst, a nie kod paska: w pasku stoją odnośniki
 *    i przyciski, więc przebudowa kasowałaby fokus i przerywała najechanie. Reguła znaku jest
 *    przepisana z ``timeline.cell_char`` – duplikat jest świadomy i pilnowany testem (patrz
 *    docstring tamtej funkcji).
 *
 * 2. **Rysuje „pomiar” pod kursorem.** Nad paskiem faluje paczka falowa (|ψ|² z nośną), rozmyta
 *    na całą oś. Najechanie na znacznik wydarzenia jest pomiarem: w ~300 ms paczka zapada się
 *    w ostry pik nad tym wydarzeniem i puszcza jedno kółko fali; zjechanie kursorem to
 *    dekoherencja – pik rozpływa się z powrotem. Warstwa jest ozdobą: ``aria-hidden``,
 *    ``pointer-events: none``, bez niej strona nie traci żadnej informacji.
 *
 *    Pętla ``requestAnimationFrame`` chodzi **wyłącznie** pod kursorem i jest ograniczona do
 *    ~30 klatek na sekundę; na dotyku i przy ``prefers-reduced-motion: reduce`` nie startuje
 *    w ogóle, bo tam nie ma ani najechania, ani zgody na ruch.
 *
 * 3. **Wiąże kreskę z chipem legendy** – podświetla parę, bo kreska i chip stoją w różnych
 *    gałęziach drzewa i selektor ich nie połączy. Na ekranie bez najechania ten sam kawałek
 *    obsługuje dotknięcie kreski, które panel otwiera.
 */
(function () {
  "use strict";

  var strip = document.querySelector("[data-timeline-strip]");
  if (!strip) {
    return;
  }

  var DAY_MS = 86400000;
  var SIZE = parseInt(strip.getAttribute("data-size"), 10) || 100;
  var cells = strip.querySelectorAll("[data-tl-cell]");
  var head = parseInt(strip.getAttribute("data-head"), 10) || 0;

  function parseDay(text) {
    var parts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text || "");
    return parts ? new Date(+parts[1], +parts[2] - 1, +parts[3]) : null;
  }

  var axisStart = parseDay(strip.getAttribute("data-axis-start"));
  var axisEnd = parseDay(strip.getAttribute("data-axis-end"));

  /* --- głowica ------------------------------------------------------------------------------ */

  function todayHead() {
    if (!axisStart || !axisEnd) {
      return head;
    }
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var total = Math.max(Math.round((axisEnd - axisStart) / DAY_MS), 1);
    var elapsed = Math.round((today - axisStart) / DAY_MS);
    var fraction = Math.min(Math.max(elapsed / total, 0), 1);
    return Math.min(Math.round(fraction * SIZE), SIZE - 1);
  }

  function cellChar(index, at, isMark) {
    if (index === at) {
      return ">";
    }
    if (isMark) {
      return "|";
    }
    return index < at ? "=" : ".";
  }

  function renderCells(at) {
    for (var order = 0; order < cells.length; order += 1) {
      var cell = cells[order];
      var index = parseInt(cell.getAttribute("data-tl-cell"), 10);
      /* Podmieniamy węzeł tekstowy, a nie ``textContent``: znacznik bywa odnośnikiem
         z atrybutami, a przypisanie do ``textContent`` przebudowałoby jego zawartość. */
      if (cell.firstChild && cell.firstChild.nodeType === 3) {
        cell.firstChild.nodeValue = cellChar(index, at, cell.hasAttribute("data-tl-mark"));
      }
      cell.classList.toggle("tl__head", index === at);
    }
  }

  function refresh() {
    var next = todayHead();
    if (next !== head) {
      head = next;
      strip.setAttribute("data-head", String(head));
      renderCells(head);
    }
  }

  /* Minuta, a nie sekunda: głowica przesuwa się o komórkę raz na kilka dni. Odpytywanie zegara
     częściej byłoby pracą bez żadnego skutku widocznego na ekranie. */
  window.setInterval(refresh, 60000);
  refresh();

  /* Klasa animacji pada dopiero tutaj, więc bez JavaScriptu pasek jest po prostu narysowany:
     nic nie „dojeżdża”. */
  strip.classList.add("is-animated");

  /* --- kreska ↔ chip legendy ------------------------------------------------------------------ */
  /*
   * Rozwijanie panelu robi sam arkusz (``:hover``, ``:focus-within``), więc bez JavaScriptu
   * legenda działa. Skrypt dokłada dwie rzeczy, których selektorem nie da się wyrazić: parowanie
   * kreski z jej chipem (są w różnych gałęziach drzewa, a ``~`` sięga tylko rodzeństwa) i –
   * na ekranie bez najechania – dotknięcie, które panel otwiera.
   */

  var marks = strip.querySelectorAll("[data-tl-mark]");
  var ACTIVE = "is-active";

  function chipsOf(mark) {
    return (mark.getAttribute("data-tl-items") || "")
      .split(",")
      .map(function (number) {
        return strip.querySelector('[data-tl-chip="' + number + '"]');
      })
      .filter(Boolean);
  }

  function highlight(mark, on) {
    mark.classList.toggle(ACTIVE, on);
    chipsOf(mark).forEach(function (chip) {
      chip.classList.toggle(ACTIVE, on);
    });
  }

  /* Dotyk: brak najechania, więc panel otwiera dotknięcie kreski. Pierwsze dotknięcie **tylko**
     otwiera (odnośnik by nawigował, zanim ktokolwiek zobaczyłby legendę), drugie – w tę samą
     kreskę – idzie już zwykłą drogą. To ten sam wzorzec, co w rozwijanych menu na telefonie. */
  var touch = window.matchMedia && !window.matchMedia("(hover: hover)").matches;
  var opened = null;

  Array.prototype.forEach.call(marks, function (mark) {
    mark.addEventListener("mouseenter", function () {
      highlight(mark, true);
    });
    mark.addEventListener("mouseleave", function () {
      highlight(mark, false);
    });
    mark.addEventListener("focus", function () {
      highlight(mark, true);
    });
    mark.addEventListener("blur", function () {
      highlight(mark, false);
    });
    if (!touch) {
      return;
    }
    mark.addEventListener("click", function (event) {
      if (opened === mark) {
        return;
      }
      event.preventDefault();
      if (opened) {
        highlight(opened, false);
      }
      opened = mark;
      highlight(mark, true);
      strip.classList.add("is-open");
    });
  });

  if (touch) {
    /* Dotknięcie poza paskiem zamyka panel – inaczej zostawałby otwarty na resztę wizyty. */
    document.addEventListener("click", function (event) {
      if (strip.contains(event.target)) {
        return;
      }
      if (opened) {
        highlight(opened, false);
        opened = null;
      }
      strip.classList.remove("is-open");
    });
  }

  /* --- warstwa „pomiaru” --------------------------------------------------------------------- */

  var canvas = strip.querySelector("[data-tl-quantum]");
  var canHover = window.matchMedia && window.matchMedia("(hover: hover)").matches;
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!canvas || !canvas.getContext || !canHover || reduced) {
    return;
  }

  var context = canvas.getContext("2d");
  var FRAME_MS = 33; /* ~30 kl./s – warstwa jest tłem, nie animacją, na którą się patrzy. */
  var SPREAD_SIGMA = 0.26; /* szerokość rozmytej paczki w ułamku osi */
  var PEAK_SIGMA = 0.014; /* szerokość piku po „pomiarze” */
  var COLLAPSE_MS = 300; /* czas zapadania i rozpływania się z powrotem */
  var frame = null;
  var lastDraw = 0;
  var collapse = 0; /* 0 – paczka rozmyta, 1 – zapadnięta w pik */
  var target = 0;
  var focus = 0.5; /* położenie piku w ułamku szerokości */
  var rippleAt = -1;
  var width = 0;
  var height = 0;

  var palette = window.getComputedStyle(strip);
  var waveColor = (palette.getPropertyValue("--tl-past") || "#8fb4e6").trim();
  var peakColor = (palette.getPropertyValue("--tl-current") || "#ef8a82").trim();

  function resize() {
    var rect = strip.getBoundingClientRect();
    var ratio = window.devicePixelRatio || 1;
    width = Math.max(Math.round(rect.width), 1);
    height = Math.max(Math.round(rect.height), 1);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  /* Miejsce „pomiaru” bierzemy z prostokąta samego znacznika, a nie z siatki komórek: znacznik
     jest szeroki na jeden znak i stoi dokładnie tam, gdzie zaczyna się wydarzenie, więc jego
     środek **jest** położeniem wydarzenia na osi – i pozostaje nim po każdej zmianie kroju. */
  function aim(event) {
    var mark = event.target && event.target.closest ? event.target.closest("[data-tl-mark]") : null;
    if (!mark) {
      target = 0;
      return;
    }
    var box = strip.getBoundingClientRect();
    var spot = mark.getBoundingClientRect();
    var next = box.width > 0 ? (spot.left + spot.width / 2 - box.left) / box.width : 0.5;
    if (target === 0 || Math.abs(next - focus) > 0.001) {
      /* Nowy pomiar puszcza kółko fali – ślad po tym, że stan właśnie się zmienił. */
      rippleAt = performance.now();
    }
    focus = next;
    target = 1;
  }

  /* |ψ|²: obwiednia gaussowska z nośną. Amplituda jest niska świadomie – to tło nagłówka,
     a nie wykres do odczytania. */
  function amplitude(u, time) {
    var sigma = SPREAD_SIGMA + (PEAK_SIGMA - SPREAD_SIGMA) * collapse;
    var centre = 0.5 + (focus - 0.5) * collapse;
    var drift = collapse < 1 ? Math.sin(time / 2600) * 0.06 * (1 - collapse) : 0;
    var offset = (u - centre - drift) / sigma;
    var envelope = Math.exp(-0.5 * offset * offset);
    var carrier = Math.cos(u * 46 - time / 340);
    return envelope * (0.55 + 0.45 * carrier * carrier);
  }

  function draw(time) {
    /* Panel rozwija się w dół przez ćwierć sekundy, więc pasek rośnie **w trakcie** rysowania:
       płótno zmierzone w chwili najechania byłoby przez cały ten czas za niskie. Porównanie
       wysokości jest tańsze niż przeliczanie płótna w każdej klatce na zapas. */
    if (Math.round(strip.getBoundingClientRect().height) !== height) {
      resize();
    }
    context.clearRect(0, 0, width, height);
    var base = height - 2;
    var reach = height * 0.62;

    context.beginPath();
    context.moveTo(0, base);
    for (var x = 0; x <= width; x += 2) {
      context.lineTo(x, base - amplitude(x / width, time) * reach);
    }
    context.lineTo(width, base);
    context.closePath();
    context.globalAlpha = 0.16 + 0.12 * collapse;
    context.fillStyle = collapse > 0.5 ? peakColor : waveColor;
    context.fill();

    context.globalAlpha = 0.5;
    context.lineWidth = 1;
    context.strokeStyle = collapse > 0.5 ? peakColor : waveColor;
    context.stroke();

    /* Kółko fali tuż po pomiarze: jedno, rozchodzące się od piku i gasnące w pół sekundy. */
    if (rippleAt > 0) {
      var age = (time - rippleAt) / 520;
      if (age >= 1) {
        rippleAt = -1;
      } else {
        context.beginPath();
        context.globalAlpha = 0.35 * (1 - age);
        context.arc(focus * width, base, age * height * 1.6, Math.PI, 2 * Math.PI);
        context.stroke();
      }
    }
    context.globalAlpha = 1;
  }

  function step(time) {
    frame = window.requestAnimationFrame(step);
    if (time - lastDraw < FRAME_MS) {
      return;
    }
    var delta = Math.min(time - lastDraw, 200);
    lastDraw = time;
    var toward = (delta / COLLAPSE_MS) * (target ? 1 : -1);
    collapse = Math.min(Math.max(collapse + toward, 0), 1);
    draw(time);
  }

  function start() {
    if (frame !== null) {
      return;
    }
    resize();
    lastDraw = performance.now();
    strip.classList.add("is-measuring");
    frame = window.requestAnimationFrame(step);
  }

  function stop() {
    if (frame === null) {
      return;
    }
    window.cancelAnimationFrame(frame);
    frame = null;
    collapse = 0;
    target = 0;
    rippleAt = -1;
    strip.classList.remove("is-measuring");
    context.clearRect(0, 0, width, height);
  }

  strip.addEventListener("mouseenter", start);
  strip.addEventListener("mousemove", aim);
  strip.addEventListener("mouseleave", stop);
  /* Zmiana szerokości okna zmienia rozmiar płótna; przeliczamy je tylko wtedy, gdy warstwa
     w ogóle chodzi – poza najechaniem nie ma czego skalować. */
  window.addEventListener("resize", function () {
    if (frame !== null) {
      resize();
    }
  });
})();
