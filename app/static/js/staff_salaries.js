(function () {
  "use strict";

  var teamSelect = document.getElementById("admin-team-select");
  if (teamSelect) {
    teamSelect.addEventListener("change", function () {
      var tid = teamSelect.value || "";
      var base = window.location.pathname;
      if (tid) {
        window.location.href = base + "?admin_team_id=" + encodeURIComponent(tid);
      } else {
        window.location.href = base;
      }
    });
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function roleSalary(form, role) {
    var attr = "data-salary-" + String(role || "").replace(/_/g, "-");
    var raw = form.getAttribute(attr);
    var n = parseInt(raw || "0", 10);
    return isNaN(n) ? 0 : n;
  }

  function setupSearch(formId, opts) {
    var form = document.getElementById(formId);
    if (!form) return;

    var searchInput = document.getElementById(opts.searchId);
    var ac = document.getElementById(opts.acId);
    var staffIdInput = document.getElementById(opts.staffIdInput);
    var nameDisplay = document.getElementById(opts.nameDisplayId);
    var searchUrl = form.getAttribute("data-search-url") || "";
    var mode = form.getAttribute("data-search-mode") || "hire";
    var teamId = form.getAttribute("data-team-id") || "";
    var timer = null;

    function closeAc() {
      if (!ac) return;
      ac.innerHTML = "";
      ac.setAttribute("hidden", "hidden");
    }

    function pick(row) {
      if (staffIdInput) staffIdInput.value = row.staff_fhm_id || "";
      if (nameDisplay) nameDisplay.value = row.full_name || "";
      if (opts.onPick) opts.onPick(row);
      closeAc();
      if (searchInput) searchInput.value = row.full_name || "";
    }

    function renderResults(results) {
      if (!ac) return;
      ac.innerHTML = "";
      if (!results || !results.length) {
        closeAc();
        return;
      }
      results.forEach(function (row) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "staff-salaries-ac__item";
        var meta = row.role_label ? " · " + row.role_label : "";
        if (row.annual_salary) {
          meta += " · $" + Number(row.annual_salary).toLocaleString();
        }
        btn.innerHTML =
          "<strong>" + escapeHtml(row.full_name) + "</strong>" +
          "<span class='muted'>" + escapeHtml(meta) + "</span>";
        btn.addEventListener("click", function () {
          pick(row);
        });
        ac.appendChild(btn);
      });
      ac.removeAttribute("hidden");
    }

    if (searchInput) {
      searchInput.addEventListener("input", function () {
        var q = (searchInput.value || "").trim();
        if (staffIdInput) staffIdInput.value = "";
        if (nameDisplay) nameDisplay.value = "";
        if (opts.onClear) opts.onClear();
        if (q.length < 2) {
          closeAc();
          return;
        }
        clearTimeout(timer);
        timer = setTimeout(function () {
          var url =
            searchUrl +
            "?q=" +
            encodeURIComponent(q) +
            "&mode=" +
            encodeURIComponent(mode) +
            "&team_id=" +
            encodeURIComponent(teamId);
          fetch(url)
            .then(function (r) {
              return r.json();
            })
            .then(function (data) {
              renderResults(data.results || []);
            })
            .catch(function () {
              closeAc();
            });
        }, 200);
      });
      document.addEventListener("click", function (ev) {
        if (!ac || ac.hasAttribute("hidden")) return;
        if (ev.target === searchInput || ac.contains(ev.target)) return;
        closeAc();
      });
    }
  }

  var hireForm = document.getElementById("staff-hire-form");
  var salaryPreview = document.getElementById("staff-hire-salary-preview");
  var roleSelect = document.getElementById("staff-hire-role");

  function updateSalaryPreview() {
    if (!hireForm || !salaryPreview || !roleSelect) return;
    var sal = roleSalary(hireForm, roleSelect.value);
    salaryPreview.textContent = sal > 0 ? "$" + sal.toLocaleString() : "—";
  }

  if (roleSelect) {
    roleSelect.addEventListener("change", updateSalaryPreview);
    updateSalaryPreview();
  }

  setupSearch("staff-hire-form", {
    searchId: "staff-hire-search",
    acId: "staff-hire-ac",
    staffIdInput: "staff-hire-staff-id",
    nameDisplayId: "staff-hire-name-display",
  });

  if (hireForm) {
    hireForm.addEventListener("submit", function (ev) {
      var sid = document.getElementById("staff-hire-staff-id");
      if (!sid || !sid.value) {
        ev.preventDefault();
        window.alert("Choose a staff member from search.");
        return;
      }
      var remaining = parseInt(hireForm.getAttribute("data-remaining") || "0", 10);
      var budget = parseInt(hireForm.getAttribute("data-budget") || "0", 10);
      if (budget > 0 && roleSelect) {
        var cost = roleSalary(hireForm, roleSelect.value);
        if (cost > remaining) {
          ev.preventDefault();
          window.alert(
            hireForm.getAttribute("data-insufficient-msg") ||
              "Insufficient staff budget for this hire."
          );
        }
      }
    });
  }

  var fireSalaryDisplay = document.getElementById("staff-fire-salary-display");
  setupSearch("staff-fire-form", {
    searchId: "staff-fire-search",
    acId: "staff-fire-ac",
    staffIdInput: "staff-fire-staff-id",
    nameDisplayId: "staff-fire-name-display",
    onPick: function (row) {
      if (fireSalaryDisplay) {
        fireSalaryDisplay.value =
          row.annual_salary > 0
            ? "$" + Number(row.annual_salary).toLocaleString()
            : "—";
      }
    },
    onClear: function () {
      if (fireSalaryDisplay) fireSalaryDisplay.value = "";
    },
  });

  var fireForm = document.getElementById("staff-fire-form");
  if (fireForm) {
    fireForm.addEventListener("submit", function (ev) {
      var sid = document.getElementById("staff-fire-staff-id");
      if (!sid || !sid.value) {
        ev.preventDefault();
        window.alert("Choose a staff member from search.");
      }
    });
  }
})();
