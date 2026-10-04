/*
 * Pasek nadzoru na stronie etapu (zadanie PROC-01). Konsola nadzoru działa w osobnej karcie i co 5 s
 * ogłasza swój stan przez BroadcastChannel („proctoring-<etap>”); ta strona go słucha.
 * Brak wieści przez 15 s, „dropped” albo zamknięta konsola = czerwony pasek z odnośnikiem do konsoli.
 * Bez BroadcastChannel (stare przeglądarki) pasek zostaje w stanie neutralnym – nic nie udaje.
 */
(function () {
  "use strict";

  var bar = document.querySelector("[data-proctoring-banner]");
  if (!bar || !("BroadcastChannel" in window)) {
    return;
  }
  var text = bar.querySelector("[data-proctoring-text]");
  var lastSeen = 0;
  var lastState = "";

  function render() {
    var stale = Date.now() - lastSeen > 15000;
    // Praca bez nadzoru (awaria serwera) NIE jest „nadzór działa” – osobny, ostrzegawczy stan.
    var unproctored = !stale && lastState === "unproctored";
    var bad = stale || lastState === "dropped" || lastState === "stopped" || lastState === "unavailable";
    var good = !stale && lastState === "live";
    bar.dataset.state = unproctored ? "warn" : bad ? "bad" : good ? "ok" : "neutral";
    text.textContent = unproctored
      ? bar.dataset.textUnproctored
      : bad
        ? bar.dataset.textBad
        : good
          ? bar.dataset.textOk
          : bar.dataset.textNeutral;
  }

  new BroadcastChannel("proctoring-" + bar.dataset.stageId).onmessage = function (event) {
    lastSeen = Date.now();
    lastState = (event.data && event.data.state) || "";
    render();
  };
  window.setInterval(render, 5000);
  window.setTimeout(render, 6000);
})();
