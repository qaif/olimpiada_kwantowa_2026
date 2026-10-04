/*
 * Decyzje moderatora pokoju rozmowy (STAGE-LK-01): „Wpuść ponownie” i „Oddaj głos” dla osób, które
 * moderator wcześniej wyprosił albo wyciszył. Polecenie idzie przez platformę (ten sam adres, co
 * polecenia z listy uczestników), po sukcesie strona się odświeża – lista jest rysowana przez serwer.
 */
(function () {
  "use strict";

  var box = document.getElementById("room-blocks");
  if (!box) {
    return;
  }
  var csrf = document.querySelector("input[name=csrfmiddlewaretoken]").value;
  box.querySelectorAll("[data-block-action]").forEach(function (button) {
    button.addEventListener("click", function () {
      button.disabled = true;
      fetch(box.dataset.controlUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrf, "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ action: button.dataset.blockAction, identity: button.dataset.identity }),
      })
        .then(function (response) {
          if (response.ok) {
            window.location.reload();
          } else {
            button.disabled = false;
          }
        })
        .catch(function () {
          button.disabled = false;
        });
    });
  });
})();
