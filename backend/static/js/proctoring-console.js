/*
 * Konsola nadzoru ucznia (zadanie PROC-01) – sprawdzenie sprzętu, zdjęcie dokumentu, nadawanie kamery.
 *
 * Zasady:
 * - kroki rozstrzyga serwer; po udanym kroku przeładowujemy stronę (serwer pokaże następny),
 * - token pobieramy POST-em z CSRF – w HTML-u strony go nie ma,
 * - kamera 320×240, 10 kl./s, ≤ 150 kb/s, bez simulcastu (oszczędność łącza ucznia i serwera);
 *   ekran 2 kl./s, ≤ 300 kb/s – tylko gdy etap go wymaga,
 * - po publikacji serwer sam sprawdza w LiveKit, że kamera nadaje („started”) – dopiero wtedy etap
 *   się otwiera; zerwanie = czerwony komunikat, zdarzenie w dzienniku i ponowne łączenie,
 * - druga karta (strona etapu) dostaje stan przez BroadcastChannel – pasek ostrzeżenia,
 * - wiadomości od nadzorujących: kanał danych LiveKit (temat „proctoring”) i odpytanie serwera
 *   co 20 s; treść wyłącznie przez textContent,
 * - nie zbieramy niczego poza tym, co wysyłamy jawnie: wartości logiczne sprawdzenia i rodzinę
 *   przeglądarki (bez wersji, systemu, rozdzielczości).
 */
