// ═══════════════════════════════════════════════════════════════════════════════════════
// ÇOKLU UYAP — TEMA SEÇİMİ (2026-11-11)
// ═══════════════════════════════════════════════════════════════════════════════════════
// UYAP'ın üst çubuktaki tema düğmesi (güneş / ay simgeli .nav-link; UYAP aydınlık ↔ koyu arasında geçer,
// tercihini localStorage.theme ve <html data-theme> ile tutar) artık doğrudan geçiş yapmaz: tema seçim
// bir menü açar — Koyu Tema, UYAP, Armonia, Gül, Okyanus, Lavanta, Gün Batımı, Güneş, Grafit (renkler: tema.css).
//   Koyu Tema → UYAP'ın kendi koyu modu açılır (UYAP'ın düğmesi bizim yerimize bir kez tıklanır)
//   Diğerleri → UYAP aydınlık moda alınır; Çoklu UYAP renkleri (deri temalarında UYAP'ın yüzeyleri de) değişir
// Seçim localStorage.cokluuyapTema'da tutulur; <html data-cu-tema> hem sayfa başındaki küçük betikle
// (uyap_proxy._tema_on_eki, renk sıçraması olmasın) hem burada güncel tutulur. UYAP koyu moddaysa tema her
// zaman "koyu" sayılır.
(function() {
    'use strict';
    const anaCerceve = window.__uyapAnaCerceve ? window.__uyapAnaCerceve() : window.top === window;
    if (!anaCerceve || window.__uyapTemaSecimi) return;
    window.__uyapTemaSecimi = true;

    const DEPO = 'cokluuyapTema';
    // [kimlik, ad, açıklama, örnek renkler]. "deri" temaları (UYAP ve Koyu dışındakiler) UYAP'ın kendi
    // yüzeylerini de boyar: <html data-cu-deri> (tema.css). Sıra menüdeki sıradır.
    const TEMALAR = [
        ['koyu', 'Koyu Tema', 'UYAP\'ın koyu görünümü', ['#1d2130', '#3a55b5']],
        ['uyap', 'UYAP', 'UYAP\'ın lacivert tonları', ['#24377f', '#e8ecf8']],
        ['armonia', 'Armonia', 'Adaçayı ve ılık kâğıt tonları', ['#5e7d63', '#f1eee8']],
        ['gul', 'Gül', 'Pembe ve tonları', ['#d6447e', '#fde6ef']],
        ['okyanus', 'Okyanus', 'Camgöbeği ve deniz tonları', ['#0f7c8c', '#e1eff1']],
        ['lavanta', 'Lavanta', 'Yumuşak mor tonlar', ['#7b5ea7', '#ece6f5']],
        ['gunbatimi', 'Gün Batımı', 'Kehribar ve kiremit tonları', ['#c2602f', '#f5e8dc']],
        ['gunes', 'Güneş', 'Sarı ve altın tonları', ['#c49000', '#fff3c4']],
        ['grafit', 'Grafit', 'Sade gri tonlar', ['#374151', '#e5e7eb']],
    ];
    const DERI_DISI = { koyu: 1, uyap: 1 };
    const html = document.documentElement;

    function uyapKoyuMu() { return html.getAttribute('data-theme') === 'dark'; }
    function secim() {
        if (uyapKoyuMu()) return 'koyu';
        let t = null;
        try { t = localStorage.getItem(DEPO); } catch (e) {}
        return TEMALAR.some(function(x) { return x[0] === t && x[0] !== 'koyu'; }) ? t : 'uyap';
    }
    function uygula() {
        const t = secim();
        if (html.getAttribute('data-cu-tema') !== t) html.setAttribute('data-cu-tema', t);
        if (DERI_DISI[t]) { if (html.hasAttribute('data-cu-deri')) html.removeAttribute('data-cu-deri'); }
        else if (!html.hasAttribute('data-cu-deri')) html.setAttribute('data-cu-deri', '');
        const tt = TEMALAR.find(function(x) { return x[0] === t; }) || [];
        const d = document.getElementById('cu-tema-dugme');
        if (d && tt[1] && d.title !== 'Tema: ' + tt[1]) {
            d.title = 'Tema: ' + tt[1];
            const n = d.querySelector('.cu-tema-nokta');
            if (n) n.style.background = 'linear-gradient(135deg,' + tt[3][0] + ' 50%,' + tt[3][1] + ' 50%)';
        }
    }

    function temaDugmesi(e) {
        const n = e && e.closest && e.closest('.u-navbar .nav-link');
        return n && n.querySelector('.icon-sun, .icon-moon') ? n : null;
    }
    // UYAP'ın aydınlık / koyu geçişi: onun kendi düğmesine bir kez tıklanır (bizim yakalayıcımız geçirir).
    let gecir = false;
    function uyapModu(koyu) {
        if (uyapKoyuMu() === koyu) return;
        const d = temaDugmesi(document.querySelector('.u-navbar i.icon-sun, .u-navbar i.icon-moon'));
        if (!d) return;
        gecir = true;
        try { d.click(); } finally { gecir = false; }
    }
    function sec(t) {
        try { localStorage.setItem(DEPO, t); } catch (e) {}
        uyapModu(t === 'koyu');
        // UYAP modu bir sonraki çizimde değişir; tema hemen ve kısa süre sonra yeniden uygulanır.
        uygula();
        setTimeout(uygula, 150);
        menuKapat();
    }

    // ── Menü ──
    let menu = null;
    function stilKur() {
        if (document.getElementById('cu-tema-stil')) return;
        const st = document.createElement('style');
        st.id = 'cu-tema-stil';
        st.textContent = [
            '#cu-tema-menu{position:fixed;z-index:2147483646;min-width:240px;padding:6px;border-radius:10px;overflow-y:auto;background:rgba(var(--cu-panel-rgb),0.98);',
            'border:1px solid rgba(255,255,255,0.15);box-shadow:0 12px 32px rgba(0,0,0,0.45);color:#e5e7eb;font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}',
            '#cu-tema-menu .baslik{padding:4px 8px 6px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--cu-vurgu-acik)}',
            '#cu-tema-menu .tema{display:flex;align-items:center;gap:10px;width:100%;padding:8px;border:0;border-radius:7px;background:none;color:inherit;cursor:pointer;text-align:left;font:inherit}',
            '#cu-tema-menu .tema:hover{background:rgba(255,255,255,0.08)}',
            '#cu-tema-menu .tema.sec{background:rgba(var(--cu-vurgu-parlak-rgb),0.18)}',
            '#cu-tema-menu .ornek{flex:none;width:30px;height:20px;border-radius:5px;border:1px solid rgba(255,255,255,0.25);overflow:hidden;display:flex}',
            '#cu-tema-menu .ornek i{flex:1}',
            '#cu-tema-menu .ad{font-weight:600}',
            '#cu-tema-menu .not{font-size:11px;color:#9ca3af}',
            '#cu-tema-menu .isaret{margin-left:auto;font-size:14px;color:var(--cu-vurgu-parlak)}',
        ].join('');
        document.head.appendChild(st);
    }
    function menuKapat() { if (menu) { menu.remove(); menu = null; } }
    function menuAc(d) {
        if (menu) { menuKapat(); return; }
        maviMenuKapat();
        stilKur();
        menu = document.createElement('div');
        menu.id = 'cu-tema-menu';
        menu.setAttribute('role', 'menu');
        const bas = document.createElement('div');
        bas.className = 'baslik';
        bas.textContent = 'Tema';
        menu.appendChild(bas);
        const simdiki = secim();
        TEMALAR.forEach(function(t) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'tema' + (t[0] === simdiki ? ' sec' : '');
            b.setAttribute('role', 'menuitemradio');
            b.setAttribute('aria-checked', t[0] === simdiki ? 'true' : 'false');
            const ornek = document.createElement('span');
            ornek.className = 'ornek';
            t[3].forEach(function(renk) { const i = document.createElement('i'); i.style.background = renk; ornek.appendChild(i); });
            const metin = document.createElement('span');
            const ad = document.createElement('div'); ad.className = 'ad'; ad.textContent = t[1];
            const not = document.createElement('div'); not.className = 'not'; not.textContent = t[2];
            metin.append(ad, not);
            const isaret = document.createElement('span');
            isaret.className = 'isaret';
            isaret.textContent = t[0] === simdiki ? '✓' : '';
            b.append(ornek, metin, isaret);
            b.addEventListener('click', function(ev) { ev.stopPropagation(); sec(t[0]); });
            menu.appendChild(b);
        });
        document.body.appendChild(menu);
        const r = d.getBoundingClientRect();
        const g = menu.offsetWidth;
        const ust = Math.round(r.bottom + 6);
        menu.style.top = ust + 'px';
        menu.style.left = Math.round(Math.max(8, Math.min(r.right - g, innerWidth - g - 8))) + 'px';
        // Kısa ekranda (telefon) menü görev çubuğunun üstünde biter, kendi içinde kayar.
        const cubuk = document.getElementById('uyap-gorev-cubugu');
        menu.style.maxHeight = Math.max(160, innerHeight - ust - (cubuk ? cubuk.offsetHeight : 0) - 8) + 'px';
    }

    // UYAP'ın tema düğmesine basılınca geçiş yerine menü (yakalama aşamasında; React'in dinleyicisinden önce).
    document.addEventListener('click', function(ev) {
        if (gecir) return;
        const d = temaDugmesi(ev.target);
        if (!d) return;
        ev.preventDefault();
        ev.stopPropagation();
        ev.stopImmediatePropagation();
        menuAc(d);
    }, true);
    document.addEventListener('mousedown', function(ev) {
        if (menu && !menu.contains(ev.target) && !temaDugmesi(ev.target) && !ev.target.closest('#cu-tema-dugme')) menuKapat();
    }, true);
    document.addEventListener('keydown', function(ev) { if (menu && ev.key === 'Escape') menuKapat(); }, true);

    // ── Yazı tipi ──
    // UYAP her öğeye Poppins (cdn.uyap.gov.tr), DevExtreme bileşenlerine Roboto veriyor. Poppins geometrik ve
    // geniş; saatlerce okumada yorucu. Bu adlar @font-face ile Windows'un ekran okuması için ayarlanmış
    // (ClearType) Segoe UI'sına yönlendirilir: seçici yazmadan her yere uyar, simge yazı tiplerine (feather,
    // DXIcons, e-icons) ve belge görüntüleyicinin kendi yazı tiplerine dokunmaz. size-adjust: Segoe UI aynı
    // puntoda Poppins'ten küçük görünür; boyut yaklaşık korunur. UYAP'ın 300 (ince) ağırlığı normal, 600'ü
    // yarı kalın çizilir (ince yazı ekranda zor okunur, kalın yazı yorar). Aynı adlı @font-face'lerden
    // SONUNCUSU geçerli olduğu için stil, UYAP'ın (sonradan yüklenenler dahil) stil dosyalarından sonra tutulur.
    const YAZI_ADLARI = ['Poppins', 'Roboto', 'Raleway', 'Open Sans'];
    const YAZI_AGIRLIK = [
        ['100 449', 'local("Segoe UI"), local("SegoeUI")'],
        ['450 649', 'local("Segoe UI Semibold"), local("SegoeUI-Semibold")'],
        ['650 1000', 'local("Segoe UI Bold"), local("SegoeUI-Bold")'],
    ];
    let yaziStili = null;
    function yaziTipi() {
        if (!yaziStili) {
            yaziStili = document.createElement('style');
            yaziStili.id = 'cu-yazi-tipi';
            const kurallar = [];
            YAZI_ADLARI.forEach(function(ad) {
                YAZI_AGIRLIK.forEach(function(a) {
                    ['normal', 'italic'].forEach(function(bicim) {
                        kurallar.push('@font-face{font-family:"' + ad + '";font-style:' + bicim + ';font-weight:' + a[0] +
                            ';src:' + (bicim === 'italic' ? a[1].replace(/"\)/g, ' Italic")') + ',' : '') + a[1] +
                            ';size-adjust:104%;font-display:block}');
                    });
                });
            });
            yaziStili.textContent = kurallar.join('\n');
        }
        // Kendisinden sonra bir stil dosyası / stil öğesi geldiyse en sona taşı.
        const head = document.head;
        if (!head) return;
        let s = yaziStili.parentNode === head ? yaziStili.nextElementSibling : null;
        let sonda = yaziStili.parentNode === head;
        for (; s && sonda; s = s.nextElementSibling) {
            if (s.tagName === 'STYLE' && s.id !== 'cu-tema-stil' && s.id !== 'cu-mavi-stil') sonda = false;
            else if (s.tagName === 'LINK' && /stylesheet/i.test(s.rel)) sonda = false;
        }
        if (!sonda) head.appendChild(yaziStili);
    }

    // ── Mavi ışık koruması (5 kademe) ──
    // Tema düğmesinin yanında bir düğme; tıklanınca Kapalı / 1–5 seçimi açılır, fare tekerleği düğme üstünde
    // kademeyi doğrudan değiştirir. Katman ve kademe renkleri tema.css'te (#cu-mavi-katman, data-cu-mavi).
    const MAVI_DEPO = 'cokluuyapMaviIsik';
    function maviKademe() {
        let k = 0;
        try { k = parseInt(localStorage.getItem(MAVI_DEPO) || '0', 10) || 0; } catch (e) {}
        return Math.max(0, Math.min(5, k));
    }
    function maviUygula() {
        const k = maviKademe();
        if (k) { if (html.getAttribute('data-cu-mavi') !== String(k)) html.setAttribute('data-cu-mavi', String(k)); }
        else if (html.hasAttribute('data-cu-mavi')) html.removeAttribute('data-cu-mavi');
        if (k && !document.getElementById('cu-mavi-katman')) {
            const katman = document.createElement('div');
            katman.id = 'cu-mavi-katman';
            katman.setAttribute('aria-hidden', 'true');
            (document.body || html).appendChild(katman);
        }
        const d = document.getElementById('cu-mavi-dugme');
        if (d) {
            d.title = 'Mavi ışık koruması: ' + (k ? k + '. kademe' : 'kapalı') + ' (tekerlekle değiştirin)';
            d.querySelectorAll('.nokta').forEach(function(n, i) { n.classList.toggle('dolu', i < k); });
            d.classList.toggle('acik', k > 0);
        }
        if (maviMenu) maviMenu.querySelectorAll('button').forEach(function(b) { b.classList.toggle('sec', +b.dataset.k === k); });
    }
    function maviSec(k) {
        k = Math.max(0, Math.min(5, k));
        try { localStorage.setItem(MAVI_DEPO, String(k)); } catch (e) {}
        maviUygula();
    }
    function maviStil() {
        if (document.getElementById('cu-mavi-stil')) return;
        const st = document.createElement('style');
        st.id = 'cu-mavi-stil';
        st.textContent = [
            '#cu-mavi-dugme{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;cursor:pointer;user-select:none}',
            '#cu-mavi-dugme svg{width:20px;height:20px;display:block}',
            '#cu-mavi-dugme .noktalar{display:flex;gap:2px;height:4px}',
            '#cu-mavi-dugme .nokta{width:4px;height:4px;border-radius:50%;background:currentColor;opacity:.3}',
            '#cu-mavi-dugme .nokta.dolu{opacity:1;background:#ffb347}',
            '#cu-mavi-menu{position:fixed;z-index:2147483646;padding:8px;border-radius:10px;background:rgba(var(--cu-panel-rgb),0.98);',
            'border:1px solid rgba(255,255,255,0.15);box-shadow:0 12px 32px rgba(0,0,0,0.45);color:#e5e7eb;font:13px/1.35 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}',
            '#cu-mavi-menu .baslik{padding:2px 4px 8px;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--cu-vurgu-acik)}',
            '#cu-mavi-menu .sira{display:flex;gap:4px}',
            '#cu-mavi-menu button{min-width:38px;height:32px;padding:0 8px;border:1px solid rgba(255,255,255,0.18);border-radius:7px;background:none;color:inherit;cursor:pointer;font:inherit;font-weight:600}',
            '#cu-mavi-menu button:hover{background:rgba(255,255,255,0.08)}',
            '#cu-mavi-menu button.sec{background:#ffb347;border-color:#ffb347;color:#3b2f0b}',
            '#cu-mavi-menu .not{padding:8px 4px 0;font-size:11px;color:#9ca3af}',
        ].join('');
        (document.head || html).appendChild(st);
    }
    let maviMenu = null;
    function maviMenuKapat() { if (maviMenu) { maviMenu.remove(); maviMenu = null; } }
    function maviMenuAc(d) {
        if (maviMenu) { maviMenuKapat(); return; }
        menuKapat();
        maviMenu = document.createElement('div');
        maviMenu.id = 'cu-mavi-menu';
        maviMenu.innerHTML = '<div class="baslik">Mavi ışık koruması</div><div class="sira"></div>' +
            '<div class="not">Kademe arttıkça ekran sıcak tona döner.</div>';
        const sira = maviMenu.querySelector('.sira');
        for (let k = 0; k <= 5; k++) {
            const b = document.createElement('button');
            b.type = 'button';
            b.dataset.k = k;
            b.textContent = k ? String(k) : 'Kapalı';
            b.addEventListener('click', function(ev) { ev.stopPropagation(); maviSec(k); });
            sira.appendChild(b);
        }
        document.body.appendChild(maviMenu);
        maviUygula();
        const r = d.getBoundingClientRect();
        const g = maviMenu.offsetWidth;
        maviMenu.style.top = Math.round(r.bottom + 6) + 'px';
        maviMenu.style.left = Math.round(Math.max(8, Math.min(r.right - g, innerWidth - g - 8))) + 'px';
    }
    // Düğme, UYAP'ın tema düğmesinin hemen soluna; tema düğmesi bir <li> içindeyse yanına aynı türde bir kap.
    function maviDugmesiKur() {
        if (document.getElementById('cu-mavi-dugme')) return;
        const t = temaDugmesi(document.querySelector('.u-navbar i.icon-sun, .u-navbar i.icon-moon'));
        if (!t || !t.parentElement) return;
        maviStil();
        const d = document.createElement(t.tagName === 'BUTTON' ? 'button' : 'a');
        d.id = 'cu-mavi-dugme';
        d.className = t.className;
        d.setAttribute('role', 'button');
        if (d.tagName === 'BUTTON') d.type = 'button'; else d.href = 'javascript:void(0)';
        d.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>' +
            '<span class="noktalar"><i class="nokta"></i><i class="nokta"></i><i class="nokta"></i><i class="nokta"></i><i class="nokta"></i></span>';
        d.addEventListener('click', function(ev) { ev.preventDefault(); ev.stopPropagation(); maviMenuAc(d); });
        d.addEventListener('wheel', function(ev) {
            ev.preventDefault();
            maviSec(maviKademe() + (ev.deltaY < 0 ? 1 : -1));
        }, { passive: false });
        const kap = t.parentElement;
        if (kap.tagName === 'LI') {
            const li = document.createElement('li');
            // UYAP'ın tema li'si gizleme sınıfı taşıyor (temaDugmemiziKur); kopyalanmasın (kullanıcı bulgusu
            // 2026-11-11: mavi ışık düğmesi kaybolmuştu).
            li.className = kap.className.replace(/cu-uyap-tema/g, '').trim();
            li.appendChild(d);
            kap.parentElement.insertBefore(li, kap);
        } else {
            kap.insertBefore(d, t);
        }
        maviUygula();
    }
    document.addEventListener('mousedown', function(ev) {
        if (maviMenu && !maviMenu.contains(ev.target) && !ev.target.closest('#cu-mavi-dugme')) maviMenuKapat();
    }, true);
    document.addEventListener('keydown', function(ev) { if (maviMenu && ev.key === 'Escape') maviMenuKapat(); }, true);

    // ── Kendi logomuz (kullanıcı kararı 2026-11-11): üst çubuktaki UYAP logosunun yerine üst üste binmiş üç
    // pencere + "Çoklu UYAP / büro paneli". UYAP'ın öğesi (h1.logo-content) yerinde kalır, içindekiler
    // tema.css'te gizlenir; React üst çubuğu yeniden çizerse logo bir sonraki turda yeniden eklenir.
    const LOGO_SVG = '<svg class="cu-logo-isaret" viewBox="0 0 34 30" width="34" height="30" aria-hidden="true" focusable="false">' +
        '<rect x="1" y="1" width="20" height="15" rx="3" fill="currentColor" fill-opacity=".28"/>' +
        '<rect x="7" y="7" width="20" height="15" rx="3" fill="currentColor" fill-opacity=".5"/>' +
        '<rect class="cu-logo-on" x="13" y="13" width="20" height="16" rx="3"/>' +
        '<g class="cu-logo-nokta"><circle cx="16.5" cy="16.3" r="1.1"/><circle cx="19.8" cy="16.3" r="1.1"/><circle cx="23.1" cy="16.3" r="1.1"/></g>' +
        '</svg>';
    function logoKur() {
        const kap = (document.querySelector('.u-navbar .uyap-logo .logo-content') || document.querySelector('.u-navbar .navbar-brand h1') || document.querySelector('.u-navbar .navbar-brand'));
        if (!kap || kap.querySelector(':scope > .cu-logo')) return;
        const l = document.createElement('span');
        l.className = 'cu-logo';
        l.title = 'Ana sayfa';
        l.innerHTML = LOGO_SVG + '<span class="cu-logo-yazi"><b>Çoklu UYAP</b><small>BÜRO PANELİ</small></span>';
        kap.appendChild(l);
        const a = kap.closest('a');
        if (a) a.title = 'Çoklu UYAP — ana sayfa';
    }

    // Sekme başlığı ve simgesi (kullanıcı isteği 2026-11-11): "Uyap Avukat Bilgi Sistemi" → "Çoklu UYAP";
    // UYAP'ın simgesi yerine logomuzun işareti (sabit renkler: sekme çubuğu temadan bağımsız).
    const SEKME_SIMGE = 'data:image/svg+xml,' + encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 34 30">' +
        '<rect x="1" y="1" width="20" height="15" rx="3" fill="#24377f" fill-opacity=".45"/>' +
        '<rect x="7" y="7" width="20" height="15" rx="3" fill="#24377f" fill-opacity=".72"/>' +
        '<rect x="13" y="13" width="20" height="16" rx="3" fill="#3f6fe0"/>' +
        '<g fill="#fff"><circle cx="16.5" cy="16.3" r="1.1"/><circle cx="19.8" cy="16.3" r="1.1"/><circle cx="23.1" cy="16.3" r="1.1"/></g></svg>');
    function sekmeKur() {
        const t = document.title || '';
        if (/uyap\s+avukat\s+bilgi\s+sistemi/i.test(t)) document.title = t.replace(/uyap\s+avukat\s+bilgi\s+sistemi/ig, 'Çoklu UYAP');
        else if (!t.trim() && document.head) document.title = 'Çoklu UYAP';
        if (!document.head) return;
        const ikonlar = document.querySelectorAll('link[rel~="icon"]');
        let bizim = false;
        ikonlar.forEach(function(l) {
            if (l.id === 'cu-sekme-simge') { bizim = true; return; }
            l.parentNode.removeChild(l);
        });
        if (!bizim) {
            const l = document.createElement('link');
            l.id = 'cu-sekme-simge'; l.rel = 'icon'; l.type = 'image/svg+xml'; l.href = SEKME_SIMGE;
            document.head.appendChild(l);
        }
    }

    // ── Kendi tema düğmemiz (kullanıcı kararı 2026-11-11): UYAP'ın ay/güneş düğmesi gizlenir (DOM'da kalır;
    // Koyu Tema seçilince uyapModu onu tıklar), yerine palet simgeli düğme; üzerindeki nokta seçili temanın rengi.
    // Mavi ışık düğmesinin hemen sağında, UYAP'ın düğmesinin eski yerinde.
    function temaDugmemiziKur() {
        const t = temaDugmesi(document.querySelector('.u-navbar i.icon-sun, .u-navbar i.icon-moon'));
        if (!t || !t.parentElement) return;
        const kap = t.parentElement;
        const gizlenecek = kap.tagName === 'LI' ? kap : t;
        if (!gizlenecek.classList.contains('cu-uyap-tema')) gizlenecek.classList.add('cu-uyap-tema');
        if (document.getElementById('cu-tema-dugme')) return;
        const d = document.createElement(t.tagName === 'BUTTON' ? 'button' : 'a');
        d.id = 'cu-tema-dugme';
        d.className = t.className;
        d.setAttribute('role', 'button');
        if (d.tagName === 'BUTTON') d.type = 'button'; else d.href = 'javascript:void(0)';
        d.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M12 3a9 9 0 1 0 0 18c1.1 0 1.8-.9 1.8-1.9 0-.5-.2-.9-.5-1.3-.3-.4-.5-.8-.5-1.3 0-1 .8-1.8 1.8-1.8H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3z"/>' +
            '<circle cx="7.5" cy="11" r="1.2" fill="currentColor" stroke="none"/><circle cx="10.5" cy="7.2" r="1.2" fill="currentColor" stroke="none"/>' +
            '<circle cx="15" cy="7.4" r="1.2" fill="currentColor" stroke="none"/></svg><i class="cu-tema-nokta"></i>';
        d.addEventListener('click', function(ev) { ev.preventDefault(); ev.stopPropagation(); menuAc(d); });
        if (kap.tagName === 'LI') {
            const li = document.createElement('li');
            li.className = kap.className.replace(/\bcu-uyap-tema\b/, '').trim();
            li.appendChild(d);
            kap.parentElement.insertBefore(li, kap);
        } else {
            kap.insertBefore(d, t);
        }
        uygula();
    }

    // ── Kullanıcı alanı: UYAP'ın kişi resmi (çoğu hesapta kırık resim, "kullanıcı" yazısı görünüyor) yerine baş
    // harfli yuvarlak avatar. Resim DOM'da kalır, CSS ile gizlenir.
    function avatarKur() {
        const kap = document.querySelector('.u-navbar #dropdownMenuButton');
        if (!kap) return;
        const adOgesi = kap.querySelector('.dropdown-toggle');
        const ad = adOgesi ? adOgesi.textContent.trim() : '';
        const p = ad.split(/\s+/).filter(Boolean);
        const harf = p.length ? (p[0][0] + (p.length > 1 ? p[p.length - 1][0] : '')).toLocaleUpperCase('tr-TR') : '';
        let a = kap.querySelector(':scope > .cu-avatar');
        if (!harf) { if (a) a.remove(); kap.classList.remove('cu-avatarli'); return; }
        if (!a) {
            a = document.createElement('span');
            a.className = 'cu-avatar';
            a.setAttribute('aria-hidden', 'true');
            kap.insertBefore(a, kap.firstChild);
        }
        if (a.textContent !== harf) a.textContent = harf;
        if (!kap.classList.contains('cu-avatarli')) kap.classList.add('cu-avatarli');
    }

    // Logo ana sayfaya götürür (UYAP'ın bağlantısı mobilde gizlenen resmin üstündeydi). Sayfa yenilenmez.
    document.addEventListener('click', function(ev) {
        const l = ev.target.closest && ev.target.closest('.cu-logo');
        if (!l || l.closest('a[href]')) return;              // masaüstünde logo zaten UYAP'ın "/" bağlantısının içinde
        ev.preventDefault();
        if (location.pathname === '/') return;
        history.pushState({}, '', '/');
        window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
    });

    function dugmeKaplariniDuzelt() {
        ['cu-mavi-dugme', 'cu-tema-dugme'].forEach(function(id) {
            const d = document.getElementById(id);
            const li = d && d.parentElement;
            if (li && li.classList.contains('cu-uyap-tema')) li.classList.remove('cu-uyap-tema');
        });
    }

    function tur() {
        uygula();
        logoKur();
        temaDugmemiziKur();
        avatarKur();
        dugmeKaplariniDuzelt();
        sekmeKur();
        yaziTipi();
        maviDugmesiKur();
        maviUygula();
    }
    // ── Aynı karede uygulama (kullanıcı bulgusu 2026-11-11: açılışta / sayfa geçişinde bir an UYAP'ın orijinal
    // arayüzü görünüyordu; düzenlemeler 0,5 sn'lik zamanlayıcıyla geliyordu). DOM'a öğe eklenince düzenlemeler bir
    // sonraki çizimden ÖNCE (requestAnimationFrame) çalışır. Diğer enjekte dosyaları da window.__cuDegisince ile
    // kayıt olur. İşlevler tekrar çalışmaya dayanıklı: değişiklik yoksa DOM'a dokunmazlar (yalnız childList
    // izlenir; sınıf / stil değişiklikleri yeni tur başlatmaz).
    const degisinceListe = [tur];
    let kareBekliyor = false;
    function kareCalistir() {
        kareBekliyor = false;
        degisinceListe.forEach(function(f) { try { f(); } catch (e) { console.warn('[cu]', e); } });
        ortuyuKaldir();
    }
    function kareIste() {
        if (kareBekliyor) return;
        kareBekliyor = true;
        requestAnimationFrame(kareCalistir);
    }
    window.__cuDegisince = function(f) { if (degisinceListe.indexOf(f) < 0) degisinceListe.push(f); kareIste(); };
    new MutationObserver(kareIste).observe(document.documentElement, { childList: true, subtree: true });

    // Örtü (uyap_proxy._tema_on_eki): UYAP ekrana bir şey çizdiğinde ve düzenlemelerimiz yerine oturduğunda
    // kaldırılır: üst çubuk varsa logomuz eklenmiş, ana sayfadaysa konsol kurulmuş olmalı.
    function ortuyuKaldir() {
        if (!html.classList.contains('cu-ortu')) return;
        const kok = document.getElementById('root');
        if (!kok || !kok.firstElementChild) return;
        if (document.querySelector('.u-navbar') && !document.querySelector('.cu-logo')) return;
        if (document.getElementById('searchId') && !document.querySelector('.cu-ana-panel')) return;
        html.classList.remove('cu-ortu');
    }

    tur();
    document.addEventListener('DOMContentLoaded', kareIste);
    setInterval(tur, 1000);            // UYAP modu başka yoldan değişirse tema uysun (öznitelik değişikliği izlenmiyor)
})();
