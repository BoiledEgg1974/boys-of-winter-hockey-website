(function (global) {
  "use strict";

  function parseIntField(id, fallback) {
    var el = document.getElementById(id);
    if (!el) return fallback || 0;
    var n = parseInt(el.value || "0", 10);
    return isNaN(n) ? fallback || 0 : n;
  }

  function attachTransferCompensation(ledger) {
    ledger.transfer_compensation = {
      left_acquiring: {
        pta_transfer_fee: parseIntField("pta-left-fee", 0),
        cash_sweetener: parseIntField("pta-left-cash", 0),
      },
      right_acquiring: {
        pta_transfer_fee: parseIntField("pta-right-fee", 0),
        cash_sweetener: parseIntField("pta-right-cash", 0),
      },
    };
    return ledger;
  }

  function wirePtaInputs(onChange) {
    ["pta-left-fee", "pta-left-cash", "pta-right-fee", "pta-right-cash"].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener("input", onChange);
    });
  }

  global.BowlTradePta = {
    attachTransferCompensation: attachTransferCompensation,
    wirePtaInputs: wirePtaInputs,
  };
})(window);
