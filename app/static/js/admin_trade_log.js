(function () {
  var searchUrl = window.MANUAL_TRADE_LOG_PLAYER_SEARCH_URL || "";
  var debounceMs = 220;

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function closePanel(panel) {
    panel.classList.remove("is-open");
    panel.innerHTML = "";
  }

  function bindPlayerAutocomplete(input) {
    if (!input || input.dataset.autocompleteBound === "1") return;
    input.dataset.autocompleteBound = "1";
    var wrap = input.closest(".manual-trade-log__asset-input-wrap");
    var panel = wrap ? wrap.querySelector(".manual-trade-log__player-suggestions") : null;
    if (!panel || !searchUrl) return;

    var timer = null;

    function pickPlayer(player) {
      input.value = player.full_name || "";
      closePanel(panel);
      input.focus();
    }

    function renderResults(results) {
      panel.innerHTML = "";
      if (!results || !results.length) {
        closePanel(panel);
        return;
      }
      results.forEach(function (p) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.setAttribute("role", "option");
        var teamLabel = (p.team_abbr || p.team || "FA").trim();
        btn.innerHTML =
          "<strong>" + escapeHtml(p.full_name) + "</strong><br>" +
          '<span class="meta">#' + escapeHtml(String(p.id)) + " · " +
          escapeHtml(p.position || "—") + " · " + escapeHtml(teamLabel) + "</span>";
        btn.addEventListener("click", function () {
          pickPlayer(p);
        });
        panel.appendChild(btn);
      });
      panel.classList.add("is-open");
    }

    input.addEventListener("blur", function () {
      setTimeout(function () {
        closePanel(panel);
      }, 200);
    });
    panel.addEventListener("mousedown", function (e) {
      e.preventDefault();
    });
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2 || /^\d+$/.test(q)) {
        closePanel(panel);
        return;
      }
      timer = setTimeout(function () {
        fetch(searchUrl + "?q=" + encodeURIComponent(q))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            renderResults(data.results || []);
          })
          .catch(function () {
            closePanel(panel);
          });
      }, debounceMs);
    });
  }

  function assetRowTemplate() {
    var row = document.createElement("div");
    row.className = "manual-trade-log__asset-row";
    row.innerHTML =
      '<label class="manual-trade-log__asset-label">' +
        '<span class="manual-trade-log__asset-label-text">Player</span>' +
        '<span class="manual-trade-log__asset-input-wrap">' +
          '<input type="text" name="PLACEHOLDER_player[]" value="" autocomplete="off" aria-autocomplete="list" placeholder="Search player name…" class="manual-trade-log__player-input">' +
          '<div class="autocomplete-panel manual-trade-log__player-suggestions" role="listbox" aria-label="Player suggestions"></div>' +
        "</span>" +
      "</label>" +
      '<label class="manual-trade-log__asset-label">' +
        '<span class="manual-trade-log__asset-label-text">Picks / other</span>' +
        '<input type="text" name="PLACEHOLDER_other[]" value="" placeholder="Draft picks, cash, conditions…" class="manual-trade-log__other-input">' +
      "</label>" +
      '<button type="button" class="admin-news-body-btn manual-trade-log__remove-row" aria-label="Remove asset row">Remove</button>';
    return row;
  }

  function bindRemoveButton(btn) {
    if (!btn || btn.dataset.bound === "1") return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", function () {
      var list = btn.closest(".manual-trade-log__asset-list");
      var row = btn.closest(".manual-trade-log__asset-row");
      if (!list || !row) return;
      if (list.querySelectorAll(".manual-trade-log__asset-row").length <= 1) {
        row.querySelectorAll("input").forEach(function (input) {
          input.value = "";
        });
        return;
      }
      row.remove();
    });
  }

  function bindAssetList(list) {
    if (!list || list.dataset.bound === "1") return;
    list.dataset.bound = "1";
    var side = list.getAttribute("data-asset-side") || "team_a";
    list.querySelectorAll(".manual-trade-log__player-input").forEach(bindPlayerAutocomplete);
    list.querySelectorAll(".manual-trade-log__remove-row").forEach(bindRemoveButton);

    var addBtn = document.querySelector('.manual-trade-log__add-row[data-asset-side="' + side + '"]');
    if (addBtn && addBtn.dataset.bound !== "1") {
      addBtn.dataset.bound = "1";
      addBtn.addEventListener("click", function () {
        var row = assetRowTemplate();
        row.innerHTML = row.innerHTML.replace(/PLACEHOLDER/g, side);
        list.appendChild(row);
        bindPlayerAutocomplete(row.querySelector(".manual-trade-log__player-input"));
        bindRemoveButton(row.querySelector(".manual-trade-log__remove-row"));
      });
    }
  }

  function updateLogo(select) {
    var img = document.getElementById(select.getAttribute("data-logo-preview") || "");
    if (!img) return;
    var opt = select.options[select.selectedIndex];
    var url = opt ? opt.getAttribute("data-logo-url") : "";
    img.src = url || "";
    img.style.display = url ? "" : "none";
  }

  document.querySelectorAll(".manual-trade-log__asset-list").forEach(bindAssetList);
  document.querySelectorAll("select[data-logo-preview]").forEach(function (select) {
    select.addEventListener("change", function () {
      updateLogo(select);
    });
    updateLogo(select);
  });
})();