(function () {
  "use strict";

  var root = document.getElementById("proctoring-console");
  if (!root) {
    return;
  }
  var T = JSON.parse(document.getElementById("proctoring-console-strings").textContent);
  var LK = window.LivekitClient;
  var csrf = root.querySelector("input[name=csrfmiddlewaretoken]").value;
  var needScreen = root.dataset.screen === "1";
  var needMic = root.dataset.microphone === "1";
  var allowUnproctored = root.dataset.allowUnproctored === "1";
  var statusLine = root.querySelector("[data-status]");
  var alertLine = root.querySelector("[data-alert]");
  var goStage = root.querySelector("[data-go-stage]");
  var unproctoredButton = root.querySelector("[data-unproctored]");
  var screenButton = root.querySelector("[data-share-screen]");
  var startButton = root.querySelector("[data-start]");
  var messagesList = root.querySelector("[data-messages]");
  var channel = "BroadcastChannel" in window ? new BroadcastChannel("proctoring-" + root.dataset.stageId) : null;
  var room = null;
  var state = "idle";
  var lastMessage = 0;
  var shown = new Set();
  var reconnectTimer = null;

  function actionUrl(name) {
    return root.dataset.actionUrl.replace("ACTION", name);
  }

  function request(url, options) {
    options = options || {};
    var headers = Object.assign({ "X-CSRFToken": csrf }, options.headers || {});
    return fetch(url, {
      method: options.method || "POST",
      credentials: "same-origin",
      headers: headers,
      body: options.body,
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
            error.status = response.status;
            throw error;
          }
          return json;
        });
    });
  }

  function form(data) {
    return {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(data || {}),
    };
  }

  function setStatus(text) {
    if (statusLine) {
      statusLine.textContent = text;
    }
  }

  function setAlert(text) {
    if (!alertLine) {
      return;
    }
    alertLine.textContent = text || "";
    alertLine.hidden = !text;
  }

  function broadcast() {
    if (channel) {
      channel.postMessage({ state: state, at: Date.now() });
    }
  }

  function browserFamily() {
    var ua = navigator.userAgent;
    if (/Edg\//.test(ua)) return "edge";
    if (/OPR\//.test(ua)) return "opera";
    if (/Firefox\//.test(ua)) return "firefox";
    if (/Chrome\//.test(ua)) return "chrome";
    if (/Safari\//.test(ua)) return "safari";
    return "other";
  }

  // --- sprawdzenie sprzętu ------------------------------------------------------------------------

  function mark(name, value) {
    var cell = root.querySelector('[data-check="' + name + '"]');
    if (cell) {
      cell.textContent = value === null ? T.notRequired : value ? T.ok : T.missing;
      cell.dataset.state = value === null ? "na" : value ? "ok" : "missing";
    }
  }

  function runCheck() {
    var result = { webrtc: false, camera: false, microphone: false, screen: false, browser: browserFamily() };
    var supported =
      "RTCPeerConnection" in window && navigator.mediaDevices && navigator.mediaDevices.getUserMedia;
    result.webrtc = Boolean(supported);
    mark("webrtc", result.webrtc);
    if (!supported) {
      setAlert(T.unsupported);
      return send(result);
    }
    var preview = root.querySelector("[data-preview]");
    return navigator.mediaDevices
      .getUserMedia({ video: { width: 320, height: 240, frameRate: 10 }, audio: needMic })
      .then(function (stream) {
        result.camera = stream.getVideoTracks().length > 0;
        result.microphone = stream.getAudioTracks().length > 0;
        if (preview) {
          preview.srcObject = stream;
          preview.play().catch(function () {});
        }
        mark("camera", result.camera);
        mark("microphone", needMic ? result.microphone : null);
        window.setTimeout(function () {
          stream.getTracks().forEach(function (track) {
            track.stop();
          });
        }, 4000);
      })
      .catch(function () {
        mark("camera", false);
        setAlert(T.cameraDenied);
      })
      .then(function () {
        if (!needScreen) {
          mark("screen", null);
          return;
        }
        if (!navigator.mediaDevices.getDisplayMedia) {
          mark("screen", false);
          return;
        }
        return navigator.mediaDevices
          .getDisplayMedia({ video: true })
          .then(function (stream) {
            result.screen = true;
            stream.getTracks().forEach(function (track) {
              track.stop();
            });
            mark("screen", true);
          })
          .catch(function () {
            mark("screen", false);
          });
      })
      .then(function () {
        return send(result);
      });
  }

  function send(result) {
    return request(actionUrl("check"), {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(result),
    })
      .then(function (json) {
        if (json.passed) {
          window.location.reload();
        }
      })
      .catch(function (error) {
        setAlert(error.detail);
      });
  }

  // --- zdjęcie dokumentu -------------------------------------------------------------------------

  function takePhoto() {
    var canvas = root.querySelector("[data-photo-canvas]");
    navigator.mediaDevices
      .getUserMedia({ video: { width: 640, height: 480 } })
      .then(function (stream) {
        var video = document.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.srcObject = stream;
        return video.play().then(function () {
          return new Promise(function (resolve) {
            window.setTimeout(function () {
              canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
              stream.getTracks().forEach(function (track) {
                track.stop();
              });
              canvas.toBlob(resolve, "image/jpeg", 0.8);
            }, 1500);
          });
        });
      })
      .then(function (blob) {
        return request(actionUrl("photo"), { headers: { "Content-Type": "image/jpeg" }, body: blob });
      })
      .then(function () {
        setStatus(T.photoSaved);
        window.location.reload();
      })
      .catch(function (error) {
        setAlert(error.detail || T.cameraDenied);
      });
  }

  // --- nadawanie ---------------------------------------------------------------------------------

  function showReady() {
    if (goStage) {
      goStage.hidden = false;
    }
  }

  // Nieudane połączenie zgłaszamy serwerowi z powodem (token / connect / publish / camera). Przycisk
  // „Kontynuuj bez nadzoru” pokazujemy WYŁĄCZNIE, gdy serwer odpowie, że wolno (awaria nadzoru albo
  // kilka nieudanych połączeń przy działającym serwerze). Kamera odmówiona albo odłączona to nie
  // awaria serwera – wtedy prowadzimy do prośby o inną formę nadzoru.
  function unavailable(detail, reason) {
    state = "unavailable";
    broadcast();
    if (startButton) {
      startButton.disabled = false;
      startButton.textContent = T.retry;
    }
    if (reason === "camera") {
      setAlert(T.cameraUnavailable);
    } else {
      setAlert(detail || T.unavailable);
    }
    request(actionUrl("event"), form({ kind: "connect_failed", reason: reason || "connect" }))
      .then(function (json) {
        if (unproctoredButton && allowUnproctored && json.unproctored_allowed) {
          unproctoredButton.hidden = false;
        }
      })
      .catch(function () {});
  }

  function confirmStarted(attempt) {
    return request(actionUrl("started"))
      .then(function () {
        state = "live";
        setAlert("");
        setStatus(T.live);
        showReady();
        broadcast();
      })
      .catch(function (error) {
        if (error.status === 409 && attempt < 6) {
          return new Promise(function (resolve) {
            window.setTimeout(resolve, 2000);
          }).then(function () {
            return confirmStarted(attempt + 1);
          });
        }
        if (error.status === 502) {
          return unavailable(error.detail, "connect");
        }
        setAlert(error.detail);
      });
  }

  function publishScreen() {
    if (!room) {
      return;
    }
    room.localParticipant
      .setScreenShareEnabled(true, { video: { frameRate: 2 }, audio: false }, {
        videoEncoding: { maxBitrate: 300000, maxFramerate: 2 },
        simulcast: false,
      })
      .then(function () {
        if (screenButton) {
          screenButton.hidden = true;
        }
        return confirmStarted(0);
      })
      .catch(function () {
        setAlert(T.screenStopped);
      });
  }

  function onDisconnected() {
    if (state === "stopped") {
      return;
    }
    state = "dropped";
    broadcast();
    setAlert(T.dropped);
    request(actionUrl("event"), form({ kind: "stream_dropped" })).catch(function () {});
    if (!reconnectTimer) {
      reconnectTimer = window.setTimeout(function () {
        reconnectTimer = null;
        start(true);
      }, 5000);
    }
  }

  function onData(payload, participant, kind, topic) {
    if (topic && topic !== "proctoring") {
      return;
    }
    try {
      var data = JSON.parse(new TextDecoder().decode(payload));
      if (data.t === "msg") {
        showMessage({ id: data.id, kind: data.k, body: data.b });
      }
    } catch (error) {
      /* zignorowane – nieczytelny pakiet */
    }
  }

  function start(isRetry) {
    var phase = "token";
    if (!LK) {
      return unavailable(null, "connect");
    }
    if (startButton) {
      startButton.disabled = true;
    }
    setStatus(T.connecting);
    request(root.dataset.tokenUrl)
      .then(function (data) {
        phase = "connect";
        room = new LK.Room({ adaptiveStream: false, dynacast: false });
        room.on(LK.RoomEvent.Disconnected, onDisconnected);
        room.on(LK.RoomEvent.Reconnecting, function () {
          state = "dropped";
          broadcast();
          setAlert(T.dropped);
        });
        room.on(LK.RoomEvent.Reconnected, function () {
          state = "live";
          broadcast();
          setAlert("");
          setStatus(T.reconnected);
          request(actionUrl("event"), form({ kind: "reconnected" })).catch(function () {});
        });
        room.on(LK.RoomEvent.LocalTrackUnpublished, function (publication) {
          if (publication.source === LK.Track.Source.ScreenShare && needScreen) {
            setAlert(T.screenStopped);
            if (screenButton) {
              screenButton.hidden = false;
            }
          }
          if (publication.source === LK.Track.Source.Camera) {
            onDisconnected();
          }
        });
        room.on(LK.RoomEvent.DataReceived, onData);
        return room.connect(data.url, data.token, { autoSubscribe: false });
      })
      .then(function () {
        phase = "camera";
        return LK.createLocalVideoTrack({ resolution: { width: 320, height: 240, frameRate: 10 } });
      })
      .then(function (track) {
        phase = "publish";
        var live = root.querySelector("[data-live]");
        if (live) {
          track.attach(live);
        }
        return room.localParticipant.publishTrack(track, {
          simulcast: false,
          videoEncoding: { maxBitrate: 150000, maxFramerate: 10 },
        });
      })
      .then(function () {
        if (needMic) {
          return room.localParticipant.setMicrophoneEnabled(true);
        }
      })
      .then(function () {
        if (isRetry) {
          request(actionUrl("event"), form({ kind: "reconnected" })).catch(function () {});
        }
        if (needScreen) {
          if (screenButton) {
            screenButton.hidden = false;
          }
          setStatus(T.shareScreen);
          return;
        }
        return confirmStarted(0);
      })
      .catch(function (error) {
        if (error && error.status && error.status !== 502) {
          setAlert(error.detail);
          if (startButton) {
            startButton.disabled = false;
          }
          return;
        }
        unavailable(error && error.detail, phase);
      });
  }

  function continueUnproctored() {
    request(actionUrl("unproctored"))
      .then(function () {
        state = "unproctored";
        broadcast();
        setAlert("");
        showReady();
      })
      .catch(function (error) {
        setAlert(error.detail);
      });
  }

  // --- wiadomości ---------------------------------------------------------------------------------

  function showMessage(message) {
    if (!messagesList || shown.has(message.id)) {
      return;
    }
    shown.add(message.id);
    lastMessage = Math.max(lastMessage, message.id || 0);
    var item = document.createElement("li");
    item.className = "pc__message";
    var title = document.createElement("strong");
    title.textContent = T.message;
    item.appendChild(title);
    var text = document.createElement("p");
    text.textContent =
      message.kind === "show_room" ? T.showRoom : message.kind === "show_id" ? T.showId : message.body;
    item.appendChild(text);
    var ack = document.createElement("button");
    ack.type = "button";
    ack.className = "btn btn--small btn--primary";
    ack.textContent = T.ack;
    ack.addEventListener("click", function () {
      request(actionUrl("ack"), form({ id: message.id })).catch(function () {});
      item.classList.add("pc__message--seen");
      ack.remove();
    });
    item.appendChild(ack);
    messagesList.prepend(item);
    if (navigator.vibrate) {
      navigator.vibrate(200);
    }
  }

  function poll() {
    request(root.dataset.messagesUrl + "?after=" + lastMessage, { method: "GET" })
      .then(function (json) {
        (json.messages || []).forEach(showMessage);
        if (json.ready) {
          showReady();
        }
      })
      .catch(function () {});
  }

  // --- przyciski ----------------------------------------------------------------------------------

  var checkButton = root.querySelector("[data-run-check]");
  if (checkButton) {
    checkButton.addEventListener("click", function () {
      checkButton.disabled = true;
      setAlert("");
      runCheck().then(function () {
        checkButton.disabled = false;
      });
    });
  }
  var photoButton = root.querySelector("[data-take-photo]");
  if (photoButton) {
    photoButton.addEventListener("click", takePhoto);
  }
  if (startButton) {
    startButton.addEventListener("click", function () {
      start(false);
    });
  }
  if (screenButton) {
    screenButton.addEventListener("click", publishScreen);
  }
  if (unproctoredButton) {
    unproctoredButton.addEventListener("click", continueUnproctored);
  }

  if (root.dataset.step === "start" || root.dataset.step === "live") {
    poll();
    window.setInterval(poll, 20000);
    window.setInterval(function () {
      if (state === "live") {
        request(actionUrl("event"), form({ heartbeat: "1" })).catch(function () {});
      }
    }, 60000);
    window.setInterval(broadcast, 5000);
  }
  window.addEventListener("beforeunload", function () {
    state = "stopped";
    broadcast();
    if (room) {
      room.disconnect();
    }
  });
})();
