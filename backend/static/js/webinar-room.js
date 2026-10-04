/*
 * Pokój webinaru (zadanie WEB-01) – interfejs platformy na SDK livekit-client (static/vendor/,
 * globalny ``LivekitClient`` z paczki UMD; bez CDN).
 *
 * Zasady:
 * - token pobieramy POST-em z CSRF (``data-token-url``) – w HTML-u strony go nie ma; odpowiedź
 *   z odmową niesie zdanie dla człowieka (okno, „jeszcze nie rozpoczęty”), które pokazujemy,
 * - uprawnienia rozstrzyga serwer LiveKit z tokenu: widz ma ``canPublish=false``; przyciski mikrofonu,
 *   kamery i ekranu włączają się dopiero, gdy serwer zmieni uprawnienia (``ParticipantPermissionsChanged``
 *   po „Daj głos” prowadzącego – polecenie idzie przez **nasz** widok ``data-control-url``),
 * - czat i podniesiona ręka – kanał danych LiveKit (``publishData``), tematy ``chat`` i ``hand``;
 *   treść wstawiamy wyłącznie przez ``textContent``, nigdy jako HTML,
 * - wszystkie napisy z ``json_script`` (``webinar-room-strings``) – w języku strony (11 języków, RTL
 *   obsługuje CSS na właściwościach logicznych).
 */
