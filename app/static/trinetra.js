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
