(function () {
  "use strict";

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function updateLockTimers() {
    var nodes = document.querySelectorAll("[data-lock-at]");
    var now = Date.now();
    nodes.forEach(function (el) {
      var parent = el.closest(".bowl-six-lock") || el;
      var raw = parent.getAttribute("data-lock-at");
      if (!raw) return;
      var target = new Date(raw).getTime();
      if (isNaN(target)) return;
      var diff = Math.max(0, target - now);
      var sec = Math.floor(diff / 1000);
      var d = Math.floor(sec / 86400);
      sec -= d * 86400;
      var h = Math.floor(sec / 3600);
      sec -= h * 3600;
      var m = Math.floor(sec / 60);
      sec -= m * 60;
      var label =
        (d ? d + "d " : "") + pad(h) + ":" + pad(m) + ":" + pad(sec);
      var out = parent.querySelector(".bowl-six-lock__timer, .bowl-six-lock-timer");
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

  function slotForPosition(posKind) {
    if (posKind === "gk") return "gk";
    if (posKind === "def") return ["def1", "def2"];
    return ["fwd1", "fwd2", "fwd3"];
  }

  function positionKindFromFilter(f) {
    return f || "";
  }

  function assignPlayer(slot, player) {
    var input = document.getElementById("slot-input-" + slot);
    var nameEl = document.getElementById("slot-name-" + slot);
    if (input) input.value = player.id;
    if (nameEl) nameEl.textContent = player.name;
  }

  function teamCounts() {
    var counts = {};
    document.querySelectorAll("[id^='slot-input-']").forEach(function (inp) {
      var pid = parseInt(inp.value, 10);
      if (!pid) return;
      var row = players.find(function (p) {
        return p.id === pid;
      });
      if (row && row.team_id) {
        counts[row.team_id] = (counts[row.team_id] || 0) + 1;
      }
    });
    return counts;
  }

  function renderPool() {
    if (!poolBody) return;
    var q = (searchEl && searchEl.value || "").toLowerCase();
    var pf = positionKindFromFilter(posEl && posEl.value);
    var html = "";
    var counts = teamCounts();
    players.forEach(function (p) {
      if (q && p.name.toLowerCase().indexOf(q) < 0) return;
      if (pf && p.position_kind !== pf) return;
      var blocked = p.blocked;
      var teamFull = p.team_id && counts[p.team_id] >= 3;
      var dis = blocked || teamFull || !cfg.editable;
      html +=
        "<tr class=\"" +
        (dis ? "bowl-six-pool__row--disabled" : "") +
        "\"><td><input type=\"radio\" name=\"pool_pick\" value=\"" +
        p.id +
        "\" data-pos=\"" +
        p.position_kind +
        "\" " +
        (dis ? "disabled" : "") +
        "></td><td>" +
        p.name +
        (blocked ? " <span class=\"muted\">(last week)</span>" : "") +
        "</td><td>—</td><td>—</td></tr>";
    });
    poolBody.innerHTML = html || "<tr><td colspan=\"4\" class=\"muted\">No players</td></tr>";
    poolBody.querySelectorAll("input[name=pool_pick]").forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (!activeSlot) {
          var pk = radio.getAttribute("data-pos");
          var slots = slotForPosition(pk);
          activeSlot = Array.isArray(slots) ? slots.find(function (s) {
            return !document.getElementById("slot-input-" + s).value;
          }) : slots;
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
  }

  function loadPlayers() {
    var url = cfg.playersUrl;
    fetch(url, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (data) {
        players = (data.players || []).map(function (p) {
          var pos = (p.position || "").toUpperCase();
          var kind = pos.startsWith("G") ? "gk" : ["D", "LD", "RD"].indexOf(pos) >= 0 || pos === "DEF" ? "def" : "fwd";
          return {
            id: p.id,
            name: p.name,
            team_id: p.team_id,
            blocked: p.blocked,
            position_kind: kind,
          };
        });
        renderPool();
      })
      .catch(function () {
        if (poolBody) poolBody.innerHTML = "<tr><td colspan=\"4\">Failed to load players.</td></tr>";
      });
  }

  document.querySelectorAll(".bowl-six-slot").forEach(function (slotEl) {
    slotEl.addEventListener("click", function () {
      activeSlot = slotEl.getAttribute("data-slot");
    });
  });

  if (searchEl) searchEl.addEventListener("input", renderPool);
  if (posEl) posEl.addEventListener("change", renderPool);

  var capBtn = document.getElementById("bowl-six-pick-captain");
  if (capBtn) {
    capBtn.addEventListener("click", function () {
      var picks = [];
      document.querySelectorAll("[id^='slot-input-']").forEach(function (inp) {
        if (inp.id.indexOf("gk") >= 0 && inp.value) return;
        if (inp.value) picks.push({ slot: inp.id.replace("slot-input-", ""), id: parseInt(inp.value, 10) });
      });
      if (!picks.length) {
        alert("Select skaters first.");
        return;
      }
      var names = picks.map(function (p, i) {
        return i + 1 + ") " + (document.getElementById("slot-name-" + p.slot) || {}).textContent;
      });
      var choice = prompt("Captain (enter number):\n" + names.join("\n"));
      var idx = parseInt(choice, 10) - 1;
      if (idx >= 0 && idx < picks.length) {
        document.getElementById("bowl-six-captain-id").value = picks[idx].id;
        alert("Captain set.");
      }
    });
  }

  loadPlayers();
})();
