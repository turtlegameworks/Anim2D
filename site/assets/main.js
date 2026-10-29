// Points the download buttons at the latest release's files and highlights the one for this computer.
// Without JavaScript, or if GitHub's API can't be reached, every button opens the latest release page.
(function () {
  "use strict";
  var REPO = "turtlegameworks/Anim2D";

  function platform() {
    var ua = navigator.userAgent || "";
    var uaPlatform = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || "";
    if (/Win/i.test(uaPlatform) || /Windows/i.test(ua)) return "windows";
    if (/Mac/i.test(uaPlatform) || /Macintosh/i.test(ua)) {
      // Browsers on Apple silicon report "Intel" in the user agent; WebGL's renderer names the real GPU.
      try {
        var gl = document.createElement("canvas").getContext("webgl");
        var info = gl && gl.getExtension("WEBGL_debug_renderer_info");
        var renderer = info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : "";
        if (/Apple (M\d|GPU)/i.test(renderer)) return "mac-arm";
        if (/Intel|AMD|Radeon|NVIDIA/i.test(renderer)) return "mac-intel";
      } catch (e) { /* fall through */ }
      return "mac-arm"; // most Macs sold since 2020
    }
    return null;
  }

  var cards = Array.prototype.slice.call(document.querySelectorAll(".downloads .card"));
  var primary = document.getElementById("primary-download");
  var here = platform();
  var mine = cards.filter(function (c) { return c.dataset.os === here; })[0];
  if (mine) {
    mine.classList.add("recommended");
    primary.textContent = "Download for " + mine.querySelector("strong").textContent;
  }

  fetch("https://api.github.com/repos/" + REPO + "/releases/latest", { headers: { Accept: "application/vnd.github+json" } })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (release) {
      var version = String(release.tag_name || "").replace(/^v/, "");
      var urls = {};
      (release.assets || []).forEach(function (a) { urls[a.name] = a.browser_download_url; });
      cards.forEach(function (card) {
        var url = urls[card.dataset.asset.replace("{v}", version)];
        if (url) card.href = url;
      });
      if (mine && urls[mine.dataset.asset.replace("{v}", version)]) primary.href = mine.href;
      else primary.href = "#download";
      var line = document.getElementById("release-line");
      var date = (release.published_at || "").slice(0, 10);
      line.innerHTML = "";
      line.appendChild(document.createTextNode("Version " + version + (date ? ", released " + date + ". " : ". ")));
      var notes = document.createElement("a");
      notes.href = release.html_url;
      notes.textContent = "What's new";
      line.appendChild(notes);
      document.getElementById("primary-note").textContent =
        "Version " + version + " · Windows 10/11 · macOS 14+ on Apple silicon and Intel · MIT License";
    })
    .catch(function () { /* the buttons already open the latest release page */ });
})();