(function () {
  "use strict";

  var root = document.getElementById("webinar-room");
  var LK = window.LivekitClient;
  if (!root || !LK) {
    return;
  }
  var T = JSON.parse(document.getElementById("webinar-room-strings").textContent);
  var stage = root.querySelector("[data-stage]");
  var people = root.querySelector("[data-people]");
  var count = root.querySelector("[data-count]");
  var chat = root.querySelector("[data-chat]");
  var chatForm = root.querySelector("[data-chat-form]");
  var chatInput = root.querySelector("[data-chat-input]");
  var chatSend = root.querySelector("[data-chat-send]");
  var statusLine = root.querySelector("[data-status]");
  var recordingBadge = root.querySelector("[data-recording-badge]");
  var startAudio = root.querySelector("[data-start-audio]");
  var csrf = root.querySelector("input[name=csrfmiddlewaretoken]").value;
  var isPresenter = root.dataset.role === "presenter";
  var controlUrl = root.dataset.controlUrl || "";
  var encoder = new TextEncoder();
  var decoder = new TextDecoder();
  var hands = new Set();
  var handRaised = false;
  var room = null;
  var leaving = false;

  function button(name) {
    return root.querySelector('[data-action="' + name + '"]');
  }

  function setStatus(text) {
    statusLine.textContent = text;
  }

  function post(url, data) {
    var body = new URLSearchParams(data || {});
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrf, "Content-Type": "application/x-www-form-urlencoded" },
      body: body,
    }).then(function (response) {
      return response.json().catch(function () {
        return {};
      }).then(function (json) {
        if (!response.ok) {
          var error = new Error(json.detail || T.error);
          error.detail = json.detail || T.error;
          throw error;
        }
        return json;
      });
    });
  }

  function control(action, identity) {
    if (!controlUrl) {
      return Promise.resolve();
    }
    return post(controlUrl, { action: action, identity: identity || "" }).catch(function (error) {
      setStatus(error.detail || T.error);
    });
  }

  // --- kafelki obrazu --------------------------------------------------------------------------

  function tileId(participant, source) {
    return "wr-tile-" + participant.identity + "-" + source;
  }

  function tileFor(participant, source) {
    var id = tileId(participant, source);
    var tile = document.getElementById(id);
    if (!tile) {
      tile = document.createElement("figure");
      tile.id = id;
      tile.className = "wr__tile";
      if (source === LK.Track.Source.ScreenShare) {
        tile.classList.add("wr__tile--screen");
      }
      var caption = document.createElement("figcaption");
      caption.className = "wr__tile-name";
      caption.textContent =
        source === LK.Track.Source.ScreenShare
          ? T.screenOf.replace("%(name)s", participant.name || participant.identity)
          : participant.name || participant.identity;
      tile.appendChild(caption);
      stage.appendChild(tile);
    }
    return tile;
  }

  function attachTrack(track, participant) {
    if (track.kind === LK.Track.Kind.Audio) {
      if (participant !== room.localParticipant) {
        var audio = track.attach();
        audio.hidden = true;
        root.appendChild(audio);
      }
      return;
    }
    var tile = tileFor(participant, track.source);
    var element = track.attach();
    element.setAttribute("playsinline", "");
    if (participant === room.localParticipant) {
      element.muted = true;
    }
    tile.insertBefore(element, tile.firstChild);
    updateLayout();
  }

  function detachTrack(track, participant) {
    track.detach().forEach(function (element) {
      element.remove();
    });
    if (track.kind !== LK.Track.Kind.Audio) {
      var tile = document.getElementById(tileId(participant, track.source));
      if (tile) {
        tile.remove();
      }
    }
    updateLayout();
  }

  function updateLayout() {
    var speaker = stage.dataset.layout === "speaker";
    var main = stage.querySelector(".wr__tile--screen") || stage.querySelector(".wr__tile--speaking");
    stage.querySelectorAll(".wr__tile").forEach(function (tile) {
      tile.classList.toggle("wr__tile--main", speaker && tile === main);
    });
  }

  // --- lista uczestników -------------------------------------------------------------------------

  function allParticipants() {
    var list = [room.localParticipant];
    room.remoteParticipants.forEach(function (participant) {
      list.push(participant);
    });
    return list;
  }

  function canPublish(participant) {
    return Boolean(participant.permissions && participant.permissions.canPublish);
  }

  function renderPeople() {
    people.textContent = "";
    var list = allParticipants();
    count.textContent = "(" + list.length + ")";
    list.forEach(function (participant) {
      var item = document.createElement("li");
      item.className = "wr__person";
      var name = document.createElement("span");
      name.textContent =
        (participant.name || participant.identity) + (participant === room.localParticipant ? " (" + T.you + ")" : "");
      item.appendChild(name);
      if (canPublish(participant)) {
        var badge = document.createElement("span");
        badge.className = "badge badge--info";
        badge.textContent = T.presenter;
        item.appendChild(badge);
      }
      if (hands.has(participant.identity)) {
        var hand = document.createElement("span");
        hand.className = "badge badge--warn";
        hand.textContent = T.hand;
        item.appendChild(hand);
      }
      if (isPresenter && participant !== room.localParticipant) {
        var actions = document.createElement("span");
        actions.className = "wr__person-actions";
        var toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "btn btn--small btn--secondary";
        toggle.textContent = canPublish(participant) ? T.revoke : T.grant;
        toggle.addEventListener("click", function () {
          control(canPublish(participant) ? "listener" : "speaker", participant.identity);
        });
        actions.appendChild(toggle);
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "btn btn--small btn--danger";
        remove.textContent = T.remove;
        remove.addEventListener("click", function () {
          if (window.confirm(T.removeConfirm)) {
            control("remove", participant.identity);
          }
        });
        actions.appendChild(remove);
        item.appendChild(actions);
      }
      people.appendChild(item);
    });
  }

  // --- kanał danych: czat i ręka -------------------------------------------------------------------

  function send(topic, payload, destinations) {
    var options = { reliable: true, topic: topic };
    if (destinations) {
      options.destinationIdentities = destinations;
    }
    return room.localParticipant.publishData(encoder.encode(JSON.stringify(payload)), options);
  }

  // Znacznik nagrywania widoczny przez cały czas nagrania (nie tylko w pasku stanu, który zmienia
  // się przy każdym zdarzeniu) – uczestnik ma wiedzieć, że jest nagrywany.
  function syncRecording() {
    recordingBadge.hidden = !(room && room.isRecording);
  }

  // Przeglądarka blokuje odtwarzanie dźwięku bez gestu użytkownika (autoplay). SDK zgłasza to
  // zdarzeniem; przycisk „Włącz dźwięk” wywołuje ``room.startAudio()`` w obsłudze kliknięcia.
  function syncAudio() {
    startAudio.hidden = !room || room.canPlaybackAudio;
  }

  startAudio.addEventListener("click", function () {
    if (room) {
      room.startAudio().then(syncAudio, syncAudio);
    }
  });

  function resetStage() {
    stage.textContent = "";
    people.textContent = "";
    root.querySelectorAll("audio").forEach(function (element) {
      element.remove();
    });
    hands.clear();
  }

  function offerRetry(message) {
    setStatus(message);
    var retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn--small btn--primary";
    retry.textContent = T.retry;
    retry.addEventListener("click", function () {
      retry.remove();
      connect();
    });
    statusLine.appendChild(document.createTextNode(" "));
    statusLine.appendChild(retry);
  }

  function addChat(name, text) {
    var item = document.createElement("li");
    var who = document.createElement("strong");
    who.textContent = name + ": ";
    var body = document.createElement("span");
    body.textContent = text;
    item.appendChild(who);
    item.appendChild(body);
    chat.appendChild(item);
    chat.scrollTop = chat.scrollHeight;
  }

  function onData(payload, participant, kind, topic) {
    var message;
    try {
      message = JSON.parse(decoder.decode(payload));
    } catch (err) {
      return;
    }
    if (!participant || typeof message !== "object" || message === null) {
      return;
    }
    if (topic === "chat" && typeof message.text === "string") {
      addChat(participant.name || participant.identity, message.text.slice(0, 500));
    } else if (topic === "hand") {
      if (message.raised) {
        hands.add(participant.identity);
      } else {
        hands.delete(participant.identity);
      }
      renderPeople();
    }
  }

  // --- przyciski ---------------------------------------------------------------------------------

  function syncControls() {
    var publish = canPublish(room.localParticipant);
    ["mic", "cam", "screen"].forEach(function (name) {
      button(name).disabled = !publish;
    });
    var local = room.localParticipant;
    button("mic").setAttribute("aria-pressed", String(local.isMicrophoneEnabled));
    button("cam").setAttribute("aria-pressed", String(local.isCameraEnabled));
    button("screen").setAttribute("aria-pressed", String(local.isScreenShareEnabled));
    button("screen").textContent = local.isScreenShareEnabled ? T.stopScreen : T.screen;
    button("hand").disabled = publish;
    button("layout").disabled = false;
    chatInput.disabled = false;
    chatSend.disabled = false;
    var record = button("record");
    if (record) {
      record.disabled = false;
      record.setAttribute("aria-pressed", String(Boolean(room.isRecording)));
      record.textContent = room.isRecording ? T.stopRecord : T.record;
    }
  }

  function toggle(name, action) {
    button(name).addEventListener("click", function () {
      action().then(syncControls, function (error) {
        setStatus((error && error.message) || T.error);
        syncControls();
      });
    });
  }

  toggle("mic", function () {
    return room.localParticipant.setMicrophoneEnabled(!room.localParticipant.isMicrophoneEnabled);
  });
  toggle("cam", function () {
    return room.localParticipant.setCameraEnabled(!room.localParticipant.isCameraEnabled);
  });
  toggle("screen", function () {
    return room.localParticipant.setScreenShareEnabled(!room.localParticipant.isScreenShareEnabled);
  });
  toggle("hand", function () {
    handRaised = !handRaised;
    button("hand").textContent = handRaised ? T.lowerHand : T.raiseHand;
    button("hand").setAttribute("aria-pressed", String(handRaised));
    if (handRaised) {
      hands.add(room.localParticipant.identity);
    } else {
      hands.delete(room.localParticipant.identity);
    }
    renderPeople();
    return send("hand", { raised: handRaised });
  });
  button("layout").addEventListener("click", function () {
    var speaker = stage.dataset.layout !== "speaker";
    stage.dataset.layout = speaker ? "speaker" : "grid";
    button("layout").textContent = speaker ? T.gridView : T.speakerView;
    button("layout").setAttribute("aria-pressed", String(speaker));
    updateLayout();
  });
  if (button("record")) {
    button("record").addEventListener("click", function () {
      button("record").disabled = true;
      control(room.isRecording ? "record_stop" : "record_start").then(syncControls);
    });
  }
  chatForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var text = chatInput.value.trim().slice(0, 500);
    if (!text) {
      return;
    }
    send("chat", { text: text }).then(function () {
      addChat(T.you, text);
      chatInput.value = "";
    });
  });

  // --- połączenie --------------------------------------------------------------------------------

  function connect() {
    // Ponowne połączenie zaczyna od zera: stary pokój rozłączony i bez nasłuchów, scena wyczyszczona.
    if (room) {
      var old = room;
      room = null;
      old.removeAllListeners();
      old.disconnect();
    }
    resetStage();
    handRaised = false;
    button("hand").textContent = T.raiseHand;
    button("hand").setAttribute("aria-pressed", "false");
    setStatus(T.connecting);
    post(root.dataset.tokenUrl)
      .then(function (data) {
        room = new LK.Room({ adaptiveStream: true, dynacast: true });
        room
          .on(LK.RoomEvent.TrackSubscribed, function (track, publication, participant) {
            attachTrack(track, participant);
          })
          .on(LK.RoomEvent.TrackUnsubscribed, function (track, publication, participant) {
            detachTrack(track, participant);
          })
          .on(LK.RoomEvent.LocalTrackPublished, function (publication) {
            attachTrack(publication.track, room.localParticipant);
            syncControls();
          })
          .on(LK.RoomEvent.LocalTrackUnpublished, function (publication) {
            detachTrack(publication.track, room.localParticipant);
            syncControls();
          })
          .on(LK.RoomEvent.ParticipantConnected, function (participant) {
            // Prowadzący, który dołączył później, nie widział wcześniejszego „podnieś rękę” –
            // ręka jest stanem, więc wysyłamy ją nowej osobie jeszcze raz.
            if (handRaised) {
              send("hand", { raised: true }, [participant.identity]);
            }
            renderPeople();
          })
          .on(LK.RoomEvent.AudioPlaybackStatusChanged, syncAudio)
          .on(LK.RoomEvent.ParticipantDisconnected, function (participant) {
            hands.delete(participant.identity);
            renderPeople();
          })
          .on(LK.RoomEvent.ActiveSpeakersChanged, function (speakers) {
            var speaking = new Set(
              speakers.map(function (participant) {
                return participant.identity;
              })
            );
            stage.querySelectorAll(".wr__tile").forEach(function (tile) {
              var identity = tile.id.replace(/^wr-tile-/, "").replace(/-[^-]+$/, "");
              tile.classList.toggle("wr__tile--speaking", speaking.has(identity));
            });
            updateLayout();
          })
          .on(LK.RoomEvent.ParticipantPermissionsChanged, function (previous, participant) {
            if (participant === room.localParticipant) {
              var now = canPublish(participant);
              setStatus(now ? T.speakerGranted : T.speakerRevoked);
              if (now && handRaised) {
                button("hand").click();
              }
              if (!now) {
                participant.setMicrophoneEnabled(false);
                participant.setCameraEnabled(false);
                participant.setScreenShareEnabled(false);
              }
              syncControls();
            }
            renderPeople();
          })
          .on(LK.RoomEvent.RecordingStatusChanged, function () {
            setStatus(room.isRecording ? T.recording : T.connected);
            syncRecording();
            syncControls();
          })
          .on(LK.RoomEvent.DataReceived, onData)
          .on(LK.RoomEvent.Disconnected, function () {
            ["mic", "cam", "screen", "hand"].forEach(function (name) {
              button(name).disabled = true;
            });
            recordingBadge.hidden = true;
            if (!leaving) {
              // Zerwane połączenie (sieć, usunięcie z pokoju, koniec webinaru) – nowy token przez
              // platformę rozstrzygnie, czy wolno wrócić (usunięty i po „Zakończ” dostanie odmowę).
              offerRetry(T.disconnected);
            }
          });
        return room.connect(data.url, data.token).then(function () {
          setStatus(room.isRecording ? T.recording : T.connected);
          room.remoteParticipants.forEach(function (participant) {
            participant.trackPublications.forEach(function (publication) {
              if (publication.track) {
                attachTrack(publication.track, participant);
              }
            });
          });
          renderPeople();
          syncControls();
          syncRecording();
          syncAudio();
          if (isPresenter) {
            room.localParticipant.setMicrophoneEnabled(true).then(syncControls, syncControls);
          }
        });
      })
      .catch(function (error) {
        offerRetry((error && error.detail) || T.failed);
      });
  }

  root.querySelector("[data-leave]").addEventListener("click", function () {
    leaving = true;
    if (room) {
      room.disconnect();
    }
  });
  window.addEventListener("pagehide", function () {
    leaving = true;
    if (room) {
      room.disconnect();
    }
  });

  connect();
})();
