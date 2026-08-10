(function () {
  "use strict";

  var root = document.querySelector("[data-player-compare]");
  if (!root) return;

  function withRoot(path) {
    var base = document.documentElement.getAttribute("data-application-root") || "";
    base = base.replace(/\/$/, "");
    if (!path.startsWith("/")) path = "/" + path;
    if (base && (path === base || path.indexOf(base + "/") === 0)) {
      return path;
    }
    return base + path;
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function currentIds() {
    var params = new URLSearchParams(window.location.search);
    return {
      left: params.get("left") || "",
      right: params.get("right") || "",
    };
  }

  function navigateWith(side, playerId) {
    var ids = currentIds();
    ids[side] = String(playerId);
    var qs = [];
    if (ids.left) qs.push("left=" + encodeURIComponent(ids.left));
    if (ids.right) qs.push("right=" + encodeURIComponent(ids.right));
    var url = withRoot("/player-comparison");
    if (qs.length) url += "?" + qs.join("&");
    window.location.href = url;
  }

  function bindPicker(side) {
    var input = root.querySelector('[data-compare-input="' + side + '"]');
    var ac = root.querySelector('[data-compare-ac="' + side + '"]');
    if (!input || !ac) return;

    var timer = null;

    function closeAc() {
      ac.classList.remove("is-open");
      ac.innerHTML = "";
      ac.hidden = true;
    }

    input.addEventListener("blur", function () {
      setTimeout(closeAc, 200);
    });
    ac.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });

    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) {
        closeAc();
        return;
      }
      timer = setTimeout(function () {
        fetch(withRoot("/api/search/players?q=" + encodeURIComponent(q)))
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            ac.innerHTML = "";
            if (!data.results || !data.results.length) {
              closeAc();
              return;
            }
            data.results.forEach(function (p) {
              var btn = document.createElement("button");
              btn.type = "button";
              var meta;
              if (p.team_logo_url && p.team_logo_url.length) {
                var logoHtml = p.team_slug
                  ? '<span class="team-name-lockup team-name-lockup--icon" title="' +
                    escapeAttr((p.team_abbr || p.team || "").trim()) +
                    '"><img src="' +
                    escapeAttr(p.team_logo_url) +
                    '" alt="" class="team-name-lockup__logo"></span> '
                  : '<img src="' +
                    escapeAttr(p.team_logo_url) +
                    '" alt="" class="team-name-lockup__logo"> ';
                meta = logoHtml + escapeHtml(p.position || "—");
              } else {
                meta =
                  escapeHtml(p.position || "—") +
                  " · " +
                  escapeHtml(p.team_abbr || p.team || "FA");
              }
              btn.innerHTML =
                "<strong>" +
                escapeHtml(p.full_name) +
                '</strong><br><span class="meta">' +
                meta +
                "</span>";
              btn.addEventListener("click", function () {
                input.value = p.full_name || "";
                input.setAttribute("data-selected-id", String(p.id));
                closeAc();
                navigateWith(side, p.id);
              });
              ac.appendChild(btn);
            });
            ac.hidden = false;
            ac.classList.add("is-open");
          })
          .catch(function () {
            closeAc();
          });
      }, 200);
    });

    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeAc();
    });
  }

  bindPicker("left");
  bindPicker("right");
})();
