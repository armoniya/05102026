// Sağ alt hızlı menü (FAB) — CSP (script-src 'self') için index.html'deki satır içi script'ten taşındı.
  // Sağ alt hızlı menü (FAB). Kısayol eylemleri şimdilik YER TUTUCUDUR;
  // her topun işini aşağıdaki "fabActions" içine yazın.
  (function () {
    var fab = document.getElementById("fab");
    var mainBtn = document.getElementById("fab-main");
    if (!fab || !mainBtn) return;

    var fabActions = {
      "1": function () { console.log("[kısayol 1] tıklandı"); },
      "2": function () { console.log("[kısayol 2] tıklandı"); },
      "3": function () { console.log("[kısayol 3] tıklandı"); },
      "4": function () { console.log("[kısayol 4] tıklandı"); }
    };

    function closeFab() {
      fab.classList.remove("open");
      mainBtn.setAttribute("aria-expanded", "false");
    }
    mainBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = fab.classList.toggle("open");
      mainBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    Array.prototype.forEach.call(fab.querySelectorAll(".fab-item"), function (btn) {
      btn.addEventListener("click", function (e) {
        e.stopPropagation();
        var act = btn.getAttribute("data-act");
        if (fabActions[act]) { try { fabActions[act](); } catch (err) { console.error(err); } }
        closeFab();
      });
    });
    document.addEventListener("click", function (e) { if (!fab.contains(e.target)) closeFab(); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeFab(); });
  })();
