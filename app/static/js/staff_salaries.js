(function () {
  "use strict";

  var tabs = document.querySelectorAll("[data-browse-tab]");
  var panels = document.querySelectorAll("[data-browse-panel]");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      var key = tab.getAttribute("data-browse-tab");
      tabs.forEach(function (t) {
        t.classList.toggle("is-active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      panels.forEach(function (p) {
        var on = p.getAttribute("data-browse-panel") === key;
        p.classList.toggle("is-active", on);
        if (on) {
          p.removeAttribute("hidden");
        } else {
          p.setAttribute("hidden", "hidden");
        }
      });
    });
  });

  var hireForm = document.getElementById("staff-hire-form");
  if (!hireForm) return;

  var staffIdInput = document.getElementById("staff-hire-staff-id");
  var staffNameDisplay = document.getElementById("staff-hire-name-display");
  var roleSelect = document.getElementById("staff-hire-role");

  document.querySelectorAll(".staff-hire-pick").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sid = btn.getAttribute("data-staff-id") || "";
      var name = btn.getAttribute("data-staff-name") || "";
      var browseRole = btn.getAttribute("data-browse-role") || "";
      if (staffIdInput) staffIdInput.value = sid;
      if (staffNameDisplay) staffNameDisplay.value = name;
      if (roleSelect && browseRole) {
        roleSelect.value = browseRole;
      }
      hireForm.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  hireForm.addEventListener("submit", function (ev) {
    if (!staffIdInput || !String(staffIdInput.value || "").trim()) {
      ev.preventDefault();
      window.alert("Select a staff member from the browse list first.");
    }
  });
})();
