/**
 * Draft Hub boost lottery — scratch extras, weighted pool, and auto-applied draws.
 */
(function () {
  "use strict";

  var shell = document.getElementById("dh-boost-lottery-shell");
  if (!shell) return;

  var root = document.getElementById("dh-boost-lottery-root");
  var boostUrl = shell.getAttribute("data-boost-url") || "";
  var generateUrl = shell.getAttribute("data-generate-url") || "";
  var drawUrl = shell.getAttribute("data-draw-url") || "";
  var resetUrl = shell.getAttribute("data-reset-url") || "";
  var goLiveUrl = shell.getAttribute("data-go-live-url") || "";
  var adminDraftUrl = shell.getAttribute("data-admin-draft-url") || "";
  var csrf = shell.getAttribute("data-csrf") || "";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function postJson(url, body) {
    return fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(Object.assign({ csrf_token: csrf }, body || {})),
    }).then(function (r) {
      return r.json();
    });
  }

  function syncDrawTotals(bl) {
    var goldEl = document.getElementById("bl-gold-n");
    var silverEl = document.getElementById("bl-silver-n");
    var totalsEl = document.getElementById("bl-draw-totals");
    if (!bl) return;
    if (goldEl) {
      goldEl.value = String(bl.baseline_gold || 4);
      goldEl.setAttribute("data-draw-gold", String(bl.draw_gold || 0));
      goldEl.readOnly = true;
    }
    if (silverEl) {
      silverEl.value = String(bl.baseline_silver || 6);
      silverEl.setAttribute("data-draw-silver", String(bl.draw_silver || 0));
      silverEl.readOnly = true;
    }
    if (totalsEl) {
      totalsEl.textContent =
        "Draw totals: " + (bl.draw_gold || 0) + " gold · " + (bl.draw_silver || 0) + " silver";
    }
  }

  function hasScratchHost() {
    return shell.getAttribute("data-has-scratch") === "1" || !!document.getElementById("dh-boost-scratch-host");
  }

  function setShellVisible(visible) {
    shell.hidden = !visible;
  }

  function renderControls(bl) {
    var hasScratch = hasScratchHost();
    if (!bl || !bl.enabled) {
      if (hasScratch) {
        setShellVisible(true);
        if (root) root.innerHTML = "";
      } else {
        setShellVisible(false);
      }
      return;
    }
    if (!bl.show_panel) {
      if (hasScratch) {
        setShellVisible(true);
        if (root) root.innerHTML = "";
      } else {
        setShellVisible(false);
      }
      return;
    }
    if (!root) return;
    setShellVisible(true);
    var params = bl.params || {};
    var pendingEvent = !!bl.no_draft_event || bl.draft_status === "pending";
    var canAdmin = !!bl.can_admin && bl.draft_status === "setup" && !pendingEvent;
    var pool = bl.pool_summary || {};
    var html = "";

    html += '<section class="boost-lottery-panel draft-lottery-panel--controls card">';
    html += '<h2 class="draft-lottery-panel__title">Boost lottery</h2>';
    if (pendingEvent) {
      html += '<p class="draft-lottery-panel__hint muted">Scratch tickets are open for the upcoming draft';
      if (bl.draft_year) {
        html += " (" + esc(bl.draft_year) + ")";
      }
      html += ". Create the draft event before generating the pool or going live.</p>";
      if (bl.can_admin && adminDraftUrl) {
        html += '<p class="draft-lottery-panel__hint"><a class="button secondary" href="' + esc(adminDraftUrl) + '">Create draft in Admin</a></p>';
      } else if (bl.go_live_blocker) {
        html += '<p class="draft-lottery-panel__hint muted">' + esc(bl.go_live_blocker) + "</p>";
      }
      html += '<p class="muted">Draw totals: <strong>' + esc(bl.draw_gold) + " gold · " + esc(bl.draw_silver) + " silver</strong>";
      if (bl.extra_gold || bl.extra_silver) {
        html += " (includes scratch extras +" + esc(bl.extra_gold) + "G / +" + esc(bl.extra_silver) + "S)";
      }
      html += "</p>";
      html += "</section>";
      root.innerHTML = html;
      syncDrawTotals(bl);
      return;
    }
    if (bl.draft_status === "live") {
      html += '<p class="draft-lottery-panel__hint muted">Boost picks for this draft are locked in. Scratch tickets above are practice-only while the draft is live.</p>';
    } else {
      html += '<p class="draft-lottery-panel__hint muted">Scratch tickets for extra gold and silver, then draw unique pick numbers. Results apply to this draft automatically.</p>';
    }

    if (canAdmin) {
      html += '<p class="draft-lottery-panel__hint muted" id="dh-bl-pool-stale" hidden>Numbers changed since the last pool build — click <strong>Generate pool</strong> again.</p>';
      html += '<fieldset class="boost-lottery-fieldset"><legend class="boost-lottery-fieldset__legend">Rounds 2–3 (×3 tickets per pick number)</legend>';
      html += '<div class="boost-lottery-range-row">';
      html += '<label class="boost-lottery-label">Start <span class="muted">(inclusive)</span><input type="number" id="bl-triple-lo" class="boost-lottery-input" value="' + esc(params.triple_lo) + '" min="0" step="1"></label>';
      html += '<label class="boost-lottery-label">End <span class="muted">(exclusive)</span><input type="number" id="bl-triple-hi" class="boost-lottery-input" value="' + esc(params.triple_hi) + '" min="1" step="1"></label>';
      html += "</div></fieldset>";
      html += '<fieldset class="boost-lottery-fieldset"><legend class="boost-lottery-fieldset__legend">Rounds 4–8 (×1 ticket per pick number)</legend>';
      html += '<div class="boost-lottery-range-row">';
      html += '<label class="boost-lottery-label">Start <span class="muted">(inclusive)</span><input type="number" id="bl-single-lo" class="boost-lottery-input" value="' + esc(params.single_lo) + '" min="0" step="1"></label>';
      html += '<label class="boost-lottery-label">End <span class="muted">(exclusive)</span><input type="number" id="bl-single-hi" class="boost-lottery-input" value="' + esc(params.single_hi) + '" min="1" step="1"></label>';
      html += "</div></fieldset>";
      html += '<fieldset class="boost-lottery-fieldset"><legend class="boost-lottery-fieldset__legend">Winners this draw</legend>';
      html += '<div class="boost-lottery-range-row">';
      html += '<label class="boost-lottery-label">Baseline gold<input type="number" id="bl-gold-n" class="boost-lottery-input" value="' + esc(bl.baseline_gold) + '" readonly tabindex="-1"></label>';
      html += '<label class="boost-lottery-label">Baseline silver<input type="number" id="bl-silver-n" class="boost-lottery-input" value="' + esc(bl.baseline_silver) + '" readonly tabindex="-1"></label>';
      html += "</div>";
      html += '<p class="boost-lottery-draw-totals muted" id="bl-draw-totals">Draw totals: ' + esc(bl.draw_gold) + " gold · " + esc(bl.draw_silver) + " silver</p>";
      html += "</fieldset>";
      html += '<div class="draft-lottery-actions boost-lottery-actions">';
      html += '<button type="button" class="btn-draft-lottery btn-draft-lottery--primary" id="dh-bl-generate">Generate pool</button>';
      html += '<button type="button" class="btn-draft-lottery" id="dh-bl-draw"' + (bl.pool_ready ? "" : " disabled") + ">Execute draw</button>";
      html += '<button type="button" class="btn-draft-lottery btn-draft-lottery--ghost" id="dh-bl-reset">Reset pool</button>';
      html += "</div>";
      html += '<p class="draft-lottery-status muted" id="dh-bl-status" aria-live="polite"></p>';
    } else {
      html += '<p class="muted">Draw totals: <strong>' + esc(bl.draw_gold) + " gold · " + esc(bl.draw_silver) + " silver</strong>";
      if (bl.extra_gold || bl.extra_silver) {
        html += " (includes scratch extras +" + esc(bl.extra_gold) + "G / +" + esc(bl.extra_silver) + "S)";
      }
      html += "</p>";
    }

    if (bl.pool_ready || pool.ticket_count) {
      html += '<p class="boost-lottery-output__summary muted" id="bl-pool-summary">Pool: ' + esc(pool.ticket_count || 0) + " tickets · " + esc(pool.unique_count || 0) + " unique pick numbers.</p>";
    }
    if ((bl.last_gold && bl.last_gold.length) || (bl.last_silver && bl.last_silver.length)) {
      html += '<div class="boost-lottery-pick-results"><p class="boost-lottery-output__label">Latest draw</p><div class="boost-lottery-output__results">';
      html += '<p class="boost-lottery-results__gold"><strong>Gold winners</strong><br><span class="boost-lottery-results__nums">' + esc((bl.last_gold || []).join(", ") || "—") + "</span></p>";
      html += '<p class="boost-lottery-results__silver"><strong>Silver winners</strong><br><span class="boost-lottery-results__nums">' + esc((bl.last_silver || []).join(", ") || "—") + "</span></p>";
      html += "</div></div>";
    }
    if (bl.applied_gold || bl.applied_silver) {
      html += '<p class="muted">Applied on this draft: ' + esc(bl.applied_gold) + " gold · " + esc(bl.applied_silver) + " silver slot tags.</p>";
    }
    html += "</section>";

    root.innerHTML = html;
    syncDrawTotals(bl);
    bindControls(bl);
  }

  function readInt(id, fallback) {
    var el = document.getElementById(id);
    var v = parseInt(String(el && el.value).trim(), 10);
    return Number.isFinite(v) ? v : fallback;
  }

  function setStatus(msg) {
    var el = document.getElementById("dh-bl-status");
    if (el) el.textContent = msg || "";
  }

  function bindControls(bl) {
    if (!bl.can_admin || bl.draft_status !== "setup") return;
    var genBtn = document.getElementById("dh-bl-generate");
    var drawBtn = document.getElementById("dh-bl-draw");
    var resetBtn = document.getElementById("dh-bl-reset");
    if (genBtn) {
      genBtn.addEventListener("click", function () {
        genBtn.disabled = true;
        postJson(generateUrl, {
          triple_lo: readInt("bl-triple-lo", 0),
          triple_hi: readInt("bl-triple-hi", 0),
          single_lo: readInt("bl-single-lo", 0),
          single_hi: readInt("bl-single-hi", 0),
        })
          .then(function (res) {
            genBtn.disabled = false;
            if (!res || !res.ok) {
              setStatus((res && res.error) || "Could not generate pool.");
              return;
            }
            renderControls(res.boost_lottery);
            setStatus("Pool ready — execute draw when scratch extras are set.");
            window.dispatchEvent(new CustomEvent("dh-boost-lottery-updated"));
          })
          .catch(function () {
            genBtn.disabled = false;
          });
      });
    }
    if (drawBtn) {
      drawBtn.addEventListener("click", function () {
        if (!window.confirm("Execute the boost lottery draw? Winning picks will be tagged on this draft and tracker totals will update.")) return;
        drawBtn.disabled = true;
        postJson(drawUrl, {})
          .then(function (res) {
            drawBtn.disabled = false;
            if (!res || !res.ok) {
              setStatus((res && res.error) || "Draw failed.");
              return;
            }
            renderControls(res.boost_lottery);
            setStatus("Draw complete — boost picks applied to draft slots and tracker.");
            window.dispatchEvent(new CustomEvent("dh-boost-lottery-updated"));
          })
          .catch(function () {
            drawBtn.disabled = false;
          });
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        if (!window.confirm("Clear the ticket pool? Applied boost tags on this draft are not removed.")) return;
        postJson(resetUrl, {}).then(function (res) {
          if (!res || !res.ok) {
            setStatus((res && res.error) || "Reset failed.");
            return;
          }
          renderControls(res.boost_lottery);
          setStatus("Pool cleared.");
          window.dispatchEvent(new CustomEvent("dh-boost-lottery-updated"));
        });
      });
    }
  }

  function renderFromPayload(bl) {
    renderControls(bl);
  }

  function poll() {
    if (!boostUrl) return;
    fetch(boostUrl, { credentials: "same-origin" })
      .then(function (r) {
        return r.json();
      })
      .then(function (res) {
        if (!res || !res.boost_lottery) return;
        renderFromPayload(res.boost_lottery);
      })
      .catch(function () {});
  }

  window.dhBoostLotteryRender = renderFromPayload;

  document.addEventListener("DOMContentLoaded", function () {
    poll();
  });
  window.addEventListener("dh-boost-lottery-updated", poll);
})();
