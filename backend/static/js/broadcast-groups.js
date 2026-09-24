/* Ekran „Komunikaty” (/coordinator/messages/): pokazuje tylko pole, którego wymaga wybrana grupa.
 *
 * Grup z parametrem jest kilka (etap, województwo, region, szkoła, klasa, warsztat, wklejona lista)
 * i każda potrzebuje **innego** pola. Formularz z siedmioma polami doprecyzowującymi naraz zaprasza
 * do pomyłki: koordynator wybiera szkołę, a wysyła do „zapisanych do etapu”, bo to pole etapu było
 * wypełnione. Skrypt chowa więc wszystko poza polem wybranej grupy.
 *
 * Czego ten skrypt **nie** robi: nie rozstrzyga, które pole należy do grupy. Mapę „grupa → pole”
 * liczy serwer (``BroadcastForm.parameter_map``) i wstawia ją do strony jako JSON
 * (``#broadcast-parameters``) – to ta sama funkcja, która w ``clean()`` decyduje, czego wymagać,
 * więc ekran i walidacja nie mają jak się rozjechać. Serwer i tak bierze pod uwagę wyłącznie pole
 * wybranej grupy, więc wartość w polu schowanym niczego nie zmienia.
 *
 * Bez JavaScriptu widać wszystkie pola i formularz działa tak samo – jest tylko dłuższy.
 */
(function () {
  "use strict";

  function readMap() {
    var node = document.getElementById("broadcast-parameters");
    if (!node) {
      return null;
    }
    try {
      return JSON.parse(node.textContent);
    } catch (error) {
      return null;
    }
  }

  function wire() {
    var form = document.querySelector("[data-broadcast-form]");
    var map = readMap();
    if (!form || !map) {
      return;
    }
    var select = form.querySelector("select[name='group']");
    if (!select) {
      return;
    }
    var fields = form.querySelectorAll("[data-broadcast-param]");

    function sync() {
      var wanted = map[select.value] || null;
      for (var index = 0; index < fields.length; index += 1) {
        fields[index].hidden = fields[index].getAttribute("data-broadcast-param") !== wanted;
      }
    }

    select.addEventListener("change", sync);
    sync();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
