/**
 * BOWL-Fantasy admin draft lottery — two draws max, Matter.js physics.
 * Finish slot s (1=17th..8=24th) spawns 8*s balls (17th→8, 24th→64). Reorder: winner up to +5
 * cumulative vs start; leapfrogged teams +1 drop each event, max +2 drops vs start.
 *
 * Draw flow: balls spawn inside a rotating drum → Release → Open hatch
 * exposes hatch; first ball whose on-screen position overlaps the bottom hit band wins (yellow bar is visual-only above controls).
 * Teams that already won an earlier draw are omitted from the next prepare (no balls).
 * After the last draw, the first MAX_DRAWS slots stay as the lottery left them; picks below that
 * are filled worst regular-season finish first (24th → next slot, … 17th → last).
 */
(function () {
  "use strict";

  var W = 800;
  var H = 960;
  var BALL_R = 7;
  var BALLS_MULTIPLIER = 8;
  var MAX_DRAWS = 2;
  var MAX_RISE = 5;
  var MAX_DROP = 2;

  /** Drum geometry (world space matches Matter canvas 800×960). */
  var WHEEL_CX = 400;
  var WHEEL_CY = 455;
  var WHEEL_R = 248;
  /**
   * One skipped arc segment. Fewer segments → wider rim gap (≥1.75× the prior narrow hatch).
   */
  var WHEEL_SEG_N = 21;
  var WHEEL_GAP_COUNT = 1;
  /** Radians per tick — slow drum + tumblers (Matter ~60Hz). */
  var WHEEL_OMEGA = 0.012;
  var TUMBLER_COUNT = 4;
  /** Hub distance to tumbler center (fraction of WHEEL_R); higher = paddles hug the ring. */
  var TUMBLER_CENTER_FR = 0.76;
  /** Clear zone at hub (px); keeps a large open middle inside the drum. */
  var INNER_HOLE_R = 110;
  var TUMBLER_TANGENT_THICK = 11;

  var PLACE_LABELS = [
    "17th",
    "18th",
    "19th",
    "20th",
    "21st",
    "22nd",
    "23rd",
    "24th",
  ];

  var elData = document.getElementById("draft-lottery-teams-data");
  var elSlots = document.getElementById("draft-lottery-slots");
  var elMount = document.getElementById("draft-lottery-canvas-mount");
  var elStatus = document.getElementById("dl-status");
  var elResults = document.getElementById("draft-lottery-results");
  var elResultsList = document.getElementById("draft-lottery-results-list");
  var elOrderList = document.getElementById("draft-lottery-order-list");
  var elOrderSub = document.getElementById("draft-lottery-order-sub");
  var elFinal = document.getElementById("draft-lottery-final");
  var elFinalList = document.getElementById("draft-lottery-final-list");
  var btnPrepare = document.getElementById("dl-btn-prepare");
  var btnRelease = document.getElementById("dl-btn-release");
  var btnReleaseStage = document.getElementById("dl-btn-release-stage");
  var btnOpenGateStage = document.getElementById("dl-btn-open-gate-stage");
  var elWinnerReveal = document.getElementById("draft-lottery-winner-reveal");
  var elWinnerLogo = document.getElementById("draft-lottery-winner-logo");
  var elWinnerName = document.getElementById("draft-lottery-winner-name");
  var elChuteDecor = document.getElementById("draft-lottery-chute-decor");
  var elWinHitzone = document.getElementById("draft-lottery-win-hitzone");
  var btnReset = document.getElementById("dl-btn-reset");

  if (!elData || !elSlots || !elMount || typeof window.Matter === "undefined") {
    return;
  }

  var teams;
  try {
    teams = JSON.parse(elData.textContent || "[]");
  } catch (e) {
    teams = [];
  }

  var Matter = window.Matter;
  var Engine = Matter.Engine;
  var Render = Matter.Render;
  var Runner = Matter.Runner;
  var Bodies = Matter.Bodies;
  var Composite = Matter.Composite;
  var Events = Matter.Events;
  var Body = Matter.Body;

  var engine = null;
  var render = null;
  var runner = null;
  var wallsComposite = null;
  var balls = [];
  var roundComplete = false;
  var released = false;
  var winZoneHandler = null;

  var wheelSegmentBodies = [];
  var wheelTumblerBodies = [];
  /** Corner plugs sealing segment joints at the hatch gap (indices in gapMeta.start). */
  var wheelGapPlugBodies = [];
  /** First skipped segment index for the bottom opening. */
  var wheelGapStartIndex = 0;
  var wheelDoorBody = null;
  var wheelAngle = 0;
  var wheelBeforeUpdate = null;
  /** True after Release until draw resolved (or engine destroyed). */
  var wheelSpinning = false;
  var gateOpen = false;

  var order = [];
  var startIndex = {};
  var drops = {};
  var rise = {};
  var drawsCompleted = 0;
  var selectionsLocked = false;
  var drawLog = [];
  /** Team ids (string) that won a completed draw — excluded from spawning in later draws. */
  var wonPreviousDrawTeamIds = [];

  function setStatus(msg) {
    if (elStatus) elStatus.textContent = msg || "";
  }

  function syncReleaseDisabled(dis) {
    var v = !!dis;
    if (btnRelease) btnRelease.disabled = v;
    if (btnReleaseStage) btnReleaseStage.disabled = v;
  }

  function syncOpenGateDisabled(dis) {
    var v = !!dis;
    if (btnOpenGateStage) btnOpenGateStage.disabled = v;
  }

  /** Yellow win bar (DOM); shown when hatch is open. */
  function syncChuteDecor() {
    if (!elChuteDecor) return;
    elChuteDecor.hidden = !gateOpen;
  }

  function hideWinnerLogoOverlay() {
    if (elWinnerReveal) elWinnerReveal.hidden = true;
    if (elWinnerLogo) {
      elWinnerLogo.onload = null;
      elWinnerLogo.removeAttribute("src");
      elWinnerLogo.alt = "";
      elWinnerLogo.style.width = "";
      elWinnerLogo.style.height = "";
    }
    if (elWinnerName) elWinnerName.textContent = "";
  }

  function applyWinnerLogoIntrinsicSize() {
    if (!elWinnerLogo) return;
    /* Let CSS max-width / max-height constrain the image so the team name stays in view. */
    elWinnerLogo.style.width = "";
    elWinnerLogo.style.height = "";
  }

  /** Show winning team logo at natural (100%) pixel size, capped only by drum width. */
  function showWinnerLogoOverlay(teamId, teamName) {
    if (!elWinnerReveal || !elWinnerLogo) return;
    var team = teamById(teamId);
    if (!team || !team.logo_url) {
      elWinnerReveal.hidden = true;
      if (elWinnerName) elWinnerName.textContent = "";
      return;
    }
    var displayName = (teamName || team.name || "").trim() || "Winner";
    if (elWinnerName) {
      elWinnerName.textContent = displayName;
    }
    elWinnerLogo.style.width = "";
    elWinnerLogo.style.height = "";
    elWinnerLogo.onload = applyWinnerLogoIntrinsicSize;
    elWinnerLogo.alt = displayName;
    elWinnerLogo.src = team.logo_url;
    elWinnerReveal.hidden = false;
    if (elWinnerLogo.complete) applyWinnerLogoIntrinsicSize();
  }

  /** Long radial blade length; outer tip stays inside the drum ring. */
  function computeTumblerRadialLength() {
    var tC = WHEEL_R * TUMBLER_CENTER_FR;
    var outerHalf = WHEEL_R - 14 - tC;
    var innerHalf = tC - INNER_HOLE_R;
    var halfLen = Math.min(outerHalf, innerHalf) * 0.95;
    return Math.max(40, Math.floor(2 * halfLen));
  }

  function defaultHex(primary, secondary, text) {
    if (primary) return { fill: primary, stroke: secondary || "#c0c8d4", line: text ? 2 : 2 };
    return { fill: "#6b7280", stroke: secondary || "#9ca3af", line: 2 };
  }

  function teamById(id) {
    var sid = String(id);
    for (var i = 0; i < teams.length; i++) {
      if (String(teams[i].id) === sid) return teams[i];
    }
    return null;
  }

  function buildSlotRow(slotNum) {
    var placeIdx = slotNum - 1;
    var place = PLACE_LABELS[placeIdx] || String(16 + slotNum) + "th";

    var li = document.createElement("li");
    li.className = "draft-lottery-slot";
    var label = document.createElement("div");
    label.className = "draft-lottery-slot__label";
    var nBalls = slotNum * BALLS_MULTIPLIER;
    label.innerHTML =
      "<span>" +
      slotNum +
      " (" +
      place +
      " Place):</span><span class=\"draft-lottery-slot__balls\">" +
      nBalls +
      " ball" +
      (nBalls === 1 ? "" : "s") +
      "</span>";

    var sel = document.createElement("select");
    sel.className = "draft-lottery-slot__select";
    sel.id = "dl-slot-" + slotNum;
    sel.dataset.slot = String(slotNum);
    var opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "— Choose team —";
    sel.appendChild(opt0);
    for (var i = 0; i < teams.length; i++) {
      var t = teams[i];
      var opt = document.createElement("option");
      opt.value = String(t.id);
      opt.textContent = t.name;
      sel.appendChild(opt);
    }
    if (teams.length) {
      sel.selectedIndex = 1 + ((slotNum - 1) % teams.length);
    }
    var prev = document.createElement("div");
    prev.className = "draft-lottery-slot__preview";
    prev.id = "dl-slot-prev-" + slotNum;
    li.appendChild(label);
    li.appendChild(sel);
    li.appendChild(prev);
    sel.addEventListener("change", function () {
      updatePreview(slotNum);
      if (!selectionsLocked) rebuildOrderFromSlots();
    });
    updatePreview(slotNum);
    return li;
  }

  function updatePreview(slotNum) {
    var sel = document.getElementById("dl-slot-" + slotNum);
    var prev = document.getElementById("dl-slot-prev-" + slotNum);
    if (!sel || !prev) return;
    var t = teamById(sel.value);
    prev.innerHTML = "";
    if (!t || !t.logo_url) return;
    var img = document.createElement("img");
    img.src = t.logo_url;
    img.alt = "";
    img.width = 36;
    img.height = 36;
    img.loading = "lazy";
    prev.appendChild(img);
  }

  function readSlotTeamMap() {
    var map = {};
    for (var s = 1; s <= 8; s++) {
      var sel = document.getElementById("dl-slot-" + s);
      if (!sel || !sel.value) return null;
      var t = teamById(sel.value);
      if (!t) return null;
      map[s] = t;
    }
    return map;
  }

  function rebuildOrderFromSlots() {
    var map = readSlotTeamMap();
    if (!map) {
      order = [];
      renderOrderSummary();
      return;
    }
    order = [];
    for (var s = 1; s <= 8; s++) {
      var t = map[s];
      order.push({ teamId: t.id, origSlot: s, name: t.name });
    }
    renderOrderSummary();
  }

  function setSelectsDisabled(dis) {
    for (var s = 1; s <= 8; s++) {
      var sel = document.getElementById("dl-slot-" + s);
      if (sel) sel.disabled = !!dis;
    }
  }

  function renderOrderSummary() {
    if (!elOrderList) return;
    elOrderList.innerHTML = "";
    if (!order.length) {
      var li0 = document.createElement("li");
      li0.className = "draft-lottery-order-list__empty muted";
      li0.textContent = "Assign all eight teams to see the order.";
      elOrderList.appendChild(li0);
      return;
    }
    for (var i = 0; i < order.length; i++) {
      var li = document.createElement("li");
      li.className = "draft-lottery-order-list__row";
      var place = PLACE_LABELS[i] || String(i + 17) + "th";
      li.innerHTML =
        "<span class=\"draft-lottery-order-list__place\">" +
        place +
        "</span>" +
        "<span class=\"draft-lottery-order-list__name\">" +
        escapeHtml(order[i].name) +
        "</span>" +
        "<span class=\"draft-lottery-order-list__meta muted\">" +
        ordinalFinish(order[i].origSlot) +
        " finish</span>";
      elOrderList.appendChild(li);
    }
    if (elOrderSub) {
      if (drawsCompleted >= MAX_DRAWS) {
        elOrderSub.textContent = "Final order after two draws (caps: +5 spots up, −2 down vs original).";
      } else if (drawsCompleted === 1) {
        elOrderSub.textContent = "After draw 1. One draw remaining.";
      } else {
        elOrderSub.textContent = "Best pick at top. Updates as you assign teams and after each draw.";
      }
    }
  }

  function renderDrawLog() {
    if (!elResults || !elResultsList) return;
    elResults.hidden = drawLog.length === 0;
    elResultsList.innerHTML = "";
    for (var i = 0; i < drawLog.length; i++) {
      var d = drawLog[i];
      var li = document.createElement("li");
      li.className = "draft-lottery-result-row";
      var tm = d.teamId != null && d.teamId !== "" ? teamById(d.teamId) : null;
      var logoHtml =
        tm && tm.logo_url
          ? "<img class=\"draft-lottery-result-row__logo\" src=\"" +
            escapeHtml(tm.logo_url) +
            "\" alt=\"\" width=\"28\" height=\"28\" decoding=\"async\">"
          : "";
      li.innerHTML =
        "<span class=\"draft-lottery-result-row__pick\">Draw " +
        d.draw +
        "</span>" +
        "<span class=\"draft-lottery-result-row__name-wrap\">" +
        logoHtml +
        "<span class=\"draft-lottery-result-row__name\">" +
        escapeHtml(d.name) +
        "</span></span>" +
        "<span class=\"draft-lottery-result-row__slot muted\">" +
        (d.moved
          ? "moved up " + d.moved + " spot" + (d.moved === 1 ? "" : "s")
          : "no move (rise/drop caps)") +
        "</span>";
      elResultsList.appendChild(li);
    }
  }

  /** Draft slot within the 17–24 group: 1st Pick … 8th Pick (top = best). */
  function draftLotteryPickLabel(indexFromZero) {
    var n = indexFromZero + 1;
    var suf = "th";
    if (n % 10 === 1 && n % 100 !== 11) suf = "st";
    else if (n % 10 === 2 && n % 100 !== 12) suf = "nd";
    else if (n % 10 === 3 && n % 100 !== 13) suf = "rd";
    return n + suf + " Pick";
  }

  function renderFinalStandings() {
    if (!elFinal || !elFinalList) return;
    if (drawsCompleted < MAX_DRAWS) {
      elFinal.hidden = true;
      elFinalList.innerHTML = "";
      return;
    }
    elFinal.hidden = false;
    elFinalList.innerHTML = "";
    for (var i = 0; i < order.length; i++) {
      var row = order[i];
      var li = document.createElement("li");
      li.className = "draft-lottery-final__row";
      var tm = row.teamId != null && row.teamId !== "" ? teamById(row.teamId) : null;
      var logoHtml =
        tm && tm.logo_url
          ? "<img class=\"draft-lottery-final__logo\" src=\"" +
            escapeHtml(tm.logo_url) +
            "\" alt=\"\" width=\"28\" height=\"28\" decoding=\"async\">"
          : "";
      li.innerHTML =
        "<span class=\"draft-lottery-final__pick\">" +
        draftLotteryPickLabel(i) +
        "</span>" +
        "<span class=\"draft-lottery-final__name-wrap\">" +
        logoHtml +
        "<span class=\"draft-lottery-final__name\">" +
        escapeHtml(row.name) +
        "</span></span>" +
        "<span class=\"draft-lottery-final__meta muted\">" +
        ordinalFinish(row.origSlot) +
        " finish</span>";
      elFinalList.appendChild(li);
    }
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function ordinalFinish(origSlot) {
    var n = 16 + origSlot;
    var suf = "th";
    if (n % 10 === 1 && n % 100 !== 11) suf = "st";
    else if (n % 10 === 2 && n % 100 !== 12) suf = "nd";
    else if (n % 10 === 3 && n % 100 !== 13) suf = "rd";
    return n + suf;
  }

  function applyWinnerMove(winnerTeamId) {
    var w = -1;
    for (var i = 0; i < order.length; i++) {
      if (order[i].teamId === winnerTeamId) {
        w = i;
        break;
      }
    }
    if (w < 0) return 0;

    var maxK = Math.min(MAX_RISE, w);
    var kChosen = 0;
    for (var k = maxK; k >= 1; k--) {
      var newIdx = w - k;
      var ok = true;
      for (var j = newIdx; j < w; j++) {
        var uid = order[j].teamId;
        if ((drops[uid] || 0) + 1 > MAX_DROP) {
          ok = false;
          break;
        }
      }
      var r0 = rise[winnerTeamId] || 0;
      if (r0 + k > MAX_RISE) ok = false;
      if (ok) {
        kChosen = k;
        break;
      }
    }

    if (kChosen === 0) return 0;

    for (var j2 = w - kChosen; j2 < w; j2++) {
      var u = order[j2].teamId;
      drops[u] = (drops[u] || 0) + 1;
    }
    rise[winnerTeamId] = (rise[winnerTeamId] || 0) + kChosen;

    var next = order.slice();
    var entry = next.splice(w, 1)[0];
    next.splice(w - kChosen, 0, entry);
    order = next;
    return kChosen;
  }

  /**
   * After all lottery draws, keep the top MAX_DRAWS spots as-is (where winners landed),
   * then order the rest by regular-season finish worst-first (origSlot 8 = 24th → best remaining pick).
   */
  function sortFinalDraftOrderAfterAllDraws() {
    if (drawsCompleted < MAX_DRAWS || order.length <= MAX_DRAWS) return;
    var head = order.slice(0, MAX_DRAWS);
    var tail = order.slice(MAX_DRAWS).sort(function (a, b) {
      return b.origSlot - a.origSlot;
    });
    order = head.concat(tail);
  }

  function initSlotUI() {
    elSlots.innerHTML = "";
    for (var s = 1; s <= 8; s++) {
      elSlots.appendChild(buildSlotRow(s));
    }
    rebuildOrderFromSlots();
  }

  /** Skipped segment indices for bottom opening + first skipped index for plug placement. */
  function computeGapMeta() {
    var N = WHEEL_SEG_N;
    var step = (Math.PI * 2) / N;
    var target = Math.PI / 2;
    var best = 0;
    var bestD = 1e9;
    var i;
    for (i = 0; i < N; i++) {
      var mid = (i + 0.5) * step;
      var d = Math.abs(mid - target);
      d = Math.min(d, Math.abs(mid - target + 2 * Math.PI), Math.abs(mid - target - 2 * Math.PI));
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
    var set = {};
    for (i = 0; i < WHEEL_GAP_COUNT; i++) {
      set[(best + i) % N] = true;
    }
    return { set: set, start: best };
  }

  function positionWheelRing() {
    var step = (Math.PI * 2) / WHEEL_SEG_N;
    var k;
    for (k = 0; k < wheelSegmentBodies.length; k++) {
      var item = wheelSegmentBodies[k];
      var idx = item.index;
      var mid = (idx + 0.5) * step + wheelAngle;
      var x = WHEEL_CX + Math.cos(mid) * WHEEL_R;
      var y = WHEEL_CY + Math.sin(mid) * WHEEL_R;
      Body.setPosition(item.body, { x: x, y: y });
      Body.setAngle(item.body, mid + Math.PI / 2);
    }
    if (wheelDoorBody) {
      var midD = Math.PI / 2 + wheelAngle;
      Body.setPosition(wheelDoorBody, {
        x: WHEEL_CX + Math.cos(midD) * WHEEL_R,
        y: WHEEL_CY + Math.sin(midD) * WHEEL_R,
      });
      Body.setAngle(wheelDoorBody, midD + Math.PI / 2);
    }
    var tC = WHEEL_R * TUMBLER_CENTER_FR;
    var ti;
    for (ti = 0; ti < wheelTumblerBodies.length; ti++) {
      var tb = wheelTumblerBodies[ti];
      var tang = (ti / wheelTumblerBodies.length) * Math.PI * 2 + wheelAngle + Math.PI / TUMBLER_COUNT;
      var xr = WHEEL_CX + Math.cos(tang) * tC;
      var yr = WHEEL_CY + Math.sin(tang) * tC;
      Body.setPosition(tb, { x: xr, y: yr });
      /* Long axis along local X → radial direction when angle = tang (90° from old tangential blades). */
      Body.setAngle(tb, tang);
    }
    var g0 = wheelGapStartIndex;
    var angL = g0 * step + wheelAngle + step * 0.28;
    var angR = (g0 + WHEEL_GAP_COUNT) * step + wheelAngle - step * 0.28;
    var plugR = WHEEL_R - 5;
    if (wheelGapPlugBodies.length >= 2) {
      Body.setPosition(wheelGapPlugBodies[0], {
        x: WHEEL_CX + Math.cos(angL) * plugR,
        y: WHEEL_CY + Math.sin(angL) * plugR,
      });
      Body.setPosition(wheelGapPlugBodies[1], {
        x: WHEEL_CX + Math.cos(angR) * plugR,
        y: WHEEL_CY + Math.sin(angR) * plugR,
      });
    }
  }

  function buildWheelDrum() {
    wheelAngle = 0;
    wheelSegmentBodies = [];
    wheelTumblerBodies = [];
    wheelGapPlugBodies = [];
    wheelDoorBody = null;
    gateOpen = false;
    wheelSpinning = false;
    syncChuteDecor();

    var world = engine.world;
    var step = (Math.PI * 2) / WHEEL_SEG_N;
    var chord = 2 * WHEEL_R * Math.sin(step / 2);
    var segW = chord * 1.18 + 28;
    var segH = 16;
    var gapMeta = computeGapMeta();
    var gapSet = gapMeta.set;
    wheelGapStartIndex = gapMeta.start;
    var i;

    for (i = 0; i < WHEEL_SEG_N; i++) {
      if (gapSet[i]) continue;
      var seg = Bodies.rectangle(WHEEL_CX, WHEEL_CY, segW, segH, {
        isStatic: true,
        friction: 0.1,
        restitution: 0.82,
        label: "lotteryWallSeg",
        render: {
          fillStyle: "rgba(100, 116, 139, 0.45)",
          strokeStyle: "#64748b",
          lineWidth: 1,
        },
      });
      wheelSegmentBodies.push({ body: seg, index: i });
      Composite.add(world, seg);
    }

    var slotChord = 2 * WHEEL_R * Math.sin(step / 2);
    var doorW = slotChord * 1.06 + 10;
    wheelDoorBody = Bodies.rectangle(WHEEL_CX, WHEEL_CY, doorW, segH + 6, {
      isStatic: true,
      friction: 0.08,
      restitution: 0.8,
      label: "lotteryDoor",
      render: {
        fillStyle: "rgba(51, 65, 85, 0.92)",
        strokeStyle: "#94a3b8",
        lineWidth: 1,
      },
    });
    Composite.add(world, wheelDoorBody);

    for (var plugIdx = 0; plugIdx < 2; plugIdx++) {
      var plug = Bodies.circle(WHEEL_CX, WHEEL_CY, 10, {
        isStatic: true,
        friction: 0.12,
        restitution: 0.55,
        label: "lotteryGapPlug",
        render: {
          fillStyle: "rgba(71, 85, 105, 0.95)",
          strokeStyle: "#94a3b8",
          lineWidth: 1,
        },
      });
      wheelGapPlugBodies.push(plug);
      Composite.add(world, plug);
    }

    var tumblerRadialLen = computeTumblerRadialLength();
    for (i = 0; i < TUMBLER_COUNT; i++) {
      var tumb = Bodies.rectangle(WHEEL_CX, WHEEL_CY, tumblerRadialLen, TUMBLER_TANGENT_THICK, {
        isStatic: true,
        friction: 0.14,
        restitution: 0.86,
        label: "lotteryTumbler",
        render: {
          fillStyle: "rgba(71, 85, 105, 0.92)",
          strokeStyle: "#cbd5e1",
          lineWidth: 1,
        },
      });
      wheelTumblerBodies.push(tumb);
      Composite.add(world, tumb);
    }
    positionWheelRing();

    wheelBeforeUpdate = function () {
      if (!engine || roundComplete) return;
      if (wheelSegmentBodies.length === 0) return;
      wheelAngle += WHEEL_OMEGA;
      positionWheelRing();
    };
    Events.on(engine, "beforeUpdate", wheelBeforeUpdate);
  }

  function clearWheelInfrastructure() {
    if (engine && wheelBeforeUpdate) {
      Events.off(engine, "beforeUpdate", wheelBeforeUpdate);
    }
    wheelBeforeUpdate = null;
    if (!engine) return;
    var w = engine.world;
    var i;
    for (i = 0; i < wheelSegmentBodies.length; i++) {
      Composite.remove(w, wheelSegmentBodies[i].body, true);
    }
    wheelSegmentBodies = [];
    for (i = 0; i < wheelTumblerBodies.length; i++) {
      Composite.remove(w, wheelTumblerBodies[i], true);
    }
    wheelTumblerBodies = [];
    for (i = 0; i < wheelGapPlugBodies.length; i++) {
      Composite.remove(w, wheelGapPlugBodies[i], true);
    }
    wheelGapPlugBodies = [];
    if (wheelDoorBody) {
      Composite.remove(w, wheelDoorBody, true);
      wheelDoorBody = null;
    }
  }

  function ensureEngine() {
    if (engine) return;
    engine = Engine.create({
      enableSleeping: false,
      gravity: { x: 0, y: 0 },
      positionIterations: 10,
      velocityIterations: 10,
    });
    var world = engine.world;

    var wallOpts = {
      isStatic: true,
      friction: 0.14,
      restitution: 0.42,
      render: { visible: false },
    };
    var left = Bodies.rectangle(22, H / 2, 44, H + 80, wallOpts);
    var right = Bodies.rectangle(W - 22, H / 2, 44, H + 80, wallOpts);
    var floor = Bodies.rectangle(W / 2, H - 22, W - 40, 28, {
      isStatic: true,
      friction: 0.45,
      restitution: 0.22,
      label: "lotteryFloor",
      render: { fillStyle: "#1e293b", visible: true },
    });
    var roof = Bodies.rectangle(W / 2, 88, W - 100, 20, {
      isStatic: true,
      friction: 0.06,
      restitution: 0.55,
      label: "lotteryRoof",
      render: { fillStyle: "#334155", visible: true },
    });

    wallsComposite = Composite.create({ bodies: [left, right, floor, roof], label: "walls" });
    Composite.add(world, wallsComposite);

    render = Render.create({
      element: elMount,
      engine: engine,
      options: {
        width: W,
        height: H,
        wireframes: false,
        background: "transparent",
        pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
        showAngleIndicator: false,
        showVelocity: false,
      },
    });
    Render.run(render);
    runner = Runner.create();
    Runner.run(runner, engine);
  }

  function destroyEngine() {
    if (winZoneHandler && engine) {
      Events.off(engine, "afterUpdate", winZoneHandler);
    }
    winZoneHandler = null;
    clearWheelInfrastructure();
    wheelSpinning = false;
    gateOpen = false;
    syncChuteDecor();
    if (runner) {
      Runner.stop(runner);
      runner = null;
    }
    if (render) {
      Render.stop(render);
      if (render.canvas && render.canvas.parentNode) {
        render.canvas.parentNode.removeChild(render.canvas);
      }
      if (render.textures) render.textures = {};
      render = null;
    }
    if (engine) {
      Engine.clear(engine);
      engine = null;
    }
    balls = [];
    wallsComposite = null;
  }

  function clearBalls() {
    if (!engine) return;
    for (var i = 0; i < balls.length; i++) {
      Composite.remove(engine.world, balls[i], true);
    }
    balls = [];
  }

  function teamAlreadyWonPreviousDraw(teamId) {
    var sid = String(teamId);
    var i;
    for (i = 0; i < wonPreviousDrawTeamIds.length; i++) {
      if (wonPreviousDrawTeamIds[i] === sid) return true;
    }
    return false;
  }

  function removeSiblingBalls(winningBall) {
    if (!engine || !winningBall) return;
    var orig = winningBall.lotteryOrigSlot;
    var next = [];
    for (var i = 0; i < balls.length; i++) {
      var b = balls[i];
      if (b === winningBall) {
        next.push(b);
        continue;
      }
      if (b.lotteryOrigSlot === orig) {
        Composite.remove(engine.world, b, true);
        continue;
      }
      next.push(b);
    }
    balls = next;
  }

  function spawnBallsFromOrder(holdUntilRelease) {
    clearBalls();
    roundComplete = false;
    released = !holdUntilRelease;
    var rng = function (a, b) {
      return a + Math.random() * (b - a);
    };
    var inner = WHEEL_R - BALL_R - 32;
    var i;
    for (i = 0; i < order.length; i++) {
      var row = order[i];
      if (teamAlreadyWonPreviousDraw(row.teamId)) continue;
      var team = teamById(row.teamId);
      if (!team) continue;
      var slot = row.origSlot;
      var ballCount = slot * BALLS_MULTIPLIER;
      var cols = defaultHex(team.primary, team.secondary, team.text);
      var j;
      for (j = 0; j < ballCount; j++) {
        var ang = rng(0, Math.PI * 2);
        var rad = rng(INNER_HOLE_R + 14, inner);
        var x = WHEEL_CX + Math.cos(ang) * rad;
        var y = WHEEL_CY + Math.sin(ang) * rad;
        var ball = Bodies.circle(x, y, BALL_R, {
          isStatic: !!holdUntilRelease,
          restitution: 0.76,
          friction: 0.02,
          frictionAir: 0.00018,
          density: 0.001,
          label: "lotteryBall",
          collisionFilter: { category: 0x0002, mask: 0xffffffff },
          render: {
            fillStyle: cols.fill,
            strokeStyle: cols.stroke,
            lineWidth: cols.line,
          },
        });
        ball.lotteryTeamId = row.teamId;
        ball.lotteryTeamName = row.name;
        ball.lotteryOrigSlot = slot;
        if (!holdUntilRelease) {
          Body.setVelocity(ball, { x: rng(-1.6, 1.6), y: rng(0.15, 2.0) });
          Body.setAngularVelocity(ball, rng(-0.28, 0.28));
        } else {
          Body.setVelocity(ball, { x: 0, y: 0 });
          Body.setAngularVelocity(ball, 0);
        }
        Composite.add(engine.world, ball);
        balls.push(ball);
      }
    }
  }

  function circleRectOverlap(cx, cy, r, rx, ry, rw, rh) {
    var nx = Math.max(rx, Math.min(cx, rx + rw));
    var ny = Math.max(ry, Math.min(cy, ry + rh));
    var dx = cx - nx;
    var dy = cy - ny;
    return dx * dx + dy * dy <= r * r;
  }

  function declareWinnerFromBall(ball) {
    if (!ball || roundComplete) return;
    roundComplete = true;
    wheelSpinning = false;
    var winId = ball.lotteryTeamId;
    var name = ball.lotteryTeamName || "Team";
    removeSiblingBalls(ball);
    var moved = applyWinnerMove(winId);
    drawsCompleted += 1;
    drawLog.push({ draw: drawsCompleted, teamId: winId, name: name, moved: moved });
    wonPreviousDrawTeamIds.push(String(winId));

    setStatus(
      "Draw " +
        drawsCompleted +
        ": " +
        name +
        (moved ? " moved up " + moved + " spot(s)." : " could not move (caps).")
    );

    showWinnerLogoOverlay(winId, name);

    var j;
    for (j = 0; j < balls.length; j++) {
      Body.setVelocity(balls[j], { x: 0, y: 0 });
      Body.setAngularVelocity(balls[j], 0);
      Body.setStatic(balls[j], true);
    }
    syncReleaseDisabled(true);
    syncOpenGateDisabled(true);

    if (drawsCompleted >= MAX_DRAWS) {
      sortFinalDraftOrderAfterAllDraws();
    }
    renderOrderSummary();
    renderDrawLog();
    renderFinalStandings();

    if (drawsCompleted >= MAX_DRAWS) {
      destroyEngine();
      if (btnPrepare) btnPrepare.disabled = true;
      setStatus("Both draws complete. Final order is below the draw log. New lottery to reset.");
    } else {
      destroyEngine();
      if (btnPrepare) btnPrepare.disabled = false;
      if (btnPrepare) btnPrepare.textContent = "Prepare draw 2";
      setStatus("Draw " + drawsCompleted + " done. Prepare the machine for the second draw.");
    }
  }

  /** Winner when a moving ball overlaps the invisible bottom hit band (hatch open; yellow bar is visual-only). */
  function attachWinZoneAfterUpdate() {
    if (!engine || winZoneHandler) return;
    winZoneHandler = function () {
      if (roundComplete || !released || !gateOpen || !render || !render.canvas) return;
      if (!elWinHitzone) return;
      var canvas = render.canvas;
      var cr = canvas.getBoundingClientRect();
      if (cr.width < 4 || cr.height < 4) return;
      var chute = elWinHitzone.getBoundingClientRect();
      if (chute.width < 2 || chute.height < 2) return;
      var scaleX = cr.width / W;
      var scaleY = cr.height / H;
      var ballRScreen = BALL_R * (scaleX + scaleY) * 0.5;
      var bi;
      for (bi = 0; bi < balls.length; bi++) {
        var ball = balls[bi];
        if (!ball || ball.label !== "lotteryBall" || ball.isStatic) continue;
        var px = cr.left + ball.position.x * scaleX;
        var py = cr.top + ball.position.y * scaleY;
        if (
          circleRectOverlap(px, py, ballRScreen, chute.left, chute.top, chute.width, chute.height)
        ) {
          declareWinnerFromBall(ball);
          return;
        }
      }
    };
    Events.on(engine, "afterUpdate", winZoneHandler);
  }

  function onPrepare() {
    if (drawsCompleted >= MAX_DRAWS) return;

    var map = readSlotTeamMap();
    if (!map) {
      setStatus("Choose a team in every row before preparing.");
      return;
    }

    if (drawsCompleted === 0) {
      order = [];
      for (var s = 1; s <= 8; s++) {
        var t = map[s];
        order.push({ teamId: t.id, origSlot: s, name: t.name });
      }
      startIndex = {};
      drops = {};
      rise = {};
      for (var i = 0; i < order.length; i++) {
        startIndex[order[i].teamId] = i;
        drops[order[i].teamId] = 0;
        rise[order[i].teamId] = 0;
      }
      drawLog = [];
      wonPreviousDrawTeamIds = [];
      selectionsLocked = true;
      setSelectsDisabled(true);
      renderDrawLog();
      if (elResults) elResults.hidden = true;
      renderFinalStandings();
    }

    destroyEngine();
    hideWinnerLogoOverlay();
    ensureEngine();
    buildWheelDrum();
    attachWinZoneAfterUpdate();
    spawnBallsFromOrder(true);
    engine.gravity.x = 0;
    engine.gravity.y = 0;
    renderOrderSummary();
    setStatus(
      "Draw " +
        (drawsCompleted + 1) +
        " of " +
        MAX_DRAWS +
        ". Release balls to mix, then open the hatch on the drum when you want a winner (first ball on the yellow line wins)."
    );
    syncReleaseDisabled(false);
    syncOpenGateDisabled(true);
    if (btnPrepare) btnPrepare.disabled = true;
  }

  function onRelease() {
    if (!engine || roundComplete) return;
    released = true;
    wheelSpinning = true;
    gateOpen = false;
    engine.gravity.y = 0.42;
    engine.gravity.x = 0;
    var rng = function (a, b) {
      return a + Math.random() * (b - a);
    };
    var j;
    for (j = 0; j < balls.length; j++) {
      Body.setStatic(balls[j], false);
      Body.setVelocity(balls[j], {
        x: rng(-2.8, 2.8),
        y: rng(-1.2, 2.4),
      });
      Body.setAngularVelocity(balls[j], rng(-0.5, 0.5));
    }
    syncReleaseDisabled(true);
    syncOpenGateDisabled(false);
    syncChuteDecor();
    setStatus("Drum is spinning. When you are ready, open the hatch — first ball to touch the yellow win line wins.");
  }

  function onOpenGate() {
    if (!engine || roundComplete || !wheelSpinning || gateOpen) return;
    gateOpen = true;
    var world = engine.world;
    if (wheelDoorBody) {
      Composite.remove(world, wheelDoorBody, true);
      wheelDoorBody = null;
    }
    var qi;
    for (qi = 0; qi < wheelGapPlugBodies.length; qi++) {
      Composite.remove(world, wheelGapPlugBodies[qi], true);
    }
    wheelGapPlugBodies = [];
    positionWheelRing();
    syncOpenGateDisabled(true);
    syncChuteDecor();
    setStatus("Hatch open — first ball to touch the yellow win line wins this draw.");
  }

  function onReset() {
    destroyEngine();
    hideWinnerLogoOverlay();
    drawsCompleted = 0;
    drawLog = [];
    wonPreviousDrawTeamIds = [];
    order = [];
    startIndex = {};
    drops = {};
    rise = {};
    roundComplete = false;
    released = false;
    wheelSpinning = false;
    gateOpen = false;
    syncChuteDecor();
    selectionsLocked = false;
    setSelectsDisabled(false);
    if (btnPrepare) btnPrepare.textContent = "Prepare machine";
    if (elResults) elResults.hidden = true;
    if (elResultsList) elResultsList.innerHTML = "";
    if (elFinal) elFinal.hidden = true;
    if (elFinalList) elFinalList.innerHTML = "";
    if (btnPrepare) btnPrepare.disabled = false;
    syncReleaseDisabled(true);
    syncOpenGateDisabled(true);
    rebuildOrderFromSlots();
    setStatus("New lottery. Assign 17th–24th places, then prepare the machine.");
  }

  if (btnPrepare) btnPrepare.addEventListener("click", onPrepare);
  if (btnRelease) btnRelease.addEventListener("click", onRelease);
  if (btnReleaseStage) btnReleaseStage.addEventListener("click", onRelease);
  if (btnOpenGateStage) btnOpenGateStage.addEventListener("click", onOpenGate);
  if (btnReset) btnReset.addEventListener("click", onReset);

  initSlotUI();
  syncReleaseDisabled(true);
  syncOpenGateDisabled(true);
  setStatus("Assign teams to 17th through 24th place, then prepare the machine.");
})();
