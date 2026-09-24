/* Wgrywanie filmu albo pliku materiału z warsztatu – z przeglądarki prosto do magazynu (MinIO).
 *
 * Po co: nagranie ma setki megabajtów albo gigabajty, a serwer aplikacji nie może ani trzymać go
 * w pamięci, ani zajmować wątku na czas wgrywania (apps/workshop_materials/storage.py). Serwer
 * tylko zakłada wgrywanie, podpisuje adresy kolejnych części i na końcu sprawdza plik.
 *
 * Umowa z szablonem (templates/web/coordinator/workshop_material_form.html):
 * - form[data-upload-form] z data-start-url i data-list-url,
 * - input[name=kind] (radio: video/file/link), input[data-upload-file],
 * - [data-upload-status] z <progress data-upload-progress> i [data-upload-message],
 * - button[data-upload-submit], button[data-upload-cancel].
 *
 * Kolejność: POST start (formularz bez pliku + nazwa i rozmiar) → podpisy części partiami →
 * PUT każdej części (trzy naraz, trzy próby na część, nowy podpis przy wygaśnięciu) → POST complete.
 * ETagów nie czytamy – listę części serwer bierze od MinIO. Odnośnik („link”) idzie zwykłym POST-em.
 */
(function () {
  "use strict";

  var form = document.querySelector("form[data-upload-form]");
  if (!form || !window.fetch || !window.FormData || !window.XMLHttpRequest) {
    return;
  }

  var CONCURRENCY = 3;
  var MAX_ATTEMPTS = 3;
  var SIGN_BATCH = 20;

  var statusBox = form.querySelector("[data-upload-status]");
  var progress = form.querySelector("[data-upload-progress]");
  var message = form.querySelector("[data-upload-message]");
  var submitButton = form.querySelector("[data-upload-submit]");
  var cancelButton = form.querySelector("[data-upload-cancel]");
  var fileInput = form.querySelector("[data-upload-file]");
  var csrf = form.querySelector("input[name=csrfmiddlewaretoken]").value;

  var state = null;

  function selectedKind() {
    var checked = form.querySelector("input[name=kind]:checked");
    return checked ? checked.value : "";
  }

  function show(text, isError) {
    statusBox.hidden = false;
    message.textContent = text;
    message.classList.toggle("error", !!isError);
  }

  function describeErrors(payload) {
    var lines = [payload && payload.error ? payload.error : "Nie udało się zapisać materiału."];
    var errors = (payload && payload.errors) || {};
    Object.keys(errors).forEach(function (field) {
      var label = form.querySelector('label[for="id_' + field + '"]');
      var name = label ? label.textContent.replace("*", "").trim() + ": " : "";
      lines.push(name + errors[field].join(" "));
    });
    return lines.join(" ");
  }

  function post(url, data) {
    data.append("csrfmiddlewaretoken", csrf);
    return fetch(url, {
      method: "POST",
      body: data,
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return { error: "Serwer odpowiedział nieoczekiwanie (" + response.status + ")." };
        })
        .then(function (payload) {
          if (!response.ok) {
            var error = new Error(describeErrors(payload));
            error.payload = payload;
            throw error;
          }
          return payload;
        });
    });
  }

  function busy(on) {
    submitButton.disabled = on;
    cancelButton.hidden = !on;
    Array.prototype.forEach.call(form.elements, function (element) {
      if (element !== cancelButton && element.type !== "hidden") {
        element.readOnly = on;
      }
    });
  }

  function onBeforeUnload(event) {
    event.preventDefault();
    event.returnValue = "";
  }

  function updateProgress() {
    var loaded = 0;
    Object.keys(state.loaded).forEach(function (key) {
      loaded += state.loaded[key];
    });
    var percent = Math.min(100, Math.floor((loaded / state.file.size) * 100));
    progress.value = percent;
    show("Wgrywanie: " + percent + "% (" + Math.round(loaded / 1048576) + " z " +
      Math.round(state.file.size / 1048576) + " MB). Nie zamykaj tej karty.");
  }

  function sign(numbers) {
    var data = new FormData();
    numbers.forEach(function (number) {
      data.append("part", String(number));
    });
    return post(state.upload.sign_url, data).then(function (payload) {
      Object.keys(payload.urls).forEach(function (key) {
        state.urls[key] = payload.urls[key];
      });
    });
  }

  function ensureSigned(number) {
    if (state.urls[number]) {
      return Promise.resolve();
    }
    var numbers = [];
    for (var n = number; n <= state.upload.parts && numbers.length < SIGN_BATCH; n += 1) {
      if (!state.urls[n]) {
        numbers.push(n);
      }
    }
    return sign(numbers);
  }

  function putPart(number, attempt) {
    if (state.aborted) {
      return Promise.reject(new Error("Wgrywanie przerwane."));
    }
    return ensureSigned(number).then(function () {
      return new Promise(function (resolve, reject) {
        var start = (number - 1) * state.upload.part_size;
        var blob = state.file.slice(start, Math.min(start + state.upload.part_size, state.file.size));
        var xhr = new XMLHttpRequest();
        state.requests[number] = xhr;
        xhr.open("PUT", state.urls[number]);
        xhr.upload.onprogress = function (event) {
          state.loaded[number] = event.loaded;
          updateProgress();
        };
        xhr.onload = function () {
          delete state.requests[number];
          if (xhr.status >= 200 && xhr.status < 300) {
            state.loaded[number] = blob.size;
            updateProgress();
            resolve();
          } else {
            reject({ status: xhr.status });
          }
        };
        xhr.onerror = function () {
          delete state.requests[number];
          reject({ status: 0 });
        };
        xhr.send(blob);
      });
    }).catch(function (error) {
      if (state.aborted || attempt >= MAX_ATTEMPTS || error instanceof Error) {
        throw error instanceof Error ? error : new Error(
          "Część " + number + " pliku nie dotarła do magazynu (kod " + error.status + "). Sprawdź połączenie i spróbuj ponownie."
        );
      }
      // 403 po długim wgrywaniu to zwykle wygasły podpis – przy ponowieniu podpisujemy od nowa.
      delete state.urls[number];
      state.loaded[number] = 0;
      return new Promise(function (resolve) {
        setTimeout(resolve, 1000 * attempt * attempt);
      }).then(function () {
        return putPart(number, attempt + 1);
      });
    });
  }

  function uploadAll() {
    var next = 1;
    function worker() {
      if (state.aborted || next > state.upload.parts) {
        return Promise.resolve();
      }
      var number = next;
      next += 1;
      return putPart(number, 1).then(worker);
    }
    var workers = [];
    for (var i = 0; i < CONCURRENCY; i += 1) {
      workers.push(worker());
    }
    return Promise.all(workers);
  }

  function finish(error) {
    window.removeEventListener("beforeunload", onBeforeUnload);
    busy(false);
    if (error) {
      progress.value = 0;
      show(error.message, true);
    }
  }

  function start(file) {
    var data = new FormData(form);
    data.delete("file");
    data.delete("csrfmiddlewaretoken");
    data.append("filename", file.name);
    data.append("size", String(file.size));
    state = { file: file, urls: {}, loaded: {}, requests: {}, aborted: false, upload: null };
    busy(true);
    window.addEventListener("beforeunload", onBeforeUnload);
    show("Zakładanie wgrywania…");
    post(form.getAttribute("data-start-url"), data)
      .then(function (upload) {
        state.upload = upload;
        updateProgress();
        return uploadAll();
      })
      .then(function () {
        if (state.aborted) {
          throw new Error("Wgrywanie przerwane.");
        }
        show("Sprawdzanie pliku na serwerze…");
        return post(state.upload.complete_url, new FormData());
      })
      .then(function (result) {
        window.removeEventListener("beforeunload", onBeforeUnload);
        window.location.assign(result.redirect || form.getAttribute("data-list-url"));
      })
      .catch(function (error) {
        if (state.upload && !state.aborted) {
          // Porzucamy wgrywanie po stronie serwera, żeby części nie czekały dobę na sprzątanie.
          // Po odrzuceniu w „complete” materiału już nie ma – 404 z abort jest wtedy w porządku.
          post(state.upload.abort_url, new FormData()).catch(function () {});
        }
        finish(error);
      });
  }

  cancelButton.addEventListener("click", function () {
    if (!state || state.aborted) {
      return;
    }
    state.aborted = true;
    Object.keys(state.requests).forEach(function (key) {
      state.requests[key].abort();
    });
    var done = function () {
      finish(new Error("Wgrywanie przerwane – nic nie zostało zapisane."));
    };
    if (state.upload) {
      post(state.upload.abort_url, new FormData()).then(done, done);
    } else {
      done();
    }
  });

  form.addEventListener("submit", function (event) {
    var kind = selectedKind();
    if (kind !== "video" && kind !== "file") {
      return; // odnośnik – zwykły POST formularza
    }
    event.preventDefault();
    var file = fileInput && fileInput.files && fileInput.files[0];
    if (!file) {
      show("Wybierz plik do wgrania.", true);
      return;
    }
    start(file);
  });
})();
