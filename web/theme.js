"use strict";
// Which theme the Board is in, and the three-state control that sets it
// (ADR 0019). Loaded SYNCHRONOUSLY FROM THE HEAD, which is the whole reason
// this is a file of its own rather than a corner of board.js: it has to write
// `data-theme` before the body is parsed, or the page paints dark and then
// corrects itself in front of you. board.js loads at the end of the body and is
// far too late; an inline <script> would be early enough but the server sends
// `script-src 'self'` and the browser drops it on the floor.
//
// It resolves `auto` ITSELF rather than leaving it to a media query, and always
// writes a concrete answer. The alternative — `@media(prefers-color-scheme)`
// for auto plus an attribute to override it — needs the whole light palette
// stated twice, in two places that must not drift. Once is better.
(function () {
  var KEY = "cl_theme";

  // null means auto. Anything unrecognised in storage is treated as auto rather
  // than trusted: this value survives across deploys and is trivial to hand-edit.
  var pinned = null;
  try {
    var raw = localStorage.getItem(KEY);
    if (raw === "light" || raw === "dark") pinned = raw;
  } catch (e) { /* storage blocked — auto every load, which is the sane default */ }

  var mq = window.matchMedia("(prefers-color-scheme: light)");

  function paint() {
    document.documentElement.setAttribute(
      "data-theme", pinned || (mq.matches ? "light" : "dark"));
  }
  paint();

  // Only while auto: on `light` or `dark` the phone's own switch is not ours to
  // obey. Android and iOS both flip this on a schedule, so it does fire.
  if (mq.addEventListener) {
    mq.addEventListener("change", function () { if (!pinned) paint(); });
  } else if (mq.addListener) {
    mq.addListener(function () { if (!pinned) paint(); });   // older WebKit
  }

  var box = null;

  function sync() {
    if (!box) return;
    var cur = pinned || "auto";
    var bs = box.querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) {
      var on = bs[i].getAttribute("data-t") === cur;
      bs[i].classList.toggle("on", on);
      bs[i].setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  // The control is static markup in the page header, so there is nothing to
  // build — just bind it once the parser has reached it.
  document.addEventListener("DOMContentLoaded", function () {
    box = document.getElementById("theme");
    if (!box) return;
    box.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b || !box.contains(b)) return;
      var t = b.getAttribute("data-t");
      pinned = (t === "auto") ? null : t;
      try {
        if (pinned) localStorage.setItem(KEY, pinned);
        else localStorage.removeItem(KEY);
      } catch (e2) { /* the choice still holds for this load */ }
      paint();
      sync();
    });
    sync();
  });
})();
