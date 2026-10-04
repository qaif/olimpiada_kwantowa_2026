/*
 * Skaner kodów QR identyfikatorów finału w przeglądarce (LOG-01) – dodatek, nie warunek.
 *
 * Kod QR to zwykły adres strony osoby, więc aparat telefonu otwiera go sam; ten skrypt oszczędza
 * przełączania aplikacji tam, gdzie przeglądarka ma ``BarcodeDetector`` (Chrome na Androidzie).
 * Bez niego przycisk się nie pokazuje, a ekran działa wyszukiwarką i aparatem.
 *
 * Bezpieczeństwo: przechodzimy **wyłącznie** pod adres z tego samego pochodzenia i spod ścieżki
 * ekranu obsługi. Kod QR podsunięty przez kogokolwiek (naklejka na identyfikatorze) nie może więc
 * wyprowadzić telefonu obsługi na obcą stronę.
 */
(function () {
  "use strict";

  var box = document.getElementById("scanner");
  var start = document.getElementById("scanner-start");
  if (!box || !start || !("BarcodeDetector" in window) || !navigator.mediaDevices) {
    return;
  }
  var base = box.getAttribute("data-base") || "";
  var checkpoint = box.getAttribute("data-cp") || "";
  var video = document.getElementById("scanner-video");
  start.hidden = false;

  function target(raw) {
    var url;
    try {
      url = new URL(raw, window.location.href);
    } catch (error) {
      return null;
    }
    if (url.origin !== window.location.origin || url.pathname.indexOf(base) !== 0 || url.pathname === base) {
      return null;
    }
    return url.pathname + (checkpoint ? "?cp=" + encodeURIComponent(checkpoint) : "");
  }

  start.addEventListener("click", function () {
    var detector = new window.BarcodeDetector({ formats: ["qr_code"] });
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then(function (stream) {
        box.hidden = false;
        start.hidden = true;
        video.srcObject = stream;
        video.play();
        function tick() {
          detector
            .detect(video)
            .then(function (codes) {
              for (var i = 0; i < codes.length; i += 1) {
                var next = target(codes[i].rawValue);
                if (next) {
                  stream.getTracks().forEach(function (track) {
                    track.stop();
                  });
                  window.location.assign(next);
                  return;
                }
              }
              window.requestAnimationFrame(tick);
            })
            .catch(function () {
              window.requestAnimationFrame(tick);
            });
        }
        tick();
      })
      .catch(function () {
        start.hidden = true;
      });
  });
})();
