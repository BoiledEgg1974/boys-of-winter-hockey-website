(function () {
  "use strict";

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function escapeAttr(s) {
    if (s == null || s === "") return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function withRoot(path) {
    var root = document.documentElement.getAttribute("data-application-root") || "";
    root = root.replace(/\/$/, "");
    if (!path.startsWith("/")) path = "/" + path;
    return root + path;
  }

  function teamLogoCell(logoUrl, slug, abbrFallback) {
    if (!logoUrl) {
      return escapeHtml(abbrFallback || "—");
    }
    var img =
      '<img src="' +
      escapeAttr(logoUrl) +
      '" alt="" class="team-name-lockup__logo">';
    if (slug) {
      return (
        '<a class="team-name-lockup team-name-lockup--icon" href="' +
        escapeAttr(withRoot("/team/" + slug)) +
        '" title="' +
        escapeAttr(abbrFallback || "") +
        '">' +
        img +
        "</a>"
      );
    }
    return '<span class="team-name-lockup team-name-lockup--icon">' + img + "</span>";
  }

  const THEME_KEY = "bowl-universe-theme";
  /** Preserve window scroll when switching team page panels (?panel=) across full reloads. */
  var TEAM_TAB_SCROLL_Y_KEY = "bowTeamMgmtTabScrollY";
  var TEAM_TAB_SCROLL_PATH_KEY = "bowTeamMgmtTabScrollPath";

  function getPreferredTheme() {
    return localStorage.getItem(THEME_KEY) || "light";
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme === "dark" ? "dark" : "light");
    const btn = document.querySelector(".theme-toggle");
    if (btn) {
      btn.setAttribute("aria-label", theme === "dark" ? "Switch to light mode" : "Switch to dark mode");
      btn.textContent = theme === "dark" ? "\u2600" : "\u263E";
    }
  }

  function toggleTheme() {
    const next = getPreferredTheme() === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(getPreferredTheme());
    document.querySelectorAll(".theme-toggle").forEach(function (el) {
      el.addEventListener("click", toggleTheme);
    });

    document.querySelectorAll(".js-expand-game-log").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var panel = btn.closest(".player-profile__card--table");
        if (!panel) return;
        var expanded = panel.classList.toggle("is-expanded");
        btn.setAttribute("aria-expanded", expanded ? "true" : "false");
        btn.textContent = expanded ? "Show fewer games" : "Show more games";
      });
    });

    var navToggle = document.querySelector(".nav-toggle");
    var mainNav = document.querySelector(".main-nav");
    if (navToggle && mainNav) {
      navToggle.addEventListener("click", function () {
        mainNav.classList.toggle("is-open");
        var open = mainNav.classList.contains("is-open");
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
    }

    var leagueSwitcher = document.getElementById("league-switcher");
    if (leagueSwitcher) {
      leagueSwitcher.addEventListener("change", function () {
        var slug = leagueSwitcher.value;
        var path = window.location.pathname;
        var m = path.match(/^\/([^/]+)(\/.*)?$/);
        var rest = m && m[2] ? m[2] : "/";
        var qs = window.location.search || "";
        window.location.href = "/" + slug + rest + qs;
      });
    }

    var teamMgmtTabs = document.querySelector(".team-management-tabs");
    if (teamMgmtTabs) {
      teamMgmtTabs.querySelectorAll("a").forEach(function (a) {
        a.addEventListener("click", function () {
          try {
            sessionStorage.setItem(TEAM_TAB_SCROLL_Y_KEY, String(window.scrollY));
            sessionStorage.setItem(TEAM_TAB_SCROLL_PATH_KEY, window.location.pathname);
          } catch (err) {
            /* private mode / quota */
          }
        });
      });
    }
    if (document.querySelector(".team-page")) {
      try {
        var tabSavedY = sessionStorage.getItem(TEAM_TAB_SCROLL_Y_KEY);
        var tabSavedPath = sessionStorage.getItem(TEAM_TAB_SCROLL_PATH_KEY);
        if (tabSavedY !== null && tabSavedPath === window.location.pathname) {
          var scrollYRestore = parseInt(tabSavedY, 10);
          sessionStorage.removeItem(TEAM_TAB_SCROLL_Y_KEY);
          sessionStorage.removeItem(TEAM_TAB_SCROLL_PATH_KEY);
          if (!isNaN(scrollYRestore) && scrollYRestore >= 0) {
            function applyTeamTabScroll() {
              window.scrollTo(0, scrollYRestore);
            }
            requestAnimationFrame(function () {
              requestAnimationFrame(applyTeamTabScroll);
            });
            window.addEventListener("load", applyTeamTabScroll, { once: true });
          }
        }
      } catch (err2) {
        /* */
      }
    }

    document.querySelectorAll("[data-team-schedule-carousel]").forEach(function (root) {
      var track = root.querySelector(".team-schedule-carousel__track");
      var prevBtn = root.querySelector(".team-schedule-carousel__btn--prev");
      var nextBtn = root.querySelector(".team-schedule-carousel__btn--next");
      if (!track) return;

      function stepScroll(dir) {
        var card = track.querySelector(".team-schedule-card");
        if (!card) return;
        var w = card.getBoundingClientRect().width;
        var st = window.getComputedStyle(track);
        var gap = parseFloat(st.gap || st.columnGap) || 10;
        track.scrollBy({ left: dir * (w + gap), behavior: "smooth" });
      }
      if (prevBtn) prevBtn.addEventListener("click", function () { stepScroll(-1); });
      if (nextBtn) nextBtn.addEventListener("click", function () { stepScroll(1); });

      function scrollToFocus() {
        var idx = parseInt(track.getAttribute("data-focus-index") || "0", 10);
        var cards = track.querySelectorAll(".team-schedule-card");
        if (!cards.length || idx < 0 || idx >= cards.length) return;
        var el = cards[idx];
        var target = el.offsetLeft - (track.clientWidth - el.offsetWidth) / 2;
        track.scrollLeft = Math.max(0, target);
      }
      requestAnimationFrame(function () {
        requestAnimationFrame(scrollToFocus);
      });
      window.addEventListener("load", scrollToFocus);
    });

    var searchInput = document.getElementById("global-search");
    var ac = document.getElementById("search-autocomplete");
    if (searchInput && ac) {
      var timer = null;
      function closeAc() {
        ac.classList.remove("is-open");
        ac.innerHTML = "";
      }

      searchInput.addEventListener("blur", function () {
        setTimeout(closeAc, 200);
      });

      searchInput.addEventListener("input", function () {
        clearTimeout(timer);
        var q = searchInput.value.trim();
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
                  var logoHtml =
                    p.team_slug
                      ? '<a class="team-name-lockup team-name-lockup--icon" href="' +
                        escapeAttr(withRoot("/team/" + p.team_slug)) +
                        '" onclick="event.stopPropagation()" title="' +
                        escapeAttr((p.team_abbr || p.team || "").trim()) +
                        '"><img src="' +
                        escapeAttr(p.team_logo_url) +
                        '" alt="" class="team-name-lockup__logo"></a> '
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
                  "</strong><br><span class=\"meta\">" +
                  meta +
                  "</span>";
                btn.addEventListener("click", function () {
                  window.location.href = withRoot("/player/" + p.id);
                });
                ac.appendChild(btn);
              });
              ac.classList.add("is-open");
            })
            .catch(function () {
              closeAc();
            });
        }, 200);
      });
    }
  });

  function isEmptySortValue(v) {
    if (v === null || v === undefined) return true;
    var s = String(v).trim();
    return s === "" || s === "—";
  }

  function compareSortValues(va, vb) {
    if (isEmptySortValue(va) && isEmptySortValue(vb)) return 0;
    if (isEmptySortValue(va)) return 1;
    if (isEmptySortValue(vb)) return -1;
    var sa = String(va).trim().replace(/%$/, "");
    var sb = String(vb).trim().replace(/%$/, "");
    var na = parseFloat(sa);
    var nb = parseFloat(sb);
    var aNum = !isNaN(na) && /^-?[\d.]+(?:e[+-]?\d+)?$/i.test(sa);
    var bNum = !isNaN(nb) && /^-?[\d.]+(?:e[+-]?\d+)?$/i.test(sb);
    if (aNum && bNum) {
      if (na < nb) return -1;
      if (na > nb) return 1;
      return 0;
    }
    var ca = sa.toLowerCase();
    var cb = sb.toLowerCase();
    if (ca < cb) return -1;
    if (ca > cb) return 1;
    return 0;
  }

  function initSortableTable(table) {
    var tbody = table.tBodies[0];
    var thead = table.tHead;
    if (!tbody || !thead || !thead.rows[0]) return;
    var headerRow = thead.rows[0];
    var headers = headerRow.cells;
    if (!headers.length) return;

    var renumberFirst =
      table.getAttribute("data-sort-renumber") === "1" ||
      table.getAttribute("data-sort-renumber") === "true";

    var sortState = { col: null, asc: true };

    function getCellSortValue(tr, colIdx) {
      var cell = tr.cells[colIdx];
      if (!cell) return "";
      var attr = cell.getAttribute("data-sort-value");
      if (attr !== null && attr !== "") return attr;
      return cell.textContent.trim();
    }

    function renumberFirstColumn() {
      var rows = tbody.rows;
      for (var i = 0; i < rows.length; i++) {
        var c0 = rows[i].cells[0];
        if (c0) {
          c0.textContent = String(i + 1);
          c0.setAttribute("data-sort-value", String(i));
        }
      }
    }

    function sortByColumn(colIdx) {
      var th = headers[colIdx];
      var type = th.getAttribute("data-sort-type") || "str";
      var preferNum = type === "num";

      if (sortState.col === colIdx) {
        sortState.asc = !sortState.asc;
      } else {
        sortState.col = colIdx;
        sortState.asc = preferNum ? false : true;
      }

      var rows = Array.from(tbody.rows);
      rows.sort(function (a, b) {
        var va = getCellSortValue(a, colIdx);
        var vb = getCellSortValue(b, colIdx);
        var c = compareSortValues(va, vb);
        return sortState.asc ? c : -c;
      });

      rows.forEach(function (tr) {
        tbody.appendChild(tr);
      });

      if (renumberFirst) {
        renumberFirstColumn();
      }

      for (var i = 0; i < headers.length; i++) {
        headers[i].classList.remove("is-sorted", "is-sorted-asc", "is-sorted-desc");
        headers[i].removeAttribute("aria-sort");
      }
      th.classList.add("is-sorted", sortState.asc ? "is-sorted-asc" : "is-sorted-desc");
      th.setAttribute("aria-sort", sortState.asc ? "ascending" : "descending");
    }

    for (var c = 0; c < headers.length; c++) {
      (function (colIdx) {
        var th = headers[colIdx];
        if (th.hasAttribute("data-sort-nosort")) return;
        th.classList.add("th-sortable");
        th.setAttribute("tabindex", "0");
        function activate(e) {
          if (e.type === "keydown" && e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          sortByColumn(colIdx);
        }
        th.addEventListener("click", activate);
        th.addEventListener("keydown", activate);
      })(c);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.data-sortable").forEach(initSortableTable);
  });

  function boxscorePlayerLink(id, name) {
    if (id == null) return escapeHtml(name || "—");
    return (
      '<a href="' +
      escapeAttr(withRoot("/player/" + encodeURIComponent(String(id)))) +
      '" class="boxscore-player-link">' +
      escapeHtml(name || "") +
      "</a>"
    );
  }

  function boxscoreStrengthNote(s) {
    if (!s) return "—";
    var u = String(s).toUpperCase();
    if (u.indexOf("PP") >= 0 || u.indexOf("POWER") >= 0) return "PP";
    if (u.indexOf("SH") >= 0 || u.indexOf("SHORT") >= 0) return "SH";
    if (u.indexOf("EN") >= 0) return "EN";
    return "ES";
  }

  function boxscoreStarHasContent(s) {
    if (s == null || s === "") return false;
    if (typeof s === "string") return s.length > 0;
    return !!(s.name && String(s.name).length);
  }

  function renderBoxScoreHtml(d) {
    var st = d.special_teams || {};
    var away = d.away || {};
    var home = d.home || {};
    var html = '<div class="boxscore-panel">';

    if (d.stars && d.stars.some(boxscoreStarHasContent)) {
      html += '<div class="boxscore-stars">';
      html += '<div class="boxscore-stars__title">Three stars</div><ol class="boxscore-stars__list">';
      d.stars.forEach(function (star) {
        var name = typeof star === "string" ? star : star && star.name;
        if (!name) return;
        var logoHtml =
          typeof star === "object" && star
            ? teamLogoCell(star.team_logo_url, star.team_slug, star.team_abbr)
            : "";
        html +=
          '<li><span class="boxscore-star-line">' +
          logoHtml +
          '<span class="boxscore-star-name">' +
          escapeHtml(String(name)) +
          "</span></span></li>";
      });
      html += "</ol></div>";
    }

    var awayPP = st.away_pp || "—";
    var homePP = st.home_pp || "—";
    html += '<div class="boxscore-team-summary">';
    html +=
      '<div class="boxscore-team-summary__line">' +
      teamLogoCell(away.logo_url, away.slug, away.abbr) +
      "<span> — Shots " +
      (away.shots != null ? away.shots : "—") +
      " · PIM " +
      (d.pim_away != null ? d.pim_away : "—") +
      " · PP " +
      escapeHtml(String(awayPP)) +
      "</span></div>";
    html +=
      '<div class="boxscore-team-summary__line">' +
      teamLogoCell(home.logo_url, home.slug, home.abbr) +
      "<span> — Shots " +
      (home.shots != null ? home.shots : "—") +
      " · PIM " +
      (d.pim_home != null ? d.pim_home : "—") +
      " · PP " +
      escapeHtml(String(homePP)) +
      "</span></div>";
    html += "</div>";

    var pcols = d.period_columns || [];
    html += '<div class="table-wrap boxscore-table-wrap"><table class="boxscore-table boxscore-table--period"><thead><tr><th>Team</th>';
    pcols.forEach(function (col) {
      var lab = String(col.label);
      html += "<th>" + (lab === "OT" ? "OT" : "P" + escapeHtml(lab)) + "</th>";
    });
    html += "<th>T</th></tr></thead><tbody>";
    html +=
      "<tr><td><span class=\"boxscore-team-cell\">" +
      teamLogoCell(away.logo_url, away.slug, away.abbr) +
      '<span class="boxscore-team-cell__name">' +
      escapeHtml(away.name || away.abbr || "") +
      "</span></span></td>";
    pcols.forEach(function (col) {
      html += "<td>" + (col.away != null ? col.away : "—") + "</td>";
    });
    html += "<td><strong>" + (away.score != null ? away.score : "—") + "</strong></td></tr>";
    html +=
      "<tr><td><span class=\"boxscore-team-cell\">" +
      teamLogoCell(home.logo_url, home.slug, home.abbr) +
      '<span class="boxscore-team-cell__name">' +
      escapeHtml(home.name || home.abbr || "") +
      "</span></span></td>";
    pcols.forEach(function (col) {
      html += "<td>" + (col.home != null ? col.home : "—") + "</td>";
    });
    html += "<td><strong>" + (home.score != null ? home.score : "—") + "</strong></td></tr>";
    html += "</tbody></table></div>";

    html += '<h3 class="boxscore-section-title">Scoring</h3>';
    html += '<div class="table-wrap boxscore-table-wrap"><table class="boxscore-table"><thead><tr>';
    html += "<th>Pd</th><th>Time</th><th>Team</th><th>Goal</th><th>Assists</th><th>Note</th></tr></thead><tbody>";
    if (d.goals && d.goals.length) {
      d.goals.forEach(function (g) {
        var ast = "";
        if (g.a1) {
          ast = escapeHtml(g.a1);
          if (g.a2) ast += ", " + escapeHtml(g.a2);
        } else {
          ast = "—";
        }
        html +=
          "<tr><td>" +
          g.period +
          "</td><td>" +
          escapeHtml(g.time || "—") +
          "</td><td>" +
          teamLogoCell(g.team_logo_url, g.team_slug, g.team_abbr) +
          "</td><td>" +
          boxscorePlayerLink(g.scorer_id, g.scorer || "?") +
          "</td><td>" +
          ast +
          "</td><td>" +
          escapeHtml(boxscoreStrengthNote(g.strength)) +
          "</td></tr>";
      });
    } else {
      html += '<tr><td colspan="6" class="boxscore-empty">No goal events in database.</td></tr>';
    }
    html += "</tbody></table></div>";

    var goalies = d.goalies || [];
    var skaters = d.skaters || [];
    var awayAbbr = away.abbr || "";
    var homeAbbr = home.abbr || "";
    var gAway = goalies.filter(function (x) {
      return x.team_abbr === awayAbbr;
    });
    var gHome = goalies.filter(function (x) {
      return x.team_abbr === homeAbbr;
    });
    var sAway = skaters.filter(function (x) {
      return x.team_abbr === awayAbbr;
    });
    var sHome = skaters.filter(function (x) {
      return x.team_abbr === homeAbbr;
    });

    function goalieRows(arr) {
      var h = "";
      arr.forEach(function (g) {
        var pct = g.sv_pct != null ? Number(g.sv_pct).toFixed(3) : "—";
        h +=
          "<tr><td>" +
          boxscorePlayerLink(g.player_id, g.player) +
          "</td><td>" +
          escapeHtml(g.toi || "—") +
          "</td><td>" +
          g.sa +
          "</td><td>" +
          g.ga +
          "</td><td>" +
          g.saves +
          "</td><td>" +
          pct +
          "</td></tr>";
      });
      if (!arr.length) {
        h += '<tr><td colspan="6" class="boxscore-empty">—</td></tr>';
      }
      return h;
    }

    html += '<div class="boxscore-split boxscore-split--after-scoring">';
    html +=
      '<div class="boxscore-split__col"><h4 class="boxscore-split__head">' +
      teamLogoCell(away.logo_url, away.slug, away.abbr) +
      '<span class="boxscore-split__head-suffix"> — Goalies</span></h4>';
    html +=
      '<div class="table-wrap"><table class="boxscore-table"><thead><tr><th>Goalie</th><th>TOI</th><th>SA</th><th>GA</th><th>SV</th><th>SV%</th></tr></thead><tbody>';
    html += goalieRows(gAway);
    html += "</tbody></table></div></div>";

    html +=
      '<div class="boxscore-split__col"><h4 class="boxscore-split__head">' +
      teamLogoCell(home.logo_url, home.slug, home.abbr) +
      '<span class="boxscore-split__head-suffix"> — Goalies</span></h4>';
    html +=
      '<div class="table-wrap"><table class="boxscore-table"><thead><tr><th>Goalie</th><th>TOI</th><th>SA</th><th>GA</th><th>SV</th><th>SV%</th></tr></thead><tbody>';
    html += goalieRows(gHome);
    html += "</tbody></table></div></div>";
    html += "</div>";

    function skaterRows(arr) {
      var h = "";
      arr.forEach(function (s) {
        h +=
          "<tr><td>" +
          boxscorePlayerLink(s.player_id, s.player) +
          "</td><td>" +
          s.g +
          "</td><td>" +
          s.a +
          "</td><td>" +
          (s.plus_minus != null ? s.plus_minus : "—") +
          "</td><td>" +
          s.s +
          "</td><td>" +
          (s.bs != null ? s.bs : "—") +
          "</td><td>" +
          (s.hits != null ? s.hits : "—") +
          "</td><td>" +
          s.pim +
          "</td><td>" +
          (s.gr != null ? Number(s.gr).toFixed(1) : "—") +
          "</td><td>" +
          (s.toi ? escapeHtml(s.toi) : "—") +
          "</td></tr>";
      });
      if (!arr.length) {
        h += '<tr><td colspan="10" class="boxscore-empty">—</td></tr>';
      }
      return h;
    }

    html += '<div class="boxscore-split boxscore-split--skaters">';
    html +=
      '<div class="boxscore-split__col"><h4 class="boxscore-split__head">' +
      teamLogoCell(away.logo_url, away.slug, away.abbr) +
      '<span class="boxscore-split__head-suffix"> — Skaters</span></h4>';
    html +=
      '<div class="table-wrap"><table class="boxscore-table"><thead><tr><th>Player</th><th>G</th><th>A</th><th>+/-</th><th>SOG</th><th>BLK</th><th>HIT</th><th>PIM</th><th>GR</th><th>TOI</th></tr></thead><tbody>';
    html += skaterRows(sAway);
    html += "</tbody></table></div></div>";

    html +=
      '<div class="boxscore-split__col"><h4 class="boxscore-split__head">' +
      teamLogoCell(home.logo_url, home.slug, home.abbr) +
      '<span class="boxscore-split__head-suffix"> — Skaters</span></h4>';
    html +=
      '<div class="table-wrap"><table class="boxscore-table"><thead><tr><th>Player</th><th>G</th><th>A</th><th>+/-</th><th>SOG</th><th>BLK</th><th>HIT</th><th>PIM</th><th>GR</th><th>TOI</th></tr></thead><tbody>';
    html += skaterRows(sHome);
    html += "</tbody></table></div></div>";
    html += "</div>";

    html += "</div>";
    return html;
  }

  window.BOWL = window.BOWL || {};
  window.BOWL.loadBoxScore = function (gameId, container) {
    if (!container) return;
    container.innerHTML = '<p class="boxscore-loading">Loading box score…</p>';
    fetch(withRoot("/api/game/" + gameId + "/boxscore"))
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d.error) {
          container.innerHTML = "<p class=\"boxscore-error\">Box score unavailable.</p>";
          return;
        }
        container.innerHTML = renderBoxScoreHtml(d);
      })
      .catch(function () {
        container.innerHTML = "<p class=\"boxscore-error\">Failed to load box score.</p>";
      });
  };
})();
