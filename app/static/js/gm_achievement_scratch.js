/**
 * Achievement reward scratch tickets — three spots, then claim AP × multiplier.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-ach-scratch]");
  if (!root) return;

  var CLEAR_THRESHOLD = 0.55;
  var startUrl = root.getAttribute("data-start-url") || "";
  var claimUrl = root.getAttribute("data-claim-url") || "";
  var previewMode = root.getAttribute("data-preview") === "1";
  var csrfEl = document.getElementById("gm-ach-csrf");
  var csrf = (csrfEl && csrfEl.value) || "";
  var dialog = document.getElementById("gm-ach-scratch-dialog");
  var fwLayer = document.getElementById("gm-ach-fw");
  var reducedMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var FW_BURSTS = {
    diamond: [
      { x: "18%", y: "10%", kind: "diamond", delay: 0, scale: 1.1 },
      { x: "82%", y: "14%", kind: "diamond", delay: 160, scale: 1.25 },
      { x: "50%", y: "8%", kind: "perfect", delay: 320, scale: 1 },
      { x: "24%", y: "68%", kind: "diamond", delay: 480, scale: 0.95 },
      { x: "76%", y: "72%", kind: "gold", delay: 620, scale: 1.1 },
    ],
    perfect: [
      { x: "50%", y: "6%", kind: "perfect", delay: 0, scale: 1.35 },
      { x: "16%", y: "16%", kind: "gold", delay: 120, scale: 1.15 },
      { x: "84%", y: "14%", kind: "diamond", delay: 200, scale: 1.2 },
      { x: "28%", y: "48%", kind: "perfect", delay: 340, scale: 1.05 },
      { x: "72%", y: "52%", kind: "gold", delay: 420, scale: 1.15 },
      { x: "20%", y: "78%", kind: "diamond", delay: 560, scale: 1 },
      { x: "80%", y: "80%", kind: "perfect", delay: 680, scale: 1.2 },
    ],
  };

  function previewPayload(isClaim) {
    var title = root.getAttribute("data-preview-title") || "The Pinnacle";
    var cells = [1, 3, 3];
    var ticket = 7;
    var mult = Number(root.getAttribute("data-preview-multiplier") || 3);
    var total = ticket * mult;
    return {
      ok: true,
      title: title,
      cells: cells,
      ticket_ap: ticket,
      multiplier: mult,
      total_ap: isClaim ? total : null,
      balance: isClaim ? total : null,
      unclaimed: isClaim ? 0 : 1,
      claimable: !isClaim,
    };
  }

  function postJson(url, body) {
    if (previewMode) {
      return Promise.resolve({
        ok: true,
        status: 200,
        data: previewPayload(url === claimUrl),
      });
    }
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify(Object.assign({ csrf_token: csrf }, body)),
    }).then(function (r) {
      return r.json().then(function (data) {
        return { ok: r.ok && data && data.ok, status: r.status, data: data || {} };
      });
    });
  }

  function paintFoil(canvas) {
    var ctx = canvas.getContext("2d");
    var w = canvas._cssW || 120;
    var h = canvas._cssH || 90;
    ctx.globalCompositeOperation = "source-over";
    var g = ctx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, "#8b9bb0");
    g.addColorStop(0.35, "#d7dee8");
    g.addColorStop(0.55, "#9aa8ba");
    g.addColorStop(1, "#5c6b7e");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    var i;
    for (i = 0; i < 40; i += 1) {
      ctx.fillStyle = "rgba(255,255,255," + (0.04 + Math.random() * 0.08) + ")";
      ctx.fillRect(Math.random() * w, Math.random() * h, 6 + Math.random() * 14, 1);
    }
    ctx.fillStyle = "rgba(20, 28, 40, 0.55)";
    ctx.font = "700 11px Bebas Neue, Impact, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("SCRATCH", w / 2, h / 2);
  }

  function sizeCanvas(canvas, wrap) {
    var rect = wrap.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(72, Math.floor(rect.width));
    var h = Math.max(64, Math.floor(rect.height));
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

  function fillCells(card, cells) {
    var prizes = card.querySelectorAll(".gm-ach-scratch__prize");
    cells.forEach(function (n, i) {
      if (prizes[i]) prizes[i].textContent = String(n);
    });
  }

  function celebrateTier(data) {
    var mult = Number(data.multiplier) || 1;
    var ticket = Number(data.ticket_ap) || 0;
    if (mult >= 4 || ticket >= 8) return "perfect";
    return "diamond";
  }

  function playSfx(name, opts) {
    var src = document.querySelector('[data-ach-sfx="' + name + '"]');
    if (!src || !src.getAttribute("src")) return;
    var delay = (opts && opts.delay) || 0;
    var vol = opts && opts.volume != null ? opts.volume : 0.85;
    var run = function () {
      var clip = src.cloneNode(true);
      clip.volume = Math.max(0, Math.min(1, vol));
      var played = clip.play();
      if (played && played.catch) played.catch(function () {});
    };
    if (delay) window.setTimeout(run, delay);
    else run();
  }

  function playRedeemSfx(tier) {
    playSfx("crackle", { volume: 0.75 });
    playSfx("fireworks", { volume: 0.7, delay: 40 });
    playSfx("confetti", { volume: 0.75, delay: 80 });
    if (tier === "perfect") {
      playSfx("cheer-big", { volume: 0.5, delay: 120 });
      playSfx("explosion", { volume: 0.65, delay: 160 });
    } else {
      playSfx("cheer", { volume: 0.5, delay: 120 });
    }
  }

  function spawnFireworks(tier) {
    if (!fwLayer || reducedMotion) return;
    var bursts = FW_BURSTS[tier] || FW_BURSTS.diamond;
    fwLayer.replaceChildren();
    bursts.forEach(function (burst) {
      var el = document.createElement("span");
      el.className = "gm-ach-fw__sprite gm-ach-fw--" + burst.kind;
      el.style.left = burst.x;
      el.style.top = burst.y;
      el.style.animationDelay = burst.delay + "ms";
      el.style.transform = "scale(" + burst.scale + ")";
      fwLayer.appendChild(el);
    });
    fwLayer.classList.add("is-on");
    window.setTimeout(
      function () {
        fwLayer.classList.remove("is-on");
        fwLayer.replaceChildren();
      },
      tier === "perfect" ? 5200 : 4200
    );
  }

  function celebrateClaim(data) {
    var tier = celebrateTier(data);
    playRedeemSfx(tier);
    spawnFireworks(tier);
  }

  function showResult(data) {
    var cells = Array.isArray(data.cells) ? data.cells : [];
    var ticket = data.ticket_ap;
    var mult = data.multiplier;
    var total = data.total_ap;
    var titleEl = document.getElementById("gm-ach-scratch-dialog-title");
    var leadEl = document.getElementById("gm-ach-scratch-dialog-lead");
    var mathEl = document.getElementById("gm-ach-scratch-dialog-math");
    var totalEl = document.getElementById("gm-ach-scratch-dialog-total");
    var balEl = document.getElementById("gm-ach-scratch-dialog-balance");
    if (titleEl) titleEl.textContent = data.title || "Achievement reward";
    if (leadEl) leadEl.textContent = "AP has been added to your team balance.";
    if (mathEl) mathEl.textContent = cells.join(" + ") + " = " + ticket + " AP × " + mult;
    if (totalEl) totalEl.textContent = total + " AP added to your balance";
    if (balEl) {
      balEl.textContent =
        data.balance != null ? "New balance: " + data.balance + " AP" : "";
    }
    if (dialog && dialog.showModal) dialog.showModal();
    else if (dialog) dialog.setAttribute("open", "");
    celebrateClaim(data);
  }

  function markClaimed(card, data) {
    card.classList.remove("gm-achievements__card--claimable", "is-flipped");
    card.removeAttribute("role");
    card.removeAttribute("tabindex");
    card.removeAttribute("data-storage-key");
    var status = card.querySelector(".gm-achievements__status");
    if (status) status.textContent = "Completed";
    var ap = card.querySelector(".gm-achievements__ap");
    if (ap && data.cells && data.ticket_ap != null) {
      ap.textContent =
        data.cells.join(" + ") +
        " = " +
        data.ticket_ap +
        " × " +
        data.multiplier +
        " = " +
        data.total_ap +
        " AP";
    }
    if (!data.unclaimed) {
      document.querySelectorAll(".header-tools__link--ach-ready").forEach(function (link) {
        link.classList.remove("header-tools__link--ach-ready");
        link.removeAttribute("aria-label");
      });
    }
  }

  function claimCard(card, cells) {
    if (card._claiming) return;
    card._claiming = true;
    postJson(claimUrl, { storage_key: card.getAttribute("data-storage-key") })
      .then(function (res) {
        if (!res.ok) {
          card._claiming = false;
          return;
        }
        // Ledger credit already landed in POST /claim; the popup only announces it.
        showResult(res.data);
        markClaimed(card, res.data);
      })
      .catch(function () {
        card._claiming = false;
      });
  }

  function bindCell(card, cellEl, cells, index) {
    var wrap = cellEl.querySelector(".gm-ach-scratch__foil-wrap");
    var canvas = cellEl.querySelector("canvas");
    if (!wrap || !canvas) return;
    var state = { revealed: false };
    sizeCanvas(canvas, wrap);
    var drawing = false;

    function localPos(ev) {
      var rect = canvas.getBoundingClientRect();
      var src = ev.touches && ev.touches[0] ? ev.touches[0] : ev;
      return { x: src.clientX - rect.left, y: src.clientY - rect.top };
    }

    function reveal() {
      if (state.revealed) return;
      state.revealed = true;
      canvas.style.opacity = "0";
      canvas.style.pointerEvents = "none";
      cellEl.classList.add("is-revealed");
      var remaining = card.querySelectorAll(".gm-ach-scratch__cell:not(.is-revealed)");
      if (!remaining.length) claimCard(card, cells);
    }

    function go(ev) {
      if (state.revealed) return;
      ev.preventDefault();
      var pos = localPos(ev);
      scratchAt(canvas, pos.x, pos.y, reducedMotion ? 36 : 16);
      spawnSparks(wrap, pos.x, pos.y);
      if (clearPercent(canvas) >= CLEAR_THRESHOLD || reducedMotion) reveal();
    }

    function down(ev) {
      if (state.revealed) return;
      drawing = true;
      go(ev);
    }

    function move(ev) {
      if (!drawing) return;
      go(ev);
    }

    function up() {
      drawing = false;
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
    }
  }

  function activateTicket(card) {
    if (card.classList.contains("is-flipped") || card._starting) return;
    card._starting = true;
    postJson(startUrl, { storage_key: card.getAttribute("data-storage-key") })
      .then(function (res) {
        card._starting = false;
        if (!res.ok || !res.data.cells) return;
        fillCells(card, res.data.cells);
        card.classList.add("is-flipped");
        var back = card.querySelector(".gm-achievements__face--back");
        if (back) back.setAttribute("aria-hidden", "false");
        var cells = card.querySelectorAll(".gm-ach-scratch__cell");
        cells.forEach(function (cellEl, i) {
          bindCell(card, cellEl, res.data.cells, i);
        });
        if (reducedMotion) {
          cells.forEach(function (cellEl) {
            cellEl.classList.add("is-revealed");
            var canvas = cellEl.querySelector("canvas");
            if (canvas) {
              canvas.style.opacity = "0";
              canvas.style.pointerEvents = "none";
            }
          });
          claimCard(card, res.data.cells);
        }
      })
      .catch(function () {
        card._starting = false;
      });
  }

  root.querySelectorAll(".gm-achievements__card--claimable").forEach(function (card) {
    card.addEventListener("click", function (ev) {
      if (card.classList.contains("is-flipped")) return;
      ev.preventDefault();
      activateTicket(card);
    });
    card.addEventListener("keydown", function (ev) {
      if (card.classList.contains("is-flipped")) return;
      if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        activateTicket(card);
      }
    });
  });
})();
