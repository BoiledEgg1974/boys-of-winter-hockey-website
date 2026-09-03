/**
 * BOWL Boost Lottery scratch tickets — extras round before the pick-number draw.
 * Odds match app/services/boost_scratch.py (33/33/34, +2 at 15/10/5).
 */
(function () {
  "use strict";

  var GOLD_P = 0.33;
  var SILVER_P = 0.33;
  var PLUS_TWO_FIRST = 0.15;
  var PLUS_TWO_SECOND = 0.1;
  var PLUS_TWO_AFTER = 0.05;
  var START_TICKETS = 3;
  var MAX_TICKETS = 50;
  var CLEAR_THRESHOLD = 0.55;
  var MUTE_KEY = "boost-scratch-mute";

  var root = document.querySelector("[data-boost-scratch]");
  if (!root) return;

  var initial = parseInitial();
  var role = initial.role || root.getAttribute("data-role") || "gm";
  var canLive = role === "admin";
  var goldSrc = initial.goldSrc || "";
  var silverSrc = initial.silverSrc || "";
  var csrfEl = document.getElementById("bs-csrf");
  var csrf = (csrfEl && csrfEl.value) || "";

  var ticketsHost = document.getElementById("bs-tickets");
  var bannerEl = document.getElementById("bs-banner");
  var statusEl = document.getElementById("bs-status");
  var statsEl = document.getElementById("bs-stats");
  var btnAll = document.getElementById("bs-scratch-all");
  var btnReset = document.getElementById("bs-reset");
  var btnResetLive = document.getElementById("bs-reset-live");
  var btnMute = document.getElementById("bs-mute");
  var btnPractice = document.getElementById("bs-mode-practice");
  var btnLive = document.getElementById("bs-mode-live");
  var elGoldN = document.getElementById("bl-gold-n");
  var elSilverN = document.getElementById("bl-silver-n");
  var elDrawTotals = document.getElementById("bl-draw-totals");

  var persistedTickets = Array.isArray(initial.tickets) ? initial.tickets.slice() : [];
  var persistedGold = intOr(initial.extraGold, 0);
  var persistedSilver = intOr(initial.extraSilver, 0);
  var liveComplete = Boolean(initial.complete) || persistedTickets.length > 0;

  var isLive = canLive && liveComplete;
  var locked = false;
  var tickets = [];
  var reducedMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var muted = false;
  try {
    muted = window.localStorage.getItem(MUTE_KEY) === "1";
  } catch (err) {
    muted = false;
  }

  var audioCtx = null;
  var scratchGain = null;
  var scratchSource = null;

  function parseInitial() {
    var el = document.getElementById("boost-scratch-initial");
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || "{}") || {};
    } catch (err) {
      return {};
    }
  }

  function intOr(v, fallback) {
    var n = parseInt(String(v), 10);
    return Number.isFinite(n) ? n : fallback;
  }

  function readInt(el, fallback) {
    var n = parseInt(String(el && el.value).trim(), 10);
    return Number.isFinite(n) ? n : fallback;
  }

  function plusTwoRate(index) {
    if (index <= 0) return PLUS_TWO_FIRST;
    if (index === 1) return PLUS_TWO_SECOND;
    return PLUS_TWO_AFTER;
  }

  function rollPrize() {
    var roll = Math.random();
    if (roll < GOLD_P) return "gold";
    if (roll < GOLD_P + SILVER_P) return "silver";
    return "nothing";
  }

  function rollTicket(index) {
    var prize = rollPrize();
    var plusTwo = Math.random() < plusTwoRate(index);
    if (plusTwo && prize === "nothing") {
      prize = Math.random() < 0.5 ? "gold" : "silver";
    }
    return { prize: prize, plus_two: plusTwo, revealed: false };
  }

  function tally(list) {
    var gold = 0;
    var silver = 0;
    var i;
    for (i = 0; i < list.length; i++) {
      if (!list[i].revealed) continue;
      if (list[i].prize === "gold") gold += 1;
      if (list[i].prize === "silver") silver += 1;
    }
    return { gold: gold, silver: silver };
  }

  function allRevealed() {
    return tickets.length > 0 && tickets.every(function (t) {
      return t.revealed;
    });
  }

  function baselineGold() {
    return Math.max(0, readInt(elGoldN, intOr(initial.baselineGold, 4)));
  }

  function baselineSilver() {
    return Math.max(0, readInt(elSilverN, intOr(initial.baselineSilver, 6)));
  }

  function liveExtras() {
    if (isLive && !liveComplete) return tally(tickets);
    return { gold: persistedGold, silver: persistedSilver };
  }

  function drawGold() {
    return baselineGold() + liveExtras().gold;
  }

  function drawSilver() {
    return baselineSilver() + liveExtras().silver;
  }

  function syncDrawCounts() {
    var g = drawGold();
    var s = drawSilver();
    if (elGoldN) elGoldN.setAttribute("data-draw-gold", String(g));
    if (elSilverN) elSilverN.setAttribute("data-draw-silver", String(s));
    if (elDrawTotals) {
      elDrawTotals.textContent = "Draw totals: " + g + " gold · " + s + " silver";
    }
    var extras = liveExtras();
    if (bannerEl) {
      bannerEl.textContent =
        "Baseline " +
        baselineGold() +
        " gold / " +
        baselineSilver() +
        " silver + extras +" +
        extras.gold +
        "G / +" +
        extras.silver +
        "S = draw " +
        g +
        " gold, " +
        s +
        " silver";
    }
  }

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg || "";
  }

  function updateStats() {
    var gold = 0;
    var silver = 0;
    var nothing = 0;
    var bonus = 0;
    var i;
    for (i = 0; i < tickets.length; i++) {
      if (!tickets[i].revealed) continue;
      if (tickets[i].prize === "gold") gold += 1;
      else if (tickets[i].prize === "silver") silver += 1;
      else nothing += 1;
      if (tickets[i].plus_two) bonus += 1;
    }
    if (statsEl) {
      statsEl.textContent =
        "This board: " +
        gold +
        " gold · " +
        silver +
        " silver · " +
        nothing +
        " nothing · " +
        bonus +
        " bonus tickets";
    }
  }

  function updateModeUi() {
    if (btnPractice) {
      btnPractice.classList.toggle("is-active", !isLive);
      btnPractice.setAttribute("aria-pressed", !isLive ? "true" : "false");
    }
    if (btnLive) {
      btnLive.classList.toggle("is-active", isLive);
      btnLive.setAttribute("aria-pressed", isLive ? "true" : "false");
    }
    if (btnResetLive) {
      btnResetLive.hidden = !(canLive && (isLive || liveComplete));
    }
    root.classList.toggle("boost-scratch--live", isLive);
    root.classList.toggle("boost-scratch--locked", locked);
    if (btnAll) btnAll.disabled = locked;
    if (btnReset) {
      btnReset.disabled = locked && isLive;
      btnReset.textContent = isLive ? "New live board" : "Reset";
    }
  }

  function updateMuteUi() {
    if (!btnMute) return;
    btnMute.setAttribute("aria-pressed", muted ? "true" : "false");
    btnMute.textContent = muted ? "Sound off" : "Mute";
  }

  function ensureAudio() {
    if (muted || reducedMotion) return null;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    if (!audioCtx) audioCtx = new Ctx();
    if (audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function playStinger(kind) {
    var ctx = ensureAudio();
    if (!ctx) return;
    var now = ctx.currentTime;
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    if (kind === "gold") {
      osc.type = "triangle";
      osc.frequency.setValueAtTime(523.25, now);
      osc.frequency.exponentialRampToValueAtTime(783.99, now + 0.18);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.12, now + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.42);
      osc.start(now);
      osc.stop(now + 0.44);
    } else if (kind === "silver") {
      osc.type = "sine";
      osc.frequency.setValueAtTime(392, now);
      osc.frequency.exponentialRampToValueAtTime(523.25, now + 0.16);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.09, now + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.32);
      osc.start(now);
      osc.stop(now + 0.34);
    } else if (kind === "plus") {
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(220, now);
      osc.frequency.exponentialRampToValueAtTime(660, now + 0.28);
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.exponentialRampToValueAtTime(0.06, now + 0.04);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.36);
      osc.start(now);
      osc.stop(now + 0.38);
    } else {
      osc.type = "square";
      osc.frequency.setValueAtTime(110, now);
      gain.gain.setValueAtTime(0.05, now);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
      osc.start(now);
      osc.stop(now + 0.2);
    }
  }

  function startScratchNoise() {
    var ctx = ensureAudio();
    if (!ctx || scratchSource) return;
    var buffer = ctx.createBuffer(1, ctx.sampleRate * 1, ctx.sampleRate);
    var data = buffer.getChannelData(0);
    var i;
    for (i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
    scratchSource = ctx.createBufferSource();
    scratchSource.buffer = buffer;
    scratchSource.loop = true;
    var filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.value = 1800;
    filter.Q.value = 0.7;
    scratchGain = ctx.createGain();
    scratchGain.gain.value = 0.045;
    scratchSource.connect(filter);
    filter.connect(scratchGain);
    scratchGain.connect(ctx.destination);
    scratchSource.start();
  }

  function stopScratchNoise() {
    if (scratchSource) {
      try {
        scratchSource.stop();
      } catch (err) {
        /* already stopped */
      }
      scratchSource.disconnect();
      scratchSource = null;
    }
    if (scratchGain) {
      scratchGain.disconnect();
      scratchGain = null;
    }
  }

  function serialFor(index) {
    return "BOWL-" + String(1000 + index * 17 + ((index * 91) % 97)).slice(-4);
  }

  function prizeMarkup(ticket) {
    if (ticket.prize === "gold") {
      return (
        '<img src="' +
        escapeAttr(goldSrc) +
        '" alt="" class="boost-scratch-ticket__badge" width="88" height="88">' +
        '<span class="boost-scratch-ticket__prize-label boost-scratch-ticket__prize-label--gold">Gold boost</span>'
      );
    }
    if (ticket.prize === "silver") {
      return (
        '<img src="' +
        escapeAttr(silverSrc) +
        '" alt="" class="boost-scratch-ticket__badge" width="88" height="88">' +
        '<span class="boost-scratch-ticket__prize-label boost-scratch-ticket__prize-label--silver">Silver boost</span>'
      );
    }
    return (
      '<span class="boost-scratch-ticket__void" aria-hidden="true"></span>' +
      '<span class="boost-scratch-ticket__prize-label">No boost</span>'
    );
  }

  function escapeAttr(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function paintFoil(canvas) {
    var ctx = canvas.getContext("2d");
    var w = canvas._cssW || 160;
    var h = canvas._cssH || 140;
    ctx.globalCompositeOperation = "source-over";
    var g = ctx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, "#8b9bb0");
    g.addColorStop(0.35, "#d7dee8");
    g.addColorStop(0.55, "#9aa8ba");
    g.addColorStop(1, "#5c6b7e");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    var i;
    for (i = 0; i < 80; i++) {
      ctx.fillStyle = "rgba(255,255,255," + (0.04 + Math.random() * 0.08) + ")";
      ctx.fillRect(Math.random() * w, Math.random() * h, 8 + Math.random() * 18, 1);
    }
    ctx.fillStyle = "rgba(20, 28, 40, 0.55)";
    ctx.font = "700 13px Bebas Neue, Impact, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("SCRATCH HERE", w / 2, h / 2);
  }

  function sizeCanvas(canvas, wrap) {
    var rect = wrap.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(120, Math.floor(rect.width));
    var h = Math.max(110, Math.floor(rect.height));
    if (rect.width < 40 && !canvas._sized) {
      canvas._sized = true;
      window.requestAnimationFrame(function () {
        sizeCanvas(canvas, wrap);
      });
      return;
    }
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    var ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    canvas._cssW = w;
    canvas._cssH = h;
    paintFoil(canvas);
  }

  function clearPercent(canvas) {
    var ctx = canvas.getContext("2d");
    var w = canvas.width;
    var h = canvas.height;
    if (!w || !h) return 0;
    var data = ctx.getImageData(0, 0, w, h).data;
    var clear = 0;
    var samples = 0;
    var i;
    for (i = 3; i < data.length; i += 32) {
      samples += 1;
      if (data[i] < 48) clear += 1;
    }
    return samples ? clear / samples : 0;
  }

  function scratchAt(canvas, cssX, cssY, radius) {
    var ctx = canvas.getContext("2d");
    var dpr = window.devicePixelRatio || 1;
    ctx.save();
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = "destination-out";
    ctx.beginPath();
    ctx.arc(cssX, cssY, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function spawnSparks(wrap, x, y) {
    if (reducedMotion) return;
    var spark = document.createElement("span");
    spark.className = "boost-scratch-spark";
    spark.style.left = x + "px";
    spark.style.top = y + "px";
    wrap.appendChild(spark);
    window.setTimeout(function () {
      spark.remove();
    }, 280);
  }

  function finishFoil(ticket) {
    if (!ticket.canvas) return;
    ticket.canvas.style.opacity = "0";
    ticket.canvas.style.pointerEvents = "none";
    ticket.el.classList.add("is-revealed");
    ticket.el.classList.add("is-revealed--" + ticket.prize);
    if (ticket.plus_two) ticket.el.classList.add("is-bonus");
  }

  function appendBonusTicket() {
    if (tickets.length >= MAX_TICKETS) return;
    var next = rollTicket(tickets.length);
    tickets.push(next);
    mountTicket(next, tickets.length - 1, true);
    setStatus("Bonus ticket added.");
  }

  function onReveal(ticket) {
    if (ticket.revealed) return;
    ticket.revealed = true;
    finishFoil(ticket);
    playStinger(ticket.prize);
    if (ticket.plus_two) {
      window.setTimeout(function () {
        playStinger("plus");
      }, 120);
      appendBonusTicket();
    }
    updateStats();
    if (isLive) syncDrawCounts();
    if (allRevealed()) onBoardComplete();
  }

  function onBoardComplete() {
    locked = isLive;
    updateModeUi();
    if (!isLive) {
      setStatus("Practice board finished — reset to try again. This does not affect the lottery.");
      return;
    }
    liveComplete = true;
    persistedTickets = tickets.map(function (t) {
      return { prize: t.prize, plus_two: t.plus_two };
    });
    var extras = tally(tickets);
    persistedGold = extras.gold;
    persistedSilver = extras.silver;
    syncDrawCounts();
    setStatus("Live extras locked. Execute the pick draw with the new gold/silver totals.");
    persistLive();
  }

  function persistLive() {
    if (!canLive || !initial.saveUrl) return;
    fetch(initial.saveUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({
        csrf_token: csrf,
        tickets: persistedTickets,
      }),
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          setStatus((res.data && res.data.error) || "Could not save live extras.");
          return;
        }
        persistedGold = intOr(res.data.extra_gold, persistedGold);
        persistedSilver = intOr(res.data.extra_silver, persistedSilver);
        syncDrawCounts();
        window.dispatchEvent(new CustomEvent("dh-boost-lottery-updated"));
      })
      .catch(function () {
        setStatus("Could not save live extras.");
      });
  }

  function resetLiveOnServer() {
    if (!canLive || !initial.resetUrl) return Promise.resolve();
    return fetch(initial.resetUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({ csrf_token: csrf }),
    }).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error((data && data.error) || "Reset failed");
        return data;
      });
    });
  }

  function bindTicketScratch(ticket) {
    var canvas = ticket.canvas;
    var wrap = ticket.foilWrap;
    if (!canvas || !wrap) return;
    var drawing = false;

    function localPos(ev) {
      var rect = canvas.getBoundingClientRect();
      var src = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
      return { x: src.clientX - rect.left, y: src.clientY - rect.top };
    }

    function go(ev) {
      if (locked || ticket.revealed) return;
      ev.preventDefault();
      var pos = localPos(ev);
      scratchAt(canvas, pos.x, pos.y, reducedMotion ? 48 : 18);
      spawnSparks(wrap, pos.x, pos.y);
      if (clearPercent(canvas) >= CLEAR_THRESHOLD || reducedMotion) {
        stopScratchNoise();
        onReveal(ticket);
      }
    }

    function down(ev) {
      if (locked || ticket.revealed) return;
      drawing = true;
      startScratchNoise();
      go(ev);
    }

    function move(ev) {
      if (!drawing) return;
      go(ev);
    }

    function up() {
      drawing = false;
      stopScratchNoise();
    }

    if (window.PointerEvent) {
      canvas.addEventListener("pointerdown", function (ev) {
        if (canvas.setPointerCapture) canvas.setPointerCapture(ev.pointerId);
        down(ev);
      });
      canvas.addEventListener("pointermove", move);
      canvas.addEventListener("pointerup", up);
      canvas.addEventListener("pointercancel", up);
    } else {
      canvas.addEventListener("touchstart", down, { passive: false });
      canvas.addEventListener("touchmove", move, { passive: false });
      canvas.addEventListener("touchend", up);
      canvas.addEventListener("mousedown", down);
      canvas.addEventListener("mousemove", move);
      canvas.addEventListener("mouseup", up);
      canvas.addEventListener("mouseleave", up);
    }
    ticket.el.addEventListener("keydown", function (ev) {
      if (locked || ticket.revealed) return;
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        onReveal(ticket);
      }
    });
  }

  function mountTicket(ticket, index, animateIn) {
    var el = document.createElement("article");
    el.className = "boost-scratch-ticket";
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.setAttribute("aria-label", "Scratch ticket " + (index + 1));
    if (animateIn && !reducedMotion) el.classList.add("boost-scratch-ticket--enter");
    el.innerHTML =
      '<div class="boost-scratch-ticket__paper">' +
      '<div class="boost-scratch-ticket__serial">' +
      escapeAttr(serialFor(index)) +
      " · INSTANT</div>" +
      '<div class="boost-scratch-ticket__well">' +
      '<div class="boost-scratch-ticket__prize">' +
      prizeMarkup(ticket) +
      (ticket.plus_two
        ? '<span class="boost-scratch-ticket__bonus">Bonus ticket</span>'
        : "") +
      "</div>" +
      '<div class="boost-scratch-ticket__foil-wrap">' +
      '<canvas class="boost-scratch-ticket__foil" aria-hidden="true"></canvas>' +
      "</div>" +
      "</div>" +
      "</div>";
    ticketsHost.appendChild(el);
    ticket.el = el;
    ticket.foilWrap = el.querySelector(".boost-scratch-ticket__foil-wrap");
    ticket.canvas = el.querySelector("canvas");
    sizeCanvas(ticket.canvas, ticket.foilWrap);
    if (ticket.revealed) {
      finishFoil(ticket);
    } else {
      bindTicketScratch(ticket);
    }
  }

  function clearBoard() {
    tickets = [];
    if (ticketsHost) ticketsHost.innerHTML = "";
    locked = false;
  }

  function startFreshBoard() {
    clearBoard();
    var i;
    for (i = 0; i < START_TICKETS; i++) {
      tickets.push(rollTicket(i));
    }
    tickets.forEach(function (ticket, index) {
      mountTicket(ticket, index, false);
    });
    updateStats();
    updateModeUi();
    if (isLive) {
      liveComplete = false;
      setStatus("Live board is ready — scratch to add extras on top of the baseline.");
    } else {
      setStatus("Practice board — results stay on this page only.");
    }
  }

  function showPersistedLive() {
    clearBoard();
    locked = true;
    liveComplete = true;
    isLive = true;
    tickets = persistedTickets.map(function (t) {
      return { prize: t.prize, plus_two: Boolean(t.plus_two), revealed: true };
    });
    tickets.forEach(function (ticket, index) {
      mountTicket(ticket, index, false);
    });
    updateStats();
    updateModeUi();
    syncDrawCounts();
    setStatus("Live extras are locked from the last completed session.");
  }

  function scratchAll() {
    if (locked) return;
    function step() {
      var next = null;
      var i;
      for (i = 0; i < tickets.length; i++) {
        if (!tickets[i].revealed) {
          next = tickets[i];
          break;
        }
      }
      if (!next || locked) return;
      onReveal(next);
      if (!allRevealed() && !locked) {
        window.setTimeout(step, reducedMotion ? 0 : 160);
      }
    }
    step();
  }

  function setLiveMode(nextLive) {
    if (!canLive) return;
    if (nextLive === isLive && tickets.length) return;
    isLive = nextLive;
    if (isLive && liveComplete && persistedTickets.length) {
      showPersistedLive();
      return;
    }
    startFreshBoard();
    syncDrawCounts();
  }

  function onReset() {
    if (isLive && locked) {
      setStatus("Live extras are locked. Use Reset live extras to return to the 4/6 baseline.");
      return;
    }
    startFreshBoard();
    if (isLive) syncDrawCounts();
    else updateStats();
  }

  function onResetLive() {
    if (!canLive) return;
    if (!window.confirm("Clear live scratch extras and return draw totals to the baseline?")) {
      return;
    }
    resetLiveOnServer()
      .then(function () {
        persistedTickets = [];
        persistedGold = 0;
        persistedSilver = 0;
        liveComplete = false;
        isLive = true;
        startFreshBoard();
        syncDrawCounts();
        setStatus("Live extras cleared. Scratch a new live board when ready.");
      })
      .catch(function (err) {
        setStatus(err.message || "Could not reset live extras.");
      });
  }

  if (btnAll) btnAll.addEventListener("click", scratchAll);
  if (btnReset) btnReset.addEventListener("click", onReset);
  if (btnResetLive) btnResetLive.addEventListener("click", onResetLive);
  if (btnMute) {
    btnMute.addEventListener("click", function () {
      muted = !muted;
      if (muted) stopScratchNoise();
      try {
        window.localStorage.setItem(MUTE_KEY, muted ? "1" : "0");
      } catch (err) {
        /* ignore */
      }
      updateMuteUi();
    });
  }
  if (btnPractice) {
    btnPractice.addEventListener("click", function () {
      setLiveMode(false);
    });
  }
  if (btnLive) {
    btnLive.addEventListener("click", function () {
      setLiveMode(true);
    });
  }
  if (elGoldN) elGoldN.addEventListener("input", syncDrawCounts);
  if (elSilverN) elSilverN.addEventListener("input", syncDrawCounts);

  window.addEventListener("resize", function () {
    tickets.forEach(function (ticket) {
      if (!ticket.canvas || !ticket.foilWrap || ticket.revealed) return;
      sizeCanvas(ticket.canvas, ticket.foilWrap);
    });
  });

  updateMuteUi();
  if (canLive && liveComplete && persistedTickets.length) {
    showPersistedLive();
  } else {
    startFreshBoard();
    syncDrawCounts();
  }
})();
