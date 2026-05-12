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
    var hw = formatHeight(d.height_inches) + " · " + (d.weight_lbs != null ? escapeHtml(String(d.weight_lbs)) + " lbs" : "—");
    var sub =
      "Age " +
      escapeHtml(String(d.age != null ? d.age : "—")) +
      " · " +
      escapeHtml(nat) +
      " · " +
      hw +
      " · Shoots " +
      escapeHtml(shoots);
    var contr = d.contract;
    if (contr && (contr.aav != null || contr.years_left != null)) {
      sub += " · ";
      if (contr.aav != null) sub += "AAV " + escapeHtml(fmtMoneyShare(contr.aav));
      if (contr.years_left != null) {
        if (contr.aav != null) sub += " · ";
        sub +=
          escapeHtml(String(contr.years_left)) +
          " yr" +
          (Number(contr.years_left) === 1 ? "" : "s") +
          " left";
      }
    }
    var logoHtml = d.team_logo_url
      ? '<img class="player-share-card__team-logo" src="' + escapeAttr(d.team_logo_url) + '" alt="">'
      : '<span class="player-share-card__team-logo-ph"></span>';
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
    var pills =
      '<div class="player-share-card__pills">' +
      '<span class="player-share-card__pill player-share-card__pill--ovr"><span class="player-share-card__pill-lbl">OVR</span> ' +
      ovr +
      "</span>" +
      '<span class="player-share-card__pill player-share-card__pill--ap"><span class="player-share-card__pill-lbl">ABI</span> ' +
      abiS +
      "</span>" +
      '<span class="player-share-card__pill player-share-card__pill--ap"><span class="player-share-card__pill-lbl">POT</span> ' +
      potS +
      "</span></div>";
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
    var leftTitle = d.is_goalie ? "Goalie" : "Attributes";
    var rightTitle = d.is_goalie ? "Mental" : "Attributes";
    function colHtml(title, rows) {
      var h =
        '<div class="player-share-card__col"><div class="player-share-card__col-title">' +
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
    var ratingsBlk =
      '<div class="player-share-card__ratings">' +
      colHtml(leftTitle, rc.left) +
      colHtml(rightTitle, rc.right) +
      "</div>";
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
      "</div></div></div>" +
      '<div class="player-share-card__sub">' +
      sub +
      "</div>" +
      '<div class="player-share-card__body-row">' +
      photoHtml +
      '<div class="player-share-card__body-main">' +
      pills +
      chipRow +
      "</div></div>" +
      ratingsBlk +
      statsBlk +
      '<div class="player-share-card__footer">' +
      escapeHtml(league) +
      "</div></div>";
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
    var HOVER_CARD_CACHE_VER = 6;
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
          "<thead><tr><th>Season</th><th title=\"Team (era logo)\">TM</th><th>GP</th><th>W</th><th>L</th><th>GA</th><th>SV%</th></tr></thead><tbody>";
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
      var copyBar =
        '<div class="player-hover-card__toolbar">' +
        '<button type="button" class="player-hover-card__copy js-copy-player-card" data-player-id="' +
        escapeAttr(String(playerIdForCard)) +
        '" title="Copies the player card image to the clipboard for Discord.">Copy card</button></div>';
      card.innerHTML =
        copyBar +
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

  function initTeamHoverCards() {
    var cache = {};
    var HOVER_TEAM_CACHE_VER = 1;
    var activeAnchor = null;
    var showTimer = null;
    var hideTimer = null;
    var card = document.createElement("div");
    card.className = "team-hover-preview-card";
    card.hidden = true;
    document.body.appendChild(card);

    function hideCard() {
      card.hidden = true;
      activeAnchor = null;
    }

    function scheduleHide() {
      clearTimeout(showTimer);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideCard, 140);
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
      var streak =
        d.streak && String(d.streak).trim()
          ? '<div class="team-hover-preview-card__streak">Streak: ' + escapeHtml(String(d.streak).trim()) + "</div>"
          : "";

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
        "</div></div></div>" +
        statsBar +
        streak +
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

    function bindTeamHoverAnchors() {
      document.querySelectorAll('a[href*="/team/"]').forEach(function (a) {
        if (a.getAttribute("data-team-hover-bound") === "1") return;
        var slug = teamSlugFromHref(a.getAttribute("href"));
        if (!slug) return;
        a.setAttribute("data-team-hover-bound", "1");
        a.addEventListener("mouseenter", function () {
          showFor(a, slug);
        });
        a.addEventListener("mouseleave", scheduleHide);
        a.addEventListener("focusin", function () {
          showFor(a, slug);
        });
        a.addEventListener("focusout", scheduleHide);
      });
    }

    bindTeamHoverAnchors();

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

  document.addEventListener("DOMContentLoaded", function () {
    applyTheme(getPreferredTheme());
    initPlayerShareCardClipboardHint();
    initPlayerSeasonTrendCharts();
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

  function renderGamePreviewHtml(d) {
    var away = d.away || {};
    var home = d.home || {};
    var odds = d.odds || {};
    var hp = odds.home_pct_display != null ? Number(odds.home_pct_display) : 50;
    var ap = odds.away_pct_display != null ? Number(odds.away_pct_display) : 50;
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
    oddsHtml += '<div class="game-preview-odds__bar" title="' + escapeAttr(odds.method_note || "") + '">';
    oddsHtml +=
      '<div class="game-preview-odds__seg game-preview-odds__seg--away" style="width:' +
      ap +
      '%"></div>';
    oddsHtml +=
      '<div class="game-preview-odds__seg game-preview-odds__seg--home" style="width:' +
      hp +
      '%"></div>';
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
})();
