// Trinetra pages: small progressive enhancements. Every page works without this file.
(function () {
  "use strict";

  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, value);
    } catch (e) { /* private mode or blocked storage: just do not remember */ }
    return null;
  }

  // Copy buttons: <button data-copy="text">
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-copy]");
    if (!button) return;
    var text = button.getAttribute("data-copy");
    var done = function () {
      var label = button.textContent;
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = label; }, 1500);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () {});
    } else {
      var area = document.createElement("textarea");
      area.value = text; area.style.position = "fixed"; area.style.opacity = "0";
      document.body.appendChild(area); area.select();
      try { document.execCommand("copy"); done(); } catch (e) {}
      document.body.removeChild(area);
    }
  });

  // Confirm before destructive actions: <form data-confirm="Are you sure?">
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute && form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) event.preventDefault();
  });

  // File drop zones: show the chosen file, accept drag and drop.
  document.querySelectorAll(".dropzone").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]");
    var chosen = zone.querySelector(".chosen");
    if (!input) return;
    var show = function () { if (chosen) chosen.textContent = input.files.length ? input.files[0].name : ""; };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.remove("is-over"); });
    });
    zone.addEventListener("drop", function (e) {
      if (e.dataTransfer && e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); }
    });
  });

  // Hindi / English toggle on public pages: swaps [data-en]/[data-hi] text and sets the reply language.
  var toggle = document.querySelector("[data-lang-toggle]");
  if (toggle) {
    var apply = function (lang) {
      document.documentElement.lang = lang;
      document.querySelectorAll("[data-" + lang + "]").forEach(function (el) {
        var value = el.getAttribute("data-" + lang);
        if (el.hasAttribute("placeholder")) el.setAttribute("placeholder", value); else el.textContent = value;
      });
      toggle.querySelectorAll("button").forEach(function (b) {
        b.setAttribute("aria-pressed", b.getAttribute("data-set-lang") === lang ? "true" : "false");
      });
      var field = document.querySelector("input[name=lang]");
      if (field && toggle.dataset.touched) field.value = lang;
    };
    toggle.addEventListener("click", function (e) {
      var b = e.target.closest("[data-set-lang]");
      if (!b) return;
      toggle.dataset.touched = "1";
      store("trinetra-lang", b.getAttribute("data-set-lang"));
      apply(b.getAttribute("data-set-lang"));
    });
    var saved = store("trinetra-lang") || toggle.getAttribute("data-initial");
    if (saved === "hi" || saved === "en") {
      if (store("trinetra-lang")) toggle.dataset.touched = "1";
      apply(saved);
    }
  }

  // Voice note recorder: <div data-recorder> inside a form with a file input. Tap to start, tap to stop
  // (60 seconds at most), then "Check this" sends the recording through the same upload as a chosen file.
  document.querySelectorAll("[data-recorder]").forEach(function (box) {
    var form = box.closest("form");
    var input = form && form.querySelector("input[type=file]");
    var toggleBtn = box.querySelector("[data-rec-toggle]");
    var state = box.querySelector("[data-rec-state]");
    var clock = box.querySelector("[data-rec-time]");
    var ready = box.querySelector("[data-rec-ready]");
    var player = box.querySelector("audio");
    var sendBtn = box.querySelector("[data-rec-send]");
    var error = box.querySelector("[data-rec-error]");
    var MAX_SECONDS = 60;
    var recorder = null, chunks = [], started = 0, ticker = null, blob = null, stream = null;
    var say = function (el) {
      var lang = document.documentElement.lang === "hi" ? "hi" : "en";
      return el.getAttribute("data-" + lang) || el.textContent;
    };
    var label = function (key) { return say(box.querySelector("[data-label-" + key + "]")); };
    var fail = function () {
      box.classList.remove("is-recording");
      state.hidden = true;
      ready.hidden = true;
      error.hidden = false;
      toggleBtn.hidden = true;  // mic denied or unsupported: only the "can't record here" message
    };
    var supported = !!(window.MediaRecorder && navigator.mediaDevices && navigator.mediaDevices.getUserMedia
                       && window.isSecureContext && input);
    box.hidden = false;
    if (!supported) { fail(); return; }

    var pickType = function () {
      var types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
      for (var i = 0; i < types.length; i++) {
        if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(types[i])) return types[i];
      }
      return "";
    };
    var fmt = function (s) { return Math.floor(s / 60) + ":" + ("0" + Math.floor(s % 60)).slice(-2); };
    var stop = function () {
      if (recorder && recorder.state === "recording") recorder.stop();
      clearInterval(ticker);
    };
    var start = function () {
      navigator.mediaDevices.getUserMedia({ audio: true }).then(function (s) {
        stream = s;
        var type = pickType();
        try { recorder = type ? new MediaRecorder(s, { mimeType: type }) : new MediaRecorder(s); }
        catch (e) { fail(); return; }
        chunks = [];
        recorder.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
        recorder.onstop = function () {
          stream.getTracks().forEach(function (t) { t.stop(); });
          blob = new Blob(chunks, { type: (recorder.mimeType || type || "audio/webm").split(";")[0] });
          box.classList.remove("is-recording");
          state.hidden = true;
          toggleBtn.textContent = label("again");
          player.src = URL.createObjectURL(blob);
          ready.hidden = false;
        };
        recorder.start();
        started = Date.now();
        clock.textContent = "0:00";
        box.classList.add("is-recording");
        state.hidden = false;
        ready.hidden = true;
        toggleBtn.textContent = label("stop");
        ticker = setInterval(function () {
          var secs = (Date.now() - started) / 1000;
          clock.textContent = fmt(Math.min(secs, MAX_SECONDS));
          if (secs >= MAX_SECONDS) stop();
        }, 250);
      }, fail);
    };
    toggleBtn.addEventListener("click", function () {
      if (recorder && recorder.state === "recording") stop(); else start();
    });
    sendBtn.addEventListener("click", function () {
      if (!blob) return;
      var ext = blob.type.indexOf("mp4") >= 0 ? "m4a" : blob.type.indexOf("ogg") >= 0 ? "ogg" : "webm";
      var file = new File([blob], "voice-note." + ext, { type: blob.type });
      sendBtn.disabled = true;
      sendBtn.textContent = label("checking");
      try {
        var dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
        var text = form.querySelector("textarea");
        if (text) text.value = "";
        form.submit();
      } catch (e) {
        // Older browsers cannot set a file input: post it ourselves and show the result page.
        var data = new FormData(form);
        data.set("file", file);
        fetch(form.action, { method: "POST", body: data }).then(function (r) { return r.text(); }).then(function (html) {
          document.open(); document.write(html); document.close();
        }, fail);
      }
    });
  });

  // Table filters: chips with data-filter="value" show rows whose data-action matches.
  document.querySelectorAll("[data-filter-for]").forEach(function (chips) {
    var table = document.getElementById(chips.getAttribute("data-filter-for"));
    if (!table) return;
    chips.addEventListener("click", function (e) {
      var chip = e.target.closest("[data-filter]");
      if (!chip) return;
      var value = chip.getAttribute("data-filter");
      chips.querySelectorAll("[data-filter]").forEach(function (c) { c.setAttribute("aria-pressed", c === chip ? "true" : "false"); });
      table.querySelectorAll("tbody tr[data-action]").forEach(function (row) {
        row.hidden = value !== "*" && row.getAttribute("data-action").indexOf(value) !== 0;
      });
    });
  });
})();
