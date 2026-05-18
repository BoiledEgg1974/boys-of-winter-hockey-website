(function () {
  "use strict";

  var PLAYER_URL_PH = "988776655";

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/"/g, "&quot;");
  }

  function updateLockTimers() {
    var now = Date.now();
    document.querySelectorAll(".bowl-six-lock").forEach(function (parent) {
      var out = parent.querySelector(".bowl-six-lock__timer, .bowl-six-lock-timer");
      var mode = parent.getAttribute("data-lock-mode") || "countdown";
      var display = parent.getAttribute("data-lock-display") || "";
      if (mode === "locked") {
        if (out) out.textContent = display || "Locked";
        return;
      }
      var raw = parent.getAttribute("data-lock-at");
      if (!raw) return;
      var target = new Date(raw).getTime();
      if (isNaN(target)) return;
      var diff = target - now;
      if (diff <= 0) {
        parent.setAttribute("data-lock-mode", "locked");
        var label = parent.querySelector(".bowl-six-lock__label");
        if (label) label.textContent = "Lineups locked";
        if (out) out.textContent = display || "Locked";
        return;
      }
      var sec = Math.floor(diff / 1000);
      var d = Math.floor(sec / 86400);
      sec -= d * 86400;
      var h = Math.floor(sec / 3600);
      sec -= h * 3600;
      var m = Math.floor(sec / 60);
      sec -= m * 60;
      var label =
        (d ? d + "d " : "") + pad(h) + ":" + pad(m) + ":" + pad(sec);
      if (out) out.textContent = label;
    });
  }

  setInterval(updateLockTimers, 1000);
  updateLockTimers();

  var cfg = window.BOWL_SIX;
  if (!cfg || !cfg.playersUrl) return;

  var poolBody = document.getElementById("bowl-six-pool-body");
  var searchEl = document.getElementById("bowl-six-search");
  var posEl = document.getElementById("bowl-six-pos-filter");
  var players = [];
  var activeSlot = null;
  var initialPickPlayers = cfg.pickPlayers || {};

  function playerPageUrl(pid) {
    var tpl = cfg.playerPageTpl || "";
    if (!tpl) return "#";
    return tpl.split(PLAYER_URL_PH).join(String(pid));
  }

  function playerById(pid) {
    var id = parseInt(pid, 10);
    if (!id) return null;
    var found = players.find(function (p) {
      return p.id === id;
    });
    if (found) return found;
    var slots = cfg.slots || [];
    var i;
    for (i = 0; i < slots.length; i++) {
      var slot = slots[i];
      var init = initialPickPlayers[slot];
      if (init && parseInt(init.id, 10) === id) return init;
    }
    return null;
  }

  function slotForPosition(posKind) {
    if (posKind === "gk") return "gk";
    if (posKind === "def") return ["def1", "def2"];
    return ["fwd1", "fwd2", "fwd3"];
  }

  function positionKindFromFilter(f) {
    return f || "";
  }

  function headshotImgHtml(p, className) {
    var cls = className || "stats-player-cell__photo";
    if (p && p.headshot_url) {
      return (
        '<img src="' +
        esc(p.headshot_url) +
        '" alt="" loading="lazy" decoding="async">'
      );
    }
    return '<span class="stats-player-cell__photo-ph" aria-hidden="true"></span>';
  }

  function playerLinksHtml(p, nameClass, photoClass) {
    var href = esc(playerPageUrl(p.id));
    var nCls = nameClass || "stats-player-cell__name";
    var pCls = photoClass || "stats-player-cell__photo";
    return (
      '<a class="' +
      pCls +
      '" href="' +
      href +
      '">' +
      headshotImgHtml(p, pCls) +
      "</a>" +
      '<a class="' +
      nCls +
      '" href="' +
      href +
      '">' +
      esc(p.name) +
      "</a>"
    );
  }

  function emptySlotHtml() {
    var jersey = cfg.jerseyEmptyUrl || "";
    return (
      '<img class="bowl-six-slot__jersey-img" src="' +
      esc(jersey) +
      '" alt="">' +
      '<span class="bowl-six-slot__placeholder">Pick</span>'
    );
  }

  function filledSlotHtml(p, slot) {
    var removeBtn = "";
    if (cfg.editable) {
      removeBtn =
        '<button type="button" class="bowl-six-slot__clear" data-slot="' +
        esc(slot) +
        '" aria-label="Remove ' +
        esc(p.name) +
        ' from lineup" title="Remove player">×</button>';
    }
    return (
      '<div class="bowl-six-slot__player stats-player-cell">' +
      '<a class="stats-player-cell__photo bowl-six-slot__photo" href="' +
      esc(playerPageUrl(p.id)) +
      '">' +
      headshotImgHtml(p, "stats-player-cell__photo bowl-six-slot__photo") +
      "</a>" +
      '<div class="bowl-six-slot__name-row">' +
      '<a class="stats-player-cell__name bowl-six-slot__name" href="' +
      esc(playerPageUrl(p.id)) +
      '">' +
      esc(p.name) +
      "</a>" +
      removeBtn +
      "</div></div>"
    );
  }

  function pickedPlayerIds() {
    var ids = {};
    document.querySelectorAll("[id^='slot-input-']").forEach(function (inp) {
      var pid = parseInt(inp.value, 10);
      if (pid) ids[pid] = true;
    });
    return ids;
  }

  function slotForPlayerId(pid) {
    var id = parseInt(pid, 10);
    if (!id) return null;
    var slots = cfg.slots || [];
    var i;
    for (i = 0; i < slots.length; i++) {
      var slot = slots[i];
      var inp = document.getElementById("slot-input-" + slot);
      if (inp && parseInt(inp.value, 10) === id) return slot;
    }
    return null;
  }

  function clearSlot(slot, opts) {
    var input = document.getElementById("slot-input-" + slot);
    if (!input || !cfg.editable) return;
    var pid = parseInt(input.value, 10);
    input.value = "";
    delete initialPickPlayers[slot];
    var capEl = document.getElementById("bowl-six-captain-id");
    if (capEl && pid && parseInt(capEl.value, 10) === pid) {
      capEl.value = "";
    }
    renderSlot(slot);
    if (!opts || !opts.skipPool) renderPool();
  }

  function renderSlot(slot) {
    var display = document.getElementById("slot-display-" + slot);
    var input = document.getElementById("slot-input-" + slot);
    if (!display || !input) return;
    var pid = parseInt(input.value, 10);
    var p = pid ? playerById(pid) : null;
    if (!p && initialPickPlayers[slot]) {
      p = initialPickPlayers[slot];
    }
    display.innerHTML = p ? filledSlotHtml(p, slot) : emptySlotHtml();
    if (p) {
      display.classList.add("bowl-six-slot__jersey--filled");
      var clearBtn = display.querySelector(".bowl-six-slot__clear");
      if (clearBtn) {
        clearBtn.addEventListener("click", function (ev) {
          ev.preventDefault();
          ev.stopPropagation();
          clearSlot(slot);
        });
      }
    } else {
      display.classList.remove("bowl-six-slot__jersey--filled");
    }
  }

  function renderAllSlots() {
    var slots = cfg.slots || [];
    slots.forEach(function (slot) {
      renderSlot(slot);
    });
    bindPoolHovers();
  }

  function assignPlayer(slot, player) {
    var otherSlot = slotForPlayerId(player.id);
    if (otherSlot && otherSlot !== slot) {
      clearSlot(otherSlot, { skipPool: true });
    }
    var input = document.getElementById("slot-input-" + slot);
    if (input) input.value = player.id;
    initialPickPlayers[slot] = {
      id: player.id,
      name: player.name,
      positions: player.positions || player.position || "",
      headshot_url: player.headshot_url || null,
    };
    renderSlot(slot);
  }

  function slotPlayerName(slot) {
    var input = document.getElementById("slot-input-" + slot);
    if (!input || !input.value) return "—";
    var p = playerById(input.value);
    return p ? p.name : "—";
  }

  function teamCounts() {
    var counts = {};
    document.querySelectorAll("[id^='slot-input-']").forEach(function (inp) {
      var pid = parseInt(inp.value, 10);
      if (!pid) return;
      var row = playerById(pid);
      if (row && row.team_id) {
        counts[row.team_id] = (counts[row.team_id] || 0) + 1;
      }
    });
    return counts;
  }

  function teamCountLabel(p, counts) {
    if (!p.team_id) return "—";
    var n = counts[p.team_id] || 0;
    return n + "/3";
  }

  function ratingPillHtml(val, style, decimals) {
    if (val == null || val === "" || isNaN(Number(val))) return "—";
    var label =
      decimals === 0
        ? String(Math.round(Number(val)))
        : Number(val).toFixed(1);
    if (style) {
      return (
        '<span class="stats-badge stats-badge--rating stats-badge--overall bowl-six-pool__rating" style="' +
        esc(style) +
        '">' +
        esc(label) +
        "</span>"
      );
    }
    return esc(label);
  }

  function ovrCellHtml(p) {
    if (p.ovr == null || isNaN(Number(p.ovr))) {
      return '<td class="bowl-six-pool__col-ovr" data-sort-value="">—</td>';
    }
    return (
      '<td class="bowl-six-pool__col-ovr" data-sort-value="' +
      esc(String(p.ovr)) +
      '"><span class="stats-ova__score">' +
      esc(String(p.ovr)) +
      "</span></td>"
    );
  }

  function ratingCellHtml(val, style, decimals, colClass) {
    var sortVal =
      val != null && val !== "" && !isNaN(Number(val)) ? String(Number(val)) : "";
    return (
      '<td class="' +
      colClass +
      '" data-sort-value="' +
      esc(sortVal) +
      '">' +
      ratingPillHtml(val, style, decimals) +
      "</td>"
    );
  }

  function poolSortValueNum(val) {
    return val != null && val !== "" && !isNaN(Number(val)) ? String(Number(val)) : "";
  }

  function playerCellHtml(p) {
    var href = esc(playerPageUrl(p.id));
    var blockedNote = p.blocked
      ? ' <span class="muted">(last week)</span>'
      : "";
    return (
      '<span class="stats-player-cell bowl-six-pool__player">' +
      '<a class="stats-player-cell__photo" href="' +
      href +
      '">' +
      headshotImgHtml(p) +
      "</a>" +
      '<span class="stats-player-cell__text">' +
      '<a class="stats-player-cell__name" href="' +
      href +
      '">' +
      esc(p.name) +
      "</a>" +
      blockedNote +
      "</span></span>"
    );
  }

  function bindPoolHovers() {
    if (typeof window.bindPlayerHoverAnchors === "function") {
      window.bindPlayerHoverAnchors();
    }
  }

  function renderPool() {
    if (!poolBody) return;
    var pf = positionKindFromFilter(posEl && posEl.value);
    var html = "";
    var counts = teamCounts();
    var pickedIds = pickedPlayerIds();
    var rows = [];
    players.forEach(function (p) {
      if (pickedIds[p.id]) return;
      if (pf && p.position_kind !== pf) return;
      rows.push(p);
    });
    rows.forEach(function (p) {
      var blocked = p.blocked;
      var teamFull = p.team_id && counts[p.team_id] >= 3;
      var dis = blocked || teamFull || !cfg.editable;
      var teamN = counts[p.team_id] || 0;
      var teamCls =
        "bowl-six-pool__team-count" +
        (teamN >= 3
          ? " bowl-six-pool__team-count--full"
          : teamN > 0
            ? " bowl-six-pool__team-count--active"
            : "");
      var pts =
        p.fantasy_points != null && !isNaN(p.fantasy_points)
          ? Number(p.fantasy_points).toFixed(1)
          : "—";
      var pickedPct = p.pick_pct != null ? p.pick_pct + "%" : "—";
      html +=
        '<tr class="' +
        (dis ? "bowl-six-pool__row--disabled" : "") +
        '"><td class="bowl-six-pool__col-pick"><input type="radio" name="pool_pick" value="' +
        p.id +
        '" data-pos="' +
        esc(p.position_kind) +
        '" ' +
        (dis ? "disabled" : "") +
        '"></td><td data-sort-value="' +
        esc((p.name || "").toLowerCase()) +
        '">' +
        playerCellHtml(p) +
        '</td><td class="bowl-six-pool__col-pos" data-sort-value="' +
        esc((p.positions || p.position || "").toLowerCase()) +
        '">' +
        esc(p.positions || p.position || "—") +
        "</td>" +
        ovrCellHtml(p) +
        ratingCellHtml(p.abi, p.abi_style, 1, "bowl-six-pool__col-rating") +
        ratingCellHtml(p.pot, p.pot_style, 1, "bowl-six-pool__col-rating") +
        '<td class="bowl-six-pool__col-team" data-sort-value="' +
        esc(String(teamN)) +
        '"><span class="' +
        teamCls +
        '" title="' +
        esc(p.team_name || "No team") +
        '">' +
        teamCountLabel(p, counts) +
        '</span></td><td class="bowl-six-pool__col-num" data-sort-value="' +
        esc(poolSortValueNum(p.fantasy_points)) +
        '">' +
        esc(pts) +
        '</td><td class="bowl-six-pool__col-num" data-sort-value="' +
        esc(poolSortValueNum(p.pick_pct != null ? p.pick_pct : "")) +
        '">' +
        esc(pickedPct) +
        "</td></tr>";
    });
    poolBody.innerHTML =
      html || '<tr><td colspan="9" class="muted">No players</td></tr>';
    poolBody.querySelectorAll("input[name=pool_pick]").forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (!activeSlot) {
          var pk = radio.getAttribute("data-pos");
          var slots = slotForPosition(pk);
          activeSlot = Array.isArray(slots)
            ? slots.find(function (s) {
                return !document.getElementById("slot-input-" + s).value;
              })
            : slots;
          if (!activeSlot) activeSlot = Array.isArray(slots) ? slots[0] : slots;
        }
        var pl = players.find(function (x) {
          return String(x.id) === radio.value;
        });
        if (pl && activeSlot) assignPlayer(activeSlot, pl);
        activeSlot = null;
        renderPool();
      });
    });
    bindPoolHovers();
  }

  function loadPlayers(searchText) {
    var url = cfg.playersUrl;
    var q = (searchText != null ? searchText : searchEl && searchEl.value) || "";
    q = String(q).trim();
    if (q) {
      url +=
        (url.indexOf("?") >= 0 ? "&" : "?") +
        "q=" +
        encodeURIComponent(q);
    }
    if (poolBody) {
      poolBody.innerHTML =
        '<tr><td colspan="9" class="muted">Loading…</td></tr>';
    }
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        players = (data.players || []).map(function (p) {
          return {
            id: p.id,
            name: p.name,
            team_id: p.team_id,
            team_name: p.team_name || "",
            blocked: p.blocked,
            position: p.position || "",
            positions: p.positions || p.position || "",
            position_kind: p.position_kind || "fwd",
            headshot_url: p.headshot_url || null,
            fantasy_points: p.fantasy_points,
            pick_pct: p.pick_pct,
            ovr: p.ovr,
            abi: p.abi,
            pot: p.pot,
            ovr_style: p.ovr_style || "",
            abi_style: p.abi_style || "",
            pot_style: p.pot_style || "",
          };
        });
        renderAllSlots();
        renderPool();
      })
      .catch(function () {
        renderAllSlots();
        if (poolBody) {
          poolBody.innerHTML =
            '<tr><td colspan="9">Failed to load players.</td></tr>';
        }
      });
  }

  document.querySelectorAll(".bowl-six-slot").forEach(function (slotEl) {
    slotEl.addEventListener("click", function () {
      activeSlot = slotEl.getAttribute("data-slot");
    });
  });

  var poolSearchTimer = null;
  if (searchEl) {
    searchEl.addEventListener("input", function () {
      clearTimeout(poolSearchTimer);
      poolSearchTimer = setTimeout(function () {
        loadPlayers(searchEl.value);
      }, 280);
    });
  }
  if (posEl) posEl.addEventListener("change", renderPool);

  var capBtn = document.getElementById("bowl-six-pick-captain");
  if (capBtn) {
    capBtn.addEventListener("click", function () {
      var picks = [];
      document.querySelectorAll("[id^='slot-input-']").forEach(function (inp) {
        if (inp.id.indexOf("gk") >= 0 && inp.value) return;
        if (inp.value) {
          picks.push({
            slot: inp.id.replace("slot-input-", ""),
            id: parseInt(inp.value, 10),
          });
        }
      });
      if (!picks.length) {
        alert("Select skaters first.");
        return;
      }
      var names = picks.map(function (p, i) {
        return i + 1 + ") " + slotPlayerName(p.slot);
      });
      var choice = prompt("Captain (enter number):\n" + names.join("\n"));
      var idx = parseInt(choice, 10) - 1;
      if (idx >= 0 && idx < picks.length) {
        document.getElementById("bowl-six-captain-id").value = picks[idx].id;
        alert("Captain set.");
      }
    });
  }

  renderAllSlots();
  loadPlayers();
})();
