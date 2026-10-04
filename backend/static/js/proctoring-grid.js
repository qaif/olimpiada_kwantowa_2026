/*
 * Siatka nadzorującego (zadanie PROC-01) – SDK livekit-client z static/vendor/ (globalny LivekitClient).
 *
 * Zasady:
 * - lista uczniów przychodzi z serwera (``roster``) i jest już zawężona do zakresu tej osoby;
 *   pokój LiveKit – jednej grupy (token z serwera; opiekun drużyny dostaje wyłącznie pokój swojej
 *   delegacji, więc inne kraje są poza zasięgiem także dla przerobionego skryptu),
 * - ``autoSubscribe: false`` i subskrypcja **tylko kafli widocznej strony** (12/16/24) w najniższej
 *   jakości; zmiana strony odsubskrybowuje poprzednie – łącze nadzorującego i serwera nie płaci za
 *   uczniów, których nikt nie ogląda; ekran ucznia – dopiero po „Ekran”, dźwięk – jednego kafla naraz,
 * - czynności idą POST-em przez platformę (zapis, dziennik, audyt), nie kanałem danych z przeglądarki,
 * - treści z serwera wstawiamy wyłącznie przez textContent.
 */
(function () {
  "use strict";

  var root = document.getElementById("proctoring-grid");
  if (!root) {
    return;
  }
  var T = JSON.parse(document.getElementById("proctoring-grid-strings").textContent);
  var LK = window.LivekitClient;
  var csrf = root.querySelector("input[name=csrfmiddlewaretoken]").value;
  var tiles = root.querySelector("[data-tiles]");
  var statusLine = root.querySelector("[data-status]");
  var groupSelect = root.querySelector("[data-group]");
  var sizeSelect = root.querySelector("[data-size]");
  var pageLabel = root.querySelector("[data-page-label]");
  var audioButton = root.querySelector("[data-start-audio]");
  var dialog = root.querySelector("[data-incident-dialog]");
  var incidentForm = root.querySelector("[data-incident-form]");
  var microphone = root.dataset.microphone === "1";
  var state = { group: groupSelect ? groupSelect.value : "m", page: 1, pages: 1, size: 12, items: [] };
  var visible = new Map(); // identity → session id
  var screens = new Set();
  var audioFocus = "";
  var room = null;
  var incidentFor = null;
  var incidentAt = null;

  function setStatus(text) {
    statusLine.textContent = text;
  }

  function post(url, data) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(data || {}),
    }).then(function (response) {
      return response
        .json()
        .catch(function () {
          return {};
        })
        .then(function (json) {
          if (!response.ok) {
            var error = new Error(json.detail || T.error);
            error.detail = json.detail || T.error;
            throw error;
          }
          return json;
        });
    });
  }

  function actionUrl(id) {
    return root.dataset.actionUrl.replace("/s/0/", "/s/" + id + "/");
  }

  function act(id, data, done) {
    return post(actionUrl(id), data)
      .then(function () {
        setStatus(done || T.saved);
        loadRoster();
      })
      .catch(function (error) {
        setStatus(error.detail);
      });
  }

  // --- kafle ------------------------------------------------------------------------------------

  function button(label, onClick, extra) {
    var element = document.createElement("button");
    element.type = "button";
    element.className = "btn btn--small btn--secondary" + (extra ? " " + extra : "");
    element.textContent = label;
    element.addEventListener("click", onClick);
    return element;
  }

  function renderTile(item) {
    var tile = document.createElement("figure");
    tile.className = "pg__tile";
    tile.dataset.identity = item.identity;
    tile.dataset.status = item.status;
    var media = document.createElement("div");
    media.className = "pg__media";
    tile.appendChild(media);
    var caption = document.createElement("figcaption");
    var name = document.createElement("strong");
    name.textContent = item.label;
    caption.appendChild(name);
    var badges = document.createElement("span");
    badges.className = "pg__badges";
    var status = document.createElement("span");
    status.className = "badge pg__state pg__state--" + item.status;
    status.textContent = T[item.status] || item.status;
    badges.appendChild(status);
    if (!item.consent && item.status !== "alternative") {
      var consent = document.createElement("span");
      consent.className = "badge badge--warn";
      consent.textContent = T.noConsent;
      badges.appendChild(consent);
    }
    if (item.incidents) {
      var incidents = document.createElement("span");
      incidents.className = "badge badge--warn";
      incidents.textContent = T.incidents.replace("%(count)s", item.incidents);
      badges.appendChild(incidents);
    }
    if (item.late) {
      var late = document.createElement("span");
      late.className = "badge badge--warn";
      late.textContent = T.late.replace("%(minutes)s", item.late);
      badges.appendChild(late);
    }
    if (item.unproctored && item.unproctored_reason) {
      var reason = document.createElement("span");
      reason.className = "badge badge--warn";
      reason.textContent = T["reason_" + item.unproctored_reason] || item.unproctored_reason;
      badges.appendChild(reason);
    }
    if (item.attendance !== "unknown") {
      var attendance = document.createElement("span");
      attendance.className = "badge";
      attendance.textContent = item.attendance === "present" ? T.present : T.absent;
      badges.appendChild(attendance);
    }
    caption.appendChild(badges);
    var actions = document.createElement("div");
    actions.className = "pg__actions";
    actions.appendChild(
      button(T.message, function () {
        var body = window.prompt(T.messagePrompt);
        if (body) {
          act(item.id, { action: "message", body: body }, T.sent);
        }
      })
    );
    actions.appendChild(button(T.showRoom, function () { act(item.id, { action: "show_room" }, T.sent); }));
    actions.appendChild(button(T.showId, function () { act(item.id, { action: "show_id" }, T.sent); }));
    actions.appendChild(button(T.present, function () { act(item.id, { action: "present" }); }));
    actions.appendChild(button(T.absent, function () { act(item.id, { action: "absent" }); }));
    actions.appendChild(
      button(
        T.incident,
        function () {
          incidentFor = item.id;
          incidentAt = new Date().toISOString();
          root.querySelector("[data-incident-who]").textContent = "– " + item.label;
          incidentForm.reset();
          dialog.showModal();
        },
        "btn--danger"
      )
    );
    if (item.screen) {
      actions.appendChild(
        button(screens.has(item.identity) ? T.hideScreen : T.screen, function () {
          if (screens.has(item.identity)) {
            screens.delete(item.identity);
          } else {
            screens.add(item.identity);
          }
          render();
        })
      );
    }
    if (microphone) {
      actions.appendChild(
        button(T.audio, function () {
          audioFocus = audioFocus === item.identity ? "" : item.identity;
          updateSubscriptions();
        })
      );
    }
    if (root.dataset.reportUrl) {
      var report = document.createElement("a");
      report.className = "btn btn--small btn--secondary";
      report.href = root.dataset.reportUrl.replace("/s/0/", "/s/" + item.id + "/");
      report.target = "_blank";
      report.rel = "noopener";
      report.textContent = T.report;
      actions.appendChild(report);
    }
    caption.appendChild(actions);
    tile.appendChild(caption);
    return tile;
  }

  function render() {
    // Odświeżenie listy co 15 s nie może migać obrazem: elementy wideo kafli, które zostają na
    // stronie, przenosimy do nowych kafli zamiast podpinać ścieżki od nowa.
    var kept = new Map();
    tiles.querySelectorAll(".pg__tile").forEach(function (tile) {
      kept.set(tile.dataset.identity, tile.querySelector(".pg__media"));
    });
    tiles.textContent = "";
    visible.clear();
    if (!state.items.length) {
      var empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = T.empty;
      tiles.appendChild(empty);
    }
    state.items.forEach(function (item) {
      visible.set(item.identity, item.id);
      var tile = renderTile(item);
      var media = kept.get(item.identity);
      if (media) {
        tile.replaceChild(media, tile.querySelector(".pg__media"));
      }
      tiles.appendChild(tile);
    });
    pageLabel.textContent = T.page.replace("%(page)s", state.page).replace("%(pages)s", state.pages);
    updateSubscriptions();
  }

  // --- LiveKit -----------------------------------------------------------------------------------

  function tileOf(identity) {
    return tiles.querySelector('[data-identity="' + identity + '"] .pg__media');
  }

  function wanted(publication, identity) {
    if (!visible.has(identity)) {
      return false;
    }
    if (publication.source === LK.Track.Source.Camera) {
      return true;
    }
    if (publication.source === LK.Track.Source.ScreenShare) {
      return screens.has(identity);
    }
    if (publication.source === LK.Track.Source.Microphone) {
      return audioFocus === identity;
    }
    return false;
  }

  function updateSubscriptions() {
    if (!room) {
      return;
    }
    room.remoteParticipants.forEach(function (participant) {
      participant.trackPublications.forEach(function (publication) {
        var want = wanted(publication, participant.identity);
        if (publication.isSubscribed !== want) {
          publication.setSubscribed(want);
        }
        if (want && publication.source === LK.Track.Source.Camera && publication.setVideoQuality) {
          publication.setVideoQuality(LK.VideoQuality.LOW);
        }
        if (want && publication.track) {
          attach(publication.track, participant);
        }
      });
    });
  }

  function attach(track, participant) {
    var media = tileOf(participant.identity);
    if (!media) {
      return;
    }
    var marker = "pg-" + track.source;
    if (media.querySelector('[data-track="' + marker + '"]')) {
      return;
    }
    var element = track.attach();
    element.dataset.track = marker;
    if (track.kind === LK.Track.Kind.Audio) {
      element.hidden = true;
      if (!room.canPlaybackAudio && audioButton) {
        audioButton.hidden = false;
      }
    } else {
      element.setAttribute("playsinline", "");
      element.muted = true;
    }
    media.appendChild(element);
  }

  function connect() {
    if (room) {
      room.disconnect();
      room = null;
    }
    if (!LK) {
      return;
    }
    setStatus(T.connecting);
    post(root.dataset.tokenUrl, { group: state.group })
      .then(function (data) {
        room = new LK.Room({ adaptiveStream: false, dynacast: false });
        room.on(LK.RoomEvent.TrackPublished, updateSubscriptions);
        room.on(LK.RoomEvent.ParticipantConnected, updateSubscriptions);
        room.on(LK.RoomEvent.TrackSubscribed, function (track, publication, participant) {
          attach(track, participant);
        });
        room.on(LK.RoomEvent.TrackUnsubscribed, function (track) {
          track.detach().forEach(function (element) {
            element.remove();
          });
        });
        room.on(LK.RoomEvent.AudioPlaybackStatusChanged, function () {
          if (audioButton) {
            audioButton.hidden = room.canPlaybackAudio;
          }
        });
        room.on(LK.RoomEvent.Disconnected, function () {
          setStatus(T.disconnected);
        });
        return room.connect(data.url, data.token, { autoSubscribe: false });
      })
      .then(function () {
        setStatus(T.connected);
        updateSubscriptions();
      })
      .catch(function (error) {
        setStatus((error && error.detail) || T.failed);
      });
  }

  // --- lista ---------------------------------------------------------------------------------------

  function loadRoster() {
    var url =
      root.dataset.rosterUrl +
      "?group=" + encodeURIComponent(state.group) +
      "&page=" + state.page +
      "&size=" + state.size;
    return fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error(T.error);
        }
        return response.json();
      })
      .then(function (data) {
        state.page = data.page;
        state.pages = data.pages;
        state.items = data.items;
        render();
      })
      .catch(function () {
        setStatus(T.error);
      });
  }

  if (groupSelect) {
    groupSelect.addEventListener("change", function () {
      state.group = groupSelect.value;
      state.page = 1;
      screens.clear();
      audioFocus = "";
      loadRoster().then(connect);
    });
  }
  sizeSelect.addEventListener("change", function () {
    state.size = parseInt(sizeSelect.value, 10);
    state.page = 1;
    loadRoster();
  });
  root.querySelector("[data-prev]").addEventListener("click", function () {
    if (state.page > 1) {
      state.page -= 1;
      loadRoster();
    }
  });
  root.querySelector("[data-next]").addEventListener("click", function () {
    if (state.page < state.pages) {
      state.page += 1;
      loadRoster();
    }
  });
  if (audioButton) {
    audioButton.addEventListener("click", function () {
      if (room) {
        room.startAudio().then(function () {
          audioButton.hidden = true;
        });
      }
    });
  }
  incidentForm.addEventListener("submit", function (event) {
    var submitter = event.submitter;
    if (!submitter || submitter.value !== "save" || incidentFor === null) {
      return;
    }
    var data = new FormData(incidentForm);
    act(incidentFor, {
      action: "incident",
      category: data.get("category"),
      severity: data.get("severity"),
      note: data.get("note") || "",
      occurred_at: incidentAt,
    });
  });

  loadRoster().then(connect);
  window.setInterval(loadRoster, 15000);
  window.addEventListener("beforeunload", function () {
    if (room) {
      room.disconnect();
    }
  });
})();
