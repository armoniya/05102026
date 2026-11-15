// ═══════════════════════════════════════════════════════════════════════════════════════
// ÇOKLU UYAP — MOBİL GÖRÜNÜM (2026-11-11)
// ═══════════════════════════════════════════════════════════════════════════════════════
// Kurallar enjekte/mobil.css'te (tema.css'le birlikte sayfanın en başında). Bu dosya:
//
//  1. Kip sınıfları   <html class="cu-dar cu-mobil">. Eşikler aşağıda (DAR / MOBIL). Öbür dosyalar
//                     window.__cuDar() / window.__cuMobil() ile sorar, window.__cuKipDegisince(fn) ile dinler.
//  2. Evrak sekmesi   UYAP belge listesini ve belgeyi yan yana bölüyor; telefonda liste 94 px'e, belge 275 px'e
//                     sıkışıyordu (kullanıcı bulgusu, canlı ölçüm 390 px). Telefonda aynı anda biri görünür:
//                     liste → belgeye dokun → belge tam genişlikte; sekme şeridinin yerinde "‹ Belgeler" çubuğu
//                     (geri, belgenin adı, önceki / sonraki belge). UYAP'ın bileşenleri yerinde kalır; yalnız
//                     hangisinin görüneceği değişir. Belge açılınca UYAP görüntüleyicisinin kendi "Genişliğe
//                     Sığdır"ı uygulanır (düğmesi 720 px altında gizli ama çalışır).
//  2b. Sekmeler      Telefonda dosya penceresinin sekme şeridi yerine sol altta yuvarlak düğme; dokununca sekmeler
//                     yuvarlak düğmeler halinde açılır (mobil.css §6b).
//  3. Kart başlığı   "Evrak İndir (N)": telefonda yazı gizli; sayı rozet olarak (data-cu-say, mobil.css).
//  4. Dokunma         iOS uzun basışta contextmenu olayı üretmez: uzun basış sağ tık menüsünü açar (Android bunu
//                     kendisi yapar). Açık kullanıcı şeridi dışarı dokununca kapanır.
(function() {
    'use strict';
    const anaCerceve = window.__uyapAnaCerceve ? window.__uyapAnaCerceve() : window.top === window;
    if (!anaCerceve || window.__cuMobilKuruldu) return;
    window.__cuMobilKuruldu = true;

    const html = document.documentElement;

    // ── 1. Kip ──
    // DAR: UYAP'ın kendi mobil üst çubuğuna geçtiği eşik (Bootstrap lg). MOBIL: telefon; yatık telefonda genişlik
    // 768'i aşar ama yükseklik çok azdır (≤ 520), o da telefon düzenini alır.
    const DAR = '(max-width: 991.98px)';
    const MOBIL = '(max-width: 767.98px), (max-width: 991.98px) and (max-height: 520px) and (orientation: landscape)';
    const mqDar = window.matchMedia ? matchMedia(DAR) : null;
    const mqMobil = window.matchMedia ? matchMedia(MOBIL) : null;
    const kipDinleyenler = [];
    function dar() { return html.classList.contains('cu-dar'); }
    function mobil() { return html.classList.contains('cu-mobil'); }
    function kipUygula() {
        const m = !!(mqMobil && mqMobil.matches), d = m || !!(mqDar && mqDar.matches);
        if (dar() === d && mobil() === m) return;
        html.classList.toggle('cu-dar', d);
        html.classList.toggle('cu-mobil', m);
        kipDinleyenler.forEach(function(f) { try { f(); } catch (e) { console.warn('[cu]', e); } });
        // Yerleşim değişti: UYAP'ın ızgaraları / bölücüsü yeni genişliğe uysun.
        setTimeout(function() { window.dispatchEvent(new Event('resize')); }, 60);
    }
    [mqDar, mqMobil].forEach(function(mq) {
        if (!mq) return;
        if (mq.addEventListener) mq.addEventListener('change', kipUygula); else if (mq.addListener) mq.addListener(kipUygula);
    });
    kipUygula();
    window.__cuDar = dar;
    window.__cuMobil = mobil;
    window.__cuKipDegisince = function(f) { if (kipDinleyenler.indexOf(f) < 0) kipDinleyenler.push(f); };

    // ── 2. Evrak sekmesi: tek bölme ──
    const OK_SOL = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg>';
    const OK_SAG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>';
    const durumlar = new WeakMap();        // UYAP'ın bölücüsü (.dx-splitter) → {durum: 'liste' | 'belge', secili, sigdirSon}

    // Görünen kartın Evrak bölücüsü. Öbür sekmelerin içeriği DOM'da kalır (ekran dışına itilir): yalnız seçili
    // sekmenin (.dx-item-selected) bölücüsü sayılır.
    function evrakBolucu() {
        const l = document.querySelectorAll('.dosya-sorgula-popup .dx-splitter');
        for (let i = 0; i < l.length; i++) {
            const s = l[i];
            if (!s.querySelector('.evrak-list-area') || !s.querySelector('.evrak-viewer-area')) continue;
            const sekme = s.closest('.dx-multiview-item');
            if (sekme && !sekme.classList.contains('dx-item-selected')) continue;
            if (!s.getClientRects().length) continue;
            return s;
        }
        return null;
    }
    // Ağaçta seçili BELGE düğümü (klasörler sayılmaz: belge satırlarında UYAP'ın .evrak-list--item'ı vardır).
    function seciliDugum(sp) {
        const n = sp.querySelector('.evrak-treeview .dx-treeview-node[aria-selected="true"]');
        const satir = n && n.querySelector(':scope > .dx-treeview-item');
        return satir && satir.querySelector('.evrak-list--item') ? n : null;
    }
    function uyapGezinme(sp) { return Array.prototype.slice.call(sp.querySelectorAll('.evrak-list-area .evrak-list--button'), 0, 2); }

    // UYAP görüntüleyicisi son yakınlaştırmayı bu anahtarda tutar (canlı, 2026-11-11: "Genişliğe Sığdır" →
    // "fit-to-window"). Telefonda belge açılmadan önce yazılır: belge doğrudan ekrana sığmış gelir.
    const ZOOM_TERCIHI = 'ude:pref:viewer-last-zoom';
    function sigdirTercihi() {
        try { if (localStorage.getItem(ZOOM_TERCIHI) !== '"fit-to-window"') localStorage.setItem(ZOOM_TERCIHI, '"fit-to-window"'); } catch (e) {}
    }
    // Görüntüleyici yüzde ile açıldıysa (tercih okunmadıysa / değiştiyse) kendi "Genişliğe Sığdır" düğmesine basılır.
    // zorla: zaten sığdırma kipindeyken de bas (bölme genişliği değişti: yeniden hesaplasın).
    function sigdir(sp, zorla) {
        const g = sp.querySelector(':scope > .cu-me-gor');
        if (!g) return;
        const b = Array.prototype.find.call(g.querySelectorAll('.udv-btn-fit'), function(x) { return /geni[şs]li[ğg]e/i.test(x.title || ''); });
        if (!b) return;                       // görüntüleyici henüz yok (PDF: UYAP'ın varsayılanı zaten "Sayfa Genişliği")
        const kutu = g.querySelector('.udv-zoom-controls input');
        if (zorla || (kutu && /^\s*\d+([.,]\d+)?\s*%/.test(kutu.value))) b.click();
    }

    function cubukDugmesi(sinif, ic, ipucu, fn) {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = sinif;
        b.innerHTML = ic;
        b.title = ipucu;
        b.setAttribute('aria-label', ipucu);
        b.addEventListener('click', function(ev) { ev.preventDefault(); ev.stopPropagation(); fn(); });
        return b;
    }
    function gecis(durum) {
        const sp = evrakBolucu();
        const d = sp && durumlar.get(sp);
        if (!d) return;
        d.durum = durum;
        if (durum === 'belge') d.sigdirSon = Date.now() + 6000;
        evrakTur();
        if (durum === 'liste') {
            const n = seciliDugum(sp);
            if (n) { try { n.querySelector(':scope > .dx-treeview-item').scrollIntoView({ block: 'center' }); } catch (e) {} }
        } else sigdir(sp, true);
    }
    function cubukKur(kart) {
        const tp = kart.querySelector('.dx-tabpanel');
        if (!tp) return null;
        let c = tp.querySelector(':scope > .cu-me-cubuk');
        if (c) return c;
        c = document.createElement('div');
        c.className = 'cu-me-cubuk';
        const ad = document.createElement('span');
        ad.className = 'cu-me-ad';
        // Önceki / sonraki: UYAP'ın liste başındaki iki düğmesi (gizli bölmede; tıklaması yine çalışır).
        const git = function(i) { return function() { const sp = evrakBolucu(); const u = sp && uyapGezinme(sp)[i]; if (u) u.click(); }; };
        c.append(cubukDugmesi('cu-me-geri', OK_SOL + '<span>Belgeler</span>', 'Belge listesine dön', function() { gecis('liste'); }),
                 ad, cubukDugmesi('cu-me-git', OK_SOL, 'Önceki belge', git(0)), cubukDugmesi('cu-me-git', OK_SAG, 'Sonraki belge', git(1)));
        tp.insertBefore(c, tp.firstChild);
        return c;
    }

    function evrakTur() {
        const sp = mobil() ? evrakBolucu() : null;
        const kart = sp ? sp.closest('.dosya-sorgula-popup') : null;
        const isaretli = document.querySelectorAll('.dosya-sorgula-popup[data-cu-me]');
        for (let i = 0; i < isaretli.length; i++) if (isaretli[i] !== kart) isaretli[i].removeAttribute('data-cu-me');
        if (!sp || !kart) return;
        for (let i = 0; i < sp.children.length; i++) {
            const b = sp.children[i];
            if (!b.classList.contains('dx-splitter-item')) continue;
            const sinif = b.querySelector('.evrak-list-area') ? 'cu-me-liste' : b.querySelector('.evrak-viewer-area') ? 'cu-me-gor' : '';
            if (sinif && !b.classList.contains(sinif)) b.classList.add(sinif);
        }
        const n = seciliDugum(sp);
        const secili = n ? n.getAttribute('data-item-id') : null;
        let d = durumlar.get(sp);
        if (!d) {
            sigdirTercihi();
            d = { durum: secili ? 'belge' : 'liste', secili: secili, sigdirSon: Date.now() + 6000 };
            durumlar.set(sp, d);
        } else if (secili && secili !== d.secili) {
            // Yeni belge seçildi: listeden dokunuldu, önceki / sonraki ya da kart görev çubuğundan geri açılırken.
            d.durum = 'belge';
            d.sigdirSon = Date.now() + 6000;
        }
        if (secili) d.secili = secili;
        if (kart.getAttribute('data-cu-me') !== d.durum) kart.setAttribute('data-cu-me', d.durum);
        if (d.durum !== 'belge') return;
        const c = cubukKur(kart);
        if (c) {
            const ad = n ? (n.getAttribute('aria-label') || '') : '';
            const adOgesi = c.querySelector('.cu-me-ad');
            if (ad && adOgesi.textContent !== ad) { adOgesi.textContent = ad; adOgesi.title = ad; }
            const u = uyapGezinme(sp);
            const git = c.querySelectorAll('.cu-me-git');
            for (let i = 0; i < git.length; i++) {
                const kapali = !u[i] || u[i].classList.contains('pe-none');
                if (git[i].disabled !== kapali) git[i].disabled = kapali;
                if (u[i] && u[i].title && git[i].title !== u[i].title) { git[i].title = u[i].title; git[i].setAttribute('aria-label', u[i].title); }
            }
        }
        // Belge yeni açıldıysa bir süre: yüzdeyle geldiyse genişliğe sığdır. Kullanıcı görüntüleyiciye dokununca biter.
        if (d.sigdirSon && Date.now() < d.sigdirSon) sigdir(sp, false);
    }

    // Listede bir belgeye dokunma (yakalama aşamasında; UYAP belgeyi açarken biz bölmeyi değiştiririz). Zaten seçili
    // belgeye yeniden dokununca da belgeye dönülür. İşaret kutusu, aç/kapa oku, indirme ve "+ İş" düğmeleri hariç.
    document.addEventListener('click', function(ev) {
        if (!mobil()) return;
        const t = ev.target;
        const satir = t && t.closest ? t.closest('.dosya-sorgula-popup .evrak-treeview .dx-treeview-item') : null;
        if (!satir || !satir.querySelector('.evrak-list--item')) return;
        if (t.closest('input, button, .dx-treeview-toggle-item-visibility, .evrak-agac-tiklama')) return;
        const sp = satir.closest('.dx-splitter');
        const d = sp && durumlar.get(sp);
        if (!d) return;
        sigdirTercihi();
        const dugum = satir.closest('.dx-treeview-node');
        d.durum = 'belge';
        if (dugum && dugum.getAttribute('data-item-id')) d.secili = dugum.getAttribute('data-item-id');
        d.sigdirSon = Date.now() + 6000;
        evrakTur();
    }, true);
    // Kullanıcı görüntüleyiciyle uğraşıyor (yakınlaştırma, kaydırma): kendiliğinden sığdırma biter.
    ['pointerdown', 'touchstart', 'wheel'].forEach(function(tur) {
        document.addEventListener(tur, function(ev) {
            const g = ev.target && ev.target.closest ? ev.target.closest('.dosya-sorgula-popup .cu-me-gor') : null;
            const sp = g && g.parentElement;
            const d = sp && durumlar.get(sp);
            if (d) d.sigdirSon = 0;
        }, { capture: true, passive: true });
    });
    // Telefon çevrildi / pencere boyutu değişti: sığdırma kipindeki belge yeni genişliğe otursun.
    let boyutZamani = null;
    window.addEventListener('resize', function() {
        clearTimeout(boyutZamani);
        boyutZamani = setTimeout(function() {
            const sp = mobil() ? evrakBolucu() : null;
            const d = sp && durumlar.get(sp);
            const kutu = sp && sp.querySelector(':scope > .cu-me-gor .udv-zoom-controls input');
            if (d && d.durum === 'belge' && kutu && !/^\s*\d/.test(kutu.value)) sigdir(sp, true);
        }, 250);
    });

    // ── 2b. Dosya penceresi sekmeleri: sol altta yuvarlak düğme (kullanıcı isteği 2026-11-11) ──
    // Telefonda UYAP'ın yatay sekme şeridi gizlenir (yer kaplıyor, yana kaydırmak gerekiyordu). Kartın sol altında
    // temaya uygun yuvarlak "sekmeler" düğmesi; dokununca o penceredeki sekmeler yukarı doğru yuvarlak düğmeler
    // olarak açılır (yanlarında adları). Seçim UYAP'ın kendi durum işleviyle anında: sekme paneli denetimli
    // (main.js: AdaletTabPanel selectedIndex:M, onSelectionChanged:e=>D(e.itemIndex)) → React prop'u doğrudan
    // çağrılır; bulunamazsa sekmenin kendisine tıklanır. Yalnız kartın ANA sekme paneli (.custom-tab-header);
    // sekmelerin içindeki alt paneller (ör. Dosya Ödemeleri) dokunulmaz.
    const SK_SVG = function(ic) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ic + '</svg>'; };
    const SK_SIMGE = [
        [/evrak|belge/i, '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>'],
        [/taraf|vekil|ki[şs]i/i, '<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/><circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.2c3 .2 5.5 2.6 5.5 5.8"/>'],
        [/safahat|tarih|ge[çc]mi[şs]/i, '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'],
        [/tahsil|re[şs]i|harc|masraf|[öo]deme|para/i, '<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M7 9.5v5M17 9.5v5"/>'],
        [/duru[şs]ma|celse|takvim/i, '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>'],
        [/haciz|icra|mal/i, '<path d="M4 21h16M6 18V10M10 18V10M14 18V10M18 18V10M3 10l9-6 9 6"/>'],
        [/dosya|genel|bilgi/i, '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'],
    ];
    const SK_IZGARA = SK_SVG('<rect x="4" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.6"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.6"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.6"/>');
    const SK_KAPAT = SK_SVG('<path d="M6 6l12 12M18 6L6 18"/>');
    function skSimge(ad) {
        for (let i = 0; i < SK_SIMGE.length; i++) if (SK_SIMGE[i][0].test(ad)) return SK_SVG(SK_SIMGE[i][1]);
        const h = (ad.match(/[A-Za-zÇĞİÖŞÜçğıöşü0-9]/g) || ['?']).slice(0, 2).join('');
        return '<b>' + h.replace(/[<>&]/g, '') + '</b>';
    }
    function anaSekmePaneli(kart) { return kart.querySelector('.custom-tab-header .dx-tabpanel') || kart.querySelector('.dx-tabpanel'); }
    function sekmeler(tp) {
        const sr = tp.querySelector(':scope > .dx-tabpanel-tabs');
        return sr ? Array.prototype.slice.call(sr.querySelectorAll('.dx-tab')) : [];
    }
    // Sekmenin görünen adı. DevExtreme, seçili (kalın) hâlde yer sıçramasın diye başlığın yanında GİZLİ bir
    // ölçüm kopyası (.dx-tab-text-span-pseudo) tutar; düz textContent bunu da alıp adı ikiler ("EvrakEvrak",
    // kullanıcı bulgusu 2026-11-13). Kopyayı (DOM'a dokunmadan, klonda) atıp okuruz; sınıf adı değişmişse
    // tam ikilenmeyi tekleyen güvenlik ağı da var.
    function sekmeAdi(x) {
        const tt = x.querySelector('.dx-tab-text') || x;
        const klon = tt.cloneNode(true);
        klon.querySelectorAll('.dx-tab-text-span-pseudo, [class*="pseudo"]').forEach(function(p) { p.remove(); });
        let s = (klon.textContent || '').replace(/\s+/g, ' ').trim();
        const h = s.length / 2;
        if (s.length > 1 && s.length % 2 === 0 && s.slice(0, h) === s.slice(h)) s = s.slice(0, h);
        return s;
    }
    // React fiber'ından AdaletTabPanel'in onSelectionChanged'ini bulur (DevExtreme'in çizdiği düğümden yukarı).
    function reactSecici(tp) {
        let el = tp;
        for (let k = 0; el && k < 4; k++, el = el.parentElement) {
            const ad = Object.keys(el).find(function(x) { return x.indexOf('__reactFiber$') === 0; });
            let f = ad ? el[ad] : null;
            for (let j = 0; f && j < 25; j++, f = f.return) {
                const p = f.memoizedProps;
                if (p && typeof p.onSelectionChanged === 'function' && Array.isArray(p.items) && 'selectedIndex' in p) return p.onSelectionChanged;
            }
        }
        return null;
    }
    function sekmeSec(tp, i) {
        const fn = reactSecici(tp);
        if (fn) { try { fn({ itemIndex: i }); return; } catch (e) { console.warn('[cu] sekme', e); } }
        const s = sekmeler(tp)[i];
        if (s) s.click();
    }
    function skKapat(kok) { if (kok) kok.classList.remove('acik'); }
    function skKur(kart) {
        const ic = kart.querySelector(':scope > .dx-overlay-content') || kart;
        let kok = ic.querySelector(':scope > .cu-sk');
        if (kok) return kok;
        kok = document.createElement('div');
        kok.className = 'cu-sk';
        const perde = document.createElement('div');
        perde.className = 'cu-sk-perde';
        perde.addEventListener('click', function(ev) { ev.stopPropagation(); skKapat(kok); });
        const liste = document.createElement('div');
        liste.className = 'cu-sk-liste';
        const ana = cubukDugmesi('cu-sk-ana', SK_IZGARA + SK_KAPAT, 'Sekmeler', function() {
            kok.classList.toggle('acik');
            if (kok.classList.contains('acik')) skTur(kart);
        });
        kok.append(perde, liste, ana);
        ic.appendChild(kok);
        return kok;
    }
    function skTur(kart) {
        const tp = anaSekmePaneli(kart);
        const s = tp ? sekmeler(tp) : [];
        if (!tp || s.length < 2) { const k = kart.querySelector('.cu-sk'); if (k) k.style.display = 'none'; return; }
        if (!tp.classList.contains('cu-sk-tp')) tp.classList.add('cu-sk-tp');
        const kok = skKur(kart);
        if (kok.style.display) kok.style.display = '';
        const liste = kok.querySelector('.cu-sk-liste');
        const adlar = s.map(sekmeAdi);
        const imza = adlar.join('|');
        if (liste.getAttribute('data-imza') !== imza) {
            liste.setAttribute('data-imza', imza);
            liste.textContent = '';
            adlar.forEach(function(ad, i) {
                const b = cubukDugmesi('cu-sk-madde', '<span class="cu-sk-ad"></span><span class="cu-sk-yuv">' + skSimge(ad) + '</span>', ad, function() {
                    skKapat(kok);
                    const t = anaSekmePaneli(kart);
                    if (t) sekmeSec(t, i);
                });
                b.querySelector('.cu-sk-ad').textContent = ad;
                b.style.setProperty('--cu-sk-i', String(adlar.length - 1 - i));
                liste.appendChild(b);
            });
        }
        const m = liste.children;
        for (let i = 0; i < m.length; i++) {
            const sec = !!(s[i] && s[i].classList.contains('dx-tab-selected'));
            if (m[i].classList.contains('secili') !== sec) m[i].classList.toggle('secili', sec);
        }
        const ana = kok.querySelector('.cu-sk-ana');
        const acik = kok.classList.contains('acik') ? 'true' : 'false';
        if (ana.getAttribute('aria-expanded') !== acik) ana.setAttribute('aria-expanded', acik);
    }
    function sekmeTur() {
        const kartlar = document.querySelectorAll('.dosya-sorgula-popup');
        for (let i = 0; i < kartlar.length; i++) {
            const k = kartlar[i];
            if (!k.classList.contains('dx-overlay-wrapper')) continue;
            if (!mobil()) { const kok = k.querySelector('.cu-sk'); if (kok) { skKapat(kok); kok.style.display = 'none'; } continue; }
            skTur(k);
        }
    }
    document.addEventListener('keydown', function(ev) {
        if (ev.key !== 'Escape') return;
        const a = document.querySelector('.cu-sk.acik');
        if (a) { skKapat(a); ev.stopPropagation(); }
    }, true);

    // ── 3. Kart başlığı: "Evrak İndir (N)" sayısı ──
    function baslikTur() {
        const l = document.querySelectorAll('.dosya-sorgula-popup .dx-popup-title [data-tumevrak]');
        for (let i = 0; i < l.length; i++) {
            const m = /\((\d+)\)/.exec(l[i].textContent || '');
            const say = m ? m[1] : '';
            if (l[i].getAttribute('data-cu-say') !== say) l[i].setAttribute('data-cu-say', say);
        }
    }

    // ── 4. Dokunma ──
    // iOS: uzun basış (550 ms, parmak kıpırdamadan) → contextmenu olayı. Bizim menülerimizden biri açıldıysa
    // (olay engellendiyse) parmak kalkınca gelebilecek tıklama yutulur; açılmadıysa hiçbir şey değişmez.
    const IOS = /iP(hone|ad|od)/.test(navigator.userAgent || '') || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    if (IOS) {
        let zaman = null, bas = null, yutSon = 0;
        const iptal = function() { if (zaman) { clearTimeout(zaman); zaman = null; } };
        document.addEventListener('touchstart', function(ev) {
            iptal();
            if (ev.touches.length !== 1) return;
            const t = ev.touches[0];
            bas = { x: t.clientX, y: t.clientY, hedef: ev.target };
            zaman = setTimeout(function() {
                zaman = null;
                if (!bas.hedef || !bas.hedef.isConnected) return;
                const olay = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, view: window, clientX: bas.x, clientY: bas.y, button: 2 });
                if (!bas.hedef.dispatchEvent(olay)) yutSon = Date.now() + 900;
            }, 550);
        }, { capture: true, passive: true });
        document.addEventListener('touchmove', function(ev) {
            const t = ev.touches[0];
            if (zaman && bas && t && (Math.abs(t.clientX - bas.x) > 10 || Math.abs(t.clientY - bas.y) > 10)) iptal();
        }, { capture: true, passive: true });
        ['touchend', 'touchcancel'].forEach(function(tur) { document.addEventListener(tur, iptal, { capture: true, passive: true }); });
        ['mousedown', 'mouseup', 'click'].forEach(function(tur) {
            document.addEventListener(tur, function(ev) {
                if (Date.now() > yutSon) return;
                if (ev.target && ev.target.closest && ev.target.closest('#uyap-sagmenu')) return;     // menüden seçim
                ev.stopImmediatePropagation();
                if (tur === 'click') { ev.preventDefault(); yutSon = 0; }
            }, true);
        });
    }
    // Açık kullanıcı şeridi (tarih, tema, kullanıcı): dışarı dokununca kapanır.
    document.addEventListener('click', function(ev) {
        if (!dar() || !ev.target || !ev.target.closest) return;
        if (!document.querySelector('.u-navbar .navbar-collapse.show')) return;
        if (ev.target.closest('.u-navbar, #cu-tema-menu, #cu-mavi-menu, .dropdown-menu, .dx-overlay-wrapper')) return;
        const b = document.getElementById('navHeaderContent');
        if (b) b.click();
    });

    function tur() {
        evrakTur();
        sekmeTur();
        baslikTur();
    }
    window.__cuKipDegisince(tur);
    if (window.__cuDegisince) window.__cuDegisince(tur);       // DOM değişince, bir sonraki çizimden önce (tema.js)
    setInterval(tur, 400);
})();
