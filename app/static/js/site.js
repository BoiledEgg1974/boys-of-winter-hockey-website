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

  function playerIdFromHref(href) {
    if (!href) return null;
    try {
      var u = new URL(href, window.location.origin);
      var m = u.pathname.match(/\/player\/(\d+)(?:\/)?$/);
      return m ? parseInt(m[1], 10) : null;
    } catch (err) {
      return null;
    }
  }

  function withRoot(path) {
    var root = document.documentElement.getAttribute("data-application-root") || "";
    root = root.replace(/\/$/, "");
    if (!path.startsWith("/")) path = "/" + path;
    return root + path;
  }

  function attrColorStyle(v) {
    if (v == null || isNaN(v)) return "";
    var x = Math.max(0, Math.min(20, Number(v)));
    var stops = [
      [0, [220, 38, 38]],
      [8, [251, 146, 60]],
      [13, [190, 220, 80]],
      [16, [45, 212, 191]],
      [20, [59, 130, 246]],
    ];
    var i;
    for (i = 1; i < stops.length; i += 1) {
      if (x <= stops[i][0]) break;
    }
    var lo = stops[Math.max(0, i - 1)];
    var hi = stops[Math.min(stops.length - 1, i)];
    var t = hi[0] > lo[0] ? (x - lo[0]) / (hi[0] - lo[0]) : 0;
    var r = Math.round(lo[1][0] + (hi[1][0] - lo[1][0]) * t);
    var g = Math.round(lo[1][1] + (hi[1][1] - lo[1][1]) * t);
    var b = Math.round(lo[1][2] + (hi[1][2] - lo[1][2]) * t);
    return "color:rgb(" + r + "," + g + "," + b + ")";
  }

  function hoverStars(v) {
    if (v == null || isNaN(v)) return '<span class="player-hover-stars__empty">—</span>';
    var steps = Math.round(Number(v) * 2);
    if (steps < 0) steps = 0;
    if (steps > 10) steps = 10;
    var full = Math.floor(steps / 2);
    var half = steps % 2;
    var empty = 5 - full - half;
    var h = "";
    while (full-- > 0) h += '<span class="player-hover-star">★</span>';
    if (half) h += '<span class="player-hover-star player-hover-star--half">★</span>';
    while (empty-- > 0) h += '<span class="player-hover-star player-hover-star--empty">★</span>';
    return h;
  }

  function formatHeight(heightInches) {
    if (heightInches == null || isNaN(heightInches)) return "—";
    var h = Number(heightInches);
    if (h <= 0) return "—";
    return Math.floor(h / 12) + "'" + (h % 12) + '"';
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

  function initPlayerHoverCards() {
    var cache = {};
    var activeAnchor = null;
    var showTimer = null;
    var hideTimer = null;
    var card = document.createElement("div");
    card.className = "player-hover-card";
    card.hidden = true;
    document.body.appendChild(card);

    function hideCard() {
      card.hidden = true;
      activeAnchor = null;
    }

    function scheduleHide() {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideCard, 120);
    }

    function moveCardNear(anchor) {
      if (!anchor) return;
      var rect = anchor.getBoundingClientRect();
      var pad = 12;
      var cardRect = card.getBoundingClientRect();
      var left = rect.left + window.scrollX + rect.width / 2 - cardRect.width / 2;
      var top = rect.bottom + window.scrollY + pad;
      var maxLeft = window.scrollX + document.documentElement.clientWidth - cardRect.width - 8;
      var minLeft = window.scrollX + 8;
      if (left < minLeft) left = minLeft;
      if (left > maxLeft) left = maxLeft;
      var maxTop = window.scrollY + document.documentElement.clientHeight - cardRect.height - 8;
      if (top > maxTop) {
        top = rect.top + window.scrollY - cardRect.height - 8;
      }
      card.style.left = Math.round(left) + "px";
      card.style.top = Math.round(top) + "px";
    }

    function renderCard(d) {
      var attrsHtml = "";
      if (d.is_goalie) {
        var goa = d.attrs && d.attrs.goa != null ? d.attrs.goa : "—";
        var menG = d.attrs && d.attrs.men != null ? d.attrs.men : "—";
        attrsHtml =
          '<div class="player-hover-attrs">' +
          '<span>GOA <strong style="' + attrColorStyle(goa) + '">' + escapeHtml(String(goa)) + "</strong></span>" +
          '<span>MEN <strong style="' + attrColorStyle(menG) + '">' + escapeHtml(String(menG)) + "</strong></span>" +
          "</div>";
      } else {
        var off = d.attrs && d.attrs.off != null ? d.attrs.off : "—";
        var def = d.attrs && d.attrs.def != null ? d.attrs.def : "—";
        var phy = d.attrs && d.attrs.phy != null ? d.attrs.phy : "—";
        var men = d.attrs && d.attrs.men != null ? d.attrs.men : "—";
        attrsHtml =
          '<div class="player-hover-attrs">' +
          '<span>OFF <strong style="' + attrColorStyle(off) + '">' + escapeHtml(String(off)) + "</strong></span>" +
          '<span>DEF <strong style="' + attrColorStyle(def) + '">' + escapeHtml(String(def)) + "</strong></span>" +
          '<span>PHY <strong style="' + attrColorStyle(phy) + '">' + escapeHtml(String(phy)) + "</strong></span>" +
          '<span>MEN <strong style="' + attrColorStyle(men) + '">' + escapeHtml(String(men)) + "</strong></span>" +
          "</div>";
      }
      var shoots = d.shoots || "—";
      if (/^l/i.test(shoots)) shoots = "Left";
      else if (/^r/i.test(shoots)) shoots = "Right";
      card.innerHTML =
        '<div class="player-hover-card__row">' +
        '<div class="player-hover-card__photo">' +
        (d.photo_url
          ? '<img src="' + escapeAttr(d.photo_url) + '" alt="">'
          : '<span class="player-hover-card__photo-ph"></span>') +
        "</div>" +
        '<div class="player-hover-card__body">' +
        '<div class="player-hover-card__name">' + escapeHtml(d.name || "Player") + "</div>" +
        '<div class="player-hover-card__meta">' +
        escapeHtml(d.position || "—") +
        (d.team_abbr ? ", " + escapeHtml(d.team_abbr) : "") +
        " · Age " + escapeHtml(String(d.age != null ? d.age : "—")) +
        " | Shoots " + escapeHtml(shoots) +
        " | " + escapeHtml(formatHeight(d.height_inches)) +
        " - " + escapeHtml(String(d.weight_lbs != null ? d.weight_lbs : "—")) + " lbs" +
        "</div>" +
        attrsHtml +
        '<div class="player-hover-ap">' +
        '<span class="player-hover-ap__label">ABI</span><span class="player-hover-ap__stars">' + hoverStars(d.abi) + "</span>" +
        '<span class="player-hover-ap__sep">|</span>' +
        '<span class="player-hover-ap__label">POT</span><span class="player-hover-ap__stars">' + hoverStars(d.pot) + "</span>" +
        "</div>" +
        "</div></div>";
    }

    function showFor(anchor, playerId) {
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      showTimer = setTimeout(function () {
        activeAnchor = anchor;
        var cached = cache[playerId];
        if (cached) {
          renderCard(cached);
          card.hidden = false;
          moveCardNear(anchor);
          return;
        }
        fetch(withRoot("/api/player/" + playerId + "/hover-card"))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d || d.error) return;
            cache[playerId] = d;
            if (activeAnchor !== anchor) return;
            renderCard(d);
            card.hidden = false;
            moveCardNear(anchor);
          })
          .catch(function () {});
      }, 120);
    }

    card.addEventListener("mouseenter", function () {
      clearTimeout(hideTimer);
    });
    card.addEventListener("mouseleave", scheduleHide);

    function bindPlayerHoverAnchors() {
      document.querySelectorAll('a[href*="/player/"]').forEach(function (a) {
        if (a.getAttribute("data-player-hover-bound") === "1") return;
        var playerId = playerIdFromHref(a.getAttribute("href"));
        if (!playerId) return;
        a.setAttribute("data-player-hover-bound", "1");
        a.addEventListener("mouseenter", function () { showFor(a, playerId); });
        a.addEventListener("mouseleave", scheduleHide);
        a.addEventListener("focusin", function () { showFor(a, playerId); });
        a.addEventListener("focusout", scheduleHide);
      });
    }

    bindPlayerHoverAnchors();

    window.addEventListener("scroll", function () {
      if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
    }, { passive: true });
    window.addEventListener("resize", function () {
      if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
    });

    return bindPlayerHoverAnchors;
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

  function scrollScheduleTrackToFocus(track) {
    if (!track) return;
    var idx = parseInt(track.getAttribute("data-focus-index") || "0", 10);
    var cards = track.querySelectorAll(".team-schedule-card");
    if (!cards.length || idx < 0 || idx >= cards.length) return;
    var el = cards[idx];
    var target = el.offsetLeft - (track.clientWidth - el.offsetWidth) / 2;
    track.scrollLeft = Math.max(0, target);
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(getPreferredTheme());
    window.bindPlayerHoverAnchors = initPlayerHoverCards();
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
        var slug = (leagueSwitcher.value || "").replace(/^\/+|\/+$/g, "");
        if (!slug) return;
        // Always open the selected league's homepage. Carrying over paths like /team/bos-t5
        // breaks when that slug or route does not exist in the other league DB.
        // Domain-root path: withRoot would prefix current SCRIPT_NAME (e.g. /bowl-fantasy/bowl-historical/).
        window.location.href = "/" + slug + "/";
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

    function bindScheduleCarousel(root) {
      if (!root || root.getAttribute("data-carousel-bound") === "1") return;
      var track = root.querySelector(".team-schedule-carousel__track");
      var prevBtn = root.querySelector(".team-schedule-carousel__btn--prev");
      var nextBtn = root.querySelector(".team-schedule-carousel__btn--next");
      if (!track) return;
      root.setAttribute("data-carousel-bound", "1");

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
        scrollScheduleTrackToFocus(track);
      }
      requestAnimationFrame(function () {
        requestAnimationFrame(scrollToFocus);
      });
      window.addEventListener("load", scrollToFocus);
    }

    document.querySelectorAll("[data-team-schedule-carousel]").forEach(bindScheduleCarousel);

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
  window.BOWL.scrollScheduleTracksToFocus = function () {
    document.querySelectorAll("[data-team-schedule-carousel] .team-schedule-carousel__track").forEach(
      scrollScheduleTrackToFocus
    );
  };
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
