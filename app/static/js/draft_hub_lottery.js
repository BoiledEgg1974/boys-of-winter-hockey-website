/**
 * BOWL-Relegation Draft Hub lottery panel (NHL 4-ball combo, two official draws).
 */
(function () {
  "use strict";

  var shell = document.getElementById("dh-lottery-shell");
  if (!shell) return;

  var lotteryUrl = shell.getAttribute("data-lottery-url") || "";
  var armUrl = shell.getAttribute("data-arm-url") || "";
  var drawUrl = shell.getAttribute("data-draw-url") || "";
  var resetUrl = shell.getAttribute("data-reset-url") || "";
  var csrf = shell.getAttribute("data-csrf") || "";
  var sfxBase = (shell.getAttribute("data-sfx-base") || "/static/sfx").replace(/\/$/, "");
  var isPreview = shell.getAttribute("data-preview") === "1";
  var practiceAllowed = shell.getAttribute("data-practice-allowed") === "1";
  var previewInitial = null;
  var previewLotto = null;

  var lastDrawCount = 0;
  var lastStatus = "";
  var pollTimer = null;
  var animating = false;
  var userCollapsed = false;
  var cagePhysics = null;
  var audioUnlocked = false;
  var sfx = {};

  function stopCagePhysics() {
    if (cagePhysics) {
      cagePhysics.destroy();
      cagePhysics = null;
    }
  }

  function startCagePhysics(live, locked) {
    stopCagePhysics();
    var cage = document.getElementById("dh-lottery-cage");
    if (!cage || typeof window.LotteryCageSim !== "function") return;
    var canvas = cage.querySelector(".dh-lottery-cage__canvas");
    if (!canvas) return;
    cagePhysics = new window.LotteryCageSim(canvas, { live: live, locked: locked });
    cagePhysics.start();
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function teamLabel(blob) {
    if (!blob) return "TBD";
    return blob.abbr || blob.name || "TBD";
  }

  function teamChip(blob, tradedFrom) {
    if (!blob) return "";
    var logo = blob.logo_url
      ? '<img class="dh-lottery-logo" src="' + esc(blob.logo_url) + '" alt="">'
      : "";
    var from = tradedFrom && tradedFrom.id && tradedFrom.id !== blob.id
      ? '<span class="dh-lottery-from">from ' + esc(teamLabel(tradedFrom)) + "</span>"
      : "";
    return (
      '<span class="dh-lottery-team">' +
      logo +
      "<span>" +
      esc(blob.name || teamLabel(blob)) +
      "</span>" +
      from +
      "</span>"
    );
  }

  function loadSfx() {
    ["lottery-tumble", "lottery-ball", "lottery-lock", "cheer"].forEach(function (name) {
      try {
        var a = new Audio(sfxBase + "/" + name + ".wav");
        a.preload = "auto";
        if (name === "lottery-tumble") a.loop = true;
        sfx[name] = a;
      } catch (_) {}
    });
  }

  function unlockAudio() {
    if (audioUnlocked) return;
    audioUnlocked = true;
    Object.keys(sfx).forEach(function (k) {
      var a = sfx[k];
      if (!a) return;
      try {
        a.volume = 0;
        var p = a.play();
        if (p && p.then) p.then(function () { a.pause(); a.currentTime = 0; a.volume = 1; }).catch(function () {});
      } catch (_) {}
    });
  }

  function playSfx(name) {
    var a = sfx[name];
    if (!a) return;
    try {
      a.currentTime = 0;
      a.volume = name === "lottery-tumble" ? 0.35 : 0.7;
      var p = a.play();
      if (p && p.catch) p.catch(function () {});
    } catch (_) {}
  }

  function stopTumble() {
    var a = sfx["lottery-tumble"];
    if (!a) return;
    try { a.pause(); a.currentTime = 0; } catch (_) {}
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ csrf_token: csrf }, body || {})),
    }).then(function (r) { return r.json(); });
  }

  function ballsHtml(values, emptyCount, slotPrefix) {
    var prefix = slotPrefix || "ball";
    var html = '<div class="dh-lottery-balls">';
    var i;
    if (values && values.length) {
      for (i = 0; i < values.length; i += 1) {
        html += '<span class="dh-lottery-ball" data-slot="' + prefix + "-" + i + '">' + esc(values[i]) + "</span>";
      }
    } else {
      for (i = 0; i < (emptyCount || 4); i += 1) {
        html += '<span class="dh-lottery-ball dh-lottery-ball--empty" data-slot="' + prefix + "-" + i + '"></span>';
      }
    }
    html += "</div>";
    return html;
  }

  function cageHtml(live, fast, locked) {
    var cls = "dh-lottery-cage";
    if (live) cls += " dh-lottery-cage--live";
    if (locked) cls += " dh-lottery-cage--locked";
    if (fast) cls += " dh-lottery-cage--fast";
    return (
      '<div class="' + cls + '" id="dh-lottery-cage">' +
      '<canvas class="dh-lottery-cage__canvas" aria-hidden="true"></canvas>' +
      '<div class="dh-lottery-cage__glass"></div>' +
      "</div>"
    );
  }

  function comboHelpHtml(lotto) {
    var example = null;
    var draws = (lotto && lotto.draws) || [];
    if (draws.length) {
      example = {
        balls: draws[0].combo_sorted || draws[0].combo || [],
        seed: draws[0].seed,
        team: draws[0].owner ? (draws[0].owner.name || teamLabel(draws[0].owner)) : "that team",
        pick: draws[0].pick || 1,
      };
    }
    var html =
      '<details class="dh-lottery-combo-help">' +
      '<summary class="dh-lottery-combo-help__title">How the four ball numbers work</summary>' +
      '<div class="dh-lottery-combo-help__body">' +
      "<p class=\"dh-lottery-combo-help__lead\">" +
      "The numbers on the balls are <strong>not</strong> draft picks, team ranks, or seed numbers. " +
      "They are just labels on 14 ping-pong balls sitting in the cage.</p>" +
      '<ol class="dh-lottery-combo-steps">' +
      "<li><strong>Four balls are drawn</strong> — any four of the 14 (for example: 5, 7, 8, 11).</li>" +
      "<li><strong>Sort them smallest → largest</strong> — that gives one unique combo: 5 — 7 — 8 — 11.</li>" +
      "<li><strong>Look up the combo</strong> — before the lottery, every possible 4-ball combo " +
      "(1,000 of them) was secretly assigned to exactly one lottery team. " +
      "Worse records get more combos, so they have better odds.</li>" +
      "<li><strong>That team wins the draw</strong> — whoever owns that combo wins pick #1 or #2. " +
      "Everyone else keeps their original seed order for the remaining lottery picks (3–16).</li>" +
      "</ol>" +
      '<div class="dh-lottery-combo-not">' +
      "<p><strong>Ball 5 does not mean</strong> pick #5, seed #5, or the 5th-worst team.</p>" +
      "<p><strong>Ball 10 does not mean</strong> pick #10 or the 10th team. It is only ping-pong ball #10.</p>" +
      "</div>";
    if (example && example.balls.length) {
      html +=
        '<div class="dh-lottery-combo-example">' +
        "<h4>Your Pick #" + esc(example.pick) + " result, in plain English</h4>" +
        "<p>Balls <strong>" + esc(example.balls.join(", ")) + "</strong> came out of the cage. " +
        "Sorted, that is combo <strong>" + esc(example.balls.join(" — ")) + "</strong>. " +
        "That combo was assigned to lottery seed <strong>#" + esc(example.seed) + "</strong>, " +
        "so <strong>" + esc(example.team) + "</strong> wins pick #" + esc(example.pick) + ".</p>" +
        "</div>";
    } else {
      html +=
        '<div class="dh-lottery-combo-example dh-lottery-combo-example--sample">' +
        "<h4>Example (before any draw)</h4>" +
        "<p>If balls <strong>2, 3, 7, 14</strong> are drawn, they become combo <strong>2 — 3 — 7 — 14</strong>. " +
        "Whichever team was pre-assigned that combo wins the draw — regardless of what the individual numbers look like.</p>" +
        "</div>";
    }
    html += "</div></details>";
    return html;
  }

  function comboResultCaption(draw, pickNum) {
    var balls = draw.combo_sorted || draw.combo || [];
    if (!balls.length) return "";
    var team = draw.owner ? (draw.owner.name || teamLabel(draw.owner)) : "the assigned team";
    return (
      '<p class="dh-lottery-result-caption">' +
      "Balls <strong>" + esc(balls.join(", ")) + "</strong> are cage labels only. " +
      "Combo <strong>" + esc(balls.join(" — ")) + "</strong> → seed #" + esc(draw.seed) + " → " +
      "<strong>" + esc(team) + "</strong> wins pick #" + esc(pickNum) + "." +
      "</p>"
    );
  }

  function drawCard(title, draw, pickNum, waiting) {
    var html = '<div class="dh-lottery-draw-card' + (draw ? " dh-lottery-draw-card--locked" : "") + '" data-pick="' + pickNum + '">';
    html += "<h3>" + esc(title) + "</h3>";
    if (draw) {
      html += ballsHtml(draw.combo || draw.combo_sorted, 4, "p" + pickNum);
      html += '<p class="dh-lottery-lockchip">✓ Pick #' + esc(draw.pick || pickNum) + " locked</p>";
      html +=
        '<p class="dh-lottery-result">' +
        esc((draw.combo_sorted || draw.combo || []).join(" — ")) +
        " → <strong>Seed " +
        esc(draw.seed) +
        "</strong>" +
        (draw.pick1_pct != null ? " · " + esc(draw.pick1_pct) + "% orig odds" : "") +
        "</p>";
      html += comboResultCaption(draw, pickNum);
      html += teamChip(draw.owner, draw.original);
    } else if (waiting) {
      html += '<p class="dh-lottery-waiting muted">Waiting for Pick #' + (pickNum - 1) + "…</p>";
      html += ballsHtml(null, 4, "p" + pickNum);
    } else {
      html += ballsHtml(null, 4, "p" + pickNum);
    }
    html += "</div>";
    return html;
  }

  function movementBadge(row, lotto) {
    if (!row.lottery || !row.seed || !(lotto.draws || []).length) return "";
    var delta = row.seed - row.overall;
    if (delta > 0) {
      return '<span class="dh-lottery-move dh-lottery-move--up">↑ ' + delta + "</span>";
    }
    if (delta < 0) {
      return '<span class="dh-lottery-move dh-lottery-move--down">↓ ' + Math.abs(delta) + "</span>";
    }
    return '<span class="dh-lottery-move dh-lottery-move--flat">—</span>';
  }

  function orderListHtml(lotto) {
    if (!lotto.round1_order || !lotto.round1_order.length) return "";
    var hasDraws = (lotto.draws || []).length > 0;
    var html = '<div class="dh-lottery-order-wrap">';
    html += '<h3 class="dh-lottery-order-title">First-round order';
    if (hasDraws) {
      html += ' <span class="dh-lottery-order-legend"><span class="dh-lottery-move dh-lottery-move--up">↑</span> moved up · <span class="dh-lottery-move dh-lottery-move--down">↓</span> bumped down</span>';
    }
    html += "</h3>";
    html += '<ol class="dh-lottery-order">';
    lotto.round1_order.forEach(function (row) {
      var delta = row.lottery && row.seed ? row.seed - row.overall : 0;
      var cls = "";
      if (hasDraws && row.lottery && row.seed) {
        if (delta > 0) cls = " dh-lottery-order__item--up";
        else if (delta < 0) cls = " dh-lottery-order__item--down";
      }
      html +=
        "<li class='dh-lottery-order__item" + cls + "'>" +
        "<span class='dh-lottery-order-n'>" + esc(row.overall) + "</span> " +
        teamChip(row.owner, row.original) +
        (row.lottery && row.seed ? " <span class='muted small'>seed #" + esc(row.seed) + "</span>" : "") +
        movementBadge(row, lotto) +
        "</li>";
    });
    html += "</ol></div>";
    return html;
  }

  function oddsTable(seeds) {
    if (!seeds || !seeds.length) return "";
    var picks = (seeds[0].pick_pcts || []).length || 16;
    var html =
      '<div class="dh-lottery-odds-wrap"><table class="dh-lottery-odds"><thead><tr>' +
      "<th>Team</th>";
    var i;
    for (i = 1; i <= picks; i += 1) html += "<th>P" + i + "</th>";
    html += "<th>Avg</th></tr></thead><tbody>";
    seeds.forEach(function (s) {
      html += "<tr><th>" + esc(teamLabel(s.owner)) + " <span class='muted'>#" + esc(s.seed) + "</span>";
      if (s.traded) html += " <span class='dh-lottery-from'>from " + esc(teamLabel(s.original)) + "</span>";
      html += "</th>";
      (s.pick_pcts || []).forEach(function (pct) {
        html += "<td>" + (pct ? esc(pct) + "%" : "") + "</td>";
      });
      html += "<td class='dh-lottery-avg'>" + esc(s.avg) + "</td></tr>";
    });
    html += "</tbody></table></div>";
    return html;
  }

  function render(data) {
    if (animating) return;
    var lotto = data && data.lottery ? data.lottery : data;
    if (!lotto || !lotto.enabled) {
      shell.hidden = true;
      return;
    }
    shell.hidden = false;
    var complete = !!lotto.complete;
    var expanded = !userCollapsed;
    var draws = lotto.draws || [];
    var canAdmin = !!lotto.can_admin;
    var nextPick = draws.length + 1;
    var canDraw = canAdmin && lotto.armed && !complete && nextPick <= (lotto.draw_count || 2);
    var cageLive = !!lotto.armed;

    var headline = "Stand by";
    var sub = "Awaiting the commissioner";
    var hint = lotto.preview
      ? "Preview only — official draws here are simulated in your browser and do not change any draft."
      : "This page updates when the commish spins the drum — keep it open, don't refresh.";
    if (complete) {
      headline = "Lottery locked";
      sub = "First-round order is set";
      hint = "Both official draws are in. Review the results below — hide the panel when you're ready to draft.";
    } else if (draws.length === 1) {
      headline = "Pick #1 locked";
      sub = "Awaiting pick #2";
    } else if (!lotto.armed) {
      headline = "Stand by";
      sub = "Lottery field is being set";
      hint = "The commissioner will arm the 16-team field from the worst-to-first ranking.";
    }

    var html = '<section class="dh-lottery-panel' + (expanded ? " is-open" : "") + '">';
    html +=
      '<button type="button" class="dh-lottery-toggle" id="dh-lottery-toggle">' +
      (expanded ? "Hide lottery" : "Show draft lottery") +
      (complete ? " · complete" : "") +
      "</button>";
    if (!expanded) {
      html += '<p class="dh-lottery-summary-line">' + esc(headline) + " — " + esc(sub) + "</p></section>";
      stopCagePhysics();
      shell.innerHTML = html;
      bind(lotto);
      return;
    }

    html += '<div class="dh-lottery-hero">';
    html += cageHtml(cageLive, false, complete);
    html += '<div class="dh-lottery-reveal-layer" id="dh-lottery-reveal-layer"></div>';
    html += '<p class="dh-lottery-kicker">BOWL-Relegation · NHL format' + (lotto.preview ? " · Preview" : "") + "</p>";
    html += '<h2 class="dh-lottery-title">' + esc(headline) + "</h2>";
    html += '<p class="dh-lottery-sub">♦ ' + esc(sub) + " ♦</p>";
    html += '<p class="dh-lottery-hint">' + esc(hint) + "</p>";
    html += "</div>";

    html += '<div class="dh-lottery-official dh-lottery-official--row">';
    html += drawCard("Official · Pick #1", draws[0], 1, false);
    html += drawCard("Official · Pick #2", draws[1], 2, !draws[0] && !complete);
    html += "</div>";

    html += comboHelpHtml(lotto);

    if (canDraw) {
      html +=
        '<div class="dh-lottery-admin">' +
        '<button type="button" class="dh-lottery-btn dh-lottery-btn--draw" id="dh-lottery-draw">Draw for Pick #' +
        nextPick +
        "</button>";
      if (lotto.armed && !complete) {
        html +=
          '<button type="button" class="dh-lottery-btn dh-lottery-btn--ghost" id="dh-lottery-reset">Reset lottery</button>';
      }
      html += "</div>";
    } else if (canAdmin && !lotto.armed) {
      html +=
        '<div class="dh-lottery-admin">' +
        '<button type="button" class="dh-lottery-btn dh-lottery-btn--draw" id="dh-lottery-arm">Arm lottery from draft order</button>' +
        "</div>";
    }

    if (practiceAllowed) {
      html +=
        '<div class="dh-lottery-practice">' +
        "<h3>Practice draw · 4-ball combo (2 picks max)</h3>" +
        "<p>Side-by-side practice runs. Does not affect the real lottery.</p>" +
        '<div class="dh-lottery-practice-row" id="dh-lottery-practice-root"></div>' +
        '<button type="button" class="dh-lottery-btn dh-lottery-btn--ghost" id="dh-lottery-practice-reset">Reset practice</button>' +
        "</div>";
    } else if (!canAdmin) {
      html +=
        '<p class="dh-lottery-hint muted">Log in as a GM to try practice draws.</p>';
    }

    html += orderListHtml(lotto);
    html += oddsTable(lotto.seeds);
    html += "</section>";
    stopCagePhysics();
    shell.innerHTML = html;
    bind(lotto);
    if (practiceAllowed) {
      renderPractice(lotto);
    }
    startCagePhysics(cageLive, complete);
  }

  var practice = { draws: [] };

  function randomCombo(lotto, excludedSeeds) {
    var map = lotto.combo_to_seed || {};
    var keys = Object.keys(map).filter(function (k) {
      return excludedSeeds.indexOf(map[k]) === -1;
    });
    if (!keys.length) {
      var balls = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14];
      var picked = [];
      while (picked.length < 4) {
        var b = balls[Math.floor(Math.random() * balls.length)];
        if (picked.indexOf(b) === -1) picked.push(b);
      }
      return { combo: picked.sort(function (a, b) { return a - b; }), seed: 1 };
    }
    for (var i = 0; i < 40; i += 1) {
      var key = keys[Math.floor(Math.random() * keys.length)];
      if (key === lotto.unused_combo) continue;
      return { combo: key.split("-").map(function (n) { return parseInt(n, 10); }), seed: map[key] };
    }
    var fallback = keys[0];
    return { combo: fallback.split("-").map(function (n) { return parseInt(n, 10); }), seed: map[fallback] };
  }

  function seedByNumber(lotto, seed) {
    var list = lotto.seeds || [];
    for (var i = 0; i < list.length; i += 1) {
      if (list[i].seed === seed) return list[i];
    }
    return null;
  }

  function practiceCard(pickNum, draw, lotto, disabled) {
    var html = '<div class="dh-lottery-draw-card" data-practice-pick="' + pickNum + '">';
    html += "<h3>Practice · Pick #" + pickNum + "</h3>";
    if (draw) {
      html += ballsHtml(draw.combo, 4, "pr" + pickNum);
      html += '<p class="dh-lottery-lockchip">✓ Pick #' + pickNum + " locked</p>";
      html +=
        '<p class="dh-lottery-result">' +
        esc(draw.combo.join(" — ")) +
        " → <strong>Seed " + esc(draw.seed) + "</strong>" +
        (draw.pct != null ? " · " + esc(draw.pct) + "% orig odds" : "") +
        "</p>";
      var seedRow = seedByNumber(lotto, draw.seed);
      var pseudoDraw = {
        combo_sorted: draw.combo,
        seed: draw.seed,
        owner: seedRow ? seedRow.owner : null,
      };
      html += comboResultCaption(pseudoDraw, pickNum);
    } else {
      html += ballsHtml(null, 4, "pr" + pickNum);
      html +=
        '<button type="button" class="dh-lottery-btn dh-lottery-btn--draw" data-practice-draw="' +
        pickNum +
        '"' +
        (disabled ? " disabled" : "") +
        ">Draw for Pick #" +
        pickNum +
        "</button>";
    }
    html += "</div>";
    return html;
  }

  function renderPractice(lotto) {
    var root = document.getElementById("dh-lottery-practice-root");
    if (!root) return;
    var d1 = practice.draws[0];
    var d2 = practice.draws[1];
    root.innerHTML =
      practiceCard(1, d1, lotto, false) +
      practiceCard(2, d2, lotto, !d1);
  }

  function setCageFast(on) {
    if (cagePhysics) cagePhysics.setFast(on);
    var cage = document.getElementById("dh-lottery-cage");
    if (!cage) return;
    if (on) cage.classList.add("dh-lottery-cage--fast");
    else cage.classList.remove("dh-lottery-cage--fast");
  }

  function animateComboReveal(combo, pickNum, cardSelector, done) {
    animating = true;
    setCageFast(true);
    playSfx("lottery-tumble");

    var sel = cardSelector || ('.dh-lottery-draw-card[data-pick="' + pickNum + '"]');
    var card = shell.querySelector(sel);
    if (!card) {
      animating = false;
      setCageFast(false);
      stopTumble();
      if (done) done();
      return;
    }

    var slots = card.querySelectorAll(".dh-lottery-ball");
    var layer = document.getElementById("dh-lottery-reveal-layer");
    var cage = document.getElementById("dh-lottery-cage");
    var idx = 0;

    function flyOne() {
      if (idx >= combo.length) {
        stopTumble();
        setCageFast(false);
        playSfx("lottery-lock");
        window.setTimeout(function () {
          playSfx("cheer");
          animating = false;
          if (done) done();
        }, 220);
        return;
      }

      var num = combo[idx];
      var slot = slots[idx];
      if (!layer || !cage || !slot) {
        idx += 1;
        flyOne();
        return;
      }

      var floater = document.createElement("span");
      floater.className = "dh-lottery-reveal-ball";
      floater.textContent = String(num);
      layer.appendChild(floater);

      var cageRect = cage.getBoundingClientRect();
      var slotRect = slot.getBoundingClientRect();
      var layerRect = layer.getBoundingClientRect();
      var startX = cageRect.left + cageRect.width / 2 - layerRect.left - 18;
      var startY = cageRect.top + cageRect.height * 0.38 - layerRect.top - 18;
      var endX = slotRect.left + slotRect.width / 2 - layerRect.left - 18;
      var endY = slotRect.top + slotRect.height / 2 - layerRect.top - 18;

      floater.style.left = startX + "px";
      floater.style.top = startY + "px";
      floater.style.transform = "scale(0.4)";
      floater.style.opacity = "0";

      requestAnimationFrame(function () {
        floater.style.transition = "left 0.55s cubic-bezier(.2,.9,.2,1), top 0.55s cubic-bezier(.2,.9,.2,1), transform 0.55s ease, opacity 0.2s ease";
        floater.style.opacity = "1";
        floater.style.transform = "scale(1.35)";
        floater.style.left = startX + "px";
        floater.style.top = (startY - 28) + "px";
        window.setTimeout(function () {
          floater.style.transform = "scale(1)";
          floater.style.left = endX + "px";
          floater.style.top = endY + "px";
        }, 180);
      });

      window.setTimeout(function () {
        slot.textContent = String(num);
        slot.classList.remove("dh-lottery-ball--empty");
        if (floater.parentNode) floater.parentNode.removeChild(floater);
        playSfx("lottery-ball");
        idx += 1;
        window.setTimeout(flyOne, 320);
      }, 780);
    }

    window.setTimeout(flyOne, 400);
  }

  function runDrawAnimation(combo, pickNum, lotto, cardSelector, onComplete) {
    render({ lottery: lotto });
    window.setTimeout(function () {
      animateComboReveal(combo, pickNum, cardSelector, function () {
        if (onComplete) onComplete();
      });
    }, 50);
  }

  function recomputePreviewOrder(lotto) {
    var winners = lotto.draws.map(function (d) { return d.seed; });
    var wonSet = {};
    winners.forEach(function (s) { wonSet[s] = true; });
    var bySeed = {};
    lotto.seeds.forEach(function (s) { bySeed[s.seed] = s; });
    var head = winners.map(function (s) { return bySeed[s]; }).filter(Boolean);
    var rest = lotto.seeds.filter(function (s) { return !wonSet[s.seed]; });
    var tail = (previewInitial && previewInitial.round1_order)
      ? previewInitial.round1_order.filter(function (r) { return !r.lottery; })
      : [];
    var ordered = head.concat(rest, tail);
    return ordered.map(function (row, idx) {
      return {
        overall: idx + 1,
        seed: row.seed,
        original_team_id: row.original_team_id,
        owner_team_id: row.owner_team_id,
        lottery: row.seed <= lotto.team_count,
        owner: row.owner,
        original: row.original,
        traded: !!row.traded,
      };
    });
  }

  function previewOfficialDraw(lotto) {
    var excluded = lotto.draws.map(function (d) { return d.seed; });
    var rolled = randomCombo(lotto, excluded);
    var seedRow = seedByNumber(lotto, rolled.seed);
    var pick = lotto.draws.length + 1;
    lotto.draws.push({
      pick: pick,
      combo: rolled.combo.slice(),
      combo_sorted: rolled.combo.slice().sort(function (a, b) { return a - b; }),
      seed: rolled.seed,
      original_team_id: seedRow ? seedRow.original_team_id : null,
      owner_team_id: seedRow ? seedRow.owner_team_id : null,
      combo_count: seedRow ? seedRow.combo_count : null,
      pick1_pct: seedRow ? seedRow.pick1_pct : null,
      owner: seedRow ? seedRow.owner : null,
      original: seedRow ? seedRow.original : null,
    });
    if (lotto.draws.length >= lotto.draw_count) {
      lotto.status = "complete";
      lotto.complete = true;
    } else {
      lotto.status = "locked_1";
      lotto.complete = false;
    }
    lotto.round1_order = recomputePreviewOrder(lotto);
    return { combo: rolled.combo.slice(), pick: pick };
  }

  function bind(lotto) {
    var toggle = document.getElementById("dh-lottery-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var isOpen = !!shell.querySelector(".dh-lottery-panel.is-open");
        userCollapsed = isOpen;
        render({ lottery: lotto });
      });
    }
    var drawBtn = document.getElementById("dh-lottery-draw");
    if (drawBtn) {
      drawBtn.addEventListener("click", function () {
        unlockAudio();
        drawBtn.disabled = true;
        if (isPreview && previewLotto) {
          var pendingPick = previewLotto.draws.length + 1;
          var rolled = randomCombo(
            previewLotto,
            previewLotto.draws.map(function (d) { return d.seed; })
          );
          runDrawAnimation(rolled.combo, pendingPick, previewLotto, null, function () {
            previewOfficialDraw(previewLotto);
            render({ lottery: previewLotto });
            drawBtn.disabled = false;
          });
          return;
        }
        postJson(drawUrl, {})
          .then(function (res) {
            if (!res || !res.ok) {
              drawBtn.disabled = false;
              window.alert((res && res.error) || "Draw failed.");
              return;
            }
            var combo = (res.result && res.result.combo) || [];
            var pickNum = res.result && res.result.pick ? res.result.pick : (res.lottery.draws || []).length;
            var lottoBefore = JSON.parse(JSON.stringify(res.lottery));
            if (lottoBefore.draws && lottoBefore.draws.length) lottoBefore.draws.pop();
            runDrawAnimation(combo, pickNum, lottoBefore, null, function () {
              render(res);
              window.dispatchEvent(new CustomEvent("dh-lottery-updated"));
              drawBtn.disabled = false;
            });
          })
          .catch(function () {
            drawBtn.disabled = false;
          });
      });
    }
    var armBtn = document.getElementById("dh-lottery-arm");
    if (armBtn) {
      armBtn.addEventListener("click", function () {
        armBtn.disabled = true;
        postJson(armUrl, {})
          .then(function (res) {
            if (!res || !res.ok) {
              armBtn.disabled = false;
              window.alert((res && res.error) || "Could not arm lottery.");
              return;
            }
            render(res);
          })
          .catch(function () { armBtn.disabled = false; });
      });
    }
    var resetBtn = document.getElementById("dh-lottery-reset");
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (isPreview && previewInitial) {
          previewLotto = JSON.parse(JSON.stringify(previewInitial));
          lastDrawCount = 0;
          lastStatus = previewLotto.status;
          practice.draws = [];
          render({ lottery: previewLotto });
          return;
        }
        if (!window.confirm("Reset the official lottery and restore the pre-lottery first round?")) return;
        postJson(resetUrl, {}).then(function (res) {
          if (!res || !res.ok) {
            window.alert((res && res.error) || "Reset failed.");
            return;
          }
          lastDrawCount = 0;
          practice.draws = [];
          render(res);
          window.dispatchEvent(new CustomEvent("dh-lottery-updated"));
        });
      });
    }
    var pracReset = document.getElementById("dh-lottery-practice-reset");
    if (pracReset) {
      pracReset.addEventListener("click", function () {
        practice.draws = [];
        renderPractice(lotto);
      });
    }
    var pracRoot = document.getElementById("dh-lottery-practice-root");
    if (pracRoot) {
      pracRoot.addEventListener("click", function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest("[data-practice-draw]") : null;
        if (!btn || btn.disabled) return;
        unlockAudio();
        var pick = parseInt(btn.getAttribute("data-practice-draw"), 10);
        if (pick === 2 && !practice.draws[0]) return;
        if (practice.draws[pick - 1]) return;
        var excluded = practice.draws.map(function (d) { return d.seed; });
        var rolled = randomCombo(lotto, excluded);
        var seedRow = seedByNumber(lotto, rolled.seed);
        runDrawAnimation(
          rolled.combo,
          pick,
          lotto,
          '.dh-lottery-draw-card[data-practice-pick="' + pick + '"]',
          function () {
            practice.draws[pick - 1] = {
              combo: rolled.combo.slice().sort(function (a, b) { return a - b; }),
              seed: rolled.seed,
              pct: seedRow ? seedRow.pick1_pct : null,
            };
            renderPractice(lotto);
          }
        );
      });
    }
  }

  function poll() {
    if (!lotteryUrl || animating) return;
    fetch(lotteryUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        var lotto = res && res.lottery ? res.lottery : null;
        if (!lotto) return;
        var count = (lotto.draws || []).length;
        if (count > lastDrawCount && lastStatus) {
          lastDrawCount = count;
          lastStatus = lotto.status;
          var newest = lotto.draws[count - 1];
          var combo = (newest && newest.combo) || [];
          var pickNum = newest && newest.pick ? newest.pick : count;
          var lottoBefore = JSON.parse(JSON.stringify(lotto));
          if (lottoBefore.draws && lottoBefore.draws.length) lottoBefore.draws.pop();
          runDrawAnimation(combo, pickNum, lottoBefore, null, function () {
            render({ lottery: lotto });
            window.dispatchEvent(new CustomEvent("dh-lottery-updated"));
          });
          return;
        }
        if (lotto.status !== lastStatus || count !== lastDrawCount || !shell.innerHTML) {
          lastDrawCount = count;
          lastStatus = lotto.status;
          render({ lottery: lotto });
        }
        if (lotto.complete && pollTimer) {
          window.clearInterval(pollTimer);
          pollTimer = null;
        }
      })
      .catch(function () {});
  }

  loadSfx();
  document.addEventListener("pointerdown", unlockAudio, { once: true });
  if (isPreview) {
    var dataEl = document.getElementById("dh-lottery-preview-data");
    if (dataEl) {
      try {
        previewInitial = JSON.parse(dataEl.textContent || "{}");
        previewLotto = JSON.parse(JSON.stringify(previewInitial));
        lastStatus = previewLotto.status || "pending";
        shell.hidden = false;
        render({ lottery: previewLotto });
      } catch (_) {
        shell.hidden = true;
      }
    }
  } else {
    poll();
    pollTimer = window.setInterval(poll, 1500);
  }
})();
