(function () {
  var root = document.getElementById("trade-log-root");
  if (!root) return;
  var aiUrl = root.getAttribute("data-ai-url") || "";
  var csrf = root.getAttribute("data-csrf") || "";
  var dialog = document.getElementById("trade-log-ai-dialog");
  var btnClose = document.getElementById("trade-log-ai-close");
  var verdictEl = document.getElementById("trade-log-ai-verdict");
  var opinionEl = document.getElementById("trade-log-ai-opinion");
  var sugEl = document.getElementById("trade-log-ai-suggestions");
  var phEl = document.getElementById("trade-log-ai-placeholder");
  var loadEl = document.getElementById("trade-log-ai-loading");

  function showDialog() {
    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
  }

  function setLoading(on) {
    if (loadEl) loadEl.hidden = !on;
    if (phEl) phEl.hidden = on;
    if (on) {
      if (verdictEl) verdictEl.hidden = true;
      if (sugEl) {
        sugEl.hidden = true;
        sugEl.innerHTML = "";
      }
      if (opinionEl) opinionEl.textContent = "";
    }
  }

  function renderResult(data) {
    setLoading(false);
    if (phEl) phEl.hidden = true;
    if (verdictEl) {
      verdictEl.textContent = data.verdict || "";
      verdictEl.hidden = !verdictEl.textContent;
    }
    if (opinionEl) opinionEl.textContent = data.opinion || "";
    if (sugEl) {
      sugEl.innerHTML = "";
      var list = data.suggestions;
      if (Array.isArray(list) && list.length) {
        list.forEach(function (s) {
          var li = document.createElement("li");
          li.textContent = s;
          sugEl.appendChild(li);
        });
        sugEl.hidden = false;
      } else {
        sugEl.hidden = true;
      }
    }
  }

  function onAiClick(card) {
    var source = card.getAttribute("data-source") || "";
    var id = card.getAttribute("data-row-id") || "";
    if (!aiUrl || !source || !id) return;
    showDialog();
    setLoading(true);
    fetch(aiUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      credentials: "same-origin",
      body: JSON.stringify({ csrf_token: csrf, source: source, id: parseInt(id, 10) }),
    })
      .then(function (r) {
        return r.text().then(function (text) {
          var body = {};
          try {
            body = text ? JSON.parse(text) : {};
          } catch (err) {
            body = { error: text || "Could not get AI take." };
          }
          return { ok: r.ok, body: body, status: r.status };
        });
      })
      .then(function (res) {
        if (!res.ok) {
          setLoading(false);
          if (phEl) {
            phEl.hidden = false;
            phEl.textContent = res.body.error || ("Could not get AI take. HTTP " + res.status + ".");
          }
          return;
        }
        renderResult(res.body);
      })
      .catch(function () {
        setLoading(false);
        if (phEl) {
          phEl.hidden = false;
          phEl.textContent = "Network error — try again.";
        }
      });
  }

  root.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-trade-ai]");
    if (!btn) return;
    var card = btn.closest(".trade-log-card");
    if (card) onAiClick(card);
  });

  if (btnClose && dialog) {
    btnClose.addEventListener("click", function () {
      dialog.close();
    });
  }
})();
