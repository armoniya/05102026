// İcra · Dosyalarım — masaüstü IcraDosyalarimEkrani'in web eşi.
// İŞ MANTIĞI sunucudaki icra_core'dadır (orijinal SorguMotoru._post → 127.0.0.1:8800
// ofis → canlı e-imza UYAP). Burada yalnızca sunum: kolon başlığı altındaki kutular
// hem sunucu kriteri (Sorgula) hem de dönen sonuçları anında daraltan filtredir.
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var sorgulaBtn = $("icra-sorgula");
  if (!sorgulaBtn) return;
  var temizleBtn = $("icra-temizle"), statusEl = $("icra-status"), sayacEl = $("icra-sayac");
  var detayBtn = $("icra-detay");
  var barkodBtn = $("icra-barkod-guncelle");
  var pauseBtn = $("icra-pause"), stopBtn = $("icra-stop");
  var durumSel = $("icra-durum"), headEl = $("icra-head"), filterRow = $("icra-filter-row"),
      bodyEl = $("icra-body"), logEl = $("icra-log");

  var columns = [];        // [{key,label}]
  var rows = [];           // [[değer...], ...] sunucudan
  var filters = {};        // colKey -> filtre kutusu <input>
  var sinceLog = 0, sinceRev = 0, running = false, paused = false, ready = false, polling = false;

  function setControls() {
    // guncelleRunning: "UYAP'tan Güncelle" işi (aşağıda) — düğmeler onunla ortak.
    if (pauseBtn) { pauseBtn.disabled = !(running || guncelleRunning); pauseBtn.textContent = paused ? "Devam" : "Duraklat"; }
    if (stopBtn) stopBtn.disabled = !(running || guncelleRunning);
  }
  var selectedIdx = -1;    // "Dosya Görüntüle" için seçili satırın rows[] içindeki (HAM, filtrelenmemiş) sırası

  // Türkçe-duyarsız küçük harf (icra_core.tr_lower eşi) — canlı filtre eşleşmesi için.
  function trLower(s) {
    s = s == null ? "" : String(s);
    var map = { "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
                "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c" };
    return s.replace(/[İIıŞşĞğÜüÖöÇç]/g, function (c) { return map[c]; }).toLowerCase();
  }

  function log(line) {
    logEl.appendChild(document.createTextNode(line + "\n"));
    logEl.scrollTop = logEl.scrollHeight;
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }

  // ── Alanları yükle: kolon başlıkları + durum seçenekleri ──
  function loadFields() {
    return fetch("api/icra/fields").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.ready) { log("[HATA] İcra modülü hazır değil: " + (d && d.err || "")); return; }
      columns = d.columns || [];
      durumSel.innerHTML = "";
      (d.durumlar || []).forEach(function (du) {
        var o = document.createElement("option");
        o.value = du.kod; o.textContent = du.label;
        if (du.kod === d.durum_default) o.selected = true;
        durumSel.appendChild(o);
      });
      buildHead();
      ready = true;
    }).catch(function () {});
  }

  // ── Başlık şeridi + filtre/kriter kutuları (her kolon için bir <input>) ──
  function buildHead() {
    headEl.innerHTML = ""; filterRow.innerHTML = ""; filters = {};
    columns.forEach(function (c) {
      var th = document.createElement("th"); th.textContent = c.label; headEl.appendChild(th);
      var ftd = document.createElement("th");
      var inp = document.createElement("input");
      inp.type = "text"; inp.className = "icra-colbox"; inp.placeholder = "süz / ara";
      inp.addEventListener("input", renderRows);                 // canlı süzme
      inp.addEventListener("keydown", function (e) {             // Enter → UYAP'tan çek
        if (e.key === "Enter") sorgula();
      });
      filters[c.key] = inp;
      ftd.appendChild(inp); filterRow.appendChild(ftd);
    });
  }

  // ── Sonuç çizimi (canlı filtre uygulanmış) ──
  function visibleRows() {
    var aktif = {};
    columns.forEach(function (c, i) {
      var q = trLower(filters[c.key] ? filters[c.key].value.trim() : "");
      if (q) aktif[i] = q;
    });
    var keys = Object.keys(aktif);
    // Her satırla birlikte ORİJİNAL (ham, filtrelenmemiş) index'i taşı — "Dosya
    // Görüntüle" sunucuya bu index'i gönderir, sunucudaki job.records de AYNI ham
    // sırada tutulur (bkz. server.py IcraJob._set_rows).
    var withIdx = rows.map(function (row, i) { return [i, row]; });
    if (!keys.length) return withIdx;
    return withIdx.filter(function (pair) {
      var row = pair[1];
      return keys.every(function (i) { return trLower(row[i]).indexOf(aktif[i]) >= 0; });
    });
  }

  function renderRows() {
    var göster = visibleRows();
    bodyEl.innerHTML = "";
    göster.forEach(function (pair) {
      var idx = pair[0], row = pair[1];
      var tr = document.createElement("tr");
      tr.dataset.idx = idx;
      if (idx === selectedIdx) tr.className = "icra-row-selected";
      tr.addEventListener("click", function () {
        selectedIdx = idx;
        renderRows();
      });
      row.forEach(function (v) {
        var td = document.createElement("td");
        td.textContent = v == null ? "" : String(v);
        tr.appendChild(td);
      });
      bodyEl.appendChild(tr);
    });
    if (!rows.length) sayacEl.textContent = "";
    else if (göster.length !== rows.length) sayacEl.textContent = göster.length + " / " + rows.length + " dosya";
    else sayacEl.textContent = rows.length + " dosya";
  }

  // ── Dosya Görüntüle (Dosya Bilgileri ayrıntısı) ──
  // 'Barkod / Tebligat Bilgileri' bölümü — masaüstü _barkod_satirlari ile aynı
  // biçim (2026-09-23: tür + evrak tarihi eklendi).
  function barkodSatirlari(barkodlar) {
    if (!barkodlar || !barkodlar.length) return [];
    var s = ["", "Barkod / Tebligat Bilgileri (" + barkodlar.length + "):"];
    barkodlar.forEach(function (b) {
      var satir = "  " + (b.evrakTarihi || "—") + " — " + (b.tebligatTuru || "Tür belirsiz") +
        " — Barkod: " + (b.barkod || "—") + " — Durum: " + (b.pttDurumu || "—");
      if (b.sonIslemTarihi) satir += " (" + b.sonIslemTarihi + ")";
      satir += " — Tebliğ Mazbatası: " + (b.tebligMazbatasiVar || "—");
      if (b.kapaliTebligMazbatasiVar === "Var") satir += ", Kapalı Mazbata: Var";
      s.push(satir);
    });
    return s;
  }

  function tarafSatirlari(taraflar) {
    taraflar = taraflar || [];
    if (!taraflar.length) return [];
    var s = ["", "Taraf Bilgileri:"];
    taraflar.forEach(function (t) {
      var satir = "  " + (t.rol || "") + ": " + (t.adi || "");
      if (t.vekil) satir += " — Vekil: " + t.vekil.replace(/^\[|\]$/g, "");
      // Kesinleşme/Tebliğ Durumu (kullanıcı bulgusu, 2026-08-04: Barkod
      // Sorgu ile hesaplanan bu veri Dosya Görüntüle'de hiç görünmüyordu)
      // — yalnız borçlu satırlarında dolu olur, bkz.
      // dosya_core._taraflar_kesinlesme_bilgisi_ekle.
      if (t.kesinlesmeDurumu) satir += " — Kesinleşme: " + t.kesinlesmeDurumu;
      if (t.tebligatDurumu) satir += " — Tebliğ: " + t.tebligatDurumu;
      s.push(satir);
    });
    return s;
  }

  function dosyaGoruntule() {
    if (selectedIdx < 0) { statusEl.textContent = "Önce listeden bir dosya seçin."; return; }
    detayBtn.disabled = true;
    statusEl.textContent = "Dosya ayrıntısı alınıyor…";
    post("api/icra/detay", { index: selectedIdx }).then(function (d) {
      detayBtn.disabled = false;
      if (!d.ok) {
        statusEl.textContent = "Dosya ayrıntısı alınamadı"; log("[HATA] " + (d.msg || ""));
        // 2026-09-23: ayrıntı alınamasa da DB'deki taraflar (borçlu) ve
        // barkodlar gösterilir (kullanıcı bulgusu, 2024/11435).
        alert(["⚠️ " + (d.msg || "Dosya ayrıntısı alınamadı.")]
          .concat(tarafSatirlari(d.taraflar), barkodSatirlari(d.barkodlar)).join("\n"));
        return;
      }
      statusEl.textContent = "Dosya ayrıntısı kaydedildi";
      var ham = d.ham || {}, satirlar = [];
      if (d.aile === "icra") {
        satirlar = [
          "Takibin Türü: " + (ham.takibinTuru || "—"),
          "Takibin Şekli: " + (ham.takibinSekli || "—"),
          "Takibin Yolu: " + (ham.takibinYolu || "—"),
          "Alacak Kalemi Toplam: " + (ham.alacakKalemToplamTutar || "—"),
          "Vekalet Ücreti: " + (ham.vekaletUcreti || "—"),
          "Tahsil Harcı: " + (ham.tahsilHarci || "—")
        ];
      } else if (d.aile === "hukuk") {
        satirlar = [
          "Dava Açılış Türü: " + (ham.davaAcilisTuru || "—"),
          "Dava Türleri: " + (ham.davaTurleriStr || "—"),
          "İlgili Dava Listesi: " + (ham.ilgiliDavaListesiStr || "—"),
          "Duruşma Tarihi: " + (ham.durusmaTarihi || "—")
        ];
      } else {
        satirlar = ["Bu yargı türü için henüz ayrıntı görüntüleme desteklenmiyor."];
      }
      satirlar = satirlar.concat(tarafSatirlari(d.taraflar));
      // Barkod Sorgu (Kapalı Tebligat — PTT) modülünün DB'ye yazdığı gerçek
      // sonuçlar — masaüstü dosyalarim_genel.py._barkod_sekmesi'nin web eşi
      // (kullanıcı bulgusu, 2026-08-04: "barkod veritabanında olan veri tüm
      // dosyaları sorgulama ekranına gelmiyor" — bu, DosyaTaraf.tebligatDurumu
      // enum'undan AYRI bir veri kaynağıdır, o enum hiçbir zaman otomatik
      // doldurulmuyor). En yeniden eskiye.
      satirlar = satirlar.concat(barkodSatirlari(d.barkodlar));
      alert(satirlar.join("\n") + (d.kaydedildi ? "\n\n(Yerel veritabanına kaydedildi.)" : ""));
    }).catch(function (e) { detayBtn.disabled = false; statusEl.textContent = ""; log("[HATA] " + e); });
  }
  if (detayBtn) detayBtn.addEventListener("click", dosyaGoruntule);

  // ── "Barkod'u da Güncelle" — Sorgula'dan TAMAMEN BAĞIMSIZ, ayrı bir iş
  // (server.py icra_barkod_hepsini_guncelle_baslat/BARKOD_JOBS — "Barkod
  // Sorgulama" ekranının 'Seçilenleri Sorgula'sıyla AYNI iş kuyruğu, yalnız
  // seçim listesi o an DB'deki TÜM açık İcra dosyalarından OTOMATİK
  // dolduruluyor). Kullanıcı tercihi (2026-09-22): 'Yenile' barkod'u
  // OTOMATİK çekmeye devam ETMEZ (2026-08-04 performans kararı korunur);
  // bu buton isteğe bağlı, yavaş olabilir (binlerce dosyada saatler sürebilir).
  var barkodSinceLog = 0, barkodRunning = false, barkodPolling = false;

  function barkodSetControls() {
    if (barkodBtn) {
      barkodBtn.disabled = barkodRunning;
      barkodBtn.textContent = barkodRunning ? "Barkod Güncelleniyor…" : "Barkod'u da Güncelle";
    }
  }

  function barkodGuncelle() {
    if (barkodRunning) return;
    barkodSinceLog = 0;
    log("… Barkod güncellemesi başlatılıyor (DB'deki tüm açık İcra dosyaları)");
    post("api/icra/barkod-hepsini-guncelle", {}).then(function (d) {
      if (!d.ok) {
        if (d.cakisma) { log("… " + (d.msg || "sırada bekliyor")); }
        else log("[HATA] " + (d.msg || "Barkod güncellemesi başlatılamadı"));
        return;
      }
      barkodRunning = true; barkodSetControls(); barkodPoll();
    }).catch(function (e) { log("[HATA] " + e); });
  }

  function barkodPoll() {
    if (barkodPolling) return;
    barkodPolling = true;
    (function tick() {
      fetch("api/barkod/sorgula-durum?log=" + barkodSinceLog)
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (s) {
          if (!s || !s.loaded) { barkodPolling = false; barkodRunning = false; barkodSetControls(); return; }
          if (s.logs && s.logs.length) { s.logs.forEach(function (l) { log("[Barkod] " + l); }); barkodSinceLog = s.log_n; }
          barkodRunning = !!s.running;
          barkodSetControls();
          if (!barkodRunning) {
            barkodPolling = false;
            if (s.sonuc && s.sonuc.n != null) log("… Barkod güncellemesi bitti (" + s.sonuc.n + " kayıt).");
            else if (s.sonuc && s.sonuc.hata) log("[HATA] Barkod güncellemesi: " + s.sonuc.hata);
            return;
          }
          setTimeout(tick, 1500);
        })
        .catch(function () { barkodPolling = false; });
    })();
  }

  if (barkodBtn) barkodBtn.addEventListener("click", barkodGuncelle);

  // ── Taraf türü değişimi (Gerçek Kişi ↔ Kurum) ──
  function tarafTur() {
    var el = document.querySelector('input[name="icra-taraf-tur"]:checked');
    return el ? el.value : "gercek";
  }
  Array.prototype.forEach.call(document.querySelectorAll('input[name="icra-taraf-tur"]'), function (r) {
    r.addEventListener("change", function () {
      var kurum = tarafTur() === "kurum";
      $("icra-taraf-kisi").hidden = kurum;
      $("icra-taraf-kurum").hidden = !kurum;
    });
  });

  // ── Sorgula (sunucuya kriter gönder; iş mantığı icra_core'da) ──
  function sorgula() {
    if (!ready || running) return;
    var body = {
      durum_kod: parseInt(durumSel.value, 10) || 0,
      dosyaAcilisTarihiStart: $("icra-tarih-bas").value.trim(),
      dosyaAcilisTarihiEnd: $("icra-tarih-bit").value.trim(),
      taraf_tur: tarafTur(),
      taraf_ad: $("icra-taraf-ad").value.trim(),
      taraf_soyad: $("icra-taraf-soyad").value.trim(),
      taraf_tckn: $("icra-taraf-tckn").value.trim(),
      taraf_kurum: $("icra-taraf-kurum-ad").value.trim(),
      taraf_vergi: $("icra-taraf-vergi").value.trim(),
      taraf_mersis: $("icra-taraf-mersis").value.trim()
    };
    // Kolon kutuları hem sunucu kriteri hem süzgeçtir (masaüstü ile aynı).
    columns.forEach(function (c) {
      body[c.key] = filters[c.key] ? filters[c.key].value.trim() : "";
    });
    statusEl.textContent = "● Sorgulanıyor…";
    window.topluIs.baslat(function (extra) {
      var b = {}; for (var k in body) b[k] = body[k]; for (var k2 in extra) b[k2] = extra[k2];
      return post("api/icra/search", b);
    }, function (t) { statusEl.textContent = "● " + t; }).then(function (d) {
      if (!d.ok) { if (!d.cakisma) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "başlatılamadı")); } return; }
      running = true; setControls(); ensurePoll();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  // ── "UYAP'tan Güncelle" (2026-09-30): künye + taraf + barkod + kesinleşme
  // birlikte — Tüm Dosyalarım'ın Yenile işi (api/dosyalarim/yenile), İcra'ya
  // sınırlı ve barkod katmanı açık. Duraklat/Durdur hangi iş sürüyorsa ona gider.
  var guncelleBtn = $("icra-uyap-guncelle");
  var guncelleSinceLog = 0, guncelleRunning = false;

  function guncelleSetControls() {
    if (guncelleBtn) {
      guncelleBtn.disabled = guncelleRunning;
      guncelleBtn.textContent = guncelleRunning ? "Güncelleniyor…" : "UYAP'tan Güncelle";
    }
    setControls();
  }

  function uyapGuncelle() {
    if (guncelleRunning || running) return;
    statusEl.textContent = "● UYAP'tan güncelleniyor…";
    window.topluIs.baslat(function (extra) {
      var b = { yargi_turu: 2, barkod_da: true };   // 2 = İcra (dosya_core.YARGI_TURU_ICRA)
      for (var k in extra) b[k] = extra[k];
      return post("api/dosyalarim/yenile", b);
    }, function (t) { statusEl.textContent = "● " + t; }).then(function (d) {
      if (!d.ok) { if (!d.cakisma) { statusEl.textContent = ""; log("[HATA] " + (d.msg || "başlatılamadı")); } return; }
      guncelleRunning = true; paused = false; guncelleSinceLog = 0; guncelleSetControls(); guncellePoll();
    }).catch(function (e) { statusEl.textContent = ""; log("[HATA] " + e); });
  }

  function guncellePoll() {
    fetch("api/dosyalarim/yenile-durum?log=" + guncelleSinceLog)
      .then(function (r) { return r.json(); }).then(function (s) {
        if (!s || !s.loaded) { guncelleRunning = false; guncelleSetControls(); return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); guncelleSinceLog = s.log_n; }
        paused = !!s.paused;
        if (s.running) { guncelleSetControls(); setTimeout(guncellePoll, 1000); return; }
        guncelleRunning = false; paused = false; guncelleSetControls();
        if (s.sonuc && s.sonuc.hata) { statusEl.textContent = ""; log("[HATA] " + s.sonuc.hata); }
        else {
          statusEl.textContent = "✔ UYAP'tan güncellendi.";
          fetch("api/icra/yerel-tazele").then(function () { ensurePoll(); }).catch(function () {});
        }
      }).catch(function () { guncelleRunning = false; guncelleSetControls(); });
  }
  if (guncelleBtn) guncelleBtn.addEventListener("click", uyapGuncelle);

  function duraklatToggle() {
    if (guncelleRunning) {
      post("api/dosyalarim/yenile-duraklat").then(function (d) { paused = !!d.paused; guncelleSetControls(); });
      return;
    }
    if (!running) return;
    post("api/icra/pause").then(function (d) { paused = !!d.paused; setControls(); });
  }

  function durdur() {
    if (guncelleRunning) { post("api/dosyalarim/yenile-durdur"); return; }
    if (running) post("api/icra/stop");
  }

  if (pauseBtn) pauseBtn.addEventListener("click", duraklatToggle);
  if (stopBtn) stopBtn.addEventListener("click", durdur);

  function temizle() {
    durumSel.selectedIndex = 0;
    ["icra-tarih-bas", "icra-tarih-bit", "icra-taraf-ad", "icra-taraf-soyad",
     "icra-taraf-tckn", "icra-taraf-kurum-ad", "icra-taraf-vergi", "icra-taraf-mersis"]
      .forEach(function (id) { var e = $(id); if (e) e.value = ""; });
    columns.forEach(function (c) { if (filters[c.key]) filters[c.key].value = ""; });
    selectedIdx = -1;
    renderRows();
    statusEl.textContent = "";
  }

  sorgulaBtn.addEventListener("click", sorgula);
  temizleBtn.addEventListener("click", temizle);

  // ── Durum polling (sunucu DB-önce → canlı-sonra akışını yansıtır) ──
  function ensurePoll() {
    if (polling) return;
    polling = true;
    poll();
  }

  function poll() {
    fetch("api/icra/status?log=" + sinceLog + "&rev=" + sinceRev)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (s) {
        if (!s || !s.loaded) { polling = false; return; }
        if (s.logs && s.logs.length) { s.logs.forEach(log); sinceLog = s.log_n; }
        if (s.rows) { rows = s.rows; sinceRev = s.rev; selectedIdx = -1; renderRows(); }
        else if (typeof s.rev === "number") sinceRev = s.rev;
        if (s.status) statusEl.textContent = "● " + s.status;
        if (s.yeni && s.yeni.length) {
          var liste = s.yeni.map(function (y) { return "- " + y.dosya_no + " (" + y.birim_adi + ")"; }).join("\n");
          log("Veritabanına " + s.yeni.length + " yeni dosya eklendi:\n" + liste);
        }
        running = !!s.running; paused = !!s.paused; setControls();
        if (!running) { polling = false; return; }   // iş bitti → polling durur
        setTimeout(poll, 1000);
      })
      .catch(function () { polling = false; });
  }

  // ── Açılışta BİR KEZ yerel veritabanını göster (UYAP'a gitmez; masaüstü
  // _acilista_db_yukle_bg eşi). 2026-09-25 kullanıcı kararı: eskiden bu 20 sn'de
  // bir tekrarlanıyordu — "sürekli değişiklik kontrolü programı, bağlantıyı,
  // diski yorar" — periyodik tazeleme KALDIRILDI; güncel veri için Sorgula.
  function ilkYerelYukle() {
    if (!running) fetch("api/icra/yerel-tazele").then(function () { ensurePoll(); }).catch(function () {});
  }

  // ── Açılış: alanları + birim listesini yükle ──
  loadFields().then(function () {
    ilkYerelYukle();
    // İcra daireleri açılır listesi (datalist) — masaüstü _combolari_doldur birimAdi eşi.
    fetch("api/icra/units").then(function (r) { return r.json(); }).then(function (d) {
      var units = (d && d.units) || [];
      if (!units.length) return;
      var dl = document.createElement("datalist"); dl.id = "icra-units";
      units.forEach(function (u) { var o = document.createElement("option"); o.value = u; dl.appendChild(o); });
      document.body.appendChild(dl);
      if (filters.birimAdi) filters.birimAdi.setAttribute("list", "icra-units");
    }).catch(function () {});
  });
})();
