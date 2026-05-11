(function () {
  var root = document.getElementById("ai-trade-tool-root");
  if (!root) return;
  var assetsUrl = root.getAttribute("data-assets-url") || "";
  var evaluateUrl = root.getAttribute("data-evaluate-url") || "";
  var csrf = root.getAttribute("data-csrf") || "";
  var maxN = parseInt(root.getAttribute("data-max") || "5", 10) || 5;
  var draftRoundCap = parseInt(root.getAttribute("data-draft-rounds") || "8", 10) || 8;
  var partnerSel = document.getElementById("partner-team-select");
  var leftPanels = document.getElementById("left-asset-panels");
  var rightPanels = document.getElementById("right-asset-panels");
  var poolMpleft = document.getElementById("pool-mpleft");
  var poolMpright = document.getElementById("pool-mpright");
  var selLeftRound = document.getElementById("sel-draft-left-round");
  var selRightRound = document.getElementById("sel-draft-right-round");
  var rightPh = document.getElementById("right-placeholder");
  var rightLoaded = document.getElementById("right-team-loaded");
  var rightLogo = document.getElementById("right-team-logo");
  var rightName = document.getElementById("right-teamname");
  var rightGm = document.getElementById("right-gm");
  var lblLeaveRight = document.getElementById("lbl-leave-right");
  var lblSendLeft = document.getElementById("lbl-send-left");
  var lblSendRight = document.getElementById("lbl-send-right");
  var lblLeaveLeft = document.getElementById("lbl-leave-left");
  var ledgerField = document.getElementById("ledger-json-field");
  var notesEl = document.getElementById("trade-notes");
  var bubbleVerdict = document.getElementById("ai-bubble-verdict");
  var bubbleOpinion = document.getElementById("ai-bubble-opinion");
  var bubbleSug = document.getElementById("ai-bubble-suggestions");
  var bubblePh = document.getElementById("ai-bubble-placeholder");
  var bubbleLoad = document.getElementById("ai-bubble-loading");
  var btnEval = document.getElementById("btn-ai-evaluate");
  var botDialog = document.getElementById("ai-trade-bot-dialog");
  var btnDialogClose = document.getElementById("ai-trade-dialog-close");
  var cached = null;
  var ledger = { from_left_to_right: [], from_right_to_left: [] };
  var dragKey = null;
  var PLAYER_URL_PH = "988776655";

  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }

  function playerPageUrl(pid) {
    var tpl = root.getAttribute("data-player-page-tpl") || "";
    if (!tpl) return "#";
    return tpl.split(PLAYER_URL_PH).join(String(pid));
  }

  function fmtDec(v) {
    if (v === null || v === undefined || v === "") return "—";
    var n = parseFloat(v);
    if (isNaN(n)) return "—";
    return n.toFixed(1);
  }

  function tradePlayerRowHtml(it, side, compact) {
    var pk = esc(it.drag_key);
    var href = esc(playerPageUrl(it.id));
    var img = it.headshot_url
      ? '<img src="' + esc(it.headshot_url) + '" alt="" loading="lazy" decoding="async">'
      : '<span class="stats-player-cell__photo-ph" aria-hidden="true"></span>';
    var abiS = (it.abi_style && String(it.abi_style)) || "";
    var potS = (it.pot_style && String(it.pot_style)) || "";
    var cls = "trade-tool-player-row trade-tool-chip" + (compact ? " trade-tool-player-row--compact" : "");
    var pos = esc((it.positions || it.position || "—").trim());
    var ovr = "—";
    if (it.ovr !== null && it.ovr !== undefined && it.ovr !== "") {
      var on = parseFloat(it.ovr);
      ovr = !isNaN(on) ? String(Math.round(on)) : String(it.ovr);
    }
    return (
      '<div class="' +
      cls +
      '" draggable="true" data-drag-key="' +
      pk +
      '" data-side="' +
      esc(side) +
      '">' +
      '<span class="stats-player-cell trade-tool-player-row__cell">' +
      '<a class="stats-player-cell__photo" href="' +
      href +
      '">' +
      img +
      "</a>" +
      '<span class="stats-player-cell__text">' +
      '<a class="stats-player-cell__name" href="' +
      href +
      '">' +
      esc(it.label) +
      "</a>" +
      '<span class="stats-player-cell__pos">' +
      pos +
      "</span>" +
      "</span>" +
      "</span>" +
      '<span class="trade-tool-player-row__ratings">' +
      '<span class="stats-badge stats-badge--rating stats-badge--overall" style="' +
      esc(abiS) +
      '" title="ABI">' +
      fmtDec(it.abi) +
      "</span>" +
      '<span class="stats-badge stats-badge--rating stats-badge--overall" style="' +
      esc(potS) +
      '" title="POT">' +
      fmtDec(it.pot) +
      "</span>" +
      '<span class="stats-ova__score trade-tool-ova" title="OVR">' +
      esc(ovr) +
      "</span>" +
      "</span></div>"
    );
  }

  function bindTradeHovers() {
    if (typeof window.bindPlayerHoverAnchors === "function") window.bindPlayerHoverAnchors();
  }

  function partnerOptionLabel(tid) {
    var opts = partnerSel.querySelectorAll("option[value]");
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].value === String(tid)) return (opts[i].textContent || "").trim();
    }
    return "Partner";
  }

  function fillRoundSelects(cap) {
    draftRoundCap = Math.max(1, Math.min(32, cap));
    root.setAttribute("data-draft-rounds", String(draftRoundCap));
    [selLeftRound, selRightRound].forEach(function (sel) {
      if (!sel) return;
      var v = sel.value;
      sel.innerHTML = "";
      for (var r = 1; r <= draftRoundCap; r++) {
        var o = document.createElement("option");
        o.value = String(r);
        o.textContent = "Round " + r;
        sel.appendChild(o);
      }
      if (v && parseInt(v, 10) >= 1 && parseInt(v, 10) <= draftRoundCap) sel.value = v;
    });
  }
  fillRoundSelects(draftRoundCap);

  function randSlug() {
    var hex = "0123456789abcdef";
    var s = "";
    for (var i = 0; i < 8; i++) s += hex[Math.floor(Math.random() * 16)];
    return s;
  }

  function appendManualPick(poolEl, prefix, round) {
    if (!poolEl) return;
    var slug = randSlug();
    var key = prefix + ":" + round + ":" + slug;
    var label = "Round " + round + " (manual)";
    poolEl.insertAdjacentHTML("beforeend", chipHtml(key, label, "PICK", prefix));
  }

  function clearManualPools() {
    if (poolMpleft) poolMpleft.innerHTML = "";
    if (poolMpright) poolMpright.innerHTML = "";
  }

  function syncLedgerField() {
    if (ledgerField) ledgerField.value = JSON.stringify(ledger);
  }

  function chipHtml(key, label, pos, side) {
    return (
      '<div class="trade-tool-chip" draggable="true" data-drag-key="' + esc(key) + '" data-side="' + esc(side) + '">' +
      '<span class="trade-tool-chip__pos">' + esc(pos) + "</span>" +
      '<span class="trade-tool-chip__name">' + esc(label) + "</span></div>"
    );
  }

  function renderList(title, items, side) {
    var h = '<div class="trade-tool-asset-block"><div class="trade-tool-asset-block__title">' + esc(title) + "</div>";
    if (!items || !items.length) {
      h += '<p class="muted trade-tool-empty">None</p>';
    } else {
      h += '<div class="trade-tool-chip-list trade-tool-chip-list--players">';
      for (var i = 0; i < items.length; i++) {
        var it = items[i];
        if (ledger.from_left_to_right.indexOf(it.drag_key) >= 0) continue;
        if (ledger.from_right_to_left.indexOf(it.drag_key) >= 0) continue;
        if (it.kind === "player") {
          h += tradePlayerRowHtml(it, side, false);
        } else {
          h += chipHtml(it.drag_key, it.label, it.position, side);
        }
      }
      h += "</div>";
    }
    h += "</div>";
    return h;
  }

  function partitionUnsigned(items) {
    var rights = [];
    var other = [];
    (items || []).forEach(function (it) {
      if (it && it.section === "rights") rights.push(it);
      else other.push(it);
    });
    return { rights: rights, other: other };
  }

  function renderSidebars() {
    if (!cached) return;
    var lu = partitionUnsigned(cached.left.unsigned);
    var ru = partitionUnsigned(cached.right.unsigned);
    leftPanels.innerHTML =
      renderList("Rostered players", cached.left.roster, "left") +
      renderList("Org rights (export / non-roster)", lu.rights, "left") +
      renderList("Prospects & unsigned (DB)", lu.other, "left");
    rightPanels.innerHTML =
      renderList("Rostered players", cached.right.roster, "right") +
      renderList("Org rights (export / non-roster)", ru.rights, "right") +
      renderList("Prospects & unsigned (DB)", ru.other, "right");
    bindTradeHovers();
  }

  function findPlayerItem(key) {
    if (!cached || key.indexOf("player:") !== 0) return null;
    var pools = [cached.left.roster, cached.left.unsigned, cached.right.roster, cached.right.unsigned];
    for (var i = 0; i < pools.length; i++) {
      for (var j = 0; j < pools[i].length; j++) {
        if (pools[i][j].drag_key === key) return pools[i][j];
      }
    }
    return null;
  }

  function renderZones() {
    ["from_left_to_right", "from_right_to_left"].forEach(function (z) {
      var el = document.getElementById("zone-" + z);
      if (!el) return;
      var html = "";
      var isLeftZone = z === "from_left_to_right";
      ledger[z].forEach(function (key) {
        if (key.indexOf("player:") === 0) {
          var pit = findPlayerItem(key);
          if (pit) {
            html += tradePlayerRowHtml(pit, isLeftZone ? "ledger-l" : "ledger-r", true);
            return;
          }
        }
        var meta = describeKey(key);
        html += chipHtml(key, meta.label, meta.pos, isLeftZone ? "ledger-l" : "ledger-r");
      });
      el.innerHTML = html;
    });
    prunePoolsAfterZoneRender();
    syncLedgerField();
    bindTradeHovers();
  }

  function prunePoolsAfterZoneRender() {
    [poolMpleft, poolMpright].forEach(function (pool) {
      if (!pool) return;
      pool.querySelectorAll(".trade-tool-chip").forEach(function (chip) {
        var k = chip.getAttribute("data-drag-key");
        if (k && (ledger.from_left_to_right.indexOf(k) >= 0 || ledger.from_right_to_left.indexOf(k) >= 0)) {
          chip.remove();
        }
      });
    });
  }

  function describeKey(key) {
    if (key.indexOf("mpleft:") === 0 || key.indexOf("mpright:") === 0) {
      var p = key.split(":");
      var rd = p.length > 1 ? p[1] : "?";
      return { label: "Round " + rd + " (manual pick)", pos: "PICK" };
    }
    if (!cached) return { label: key, pos: "—" };
    function scan(pool) {
      for (var i = 0; i < pool.length; i++) {
        if (pool[i].drag_key === key)
          return { label: pool[i].label, pos: pool[i].positions || pool[i].position };
      }
      return null;
    }
    var pools = [cached.left.roster, cached.left.unsigned, cached.right.roster, cached.right.unsigned];
    for (var j = 0; j < pools.length; j++) {
      var f = scan(pools[j]);
      if (f) return f;
    }
    return { label: key, pos: "—" };
  }

  root.addEventListener(
    "dragstart",
    function (ev) {
      var chip = ev.target && ev.target.closest ? ev.target.closest(".trade-tool-chip[draggable='true']") : null;
      if (!chip || !root.contains(chip)) return;
      dragKey = chip.getAttribute("data-drag-key");
      ev.dataTransfer.setData("text/plain", dragKey || "");
      ev.dataTransfer.effectAllowed = "move";
    },
    false
  );

  function isManualLeftKey(k) {
    return k.indexOf("mpleft:") === 0;
  }
  function isManualRightKey(k) {
    return k.indexOf("mpright:") === 0;
  }

  function isKeyFromLeft(k) {
    if (isManualLeftKey(k)) return true;
    if (isManualRightKey(k)) return false;
    function has(pool) {
      for (var j = 0; j < pool.length; j++) if (pool[j].drag_key === k) return true;
      return false;
    }
    if (!cached) return true;
    return has(cached.left.roster) || has(cached.left.unsigned);
  }

  function applyDrop(zoneName, k) {
    if (!k) return;
    var prev = null;
    if (ledger.from_left_to_right.indexOf(k) >= 0) prev = "from_left_to_right";
    else if (ledger.from_right_to_left.indexOf(k) >= 0) prev = "from_right_to_left";
    if (prev === zoneName) return;
    var fromLeft = isKeyFromLeft(k);
    var allowed =
      (zoneName === "from_left_to_right" && fromLeft) ||
      (zoneName === "from_right_to_left" && !fromLeft);
    if (!allowed) return;
    var countAfterRemove = ledger[zoneName].filter(function (x) {
      return x !== k;
    }).length;
    if (prev !== zoneName && countAfterRemove >= maxN) return;
    ledger.from_left_to_right = ledger.from_left_to_right.filter(function (x) {
      return x !== k;
    });
    ledger.from_right_to_left = ledger.from_right_to_left.filter(function (x) {
      return x !== k;
    });
    ledger[zoneName].push(k);
    [poolMpleft, poolMpright].forEach(function (pool) {
      if (!pool) return;
      pool.querySelectorAll(".trade-tool-chip").forEach(function (chip) {
        if (chip.getAttribute("data-drag-key") === k) chip.remove();
      });
    });
    renderSidebars();
    renderZones();
  }

  function setupDrop(zoneName) {
    var body = document.getElementById("zone-" + zoneName);
    if (!body) return;
    body.addEventListener("dragover", function (e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    });
    body.addEventListener("drop", function (e) {
      e.preventDefault();
      var k = (e.dataTransfer.getData("text/plain") || dragKey || "").trim();
      applyDrop(zoneName, k);
    });
    body.addEventListener("dblclick", function (e) {
      var chip = e.target && e.target.closest ? e.target.closest(".trade-tool-chip") : null;
      if (!chip || !body.contains(chip)) return;
      var k = chip.getAttribute("data-drag-key");
      ledger.from_left_to_right = ledger.from_left_to_right.filter(function (x) {
        return x !== k;
      });
      ledger.from_right_to_left = ledger.from_right_to_left.filter(function (x) {
        return x !== k;
      });
      renderSidebars();
      renderZones();
    });
  }

  setupDrop("from_left_to_right");
  setupDrop("from_right_to_left");

  function setupPoolDblclick(pool) {
    if (!pool) return;
    pool.addEventListener("dblclick", function (e) {
      var chip = e.target.closest ? e.target.closest(".trade-tool-chip") : null;
      if (!chip || !pool.contains(chip)) return;
      var k = chip.getAttribute("data-drag-key");
      if (ledger.from_left_to_right.indexOf(k) >= 0 || ledger.from_right_to_left.indexOf(k) >= 0) return;
      chip.remove();
    });
  }
  setupPoolDblclick(poolMpleft);
  setupPoolDblclick(poolMpright);

  document.getElementById("btn-add-draft-left").addEventListener("click", function () {
    var r = parseInt(selLeftRound && selLeftRound.value, 10) || 1;
    appendManualPick(poolMpleft, "mpleft", r);
  });
  document.getElementById("btn-add-draft-right").addEventListener("click", function () {
    var r = parseInt(selRightRound && selRightRound.value, 10) || 1;
    appendManualPick(poolMpright, "mpright", r);
  });

  document.getElementById("btn-clear-table").addEventListener("click", function () {
    ledger = { from_left_to_right: [], from_right_to_left: [] };
    clearManualPools();
    renderSidebars();
    renderZones();
    resetBubble();
  });

  function resetBubble() {
    if (botDialog && botDialog.open) botDialog.close();
    if (bubbleVerdict) {
      bubbleVerdict.hidden = true;
      bubbleVerdict.textContent = "";
    }
    if (bubbleOpinion) bubbleOpinion.textContent = "";
    if (bubbleSug) {
      bubbleSug.hidden = true;
      bubbleSug.innerHTML = "";
    }
    if (bubblePh) bubblePh.hidden = false;
    if (bubbleLoad) bubbleLoad.hidden = true;
  }

  function setBubbleLoading(on) {
    if (bubbleLoad) bubbleLoad.hidden = !on;
    if (bubblePh) bubblePh.hidden = on;
    if (btnEval) btnEval.disabled = !!on;
    if (on && botDialog && typeof botDialog.showModal === "function") {
      try {
        if (!botDialog.open) botDialog.showModal();
      } catch (err) {
        /* e.g. nested modal / browser restriction */
      }
    }
  }

  function updateLedgerLabels(myTeamName, partnerName) {
    var pShort = (partnerName || "partner").split("(")[0].trim();
    var mShort = (myTeamName || "your team").split("(")[0].trim();
    lblLeaveLeft.textContent = "Leaving " + mShort;
    lblSendRight.textContent = "Sending to " + pShort;
    lblLeaveRight.textContent = "Leaving " + pShort;
    lblSendLeft.textContent = "Sending to " + mShort;
  }

  if (btnDialogClose && botDialog) {
    btnDialogClose.addEventListener("click", function () {
      if (botDialog.open) botDialog.close();
    });
  }
  if (botDialog) {
    botDialog.addEventListener("click", function (e) {
      if (e.target === botDialog) botDialog.close();
    });
  }

  partnerSel.addEventListener("change", function () {
    var tid = partnerSel.value;
    if (!tid) {
      cached = null;
      rightPanels.innerHTML = "";
      clearManualPools();
      rightPh.classList.remove("is-hidden");
      rightLoaded.classList.add("is-hidden");
      if (rightLogo) rightLogo.removeAttribute("src");
      var myHead0 = root.querySelector(".trade-tool-col--left .trade-tool-teamname");
      updateLedgerLabels(myHead0 ? myHead0.textContent : "", "partner");
      ledger = { from_left_to_right: [], from_right_to_left: [] };
      renderZones();
      resetBubble();
      return;
    }
    rightPh.classList.add("is-hidden");
    rightLoaded.classList.remove("is-hidden");
    var optLab = partnerOptionLabel(tid);
    var sep = assetsUrl.indexOf("?") >= 0 ? "&" : "?";
    fetch(assetsUrl + sep + "partner_team_id=" + encodeURIComponent(tid), { credentials: "same-origin" })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        var data = res.data;
        if (!res.ok || data.error) {
          alert(data.error || "Could not load assets.");
          partnerSel.value = "";
          rightPh.classList.remove("is-hidden");
          rightLoaded.classList.add("is-hidden");
          return;
        }
        cached = data;
        if (data.player_page_url_template)
          root.setAttribute("data-player-page-tpl", data.player_page_url_template);
        ledger = { from_left_to_right: [], from_right_to_left: [] };
        clearManualPools();
        if (typeof data.draft_round_cap === "number") fillRoundSelects(data.draft_round_cap);
        var ptn = data.partner_team_name || optLab.split("(")[0].trim();
        rightName.textContent = ptn;
        rightGm.textContent = data.partner_gm_name || "";
        if (rightLogo && data.partner_logo_url) {
          rightLogo.src = data.partner_logo_url;
          rightLogo.alt = ptn;
        }
        var myHead = root.querySelector(".trade-tool-col--left .trade-tool-teamname");
        updateLedgerLabels(myHead ? myHead.textContent.trim() : "", ptn);
        renderSidebars();
        renderZones();
        resetBubble();
      })
      .catch(function () {
        alert("Could not load assets.");
      });
  });

  function renderOpinion(data) {
    if (bubblePh) bubblePh.hidden = true;
    if (bubbleLoad) bubbleLoad.hidden = true;
    if (bubbleVerdict) {
      bubbleVerdict.textContent = data.verdict || "Verdict";
      bubbleVerdict.hidden = false;
    }
    if (bubbleOpinion) {
      bubbleOpinion.textContent = data.opinion || "";
    }
    if (bubbleSug && Array.isArray(data.suggestions) && data.suggestions.length) {
      bubbleSug.innerHTML =
        "<li>" +
        data.suggestions
          .map(function (s) {
            return esc(s);
          })
          .join("</li><li>") +
        "</li>";
      bubbleSug.hidden = false;
    } else if (bubbleSug) {
      bubbleSug.hidden = true;
      bubbleSug.innerHTML = "";
    }
  }

  if (btnEval) {
    btnEval.addEventListener("click", function () {
      syncLedgerField();
      if (!partnerSel.value) {
        alert("Choose a trading partner.");
        return;
      }
      if (!evaluateUrl) {
        alert("Evaluate URL missing.");
        return;
      }
      setBubbleLoading(true);
      if (bubbleVerdict) bubbleVerdict.hidden = true;
      if (bubbleOpinion) bubbleOpinion.textContent = "";
      if (bubbleSug) {
        bubbleSug.hidden = true;
        bubbleSug.innerHTML = "";
      }
      fetch(evaluateUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          // Flask-WTF only reads CSRF from form fields or these headers—not JSON body.
          "X-CSRFToken": csrf,
        },
        body: JSON.stringify({
          csrf_token: csrf,
          partner_team_id: parseInt(partnerSel.value, 10),
          ledger: ledger,
          notes: (notesEl && notesEl.value) || "",
        }),
      })
        .then(function (r) {
          return r.json().then(function (data) {
            return { ok: r.ok, data: data };
          });
        })
        .then(function (res) {
          setBubbleLoading(false);
          if (!res.ok) {
            var msg = (res.data && res.data.error) ? res.data.error : "Request failed.";
            if (res.data && res.data.details) {
              msg += "\n\n" + res.data.details;
            }
            alert(msg);
            if (bubblePh) bubblePh.hidden = false;
            return;
          }
          renderOpinion(res.data);
        })
        .catch(function () {
          setBubbleLoading(false);
          alert("Network error.");
          if (bubblePh) bubblePh.hidden = false;
        });
    });
  }
})();
