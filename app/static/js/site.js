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

  function isTouchLikeDevice() {
    return window.matchMedia("(hover: none), (pointer: coarse)").matches;
  }

  /**
   * Touch: first tap navigates. Long-press (~480ms) opens a docked preview
   * without following the link (Android Chrome often treated tap-to-preview
   * as "links don't work").
   */
  function bindLongPressPreview(anchor, onLongPress) {
    var timer = null;
    var fired = false;
    var LP_MS = 480;

    function clearTimer() {
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    }

    function arm(e) {
      if (e.touches && e.touches.length > 1) {
        clearTimer();
        return;
      }
      fired = false;
      clearTimer();
      timer = setTimeout(function () {
        timer = null;
        fired = true;
        onLongPress();
      }, LP_MS);
    }

    function onTouchEnd(e) {
      clearTimer();
      if (!fired) return;
      // Suppress the synthetic click that would navigate away from the preview.
      e.preventDefault();
      setTimeout(function () {
        fired = false;
      }, 350);
    }

    anchor.addEventListener("touchstart", arm, { passive: true });
    anchor.addEventListener(
      "touchmove",
      function () {
        clearTimer();
      },
      { passive: true }
    );
    anchor.addEventListener("touchend", onTouchEnd);
    anchor.addEventListener("touchcancel", clearTimer);
    anchor.addEventListener("contextmenu", function (e) {
      if (fired) e.preventDefault();
    });
    anchor.addEventListener("click", function (e) {
      if (!fired) return;
      e.preventDefault();
      fired = false;
    });
  }

  function dockHoverCard(card) {
    if (!card) return;
    card.classList.add("hover-preview-card--dock");
    var pad = 12;
    var vv = window.visualViewport;
    var viewW = (vv && vv.width) || document.documentElement.clientWidth;
    var viewH = (vv && vv.height) || window.innerHeight;
    var offsetLeft = (vv && vv.offsetLeft) || 0;
    var offsetTop = (vv && vv.offsetTop) || 0;
    var cardW = card.offsetWidth || 320;
    var cardH = card.offsetHeight || 240;
    var left = window.scrollX + offsetLeft + Math.max(pad, (viewW - cardW) / 2);
    var top = window.scrollY + offsetTop + Math.max(pad, (viewH - cardH) / 2);
    card.style.left = Math.round(left) + "px";
    card.style.top = Math.round(top) + "px";
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

  function teamSlugFromHref(href) {
    if (!href) return null;
    try {
      var u = new URL(href, window.location.origin);
      var m = u.pathname.match(/\/team\/([^/]+)\/?$/);
      return m ? decodeURIComponent(m[1]) : null;
    } catch (err2) {
      return null;
    }
  }

  function withRoot(path) {
    var root = document.documentElement.getAttribute("data-application-root") || "";
    root = root.replace(/\/$/, "");
    if (!path.startsWith("/")) path = "/" + path;
    if (root && (path === root || path.indexOf(root + "/") === 0)) {
      return path;
    }
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

  /** Match Flask ``rating_meter_fill_style`` (0–21 scale) for share-card position bars. */
  function posRatingMeterFillStyle(val) {
    if (val == null || val === "") return "width:0%;background-color:transparent";
    var v = Number(val);
    if (!isFinite(v)) return "width:0%;background-color:transparent";
    v = Math.max(0, Math.min(21, v));
    var pct = (v / 21) * 100;
    var c;
    if (v >= 20) c = "rgb(59,130,246)";
    else if (v >= 17) c = "rgb(34,211,238)";
    else if (v >= 16) c = "rgb(45,212,191)";
    else if (v >= 14) c = "rgb(132,204,22)";
    else if (v >= 13) c = "rgb(190,220,80)";
    else if (v >= 8) c = "rgb(251,146,60)";
    else c = "rgb(220,38,38)";
    return "width:" + pct.toFixed(2) + "%;background-color:" + c + ";";
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

  function fmtMoneyShare(n) {
    if (n == null || isNaN(n)) return "—";
    try {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(
        Number(n)
      );
    } catch (e) {
      return "$" + String(n);
    }
  }

  function getUiTheme() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function loadHtml2CanvasLib() {
    return new Promise(function (resolve, reject) {
      if (typeof window.html2canvas === "function") return resolve(window.html2canvas);
      reject(new Error("html2canvas not loaded"));
    });
  }

  function ensurePlayerShareCardStage() {
    var id = "player-share-card-stage";
    var el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      el.setAttribute("aria-hidden", "true");
      /* Full-size off-screen: tiny overflow:hidden parents break html2canvas layouts. */
      el.style.cssText =
        "position:fixed;left:-10000px;top:0;width:auto;height:auto;overflow:visible;" +
        "opacity:0;pointer-events:none;z-index:-1;";
      document.body.appendChild(el);
    }
    return el;
  }

  /** Resolve when every <img> under root has fired load or error (so html2canvas isn't racing). */
  function whenImagesLoaded(root) {
    var imgs = root.querySelectorAll("img");
    if (!imgs.length) return Promise.resolve();
    var tasks = [];
    for (var i = 0; i < imgs.length; i += 1) {
      (function (img) {
        tasks.push(
          new Promise(function (resolve) {
            if (img.complete) {
              resolve();
              return;
            }
            var done = function () {
              img.removeEventListener("load", done);
              img.removeEventListener("error", done);
              resolve();
            };
            img.addEventListener("load", done);
            img.addEventListener("error", done);
          })
        );
      })(imgs[i]);
    }
    return Promise.all(tasks);
  }

  function canUseClipboardImage() {
    if (!window.isSecureContext) return false;
    if (!navigator.clipboard || typeof navigator.clipboard.write !== "function") return false;
    if (typeof ClipboardItem === "undefined") return false;
    return true;
  }

  /** Private LAN IPs: dev servers are almost always HTTP-only; https:// same host → ERR_SSL_PROTOCOL_ERROR. */
  function isRfc1918Hostname(hostname) {
    var h = String(hostname || "").toLowerCase();
    if (/^192\.168\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
    if (/^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
    if (/^172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}$/.test(h)) return true;
    return false;
  }

  /** True if we might get clipboard paste by switching to HTTPS on the same host. */
  function shouldOfferHttpsForClipboard() {
    if (window.isSecureContext) return false;
    if (String(location.protocol || "").toLowerCase() !== "http:") return false;
    var h = String(location.hostname || "").toLowerCase();
    if (h === "localhost" || h === "127.0.0.1" || h === "[::1]") return false;
    if (isRfc1918Hostname(h)) return false;
    return true;
  }

  function offerSwitchToHttpsForClipboardPaste(triggerBtn, oldTxt) {
    var httpsUrl =
      "https://" + location.host + location.pathname + location.search + location.hash;
    var go = window.confirm(
      "Pasting the player card as an image in Discord needs a secure (HTTPS) page. " +
        "Plain HTTP cannot use the image clipboard.\n\n" +
        "Open this same page over HTTPS now?\n\n" +
        httpsUrl
    );
    if (go) {
      location.replace(httpsUrl);
      return;
    }
    window.alert(
      "Without HTTPS, your browser will not put images on the clipboard. " +
        "Enable SSL on your host (e.g. PythonAnywhere: force HTTPS) or open the site with https://."
    );
    if (triggerBtn) {
      triggerBtn.textContent = oldTxt;
      triggerBtn.disabled = false;
    }
  }

  function writePngBlobToClipboard(blob) {
    if (!canUseClipboardImage()) return Promise.resolve(false);
    function tryWrite(item) {
      return navigator.clipboard
        .write([item])
        .then(function () {
          return true;
        })
        .catch(function () {
          return false;
        });
    }
    try {
      return tryWrite(new ClipboardItem({ "image/png": blob }));
    } catch (e1) {
      try {
        return tryWrite(
          new ClipboardItem({
            "image/png": Promise.resolve(blob),
          })
        );
      } catch (e2) {
        return Promise.resolve(false);
      }
    }
  }

  /**
   * PNG blob for the share card. Used with ClipboardItem({ 'image/png': thisPromise }) so
   * navigator.clipboard.write runs synchronously from the click handler (Safari / strict
   * user-activation: awaiting fetch/canvas before write breaks the gesture chain).
   */
  function buildPlayerCardPngBlobPromise(playerId, theme, meta) {
    meta = meta || {};
    meta.filename = "player-card.png";
    return fetch(withRoot("/api/player/" + playerId + "/hover-card"))
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (!d || d.error) throw new Error("load");
        var base = (d.name || "player").replace(/[^\w\-]+/g, "-").replace(/^-|-$/g, "") || "player";
        meta.filename = base + "-card.png";
        return loadHtml2CanvasLib().then(function (h2c) {
          var stage = ensurePlayerShareCardStage();
          stage.innerHTML = "";
          var el = buildShareCardFromData(d, theme);
          stage.appendChild(el);
          return whenImagesLoaded(el).then(function () {
            return new Promise(function (resolve) {
              requestAnimationFrame(function () {
                resolve(
                  h2c(el, {
                    /* Scale 2 hits clipboard / canvas size limits on some Windows setups. */
                    scale: 1.25,
                    useCORS: true,
                    allowTaint: false,
                    backgroundColor: theme === "dark" ? "#111827" : "#ffffff",
                    logging: false,
                  })
                );
              });
            });
          });
        });
      })
      .then(function (canvas) {
        return new Promise(function (resolve, reject) {
          canvas.toBlob(function (blob) {
            if (!blob) reject(new Error("blob"));
            else resolve(blob);
          }, "image/png");
        });
      })
      .then(function (blob) {
        if (blob.type === "image/png") return blob;
        return blob.arrayBuffer().then(function (buf) {
          return new Blob([buf], { type: "image/png" });
        });
      });
  }

  function fmtPMShare(v) {
    if (v == null || v === "") return "—";
    var n = Number(v);
    if (!isFinite(n)) return "—";
    if (n > 0) return "+" + String(n);
    return String(n);
  }

  function buildShareCardFromData(d, theme) {
    var tcls = theme === "dark" ? "player-share-card--dark" : "player-share-card--light";
    var league = d.league_display_name || "Boys of Winter Hockey League";
    var teamNm = d.team_name || (d.team_abbr ? d.team_abbr : "Free agent");
    var pos = d.position || "—";
    var nat = d.nationality || "—";
    var shoots = d.shoots || "—";
    if (/^l/i.test(shoots)) shoots = "Left";
    else if (/^r/i.test(shoots)) shoots = "Right";
    var hw =
      formatHeight(d.height_inches) +
      " · " +
      (d.weight_lbs != null ? escapeHtml(String(d.weight_lbs)) + " lbs" : "—");
    var contr = d.contract;
    var contractLine = "";
    if (contr && (contr.aav != null || contr.years_left != null)) {
      if (contr.aav != null) contractLine += "AAV " + escapeHtml(fmtMoneyShare(contr.aav));
      if (contr.years_left != null) {
        if (contr.aav != null) contractLine += " · ";
        contractLine +=
          escapeHtml(String(contr.years_left)) +
          " yr" +
          (Number(contr.years_left) === 1 ? "" : "s") +
          " left";
      }
    }
    var logoHtml = d.team_logo_url
      ? '<img class="player-share-card__team-logo" src="' + escapeAttr(d.team_logo_url) + '" alt="">'
      : '<span class="player-share-card__team-logo-ph"></span>';
    var leagueLogoHtml = d.league_logo_url
      ? '<img class="player-share-card__league-logo" src="' + escapeAttr(d.league_logo_url) + '" alt="">'
      : "";
    var photoHtml = d.photo_url
      ? '<img class="player-share-card__photo" src="' + escapeAttr(d.photo_url) + '" alt="">'
      : '<span class="player-share-card__photo-ph">No photo</span>';
    function shareFmtFixed1(v) {
      if (v == null || v === "") return "—";
      var n = Number(v);
      return isFinite(n) ? escapeHtml(n.toFixed(1)) : "—";
    }
    function shareFmtInt(v) {
      if (v == null || v === "") return "—";
      var n = Number(v);
      return isFinite(n) ? escapeHtml(String(Math.round(n))) : "—";
    }
    var ovr = shareFmtInt(d.player_ovr);
    var abiS = shareFmtFixed1(d.abi);
    var potS = shareFmtFixed1(d.pot);
    function scoreTile(label, value, cls) {
      return (
        '<div class="player-share-card__score player-share-card__score--' +
        cls +
        '"><span>' +
        escapeHtml(label) +
        "</span><strong>" +
        value +
        "</strong></div>"
      );
    }
    var scores =
      '<div class="player-share-card__scores">' +
      scoreTile("Overall", ovr, "ovr") +
      scoreTile("Ability", abiS, "ap") +
      scoreTile("Potential", potS, "ap") +
      "</div>";
    var at = d.attrs || {};
    var chipRow = "";
    if (d.is_goalie) {
      chipRow =
        '<div class="player-share-card__chip-row">' +
        '<span class="player-share-card__chip">GOA <strong style="' +
        attrColorStyle(at.goa) +
        '">' +
        escapeHtml(String(at.goa != null ? at.goa : "—")) +
        "</strong></span>" +
        '<span class="player-share-card__chip">MEN <strong style="' +
        attrColorStyle(at.men) +
        '">' +
        escapeHtml(String(at.men != null ? at.men : "—")) +
        "</strong></span></div>";
    } else {
      chipRow =
        '<div class="player-share-card__chip-row">' +
        '<span class="player-share-card__chip">OFF <strong style="' +
        attrColorStyle(at.off) +
        '">' +
        escapeHtml(String(at.off != null ? at.off : "—")) +
        "</strong></span>" +
        '<span class="player-share-card__chip">DEF <strong style="' +
        attrColorStyle(at.def) +
        '">' +
        escapeHtml(String(at.def != null ? at.def : "—")) +
        "</strong></span>" +
        '<span class="player-share-card__chip">PHY <strong style="' +
        attrColorStyle(at.phy) +
        '">' +
        escapeHtml(String(at.phy != null ? at.phy : "—")) +
        "</strong></span>" +
        '<span class="player-share-card__chip">MEN <strong style="' +
        attrColorStyle(at.men) +
        '">' +
        escapeHtml(String(at.men != null ? at.men : "—")) +
        "</strong></span></div>";
    }
    var rc = d.rating_columns || { left: [], right: [] };
    var sections = d.rating_sections || [];
    function sectionHtml(title, rows) {
      var h =
        '<div class="player-share-card__section"><div class="player-share-card__section-title">' +
        escapeHtml(title) +
        "</div>";
      (rows || []).forEach(function (row) {
        var nv = parseFloat(row.value);
        var st = attrColorStyle(isNaN(nv) ? null : nv);
        var vs =
          row.value === "—"
            ? "—"
            : '<strong style="' + st + '">' + escapeHtml(row.value) + "</strong>";
        h +=
          '<div class="player-share-card__rating-row"><span class="player-share-card__rating-lbl">' +
          escapeHtml(row.label) +
          '</span><span class="player-share-card__rating-val">' +
          vs +
          "</span></div>";
      });
      h += "</div>";
      return h;
    }
    var ratingsBlk = '<div class="player-share-card__section-grid">';
    if (sections && sections.length) {
      sections.forEach(function (sec) {
        ratingsBlk += sectionHtml(sec.title || "Attributes", sec.rows || []);
      });
    } else {
      ratingsBlk +=
        sectionHtml(d.is_goalie ? "Goalie" : "Attributes", rc.left) +
        sectionHtml(d.is_goalie ? "Mental" : "Attributes", rc.right);
    }
    ratingsBlk += "</div>";
    var posRatingsHtml = "";
    var pr = d.position_ratings;
    if (pr && pr.length) {
      posRatingsHtml =
        '<div class="player-share-card__section player-share-card__pos-ratings">' +
        '<div class="player-share-card__section-title">Position ratings</div>';
      pr.forEach(function (row) {
        var lbl =
          escapeHtml(row.label || "") +
          (row.is_primary ? '<span class="player-share-card__pos-star" aria-hidden="true"> *</span>' : "");
        var nv = row.value;
        var disp =
          nv == null || nv === "" || !isFinite(Number(nv))
            ? "—"
            : escapeHtml(String(Math.round(Number(nv))));
        var barStyle =
          nv != null && nv !== "" && isFinite(Number(nv))
            ? posRatingMeterFillStyle(Number(nv))
            : "width:0%;background-color:transparent";
        posRatingsHtml +=
          '<div class="player-share-card__pos-row">' +
          '<span class="player-share-card__pos-lbl">' +
          lbl +
          '</span><span class="player-share-card__pos-val">' +
          disp +
          '</span><div class="player-share-card__pos-track"><div class="player-share-card__pos-fill" style="' +
          escapeAttr(barStyle) +
          '"></div></div></div>';
      });
      var shootsCell = (d.shoots || "").trim();
      if (/^l/i.test(shootsCell)) shootsCell = "Left";
      else if (/^r/i.test(shootsCell)) shootsCell = "Right";
      else if (!shootsCell) shootsCell = "—";
      posRatingsHtml +=
        '<div class="player-share-card__pos-row player-share-card__pos-row--shoots">' +
        '<span class="player-share-card__pos-lbl">Shoots</span>' +
        '<span class="player-share-card__pos-val">' +
        escapeHtml(shootsCell) +
        '</span><div class="player-share-card__pos-track player-share-card__pos-track--empty"></div></div>';
      posRatingsHtml += "</div>";
    }
    var statsBlk = "";
    if (!d.retired && d.latest_season_stats) {
      var s = d.latest_season_stats;
      function statCell(k, v) {
        var vs = v == null || v === "" ? "—" : escapeHtml(String(v));
        return (
          '<div class="player-share-card__stat"><span class="player-share-card__stat-k">' +
          escapeHtml(k) +
          '</span><span class="player-share-card__stat-v">' +
          vs +
          "</span></div>"
        );
      }
      var grid = "";
      if (d.is_goalie) {
        grid +=
          statCell("GP", s.gp) +
          statCell("Record", s.record) +
          statCell("GAA", s.gaa != null ? Number(s.gaa).toFixed(2) : null) +
          statCell("SV%", s.sv_pct != null ? Number(s.sv_pct).toFixed(3) : null) +
          statCell("GR", s.gr) +
          statCell("GS", s.gs) +
          statCell("SO", s.so) +
          statCell("TOI/G", s.toi_pg) +
          statCell("SA", s.sa) +
          statCell("SV", s.saves) +
          statCell("GA", s.ga);
      } else {
        grid +=
          statCell("GP", s.gp) +
          statCell("G", s.goals) +
          statCell("A", s.assists) +
          statCell("PTS", s.points) +
          statCell("+/-", fmtPMShare(s.plus_minus)) +
          statCell("PIM", s.pim) +
          statCell("SOG", s.shots) +
          statCell("HIT", s.hits) +
          statCell("BS", s.blocked_shots) +
          statCell("ATOI", s.toi_pg) +
          statCell("GR", s.gr) +
          statCell("PDO", s.pdo);
      }
      statsBlk =
        '<div class="player-share-card__stats"><div class="player-share-card__stats-title">' +
        escapeHtml(String(s.season || "Season")) +
        ' stats</div><div class="player-share-card__stats-grid">' +
        grid +
        "</div></div>";
    }
    var html =
      '<div class="player-share-card ' +
      tcls +
      '">' +
      '<div class="player-share-card__header">' +
      logoHtml +
      '<div class="player-share-card__head-text">' +
      '<div class="player-share-card__pos">' +
      escapeHtml(pos) +
      "</div>" +
      '<div class="player-share-card__name">' +
      escapeHtml(d.name || "Player") +
      "</div>" +
      '<div class="player-share-card__team">' +
      escapeHtml(teamNm) +
      "</div></div>" +
      '<div class="player-share-card__contract">' +
      '<div>Age ' +
      escapeHtml(String(d.age != null ? d.age : "—")) +
      " · " +
      escapeHtml(nat) +
      "</div>" +
      (contractLine ? "<strong>" + contractLine + "</strong>" : "") +
      "</div></div>" +
      '<div class="player-share-card__sub">' +
      hw +
      " · Shoots " +
      escapeHtml(shoots) +
      "</div>" +
      scores +
      '<div class="player-share-card__hero">' +
      photoHtml +
      '<div class="player-share-card__body-main">' +
      chipRow +
      "</div></div>" +
      '<div class="player-share-card__content">' +
      '<div class="player-share-card__top-grid">' +
      posRatingsHtml +
      "</div>" +
      ratingsBlk +
      statsBlk +
      "</div>" +
      '<div class="player-share-card__footer">' +
      leagueLogoHtml +
      '<span>' +
      escapeHtml(league) +
      "</span></div></div>";
    var wrap = document.createElement("div");
    wrap.innerHTML = html;
    return wrap.firstElementChild;
  }

  function copyPlayerShareCardImage(playerId, triggerBtn) {
    var theme = getUiTheme();
    var oldTxt = triggerBtn ? triggerBtn.textContent : "";
    var meta = { filename: "player-card.png" };

    function resetBtn() {
      if (triggerBtn) {
        triggerBtn.textContent = oldTxt;
        triggerBtn.disabled = false;
      }
    }
    function okCopied() {
      if (triggerBtn) {
        triggerBtn.textContent = "Copied!";
        setTimeout(function () {
          resetBtn();
        }, 1600);
      }
    }
    function alertBuildErr(err) {
      var msg = "Could not build the player card image.";
      if (err && err.message === "html2canvas not loaded") {
        msg = "Image library failed to load. Try a hard refresh (Ctrl+F5).";
      } else if (err && err.message === "load") {
        msg = "Could not load player data from the server.";
      } else if (err && err.message === "blob") {
        msg = "Could not render the card image.";
      }
      window.alert(msg);
      resetBtn();
    }

    if (shouldOfferHttpsForClipboard()) {
      offerSwitchToHttpsForClipboardPaste(triggerBtn, oldTxt);
      return;
    }

    if (!canUseClipboardImage()) {
      var hintEl = document.getElementById("player-copy-card-hint");
      if (hintEl && !hintEl.hidden) {
        resetBtn();
        hintEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
        return;
      }
      var insecureMsg =
        "The browser only allows copying images from a secure page (https:// or http://localhost).\n\n" +
        "Use one of these:\n" +
        "• Open http://localhost:PORT on the PC running the app, then Copy player card, or\n" +
        "• Start the dev server with HTTPS: set FLASK_DEV_HTTPS=1, run run.py, open https://THIS_IP:PORT " +
        "(accept the self-signed certificate warning), then copy.";
      if (isRfc1918Hostname(location.hostname)) {
        insecureMsg =
          "Image clipboard is turned off by the browser for http:// on a LAN address (192.168.x.x).\n\n" +
          "Fix (pick one):\n" +
          "• http://localhost:PORT on the machine running Flask — then paste works, or\n" +
          "• HTTPS on your LAN: PowerShell: $env:FLASK_DEV_HTTPS='1'; python run.py\n" +
          "  Then open https://" +
          String(location.host || location.hostname || "") +
          " and accept the certificate warning.\n\n" +
          "There is no way to put a PNG on the clipboard from this exact URL without one of the above.";
      }
      window.alert(insecureMsg);
      resetBtn();
      return;
    }

    function tryWritePngBlob(blob) {
      var b =
        blob && blob.type === "image/png"
          ? blob
          : new Blob([blob], { type: "image/png" });
      return navigator.clipboard
        .write([new ClipboardItem({ "image/png": b })])
        .catch(function () {
          return navigator.clipboard.write([
            new ClipboardItem({ "image/png": Promise.resolve(b) }),
          ]);
        })
        .catch(function () {
          return writePngBlobToClipboard(b).then(function (ok) {
            if (!ok) throw new Error("no-clipboard");
          });
        });
    }

    function isCardBuildError(err) {
      if (!err || !err.message) return false;
      return (
        err.message === "html2canvas not loaded" ||
        err.message === "load" ||
        err.message === "blob"
      );
    }

    function explainClipboardFailure() {
      window.alert(
        "The image was not copied. Common fixes:\n\n" +
          "• Chrome/Edge: lock icon → Site settings → Clipboard → Allow.\n" +
          "• Use https:// or http://localhost for this page.\n\n" +
          "Then try Copy player card again."
      );
      resetBtn();
    }

    if (triggerBtn) {
      triggerBtn.disabled = true;
      triggerBtn.textContent = "…";
    }
    var blobPromise = buildPlayerCardPngBlobPromise(playerId, theme, meta);

    var primaryWrite;
    try {
      primaryWrite = navigator.clipboard.write([
        new ClipboardItem({ "image/png": blobPromise }),
      ]);
    } catch (e) {
      primaryWrite = Promise.reject(e);
    }

    primaryWrite
      .then(function () {
        okCopied();
      })
      .catch(function () {
        return blobPromise
          .then(function (blob) {
            return tryWritePngBlob(blob);
          })
          .then(function () {
            okCopied();
          });
      })
      .catch(function (err) {
        if (isCardBuildError(err)) {
          alertBuildErr(err);
          return;
        }
        explainClipboardFailure();
      });
  }

  function initPlayerHoverCards() {
    var cache = {};
    var HOVER_CARD_CACHE_VER = 9;
    var activeAnchor = null;
    var showTimer = null;
    var hideTimer = null;
    var card = document.createElement("div");
    card.className = "player-hover-card";
    card.hidden = true;
    document.body.appendChild(card);

    function hideCard() {
      card.hidden = true;
      card.classList.remove("is-visible", "hover-preview-card--dock");
      activeAnchor = null;
    }

    function scheduleHide() {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideCard, 120);
    }

    function moveCardNear(anchor) {
      if (!anchor) return;
      if (isTouchLikeDevice()) {
        dockHoverCard(card);
        return;
      }
      card.classList.remove("hover-preview-card--dock");
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

    function fmtPlusMinus(v) {
      if (v == null || v === "") return "—";
      var n = Number(v);
      if (!isFinite(n)) return "—";
      if (n > 0) return "+" + String(n);
      return String(n);
    }

    function hoverRecentSeasonsTmCell(r) {
      if (r.team_logo_url) {
        return (
          '<td class="player-hover-seasons__tm">' +
          '<img src="' +
          escapeAttr(r.team_logo_url) +
          '" alt="" class="player-hover-seasons__tm-img" width="24" height="24" loading="lazy" decoding="async">' +
          "</td>"
        );
      }
      return '<td class="player-hover-seasons__tm">—</td>';
    }

    function hoverRecentSeasonsBlock(d) {
      if (d.retired) return "";
      var rows = d.recent_seasons || [];
      if (!rows.length) return "";
      var role = d.recent_seasons_role || "skater";
      var h =
        '<div class="player-hover-seasons"><div class="player-hover-seasons__title">Recent seasons (RS)</div><table class="player-hover-seasons__table">';
      if (role === "goalie") {
        h +=
          "<thead><tr><th>Season</th><th title=\"Team (era logo)\">TM</th><th>GP</th><th>W</th><th>L</th><th>GA</th><th title=\"Shutouts\">SO</th><th>SV%</th></tr></thead><tbody>";
        rows.forEach(function (r) {
          var sv = r.sv_pct;
          var svS = sv == null ? "—" : escapeHtml(Number(sv).toFixed(3));
          h +=
            "<tr><td>" +
            escapeHtml(r.season || "—") +
            "</td>" +
            hoverRecentSeasonsTmCell(r) +
            "<td>" +
            escapeHtml(String(r.gp != null ? r.gp : "—")) +
            "</td><td>" +
            escapeHtml(String(r.wins != null ? r.wins : "—")) +
            "</td><td>" +
            escapeHtml(String(r.losses != null ? r.losses : "—")) +
            "</td><td>" +
            escapeHtml(String(r.ga != null ? r.ga : "—")) +
            "</td><td>" +
            escapeHtml(String(r.so != null ? r.so : "—")) +
            "</td><td>" +
            svS +
            "</td></tr>";
        });
      } else {
        h +=
          "<thead><tr><th>Season</th><th title=\"Team (era logo)\">TM</th><th>GP</th><th>G</th><th>A</th><th>PTS</th><th>PIM</th><th>+/-</th></tr></thead><tbody>";
        rows.forEach(function (r) {
          h +=
            "<tr><td>" +
            escapeHtml(r.season || "—") +
            "</td>" +
            hoverRecentSeasonsTmCell(r) +
            "<td>" +
            escapeHtml(String(r.gp != null ? r.gp : "—")) +
            "</td><td>" +
            escapeHtml(String(r.goals != null ? r.goals : "—")) +
            "</td><td>" +
            escapeHtml(String(r.assists != null ? r.assists : "—")) +
            "</td><td>" +
            escapeHtml(String(r.points != null ? r.points : "—")) +
            "</td><td>" +
            escapeHtml(String(r.pim != null ? r.pim : "—")) +
            "</td><td>" +
            escapeHtml(fmtPlusMinus(r.plus_minus)) +
            "</td></tr>";
        });
      }
      h += "</tbody></table></div>";
      return h;
    }

    function renderCard(d, playerIdForCard) {
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
      var ovrHtml =
        d.player_ovr != null && d.player_ovr !== ""
          ? '<span class="player-hover-card__ovr"> · ' + escapeHtml(String(d.player_ovr)) + " OVR</span>"
          : "";
      var seasonsHtml = hoverRecentSeasonsBlock(d);
      var hofBadgeHtml =
        d.is_hof && d.hof_badge_url
          ? '<img class="player-hover-card__hof-badge" src="' +
            escapeAttr(d.hof_badge_url) +
            '" alt="Hall of Fame" loading="lazy" decoding="async">'
          : "";
      var boostBadgeHtml =
        d.boost_badge_url && d.boost_tier
          ? '<img class="player-hover-card__boost-badge player-hover-card__boost-badge--' +
            escapeAttr(String(d.boost_tier).toLowerCase()) +
            '" src="' +
            escapeAttr(d.boost_badge_url) +
            '" alt="' +
            escapeAttr(d.boost_badge_label || d.boost_tier + " boost") +
            '" title="' +
            escapeAttr(d.boost_badge_label || d.boost_tier + " boost") +
            '" loading="lazy" decoding="async">'
          : "";
      card.classList.toggle("player-hover-card--hof", !!hofBadgeHtml);
      card.classList.toggle("player-hover-card--boosted", !!boostBadgeHtml);
      var copyBar =
        '<div class="player-hover-card__toolbar">' +
        (isTouchLikeDevice()
          ? '<button type="button" class="player-hover-card__close js-close-hover-card" aria-label="Close preview">×</button>'
          : "") +
        '<button type="button" class="player-hover-card__copy js-copy-player-card" data-player-id="' +
        escapeAttr(String(playerIdForCard)) +
        '" title="Copies the player card image to the clipboard for Discord.">Copy card</button></div>';
      card.innerHTML =
        copyBar +
        hofBadgeHtml +
        boostBadgeHtml +
        '<div class="player-hover-card__row">' +
        '<div class="player-hover-card__photo">' +
        (d.photo_url
          ? '<img src="' + escapeAttr(d.photo_url) + '" alt="">'
          : '<span class="player-hover-card__photo-ph"></span>') +
        "</div>" +
        '<div class="player-hover-card__body">' +
        '<div class="player-hover-card__name">' + escapeHtml(d.name || "Player") + ovrHtml + "</div>" +
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
        seasonsHtml +
        "</div></div>";
    }

    function showFor(anchor, playerId) {
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      showTimer = setTimeout(function () {
        activeAnchor = anchor;
        var cached = cache[playerId];
        if (cached && cached._hoverFmt === HOVER_CARD_CACHE_VER) {
          renderCard(cached, playerId);
          card.hidden = false;
          card.classList.add("is-visible");
          moveCardNear(anchor);
          return;
        }
        fetch(withRoot("/api/player/" + playerId + "/hover-card"))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d || d.error) return;
            d._hoverFmt = HOVER_CARD_CACHE_VER;
            cache[playerId] = d;
            if (activeAnchor !== anchor) return;
            renderCard(d, playerId);
            card.hidden = false;
            card.classList.add("is-visible");
            moveCardNear(anchor);
          })
          .catch(function () {});
      }, 120);
    }

    card.addEventListener("mouseenter", function () {
      clearTimeout(hideTimer);
    });
    card.addEventListener("mouseleave", scheduleHide);
    card.addEventListener("click", function (e) {
      if (e.target.closest(".js-close-hover-card")) {
        e.preventDefault();
        hideCard();
      }
    });

    function bindPlayerHoverAnchors() {
      document.querySelectorAll('a[href*="/player/"]').forEach(function (a) {
        if (a.getAttribute("data-player-hover-bound") === "1") return;
        var playerId = playerIdFromHref(a.getAttribute("href"));
        if (!playerId) return;
        a.setAttribute("data-player-hover-bound", "1");
        if (isTouchLikeDevice()) {
          bindLongPressPreview(a, function () {
            showFor(a, playerId);
          });
        } else {
          a.addEventListener("mouseenter", function () { showFor(a, playerId); });
          a.addEventListener("mouseleave", scheduleHide);
          a.addEventListener("focusin", function () { showFor(a, playerId); });
          a.addEventListener("focusout", scheduleHide);
        }
      });
    }

    bindPlayerHoverAnchors();

    if (isTouchLikeDevice()) {
      document.addEventListener("click", function (e) {
        if (card.hidden) return;
        if (card.contains(e.target)) return;
        if (activeAnchor && (activeAnchor === e.target || activeAnchor.contains(e.target))) return;
        hideCard();
      });
    }

    window.addEventListener("scroll", function () {
      if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
    }, { passive: true });
    window.addEventListener("resize", function () {
      if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
    });

    return bindPlayerHoverAnchors;
  }

  function initTeamHoverCards() {
    var cache = {};
    var HOVER_TEAM_CACHE_VER = 2;
    var activeAnchor = null;
    var showTimer = null;
    var hideTimer = null;
    var card = document.createElement("div");
    card.className = "team-hover-preview-card";
    card.hidden = true;
    document.body.appendChild(card);

    function hideCard() {
      card.hidden = true;
      card.classList.remove("hover-preview-card--dock");
      activeAnchor = null;
    }

    function scheduleHide() {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideCard, 140);
    }

    function moveCardNear(anchor) {
      if (!anchor) return;
      if (isTouchLikeDevice()) {
        dockHoverCard(card);
        return;
      }
      card.classList.remove("hover-preview-card--dock");
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

    function fmtDec(v) {
      if (v == null || v === "") return "—";
      var n = Number(v);
      if (!isFinite(n)) return "—";
      return n.toFixed(1);
    }

    function renderTeamCard(d) {
      var r = d.record || {};
      var rec =
        String(r.w != null ? r.w : "0") +
        "-" +
        String(r.l != null ? r.l : "0") +
        "-" +
        String(r.t != null ? r.t : "0") +
        "-" +
        String(r.otl != null ? r.otl : "0");
      var pts = r.pts != null ? r.pts : "—";
      var rk = d.overall_rank != null ? "#" + String(d.overall_rank) : "—";
      var nteams = d.n_teams != null ? " / " + String(d.n_teams) : "";
      var rankLine =
        '<span class="team-hover-preview-card__rank-label">Rank:</span> ' +
        '<span class="team-hover-preview-card__rank">' +
        escapeHtml(rk) +
        "</span>" +
        (nteams ? '<span class="team-hover-preview-card__rank-of">' + escapeHtml(nteams) + "</span>" : "");
      var sub = d.conf_div ? escapeHtml(d.conf_div) : "";
      var statsParts = [];
      if (r.gf != null) statsParts.push("GF " + escapeHtml(String(r.gf)));
      if (r.ga != null) statsParts.push("GA " + escapeHtml(String(r.ga)));
      if (d.pp_pct != null) statsParts.push("PP% " + escapeHtml(String(d.pp_pct)) + "%");
      if (d.pk_pct != null) statsParts.push("PK% " + escapeHtml(String(d.pk_pct)) + "%");
      var statsBar =
        statsParts.length > 0
          ? '<div class="team-hover-preview-card__stats-bar">' + statsParts.join(" | ") + "</div>"
          : "";
      var streakLine = "";
      if (d.streak_subtext && String(d.streak_subtext).trim()) {
        streakLine =
          '<div class="team-hover-preview-card__streak team-hover-preview-card__streak--record">' +
          escapeHtml(String(d.streak_subtext).trim()) +
          "</div>";
      } else if (d.streak && String(d.streak).trim()) {
        streakLine =
          '<div class="team-hover-preview-card__streak team-hover-preview-card__streak--record">' +
          "Streak: " +
          escapeHtml(String(d.streak).trim()) +
          "</div>";
      }

      var rows = "";
      (d.players || []).forEach(function (p) {
        var ovrInner =
          p.ovr != null && p.ovr !== ""
            ? escapeHtml(String(p.ovr))
            : '<span class="team-hover-preview-card__ovr-num--muted">—</span>';
        var badges =
          '<div class="team-hover-preview-card__pbadges">' +
          '<div class="team-hover-preview-card__ovr-col">' +
          '<span class="team-hover-preview-card__score-lbl">OVR</span>' +
          '<span class="team-hover-preview-card__ovr-num">' +
          ovrInner +
          "</span></div>" +
          '<div class="team-hover-preview-card__ap-col">' +
          '<div class="team-hover-preview-card__ap-row">' +
          '<span class="team-hover-preview-card__score-lbl">ABI</span>' +
          '<span class="team-hover-preview-card__badge team-hover-preview-card__badge--abi">' +
          escapeHtml(fmtDec(p.abi)) +
          "</span></div>" +
          '<div class="team-hover-preview-card__ap-row">' +
          '<span class="team-hover-preview-card__score-lbl">POT</span>' +
          '<span class="team-hover-preview-card__badge team-hover-preview-card__badge--pot">' +
          escapeHtml(fmtDec(p.pot)) +
          "</span></div></div></div>";
        var ph =
          p.photo_url
            ? '<img src="' + escapeAttr(p.photo_url) + '" alt="">'
            : '<span class="team-hover-preview-card__ph"></span>';
        var nameL =
          p.url
            ? '<a class="team-hover-preview-card__pname" href="' + escapeAttr(p.url) + '">' + escapeHtml(p.name || "") + "</a>"
            : '<span class="team-hover-preview-card__pname">' + escapeHtml(p.name || "") + "</span>";
        rows +=
          '<div class="team-hover-preview-card__prow">' +
          '<div class="team-hover-preview-card__pphoto">' +
          ph +
          "</div>" +
          '<div class="team-hover-preview-card__pbody">' +
          '<div class="team-hover-preview-card__prole">' +
          escapeHtml(p.role || "") +
          "</div>" +
          nameL +
          '<div class="team-hover-preview-card__pmeta">' +
          escapeHtml(p.pos_age || "") +
          "</div>" +
          "</div>" +
          badges +
          "</div>";
      });

      if (!rows && d.team_slug) {
        rows =
          '<div class="team-hover-preview-card__empty">No NHL roster preview (imports / ratings).</div>';
      }

      var logo =
        d.logo_url
          ? '<img src="' + escapeAttr(d.logo_url) + '" alt="">'
          : '<span class="team-hover-preview-card__logo-ph"></span>';
      var footParts = [];
      if (d.season_label) footParts.push(escapeHtml(d.season_label));
      if (d.league_display_name) footParts.push(escapeHtml(d.league_display_name));
      var footInner = footParts.join(" · ");
      if (d.team_url) {
        footInner +=
          (footInner ? " · " : "") +
          '<a class="team-hover-preview-card__foot-link" href="' +
          escapeAttr(d.team_url) +
          '">Roster & stats →</a>';
      }
      var footer = footInner ? '<div class="team-hover-preview-card__footer">' + footInner + "</div>" : "";

      card.innerHTML =
        (isTouchLikeDevice()
          ? '<button type="button" class="team-hover-preview-card__close js-close-hover-card" aria-label="Close preview">×</button>'
          : "") +
        '<div class="team-hover-preview-card__shell">' +
        '<div class="team-hover-preview-card__head">' +
        '<div class="team-hover-preview-card__head-main">' +
        '<div class="team-hover-preview-card__logo-wrap">' +
        logo +
        "</div>" +
        '<div class="team-hover-preview-card__head-text">' +
        '<div class="team-hover-preview-card__title">' +
        escapeHtml(d.team_name || "Team") +
        "</div>" +
        (sub ? '<div class="team-hover-preview-card__sub">' + sub + "</div>" : "") +
        '<div class="team-hover-preview-card__record-line">' +
        escapeHtml(rec) +
        " · " +
        escapeHtml(String(pts)) +
        " pts · " +
        rankLine +
        "</div>" +
        streakLine +
        "</div></div></div>" +
        statsBar +
        (rows ? '<div class="team-hover-preview-card__players">' + rows + "</div>" : "") +
        footer +
        "</div>";
    }

    function showFor(anchor, slug) {
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      showTimer = setTimeout(function () {
        activeAnchor = anchor;
        var cached = cache[slug];
        if (cached && cached._hoverTeamFmt === HOVER_TEAM_CACHE_VER) {
          renderTeamCard(cached);
          card.hidden = false;
          moveCardNear(anchor);
          return;
        }
        fetch(withRoot("/api/team-hover-preview?slug=" + encodeURIComponent(slug)))
          .then(function (r) {
            return r.json();
          })
          .then(function (d) {
            if (!d || d.error) return;
            d._hoverTeamFmt = HOVER_TEAM_CACHE_VER;
            cache[slug] = d;
            if (activeAnchor !== anchor) return;
            renderTeamCard(d);
            card.hidden = false;
            moveCardNear(anchor);
          })
          .catch(function () {});
      }, 140);
    }

    card.addEventListener("mouseenter", function () {
      clearTimeout(hideTimer);
    });
    card.addEventListener("mouseleave", scheduleHide);
    card.addEventListener("click", function (e) {
      if (e.target.closest(".js-close-hover-card")) {
        e.preventDefault();
        hideCard();
      }
    });

    function bindTeamHoverAnchors() {
      document.querySelectorAll('a[href*="/team/"]').forEach(function (a) {
        if (a.getAttribute("data-team-hover-bound") === "1") return;
        var slug = teamSlugFromHref(a.getAttribute("href"));
        if (!slug) return;
        a.setAttribute("data-team-hover-bound", "1");
        if (isTouchLikeDevice()) {
          bindLongPressPreview(a, function () {
            showFor(a, slug);
          });
        } else {
          a.addEventListener("mouseenter", function () {
            showFor(a, slug);
          });
          a.addEventListener("mouseleave", scheduleHide);
          a.addEventListener("focusin", function () {
            showFor(a, slug);
          });
          a.addEventListener("focusout", scheduleHide);
        }
      });
    }

    bindTeamHoverAnchors();

    if (isTouchLikeDevice()) {
      document.addEventListener("click", function (e) {
        if (card.hidden) return;
        if (card.contains(e.target)) return;
        if (activeAnchor && (activeAnchor === e.target || activeAnchor.contains(e.target))) return;
        hideCard();
      });
    }

    window.addEventListener(
      "scroll",
      function () {
        if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
      },
      { passive: true }
    );
    window.addEventListener("resize", function () {
      if (!card.hidden && activeAnchor) moveCardNear(activeAnchor);
    });

    return bindTeamHoverAnchors;
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
    refreshGamePreviewOddsSegStyles();
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

  /** Browsers (including Cursor) only allow image clipboard on https:// or http://localhost — not http://192.168… */
  function playerSeasonTrendsTooltipText(ds) {
    if (!ds || !ds.kind) return "";
    var season = ds.season || "";
    if (ds.kind === "goalie") {
      var gParts =
        "Season " +
        season +
        "\nGP " +
        ds.gp +
        " · W " +
        ds.w +
        " · L " +
        ds.l +
        " · T " +
        ds.t +
        "\nShutouts " +
        ds.so;
      if (ds.ovr != null && String(ds.ovr).trim() !== "") {
        gParts += "\nGR " + String(ds.ovr).trim();
      }
      return gParts;
    }
    var pts = ds.pts != null && ds.pts !== "" ? ds.pts : "—";
    var gpSk = ds.gp != null && ds.gp !== "" ? ds.gp : "—";
    var sParts =
      "Season " +
      season +
      "\nGP " +
      gpSk +
      "\nGoals " +
      ds.g +
      " · Assists " +
      ds.a +
      "\nPoints " +
      pts;
    if (ds.ovr != null && String(ds.ovr).trim() !== "") {
      sParts += "\nGR " + String(ds.ovr).trim();
    }
    return sParts;
  }

  function playerSeasonTrendsDatasetFromTarget(el) {
    if (!el || !el.closest) return null;
    var hit = el.closest(".player-season-trends__hit");
    if (hit && hit.dataset && hit.dataset.kind) return hit.dataset;
    var a = el.closest(".player-season-trends__season-hit");
    if (a) {
      var r = a.querySelector(".player-season-trends__hit");
      if (r && r.dataset && r.dataset.kind) return r.dataset;
    }
    return null;
  }

  function initPlayerSeasonTrendCharts() {
    document.querySelectorAll(".player-season-trends").forEach(function (card) {
      var wrap = card.querySelector(".player-season-trends__chart-wrap");
      var tip = card.querySelector("[data-player-season-trends-tooltip]");
      if (!wrap || !tip) return;

      function positionTip(clientX, clientY) {
        var br = wrap.getBoundingClientRect();
        var pad = 8;
        var offsetY = 14;
        var lx = clientX - br.left + wrap.scrollLeft;
        var ly = clientY - br.top + wrap.scrollTop + offsetY;
        tip.hidden = false;
        var tw = tip.offsetWidth;
        var th = tip.offsetHeight;
        lx = Math.max(pad, Math.min(lx, wrap.scrollWidth - tw - pad));
        ly = Math.max(pad, Math.min(ly, wrap.scrollHeight - th - pad));
        tip.style.left = lx + "px";
        tip.style.top = ly + "px";
      }

      function showTip(ds, clientX, clientY) {
        var t = playerSeasonTrendsTooltipText(ds);
        if (!t) {
          tip.hidden = true;
          tip.textContent = "";
          return;
        }
        tip.textContent = t;
        tip.hidden = false;
        requestAnimationFrame(function () {
          positionTip(clientX, clientY);
        });
      }

      function hideTip() {
        tip.hidden = true;
        tip.textContent = "";
      }

      function onPointerOverChart(ev) {
        var ds = playerSeasonTrendsDatasetFromTarget(ev.target);
        if (!ds) {
          hideTip();
          return;
        }
        showTip(ds, ev.clientX, ev.clientY);
      }

      wrap.addEventListener("pointermove", onPointerOverChart);
      wrap.addEventListener("pointerleave", hideTip);
      wrap.addEventListener("pointercancel", hideTip);

      wrap.addEventListener("focusin", function (ev) {
        var ds = playerSeasonTrendsDatasetFromTarget(ev.target);
        if (!ds) return;
        var a = ev.target.closest && ev.target.closest(".player-season-trends__season-hit");
        if (a) {
          var ar = a.getBoundingClientRect();
          showTip(ds, ar.left + ar.width / 2, ar.top + ar.height / 2);
        } else {
          var br = wrap.getBoundingClientRect();
          showTip(ds, br.left + br.width / 2, br.top + 48);
        }
      });

      wrap.addEventListener("focusout", function (ev) {
        if (!wrap.contains(ev.relatedTarget)) hideTip();
      });
    });
  }

  function initPlayerShareCardClipboardHint() {
    var hint = document.getElementById("player-copy-card-hint");
    if (!hint || canUseClipboardImage()) return;
    var h = String(location.hostname || "").toLowerCase();
    var loopback = h === "localhost" || h === "127.0.0.1" || h === "[::1]";
    var port = location.port;
    var p = port ? ":" + port : "";
    var path = location.pathname + location.search + location.hash;
    var localUrl = "http://127.0.0.1" + p + path;
    hint.hidden = false;
    if (!loopback && String(location.protocol || "").toLowerCase() === "http:") {
      hint.innerHTML =
        "The Cursor browser (like Chrome) will not copy images from <strong>http://</strong> plus a LAN IP such as yours — only <strong>https://</strong> or <strong>http://localhost</strong> / <strong>127.0.0.1</strong> count as secure for clipboard. " +
        "On the PC running Flask, open " +
        '<a href="' +
        escapeAttr(localUrl) +
        '">the same page on 127.0.0.1</a>. ' +
        "Then use <strong>Copy player card</strong> once and paste (Ctrl+V) in Discord. " +
        "(If this browser is not on the same PC as the server, use <strong>https://</strong> to your LAN IP with dev TLS instead — see run.py <code style=font-size:0.85em>FLASK_DEV_HTTPS</code>.)";
    } else {
      hint.textContent =
        "Image copy needs a secure page (https:// or http://localhost). This URL cannot use the clipboard image API.";
    }
  }

  function initPlayerMeterAnimations() {
    var root = document.querySelector(".player-profile");
    if (!root) return;
    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    root.querySelectorAll(".player-profile__meter-fill").forEach(function (fill) {
      var style = fill.getAttribute("style") || "";
      var match = style.match(/width:\s*([^;]+)/i);
      if (!match) return;
      var target = match[1].trim();
      if (reduced) return;
      fill.style.width = "0%";
      requestAnimationFrame(function () {
        fill.classList.add("player-profile__meter-fill--animate");
        fill.style.width = target;
      });
    });
  }

  function initPageLoadBarNavigation() {
    var api = window.__bowlPageLoad;
    if (!api || typeof api.start !== "function") return;

    function shouldHandleLink(a) {
      if (!a || !a.href) return false;
      if (a.target && a.target !== "_self") return false;
      if (a.hasAttribute("download")) return false;
      var href = a.getAttribute("href");
      if (!href || href.charAt(0) === "#") return false;
      if (href.indexOf("javascript:") === 0) return false;
      try {
        var url = new URL(a.href, window.location.href);
        if (url.origin !== window.location.origin) return false;
        if (
          url.pathname === window.location.pathname &&
          url.search === window.location.search &&
          url.hash
        ) {
          return false;
        }
      } catch (err) {
        return false;
      }
      return true;
    }

    document.addEventListener(
      "click",
      function (ev) {
        var a = ev.target.closest("a");
        if (!shouldHandleLink(a)) return;
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey || ev.button !== 0) return;
        api.start();
      },
      true
    );

    document.addEventListener(
      "submit",
      function (ev) {
        var form = ev.target;
        if (!form || form.tagName !== "FORM") return;
        if (form.target && form.target !== "_self") return;
        api.start();
      },
      true
    );
  }

  document.addEventListener("DOMContentLoaded", function () {
    initPageLoadBarNavigation();
    applyTheme(getPreferredTheme());
    initPlayerShareCardClipboardHint();
    initPlayerSeasonTrendCharts();
    initPlayerMeterAnimations();
    window.bindPlayerHoverAnchors = initPlayerHoverCards();
    window.bindTeamHoverAnchors = initTeamHoverCards();
    document.body.addEventListener("click", function (ev) {
      var btn = ev.target.closest(".js-copy-player-card");
      if (!btn) return;
      ev.preventDefault();
      var pid = parseInt(btn.getAttribute("data-player-id") || "0", 10);
      if (!pid) return;
      copyPlayerShareCardImage(pid, btn);
    });
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
      var navCollapseMq = window.matchMedia("(max-width: 1200px)");
      function closeMainNavIfCollapsedLayout() {
        if (!navCollapseMq.matches) {
          mainNav.classList.remove("is-open");
          navToggle.setAttribute("aria-expanded", "false");
        }
      }
      if (typeof navCollapseMq.addEventListener === "function") {
        navCollapseMq.addEventListener("change", closeMainNavIfCollapsedLayout);
      } else if (typeof navCollapseMq.addListener === "function") {
        navCollapseMq.addListener(closeMainNavIfCollapsedLayout);
      }
      navToggle.addEventListener("click", function () {
        mainNav.classList.toggle("is-open");
        var open = mainNav.classList.contains("is-open");
        navToggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open) {
          // Always start at Standings / top of Public — Android often left scroll mid-list.
          mainNav.scrollTop = 0;
        }
      });
      mainNav.querySelectorAll("a").forEach(function (link) {
        link.addEventListener("click", function () {
          if (!navCollapseMq.matches) return;
          mainNav.classList.remove("is-open");
          navToggle.setAttribute("aria-expanded", "false");
        });
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
        if (window.__bowlPageLoad && typeof window.__bowlPageLoad.start === "function") {
          window.__bowlPageLoad.start();
        }
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
      ac.addEventListener("mousedown", function (e) {
        e.preventDefault();
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
    if (table.getAttribute("data-sort-bound") === "1") return;
    var tbody = table.tBodies[0];
    var thead = table.tHead;
    if (!tbody || !thead || !thead.rows[0]) return;
    table.setAttribute("data-sort-bound", "1");
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
      table.dispatchEvent(new CustomEvent("table:sorted"));

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

  window.initSortableTable = initSortableTable;

  function initTeamFinancesPanel() {
    var root = document.querySelector("[data-team-finances]");
    if (!root || root.getAttribute("data-fin-bound") === "1") return;
    root.setAttribute("data-fin-bound", "1");
    var group = "all";
    var band = "all";
    var surplus = "year";
    var search = "";
    var rows = Array.prototype.slice.call(root.querySelectorAll("[data-fin-row]"));
    var status = root.querySelector("[data-fin-status]");
    var kpiSurplus = root.querySelector("[data-fin-surplus-year]");
    var kpiRank = root.querySelector("[data-fin-rank]");

    function applySurplus() {
      if (kpiSurplus) {
        kpiSurplus.textContent =
          surplus === "term"
            ? kpiSurplus.getAttribute("data-fin-surplus-term") || kpiSurplus.textContent
            : kpiSurplus.getAttribute("data-fin-surplus-year") || kpiSurplus.textContent;
      }
      if (kpiRank) {
        var rankLabel =
          surplus === "term"
            ? kpiRank.getAttribute("data-fin-rank-term")
            : kpiRank.getAttribute("data-fin-rank-year");
        if (rankLabel) kpiRank.textContent = rankLabel;
      }
      rows.forEach(function (tr) {
        var cell = tr.querySelector(".team-finances__surplus-cell");
        if (!cell) return;
        var label = surplus === "term" ? cell.getAttribute("data-fin-term-label") : cell.getAttribute("data-fin-year-label");
        var sort = surplus === "term" ? cell.getAttribute("data-fin-term-sort") : cell.getAttribute("data-fin-year-sort");
        var span = cell.querySelector("span");
        if (span && label) span.textContent = label;
        if (sort != null) cell.setAttribute("data-sort-value", sort);
      });
    }

    function applyFilters() {
      var q = search.trim().toLowerCase();
      var shown = 0;
      rows.forEach(function (tr) {
        var g = tr.getAttribute("data-fin-group") || "";
        var b = tr.getAttribute("data-fin-band") || "";
        var name = tr.getAttribute("data-fin-name") || "";
        var ok =
          (group === "all" || g === group) &&
          (band === "all" || b === band) &&
          (!q || name.indexOf(q) >= 0);
        tr.hidden = !ok;
        if (ok) shown += 1;
      });
      if (status) {
        status.textContent =
          shown === rows.length
            ? "Click a row for the peer-market explanation. Columns are sortable."
            : "Showing " + shown + " of " + rows.length + " contracts.";
      }
    }

    root.querySelectorAll("button[data-fin-group]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        group = btn.getAttribute("data-fin-group") || "all";
        root.querySelectorAll("button[data-fin-group]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        applyFilters();
      });
    });
    root.querySelectorAll("button[data-fin-band]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        band = btn.getAttribute("data-fin-band") || "all";
        root.querySelectorAll("button[data-fin-band]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        applyFilters();
      });
    });
    root.querySelectorAll("[data-fin-surplus]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        surplus = btn.getAttribute("data-fin-surplus") || "year";
        root.querySelectorAll("[data-fin-surplus]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        applySurplus();
      });
    });
    var searchEl = root.querySelector("[data-fin-search]");
    if (searchEl) {
      searchEl.addEventListener("input", function () {
        search = searchEl.value || "";
        applyFilters();
      });
    }
    function toggleWhy(tr) {
      var why = tr.querySelector(".team-finances__why");
      if (!why) return;
      var open = !why.hidden;
      rows.forEach(function (other) {
        var w = other.querySelector(".team-finances__why");
        if (w) w.hidden = true;
        other.classList.remove("is-open");
        other.setAttribute("aria-expanded", "false");
      });
      if (!open) {
        why.hidden = false;
        tr.classList.add("is-open");
        tr.setAttribute("aria-expanded", "true");
      }
    }
    rows.forEach(function (tr) {
      tr.addEventListener("click", function (ev) {
        if (ev.target.closest("a, button, input")) return;
        toggleWhy(tr);
      });
      tr.addEventListener("keydown", function (ev) {
        if (ev.target.closest("a, button, input")) return;
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          toggleWhy(tr);
        }
      });
    });
    applySurplus();
    applyFilters();
  }

  function initTeamLineBuilder() {
    var page = document.querySelector("[data-team-lines-page]");
    if (page && page.getAttribute("data-lines-bound") !== "1") {
      page.setAttribute("data-lines-bound", "1");
      var builder = page.querySelector("[data-team-line-builder]");
      var imported = page.querySelector("[data-lines-imported]");
      page.querySelectorAll("[data-lines-view]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var view = btn.getAttribute("data-lines-view") || "builder";
          page.querySelectorAll("[data-lines-view]").forEach(function (b) {
            var on = b === btn;
            b.classList.toggle("is-active", on);
            b.setAttribute("aria-selected", on ? "true" : "false");
          });
          if (builder) builder.hidden = view !== "builder";
          if (imported) imported.hidden = view !== "imported";
        });
      });
    }

    var root = document.querySelector("[data-team-line-builder]");
    if (!root || root.getAttribute("data-lb-bound") === "1") return;
    var payloadEl = root.querySelector("[data-lb-payload]");
    if (!payloadEl) return;
    var data;
    try {
      data = JSON.parse(payloadEl.textContent || "{}");
    } catch (err) {
      return;
    }
    root.setAttribute("data-lb-bound", "1");

    var players = data.players || {};
    var slots = Object.assign({}, data.slots || {});
    var roles = Object.assign({}, data.roles || {});
    var importedSlots = data.imported_slots || {};
    var importedRoles = data.imported_roles || {};
    var units = data.units || [];
    var selectedPid = null;
    var groupFilter = "all";
    var usedFilter = "unused";
    var search = "";
    var statusEl = root.querySelector("[data-lb-status]");
    var poolEl = root.querySelector("[data-lb-pool]");
    var saveBtn = root.querySelector("[data-lb-save]");
    var complementary = {
      "gretzkys_office|sniper": 1,
      "playmaker|sniper": 1,
      "setup_man|sniper": 1,
      "garbage_collector|playmaker": 1,
      "playmaker|screener": 1,
      "playmaker|power_forward": 1,
      "offensive_d|stay_at_home": 1,
      "puck_mover|shutdown": 1,
      "offensive_d|two_way_d": 1
    };

    function setStatus(msg) {
      if (statusEl) statusEl.textContent = msg || "";
    }

    function playerRec(pid) {
      return players[String(pid)] || players[pid] || null;
    }

    function situationFor(slotKey) {
      var key = String(slotKey || "");
      if (key.indexOf("pp_") === 0) return "pp";
      if (key.indexOf("pk_") === 0) return "pk";
      return "es";
    }

    function usedIds(situation) {
      var out = {};
      Object.keys(slots).forEach(function (k) {
        if (!slots[k]) return;
        if (situation && situationFor(k) !== situation) return;
        out[String(slots[k])] = k;
      });
      return out;
    }

    function roleRating(rec, key) {
      var list = (rec && rec.roles) || [];
      for (var i = 0; i < list.length; i++) {
        if (list[i].key === key) return list[i].rating;
      }
      return null;
    }

    function lineAbility(ratings) {
      var vals = ratings.filter(function (v) { return v != null; });
      if (!vals.length) return null;
      var sum = vals.reduce(function (a, b) { return a + b; }, 0);
      return Math.round((sum / vals.length) * 10) / 10;
    }

    function lineChemistry(roleKeys, hands) {
      var keys = roleKeys.filter(Boolean);
      if (!keys.length) return null;
      var score = 55;
      var uniq = {};
      keys.forEach(function (k) { uniq[k] = (uniq[k] || 0) + 1; });
      var extras = keys.length - Object.keys(uniq).length;
      if (extras) score -= 8 * extras;
      for (var i = 0; i < keys.length; i++) {
        for (var j = i + 1; j < keys.length; j++) {
          var pair = [keys[i], keys[j]].sort().join("|");
          if (complementary[pair]) score += 8;
        }
      }
      var norms = [];
      hands.forEach(function (h) {
        var t = String(h || "").toLowerCase();
        if (t.indexOf("l") === 0) norms.push("l");
        else if (t.indexOf("r") === 0) norms.push("r");
      });
      if (norms.indexOf("l") >= 0 && norms.indexOf("r") >= 0) score += 6;
      return Math.max(1, Math.min(100, Math.round(score)));
    }

    function unitStats(unit) {
      var ratings = [];
      var roleKeys = [];
      var hands = [];
      (unit.slots || []).forEach(function (slot) {
        var pid = slots[slot.key];
        if (!pid) {
          ratings.push(null);
          roleKeys.push(null);
          hands.push(null);
          return;
        }
        var rec = playerRec(pid);
        var role = roles[String(pid)] || (rec && rec.default_role);
        ratings.push(rec ? roleRating(rec, role) : null);
        roleKeys.push(role || null);
        hands.push(rec ? rec.hand : null);
      });
      var ability = lineAbility(ratings);
      return {
        ability: ability,
        chemistry: lineChemistry(roleKeys, hands),
        grade: lineAbilityGrade(ability, unit.kind)
      };
    }

    function lineAbilityGrade(ability, kind) {
      if (ability == null) return null;
      var pair = String(kind || "") === "defense";
      var key;
      var label;
      if (ability >= 85) {
        key = "1st";
        label = pair ? "1st Pair" : "1st line";
      } else if (ability >= 76) {
        key = "2nd";
        label = pair ? "2nd Pair" : "2nd line";
      } else if (ability >= 68) {
        key = "3rd";
        label = pair ? "3rd Pair" : "3rd line";
      } else if (ability >= 60) {
        key = "4th";
        label = pair ? "4th Pair" : "4th line";
      } else {
        key = "depth";
        label = pair ? "Depth pair" : "Depth";
      }
      return { key: key, label: label, score: ability };
    }

    function esc(s) {
      return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function faceHtml(rec) {
      var inner =
        rec && rec.headshot
          ? '<img src="' + esc(rec.headshot) + '" alt="">'
          : '<span class="team-line-builder__ph" aria-hidden="true"></span>';
      return '<span class="team-line-builder__face">' + inner + "</span>";
    }

    function ensureRole(pid) {
      var rec = playerRec(pid);
      if (!rec) return;
      var key = String(pid);
      if (roles[key] && roleRating(rec, roles[key]) != null) return;
      roles[key] = rec.default_role || ((rec.roles[0] && rec.roles[0].key) || "");
    }

    function placePlayer(slotKey, pid) {
      var rec = playerRec(pid);
      if (!rec) return;
      var slot = null;
      units.forEach(function (u) {
        (u.slots || []).forEach(function (s) {
          if (s.key === slotKey) slot = s;
        });
      });
      if (!slot) return;
      if (rec.group !== slot.group) {
        setStatus(rec.group === "goalies" ? "Goalies stay in the pool for v1." : "That player does not fit this slot.");
        return;
      }
      var used = usedIds(situationFor(slotKey));
      var prevSlot = used[String(pid)];
      var occupant = slots[slotKey];
      if (prevSlot && prevSlot !== slotKey) delete slots[prevSlot];
      if (occupant && String(occupant) !== String(pid) && prevSlot) {
        slots[prevSlot] = occupant;
      }
      slots[slotKey] = parseInt(pid, 10);
      ensureRole(pid);
      selectedPid = null;
      setStatus("");
      render();
    }

    function clearSlot(slotKey) {
      delete slots[slotKey];
      selectedPid = null;
      render();
    }

    function renderPool() {
      if (!poolEl) return;
      var used = usedIds();
      var q = search.trim().toLowerCase();
      var html = "";
      Object.keys(players)
        .map(function (id) { return players[id]; })
        .sort(function (a, b) {
          var order = { forwards: 0, defense: 1, goalies: 2 };
          var ga = order[a.group] != null ? order[a.group] : 9;
          var gb = order[b.group] != null ? order[b.group] : 9;
          if (ga !== gb) return ga - gb;
          return (b.ovr || -1) - (a.ovr || -1);
        })
        .forEach(function (rec) {
          var id = String(rec.id);
          var onLine = !!used[id];
          if (usedFilter === "unused" && onLine) return;
          if (groupFilter !== "all" && rec.group !== groupFilter) return;
          if (q && String(rec.name || "").toLowerCase().indexOf(q) < 0) return;
          html +=
            '<button type="button" class="team-line-builder__chip-player' +
            (selectedPid === rec.id || String(selectedPid) === id ? " is-selected" : "") +
            (onLine ? " is-used" : "") +
            '" draggable="true" data-lb-player="' +
            esc(id) +
            '">' +
            faceHtml(rec) +
            '<span class="team-line-builder__chip-copy"><span class="team-line-builder__chip-name">' +
            esc(rec.name) +
            '</span><span class="team-line-builder__chip-meta">' +
            esc(rec.pos || "") +
            (rec.ovr != null ? " · " + rec.ovr : "") +
            "</span></span></button>";
        });
      poolEl.innerHTML = html || '<p class="muted">No matching players.</p>';
    }

    function roleSelectHtml(rec) {
      var current = roles[String(rec.id)] || rec.default_role || "";
      var opts = (rec.roles || [])
        .map(function (r) {
          return (
            '<option value="' +
            esc(r.key) +
            '"' +
            (r.key === current ? " selected" : "") +
            ">" +
            esc(r.label) +
            " · " +
            r.rating +
            "</option>"
          );
        })
        .join("");
      return '<select class="team-line-builder__role" data-lb-role="' + esc(rec.id) + '">' + opts + "</select>";
    }

    function gradeBoxHtml(stats) {
      var grade = stats.grade;
      if (!grade) {
        return (
          '<div class="team-line-builder__grade">' +
          '<span class="team-line-builder__grade-kicker">Line ability</span>' +
          '<span class="team-line-builder__grade-value">—</span></div>'
        );
      }
      return (
        '<div class="team-line-builder__grade team-line-builder__grade--' +
        esc(grade.key) +
        '"><span class="team-line-builder__grade-kicker">Line ability</span>' +
        '<span class="team-line-builder__grade-value">' +
        grade.score +
        " — " +
        esc(grade.label) +
        "</span></div>"
      );
    }

    function chemistryHue(score) {
      var t = Math.max(0, Math.min(100, Number(score))) / 100;
      var hue = Math.round(8 + t * -152);
      return hue < 0 ? hue + 360 : hue;
    }

    function chemBarHtml(chemistry) {
      if (chemistry == null) {
        return (
          '<div class="team-line-builder__chem">' +
          '<div class="team-line-builder__chem-head">' +
          '<span class="team-line-builder__chem-label">Chemistry</span>' +
          '<span class="team-line-builder__chem-value">—</span></div>' +
          '<div class="team-line-builder__chem-track" role="meter" aria-label="Chemistry unavailable" aria-valuemin="0" aria-valuemax="100">' +
          '<span class="team-line-builder__chem-fill" style="width:0%"></span></div></div>'
        );
      }
      var pct = Math.max(0, Math.min(100, Number(chemistry)));
      var hue = chemistryHue(pct);
      var color = "hsl(" + hue + " 78% 58%)";
      return (
        '<div class="team-line-builder__chem">' +
        '<div class="team-line-builder__chem-head">' +
        '<span class="team-line-builder__chem-label">Chemistry</span>' +
        '<span class="team-line-builder__chem-value" style="color:' +
        color +
        '">' +
        chemistry +
        "</span></div>" +
        '<div class="team-line-builder__chem-track" role="meter" aria-label="Chemistry ' +
        chemistry +
        '" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' +
        pct +
        '"><span class="team-line-builder__chem-fill" style="width:' +
        pct +
        "%;background:" +
        color +
        '"></span></div></div>'
      );
    }

    function renderBoard() {
      ["forwards", "defense", "powerplay", "penalty"].forEach(function (kind) {
        var host = root.querySelector('[data-lb-kind="' + kind + '"]');
        if (!host) return;
        var html = "";
        units
          .filter(function (u) { return u.kind === kind; })
          .forEach(function (unit) {
            var stats = unitStats(unit);
            html +=
              '<article class="team-line-builder__unit"><div class="team-line-builder__unit-head"><h4 class="team-line-builder__unit-title">' +
              esc(unit.title) +
              "</h4>" +
              gradeBoxHtml(stats) +
              '</div><div class="team-line-builder__slots team-line-builder__slots--' +
              esc(kind) +
              '">';
            (unit.slots || []).forEach(function (slot) {
              var pid = slots[slot.key];
              var rec = pid ? playerRec(pid) : null;
              html +=
                '<div class="team-line-builder__slot' +
                (rec ? " is-filled" : "") +
                (selectedPid && !rec ? " is-target" : "") +
                '" data-lb-slot="' +
                esc(slot.key) +
                '" data-lb-group="' +
                esc(slot.group) +
                '"><span class="team-line-builder__slot-label">' +
                esc(slot.label) +
                "</span>";
              if (rec) {
                html +=
                  '<div class="team-line-builder__slot-body" draggable="true" data-lb-player="' +
                  esc(rec.id) +
                  '"><div class="team-line-builder__slot-player">' +
                  faceHtml(rec) +
                  '<div class="team-line-builder__slot-copy"><div class="team-line-builder__slot-name">' +
                  esc(rec.name) +
                  '</div><div class="team-line-builder__slot-meta">' +
                  (rec.ovr != null ? "OVR " + rec.ovr : "") +
                  "</div></div></div>" +
                  roleSelectHtml(rec) +
                  "</div>";
              } else {
                html += '<div class="team-line-builder__slot-empty">Drop or tap to place</div>';
              }
              html += "</div>";
            });
            html += "</div>" + chemBarHtml(stats.chemistry) + "</article>";
          });
        host.innerHTML = html;
      });
    }

    function render() {
      renderPool();
      renderBoard();
    }

    function playerIdFromEl(el) {
      var node = el && el.closest ? el.closest("[data-lb-player]") : null;
      if (!node) return null;
      var raw = node.getAttribute("data-lb-player");
      return raw ? parseInt(raw, 10) : null;
    }

    root.addEventListener("click", function (ev) {
      if (ev.target.closest("select, a, [data-lb-reset], [data-lb-save], [data-lb-search], button[data-lb-group], button[data-lb-used]")) {
        return;
      }
      var slotEl = ev.target.closest("[data-lb-slot]");
      var pid = playerIdFromEl(ev.target);
      if (slotEl) {
        var slotKey = slotEl.getAttribute("data-lb-slot");
        var occupant = slots[slotKey];
        if (selectedPid) {
          placePlayer(slotKey, selectedPid);
        } else if (occupant) {
          selectedPid = parseInt(occupant, 10);
          render();
        }
        return;
      }
      if (pid) {
        if (String(selectedPid) === String(pid)) {
          selectedPid = null;
        } else {
          selectedPid = pid;
        }
        render();
      }
    });

    root.addEventListener("change", function (ev) {
      var sel = ev.target.closest("[data-lb-role]");
      if (!sel) return;
      var pid = sel.getAttribute("data-lb-role");
      roles[String(pid)] = sel.value;
      render();
    });

    root.addEventListener("dblclick", function (ev) {
      var slotEl = ev.target.closest("[data-lb-slot]");
      if (!slotEl) return;
      clearSlot(slotEl.getAttribute("data-lb-slot"));
    });

    var dragPid = null;
    root.addEventListener("dragstart", function (ev) {
      var pid = playerIdFromEl(ev.target);
      if (!pid) return;
      dragPid = pid;
      ev.dataTransfer.setData("text/plain", String(pid));
      ev.dataTransfer.effectAllowed = "move";
    });
    root.addEventListener("dragend", function () {
      dragPid = null;
    });
    root.addEventListener("dragover", function (ev) {
      if (ev.target.closest("[data-lb-slot], [data-lb-pool]")) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = "move";
      }
    });
    root.addEventListener("drop", function (ev) {
      ev.preventDefault();
      var pid = parseInt(ev.dataTransfer.getData("text/plain") || dragPid || "", 10);
      if (!pid) return;
      var slotEl = ev.target.closest("[data-lb-slot]");
      if (slotEl) {
        placePlayer(slotEl.getAttribute("data-lb-slot"), pid);
        return;
      }
      if (ev.target.closest("[data-lb-pool]")) {
        Object.keys(slots).forEach(function (k) {
          if (String(slots[k]) === String(pid)) delete slots[k];
        });
        selectedPid = null;
        render();
      }
    });

    root.querySelectorAll("[data-lb-group]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        groupFilter = btn.getAttribute("data-lb-group") || "all";
        root.querySelectorAll("[data-lb-group]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        renderPool();
      });
    });
    root.querySelectorAll("[data-lb-used]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        usedFilter = btn.getAttribute("data-lb-used") || "unused";
        root.querySelectorAll("[data-lb-used]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        renderPool();
      });
    });
    var searchEl = root.querySelector("[data-lb-search]");
    if (searchEl) {
      searchEl.addEventListener("input", function () {
        search = searchEl.value || "";
        renderPool();
      });
    }
    var resetBtn = root.querySelector("[data-lb-reset]");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        slots = Object.assign({}, importedSlots);
        roles = Object.assign({}, importedRoles);
        selectedPid = null;
        setStatus("Reset to imported even-strength lines.");
        render();
      });
    }
    if (saveBtn) {
      saveBtn.addEventListener("click", function () {
        if (!data.can_save || !data.save_url) return;
        saveBtn.disabled = true;
        setStatus("Saving…");
        fetch(data.save_url, {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          credentials: "same-origin",
          body: JSON.stringify({ slots: slots, roles: roles })
        })
          .then(function (resp) {
            return resp.json().then(function (body) {
              return { ok: resp.ok, body: body };
            });
          })
          .then(function (out) {
            if (!out.ok) {
              setStatus(out.body && out.body.error ? out.body.error : "Could not save.");
              return;
            }
            setStatus("Saved. Everyone will see this sheet on the Builder tab.");
          })
          .catch(function () {
            setStatus("Could not save.");
          })
          .then(function () {
            saveBtn.disabled = false;
          });
      });
    }

    render();
  }

  function initPaginatedTable(table) {
    if (table.getAttribute("data-page-bound") === "1") return;
    var tbody = table.tBodies && table.tBodies[0];
    if (!tbody) return;
    var pageSize = parseInt(table.getAttribute("data-page-size") || "50", 10);
    if (!pageSize || pageSize < 1) return;
    var page = 0;
    var pager = document.createElement("div");
    pager.className = "table-pager";
    pager.innerHTML =
      '<button type="button" class="table-pager__btn" data-page-prev>Prev</button>' +
      '<span class="table-pager__status" data-page-status>Page 1 of 1</span>' +
      '<button type="button" class="table-pager__btn" data-page-next>Next</button>';
    var prev = pager.querySelector("[data-page-prev]");
    var next = pager.querySelector("[data-page-next]");
    var status = pager.querySelector("[data-page-status]");
    var wrap = table.closest(".table-wrap");
    if (wrap && wrap.parentNode) {
      wrap.parentNode.insertBefore(pager, wrap.nextSibling);
    } else if (table.parentNode) {
      table.parentNode.insertBefore(pager, table.nextSibling);
    }
    function rows() {
      return Array.from(tbody.rows).filter(function (row) {
        return !row.classList.contains("table-pager-empty-row");
      });
    }
    function renderPage() {
      var currentRows = rows();
      var totalPages = Math.max(1, Math.ceil(currentRows.length / pageSize));
      if (page >= totalPages) page = totalPages - 1;
      if (page < 0) page = 0;
      currentRows.forEach(function (row, idx) {
        row.hidden = idx < page * pageSize || idx >= (page + 1) * pageSize;
      });
      pager.hidden = currentRows.length <= pageSize;
      status.textContent = "Page " + (page + 1) + " of " + totalPages;
      prev.disabled = page === 0;
      next.disabled = page >= totalPages - 1;
    }
    prev.addEventListener("click", function () {
      page = Math.max(0, page - 1);
      renderPage();
    });
    next.addEventListener("click", function () {
      page = page + 1;
      renderPage();
    });
    table.addEventListener("table:sorted", function () {
      page = 0;
      renderPage();
    });
    table.setAttribute("data-page-bound", "1");
    renderPage();
  }

  function initAdvancedStatsTeamChart() {
    initTeamAnalyticsChart({
      rootId: "advanced-stats-team-chart",
      dataId: "advanced-stats-team-chart-data",
      invertXForLowBetter: false,
      enhancedTooltip: false,
    });
  }

  function initTeamStatisticsChart() {
    initTeamAnalyticsChart({
      rootId: "team-statistics-chart",
      dataId: "team-statistics-chart-data",
      invertXForLowBetter: true,
      enhancedTooltip: true,
    });
  }

  function initTeamAnalyticsChart(config) {
    var root = document.getElementById(config.rootId);
    var dataEl = document.getElementById(config.dataId);
    if (!root || !dataEl) return;
    var archive;
    try {
      archive = JSON.parse(dataEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    var metrics = archive.metrics || [];
    var segments = archive.segments || [];
    var seasons = archive.seasons || [];
    var datasets = archive.datasets || {};
    if (!metrics.length || !seasons.length) {
      var emptyEarly = root.querySelector("[data-team-chart-empty]");
      if (emptyEarly) emptyEarly.hidden = false;
      return;
    }

    var seasonSel = root.querySelector("[data-team-chart-season]");
    var segmentSel = root.querySelector("[data-team-chart-segment]");
    var xSel = root.querySelector("[data-team-chart-x]");
    var ySel = root.querySelector("[data-team-chart-y]");
    var normSel = root.querySelector("[data-team-chart-norm]");
    var plot = root.querySelector("[data-team-chart-plot]");
    var pointsWrap = root.querySelector("[data-team-chart-points]");
    var axisX = root.querySelector("[data-team-chart-axis-x]");
    var axisY = root.querySelector("[data-team-chart-axis-y]");
    var midX = root.querySelector("[data-team-chart-midline-x]");
    var midY = root.querySelector("[data-team-chart-midline-y]");
    var empty = root.querySelector("[data-team-chart-empty]");
    if (!seasonSel || !segmentSel || !xSel || !ySel || !normSel || !plot || !pointsWrap) return;

    var tip = document.createElement("div");
    tip.className = "advanced-stats-team-chart-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);

    var metricByKey = {};
    metrics.forEach(function (m) {
      metricByKey[m.key] = m;
    });

    function fillSelect(sel, options, selected) {
      sel.innerHTML = "";
      options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(opt.value) === String(selected)) o.selected = true;
        sel.appendChild(o);
      });
    }

    fillSelect(
      seasonSel,
      seasons.map(function (s) {
        return { value: s.id, label: s.label };
      }),
      archive.default_season_id
    );
    fillSelect(
      segmentSel,
      segments.map(function (s) {
        return { value: s.key, label: s.label };
      }),
      archive.default_segment || "rs"
    );
    fillSelect(
      xSel,
      metrics.map(function (m) {
        return { value: m.key, label: m.label };
      }),
      archive.default_x || metrics[0].key
    );
    fillSelect(
      ySel,
      metrics.map(function (m) {
        return { value: m.key, label: m.label };
      }),
      archive.default_y || (metrics[1] ? metrics[1].key : metrics[0].key)
    );
    normSel.value = archive.default_norm || "per_game";
    if (config.enhancedTooltip && normSel.querySelector('option[value="per_60"]') == null) {
      var per60 = document.createElement("option");
      per60.value = "per_60";
      per60.textContent = "Per 60";
      normSel.appendChild(per60);
    }

    function metricValue(team, key, norm) {
      var meta = metricByKey[key];
      if (!meta) return null;
      var raw = team.metrics ? team.metrics[key] : null;
      if (raw == null || raw === "") return null;
      var num = Number(raw);
      if (!isFinite(num)) return null;
      var gp = team.metrics && team.metrics.gp ? Number(team.metrics.gp) : 0;
      if (norm === "per_game" && meta.per_game) {
        if (!gp) return null;
        return num / gp;
      }
      if (norm === "per_60") {
        if (gp) return (num / gp) * 60;
        return num;
      }
      return num;
    }

    function metricLabel(key, norm) {
      var meta = metricByKey[key];
      if (!meta) return key;
      if (norm === "per_game" && meta.per_game) return meta.label + " Per Game";
      if (norm === "per_60") return meta.label + " Per 60";
      return meta.label;
    }

    function formatMetric(key, value) {
      var meta = metricByKey[key] || { decimals: 2 };
      var dec = meta.decimals != null ? meta.decimals : 2;
      return Number(value).toFixed(dec);
    }

    function datasetKey() {
      return String(seasonSel.value) + "|" + String(segmentSel.value);
    }

    function currentTeams() {
      var ds = datasets[datasetKey()];
      return ds && ds.teams ? ds.teams : [];
    }

    function updateQuadrants(xMeta, yMeta) {
      var xHigh = (xMeta && xMeta.better) === "high";
      var yHigh = (yMeta && yMeta.better) === "high";
      var labels = {
        tl: "Bad",
        tr: "Fun",
        bl: "Boring",
        br: "Good",
      };
      if (xHigh && !yHigh) {
        labels = { tl: "Bad", tr: "Fun", bl: "Boring", br: "Good" };
      } else if (xHigh && yHigh) {
        labels = { tl: "Low/Low", tr: "High/High", bl: "Low/Low", br: "Strong" };
      } else if (!xHigh && !yHigh) {
        labels = { tl: "Strong", tr: "Mixed", bl: "Mixed", br: "Weak" };
      }
      root.querySelectorAll("[data-team-chart-quad]").forEach(function (el) {
        var pos = el.getAttribute("data-team-chart-quad");
        if (pos && labels[pos]) el.textContent = labels[pos];
      });
    }

    function renderChart() {
      var teams = currentTeams();
      var xKey = xSel.value;
      var yKey = ySel.value;
      var norm = normSel.value;
      var xMeta = metricByKey[xKey];
      var yMeta = metricByKey[yKey];
      var invertX = config.invertXForLowBetter && xMeta && xMeta.better === "low";
      if (!teams.length) {
        pointsWrap.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;

      var plotted = [];
      teams.forEach(function (team) {
        var xv = metricValue(team, xKey, norm);
        var yv = metricValue(team, yKey, norm);
        if (xv == null || yv == null) return;
        plotted.push({ team: team, x: xv, y: yv });
      });
      if (!plotted.length) {
        pointsWrap.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }

      var xs = plotted.map(function (p) {
        return p.x;
      });
      var ys = plotted.map(function (p) {
        return p.y;
      });
      var minX = Math.min.apply(null, xs);
      var maxX = Math.max.apply(null, xs);
      var minY = Math.min.apply(null, ys);
      var maxY = Math.max.apply(null, ys);
      if (minX === maxX) {
        minX -= 1;
        maxX += 1;
      }
      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }
      var padX = (maxX - minX) * 0.08;
      var padY = (maxY - minY) * 0.08;
      minX -= padX;
      maxX += padX;
      minY -= padY;
      maxY += padY;

      var avgX = xs.reduce(function (a, b) {
        return a + b;
      }, 0) / xs.length;
      var avgY = ys.reduce(function (a, b) {
        return a + b;
      }, 0) / ys.length;
      var xMidPct = invertX
        ? 100 - ((avgX - minX) / (maxX - minX)) * 100
        : ((avgX - minX) / (maxX - minX)) * 100;
      var yMidPct = 100 - ((avgY - minY) / (maxY - minY)) * 100;
      if (midX) midX.style.left = xMidPct + "%";
      if (midY) midY.style.top = yMidPct + "%";
      if (axisX) axisX.textContent = metricLabel(xKey, norm);
      if (axisY) axisY.textContent = metricLabel(yKey, norm);
      updateQuadrants(xMeta, yMeta);

      var existing = {};
      Array.from(pointsWrap.querySelectorAll("[data-team-chart-point]")).forEach(function (el) {
        existing[el.getAttribute("data-team-id")] = el;
      });

      plotted.forEach(function (p) {
        var xPos = ((p.x - minX) / (maxX - minX)) * 100;
        var left = invertX ? 100 - xPos : xPos;
        var top = 100 - ((p.y - minY) / (maxY - minY)) * 100;
        var id = String(p.team.team_id);
        var el = existing[id];
        if (!el) {
          el = document.createElement("a");
          el.className = "advanced-stats-team-chart__point";
          el.setAttribute("data-team-chart-point", "1");
          el.setAttribute("data-team-id", id);
          if (p.team.slug) el.href = withRoot("/team/" + p.team.slug);
          var img = document.createElement("img");
          img.className = "advanced-stats-team-chart__logo";
          img.alt = "";
          el.appendChild(img);
          pointsWrap.appendChild(el);
        }
        var logo = el.querySelector("img");
        if (logo && p.team.logo_url) logo.src = p.team.logo_url;
        el.style.setProperty("--point-left", left + "%");
        el.style.setProperty("--point-top", top + "%");
        if (p.team.primary_color) el.style.setProperty("--point-color", p.team.primary_color);
        el.setAttribute("data-x-label", metricLabel(xKey, norm));
        el.setAttribute("data-y-label", metricLabel(yKey, norm));
        el.setAttribute("data-x-value", formatMetric(xKey, p.x));
        el.setAttribute("data-y-value", formatMetric(yKey, p.y));
        el.setAttribute("data-team-name", p.team.name || p.team.abbr || "Team");
        el.setAttribute("data-team-abbr", p.team.abbr || p.team.name || "Team");
        el.setAttribute("data-season-label", p.team.season_label || archive.season_label || "");
        el.setAttribute(
          "aria-label",
          (p.team.name || p.team.abbr || "Team") +
            ": " +
            metricLabel(xKey, norm) +
            " " +
            formatMetric(xKey, p.x) +
            ", " +
            metricLabel(yKey, norm) +
            " " +
            formatMetric(yKey, p.y)
        );
        delete existing[id];
      });
      Object.keys(existing).forEach(function (id) {
        if (existing[id] && existing[id].parentNode) existing[id].parentNode.removeChild(existing[id]);
      });
    }

    function tipHtml(el) {
      var logo = el.querySelector("img");
      var logoSrc = logo ? logo.getAttribute("src") : "";
      var seasonLine = "";
      if (config.enhancedTooltip) {
        var sl = el.getAttribute("data-season-label") || "";
        if (sl) {
          seasonLine = '<span class="advanced-stats-team-chart-tooltip__metric">Season: <strong>' + escapeHtml(sl) + "</strong></span>";
        }
      }
      return (
        '<div class="advanced-stats-team-chart-tooltip__inner">' +
        (logoSrc ? '<img class="advanced-stats-team-chart-tooltip__logo" src="' + escapeAttr(logoSrc) + '" alt="">' : "") +
        '<div class="advanced-stats-team-chart-tooltip__body">' +
        "<strong>" +
        escapeHtml(el.getAttribute("data-team-abbr") || el.getAttribute("data-team-name") || "Team") +
        "</strong>" +
        seasonLine +
        '<span class="advanced-stats-team-chart-tooltip__metric">' +
        escapeHtml(el.getAttribute("data-x-label") || "") +
        ": <strong>" +
        escapeHtml(el.getAttribute("data-x-value") || "") +
        "</strong></span>" +
        '<span class="advanced-stats-team-chart-tooltip__metric">' +
        escapeHtml(el.getAttribute("data-y-label") || "") +
        ": <strong>" +
        escapeHtml(el.getAttribute("data-y-value") || "") +
        "</strong></span>" +
        "</div></div>"
      );
    }

    function moveTip(e) {
      tip.style.left = e.clientX + 14 + "px";
      tip.style.top = e.clientY + 14 + "px";
    }

    pointsWrap.addEventListener("mouseover", function (e) {
      var point = e.target.closest("[data-team-chart-point]");
      if (!point || !pointsWrap.contains(point)) return;
      tip.innerHTML = tipHtml(point);
      tip.hidden = false;
      point.classList.add("is-active");
      moveTip(e);
    });
    pointsWrap.addEventListener("mousemove", function (e) {
      if (!tip.hidden) moveTip(e);
    });
    pointsWrap.addEventListener("mouseout", function (e) {
      var point = e.target.closest("[data-team-chart-point]");
      if (!point) return;
      var rel = e.relatedTarget;
      if (rel && point.contains(rel)) return;
      tip.hidden = true;
      point.classList.remove("is-active");
    });
    pointsWrap.addEventListener("focusin", function (e) {
      var point = e.target.closest("[data-team-chart-point]");
      if (!point) return;
      tip.innerHTML = tipHtml(point);
      tip.hidden = false;
      point.classList.add("is-active");
      var rect = point.getBoundingClientRect();
      tip.style.left = rect.right + 10 + "px";
      tip.style.top = rect.top + "px";
    });
    pointsWrap.addEventListener("focusout", function (e) {
      var point = e.target.closest("[data-team-chart-point]");
      if (!point) return;
      tip.hidden = true;
      point.classList.remove("is-active");
    });

    [seasonSel, segmentSel, xSel, ySel, normSel].forEach(function (sel) {
      sel.addEventListener("change", renderChart);
    });
    renderChart();
  }

  function initTeamStatisticsFilters() {
    var form = document.getElementById("team-statistics-filters");
    if (!form) return;
    var clearBtn = form.querySelector("[data-team-stats-clear]");
    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        var base = form.getAttribute("action") || window.location.pathname;
        window.location.href = base;
      });
    }
  }

  function initTeamStatisticsRowTooltips() {
    var table = document.querySelector(".team-statistics-table");
    var tip = document.getElementById("team-statistics-row-tooltip");
    if (!table || !tip) return;
    table.addEventListener("mouseover", function (e) {
      var row = e.target.closest(".team-statistics-table__row");
      if (!row || !table.contains(row)) return;
      var name = row.getAttribute("data-team-name") || "Team";
      var logo = row.getAttribute("data-team-logo") || "";
      tip.innerHTML =
        (logo ? '<img src="' + escapeAttr(logo) + '" alt="" class="team-statistics-row-tooltip__logo">' : "") +
        "<strong>" +
        escapeHtml(name) +
        "</strong>";
      tip.hidden = false;
      tip.style.left = e.clientX + 12 + "px";
      tip.style.top = e.clientY + 12 + "px";
    });
    table.addEventListener("mousemove", function (e) {
      if (tip.hidden) return;
      tip.style.left = e.clientX + 12 + "px";
      tip.style.top = e.clientY + 12 + "px";
    });
    table.addEventListener("mouseout", function (e) {
      var row = e.target.closest(".team-statistics-table__row");
      if (!row) return;
      var rel = e.relatedTarget;
      if (rel && row.contains(rel)) return;
      tip.hidden = true;
    });
  }

  function initTeamPlayerAnalyticsCharts() {
    var root = document.getElementById("team-player-analytics");
    var dataEl = document.getElementById("team-player-analytics-data");
    if (!root || !dataEl) return;
    var archive;
    try {
      archive = JSON.parse(dataEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    var staticBase = dataEl.getAttribute("data-static-base") || "";
    var segments = archive.segments || [];
    var seasons = archive.seasons || [];
    var datasets = archive.datasets || {};
    if (!seasons.length) {
      var emptyOnly = root.querySelector("[data-team-player-chart-empty]");
      if (emptyOnly) emptyOnly.hidden = false;
      return;
    }

    var seasonSel = root.querySelector("[data-team-player-chart-season]");
    var segmentSel = root.querySelector("[data-team-player-chart-segment]");
    var kindSel = root.querySelector("[data-team-player-chart-kind]");
    var xSel = root.querySelector("[data-team-player-chart-x]");
    var ySel = root.querySelector("[data-team-player-chart-y]");
    var normSel = root.querySelector("[data-team-player-chart-norm]");
    var pointsWrap = root.querySelector("[data-team-player-chart-points]");
    var axisX = root.querySelector("[data-team-player-chart-axis-x]");
    var axisY = root.querySelector("[data-team-player-chart-axis-y]");
    var midX = root.querySelector("[data-team-player-chart-midline-x]");
    var midY = root.querySelector("[data-team-player-chart-midline-y]");
    var empty = root.querySelector("[data-team-player-chart-empty]");
    if (!seasonSel || !segmentSel || !kindSel || !xSel || !ySel || !normSel || !pointsWrap) return;

    var tip = document.createElement("div");
    tip.className = "team-player-analytics-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);

    function fillSelect(sel, options, selected) {
      sel.innerHTML = "";
      options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(opt.value) === String(selected)) o.selected = true;
        sel.appendChild(o);
      });
    }

    fillSelect(
      seasonSel,
      seasons.map(function (s) {
        return { value: s.id, label: s.label };
      }),
      archive.default_season_id
    );
    fillSelect(
      segmentSel,
      segments.map(function (s) {
        return { value: s.key, label: s.label };
      }),
      archive.default_segment || "rs"
    );
    kindSel.value = archive.default_kind || "skater";
    normSel.value = archive.default_norm || "per_game";

    function metricsForKind(kind) {
      return kind === "goalie" ? archive.goalie_metrics || [] : archive.skater_metrics || [];
    }

    function metricByKey(kind) {
      var map = {};
      metricsForKind(kind).forEach(function (m) {
        map[m.key] = m;
      });
      return map;
    }

    function refreshMetricSelects() {
      var kind = kindSel.value;
      var defs = metricsForKind(kind);
      var xDefault = kind === "goalie" ? archive.default_x_goalie : archive.default_x_skater;
      var yDefault = kind === "goalie" ? archive.default_y_goalie : archive.default_y_skater;
      var opts = defs.map(function (m) {
        return { value: m.key, label: m.label };
      });
      fillSelect(xSel, opts, xDefault || (defs[0] ? defs[0].key : ""));
      fillSelect(ySel, opts, yDefault || (defs[1] ? defs[1].key : defs[0] ? defs[0].key : ""));
    }
    refreshMetricSelects();

    function datasetKey() {
      return String(seasonSel.value) + "|" + String(segmentSel.value) + "|" + String(kindSel.value);
    }

    function currentPlayers() {
      var ds = datasets[datasetKey()];
      return ds && ds.players ? ds.players : [];
    }

    function metricValue(player, key, norm, meta) {
      var m = player.metrics || {};
      var raw = m[key];
      if (raw == null || raw === "") {
        if (norm === "per_60" && m[key + "_per_60"] != null) return Number(m[key + "_per_60"]);
        return null;
      }
      var num = Number(raw);
      if (!isFinite(num)) return null;
      if (norm === "per_60") {
        if (m[key + "_per_60"] != null) return Number(m[key + "_per_60"]);
        if (!meta.per_60) return num;
        return null;
      }
      if (norm === "per_game" && meta.per_game) {
        var gp = m.gp ? Number(m.gp) : 0;
        if (!gp) return null;
        return num / gp;
      }
      return num;
    }

    function metricLabel(key, norm, meta) {
      if (norm === "per_game" && meta.per_game) return meta.label + " Per Game";
      if (norm === "per_60" && (meta.per_60 || meta.per_game)) return meta.label + " Per 60";
      return meta.label;
    }

    function formatMetric(key, value, meta) {
      var dec = meta.decimals != null ? meta.decimals : 2;
      return Number(value).toFixed(dec);
    }

    function playerInitials(name) {
      var parts = String(name || "").trim().split(/\s+/);
      if (!parts.length) return "?";
      if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }

    function updateQuadrants(xMeta, yMeta) {
      var xHigh = (xMeta && xMeta.better) === "high";
      var yHigh = (yMeta && yMeta.better) === "high";
      var labels = { tl: "Low/Low", tr: "High/High", bl: "Low/Low", br: "Strong" };
      if (xHigh && !yHigh) labels = { tl: "Finishing Lucky", tr: "Sniper", bl: "Bad", br: "Unlucky" };
      else if (xHigh && yHigh) labels = { tl: "Low/Low", tr: "High/High", bl: "Low/Low", br: "Strong" };
      else if (!xHigh && !yHigh) labels = { tl: "Strong", tr: "Mixed", bl: "Mixed", br: "Weak" };
      root.querySelectorAll("[data-team-player-chart-quad]").forEach(function (el) {
        var pos = el.getAttribute("data-team-player-chart-quad");
        if (pos && labels[pos]) el.textContent = labels[pos];
      });
    }

    function renderChart() {
      var players = currentPlayers();
      var kind = kindSel.value;
      var defs = metricByKey(kind);
      var xKey = xSel.value;
      var yKey = ySel.value;
      var norm = normSel.value;
      var xMeta = defs[xKey] || { decimals: 2 };
      var yMeta = defs[yKey] || { decimals: 2 };
      if (!players.length) {
        pointsWrap.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;

      var plotted = [];
      players.forEach(function (player) {
        var xv = metricValue(player, xKey, norm, xMeta);
        var yv = metricValue(player, yKey, norm, yMeta);
        if (xv == null || yv == null) return;
        plotted.push({ player: player, x: xv, y: yv });
      });
      if (!plotted.length) {
        pointsWrap.innerHTML = "";
        if (empty) empty.hidden = false;
        return;
      }

      var xs = plotted.map(function (p) {
        return p.x;
      });
      var ys = plotted.map(function (p) {
        return p.y;
      });
      var minX = Math.min.apply(null, xs);
      var maxX = Math.max.apply(null, xs);
      var minY = Math.min.apply(null, ys);
      var maxY = Math.max.apply(null, ys);
      if (minX === maxX) {
        minX -= 1;
        maxX += 1;
      }
      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }
      var padX = (maxX - minX) * 0.1;
      var padY = (maxY - minY) * 0.1;
      minX -= padX;
      maxX += padX;
      minY -= padY;
      maxY += padY;

      var avgX = xs.reduce(function (a, b) {
        return a + b;
      }, 0) / xs.length;
      var avgY = ys.reduce(function (a, b) {
        return a + b;
      }, 0) / ys.length;
      if (midX) midX.style.left = ((avgX - minX) / (maxX - minX)) * 100 + "%";
      if (midY) midY.style.top = 100 - ((avgY - minY) / (maxY - minY)) * 100 + "%";
      if (axisX) axisX.textContent = metricLabel(xKey, norm, xMeta);
      if (axisY) axisY.textContent = metricLabel(yKey, norm, yMeta);
      updateQuadrants(xMeta, yMeta);

      var existing = {};
      Array.from(pointsWrap.querySelectorAll("[data-team-player-chart-point]")).forEach(function (el) {
        existing[el.getAttribute("data-player-id")] = el;
      });

      plotted.forEach(function (p) {
        var left = ((p.x - minX) / (maxX - minX)) * 100;
        var top = 100 - ((p.y - minY) / (maxY - minY)) * 100;
        var id = String(p.player.player_id);
        var el = existing[id];
        if (!el) {
          el = document.createElement("a");
          el.className = "team-player-analytics__point";
          el.setAttribute("data-team-player-chart-point", "1");
          el.setAttribute("data-player-id", id);
          el.href = withRoot("/player/" + id);
          var marker = document.createElement("span");
          marker.className = "team-player-analytics__marker";
          var img = document.createElement("img");
          img.className = "team-player-analytics__headshot";
          img.alt = "";
          var initials = document.createElement("span");
          initials.className = "team-player-analytics__initials";
          marker.appendChild(img);
          marker.appendChild(initials);
          el.appendChild(marker);
          pointsWrap.appendChild(el);
        }
        var imgEl = el.querySelector(".team-player-analytics__headshot");
        var initEl = el.querySelector(".team-player-analytics__initials");
        if (p.player.headshot_rel && imgEl) {
          imgEl.src = staticBase + p.player.headshot_rel;
          imgEl.hidden = false;
          if (initEl) initEl.hidden = true;
        } else {
          if (imgEl) imgEl.hidden = true;
          if (initEl) {
            initEl.textContent = playerInitials(p.player.name);
            initEl.hidden = false;
          }
        }
        el.style.setProperty("--point-left", left + "%");
        el.style.setProperty("--point-top", top + "%");
        el.setAttribute("data-player-name", p.player.name || "Player");
        el.setAttribute("data-player-position", p.player.position || "");
        el.setAttribute("data-x-label", metricLabel(xKey, norm, xMeta));
        el.setAttribute("data-y-label", metricLabel(yKey, norm, yMeta));
        el.setAttribute("data-x-value", formatMetric(xKey, p.x, xMeta));
        el.setAttribute("data-y-value", formatMetric(yKey, p.y, yMeta));
        el.setAttribute("data-gp", p.player.metrics && p.player.metrics.gp != null ? String(p.player.metrics.gp) : "");
        if (kind === "goalie") {
          el.setAttribute("data-extra-1", "GSAA: " + (p.player.metrics.gsaa != null ? Number(p.player.metrics.gsaa).toFixed(2) : "—"));
          el.setAttribute("data-extra-2", "SV%: " + (p.player.metrics.sv_pct != null ? Number(p.player.metrics.sv_pct).toFixed(3) : "—"));
        } else {
          el.setAttribute("data-extra-1", "CF%: " + (p.player.metrics.cf_pct != null ? Number(p.player.metrics.cf_pct).toFixed(1) : "—"));
          el.setAttribute("data-extra-2", "PTS/60: " + (p.player.metrics.pts_per_60 != null ? Number(p.player.metrics.pts_per_60).toFixed(2) : "—"));
        }
        delete existing[id];
      });
      Object.keys(existing).forEach(function (id) {
        if (existing[id] && existing[id].parentNode) existing[id].parentNode.removeChild(existing[id]);
      });
    }

    function tipHtml(el) {
      var img = el.querySelector(".team-player-analytics__headshot");
      var logoSrc = img && !img.hidden ? img.getAttribute("src") : "";
      return (
        '<div class="team-player-analytics-tooltip__inner">' +
        (logoSrc ? '<img class="team-player-analytics-tooltip__headshot" src="' + escapeAttr(logoSrc) + '" alt="">' : "") +
        '<div class="team-player-analytics-tooltip__body">' +
        "<strong>" +
        escapeHtml(el.getAttribute("data-player-name") || "Player") +
        "</strong>" +
        (el.getAttribute("data-player-position")
          ? '<span class="team-player-analytics-tooltip__pos">' + escapeHtml(el.getAttribute("data-player-position")) + "</span>"
          : "") +
        '<span class="team-player-analytics-tooltip__metric">' +
        escapeHtml(el.getAttribute("data-x-label") || "") +
        ": <strong>" +
        escapeHtml(el.getAttribute("data-x-value") || "") +
        "</strong></span>" +
        '<span class="team-player-analytics-tooltip__metric">' +
        escapeHtml(el.getAttribute("data-y-label") || "") +
        ": <strong>" +
        escapeHtml(el.getAttribute("data-y-value") || "") +
        "</strong></span>" +
        (el.getAttribute("data-gp")
          ? '<span class="team-player-analytics-tooltip__metric">GP: <strong>' + escapeHtml(el.getAttribute("data-gp")) + "</strong></span>"
          : "") +
        (el.getAttribute("data-extra-1")
          ? '<span class="team-player-analytics-tooltip__metric">' + escapeHtml(el.getAttribute("data-extra-1")) + "</span>"
          : "") +
        (el.getAttribute("data-extra-2")
          ? '<span class="team-player-analytics-tooltip__metric">' + escapeHtml(el.getAttribute("data-extra-2")) + "</span>"
          : "") +
        "</div></div>"
      );
    }

    function moveTip(e) {
      tip.style.left = e.clientX + 14 + "px";
      tip.style.top = e.clientY + 14 + "px";
    }

    pointsWrap.addEventListener("mouseover", function (e) {
      var point = e.target.closest("[data-team-player-chart-point]");
      if (!point || !pointsWrap.contains(point)) return;
      tip.innerHTML = tipHtml(point);
      tip.hidden = false;
      point.classList.add("is-active");
      moveTip(e);
    });
    pointsWrap.addEventListener("mousemove", function (e) {
      if (!tip.hidden) moveTip(e);
    });
    pointsWrap.addEventListener("mouseout", function (e) {
      var point = e.target.closest("[data-team-player-chart-point]");
      if (!point) return;
      var rel = e.relatedTarget;
      if (rel && point.contains(rel)) return;
      tip.hidden = true;
      point.classList.remove("is-active");
    });
    pointsWrap.addEventListener("focusin", function (e) {
      var point = e.target.closest("[data-team-player-chart-point]");
      if (!point) return;
      tip.innerHTML = tipHtml(point);
      tip.hidden = false;
      point.classList.add("is-active");
      var rect = point.getBoundingClientRect();
      tip.style.left = rect.right + 10 + "px";
      tip.style.top = rect.top + "px";
    });
    pointsWrap.addEventListener("focusout", function (e) {
      var point = e.target.closest("[data-team-player-chart-point]");
      if (!point) return;
      tip.hidden = true;
      point.classList.remove("is-active");
    });

    kindSel.addEventListener("change", function () {
      refreshMetricSelects();
      renderChart();
    });
    [seasonSel, segmentSel, xSel, ySel, normSel].forEach(function (sel) {
      sel.addEventListener("change", renderChart);
    });
    renderChart();
  }

  function initTeamPlayerTrendCharts() {
    var root = document.getElementById("team-player-trends");
    var dataEl = document.getElementById("team-player-trends-data");
    if (!root || !dataEl) return;
    var archive;
    try {
      archive = JSON.parse(dataEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    var staticBase = dataEl.getAttribute("data-static-base") || "";
    var seasons = archive.seasons || [];
    var segments = archive.segments || [];
    var datasets = archive.datasets || {};
    var positionFilters = archive.position_filters || [];
    var teamLogoUrl = root.getAttribute("data-team-logo-url") || "";
    if (!seasons.length) {
      var emptyOnly = root.querySelector("[data-team-player-trend-empty]");
      if (emptyOnly) emptyOnly.hidden = false;
      return;
    }

    var seasonSel = root.querySelector("[data-team-player-trend-season]");
    var segmentSel = root.querySelector("[data-team-player-trend-segment]");
    var kindSel = root.querySelector("[data-team-player-trend-kind]");
    var metricSel = root.querySelector("[data-team-player-trend-metric]");
    var positionSel = root.querySelector("[data-team-player-trend-position]");
    var chartWrap = root.querySelector("[data-team-player-trend-chart-wrap]");
    var chartSvg = root.querySelector("[data-team-player-trend-chart]");
    var labelsWrap = root.querySelector("[data-team-player-trend-labels]");
    var tip = root.querySelector("[data-team-player-trend-tooltip]");
    var empty = root.querySelector("[data-team-player-trend-empty]");
    if (!seasonSel || !segmentSel || !kindSel || !metricSel || !positionSel || !chartWrap || !chartSvg || !labelsWrap || !tip) {
      return;
    }

    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    function fillSelect(sel, options, selected) {
      sel.innerHTML = "";
      options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(opt.value) === String(selected)) o.selected = true;
        sel.appendChild(o);
      });
    }

    fillSelect(
      seasonSel,
      seasons.map(function (s) {
        return { value: s.id, label: s.label };
      }),
      archive.default_season_id
    );
    fillSelect(
      segmentSel,
      segments.map(function (s) {
        return { value: s.key, label: s.label };
      }),
      archive.default_segment || "rs"
    );
    kindSel.value = archive.default_kind || "skater";
    fillSelect(
      positionSel,
      positionFilters.map(function (f) {
        return { value: f.key, label: f.label };
      }),
      archive.default_position_filter || "all"
    );

    function metricsForKind(kind) {
      return kind === "goalie" ? archive.goalie_metrics || [] : archive.skater_metrics || [];
    }

    function metricByKey(kind) {
      var map = {};
      metricsForKind(kind).forEach(function (m) {
        map[m.key] = m;
      });
      return map;
    }

    function refreshMetricSelect() {
      var kind = kindSel.value;
      var defs = metricsForKind(kind);
      var defaultKey = kind === "goalie" ? archive.default_metric_goalie : archive.default_metric_skater;
      fillSelect(
        metricSel,
        defs.map(function (m) {
          return { value: m.key, label: m.label };
        }),
        defaultKey || (defs[0] ? defs[0].key : "")
      );
    }
    refreshMetricSelect();

    function datasetKey() {
      return String(seasonSel.value) + "|" + String(segmentSel.value) + "|" + String(kindSel.value);
    }

    function currentDataset() {
      return datasets[datasetKey()] || null;
    }

    function isForwardPosition(pos) {
      var p = String(pos || "")
        .trim()
        .toUpperCase();
      if (!p) return false;
      if (p === "C" || p === "LW" || p === "RW" || p === "W" || p === "F" || p === "LF" || p === "RF" || p === "LC" || p === "RC") {
        return true;
      }
      return p.charAt(0) === "F";
    }

    function isDefensePosition(pos) {
      var p = String(pos || "")
        .trim()
        .toUpperCase();
      if (!p) return false;
      if (p === "D" || p === "LD" || p === "RD" || p === "DF") return true;
      return p.charAt(0) === "D";
    }

    function isGoaliePosition(pos) {
      var p = String(pos || "")
        .trim()
        .toUpperCase();
      return p === "G" || p === "GK";
    }

    function passesPositionFilter(player, filter, kind) {
      if (filter === "all") return true;
      var pos = player.position || "";
      if (filter === "forwards") return isForwardPosition(pos);
      if (filter === "defense") return isDefensePosition(pos);
      if (filter === "goalies") return isGoaliePosition(pos) || kind === "goalie";
      return true;
    }

    function cumulativeMetricValue(series, index, metric) {
      var mode = metric.mode || "sum";
      var i;
      if (mode === "sum") {
        var total = 0;
        for (i = 0; i <= index; i++) {
          var v = series[i].counts[metric.key];
          if (v != null) total += Number(v);
        }
        return total;
      }
      if (mode === "ratio") {
        var num = 0;
        var den = 0;
        for (i = 0; i <= index; i++) {
          var c = series[i].counts || {};
          if (c[metric.num] != null) num += Number(c[metric.num]);
          if (c[metric.den] != null) den += Number(c[metric.den]);
        }
        if (!den) return null;
        return (num / den) * (metric.scale != null ? metric.scale : 1);
      }
      if (mode === "avg") {
        var sum = 0;
        var n = 0;
        for (i = 0; i <= index; i++) {
          var av = series[i].counts[metric.key];
          if (av != null && isFinite(Number(av))) {
            sum += Number(av);
            n++;
          }
        }
        return n ? sum / n : null;
      }
      if (mode === "gaa") {
        var ga = 0;
        var toi = 0;
        for (i = 0; i <= index; i++) {
          var gc = series[i].counts || {};
          ga += Number(gc.ga || 0);
          toi += Number(gc.toi_seconds || 0);
        }
        return toi > 0 ? (ga * 3600) / toi : null;
      }
      return null;
    }

    function buildCumulativeSeries(player, metric) {
      var out = [];
      var series = player.series || [];
      for (var i = 0; i < series.length; i++) {
        out.push({
          date: series[i].date,
          game_number: series[i].game_number,
          value: cumulativeMetricValue(series, i, metric),
        });
      }
      return out;
    }

    function trendLineColor(playerId) {
      var hue = (Number(playerId) * 47) % 360;
      return "hsl(" + hue + ", 68%, 58%)";
    }

    function playerInitials(name) {
      var parts = String(name || "").trim().split(/\s+/);
      if (!parts.length) return "?";
      if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }

    function formatMetricValue(value, metric) {
      if (value == null || !isFinite(Number(value))) return "—";
      var dec = metric.decimals != null ? metric.decimals : 2;
      return Number(value).toFixed(dec);
    }

    function hideTip() {
      tip.hidden = true;
      tip.textContent = "";
      chartSvg.querySelectorAll(".team-player-trends__point.is-active").forEach(function (el) {
        el.classList.remove("is-active");
      });
    }

    function showTip(player, point, metric, clientX, clientY) {
      var dateLabel = point.date || "Unknown date";
      tip.innerHTML =
        "<strong>" +
        escapeHtml(player.name || "Player") +
        "</strong>" +
        (player.position ? '<span class="team-player-trends__tooltip-pos">' + escapeHtml(player.position) + "</span>" : "") +
        '<span class="team-player-trends__tooltip-line">Game ' +
        escapeHtml(String(point.display_game_number || point.game_number)) +
        " · " +
        escapeHtml(dateLabel) +
        "</span>" +
        '<span class="team-player-trends__tooltip-line">' +
        escapeHtml(metric.label) +
        ": <strong>" +
        escapeHtml(formatMetricValue(point.value, metric)) +
        "</strong></span>";
      tip.hidden = false;
      var wrapRect = chartWrap.getBoundingClientRect();
      var lx = clientX - wrapRect.left + chartWrap.scrollLeft + 12;
      var ly = clientY - wrapRect.top + chartWrap.scrollTop + 12;
      lx = Math.max(8, Math.min(lx, chartWrap.scrollWidth - tip.offsetWidth - 8));
      ly = Math.max(8, Math.min(ly, chartWrap.scrollHeight - tip.offsetHeight - 8));
      tip.style.left = lx + "px";
      tip.style.top = ly + "px";
    }

    function renderChart() {
      var ds = currentDataset();
      var kind = kindSel.value;
      var metricKey = metricSel.value;
      var defs = metricByKey(kind);
      var metric = defs[metricKey] || { label: metricKey, decimals: 2, mode: "sum" };
      var filter = positionSel.value;
      labelsWrap.innerHTML = "";
      chartSvg.innerHTML = "";
      hideTip();

      if (!ds || !ds.players || !ds.players.length) {
        if (empty) empty.hidden = false;
        return;
      }

      var players = ds.players.filter(function (player) {
        return passesPositionFilter(player, filter, kind) && player.series && player.series.length;
      });
      if (!players.length) {
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;

      var rawPlotted = [];
      var actualMaxGame = 1;
      players.forEach(function (player) {
        var series = buildCumulativeSeries(player, metric);
        var valid = series.filter(function (pt) {
          return pt.value != null && isFinite(Number(pt.value));
        });
        if (!valid.length) return;
        valid.forEach(function (pt) {
          if (pt.game_number > actualMaxGame) actualMaxGame = pt.game_number;
        });
        rawPlotted.push({
          player: player,
          series: valid,
          color: trendLineColor(player.player_id),
        });
      });

      var isRegularSeason = String(segmentSel.value) === "rs";
      var datasetGameCount = Number(ds.game_count || actualMaxGame || 1);
      var maxGame = isRegularSeason ? Math.min(datasetGameCount, 82) : datasetGameCount;
      var plotted = [];
      rawPlotted.forEach(function (plot) {
        var valid = plot.series.filter(function (pt) {
          return pt.game_number <= maxGame;
        });
        if (!valid.length) return;
        plotted.push({
          player: plot.player,
          series: valid,
          color: plot.color,
          finalValue: Number(valid[valid.length - 1].value),
        });
      });

      if (!plotted.length) {
        if (empty) empty.hidden = false;
        return;
      }

      var allValues = [];
      plotted.forEach(function (p) {
        p.series.forEach(function (pt) {
          allValues.push(Number(pt.value));
        });
      });
      var minY = Math.min.apply(null, allValues);
      var maxY = Math.max.apply(null, allValues);
      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }
      var padY = (maxY - minY) * 0.08;
      minY -= padY;
      maxY += padY;

      var width = 900;
      var height = 300;
      var pad = { top: 18, right: 20, bottom: 52, left: 54 };
      var plotW = width - pad.left - pad.right;
      var plotH = height - pad.top - pad.bottom;

      chartSvg.setAttribute("viewBox", "0 0 " + width + " " + height);
      chartSvg.setAttribute("width", "100%");
      chartSvg.setAttribute("height", String(height));
      chartSvg.style.minWidth = "";

      var ns = "http://www.w3.org/2000/svg";
      function svgEl(name, attrs) {
        var el = document.createElementNS(ns, name);
        Object.keys(attrs || {}).forEach(function (key) {
          el.setAttribute(key, attrs[key]);
        });
        return el;
      }

      if (teamLogoUrl) {
        chartSvg.appendChild(
          svgEl("image", {
            href: teamLogoUrl,
            x: String(pad.left + plotW / 2 - 105),
            y: String(pad.top + plotH / 2 - 105),
            width: "210",
            height: "210",
            class: "team-player-trends__watermark",
            preserveAspectRatio: "xMidYMid meet",
            "aria-hidden": "true",
          })
        );
      }

      var grid = svgEl("g", { class: "team-player-trends__grid" });
      for (var g = 0; g <= 4; g++) {
        var gy = pad.top + (plotH * g) / 4;
        grid.appendChild(
          svgEl("line", {
            x1: String(pad.left),
            y1: String(gy),
            x2: String(width - pad.right),
            y2: String(gy),
            class: "team-player-trends__grid-line",
          })
        );
        var tickVal = maxY - ((maxY - minY) * g) / 4;
        var tick = svgEl("text", {
          x: String(pad.left - 8),
          y: String(gy + 4),
          class: "team-player-trends__axis-tick",
          "text-anchor": "end",
        });
        tick.textContent = formatMetricValue(tickVal, metric);
        grid.appendChild(tick);
      }
      chartSvg.appendChild(grid);

      var xTicks = Math.min(maxGame, 12);
      var xStep = Math.max(1, Math.ceil(maxGame / xTicks));
      for (var gx = 1; gx <= maxGame; gx += xStep) {
        var gxPos = pad.left + ((gx - 1) / Math.max(1, maxGame - 1)) * plotW;
        var xTick = svgEl("text", {
          x: String(gxPos),
          y: String(height - pad.bottom + 22),
          class: "team-player-trends__axis-tick team-player-trends__axis-tick--x",
          "text-anchor": "middle",
        });
        xTick.textContent = String(gx);
        chartSvg.appendChild(xTick);
      }

      var axisY = svgEl("text", {
        x: "12",
        y: String(pad.top + plotH / 2),
        class: "team-player-trends__axis-title",
        transform: "rotate(-90 12 " + String(pad.top + plotH / 2) + ")",
        "text-anchor": "middle",
      });
      axisY.textContent = "Cumulative " + metric.label;
      chartSvg.appendChild(axisY);

      var axisX = svgEl("text", {
        x: String(pad.left + plotW / 2),
        y: String(height - 8),
        class: "team-player-trends__axis-title team-player-trends__axis-title--x",
        "text-anchor": "middle",
      });
      axisX.textContent = "Team Game Number";
      chartSvg.appendChild(axisX);

      function xForGame(gameNumber) {
        if (maxGame <= 1) return pad.left + plotW / 2;
        return pad.left + ((displayGameNumber(gameNumber) - 1) / (maxGame - 1)) * plotW;
      }
      function displayGameNumber(gameNumber) {
        var displayGame = Number(gameNumber || 1);
        if (!isRegularSeason) return displayGame;
        return Math.max(1, Math.min(displayGame, 82));
      }
      function yForValue(value) {
        return pad.top + plotH - ((Number(value) - minY) / (maxY - minY)) * plotH;
      }

      var hoverItems = [];
      plotted.forEach(function (plot) {
        var pathD = plot.series
          .map(function (pt, idx) {
            var cmd = idx === 0 ? "M" : "L";
            return cmd + xForGame(pt.game_number) + " " + yForValue(pt.value);
          })
          .join(" ");
        var path = svgEl("path", {
          d: pathD,
          class: "team-player-trends__line" + (reducedMotion ? "" : " team-player-trends__line--animate"),
          fill: "none",
          stroke: plot.color,
          "data-player-id": String(plot.player.player_id),
        });
        chartSvg.appendChild(path);

        plot.series.forEach(function (pt) {
          pt.display_game_number = displayGameNumber(pt.game_number);
          var circle = svgEl("circle", {
            cx: String(xForGame(pt.game_number)),
            cy: String(yForValue(pt.value)),
            r: "2.4",
            class: "team-player-trends__point",
            fill: plot.color,
            stroke: "#0f172a",
            "stroke-width": "0.75",
            tabindex: "0",
            role: "button",
            "aria-label": (plot.player.name || "Player") + " game " + pt.display_game_number,
            "data-player-id": String(plot.player.player_id),
            "data-game-number": String(pt.display_game_number),
          });
          circle._trendPlayer = plot.player;
          circle._trendPoint = pt;
          circle._trendX = xForGame(pt.game_number);
          circle._trendY = yForValue(pt.value);
          hoverItems.push({ player: plot.player, point: pt, el: circle, x: circle._trendX, y: circle._trendY });
          chartSvg.appendChild(circle);
        });
      });

      plotted
        .slice()
        .sort(function (a, b) {
          return b.finalValue - a.finalValue || String(a.player.name || "").localeCompare(String(b.player.name || ""));
        })
        .forEach(function (plot) {
        var last = plot.series[plot.series.length - 1];
        var chip = document.createElement("a");
        chip.className = "team-player-trends__label";
        chip.href = withRoot("/player/" + plot.player.player_id);
        chip.style.setProperty("--trend-color", plot.color);
        if (plot.player.headshot_rel) {
          var img = document.createElement("img");
          img.className = "team-player-trends__label-headshot";
          img.src = staticBase + plot.player.headshot_rel;
          img.alt = "";
          chip.appendChild(img);
        } else {
          var initials = document.createElement("span");
          initials.className = "team-player-trends__label-initials";
          initials.textContent = playerInitials(plot.player.name);
          chip.appendChild(initials);
        }
        var name = document.createElement("span");
        name.className = "team-player-trends__label-name";
        name.textContent = plot.player.name || "Player";
        chip.appendChild(name);
        var stat = document.createElement("span");
        stat.className = "team-player-trends__label-value";
        stat.textContent = formatMetricValue(last.value, metric);
        chip.appendChild(stat);
        labelsWrap.appendChild(chip);
      });

      function activatePoint(circle, clientX, clientY) {
        hideTip();
        circle.classList.add("is-active");
        showTip(circle._trendPlayer, circle._trendPoint, metric, clientX, clientY);
      }

      function svgPointFromEvent(e) {
        if (!chartSvg.createSVGPoint) return null;
        var matrix = chartSvg.getScreenCTM && chartSvg.getScreenCTM();
        if (!matrix) return null;
        var pt = chartSvg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        return pt.matrixTransform(matrix.inverse());
      }

      function activateNearestPoint(e) {
        var pt = svgPointFromEvent(e);
        if (!pt || pt.x < pad.left || pt.x > width - pad.right || pt.y < pad.top || pt.y > height - pad.bottom) {
          hideTip();
          return;
        }
        var nearest = null;
        var best = Infinity;
        hoverItems.forEach(function (item) {
          var dx = item.x - pt.x;
          var dy = item.y - pt.y;
          var score = dx * dx + dy * dy * 0.65;
          if (score < best) {
            best = score;
            nearest = item;
          }
        });
        if (!nearest) {
          hideTip();
          return;
        }
        hideTip();
        nearest.el.classList.add("is-active");
        showTip(nearest.player, nearest.point, metric, e.clientX, e.clientY);
      }

      chartSvg.onpointermove = activateNearestPoint;
      chartSvg.onpointerleave = hideTip;

      chartSvg.querySelectorAll(".team-player-trends__point").forEach(function (circle) {
        circle.addEventListener("pointerenter", function (e) {
          activatePoint(circle, e.clientX, e.clientY);
        });
        circle.addEventListener("pointermove", function (e) {
          if (!tip.hidden) showTip(circle._trendPlayer, circle._trendPoint, metric, e.clientX, e.clientY);
        });
        circle.addEventListener("pointerleave", hideTip);
        circle.addEventListener("focus", function () {
          var rect = circle.getBoundingClientRect();
          activatePoint(circle, rect.left + rect.width / 2, rect.top);
        });
        circle.addEventListener("blur", hideTip);
      });
    }

    kindSel.addEventListener("change", function () {
      refreshMetricSelect();
      renderChart();
    });
    [seasonSel, segmentSel, metricSel, positionSel].forEach(function (sel) {
      sel.addEventListener("change", renderChart);
    });
    renderChart();
  }

  function initTeamStatsTrendCharts() {
    var root = document.getElementById("team-stats-trends");
    var dataEl = document.getElementById("team-stats-trends-data");
    if (!root || !dataEl) return;
    var archive;
    try {
      archive = JSON.parse(dataEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    var teamLogoUrl = root.getAttribute("data-team-logo-url") || "";
    var seasons = archive.seasons || [];
    if (!seasons.length) {
      var emptyOnly = root.querySelector("[data-team-stats-trend-empty]");
      if (emptyOnly) emptyOnly.hidden = false;
      return;
    }

    var seasonSel = root.querySelector("[data-team-stats-trend-season]");
    var segmentSel = root.querySelector("[data-team-stats-trend-segment]");
    var situationSel = root.querySelector("[data-team-stats-trend-situation]");
    var basisSel = root.querySelector("[data-team-stats-trend-basis]");
    var modeSel = root.querySelector("[data-team-stats-trend-mode]");
    var metricSel = root.querySelector("[data-team-stats-trend-metric]");
    var chartWrap = root.querySelector("[data-team-stats-trend-chart-wrap]");
    var chartSvg = root.querySelector("[data-team-stats-trend-chart]");
    var tip = root.querySelector("[data-team-stats-trend-tooltip]");
    var empty = root.querySelector("[data-team-stats-trend-empty]");
    if (!seasonSel || !segmentSel || !situationSel || !basisSel || !modeSel || !metricSel || !chartWrap || !chartSvg || !tip) {
      return;
    }

    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var rsCap = Number(archive.rs_game_cap || 82);

    function fillSelect(sel, options, selected) {
      sel.innerHTML = "";
      options.forEach(function (opt) {
        var o = document.createElement("option");
        o.value = opt.value;
        o.textContent = opt.label;
        if (String(opt.value) === String(selected)) o.selected = true;
        sel.appendChild(o);
      });
    }

    fillSelect(
      seasonSel,
      seasons.map(function (s) {
        return { value: s.id, label: s.label };
      }),
      archive.default_season_id
    );
    fillSelect(
      segmentSel,
      (archive.segments || []).map(function (s) {
        return { value: s.key, label: s.label };
      }),
      archive.default_segment || "rs"
    );
    fillSelect(
      situationSel,
      (archive.situations || []).map(function (s) {
        return { value: s.key, label: s.label };
      }),
      archive.default_situation || "all"
    );
    basisSel.value = archive.default_basis || "totals";
    fillSelect(
      modeSel,
      (archive.trend_modes || []).map(function (m) {
        return { value: m.key, label: m.label };
      }),
      archive.default_trend_mode || "cumulative"
    );

    function metricHasData(ds, metric) {
      if (!ds || !ds.series || !ds.series.length) return true;
      if ((metric.mode || "sum") === "ratio") {
        return ds.series.some(function (pt) {
          var counts = pt.counts || {};
          var den = counts[metric.den];
          return den != null && isFinite(Number(den)) && Number(den) > 0;
        });
      }
      return ds.series.some(function (pt) {
        var counts = pt.counts || {};
        return counts[metric.key] != null && isFinite(Number(counts[metric.key]));
      });
    }

    function metricsForSituation(situation) {
      var ds = currentDataset();
      return (archive.metrics || []).filter(function (m) {
        return (!m.situations || m.situations.indexOf(situation) >= 0) && metricHasData(ds, m);
      });
    }

    function refreshMetricSelect() {
      var situation = situationSel.value;
      var defs = metricsForSituation(situation);
      var selected = metricSel.value;
      var keepSelected = defs.some(function (m) {
        return String(m.key) === String(selected);
      });
      fillSelect(
        metricSel,
        defs.map(function (m) {
          return { value: m.key, label: m.label };
        }),
        keepSelected ? selected : archive.default_metric || (defs[0] ? defs[0].key : "")
      );
    }
    refreshMetricSelect();

    function datasetKey() {
      return String(seasonSel.value) + "|" + String(segmentSel.value) + "|" + String(situationSel.value);
    }

    function currentDataset() {
      return (archive.datasets || {})[datasetKey()] || null;
    }

    function metricByKey() {
      var map = {};
      (archive.metrics || []).forEach(function (m) {
        map[m.key] = m;
      });
      return map;
    }

    function metricValueFromCounts(counts, metric, cumulativeIndex, series, perGameBasis) {
      var mode = metric.mode || "sum";
      var i;
      if (mode === "sum") {
        if (cumulativeIndex == null) {
          var rawValue = counts[metric.key];
          if (rawValue == null) return null;
          var raw = Number(rawValue || 0);
          return perGameBasis ? raw : raw;
        }
        var total = 0;
        var hasValue = false;
        for (i = 0; i <= cumulativeIndex; i++) {
          var v = series[i].counts[metric.key];
          if (v == null) continue;
          hasValue = true;
          total += Number(v || 0);
        }
        if (!hasValue) return null;
        if (perGameBasis) return total / (cumulativeIndex + 1);
        return total;
      }
      if (mode === "ratio") {
        var num = 0;
        var den = 0;
        var end = cumulativeIndex == null ? 0 : cumulativeIndex;
        var start = cumulativeIndex == null ? 0 : 0;
        if (cumulativeIndex == null) {
          num = Number(counts[metric.num] || 0);
          den = Number(counts[metric.den] || 0);
        } else {
          for (i = 0; i <= cumulativeIndex; i++) {
            num += Number(series[i].counts[metric.num] || 0);
            den += Number(series[i].counts[metric.den] || 0);
          }
          if (perGameBasis && metric.key !== "goal_for_pct" && metric.key !== "shot_share" && metric.key !== "pp_pct" && metric.key !== "pk_pct" && metric.key !== "hd_share") {
            return num / (cumulativeIndex + 1);
          }
        }
        if (!den) return null;
        return (num / den) * (metric.scale != null ? metric.scale : 1);
      }
      return null;
    }

    function gameLevelValues(series, metric, basis) {
      var perGameBasis = basis === "per_game";
      return series.map(function (pt, idx) {
        return metricValueFromCounts(pt.counts, metric, null, series.slice(0, idx + 1), perGameBasis);
      });
    }

    function buildPlottedSeries(series, metric, trendMode, basis) {
      var perGameBasis = basis === "per_game";
      var gameVals = gameLevelValues(series, metric, basis);
      var out = [];
      for (var i = 0; i < series.length; i++) {
        var value = null;
        if (trendMode === "game") {
          value = gameVals[i];
        } else if (trendMode === "cumulative") {
          value = metricValueFromCounts(series[i].counts, metric, i, series, perGameBasis);
        } else if (trendMode === "ma5" || trendMode === "ma10") {
          var window = trendMode === "ma5" ? 5 : 10;
          var start = Math.max(0, i - window + 1);
          var slice = gameVals.slice(start, i + 1).filter(function (v) {
            return v != null && isFinite(Number(v));
          });
          value = slice.length ? slice.reduce(function (a, b) { return a + Number(b); }, 0) / slice.length : null;
        }
        if (value == null || !isFinite(Number(value))) continue;
        out.push({
          date: series[i].date,
          game_number: series[i].game_number,
          value: Number(value),
        });
      }
      return out;
    }

    function formatMetricValue(value, metric) {
      if (value == null || !isFinite(Number(value))) return "—";
      var dec = metric.decimals != null ? metric.decimals : 2;
      return Number(value).toFixed(dec);
    }

    function hideTip() {
      tip.hidden = true;
      tip.textContent = "";
      chartSvg.querySelectorAll(".team-stats-trends__point.is-active").forEach(function (el) {
        el.classList.remove("is-active");
      });
    }

    function showTip(point, metric, clientX, clientY) {
      tip.innerHTML =
        "<strong>" +
        escapeHtml(archive.team_name || "Team") +
        "</strong>" +
        '<span class="team-stats-trends__tooltip-line">Game ' +
        escapeHtml(String(point.game_number)) +
        (point.date ? " · " + escapeHtml(point.date) : "") +
        "</span>" +
        '<span class="team-stats-trends__tooltip-line">' +
        escapeHtml(metric.label) +
        ": <strong>" +
        escapeHtml(formatMetricValue(point.value, metric)) +
        "</strong></span>";
      tip.hidden = false;
      var wrapRect = chartWrap.getBoundingClientRect();
      var lx = clientX - wrapRect.left + chartWrap.scrollLeft + 12;
      var ly = clientY - wrapRect.top + chartWrap.scrollTop + 12;
      lx = Math.max(8, Math.min(lx, chartWrap.scrollWidth - tip.offsetWidth - 8));
      ly = Math.max(8, Math.min(ly, chartWrap.scrollHeight - tip.offsetHeight - 8));
      tip.style.left = lx + "px";
      tip.style.top = ly + "px";
    }

    function renderChart() {
      var ds = currentDataset();
      var defs = metricByKey();
      var metric = defs[metricSel.value] || { label: metricSel.value, decimals: 2, mode: "sum" };
      var trendMode = modeSel.value;
      chartSvg.innerHTML = "";
      hideTip();

      if (!ds || !ds.series || !ds.series.length) {
        if (empty) empty.hidden = false;
        return;
      }
      if (empty) empty.hidden = true;

      var plotted = buildPlottedSeries(ds.series, metric, trendMode, basisSel.value);
      if (!plotted.length) {
        if (empty) empty.hidden = false;
        return;
      }

      var isRegularSeason = String(segmentSel.value) === "rs";
      var maxGame = isRegularSeason ? Math.min(Number(ds.game_count || plotted[plotted.length - 1].game_number), rsCap) : Number(ds.game_count || plotted[plotted.length - 1].game_number);
      if (isRegularSeason) {
        plotted = plotted.filter(function (pt) {
          return Number(pt.game_number || 0) <= maxGame;
        });
      }
      if (!plotted.length) {
        if (empty) empty.hidden = false;
        return;
      }
      var values = plotted.map(function (p) { return p.value; });
      var minY = Math.min.apply(null, values);
      var maxY = Math.max.apply(null, values);
      if (metric.zero_line) {
        minY = Math.min(minY, 0);
        maxY = Math.max(maxY, 0);
      }
      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }
      var padY = (maxY - minY) * 0.08;
      minY -= padY;
      maxY += padY;

      var width = 900;
      var height = 300;
      var pad = { top: 18, right: 20, bottom: 52, left: 54 };
      var plotW = width - pad.left - pad.right;
      var plotH = height - pad.top - pad.bottom;

      chartSvg.setAttribute("viewBox", "0 0 " + width + " " + height);
      chartSvg.setAttribute("width", "100%");
      chartSvg.setAttribute("height", String(height));

      var ns = "http://www.w3.org/2000/svg";
      function svgEl(name, attrs) {
        var el = document.createElementNS(ns, name);
        Object.keys(attrs || {}).forEach(function (key) {
          el.setAttribute(key, attrs[key]);
        });
        return el;
      }

      if (teamLogoUrl) {
        chartSvg.appendChild(
          svgEl("image", {
            href: teamLogoUrl,
            x: String(pad.left + plotW / 2 - 105),
            y: String(pad.top + plotH / 2 - 105),
            width: "210",
            height: "210",
            class: "team-stats-trends__watermark",
            preserveAspectRatio: "xMidYMid meet",
            "aria-hidden": "true",
          })
        );
      }

      function xForGame(gameNumber) {
        if (maxGame <= 1) return pad.left + plotW / 2;
        var gn = Math.min(Number(gameNumber || 1), maxGame);
        return pad.left + ((gn - 1) / (maxGame - 1)) * plotW;
      }
      function yForValue(value) {
        return pad.top + plotH - ((Number(value) - minY) / (maxY - minY)) * plotH;
      }

      var grid = svgEl("g", { class: "team-stats-trends__grid" });
      for (var g = 0; g <= 4; g++) {
        var gy = pad.top + (plotH * g) / 4;
        grid.appendChild(
          svgEl("line", {
            x1: String(pad.left),
            y1: String(gy),
            x2: String(width - pad.right),
            y2: String(gy),
            class: "team-stats-trends__grid-line",
          })
        );
      }
      chartSvg.appendChild(grid);

      if (metric.zero_line && minY < 0 && maxY > 0) {
        var zeroY = yForValue(0);
        chartSvg.appendChild(
          svgEl("line", {
            x1: String(pad.left),
            y1: String(zeroY),
            x2: String(width - pad.right),
            y2: String(zeroY),
            class: "team-stats-trends__zero-line",
          })
        );
      }

      var xTicks = Math.min(maxGame, 12);
      var xStep = Math.max(1, Math.ceil(maxGame / xTicks));
      for (var gx = 1; gx <= maxGame; gx += xStep) {
        var gxPos = xForGame(gx);
        var xTick = svgEl("text", {
          x: String(gxPos),
          y: String(height - pad.bottom + 22),
          class: "team-stats-trends__axis-tick team-stats-trends__axis-tick--x",
          "text-anchor": "middle",
        });
        xTick.textContent = String(gx);
        chartSvg.appendChild(xTick);
      }

      var axisY = svgEl("text", {
        x: "12",
        y: String(pad.top + plotH / 2),
        class: "team-stats-trends__axis-title",
        transform: "rotate(-90 12 " + String(pad.top + plotH / 2) + ")",
        "text-anchor": "middle",
      });
      axisY.textContent = metric.label;
      chartSvg.appendChild(axisY);

      var axisX = svgEl("text", {
        x: String(pad.left + plotW / 2),
        y: String(height - 8),
        class: "team-stats-trends__axis-title team-stats-trends__axis-title--x",
        "text-anchor": "middle",
      });
      axisX.textContent = "Team Game Number";
      chartSvg.appendChild(axisX);

      var pathD = plotted
        .map(function (pt, idx) {
          var cmd = idx === 0 ? "M" : "L";
          return cmd + xForGame(pt.game_number) + " " + yForValue(pt.value);
        })
        .join(" ");
      chartSvg.appendChild(
        svgEl("path", {
          d: pathD,
          class: "team-stats-trends__line" + (reducedMotion ? "" : " team-stats-trends__line--animate"),
          fill: "none",
          stroke: "var(--team-stats-trend-line, #f97316)",
        })
      );

      var hoverItems = [];
      plotted.forEach(function (pt, idx) {
        var isLatest = idx === plotted.length - 1;
        if (isLatest && teamLogoUrl) {
          var logoSize = 24;
          chartSvg.appendChild(
            svgEl("image", {
              href: teamLogoUrl,
              x: String(xForGame(pt.game_number) - logoSize / 2),
              y: String(yForValue(pt.value) - logoSize / 2),
              width: String(logoSize),
              height: String(logoSize),
              class: "team-stats-trends__latest-logo",
              preserveAspectRatio: "xMidYMid meet",
              "aria-hidden": "true",
            })
          );
        }
        var circle = svgEl("circle", {
          cx: String(xForGame(pt.game_number)),
          cy: String(yForValue(pt.value)),
          r: isLatest && teamLogoUrl ? "10" : "2.8",
          class: "team-stats-trends__point" + (isLatest ? " team-stats-trends__point--latest" : ""),
          fill: isLatest && teamLogoUrl ? "transparent" : "var(--team-stats-trend-line, #f97316)",
          stroke: isLatest ? "var(--team-stats-trend-accent, #0f172a)" : "#0f172a",
          "stroke-width": isLatest ? "1.4" : "0.75",
          tabindex: "0",
          role: "button",
        });
        circle._trendPoint = pt;
        circle._trendX = xForGame(pt.game_number);
        circle._trendY = yForValue(pt.value);
        hoverItems.push(circle);
        chartSvg.appendChild(circle);
      });

      function svgPointFromEvent(e) {
        if (!chartSvg.createSVGPoint) return null;
        var matrix = chartSvg.getScreenCTM && chartSvg.getScreenCTM();
        if (!matrix) return null;
        var pt = chartSvg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        return pt.matrixTransform(matrix.inverse());
      }

      function activateNearestPoint(e) {
        var pt = svgPointFromEvent(e);
        if (!pt || pt.x < pad.left || pt.x > width - pad.right || pt.y < pad.top || pt.y > height - pad.bottom) {
          hideTip();
          return;
        }
        var nearest = null;
        var best = Infinity;
        hoverItems.forEach(function (item) {
          var dx = item._trendX - pt.x;
          var dy = item._trendY - pt.y;
          var score = dx * dx + dy * dy;
          if (score < best) {
            best = score;
            nearest = item;
          }
        });
        if (!nearest) {
          hideTip();
          return;
        }
        hideTip();
        nearest.classList.add("is-active");
        showTip(nearest._trendPoint, metric, e.clientX, e.clientY);
      }

      chartSvg.onpointermove = activateNearestPoint;
      chartSvg.onpointerleave = hideTip;
      hoverItems.forEach(function (circle) {
        circle.addEventListener("focus", function () {
          var rect = circle.getBoundingClientRect();
          circle.classList.add("is-active");
          showTip(circle._trendPoint, metric, rect.left + rect.width / 2, rect.top);
        });
        circle.addEventListener("blur", hideTip);
      });
    }

    function refreshAndRenderChart() {
      refreshMetricSelect();
      renderChart();
    }

    situationSel.addEventListener("change", function () {
      refreshAndRenderChart();
    });
    [seasonSel, segmentSel].forEach(function (sel) {
      sel.addEventListener("change", refreshAndRenderChart);
    });
    [basisSel, modeSel, metricSel].forEach(function (sel) {
      sel.addEventListener("change", renderChart);
    });
    renderChart();
  }

  function initStandingsTrendCharts() {
    var root = document.getElementById("standings-trends");
    var dataEl = document.getElementById("standings-trends-data");
    if (!root || !dataEl) return;
    var payload;
    try {
      payload = JSON.parse(dataEl.textContent || "{}");
    } catch (_e) {
      return;
    }
    var teams = payload.teams || [];
    if (!teams.length) return;

    var leagueLogoUrl = root.getAttribute("data-league-logo-url") || "";
    var chartWrap = root.querySelector("[data-standings-trend-chart-wrap]");
    var chartSvg = root.querySelector("[data-standings-trend-chart]");
    var tip = root.querySelector("[data-standings-trend-tooltip]");
    if (!chartWrap || !chartSvg || !tip) return;

    var reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var maxGp = Number(payload.max_gp || payload.rs_game_cap || 82);
    if (!maxGp || maxGp < 1) maxGp = 82;
    maxGp = Math.min(maxGp, Number(payload.rs_game_cap || 82));

    function hideTip() {
      tip.hidden = true;
      tip.innerHTML = "";
      chartSvg.querySelectorAll(".standings-trends__point.is-active").forEach(function (el) {
        el.classList.remove("is-active");
      });
    }

    function showTip(team, point, clientX, clientY) {
      var line2 = point.result_line2
        ? '<span class="standings-trends__tooltip-line">' + escapeHtml(point.result_line2) + "</span>"
        : "";
      tip.innerHTML =
        '<span class="standings-trends__tooltip-line standings-trends__tooltip-line--result">' +
        escapeHtml(point.result_line1 || "") +
        "</span>" +
        line2 +
        '<span class="standings-trends__tooltip-line">' +
        escapeHtml(String(point.points_total)) +
        " Points After</span>" +
        '<span class="standings-trends__tooltip-line">' +
        escapeHtml(String(point.gp)) +
        " Games</span>";
      tip.hidden = false;
      var wrapRect = chartWrap.getBoundingClientRect();
      var lx = clientX - wrapRect.left + chartWrap.scrollLeft + 12;
      var ly = clientY - wrapRect.top + chartWrap.scrollTop + 12;
      lx = Math.max(8, Math.min(lx, chartWrap.scrollWidth - tip.offsetWidth - 8));
      ly = Math.max(8, Math.min(ly, chartWrap.scrollHeight - tip.offsetHeight - 8));
      tip.style.left = lx + "px";
      tip.style.top = ly + "px";
    }

    function renderChart() {
      chartSvg.innerHTML = "";
      hideTip();

      var plotted = [];
      teams.forEach(function (team) {
        var series = (team.points || []).filter(function (pt) {
          return pt.gp <= maxGp && pt.value != null && isFinite(Number(pt.value));
        });
        if (!series.length) return;
        plotted.push({
          team: team,
          series: series,
          color: team.color || "#60a5fa",
          finalValue: Number(series[series.length - 1].value),
        });
      });
      if (!plotted.length) return;

      var allValues = [];
      plotted.forEach(function (p) {
        p.series.forEach(function (pt) {
          allValues.push(Number(pt.value));
        });
      });
      var minY = Math.min.apply(null, allValues);
      var maxY = Math.max.apply(null, allValues);
      if (minY === maxY) {
        minY -= 1;
        maxY += 1;
      }
      var padY = (maxY - minY) * 0.08;
      minY -= padY;
      maxY += padY;

      var width = 900;
      var height = 320;
      var pad = { top: 20, right: 28, bottom: 54, left: 56 };
      var plotW = width - pad.left - pad.right;
      var plotH = height - pad.top - pad.bottom;

      chartSvg.setAttribute("viewBox", "0 0 " + width + " " + height);
      chartSvg.setAttribute("width", "100%");
      chartSvg.setAttribute("height", String(height));

      var ns = "http://www.w3.org/2000/svg";
      function svgEl(name, attrs) {
        var el = document.createElementNS(ns, name);
        Object.keys(attrs || {}).forEach(function (key) {
          el.setAttribute(key, attrs[key]);
        });
        return el;
      }

      if (leagueLogoUrl) {
        chartSvg.appendChild(
          svgEl("image", {
            href: leagueLogoUrl,
            x: String(pad.left + plotW / 2 - 105),
            y: String(pad.top + plotH / 2 - 105),
            width: "210",
            height: "210",
            class: "standings-trends__watermark",
            preserveAspectRatio: "xMidYMid meet",
            "aria-hidden": "true",
          })
        );
      }

      var grid = svgEl("g", { class: "standings-trends__grid" });
      for (var g = 0; g <= 4; g++) {
        var gy = pad.top + (plotH * g) / 4;
        grid.appendChild(
          svgEl("line", {
            x1: String(pad.left),
            y1: String(gy),
            x2: String(width - pad.right),
            y2: String(gy),
            class: "standings-trends__grid-line",
          })
        );
        var tickVal = maxY - ((maxY - minY) * g) / 4;
        var tick = svgEl("text", {
          x: String(pad.left - 8),
          y: String(gy + 4),
          class: "standings-trends__axis-tick",
          "text-anchor": "end",
        });
        tick.textContent = (tickVal > 0 ? "+" : "") + Math.round(tickVal);
        grid.appendChild(tick);
      }
      chartSvg.appendChild(grid);

      var zeroY = pad.top + plotH - ((0 - minY) / (maxY - minY)) * plotH;
      if (zeroY >= pad.top && zeroY <= pad.top + plotH) {
        chartSvg.appendChild(
          svgEl("line", {
            x1: String(pad.left),
            y1: String(zeroY),
            x2: String(width - pad.right),
            y2: String(zeroY),
            class: "standings-trends__zero-line",
          })
        );
      }

      var xTicks = Math.min(maxGp, 17);
      var xStep = Math.max(1, Math.ceil(maxGp / xTicks));
      for (var gx = 0; gx <= maxGp; gx += xStep) {
        var gxPos = pad.left + (gx / Math.max(1, maxGp)) * plotW;
        var xTick = svgEl("text", {
          x: String(gxPos),
          y: String(height - pad.bottom + 22),
          class: "standings-trends__axis-tick standings-trends__axis-tick--x",
          "text-anchor": "middle",
        });
        xTick.textContent = String(gx);
        chartSvg.appendChild(xTick);
      }

      var axisY = svgEl("text", {
        x: "12",
        y: String(pad.top + plotH / 2),
        class: "standings-trends__axis-title",
        transform: "rotate(-90 12 " + String(pad.top + plotH / 2) + ")",
        "text-anchor": "middle",
      });
      axisY.textContent = payload.y_axis_label || "Points Above Group Average";
      chartSvg.appendChild(axisY);

      var axisX = svgEl("text", {
        x: String(pad.left + plotW / 2),
        y: String(height - 8),
        class: "standings-trends__axis-title standings-trends__axis-title--x",
        "text-anchor": "middle",
      });
      axisX.textContent = payload.x_axis_label || "Games Played";
      chartSvg.appendChild(axisX);

      function xForGp(gp) {
        if (maxGp <= 0) return pad.left + plotW / 2;
        return pad.left + (Number(gp) / maxGp) * plotW;
      }
      function yForValue(value) {
        return pad.top + plotH - ((Number(value) - minY) / (maxY - minY)) * plotH;
      }

      var hoverItems = [];
      plotted.forEach(function (plot) {
        var pathD = plot.series
          .map(function (pt, idx) {
            var cmd = idx === 0 ? "M" : "L";
            return cmd + xForGp(pt.gp) + " " + yForValue(pt.value);
          })
          .join(" ");
        chartSvg.appendChild(
          svgEl("path", {
            d: pathD,
            class: "standings-trends__line" + (reducedMotion ? "" : " standings-trends__line--animate"),
            fill: "none",
            stroke: plot.color,
            "data-team-id": String(plot.team.team_id),
          })
        );

        plot.series.forEach(function (pt, idx) {
          var isLatest = idx === plot.series.length - 1;
          var cx = xForGp(pt.gp);
          var cy = yForValue(pt.value);
          if (isLatest && plot.team.logo_url) {
            var logoSize = 18;
            chartSvg.appendChild(
              svgEl("image", {
                href: plot.team.logo_url,
                x: String(cx - logoSize / 2),
                y: String(cy - logoSize / 2),
                width: String(logoSize),
                height: String(logoSize),
                class: "standings-trends__latest-logo",
                preserveAspectRatio: "xMidYMid meet",
                "aria-hidden": "true",
              })
            );
          }
          var circle = svgEl("circle", {
            cx: String(cx),
            cy: String(cy),
            r: isLatest && plot.team.logo_url ? "10" : "2.2",
            class: "standings-trends__point" + (isLatest ? " standings-trends__point--latest" : ""),
            fill: isLatest && plot.team.logo_url ? "transparent" : plot.color,
            stroke: isLatest ? "#0f172a" : plot.color,
            "stroke-width": isLatest ? "1" : "0.5",
            tabindex: "0",
            role: "button",
            "aria-label": (plot.team.abbr || plot.team.name || "Team") + " game " + pt.gp,
            "data-team-id": String(plot.team.team_id),
          });
          circle._trendTeam = plot.team;
          circle._trendPoint = pt;
          circle._trendX = cx;
          circle._trendY = cy;
          hoverItems.push({ team: plot.team, point: pt, el: circle, x: cx, y: cy });
          chartSvg.appendChild(circle);
        });
      });

      function activatePoint(circle, clientX, clientY) {
        hideTip();
        circle.classList.add("is-active");
        showTip(circle._trendTeam, circle._trendPoint, clientX, clientY);
      }

      function svgPointFromEvent(e) {
        if (!chartSvg.createSVGPoint) return null;
        var matrix = chartSvg.getScreenCTM && chartSvg.getScreenCTM();
        if (!matrix) return null;
        var pt = chartSvg.createSVGPoint();
        pt.x = e.clientX;
        pt.y = e.clientY;
        return pt.matrixTransform(matrix.inverse());
      }

      function activateNearestPoint(e) {
        var pt = svgPointFromEvent(e);
        if (!pt || pt.x < pad.left || pt.x > width - pad.right || pt.y < pad.top || pt.y > height - pad.bottom) {
          hideTip();
          return;
        }
        var nearest = null;
        var best = Infinity;
        hoverItems.forEach(function (item) {
          var dx = item.x - pt.x;
          var dy = item.y - pt.y;
          var score = dx * dx + dy * dy;
          if (score < best) {
            best = score;
            nearest = item;
          }
        });
        if (!nearest || best > 900) {
          hideTip();
          return;
        }
        hideTip();
        nearest.el.classList.add("is-active");
        showTip(nearest.team, nearest.point, e.clientX, e.clientY);
      }

      chartSvg.onpointermove = activateNearestPoint;
      chartSvg.onpointerleave = hideTip;

      chartSvg.querySelectorAll(".standings-trends__point").forEach(function (circle) {
        circle.addEventListener("pointerenter", function (e) {
          activatePoint(circle, e.clientX, e.clientY);
        });
        circle.addEventListener("pointermove", function (e) {
          if (!tip.hidden) showTip(circle._trendTeam, circle._trendPoint, e.clientX, e.clientY);
        });
        circle.addEventListener("pointerleave", hideTip);
        circle.addEventListener("focus", function () {
          var rect = circle.getBoundingClientRect();
          activatePoint(circle, rect.left + rect.width / 2, rect.top);
        });
        circle.addEventListener("blur", hideTip);
      });
    }

    renderChart();
  }

  function initAdvancedStatsDivisionTooltips() {
    var targets = document.querySelectorAll("[data-division-chart-team]");
    if (!targets.length) return;
    var tip = document.createElement("div");
    tip.className = "advanced-stats-division-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);

    function htmlFor(el) {
      var logo = el.getAttribute("data-division-chart-logo") || "";
      var team = el.getAttribute("data-division-chart-team") || "";
      var abbr = el.getAttribute("data-division-chart-abbr") || "";
      var val = el.getAttribute("data-division-chart-value") || "";
      var gp = el.getAttribute("data-division-chart-gp") || "";
      return (
        '<div class="advanced-stats-division-tooltip__inner">' +
        (logo
          ? '<img class="advanced-stats-division-tooltip__logo" src="' + escapeAttr(logo) + '" alt="">'
          : "") +
        '<div><strong>' +
        escapeHtml(abbr || team) +
        "</strong>" +
        (team && team !== abbr
          ? '<span class="advanced-stats-division-tooltip__name">' + escapeHtml(team) + "</span>"
          : "") +
        '<span class="advanced-stats-division-tooltip__value">' +
        escapeHtml(val) +
        " above PPG" +
        (gp ? " through " + escapeHtml(gp) + " GP" : "") +
        "</span></div></div>"
      );
    }

    function moveTip(e) {
      var x = e.clientX + 14;
      var y = e.clientY + 14;
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    }

    targets.forEach(function (el) {
      el.addEventListener("mouseenter", function (e) {
        tip.innerHTML = htmlFor(el);
        tip.hidden = false;
        el.classList.add("is-hovered");
        moveTip(e);
      });
      el.addEventListener("mousemove", moveTip);
      el.addEventListener("mouseleave", function () {
        tip.hidden = true;
        el.classList.remove("is-hovered");
      });
      el.addEventListener("focus", function () {
        tip.innerHTML = htmlFor(el);
        tip.hidden = false;
      });
      el.addEventListener("blur", function () {
        tip.hidden = true;
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table.data-sortable").forEach(initSortableTable);
    document.querySelectorAll("table[data-page-size]").forEach(initPaginatedTable);
    initTeamFinancesPanel();
    initTeamLineBuilder();
    initAdvancedStatsTeamChart();
    initTeamStatisticsChart();
    initTeamStatisticsFilters();
    initTeamStatisticsRowTooltips();
    initTeamPlayerAnalyticsCharts();
    initTeamPlayerTrendCharts();
    initTeamStatsTrendCharts();
    initStandingsTrendCharts();
    initAdvancedStatsDivisionTooltips();
    document.querySelectorAll("table[data-team-depth-prospects-page-size]").forEach(function (table) {
      var tbody = table.tBodies && table.tBodies[0];
      if (!tbody) return;
      var rows = Array.from(tbody.rows);
      var pageSize = parseInt(table.getAttribute("data-team-depth-prospects-page-size") || "10", 10);
      if (!pageSize || pageSize < 1 || rows.length <= pageSize) return;
      var card = table.closest(".team-depth-extra-card--prospects");
      if (!card) return;
      var pager = card.querySelector("[data-team-depth-prospects-pager]");
      var prev = card.querySelector("[data-team-depth-prospects-prev]");
      var next = card.querySelector("[data-team-depth-prospects-next]");
      var status = card.querySelector("[data-team-depth-prospects-status]");
      if (!pager || !prev || !next || !status) return;
      var page = 0;
      var totalPages = Math.ceil(rows.length / pageSize);
      function renderProspectPage() {
        rows.forEach(function (row, idx) {
          row.hidden = idx < page * pageSize || idx >= (page + 1) * pageSize;
        });
        status.textContent = "Page " + (page + 1) + " of " + totalPages;
        prev.disabled = page === 0;
        next.disabled = page >= totalPages - 1;
      }
      prev.addEventListener("click", function () {
        page = Math.max(0, page - 1);
        renderProspectPage();
      });
      next.addEventListener("click", function () {
        page = Math.min(totalPages - 1, page + 1);
        renderProspectPage();
      });
      pager.hidden = false;
      renderProspectPage();
    });
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

  function previewIconFlame() {
    return (
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" class="game-preview-icon game-preview-icon--flame" width="20" height="20" fill="none" aria-hidden="true" focusable="false">' +
      '<path stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.105-2.205 2.5-5 2.5-5 .5 2.5 2 4.9 2 8.5a6.5 6.5 0 1 1-13 0c0-4.36 2.11-6.64 4.5-10.5C9 9 8.5 14.5 8.5 14.5Z"/>' +
      "</svg>"
    );
  }

  function previewIconSnowflake() {
    return (
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" class="game-preview-icon game-preview-icon--snow" width="20" height="20" aria-hidden="true" focusable="false">' +
      '<path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M12 2v20M2 12h20M5 5l14 14M19 5L5 19"/>' +
      "</svg>"
    );
  }

  function previewPlayerTooltip(r) {
    if (!r) return "";
    var bits = ["Last ~10 GP for this club on the scoresheet"];
    if (r.gr != null) bits.push("avg GR " + r.gr);
    bits.push((r.g != null ? r.g : 0) + " G · " + (r.a != null ? r.a : 0) + " A · " + (r.p != null ? r.p : 0) + " P (window totals)");
    if (r.plus_minus != null) bits.push("avg +/− " + r.plus_minus);
    if (r.toi) bits.push("avg TOI " + r.toi);
    if (r.pos) bits.push(r.pos);
    return bits.join(" · ");
  }

  function previewPlayerLink(id, name, opts) {
    if (opts && typeof opts === "string") opts = { title: opts };
    opts = opts || {};
    var extra = "";
    if (opts.title) extra += ' title="' + escapeAttr(opts.title) + '"';
    if (opts.ariaLabel) extra += ' aria-label="' + escapeAttr(opts.ariaLabel) + '"';
    if (id == null) return escapeHtml(name || "—");
    return (
      '<a href="' +
      escapeAttr(withRoot("/player/" + encodeURIComponent(String(id)))) +
      '"' +
      extra +
      ' class="game-preview-player-link">' +
      escapeHtml(name || "") +
      "</a>"
    );
  }

  function previewPlayerCell(r) {
    var tip = previewPlayerTooltip(r);
    var imgHtml = "";
    if (r.photo_url) {
      imgHtml =
        '<img class="game-preview-player-cell__img" src="' +
        escapeAttr(r.photo_url) +
        '" alt="" width="32" height="32" loading="lazy">';
    } else {
      imgHtml =
        '<span class="game-preview-player-cell__img game-preview-player-cell__img--ph" aria-hidden="true"></span>';
    }
    return (
      '<span class="game-preview-player-cell">' +
      imgHtml +
      '<span class="game-preview-player-cell__meta">' +
      '<span class="game-preview-player-cell__name">' +
      previewPlayerLink(r.player_id, r.name, { ariaLabel: tip }) +
      "</span>" +
      '<span class="game-preview-pos muted">' +
      escapeHtml(r.pos || "") +
      "</span></span></span>"
    );
  }

  function previewBadge(label, value) {
    if (value == null || value === "") return "";
    return (
      '<span class="game-preview-badge"><span class="game-preview-badge__k">' +
      escapeHtml(label) +
      '</span><span class="game-preview-badge__v">' +
      value +
      "</span></span>"
    );
  }

  function previewSkaterMiniTable(rows) {
    var h =
      '<table class="game-preview-mini-table"><thead><tr><th>Player</th><th>GR</th><th>G</th><th>A</th><th>P</th><th>+/-</th><th>TOI</th></tr></thead><tbody>';
    if (!rows || !rows.length) {
      h += '<tr><td colspan="7" class="game-preview-empty">Not enough recent games.</td></tr>';
    } else {
      rows.forEach(function (r) {
        h +=
          "<tr><td>" +
          previewPlayerCell(r) +
          "</td><td>" +
          (r.gr != null ? escapeHtml(String(r.gr)) : "—") +
          "</td><td>" +
          (r.g != null ? r.g : "—") +
          "</td><td>" +
          (r.a != null ? r.a : "—") +
          "</td><td>" +
          (r.p != null ? r.p : "—") +
          "</td><td>" +
          (r.plus_minus != null ? r.plus_minus : "—") +
          "</td><td>" +
          escapeHtml(r.toi || "—") +
          "</td></tr>";
      });
    }
    h += "</tbody></table>";
    return h;
  }

  function renderTeamPreviewCard(side) {
    if (!side || !side.team) return "";
    var tm = side.team;
    var rec = side.record;
    var recStr = rec
      ? escapeHtml(String(rec.pts)) + " Pts · " + escapeHtml(rec.str)
      : "—";
    var pp =
      side.pp_pct != null
        ? escapeHtml(String(side.pp_pct)) +
          "%" +
          (side.pp_rank != null ? " (" + escapeHtml(String(side.pp_rank)) + ")" : "")
        : "—";
    var pk =
      side.pk_pct != null
        ? escapeHtml(String(side.pk_pct)) +
          "%" +
          (side.pk_rank != null ? " (" + escapeHtml(String(side.pk_rank)) + ")" : "")
        : "—";
    var l10 = side.last_10 || {};
    var l10Body = "";
    if (l10.str != null) {
      l10Body = escapeHtml(l10.str);
      if (l10.w > l10.l) {
        l10Body +=
          '<span class="game-preview-l10-trend" title="Winning record in last 10">' +
          previewIconFlame() +
          "</span>";
      } else if (l10.l > l10.w) {
        l10Body +=
          '<span class="game-preview-l10-trend" title="Losing record in last 10">' +
          previewIconSnowflake() +
          "</span>";
      }
    } else {
      l10Body = "—";
    }
    var html = '<div class="game-preview-team-card card">';
    html += '<div class="game-preview-team-card__head">';
    html += teamLogoCell(tm.logo_url, tm.slug, tm.abbreviation);
    html +=
      '<div class="game-preview-team-card__titles"><h3 class="game-preview-team-name">' +
      escapeHtml(tm.display_name || tm.name || tm.abbreviation || "") +
      "</h3>";
    html +=
      '<p class="muted game-preview-vs">vs ' +
      escapeHtml((side.opponent && side.opponent.abbreviation) || "") +
      "</p></div></div>";
    html += '<div class="game-preview-badges">';
    html += previewBadge("Record", recStr);
    html += previewBadge("Standing", side.standing_line ? escapeHtml(side.standing_line) : null);
    html += previewBadge("PP", pp);
    html += previewBadge("PK", pk);
    html += previewBadge("Last 10", l10Body);
    if (side.streak) html += previewBadge("Streak", escapeHtml(side.streak));
    var sh = side.season_h2h;
    if (sh) {
      var shVal;
      if (sh.gp > 0 && sh.str) {
        shVal =
          escapeHtml(sh.str) +
          " <span class=\"game-preview-h2h-meta\">(" +
          escapeHtml(String(sh.gp)) +
          " GP) · vs " +
          escapeHtml(sh.opponent_abbr || "") +
          "</span>";
      } else {
        shVal =
          '<span class="game-preview-h2h-meta">No games yet vs ' +
          escapeHtml(sh.opponent_abbr || "") +
          "</span>";
      }
      html += previewBadge("RS H2H", shVal);
    }
    html += "</div>";
    html += '<div class="game-preview-trends">';
    html +=
      '<div class="game-preview-trend-col"><h4 class="game-preview-subhead game-preview-subhead--hot">' +
      '<span class="game-preview-subhead__icon" aria-hidden="true">' +
      previewIconFlame() +
      '</span><span class="game-preview-subhead__text">Hot</span><span class="game-preview-subhead__suffix">(last 10)</span></h4>';
    html += previewSkaterMiniTable(side.hot);
    html +=
      '</div><div class="game-preview-trend-col"><h4 class="game-preview-subhead game-preview-subhead--cold">' +
      '<span class="game-preview-subhead__icon" aria-hidden="true">' +
      previewIconSnowflake() +
      '</span><span class="game-preview-subhead__text">Cold</span><span class="game-preview-subhead__suffix">(last 10)</span></h4>';
    html += previewSkaterMiniTable(side.cold);
    html += "</div></div>";
    html += '<div class="game-preview-starter"><h4 class="game-preview-subhead game-preview-subhead--block">Projected starter</h4>';
    if (side.projected_starter) {
      var g = side.projected_starter;
      var gTip =
        "Season line (regular season): " +
        (g.record || "—") +
        (g.gaa != null ? " · GAA " + g.gaa : "") +
        (g.sv_pct != null ? " · Sv% " + g.sv_pct : "");
      var gImg = "";
      if (g.photo_url) {
        gImg =
          '<img class="game-preview-starter__img" src="' +
          escapeAttr(g.photo_url) +
          '" alt="" width="40" height="40" loading="lazy">';
      } else {
        gImg =
          '<span class="game-preview-starter__img game-preview-starter__img--ph" aria-hidden="true"></span>';
      }
      html += '<p class="game-preview-starter__line">';
      html += gImg;
      html += '<span class="game-preview-starter__text">';
      html += previewPlayerLink(g.player_id, g.name, { ariaLabel: gTip });
      html += ' <span class="muted">G</span> — ';
      html += escapeHtml(g.record || "—");
      if (g.gaa != null) html += " · GAA " + escapeHtml(String(g.gaa));
      if (g.sv_pct != null) html += " · Sv% " + escapeHtml(String(g.sv_pct));
      html += "</span></p>";
    } else {
      html += '<p class="game-preview-empty">No season goalie stats for this club.</p>';
    }
    html += "</div></div>";
    return html;
  }

  function previewOddsParseHex(s) {
    if (!s || typeof s !== "string") return null;
    var m = String(s).trim().match(/^#([0-9a-fA-F]{6})$/);
    if (!m) return null;
    var h = m[1];
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
    };
  }

  function previewOddsLuminance(rgb) {
    var a = [rgb.r, rgb.g, rgb.b].map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }

  function previewOddsMixRgb(a, b, t) {
    return {
      r: Math.round(a.r + (b.r - a.r) * t),
      g: Math.round(a.g + (b.g - a.g) * t),
      b: Math.round(a.b + (b.b - a.b) * t),
    };
  }

  function previewOddsRgbToHex(rgb) {
    function hx(n) {
      var s = Math.max(0, Math.min(255, n)).toString(16);
      return s.length === 1 ? "0" + s : s;
    }
    return "#" + hx(rgb.r) + hx(rgb.g) + hx(rgb.b);
  }

  /** Keep bar fills readable on the dark bar tray and the light bar tray. */
  function previewOddsClampForBarTray(rgb) {
    var isLight = document.documentElement.getAttribute("data-theme") === "light";
    var lo = isLight ? 0 : 0.18;
    var hi = isLight ? 0.5 : 0.95;
    var cur = previewOddsLuminance(rgb);
    var out = rgb;
    var i;
    if (cur < lo) {
      var tw = 0;
      for (i = 0; i < 22; i += 1) {
        tw += 0.06;
        out = previewOddsMixRgb(rgb, { r: 255, g: 255, b: 255 }, Math.min(1, tw));
        if (previewOddsLuminance(out) >= lo) break;
      }
    } else if (cur > hi) {
      var tb = 0;
      for (i = 0; i < 22; i += 1) {
        tb += 0.06;
        out = previewOddsMixRgb(rgb, { r: 8, g: 10, b: 14 }, Math.min(1, tb));
        if (previewOddsLuminance(out) <= hi) break;
      }
    }
    return out;
  }

  function previewOddsColorDistance(a, b) {
    if (!a || !b) return 999;
    return Math.sqrt(
      Math.pow(a.r - b.r, 2) + Math.pow(a.g - b.g, 2) + Math.pow(a.b - b.b, 2)
    );
  }

  function previewOddsWinProbSegStyles(awayTeam, homeTeam) {
    var isLight = document.documentElement.getAttribute("data-theme") === "light";
    var shade = isLight ? { r: 12, g: 16, b: 24 } : { r: 15, g: 23, b: 42 };
    function baseRgb(team) {
      if (!team) return null;
      return (
        previewOddsParseHex(team.primary_color) ||
        previewOddsParseHex(team.secondary_color)
      );
    }
    function segGradient(rgb) {
      if (!rgb) return "";
      var c1 = previewOddsRgbToHex(rgb);
      var c2 = previewOddsRgbToHex(previewOddsMixRgb(rgb, shade, 0.28));
      return "background:linear-gradient(90deg," + c1 + "," + c2 + ")";
    }
    var ar = baseRgb(awayTeam);
    var hr = baseRgb(homeTeam);
    if (!ar && !hr) {
      return { away: "", home: "" };
    }
    if (!ar) ar = previewOddsMixRgb(hr, shade, 0.35);
    if (!hr) hr = previewOddsMixRgb(ar, shade, 0.35);
    ar = previewOddsClampForBarTray(ar);
    hr = previewOddsClampForBarTray(hr);
    if (previewOddsColorDistance(ar, hr) < 52) {
      var altH = homeTeam && previewOddsParseHex(homeTeam.secondary_color);
      var altA = awayTeam && previewOddsParseHex(awayTeam.secondary_color);
      if (altH && previewOddsColorDistance(ar, altH) >= 52) {
        hr = previewOddsClampForBarTray(altH);
      } else if (altA && previewOddsColorDistance(altA, hr) >= 52) {
        ar = previewOddsClampForBarTray(altA);
      } else {
        hr = previewOddsMixRgb(hr, isLight ? { r: 18, g: 22, b: 32 } : { r: 230, g: 236, b: 252 }, 0.18);
        hr = previewOddsClampForBarTray(hr);
      }
    }
    return { away: segGradient(ar), home: segGradient(hr) };
  }

  function previewOddsSegInlineStyle(widthPct, bgCss) {
    var w = typeof widthPct === "number" && !isNaN(widthPct) ? widthPct : 50;
    if (bgCss) return "width:" + w + "%;" + bgCss;
    return "width:" + w + "%";
  }

  /** Recompute win-probability segment fills after ``data-theme`` changes (colors depend on light vs dark tray). */
  function refreshGamePreviewOddsSegStyles() {
    document.querySelectorAll(".game-preview-odds__bar").forEach(function (bar) {
      var ds = bar.dataset || {};
      var awayTeam = {
        primary_color: ds.awayPrimary || "",
        secondary_color: ds.awaySecondary || "",
      };
      var homeTeam = {
        primary_color: ds.homePrimary || "",
        secondary_color: ds.homeSecondary || "",
      };
      if (
        !awayTeam.primary_color &&
        !awayTeam.secondary_color &&
        !homeTeam.primary_color &&
        !homeTeam.secondary_color
      ) {
        return;
      }
      var segAway = bar.querySelector(".game-preview-odds__seg--away");
      var segHome = bar.querySelector(".game-preview-odds__seg--home");
      if (!segAway || !segHome) return;
      var ap = parseFloat(segAway.style.width);
      var hp = parseFloat(segHome.style.width);
      if (!isFinite(ap)) {
        var m = (segAway.getAttribute("style") || "").match(/width:\s*([\d.]+)\s*%/i);
        ap = m ? parseFloat(m[1]) : 50;
      }
      if (!isFinite(hp)) {
        var m2 = (segHome.getAttribute("style") || "").match(/width:\s*([\d.]+)\s*%/i);
        hp = m2 ? parseFloat(m2[1]) : 50;
      }
      var segStyles = previewOddsWinProbSegStyles(awayTeam, homeTeam);
      segAway.setAttribute("style", previewOddsSegInlineStyle(ap, segStyles.away));
      segHome.setAttribute("style", previewOddsSegInlineStyle(hp, segStyles.home));
    });
  }

  function renderGamePreviewHtml(d) {
    var away = d.away || {};
    var home = d.home || {};
    var odds = d.odds || {};
    var hp = odds.home_pct_display != null ? Number(odds.home_pct_display) : 50;
    var ap = odds.away_pct_display != null ? Number(odds.away_pct_display) : 50;
    var segStyles = previewOddsWinProbSegStyles(away.team, home.team);
    var html = '<div class="game-preview-panel">';
    html += '<p class="game-preview-lede muted">' + escapeHtml(d.prediction_method_note || "") + "</p>";

    var meetingsHtml = "";
    if (d.recent_meetings && d.recent_meetings.length) {
      meetingsHtml +=
        '<div class="game-preview-meetings card"><h3 class="game-preview-subhead game-preview-subhead--block">Last meetings (this season)</h3><ul class="game-preview-meetings__list">';
      d.recent_meetings.forEach(function (m) {
        var line =
          escapeHtml(m.away_abbr || "") +
          " " +
          (m.away_score != null ? m.away_score : "—") +
          " – " +
          (m.home_score != null ? m.home_score : "—") +
          " " +
          escapeHtml(m.home_abbr || "");
        if (m.extra) line += " (" + escapeHtml(m.extra) + ")";
        meetingsHtml +=
          '<li><span class="game-preview-meetings__date muted">' +
          escapeHtml(m.date || "") +
          '</span> <span class="game-preview-meetings__score">' +
          line +
          '</span> <a href="' +
          escapeAttr(withRoot("/game/" + encodeURIComponent(String(m.game_id)))) +
          '">Box score</a></li>';
      });
      meetingsHtml += "</ul></div>";
    }

    var oddsHtml = "";
    oddsHtml += '<div class="game-preview-odds card">';
    oddsHtml += '<h3 class="game-preview-subhead game-preview-subhead--block game-preview-odds__title">Win probability</h3>';
    var at = away.team || {};
    var ht = home.team || {};
    var barAttrs = 'title="' + escapeAttr(odds.method_note || "") + '"';
    if (at.primary_color) barAttrs += ' data-away-primary="' + escapeAttr(at.primary_color) + '"';
    if (at.secondary_color) barAttrs += ' data-away-secondary="' + escapeAttr(at.secondary_color) + '"';
    if (ht.primary_color) barAttrs += ' data-home-primary="' + escapeAttr(ht.primary_color) + '"';
    if (ht.secondary_color) barAttrs += ' data-home-secondary="' + escapeAttr(ht.secondary_color) + '"';
    oddsHtml += '<div class="game-preview-odds__bar" ' + barAttrs + ">";
    oddsHtml +=
      '<div class="game-preview-odds__seg game-preview-odds__seg--away" style="' +
      escapeAttr(previewOddsSegInlineStyle(ap, segStyles.away)) +
      '"></div>';
    oddsHtml +=
      '<div class="game-preview-odds__seg game-preview-odds__seg--home" style="' +
      escapeAttr(previewOddsSegInlineStyle(hp, segStyles.home)) +
      '"></div>';
    oddsHtml += "</div>";
    oddsHtml += '<div class="game-preview-odds__labels">';
    oddsHtml +=
      '<span class="game-preview-odds__side">' +
      teamLogoCell(away.team && away.team.logo_url, away.team && away.team.slug, away.team && away.team.abbreviation) +
      " <strong>" +
      ap +
      "%</strong> " +
      escapeHtml((away.team && away.team.abbreviation) || "") +
      "</span>";
    oddsHtml +=
      '<span class="game-preview-odds__side game-preview-odds__side--home">' +
      "<strong>" +
      hp +
      "%</strong> " +
      escapeHtml((home.team && home.team.abbreviation) || "") +
      " " +
      teamLogoCell(home.team && home.team.logo_url, home.team && home.team.slug, home.team && home.team.abbreviation) +
      "</span>";
    oddsHtml += "</div></div>";

    var heroMod = meetingsHtml ? "" : " game-preview-hero-row--odds-only";
    html += '<div class="game-preview-hero-row' + heroMod + '">';
    html += meetingsHtml;
    html += oddsHtml;
    html += "</div>";

    html += '<div class="game-preview-team-grid">';
    html += renderTeamPreviewCard(away);
    html += renderTeamPreviewCard(home);
    html += "</div>";
    if (d.injuries_note) {
      html +=
        '<p class="game-preview-foot muted">' + escapeHtml(d.injuries_note) + "</p>";
    }
    html += "</div>";
    return html;
  }

  var BOXSCORE_SQ_BUCKETS = [
    {
      key: "sq0",
      label: "SQ0",
      danger: "Lowest",
      title: "Shot quality bucket 0: lowest-danger attempts in the imported FHM shot-quality mix.",
    },
    {
      key: "sq1",
      label: "SQ1",
      danger: "Low",
      title: "Shot quality bucket 1: low-danger attempts in the imported FHM shot-quality mix.",
    },
    {
      key: "sq2",
      label: "SQ2",
      danger: "Medium",
      title: "Shot quality bucket 2: medium-danger attempts in the imported FHM shot-quality mix.",
    },
    {
      key: "sq3",
      label: "SQ3",
      danger: "High",
      title: "Shot quality bucket 3: high-danger attempts in the imported FHM shot-quality mix.",
    },
    {
      key: "sq4",
      label: "SQ4",
      danger: "Highest",
      title: "Shot quality bucket 4: highest-danger attempts in the imported FHM shot-quality mix.",
    },
  ];
  var BOXSCORE_SQ_SORT_LABELS = {
    total: "total shots",
    sq_avg: "shot-quality average",
    hd: "high-danger share",
    sq4: "highest-danger (SQ4) shots",
  };

  function boxscorePeriodHeaderLabel(label) {
    var lab = String(label == null ? "" : label);
    return lab === "OT" ? "OT" : "P" + escapeHtml(lab);
  }

  function boxscorePeriodTableHtml(cols, away, home, opts) {
    opts = opts || {};
    var html =
      '<div class="table-wrap boxscore-table-wrap"><table class="boxscore-table boxscore-table--period"><thead><tr><th>Team</th>';
    (cols || []).forEach(function (col) {
      html += "<th>" + boxscorePeriodHeaderLabel(col.label) + "</th>";
    });
    if (opts.totalKey) html += "<th>T</th>";
    html += "</tr></thead><tbody>";
    function teamRow(side, score) {
      var row =
        "<tr><td><span class=\"boxscore-team-cell\">" +
        teamLogoCell(side.logo_url, side.slug, side.abbr) +
        '<span class="boxscore-team-cell__name">' +
        escapeHtml(side.name || side.abbr || "") +
        "</span></span></td>";
      (cols || []).forEach(function (col) {
        var v = side === away ? col.away : col.home;
        row += "<td>" + (v != null ? v : "—") + "</td>";
      });
      if (opts.totalKey) {
        row += "<td><strong>" + (score != null ? score : "—") + "</strong></td>";
      }
      return row + "</tr>";
    }
    html += teamRow(away, away.score);
    html += teamRow(home, home.score);
    html += "</tbody></table></div>";
    return html;
  }

  function boxscoreSqEmptyCounts() {
    return { sq0: 0, sq1: 0, sq2: 0, sq3: 0, sq4: 0 };
  }

  function boxscoreSqNormalizeCounts(row) {
    var counts = boxscoreSqEmptyCounts();
    if (!row) return counts;
    var src = row.counts && typeof row.counts === "object" ? row.counts : row;
    BOXSCORE_SQ_BUCKETS.forEach(function (b) {
      var v = src[b.key];
      if (v == null) v = src[b.label];
      counts[b.key] = Number(v) || 0;
    });
    return counts;
  }

  function boxscoreSqCountTotal(counts) {
    counts = counts || boxscoreSqEmptyCounts();
    return counts.sq0 + counts.sq1 + counts.sq2 + counts.sq3 + counts.sq4;
  }

  function boxscoreSqProfileFromCounts(row) {
    var counts = boxscoreSqNormalizeCounts(row);
    var total = boxscoreSqCountTotal(counts);
    if (!total) {
      return { total: 0, shares: {}, sq_avg: null, high_danger_share: null, counts: counts };
    }
    var weighted = counts.sq1 + 2 * counts.sq2 + 3 * counts.sq3 + 4 * counts.sq4;
    var hd = counts.sq3 + counts.sq4;
    return {
      total: total,
      counts: counts,
      shares: {
        SQ0: Math.round((1000 * counts.sq0) / total) / 10,
        SQ1: Math.round((1000 * counts.sq1) / total) / 10,
        SQ2: Math.round((1000 * counts.sq2) / total) / 10,
        SQ3: Math.round((1000 * counts.sq3) / total) / 10,
        SQ4: Math.round((1000 * counts.sq4) / total) / 10,
      },
      sq_avg: Math.round((weighted / total) * 100) / 100,
      high_danger_share: Math.round((1000 * hd) / total) / 10,
    };
  }

  function boxscoreSqVisibleTotal(counts, enabled) {
    var t = 0;
    BOXSCORE_SQ_BUCKETS.forEach(function (b) {
      if (!enabled || enabled[b.key]) t += (counts && counts[b.key]) || 0;
    });
    return t;
  }

  function boxscoreSqNiceMax(n) {
    var pad = Math.max(1, Math.ceil(Number(n) || 0));
    if (pad <= 5) return 5;
    if (pad <= 10) return 10;
    return Math.ceil(pad / 10) * 10;
  }

  function boxscoreSqAllEnabled() {
    var enabled = {};
    BOXSCORE_SQ_BUCKETS.forEach(function (b) {
      enabled[b.key] = true;
    });
    return enabled;
  }

  function boxscoreSqEnabledList(enabled) {
    return BOXSCORE_SQ_BUCKETS.filter(function (b) {
      return !enabled || enabled[b.key];
    });
  }

  function boxscoreSqFmtPct(n) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toFixed(1) + "%";
  }

  function boxscoreSqFmtAvg(n) {
    if (n == null || isNaN(n)) return "—";
    return Number(n).toFixed(2);
  }

  function boxscoreSqPlayerPayload(s) {
    return {
      player_id: s.player_id,
      player: s.player,
      counts: boxscoreSqNormalizeCounts(s),
    };
  }

  function boxscoreMetaLineHtml(d) {
    var bits = [];
    if (d.date) bits.push(escapeHtml(String(d.date)));
    if (d.arena) bits.push(escapeHtml(String(d.arena)));
    if (d.attendance != null) {
      try {
        bits.push(Number(d.attendance).toLocaleString() + " att.");
      } catch (e) {
        bits.push(String(d.attendance) + " att.");
      }
    }
    if (d.game_type) bits.push(escapeHtml(String(d.game_type)));
    if (!bits.length) return "";
    return '<p class="boxscore-panel__meta">' + bits.join(" · ") + "</p>";
  }

  function boxscoreSummaryHtml(d, away, home) {
    var st = d.special_teams || {};
    var html = "";
    html += boxscoreMetaLineHtml(d);
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

    html += boxscorePeriodTableHtml(d.period_columns || [], away, home, { totalKey: "score" });

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
    return html;
  }

  function boxscoreStatsHtml(d, away, home) {
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

    var html = '<div class="boxscore-split">';
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
    return html;
  }

  function boxscoreShotQualityHtml(d, away, home) {
    var skaters = d.skaters || [];
    var awayAbbr = away.abbr || "";
    var homeAbbr = home.abbr || "";
    var payload = {
      away: {
        abbr: away.abbr || "",
        name: away.name || away.abbr || "",
        slug: away.slug || "",
        logo_url: away.logo_url || "",
        counts: boxscoreSqNormalizeCounts(d.sq_away),
      },
      home: {
        abbr: home.abbr || "",
        name: home.name || home.abbr || "",
        slug: home.slug || "",
        logo_url: home.logo_url || "",
        counts: boxscoreSqNormalizeCounts(d.sq_home),
      },
      awayPlayers: skaters
        .filter(function (x) {
          return x.team_abbr === awayAbbr;
        })
        .map(boxscoreSqPlayerPayload),
      homePlayers: skaters
        .filter(function (x) {
          return x.team_abbr === homeAbbr;
        })
        .map(boxscoreSqPlayerPayload),
    };
    var hasTeam = boxscoreSqCountTotal(payload.away.counts) + boxscoreSqCountTotal(payload.home.counts) > 0;
    var hasPlayers = payload.awayPlayers.concat(payload.homePlayers).some(function (p) {
      return boxscoreSqCountTotal(p.counts) > 0;
    });

    var html = '<div class="boxscore-sq-dash" data-boxscore-sq-root data-sq-json="' + escapeAttr(JSON.stringify(payload)) + '">';
    html +=
      '<p class="boxscore-sq-note">Imported FHM mix from lowest-danger (SQ0) to highest-danger (SQ4) attempts — not expected goals. Click a color to hide or isolate it; hover a bar for exact counts.</p>';

    if (!hasTeam && !hasPlayers) {
      html += '<p class="boxscore-empty">No shot-quality data imported for this game.</p></div>';
      return html;
    }

    html += '<div class="boxscore-sq-toolbar">';
    html += '<div class="boxscore-sq-legend" role="group" aria-label="Shot quality buckets">';
    BOXSCORE_SQ_BUCKETS.forEach(function (b) {
      html +=
        '<button type="button" class="boxscore-sq-legend__btn is-on" data-sq-legend="' +
        b.key +
        '" aria-pressed="true" title="' +
        escapeAttr(b.title) +
        '"><span class="boxscore-sq-swatch boxscore-sq-swatch--' +
        b.key +
        '" aria-hidden="true"></span><span class="boxscore-sq-legend__text"><span class="boxscore-sq-legend__code">' +
        b.label +
        '</span><span class="boxscore-sq-legend__danger">' +
        escapeHtml(b.danger) +
        "</span></span></button>";
    });
    html +=
      '<button type="button" class="boxscore-sq-legend__preset" data-sq-preset="hd" title="Show only SQ3 and SQ4">High-danger</button>';
    html +=
      '<button type="button" class="boxscore-sq-legend__preset" data-sq-preset="all" hidden>Show all</button>';
    html += "</div>";
    html += '<div class="boxscore-sq-controls">';
    html +=
      '<div class="boxscore-sq-seg" role="group" aria-label="Bar mode">' +
      '<button type="button" class="boxscore-sq-seg__btn is-active" data-sq-mode="volume">Volume</button>' +
      '<button type="button" class="boxscore-sq-seg__btn" data-sq-mode="mix">Mix %</button></div>';
    html +=
      '<div class="boxscore-sq-seg" role="group" aria-label="Player bar scale">' +
      '<button type="button" class="boxscore-sq-seg__btn is-active" data-sq-scale="shared">Shared scale</button>' +
      '<button type="button" class="boxscore-sq-seg__btn" data-sq-scale="team">Fit each team</button></div>';
    html +=
      '<label class="boxscore-sq-sort"><span>Sort players</span><select data-sq-sort>' +
      '<option value="total" selected>Total shots</option>' +
      '<option value="sq_avg">SQ average</option>' +
      '<option value="hd">High-danger %</option>' +
      '<option value="sq4">SQ4 shots</option></select></label>';
    html += "</div></div>";

    html += '<p class="boxscore-sq-insight" data-sq-insight></p>';

    html += '<section class="boxscore-sq-section">';
    html +=
      '<div class="boxscore-sq-section__head"><h3 class="boxscore-section-title">Team shot quality</h3>' +
      '<p class="boxscore-sq-section__sub" data-sq-team-sub>Volume by quality</p></div>';
    html += '<div class="boxscore-sq-team" data-sq-team-chart></div></section>';

    html += '<section class="boxscore-sq-section">';
    html +=
      '<div class="boxscore-sq-section__head"><h3 class="boxscore-section-title">Player shot quality</h3>' +
      '<p class="boxscore-sq-section__sub" data-sq-player-sub>Sorted by total shots</p></div>';
    html += '<div class="boxscore-sq-players" data-sq-player-charts></div></section>';

    var sogCols = d.sog_period_columns || [];
    var hasSog = sogCols.some(function (col) {
      return col.away != null || col.home != null;
    });
    if (hasSog) {
      html += '<details class="boxscore-sq-periods"><summary>Shots by period</summary>';
      html += boxscorePeriodTableHtml(sogCols, away, home, {});
      html += "</details>";
    }
    html += "</div>";
    return html;
  }

  function bindBoxScoreShotQuality(container) {
    var root = container && container.querySelector("[data-boxscore-sq-root]");
    if (!root || root.getAttribute("data-sq-bound") === "1") return;
    var raw = root.getAttribute("data-sq-json");
    if (!raw) return;
    var data;
    try {
      data = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!root.querySelector(".boxscore-sq-toolbar")) return;
    root.setAttribute("data-sq-bound", "1");
    root.removeAttribute("data-sq-json");

    var state = {
      mode: "volume",
      scale: "shared",
      sort: "total",
      enabled: boxscoreSqAllEnabled(),
      expanded: {},
    };
    var teamMount = root.querySelector("[data-sq-team-chart]");
    var playerMount = root.querySelector("[data-sq-player-charts]");
    var insightEl = root.querySelector("[data-sq-insight]");
    var teamSub = root.querySelector("[data-sq-team-sub]");
    var playerSub = root.querySelector("[data-sq-player-sub]");
    var showAllBtn = root.querySelector('[data-sq-preset="all"]');

    var tip = document.getElementById("boxscore-sq-tooltip");
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "boxscore-sq-tooltip";
      tip.className = "boxscore-sq-tooltip";
      tip.setAttribute("role", "tooltip");
      tip.hidden = true;
      document.body.appendChild(tip);
    }

    function enabledCount() {
      return boxscoreSqEnabledList(state.enabled).length;
    }

    function hideTip(force) {
      if (tip.getAttribute("data-pinned") && !force) return;
      tip.removeAttribute("data-pinned");
      tip.hidden = true;
    }

    function placeTip(clientX, clientY) {
      var pad = 14;
      var x = clientX + pad;
      var y = clientY + pad;
      tip.hidden = false;
      var rect = tip.getBoundingClientRect();
      if (x + rect.width > window.innerWidth - 8) x = clientX - rect.width - pad;
      if (y + rect.height > window.innerHeight - 8) y = clientY - rect.height - pad;
      tip.style.left = Math.max(8, x) + "px";
      tip.style.top = Math.max(8, y) + "px";
    }

    function showTip(html, clientX, clientY, pinKey) {
      tip.innerHTML = html;
      if (pinKey) tip.setAttribute("data-pinned", pinKey);
      else tip.removeAttribute("data-pinned");
      placeTip(clientX, clientY);
    }

    function tipFromEl(el, ev, pin) {
      var bucketKey = el.getAttribute("data-sq-tip-bucket");
      var bucket = BOXSCORE_SQ_BUCKETS.filter(function (b) {
        return b.key === bucketKey;
      })[0];
      if (!bucket) return;
      var title = el.getAttribute("data-sq-tip-side") || "";
      var count = parseInt(el.getAttribute("data-sq-tip-count") || "0", 10);
      var total = parseInt(el.getAttribute("data-sq-tip-total") || "0", 10);
      var key = title + ":" + bucket.key;
      if (pin && tip.getAttribute("data-pinned") === key) {
        hideTip(true);
        return;
      }
      showTip(bucketTipHtml(title, bucket, count, total), ev.clientX, ev.clientY, pin ? key : null);
    }

    function bucketTipHtml(title, bucket, count, total) {
      var share = total ? Math.round((1000 * count) / total) / 10 : 0;
      return (
        '<div class="boxscore-sq-tooltip__inner">' +
        '<span class="boxscore-sq-swatch boxscore-sq-swatch--' +
        bucket.key +
        '" aria-hidden="true"></span>' +
        "<div><strong>" +
        escapeHtml(title) +
        "</strong>" +
        '<div class="boxscore-sq-tooltip__line">' +
        bucket.label +
        " · " +
        escapeHtml(bucket.danger) +
        " danger</div>" +
        '<div class="boxscore-sq-tooltip__line"><strong>' +
        count +
        "</strong> attempt" +
        (count === 1 ? "" : "s") +
        (total ? " · " + share + "% of this bar" : "") +
        "</div></div></div>"
      );
    }

    function sortPlayers(list) {
      var rows = list
        .map(function (p) {
          var sq = boxscoreSqProfileFromCounts(p.counts);
          return { p: p, sq: sq, vis: boxscoreSqVisibleTotal(p.counts, state.enabled) };
        })
        .filter(function (x) {
          return x.vis > 0;
        });
      rows.sort(function (a, b) {
        var av;
        var bv;
        if (state.sort === "sq_avg") {
          av = a.sq.sq_avg || 0;
          bv = b.sq.sq_avg || 0;
        } else if (state.sort === "hd") {
          av = a.sq.high_danger_share || 0;
          bv = b.sq.high_danger_share || 0;
        } else if (state.sort === "sq4") {
          av = a.p.counts.sq4 || 0;
          bv = b.p.counts.sq4 || 0;
        } else {
          av = a.vis;
          bv = b.vis;
        }
        if (bv !== av) return bv - av;
        if (b.sq.sq_avg !== a.sq.sq_avg) return (b.sq.sq_avg || 0) - (a.sq.sq_avg || 0);
        return String(a.p.player || "").localeCompare(String(b.p.player || ""));
      });
      return rows;
    }

    function insightText() {
      var aC = data.away.counts;
      var hC = data.home.counts;
      var aVis = boxscoreSqVisibleTotal(aC, state.enabled);
      var hVis = boxscoreSqVisibleTotal(hC, state.enabled);
      var aSq = boxscoreSqProfileFromCounts(aC);
      var hSq = boxscoreSqProfileFromCounts(hC);
      var aName = data.away.abbr || "Away";
      var hName = data.home.abbr || "Home";
      if (!aVis && !hVis) return "No attempts in the selected shot-quality buckets.";
      var filtered = enabledCount() < BOXSCORE_SQ_BUCKETS.length;
      if (filtered) {
        var labels = boxscoreSqEnabledList(state.enabled)
          .map(function (b) {
            return b.label;
          })
          .join(" + ");
        if (aVis === hVis) {
          return "Showing " + labels + " only — both teams had " + aVis + " attempts in this mix.";
        }
        var lead = aVis > hVis ? aName : hName;
        var trail = aVis > hVis ? hName : aName;
        var lv = Math.max(aVis, hVis);
        var tv = Math.min(aVis, hVis);
        return (
          "Showing " +
          labels +
          " only — " +
          lead +
          " had " +
          lv +
          " attempts vs " +
          trail +
          " " +
          tv +
          "."
        );
      }
      var bits = [];
      if (aSq.sq_avg != null && hSq.sq_avg != null && aSq.sq_avg !== hSq.sq_avg) {
        var better = aSq.sq_avg > hSq.sq_avg ? aName : hName;
        var worse = aSq.sq_avg > hSq.sq_avg ? hName : aName;
        var bAvg = Math.max(aSq.sq_avg, hSq.sq_avg);
        var wAvg = Math.min(aSq.sq_avg, hSq.sq_avg);
        bits.push(
          better +
            " generated the higher-quality looks (SQ avg " +
            boxscoreSqFmtAvg(bAvg) +
            " vs " +
            boxscoreSqFmtAvg(wAvg) +
            ")"
        );
      } else if (aSq.sq_avg != null && hSq.sq_avg != null) {
        bits.push("Shot quality was even (SQ avg " + boxscoreSqFmtAvg(aSq.sq_avg) + ")");
      }
      if (aSq.high_danger_share != null && hSq.high_danger_share != null) {
        if (aSq.high_danger_share !== hSq.high_danger_share) {
          var hdLead = aSq.high_danger_share > hSq.high_danger_share ? aName : hName;
          bits.push(
            hdLead +
              " had the bigger high-danger share (" +
              boxscoreSqFmtPct(Math.max(aSq.high_danger_share, hSq.high_danger_share)) +
              " vs " +
              boxscoreSqFmtPct(Math.min(aSq.high_danger_share, hSq.high_danger_share)) +
              ")"
          );
        }
      }
      if (aSq.total !== hSq.total) {
        var volLead = aSq.total > hSq.total ? aName : hName;
        bits.push(
          volLead +
            " took more attempts (" +
            Math.max(aSq.total, hSq.total) +
            "–" +
            Math.min(aSq.total, hSq.total) +
            ")"
        );
      }
      if (!bits.length) return "Both teams generated a similar shot-quality mix.";
      return bits.slice(0, 2).join(". ") + ".";
    }

    function metricCell(val, lead) {
      return (
        '<td class="boxscore-sq-compare__val' +
        (lead ? " is-lead" : "") +
        '">' +
        val +
        "</td>"
      );
    }

    function renderTeamChart() {
      if (!teamMount) return;
      var awaySq = boxscoreSqProfileFromCounts(data.away.counts);
      var homeSq = boxscoreSqProfileFromCounts(data.home.counts);
      var aVis = boxscoreSqVisibleTotal(data.away.counts, state.enabled);
      var hVis = boxscoreSqVisibleTotal(data.home.counts, state.enabled);
      var mix = state.mode === "mix";
      if (teamSub) {
        teamSub.textContent = mix
          ? "Share of selected mix (SQ4 highest · SQ0 lowest)"
          : "Volume by quality (SQ4 highest · SQ0 lowest)";
      }
      if (!aVis && !hVis) {
        teamMount.innerHTML = '<p class="boxscore-empty">No attempts in the selected buckets.</p>';
        return;
      }
      var maxVol = Math.max(aVis, hVis, 1);
      var yMax = mix ? 100 : boxscoreSqNiceMax(maxVol);
      var ticks = [];
      var steps = 4;
      for (var i = 0; i <= steps; i++) ticks.push((yMax * i) / steps);

      function stackHtml(side, counts, vis) {
        var hPct = mix ? 100 : (100 * vis) / yMax;
        var segs = "";
        BOXSCORE_SQ_BUCKETS.forEach(function (b) {
          if (!state.enabled[b.key]) return;
          var n = counts[b.key] || 0;
          if (!n || !vis) return;
          var frac = mix ? (100 * n) / vis : (100 * n) / vis;
          segs +=
            '<button type="button" class="boxscore-sq-vseg boxscore-sq-vseg--' +
            b.key +
            '" data-sq-tip-side="' +
            escapeAttr(side.abbr || "") +
            '" data-sq-tip-bucket="' +
            b.key +
            '" data-sq-tip-count="' +
            n +
            '" data-sq-tip-total="' +
            vis +
            '" style="flex:' +
            frac +
            " 0 0\" aria-label=\"" +
            escapeAttr((side.abbr || "") + " " + b.label + " " + n) +
            '"></button>';
        });
        return (
          '<div class="boxscore-sq-vchart__col">' +
          '<div class="boxscore-sq-vchart__stack-wrap">' +
          '<div class="boxscore-sq-vchart__stack" style="height:' +
          hPct +
          '%">' +
          segs +
          "</div></div></div>"
        );
      }

      function teamLabelHtml(side) {
        return (
          '<div class="boxscore-sq-vchart__team">' +
          teamLogoCell(side.logo_url, side.slug, side.abbr) +
          '<span class="boxscore-sq-vchart__abbr">' +
          escapeHtml(side.abbr || "") +
          "</span></div>"
        );
      }

      var html = '<div class="boxscore-sq-team__layout">';
      html += '<div class="boxscore-sq-vchart" aria-label="Team shot quality stacked bar chart">';
      html += '<div class="boxscore-sq-vchart__axis" aria-hidden="true">';
      ticks
        .slice()
        .reverse()
        .forEach(function (t) {
          html +=
            '<span>' +
            (mix ? Math.round(t) + "%" : String(t % 1 === 0 ? t : Math.round(t))) +
            "</span>";
        });
      html += "</div>";
      html += '<div class="boxscore-sq-vchart__plot">';
      html += '<div class="boxscore-sq-vchart__grid" aria-hidden="true">';
      ticks.forEach(function () {
        html += "<span></span>";
      });
      html += "</div>";
      html += '<div class="boxscore-sq-vchart__bars">';
      html += stackHtml(data.away, data.away.counts, aVis);
      html += stackHtml(data.home, data.home.counts, hVis);
      html += "</div></div>";
      html += '<div class="boxscore-sq-vchart__labels" aria-hidden="true">';
      html += teamLabelHtml(data.away);
      html += teamLabelHtml(data.home);
      html += "</div></div>";

      var aLeadVol = awaySq.total > homeSq.total;
      var hLeadVol = homeSq.total > awaySq.total;
      var aLeadAvg = awaySq.sq_avg != null && homeSq.sq_avg != null && awaySq.sq_avg > homeSq.sq_avg;
      var hLeadAvg = awaySq.sq_avg != null && homeSq.sq_avg != null && homeSq.sq_avg > awaySq.sq_avg;
      var aLeadHd =
        awaySq.high_danger_share != null &&
        homeSq.high_danger_share != null &&
        awaySq.high_danger_share > homeSq.high_danger_share;
      var hLeadHd =
        awaySq.high_danger_share != null &&
        homeSq.high_danger_share != null &&
        homeSq.high_danger_share > awaySq.high_danger_share;
      html +=
        '<table class="boxscore-sq-compare"><caption>Game shot-quality totals</caption><thead><tr><th></th><th>' +
        escapeHtml(data.away.abbr || "Away") +
        "</th><th>" +
        escapeHtml(data.home.abbr || "Home") +
        "</th></tr></thead><tbody>";
      html +=
        "<tr><th scope=\"row\">Attempts</th>" +
        metricCell(String(awaySq.total), aLeadVol) +
        metricCell(String(homeSq.total), hLeadVol) +
        "</tr>";
      html +=
        "<tr><th scope=\"row\">SQ avg</th>" +
        metricCell(boxscoreSqFmtAvg(awaySq.sq_avg), aLeadAvg) +
        metricCell(boxscoreSqFmtAvg(homeSq.sq_avg), hLeadAvg) +
        "</tr>";
      html +=
        '<tr><th scope="row">High-danger</th>' +
        metricCell(boxscoreSqFmtPct(awaySq.high_danger_share), aLeadHd) +
        metricCell(boxscoreSqFmtPct(homeSq.high_danger_share), hLeadHd) +
        "</tr>";
      html += "</tbody></table></div>";
      teamMount.innerHTML = html;
    }

    function renderPlayerCharts() {
      if (!playerMount) return;
      var awayRows = sortPlayers(data.awayPlayers || []);
      var homeRows = sortPlayers(data.homePlayers || []);
      var sortLabel = BOXSCORE_SQ_SORT_LABELS[state.sort] || "total shots";
      if (playerSub) {
        playerSub.textContent =
          "Sorted by " +
          sortLabel +
          (state.mode === "mix" ? " · bars show mix %" : " · SQ4 highest · SQ0 lowest");
      }
      if (!awayRows.length && !homeRows.length) {
        playerMount.innerHTML = '<p class="boxscore-empty">No player attempts in the selected buckets.</p>';
        return;
      }
      var awayMax = 1;
      var homeMax = 1;
      awayRows.forEach(function (r) {
        if (r.vis > awayMax) awayMax = r.vis;
      });
      homeRows.forEach(function (r) {
        if (r.vis > homeMax) homeMax = r.vis;
      });
      var sharedMax = Math.max(awayMax, homeMax);
      var mix = state.mode === "mix";

      function axisTicks(maxVal) {
        var yMax = mix ? 100 : boxscoreSqNiceMax(maxVal);
        var ticks = [0, yMax / 2, yMax];
        var h = '<div class="boxscore-sq-hchart__axis" aria-hidden="true">';
        ticks.forEach(function (t) {
          h += "<span>" + (mix ? Math.round(t) + "%" : String(Math.round(t))) + "</span>";
        });
        h += "</div>";
        return { html: h, max: yMax };
      }

      function colHtml(side, rows, maxVal) {
        var axis = axisTicks(maxVal);
        var h =
          '<div class="boxscore-sq-hchart">' +
          '<h4 class="boxscore-sq-hchart__head">' +
          teamLogoCell(side.logo_url, side.slug, side.abbr) +
          '<span>' +
          escapeHtml(side.abbr || "") +
          "</span></h4>";
        if (!rows.length) {
          h += '<p class="boxscore-empty">No attempts.</p></div>';
          return h;
        }
        rows.forEach(function (row) {
          var key = (side.abbr || "") + ":" + String(row.p.player_id || row.p.player || "");
          var open = !!state.expanded[key];
          var widthPct = mix ? 100 : (100 * row.vis) / axis.max;
          var segs = "";
          BOXSCORE_SQ_BUCKETS.forEach(function (b) {
            if (!state.enabled[b.key]) return;
            var n = row.p.counts[b.key] || 0;
            if (!n || !row.vis) return;
            var frac = (100 * n) / row.vis;
            segs +=
              '<button type="button" class="boxscore-sq-hseg boxscore-sq-hseg--' +
              b.key +
              '" data-sq-tip-side="' +
              escapeAttr(row.p.player || "") +
              '" data-sq-tip-bucket="' +
              b.key +
              '" data-sq-tip-count="' +
              n +
              '" data-sq-tip-total="' +
              row.vis +
              '" style="width:' +
              frac +
              '%" aria-label="' +
              escapeAttr((row.p.player || "") + " " + b.label + " " + n) +
              '"></button>';
          });
          var detail = "";
          BOXSCORE_SQ_BUCKETS.forEach(function (b) {
            detail +=
              '<span><span class="boxscore-sq-swatch boxscore-sq-swatch--' +
              b.key +
              '" aria-hidden="true"></span>' +
              b.label +
              " " +
              (row.p.counts[b.key] || 0) +
              "</span>";
          });
          detail +=
            "<span>SQ avg " +
            boxscoreSqFmtAvg(row.sq.sq_avg) +
            "</span><span>HD " +
            boxscoreSqFmtPct(row.sq.high_danger_share) +
            "</span>";
          h +=
            '<div class="boxscore-sq-player' +
            (open ? " is-open" : "") +
            '" data-sq-player-key="' +
            escapeAttr(key) +
            '">';
          h += '<div class="boxscore-sq-player__row">';
          h +=
            '<div class="boxscore-sq-player__name">' +
            boxscorePlayerLink(row.p.player_id, row.p.player) +
            "</div>";
          h +=
            '<div class="boxscore-sq-player__bar"><div class="boxscore-sq-player__fill" style="width:' +
            widthPct +
            '%">' +
            segs +
            "</div></div>";
          h += '<span class="boxscore-sq-player__n">' + row.vis + "</span>";
          h += "</div>";
          h +=
            '<div class="boxscore-sq-player__detail"' +
            (open ? "" : " hidden") +
            ">" +
            detail +
            "</div></div>";
        });
        h += axis.html;
        h += "</div>";
        return h;
      }

      var aMax = state.scale === "shared" ? sharedMax : awayMax;
      var hMax = state.scale === "shared" ? sharedMax : homeMax;
      playerMount.innerHTML =
        colHtml(data.away, awayRows, aMax) + colHtml(data.home, homeRows, hMax);
    }

    function syncLegend() {
      var allOn = enabledCount() === BOXSCORE_SQ_BUCKETS.length;
      root.querySelectorAll("[data-sq-legend]").forEach(function (btn) {
        var key = btn.getAttribute("data-sq-legend");
        var on = !!state.enabled[key];
        btn.classList.toggle("is-on", on);
        btn.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (showAllBtn) showAllBtn.hidden = allOn;
      var hdBtn = root.querySelector('[data-sq-preset="hd"]');
      if (hdBtn) {
        var hdOnly = !state.enabled.sq0 && !state.enabled.sq1 && !state.enabled.sq2 && state.enabled.sq3 && state.enabled.sq4;
        hdBtn.classList.toggle("is-active", hdOnly);
      }
    }

    function render() {
      if (insightEl) insightEl.textContent = insightText();
      syncLegend();
      renderTeamChart();
      renderPlayerCharts();
    }

    root.addEventListener("pointerover", function (ev) {
      var t = ev.target.closest("[data-sq-tip-bucket]");
      if (!t || !root.contains(t) || tip.getAttribute("data-pinned")) return;
      tipFromEl(t, ev, false);
    });
    root.addEventListener("pointermove", function (ev) {
      if (tip.hidden || tip.getAttribute("data-pinned")) return;
      if (!ev.target.closest("[data-sq-tip-bucket]")) return;
      placeTip(ev.clientX, ev.clientY);
    });
    root.addEventListener("pointerout", function (ev) {
      var t = ev.target.closest("[data-sq-tip-bucket]");
      if (!t) return;
      var rel = ev.relatedTarget;
      if (rel && rel.closest && rel.closest("[data-sq-tip-bucket]") === t) return;
      hideTip(false);
    });
    root.addEventListener("click", function (ev) {
      var seg = ev.target.closest("[data-sq-tip-bucket]");
      if (seg && root.contains(seg) && !ev.target.closest("[data-sq-player-key]")) {
        tipFromEl(seg, ev, true);
        ev.preventDefault();
        return;
      }
      var player = ev.target.closest("[data-sq-player-key]");
      if (player && root.contains(player) && !ev.target.closest("a")) {
        var key = player.getAttribute("data-sq-player-key");
        state.expanded[key] = !state.expanded[key];
        player.classList.toggle("is-open", !!state.expanded[key]);
        var det = player.querySelector(".boxscore-sq-player__detail");
        if (det) det.hidden = !state.expanded[key];
      }
    });
    if (!window.__bowlSqTipDocBound) {
      window.__bowlSqTipDocBound = true;
      document.addEventListener("click", function (ev) {
        var t = document.getElementById("boxscore-sq-tooltip");
        if (!t || t.hidden) return;
        if (ev.target.closest(".boxscore-sq-tooltip") || ev.target.closest("[data-sq-tip-bucket]")) return;
        t.hidden = true;
        t.removeAttribute("data-pinned");
      });
    }

    root.querySelectorAll("[data-sq-legend]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.getAttribute("data-sq-legend");
        var next = !state.enabled[key];
        if (!next && enabledCount() <= 1) return;
        state.enabled[key] = next;
        render();
      });
    });
    root.querySelectorAll("[data-sq-preset]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var preset = btn.getAttribute("data-sq-preset");
        if (preset === "all") state.enabled = boxscoreSqAllEnabled();
        if (preset === "hd") {
          state.enabled = { sq0: false, sq1: false, sq2: false, sq3: true, sq4: true };
        }
        render();
      });
    });
    root.querySelectorAll("[data-sq-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.mode = btn.getAttribute("data-sq-mode") || "volume";
        root.querySelectorAll("[data-sq-mode]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        render();
      });
    });
    root.querySelectorAll("[data-sq-scale]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        state.scale = btn.getAttribute("data-sq-scale") || "shared";
        root.querySelectorAll("[data-sq-scale]").forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        render();
      });
    });
    var sortSel = root.querySelector("[data-sq-sort]");
    if (sortSel) {
      sortSel.addEventListener("change", function () {
        state.sort = sortSel.value || "total";
        render();
      });
    }

    render();
  }

  function bindBoxScoreTabs(container, opts) {
    opts = opts || {};
    var panel = container.querySelector(".boxscore-panel");
    if (!panel) return;
    var tabs = Array.prototype.slice.call(panel.querySelectorAll("[data-boxscore-tab]"));
    var panes = Array.prototype.slice.call(panel.querySelectorAll("[data-boxscore-pane]"));
    if (!tabs.length) return;
    var ids = tabs.map(function (t) {
      return t.getAttribute("data-boxscore-tab");
    });
    var tablist = panel.querySelector('[role="tablist"]');

    function activate(id, sync) {
      if (ids.indexOf(id) < 0) id = ids[0];
      tabs.forEach(function (t) {
        var on = t.getAttribute("data-boxscore-tab") === id;
        t.classList.toggle("is-active", on);
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
      });
      panes.forEach(function (p) {
        p.hidden = p.getAttribute("data-boxscore-pane") !== id;
      });
      if (sync && opts.syncHash) {
        try {
          history.replaceState(null, "", "#" + id);
        } catch (e) {}
      }
    }

    tabs.forEach(function (t) {
      t.addEventListener("click", function () {
        activate(t.getAttribute("data-boxscore-tab"), true);
      });
    });
    if (tablist) {
      tablist.addEventListener("keydown", function (ev) {
        var cur = ids.indexOf(
          document.activeElement && document.activeElement.getAttribute("data-boxscore-tab")
        );
        if (cur < 0) return;
        var next = cur;
        if (ev.key === "ArrowRight") next = (cur + 1) % ids.length;
        else if (ev.key === "ArrowLeft") next = (cur - 1 + ids.length) % ids.length;
        else if (ev.key === "Home") next = 0;
        else if (ev.key === "End") next = ids.length - 1;
        else return;
        ev.preventDefault();
        tabs[next].focus();
        activate(ids[next], true);
      });
    }

    var initial = "summary";
    if (opts.syncHash) {
      var hash = (location.hash || "").replace(/^#/, "");
      if (ids.indexOf(hash) >= 0) initial = hash;
    }
    activate(initial, false);
  }

  function renderBoxScoreHtml(d) {
    var away = d.away || {};
    var home = d.home || {};
    var gid = d.game_id != null ? String(d.game_id) : "g";
    var html = '<div class="boxscore-panel">';
    html +=
      '<div class="boxscore-tabs" role="tablist" aria-label="Game views">' +
      '<button type="button" class="boxscore-tabs__tab is-active" role="tab" aria-selected="true" id="boxscore-tab-summary-' +
      escapeAttr(gid) +
      '" data-boxscore-tab="summary" aria-controls="boxscore-pane-summary-' +
      escapeAttr(gid) +
      '">Summary</button>' +
      '<button type="button" class="boxscore-tabs__tab" role="tab" aria-selected="false" tabindex="-1" id="boxscore-tab-boxscore-' +
      escapeAttr(gid) +
      '" data-boxscore-tab="boxscore" aria-controls="boxscore-pane-boxscore-' +
      escapeAttr(gid) +
      '">Box Score</button>' +
      '<button type="button" class="boxscore-tabs__tab" role="tab" aria-selected="false" tabindex="-1" id="boxscore-tab-shot-quality-' +
      escapeAttr(gid) +
      '" data-boxscore-tab="shot-quality" aria-controls="boxscore-pane-shot-quality-' +
      escapeAttr(gid) +
      '">Shot Quality</button></div>';

    html +=
      '<div class="boxscore-tab-panel" role="tabpanel" id="boxscore-pane-summary-' +
      escapeAttr(gid) +
      '" data-boxscore-pane="summary" aria-labelledby="boxscore-tab-summary-' +
      escapeAttr(gid) +
      '">';
    html += boxscoreSummaryHtml(d, away, home);
    html += "</div>";

    html +=
      '<div class="boxscore-tab-panel" role="tabpanel" id="boxscore-pane-boxscore-' +
      escapeAttr(gid) +
      '" data-boxscore-pane="boxscore" aria-labelledby="boxscore-tab-boxscore-' +
      escapeAttr(gid) +
      '" hidden>';
    html += boxscoreStatsHtml(d, away, home);
    html += "</div>";

    html +=
      '<div class="boxscore-tab-panel" role="tabpanel" id="boxscore-pane-shot-quality-' +
      escapeAttr(gid) +
      '" data-boxscore-pane="shot-quality" aria-labelledby="boxscore-tab-shot-quality-' +
      escapeAttr(gid) +
      '" hidden>';
    html += boxscoreShotQualityHtml(d, away, home);
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
  window.BOWL.loadBoxScore = function (gameId, container, opts) {
    if (!container) return;
    opts = opts || {};
    var st = (opts.status || container.getAttribute("data-game-status") || "").toLowerCase();
    if (st && st !== "final") {
      container.innerHTML = '<p class="boxscore-loading">Loading game preview…</p>';
      fetch(withRoot("/api/game/" + gameId + "/preview"))
        .then(function (r) {
          return r.json();
        })
        .then(function (d) {
          if (d.error) {
            container.innerHTML =
              "<p class=\"boxscore-error\">Preview unavailable" +
              (d.message ? ": " + escapeHtml(String(d.message)) : ".") +
              "</p>";
            return;
          }
          container.innerHTML = renderGamePreviewHtml(d);
          if (typeof window.bindPlayerHoverAnchors === "function") window.bindPlayerHoverAnchors();
          if (typeof window.bindTeamHoverAnchors === "function") window.bindTeamHoverAnchors();
        })
        .catch(function () {
          container.innerHTML = "<p class=\"boxscore-error\">Failed to load preview.</p>";
        });
      return;
    }
    container.innerHTML = '<p class="boxscore-loading">Loading box score…</p>';
    fetch(withRoot("/api/game/" + gameId + "/boxscore") + "?v=tabs-sq-v1")
      .then(function (r) {
        return r.json();
      })
      .then(function (d) {
        if (d.error) {
          container.innerHTML = "<p class=\"boxscore-error\">Box score unavailable.</p>";
          return;
        }
        container.innerHTML = renderBoxScoreHtml(d);
        bindBoxScoreTabs(container, { syncHash: !!opts.syncHash });
        bindBoxScoreShotQuality(container);
        if (typeof window.bindPlayerHoverAnchors === "function") window.bindPlayerHoverAnchors();
        if (typeof window.bindTeamHoverAnchors === "function") window.bindTeamHoverAnchors();
      })
      .catch(function () {
        container.innerHTML = "<p class=\"boxscore-error\">Failed to load box score.</p>";
      });
  };

  function newsEngUpdateVoteUI(wrap, data) {
    if (!wrap || !data) return;
    var up = wrap.querySelector('[data-news-cnt="up"]');
    var dn = wrap.querySelector('[data-news-cnt="down"]');
    if (up) up.textContent = String(data.thumbs_up != null ? data.thumbs_up : 0);
    if (dn) dn.textContent = String(data.thumbs_down != null ? data.thumbs_down : 0);
    var mv = data.my_vote;
    var bUp = wrap.querySelector('[data-news-vote="1"]');
    var bDn = wrap.querySelector('[data-news-vote="-1"]');
    if (bUp) {
      bUp.classList.toggle("is-selected", mv === 1);
      bUp.setAttribute("aria-pressed", mv === 1 ? "true" : "false");
    }
    if (bDn) {
      bDn.classList.toggle("is-selected", mv === -1);
      bDn.setAttribute("aria-pressed", mv === -1 ? "true" : "false");
    }
    var tool = wrap.querySelector(".news-eng__toolbar");
    if (!tool) return;
    var existing = tool.querySelector(".news-eng__btn--clear");
    var hasForm = wrap.querySelector("[data-news-comment-form]");
    if (mv && hasForm) {
      if (!existing) {
        var clr = document.createElement("button");
        clr.type = "button";
        clr.className = "news-eng__btn news-eng__btn--clear muted";
        clr.setAttribute("data-news-vote", "0");
        clr.setAttribute("title", "Clear your vote");
        clr.textContent = "Clear";
        tool.appendChild(clr);
      }
    } else if (existing) {
      existing.remove();
    }
  }

  function newsEngAppendComment(wrap, c) {
    if (!wrap || !c) return;
    var ul = wrap.querySelector(".news-eng__comments");
    if (!ul) {
      ul = document.createElement("ul");
      ul.className = "news-eng__comments";
      ul.setAttribute("aria-label", "Comments");
      var form = wrap.querySelector("[data-news-comment-form]");
      if (form) wrap.insertBefore(ul, form);
      else wrap.appendChild(ul);
    }
    var when = c.created_at ? String(c.created_at).slice(0, 10) : "";
    var li = document.createElement("li");
    li.className = "news-eng__comment";
    li.innerHTML =
      '<span class="news-eng__comment-meta"><strong>' +
      escapeHtml(String(c.author_label || "")) +
      "</strong>" +
      (when
        ? ' · <time datetime="' +
          escapeAttr(String(c.created_at)) +
          '">' +
          escapeHtml(when) +
          "</time>"
        : "") +
      '</span> <span class="news-eng__comment-body">' +
      escapeHtml(String(c.body || "")) +
      "</span>";
    ul.appendChild(li);
  }

  document.addEventListener("click", function (e) {
    var btn = e.target && e.target.closest("[data-news-vote]");
    if (!btn || btn.disabled) return;
    var wrap = btn.closest("[data-news-article-id]");
    if (!wrap) return;
    var aid = wrap.getAttribute("data-news-article-id");
    if (!aid) return;
    var raw = btn.getAttribute("data-news-vote");
    var val = parseInt(raw, 10);
    if (isNaN(val) || (val !== 1 && val !== -1 && val !== 0)) return;
    btn.disabled = true;
    fetch(withRoot("/api/news/" + encodeURIComponent(aid) + "/vote"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ value: val }),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, j: j };
        });
      })
      .then(function (x) {
        if (x.status === 401 && x.j && x.j.error === "auth") {
          window.location.href = withRoot("/login?next=" + encodeURIComponent(window.location.pathname));
          return;
        }
        if (x.ok && x.j && x.j.ok) newsEngUpdateVoteUI(wrap, x.j);
      })
      .finally(function () {
        btn.disabled = false;
      });
  });

  document.addEventListener("submit", function (e) {
    var form = e.target && e.target.closest("[data-news-comment-form]");
    if (!form) return;
    e.preventDefault();
    var wrap = form.closest("[data-news-article-id]");
    if (!wrap) return;
    var aid = wrap.getAttribute("data-news-article-id");
    if (!aid) return;
    var ta = form.querySelector('textarea[name="body"]');
    var body = ta ? String(ta.value || "").trim() : "";
    if (!body) return;
    var sub = form.querySelector('button[type="submit"]');
    if (sub) sub.disabled = true;
    fetch(withRoot("/api/news/" + encodeURIComponent(aid) + "/comments"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ body: body }),
    })
      .then(function (r) {
        return r.json().then(function (j) {
          return { ok: r.ok, status: r.status, j: j };
        });
      })
      .then(function (x) {
        if (x.status === 401 && x.j && x.j.error === "auth") {
          window.location.href = withRoot("/login?next=" + encodeURIComponent(window.location.pathname));
          return;
        }
        if (x.ok && x.j && x.j.ok && x.j.comment) {
          newsEngAppendComment(wrap, x.j.comment);
          if (ta) ta.value = "";
        }
      })
      .finally(function () {
        if (sub) sub.disabled = false;
      });
  });

  (function initProspectProjectionPopovers() {
    var cells = document.querySelectorAll(".prospect-proj-cell[data-prospect-proj]");
    if (!cells.length) return;

    var tip = document.createElement("div");
    tip.className = "prospect-proj-popover";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);

    var activeCell = null;
    var hideTimer = null;

    function formatPct(pct) {
      if (pct == null) return "—";
      if (pct >= 99) return ">99%";
      return String(pct) + "%";
    }

    function chartSvgMarkup(chart) {
      if (!chart || !chart.has_data) return "";
      var parts = [];
      parts.push(
        '<svg class="prospect-proj-popover__chart" viewBox="0 0 ' +
          chart.width +
          " " +
          chart.height +
          '" width="100%" height="' +
          chart.height +
          '" aria-hidden="true">'
      );
      (chart.grid_lines || []).forEach(function (line) {
        parts.push(
          '<line class="prospect-proj-popover__chart-grid" x1="' +
            line.x1 +
            '" x2="' +
            line.x2 +
            '" y1="' +
            line.y +
            '" y2="' +
            line.y +
            '"></line>'
        );
      });
      (chart.y_labels || []).forEach(function (lbl) {
        parts.push(
          '<text class="prospect-proj-popover__chart-y-label" x="' +
            lbl.x +
            '" y="' +
            lbl.y +
            '">' +
            escapeHtml(lbl.text) +
            "</text>"
        );
      });
      (chart.paths || []).forEach(function (p) {
        parts.push(
          '<path class="prospect-proj-popover__chart-line" d="' +
            escapeHtml(p.d) +
            '" fill="none" stroke-width="2" vector-effect="non-scaling-stroke"></path>'
        );
        (p.dots || []).forEach(function (dot) {
          parts.push(
            '<circle fill="#f8fafc" stroke="#0f172a" stroke-width="1" cx="' +
              dot.cx +
              '" cy="' +
              dot.cy +
              '" r="2.5"></circle>'
          );
        });
      });
      (chart.x_labels || []).forEach(function (lbl) {
        parts.push(
          '<text class="prospect-proj-popover__chart-label" x="' +
            lbl.x +
            '" y="' +
            lbl.y +
            '" text-anchor="middle">' +
            escapeHtml(lbl.text) +
            "</text>"
        );
      });
      parts.push("</svg>");
      return parts.join("");
    }

    function renderPopover(data) {
      var metaBits = [];
      if (data.nationality) metaBits.push(["Nation", data.nationality]);
      if (data.age != null) metaBits.push(["Age", String(data.age)]);
      if (data.height) metaBits.push(["Height", data.height]);
      if (data.weight != null) metaBits.push(["Weight", String(data.weight)]);
      var metaHtml = metaBits
        .map(function (pair) {
          return "<div><dt>" + escapeHtml(pair[0]) + '</dt><dd>' + escapeHtml(pair[1]) + "</dd></div>";
        })
        .join("");

      tip.innerHTML =
        '<div class="prospect-proj-popover__inner">' +
        '<div class="prospect-proj-popover__head">' +
        '<span class="prospect-proj-popover__name">' +
        escapeHtml(data.name || "Prospect") +
        "</span>" +
        (data.position
          ? '<span class="prospect-proj-popover__pos">' + escapeHtml(data.position) + "</span>"
          : "") +
        "</div>" +
        '<dl class="prospect-proj-popover__meta">' +
        metaHtml +
        "</dl>" +
        '<div class="prospect-proj-popover__scores">' +
        '<div class="prospect-proj-popover__score-box prospect-proj-popover__score-box--star">' +
        '<div class="prospect-proj-popover__score-label">Star%</div>' +
        '<div class="prospect-proj-popover__score-value">' +
        escapeHtml(formatPct(data.star_pct)) +
        "</div></div>" +
        '<div class="prospect-proj-popover__score-box prospect-proj-popover__score-box--bowl">' +
        '<div class="prospect-proj-popover__score-label">BOWL%</div>' +
        '<div class="prospect-proj-popover__score-value">' +
        escapeHtml(formatPct(data.bowl_pct)) +
        "</div></div>" +
        "</div>" +
        '<div class="prospect-proj-popover__chart-wrap">' +
        '<div class="prospect-proj-popover__chart-title">BOWL Equivalency Timeline</div>' +
        chartSvgMarkup(data.chart) +
        "</div>" +
        '<div class="prospect-proj-popover__defs">' +
        "<p><strong>Star:</strong> top 20% WAR/82 GP among F or top 15% among D.</p>" +
        "<p><strong>BOWL%:</strong> projected 200+ BOWL games.</p>" +
        "<p><strong>BOWLe (DY-1e / DYe):</strong> projected BOWL impact on a 0–30 scale (NHLe-style), " +
        "from ABI, POT, OVR, and overview ratings. DY-1e = one year before draft eligibility; " +
        "DYe = at draft year with a potential uptick. Benchmarks: ~8 depth, ~15 everyday NHLer, " +
        "~22 strong starter, ~28+ elite.</p>" +
        '<p>Inspired by <a href="https://hockeystats.com/methodology/nhle" target="_blank" rel="noopener noreferrer">HockeyStats NHLe</a>.</p>' +
        "</div></div>";
    }

    function positionTip(el) {
      var rect = el.getBoundingClientRect();
      var tipRect = tip.getBoundingClientRect();
      var left = rect.left + rect.width / 2 - tipRect.width / 2;
      var top = rect.bottom + 8;
      left = Math.max(8, Math.min(left, window.innerWidth - tipRect.width - 8));
      if (top + tipRect.height > window.innerHeight - 8) {
        top = rect.top - tipRect.height - 8;
      }
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    }

    function showTip(el) {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      var raw = el.getAttribute("data-prospect-proj");
      if (!raw) return;
      var data;
      try {
        data = JSON.parse(raw);
      } catch (_e) {
        return;
      }
      activeCell = el;
      renderPopover(data);
      tip.hidden = false;
      positionTip(el);
    }

    function hideTip() {
      hideTimer = setTimeout(function () {
        tip.hidden = true;
        activeCell = null;
      }, 120);
    }

    cells.forEach(function (cell) {
      cell.addEventListener("mouseenter", function () {
        showTip(cell);
      });
      cell.addEventListener("mouseleave", hideTip);
      cell.addEventListener("focus", function () {
        showTip(cell);
      });
      cell.addEventListener("blur", hideTip);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        tip.hidden = true;
        activeCell = null;
      }
    });

    window.addEventListener(
      "scroll",
      function () {
        if (!tip.hidden && activeCell) positionTip(activeCell);
      },
      true
    );
  })();

  function syncProspectsRankingsStickyLayout() {
    document.querySelectorAll(".prospects-rankings-page").forEach(function (page) {
      var head = page.querySelector(".prospects-rankings-page__sticky-head");
      var offset = head ? Math.ceil(head.getBoundingClientRect().height) : 0;
      page.style.setProperty("--prospects-sticky-head-offset", offset + "px");
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncProspectsRankingsStickyLayout);
  } else {
    syncProspectsRankingsStickyLayout();
  }
  window.addEventListener("resize", syncProspectsRankingsStickyLayout);
})();
