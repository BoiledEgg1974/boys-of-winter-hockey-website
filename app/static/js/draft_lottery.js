/**
 * BOWL-Fantasy GM draft lottery — two draws max, Matter.js physics.
 * Finish slot s (1=17th..8=24th) spawns 8*s balls (17th→8, 24th→64). Reorder: winner up to +5
 * cumulative vs start; leapfrogged teams +1 drop each event, max +2 drops vs start.
 */
(function () {
  "use strict";

  var W = 800;
  var H = 960;
  /** Radius; smaller so up to 288 balls (all slots) stay usable in the machine. */
  var BALL_R = 7;
  var BALLS_MULTIPLIER = 8;
  var MAX_DRAWS = 2;
  var MAX_RISE = 5;
  var MAX_DROP = 2;

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
  var btnPrepare = document.getElementById("dl-btn-prepare");
  var btnRelease = document.getElementById("dl-btn-release");
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
  var collisionHandler = null;

  /** @type {{teamId:number, origSlot:number, name:string}[]} index 0 = 17th pick */
  var order = [];
  /** @type {Record<number, number>} teamId -> start index 0..7 */
  var startIndex = {};
  /** @type {Record<number, number>} cumulative spots worsened vs start */
  var drops = {};
  /** @type {Record<number, number>} cumulative spots improved vs start (index decrease) */
  var rise = {};
  var drawsCompleted = 0;
  /** Lock slot selects after first prepare until New lottery */
  var selectionsLocked = false;
  /** @type {{draw:number, name:string, moved:number}[]} */
  var drawLog = [];

  function setStatus(msg) {
    if (elStatus) elStatus.textContent = msg || "";
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
      li.innerHTML =
        "<span class=\"draft-lottery-result-row__pick\">Draw " +
        d.draw +
        "</span>" +
        "<span class=\"draft-lottery-result-row__name\">" +
        escapeHtml(d.name) +
        "</span>" +
        "<span class=\"draft-lottery-result-row__slot muted\">" +
        (d.moved
          ? "moved up " + d.moved + " spot" + (d.moved === 1 ? "" : "s")
          : "no move (rise/drop caps)") +
        "</span>";
      elResultsList.appendChild(li);
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

  function initSlotUI() {
    elSlots.innerHTML = "";
    for (var s = 1; s <= 8; s++) {
      elSlots.appendChild(buildSlotRow(s));
    }
    rebuildOrderFromSlots();
  }

  function ensureEngine() {
    if (engine) return;
    /* Sleeping + high wall friction kills motion early with 200+ balls; bouncier, slipperier = more mixing. */
    engine = Engine.create({
      enableSleeping: false,
      gravity: { x: 0, y: 1.05 },
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
    var floor = Bodies.rectangle(W / 2, H - 28, W - 48, 36, {
      isStatic: true,
      friction: 0.38,
      restitution: 0.28,
      label: "lotteryFloor",
      render: { fillStyle: "#1e293b", visible: true },
    });
    var roof = Bodies.rectangle(W / 2, 96, W - 120, 24, {
      isStatic: true,
      friction: 0.06,
      restitution: 0.62,
      label: "lotteryRoof",
      render: { fillStyle: "#334155", visible: true },
    });
    var goal = Bodies.rectangle(W / 2, H - 120, 140, 36, {
      isStatic: true,
      isSensor: true,
      label: "lotteryGoal",
      render: { fillStyle: "rgba(94, 234, 212, 0.15)", visible: true, strokeStyle: "#5eead4", lineWidth: 1 },
    });

    wallsComposite = Composite.create({ bodies: [left, right, floor, roof, goal], label: "walls" });
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
    if (collisionHandler && engine) {
      Events.off(engine, "collisionStart", collisionHandler);
    }
    collisionHandler = null;
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

  /** Remove other balls from the same finish row (orig slot) as the winner; count of balls was tied to that slot. */
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
    for (var i = 0; i < order.length; i++) {
      var row = order[i];
      var team = teamById(row.teamId);
      if (!team) continue;
      var slot = row.origSlot;
      var ballCount = slot * BALLS_MULTIPLIER;
      var cols = defaultHex(team.primary, team.secondary, team.text);
      for (var j = 0; j < ballCount; j++) {
        var x = rng(64, W - 64);
        var y = rng(100, 268);
        var ball = Bodies.circle(x, y, BALL_R, {
          isStatic: !!holdUntilRelease,
          restitution: 0.72,
          friction: 0.02,
          frictionAir: 0.00022,
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

  function attachCollision() {
    if (!engine || collisionHandler) return;
    collisionHandler = function (ev) {
      if (roundComplete || !released) return;
      var pairs = ev.pairs;
      for (var i = 0; i < pairs.length; i++) {
        var a = pairs[i].bodyA;
        var b = pairs[i].bodyB;
        var ball = a.label === "lotteryBall" ? a : b.label === "lotteryBall" ? b : null;
        var goal = a.label === "lotteryGoal" ? a : b.label === "lotteryGoal" ? b : null;
        if (!ball || !goal) continue;

        roundComplete = true;
        var winId = ball.lotteryTeamId;
        var name = ball.lotteryTeamName || "Team";
        removeSiblingBalls(ball);
        var moved = applyWinnerMove(winId);
        drawsCompleted += 1;
        drawLog.push({ draw: drawsCompleted, name: name, moved: moved });

        setStatus(
          "Draw " +
            drawsCompleted +
            ": " +
            name +
            (moved ? " moved up " + moved + " spot(s)." : " could not move (caps).")
        );

        for (var j = 0; j < balls.length; j++) {
          Body.setVelocity(balls[j], { x: 0, y: 0 });
          Body.setAngularVelocity(balls[j], 0);
          balls[j].isStatic = true;
        }
        btnRelease.disabled = true;

        renderOrderSummary();
        renderDrawLog();

        if (drawsCompleted >= MAX_DRAWS) {
          destroyEngine();
          btnPrepare.disabled = true;
          setStatus("Both draws complete. Final order is shown above. New lottery to reset.");
        } else {
          destroyEngine();
          btnPrepare.disabled = false;
          if (btnPrepare) btnPrepare.textContent = "Prepare draw 2";
          setStatus("Draw " + drawsCompleted + " done. Prepare the machine for the second draw.");
        }
        return;
      }
    };
    Events.on(engine, "collisionStart", collisionHandler);
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
      selectionsLocked = true;
      setSelectsDisabled(true);
      renderDrawLog();
      if (elResults) elResults.hidden = true;
    }

    destroyEngine();
    ensureEngine();
    attachCollision();
    spawnBallsFromOrder(true);
    engine.gravity.y = 0;
    renderOrderSummary();
    setStatus("Draw " + (drawsCompleted + 1) + " of " + MAX_DRAWS + ". Release balls when ready.");
    btnRelease.disabled = false;
    btnPrepare.disabled = true;
  }

  function onRelease() {
    if (!engine || roundComplete) return;
    released = true;
    engine.gravity.y = 1.08;
    var rng = function (a, b) {
      return a + Math.random() * (b - a);
    };
    for (var j = 0; j < balls.length; j++) {
      balls[j].isStatic = false;
      /* Wider velocity spread + slight upward kick breaks stacked grids and scrambles order. */
      Body.setVelocity(balls[j], {
        x: rng(-2.2, 2.2),
        y: rng(-0.85, 2.85),
      });
      Body.setAngularVelocity(balls[j], rng(-0.42, 0.42));
    }
    btnRelease.disabled = true;
    setStatus("Balls in play… first through the goal wins this draw.");
  }

  function onReset() {
    destroyEngine();
    drawsCompleted = 0;
    drawLog = [];
    order = [];
    startIndex = {};
    drops = {};
    rise = {};
    roundComplete = false;
    released = false;
    selectionsLocked = false;
    setSelectsDisabled(false);
    if (btnPrepare) btnPrepare.textContent = "Prepare machine";
    if (elResults) elResults.hidden = true;
    if (elResultsList) elResultsList.innerHTML = "";
    btnPrepare.disabled = false;
    btnRelease.disabled = true;
    rebuildOrderFromSlots();
    setStatus("New lottery. Assign 17th–24th places, then prepare.");
  }

  if (btnPrepare) btnPrepare.addEventListener("click", onPrepare);
  if (btnRelease) btnRelease.addEventListener("click", onRelease);
  if (btnReset) btnReset.addEventListener("click", onReset);

  initSlotUI();
  setStatus("Assign teams to 17th through 24th place, then prepare the machine.");
})();
