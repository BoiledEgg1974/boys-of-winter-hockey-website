/**
 * BOWL Boost Lottery — client-side weighted pool (matches Discord/lottery.py semantics).
 * Triple range: each integer in [lo, hi) appears 3×; single range: each integer once; end exclusive.
 */
(function () {
  "use strict";

  function $(id) {
    return document.getElementById(id);
  }

  var elTripleLo = $("bl-triple-lo");
  var elTripleHi = $("bl-triple-hi");
  var elSingleLo = $("bl-single-lo");
  var elSingleHi = $("bl-single-hi");
  var elGoldN = $("bl-gold-n");
  var elSilverN = $("bl-silver-n");
  var elBtnGen = $("bl-btn-generate");
  var elBtnDraw = $("bl-btn-draw");
  var elBtnReset = $("bl-btn-reset");
  var elStatus = $("bl-status");
  var elPoolSummary = $("bl-pool-summary");
  var elResults = $("bl-results");
  var elStale = $("bl-pool-stale");

  if (!elBtnGen || !elBtnDraw) return;

  var ticketList = [];
  var poolFingerprint = "";

  function readInt(el, fallback) {
    var v = parseInt(String(el && el.value).trim(), 10);
    return Number.isFinite(v) ? v : fallback;
  }

  function setStatus(msg) {
    if (elStatus) elStatus.textContent = msg || "";
  }

  function fingerprintParams() {
    return [
      readInt(elTripleLo, 0),
      readInt(elTripleHi, 0),
      readInt(elSingleLo, 0),
      readInt(elSingleHi, 0),
    ].join("|");
  }

  function markStaleIfParamsChanged() {
    if (!elStale) return;
    if (!ticketList.length) {
      elStale.hidden = true;
      return;
    }
    elStale.hidden = fingerprintParams() === poolFingerprint;
  }

  function validateRanges() {
    var tLo = readInt(elTripleLo, 0);
    var tHi = readInt(elTripleHi, 0);
    var sLo = readInt(elSingleLo, 0);
    var sHi = readInt(elSingleHi, 0);
    if (!(tHi > tLo)) return "Rounds 2–3: end must be greater than start (half-open [start, end)).";
    if (!(sHi > sLo)) return "Rounds 4–8: end must be greater than start.";
    return null;
  }

  function generateTickets() {
    var err = validateRanges();
    if (err) return err;
    var tLo = readInt(elTripleLo, 0);
    var tHi = readInt(elTripleHi, 0);
    var sLo = readInt(elSingleLo, 0);
    var sHi = readInt(elSingleHi, 0);
    var list = [];
    var n;
    for (n = tLo; n < tHi; n++) {
      list.push(n, n, n);
    }
    for (n = sLo; n < sHi; n++) {
      list.push(n);
    }
    ticketList = list;
    poolFingerprint = fingerprintParams();
    if (elStale) elStale.hidden = true;
    var uniq = {};
    var i;
    for (i = 0; i < ticketList.length; i++) uniq[ticketList[i]] = true;
    var u = Object.keys(uniq).length;
    if (elPoolSummary) {
      elPoolSummary.hidden = false;
      elPoolSummary.textContent =
        "Pool: " + ticketList.length + " tickets · " + u + " unique pick numbers.";
    }
    elBtnDraw.disabled = false;
    if (elResults) elResults.innerHTML = "";
    setStatus("Pool ready — set gold/silver counts and execute draw.");
    return null;
  }

  function drawExecute() {
    if (!ticketList.length) return "Generate the ticket pool first.";
    var g = Math.max(0, readInt(elGoldN, 0));
    var s = Math.max(0, readInt(elSilverN, 0));
    var need = g + s;
    if (need === 0) return "Set at least one gold or silver winner.";

    var uniq = {};
    var j;
    for (j = 0; j < ticketList.length; j++) uniq[ticketList[j]] = true;
    var uniqueCount = Object.keys(uniq).length;
    if (need > uniqueCount) {
      return (
        "Not enough unique numbers in the pool. Need " +
        need +
        " unique winners but only " +
        uniqueCount +
        " distinct values exist."
      );
    }

    var pool = ticketList.slice();
    var picked = [];
    var pickedSet = {};
    while (picked.length < need) {
      var candidates = [];
      var idx;
      for (idx = 0; idx < pool.length; idx++) {
        if (!pickedSet[pool[idx]]) candidates.push(idx);
      }
      if (!candidates.length) return "Could not fill all winner slots — pool exhausted.";
      var pickI = candidates[Math.floor(Math.random() * candidates.length)];
      var ticket = pool.splice(pickI, 1)[0];
      pickedSet[ticket] = true;
      picked.push(ticket);
    }

    var goldWinners = picked.slice(0, g);
    var silverWinners = picked.slice(g, g + s);
    var removeSet = pickedSet;
    ticketList = ticketList.filter(function (t) {
      return !removeSet[t];
    });

    if (elResults) {
      var gh = goldWinners.length ? goldWinners.join(", ") : "—";
      var sh = silverWinners.length ? silverWinners.join(", ") : "—";
      elResults.innerHTML =
        '<p class="boost-lottery-results__gold"><strong>Gold winners</strong><br><span class="boost-lottery-results__nums">' +
        escapeHtml(gh) +
        "</span></p>" +
        '<p class="boost-lottery-results__silver"><strong>Silver winners</strong><br><span class="boost-lottery-results__nums">' +
        escapeHtml(sh) +
        "</span></p>";
    }
    if (elPoolSummary) {
      var uniq2 = {};
      for (j = 0; j < ticketList.length; j++) uniq2[ticketList[j]] = true;
      elPoolSummary.textContent =
        "Remaining pool: " + ticketList.length + " tickets · " + Object.keys(uniq2).length + " unique pick numbers.";
    }
    elBtnDraw.disabled = ticketList.length === 0;
    return "DRAW_OK";
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function resetAll() {
    ticketList = [];
    poolFingerprint = "";
    elBtnDraw.disabled = true;
    if (elPoolSummary) {
      elPoolSummary.hidden = true;
      elPoolSummary.textContent = "";
    }
    if (elResults) elResults.innerHTML = "";
    if (elStale) elStale.hidden = true;
    setStatus("Pool cleared. Adjust parameters if needed, then generate again.");
  }

  function onGenerate() {
    var err = generateTickets();
    if (err) {
      setStatus(err);
      elBtnDraw.disabled = true;
      return;
    }
  }

  function onDraw() {
    var msg = drawExecute();
    if (msg === "DRAW_OK") {
      setStatus("Draw complete — unique winners removed from the pool. Generate again or reset.");
    } else {
      setStatus(msg);
    }
  }

  function onReset() {
    resetAll();
  }

  function bindInputs() {
    var els = [elTripleLo, elTripleHi, elSingleLo, elSingleHi];
    var k;
    for (k = 0; k < els.length; k++) {
      if (!els[k]) continue;
      els[k].addEventListener("input", markStaleIfParamsChanged);
      els[k].addEventListener("change", markStaleIfParamsChanged);
    }
  }

  elBtnGen.addEventListener("click", onGenerate);
  elBtnDraw.addEventListener("click", onDraw);
  elBtnReset.addEventListener("click", onReset);
  bindInputs();
})();
