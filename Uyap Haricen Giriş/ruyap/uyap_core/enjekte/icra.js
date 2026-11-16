// ═══════════════════════════════════════════════════════════════════════════════════════
// ÇOKLU UYAP — İCRA TAKİP SÜREÇLERİ SAYFASI (2026-11-15)
// ═══════════════════════════════════════════════════════════════════════════════════════
// Ana menüye (sol menü, #av-sidebar-wrapper) "İcra Takip Süreçleri" girişi eklenir; basınca içerik alanının
// üstünde bizim sayfamız açılır (görev çubuğu ve menü yerinde kalır).
//
// Pencere gibi davranır (kullanıcı isteği 16.11.2026: "iş akışı akıp gitsin, kayıp gitmesin"):
// • Görev çubuğunda kendi düğmesi var (CU.gorev, kaynak 'icra'): tıklayınca küçülür / öne gelir. Küçültülünce
//   sayfa silinmez, gizlenir: filtre, sayfa, seçili dosya paneli ve kaydırma yerinde kalır.
// • Menüde başka bir sayfaya gidilince sayfa kapanmaz, görev çubuğuna küçülür. Yalnız "Kapat" kapatır.
// • "UYAP'ta aç": dosya kartı bu sayfanın ÜSTÜNDE açılır (kart z ≤ 1590, sayfa 1400). Kart küçültülünce ya da
//   X ile kapatılınca kullanıcı kaldığı yere, bu sayfaya döner. Bu arada sayfa küçülmüşse de
//   (kartlar.js __cuKartKapaninca) yeniden öne gelir.
// Açık / küçük hâli sayfa yenilemesinden sonra da korunur (sessionStorage: '1' açık, 'k' küçük).
//
// Veri: ofis bilgisayarındaki büro veritabanı, /__cu__/veri/… uçları (src/icra_api.rs). Uçlar bütün büro
// kullanıcılarına açık (kullanıcı kararı 15.11.2026): bu bilgisayarda vekil, uzakta tünel cevaplar.
// UYAP'a istek atılmaz; yalnız "UYAP'ta aç" düğmesi dosyayı UYAP'ta bulup kendi kartını açar.
//
// Sekmeler: Süreçler (aşama şeridi + borçlu listesi + dosya paneli) · Kesinleşme Süreleri · Resmî Tatiller.
// Renkler tema değişkenlerinden (--cu-t-*, --cu-vurgu*); UYAP / Koyu temalarında kendi yedek değerleri.
(function() {
    'use strict';
    const anaCerceve = window.__uyapAnaCerceve ? window.__uyapAnaCerceve() : window.top === window;
    if (!anaCerceve || window.__cuIcraTakip) return;
    window.__cuIcraTakip = true;

    const UC = '/__cu__/veri/';
    const ACIK_ANAHTAR = 'cuIcraTakipAcik';
    const ASAMA_RENK = {
        takip_talebi: '#8a877e', odeme_emri: '#8a877e', teb_olusturuluyor: '#3f7fa3', ptt_veriliyor: '#3f7fa3',
        teblige_cikti: '#3f7fa3', teblig: '#b8901f', iade: '#c06a2f', itiraz: '#b84444', durduruldu: '#7a5fb0',
        kesinlesti: '#4f8a57', '': '#8a877e'
    };
    const ASAMA_KISA = {
        takip_talebi: 'Takip Talebi', odeme_emri: 'Ödeme Emri', teb_olusturuluyor: 'Tebligat Oluşturuluyor',
        ptt_veriliyor: "PTT'ye Veriliyor", teblige_cikti: 'Tebliğe Çıktı', teblig: 'Tebliğ', iade: 'İade',
        itiraz: 'İtiraz', durduruldu: 'Durduruldu', kesinlesti: 'Kesinleşti', '': 'Hesaplanmadı'
    };
    const SIRA = ['takip_talebi', 'odeme_emri', 'teb_olusturuluyor', 'ptt_veriliyor', 'teblige_cikti', 'teblig', 'kesinlesti'];

    // ── Yardımcılar ──
    function e(tag, oz, cocuklar) {
        const x = document.createElement(tag);
        if (oz) Object.keys(oz).forEach(function(k) {
            const v = oz[k];
            if (v === undefined || v === null || v === false) return;
            if (k === 'sinif') x.className = v;
            else if (k === 'metin') x.textContent = v;
            else if (k === 'stil') x.setAttribute('style', v);
            else if (k.indexOf('on') === 0) x.addEventListener(k.slice(2), v);
            else x.setAttribute(k, v === true ? '' : v);
        });
        (cocuklar || []).forEach(function(c) {
            if (c === null || c === undefined || c === false) return;
            x.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
        });
        return x;
    }
    function bos(x) { while (x && x.firstChild) x.removeChild(x.firstChild); return x; }
    function gunMetni(iso) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
        return m ? m[3] + '.' + m[2] + '.' + m[1] : '';
    }
    function bildir(m) { if (window.__uyapBildirim) window.__uyapBildirim(m); }
    const dar = function() { return !!(window.__cuDar && window.__cuDar()); };

    async function istek(uc, govde) {
        let r;
        try {
            r = await fetch(UC + uc, govde === undefined
                ? { cache: 'no-store', credentials: 'same-origin' }
                : { method: 'POST', cache: 'no-store', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(govde) });
        } catch (x) {
            throw new Error('Ofis bilgisayarına ulaşılamadı.');
        }
        let j = null;
        try { j = await r.json(); } catch (x) {}
        if (!j) throw new Error('Ofis bilgisayarından beklenmeyen yanıt (' + r.status + '). Program güncel olmayabilir.');
        if (!j.ok) {
            if (j.kod === 'E-402') throw new Error('Büro veritabanı hazır değil (E-402). Ofis bilgisayarında program açık ve paylaşım başlamış olmalı.');
            throw new Error(j.error || 'İşlem yapılamadı.');
        }
        return j.veri;
    }

    // ── Stil ──
    function stilKur() {
        if (document.getElementById('cu-icra-stil')) return;
        const css = [
            '#cu-icra{--i-zemin:var(--cu-t-zemin,#f3f4f7);--i-yuzey:var(--cu-t-yuzey,#fff);--i-serit:var(--cu-t-serit,#f5f6fa);',
            '--i-kenar:var(--cu-t-kenar,#dee2e6);--i-yazi:var(--cu-t-yazi,#212529);--i-vurgu:var(--cu-vurgu,#2d4596);',
            '--i-vurgu-koyu:var(--cu-vurgu-koyu,#1f3277);--i-vurgu-zemin:var(--cu-vurgu-zemin,#e8ecf8);--i-vurgu-yazi:var(--cu-vurgu-yazi,#1f2f70);',
            '--i-soluk:color-mix(in srgb,var(--i-yazi) 68%,var(--i-yuzey));--i-silik:color-mix(in srgb,var(--i-yazi) 45%,var(--i-yuzey));',
            'position:fixed;z-index:1400;top:var(--cu-nav-yuk,60px);right:0;bottom:var(--cu-cubuk-yuk,44px);left:0;',
            'background:var(--i-zemin);color:var(--i-yazi);font:14px/1.45 "Segoe UI",system-ui,sans-serif;display:flex;flex-direction:column;overflow:hidden}',
            'html[data-cu-tema="koyu"] #cu-icra{--i-zemin:#1d2130;--i-yuzey:#2a2f40;--i-serit:#262b3b;--i-kenar:#3a4055;--i-yazi:#dedede;',
            '--i-vurgu:#3a55b5;--i-vurgu-koyu:#8ea2e6;--i-vurgu-zemin:#2c3554;--i-vurgu-yazi:#c3cdf2}',
            '#cu-icra *{box-sizing:border-box}',
            '#cu-icra button,#cu-icra input,#cu-icra select{font:inherit;color:inherit}',
            '#cu-icra .i-bas{display:flex;align-items:center;gap:14px;padding:14px 22px 0;flex-wrap:wrap}',
            '#cu-icra .i-bas h1{font-size:19px;margin:0;font-weight:600}',
            '#cu-icra .i-durum{color:var(--i-soluk);font-size:12.5px}',
            '#cu-icra .i-bosluk{flex:1}',
            '#cu-icra .i-sekmeler{display:flex;gap:2px;padding:10px 22px 0;border-bottom:1px solid var(--i-kenar)}',
            '#cu-icra .i-sekmeler button{background:none;border:0;border-bottom:2px solid transparent;padding:8px 12px;color:var(--i-soluk);cursor:pointer;margin-bottom:-1px}',
            '#cu-icra .i-sekmeler button.on{color:var(--i-vurgu-koyu);border-color:var(--i-vurgu);font-weight:600}',
            '#cu-icra .i-govde{flex:1;display:flex;min-height:0;position:relative}',
            '#cu-icra .i-ana{flex:1;overflow:auto;padding:16px 22px 22px;min-width:0}',
            '#cu-icra .i-asamalar{display:flex;gap:8px;margin-bottom:14px;overflow-x:auto;padding-bottom:2px}',
            '#cu-icra .i-asama{flex:1 0 96px;background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:12px;padding:9px 11px;text-align:left;cursor:pointer;min-width:96px}',
            '#cu-icra .i-asama .n{font-size:21px;font-weight:600;line-height:1.1}',
            '#cu-icra .i-asama .t{font-size:12px;color:var(--i-soluk);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
            '#cu-icra .i-asama i{display:block;height:3px;border-radius:2px;margin-top:7px}',
            '#cu-icra .i-asama.on{border-color:var(--i-vurgu);box-shadow:0 0 0 2px var(--i-vurgu-zemin)}',
            '#cu-icra .i-arac{display:flex;gap:10px;align-items:center;margin-bottom:12px;flex-wrap:wrap}',
            '#cu-icra input[type=search],#cu-icra input[type=text],#cu-icra input[type=date],#cu-icra input[type=number],#cu-icra select{background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:9px;padding:7px 10px;min-width:0}',
            '#cu-icra input[type=search]{flex:1;max-width:420px}',
            '#cu-icra .i-onay{display:flex;align-items:center;gap:6px;color:var(--i-soluk);cursor:pointer;user-select:none}',
            '#cu-icra .i-d{border:1px solid var(--i-kenar);background:var(--i-yuzey);border-radius:9px;padding:7px 13px;cursor:pointer;white-space:nowrap}',
            '#cu-icra .i-d:hover{border-color:var(--i-vurgu)}',
            '#cu-icra .i-d.ana{background:var(--i-vurgu);border-color:var(--i-vurgu);color:var(--cu-vurgu-uzeri,#fff)}',
            '#cu-icra .i-d.kucuk{padding:4px 9px;font-size:12.5px;border-radius:8px;color:var(--i-soluk)}',
            '#cu-icra .i-d.tehlike{color:#b84444}',
            '#cu-icra .i-d[disabled]{opacity:.55;cursor:default}',
            '#cu-icra table{width:100%;border-collapse:separate;border-spacing:0;background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:12px;overflow:hidden}',
            '#cu-icra th{text-align:left;font-weight:600;font-size:12px;color:var(--i-soluk);background:var(--i-serit);padding:8px 12px;white-space:nowrap}',
            '#cu-icra td{padding:8px 12px;border-top:1px solid var(--i-kenar);vertical-align:top}',
            '#cu-icra tr.tik{cursor:pointer}#cu-icra tr.tik:hover td{background:var(--i-serit)}',
            '#cu-icra tr.sec td{background:var(--i-vurgu-zemin)!important}',
            '#cu-icra .dno{font-weight:600;white-space:nowrap}#cu-icra .alt{color:var(--i-soluk);font-size:12px}',
            '#cu-icra .chip{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12.5px;font-weight:500;white-space:nowrap;',
            'background:color-mix(in srgb,var(--r) 18%,var(--i-yuzey));color:color-mix(in srgb,var(--r) 80%,var(--i-yazi))}',
            '#cu-icra .tarih{color:var(--i-silik);font-size:12px;margin-left:5px;white-space:nowrap}',
            '#cu-icra .uyari{color:#c06a2f;font-size:12px}',
            '#cu-icra .i-sayfa{display:flex;justify-content:space-between;align-items:center;color:var(--i-soluk);font-size:12.5px;margin-top:10px;gap:8px}',
            '#cu-icra .i-bilgi{background:var(--i-yuzey);border:1px dashed var(--i-kenar);border-radius:12px;padding:22px;color:var(--i-soluk);text-align:center}',
            '#cu-icra .i-hata{background:color-mix(in srgb,#b84444 12%,var(--i-yuzey));color:color-mix(in srgb,#b84444 80%,var(--i-yazi));border-radius:10px;padding:9px 13px;margin-bottom:12px}',
            '#cu-icra .i-panel{width:470px;max-width:100%;background:var(--i-yuzey);border-left:1px solid var(--i-kenar);display:flex;flex-direction:column;box-shadow:-10px 0 24px rgba(0,0,0,.06)}',
            '#cu-icra .i-panel .p-bas{padding:14px 18px 0}',
            '#cu-icra .i-panel h2{font-size:17px;margin:0}',
            '#cu-icra .i-rozet{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 10px}',
            '#cu-icra .i-rozet span{background:var(--i-zemin);color:var(--i-soluk);border-radius:999px;padding:2px 10px;font-size:12.5px}',
            '#cu-icra .i-panel .p-ic{flex:1;overflow:auto;padding:12px 18px 18px}',
            '#cu-icra .i-kart{border:1px solid var(--i-kenar);border-radius:12px;padding:11px 13px;margin-bottom:10px}',
            '#cu-icra .i-kart .b{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}',
            '#cu-icra .i-kart .ad{font-weight:600}',
            '#cu-icra .sat{display:flex;justify-content:space-between;gap:12px;font-size:13px;margin-top:5px;color:var(--i-soluk)}',
            '#cu-icra .sat b{color:var(--i-yazi);font-weight:500;text-align:right}',
            '#cu-icra .i-ilerleme{display:flex;gap:3px;margin:9px 0 2px}',
            '#cu-icra .i-ilerleme i{flex:1;height:5px;border-radius:3px;background:var(--i-kenar)}',
            '#cu-icra .i-ilerleme i.d{background:var(--i-vurgu)}',
            '#cu-icra .i-eylem{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px;align-items:center}',
            '#cu-icra .bolum{font-size:11.5px;letter-spacing:.04em;color:var(--i-silik);text-transform:uppercase;margin:6px 0 8px;font-weight:600}',
            '#cu-icra .i-olay{border-left:2px solid var(--i-kenar);padding:0 0 12px 12px;margin-left:4px;position:relative}',
            '#cu-icra .i-olay:before{content:"";position:absolute;left:-5px;top:5px;width:8px;height:8px;border-radius:50%;background:var(--i-vurgu)}',
            '#cu-icra .i-form{display:flex;gap:8px;flex-wrap:wrap;align-items:center;background:var(--i-serit);border-radius:10px;padding:10px;margin:10px 0}',
            '#cu-icra .i-iki{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}',
            '#cu-icra .i-kutu{background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:14px;padding:16px}',
            '#cu-icra .i-kutu h3{margin:0 0 4px;font-size:15px}#cu-icra .i-kutu p{margin:0 0 10px;color:var(--i-soluk);font-size:13px}',
            '#cu-icra .gun{display:inline-block;min-width:54px;text-align:center;border:1px solid var(--i-kenar);border-radius:8px;padding:2px 8px;background:var(--i-zemin);font-weight:600}',
            '#cu-icra .i-sarı{background:color-mix(in srgb,#b8901f 16%,var(--i-yuzey));border-radius:10px;padding:8px 12px;font-size:12.5px;margin-top:10px}',
            '#cu-menu-icra .SidebarLabel{font-weight:600}',
            '#cu-menu-icra.on > li > a{background:var(--cu-vurgu-zemin,#e8ecf8)}',
            // Dar ekran: liste satırları kart gibi, panel tam sayfa.
            'html.cu-dar #cu-icra .i-bas,html.cu-dar #cu-icra .i-sekmeler{padding-left:12px;padding-right:12px}',
            'html.cu-dar #cu-icra .i-ana{padding:12px}',
            'html.cu-dar #cu-icra .i-bas{gap:4px 10px;padding-top:10px}',
            'html.cu-dar #cu-icra .i-bas h1{font-size:17px}',
            'html.cu-dar #cu-icra .i-durum{order:3;flex-basis:100%}',
            'html.cu-dar #cu-icra input[type=search]{flex:1 1 100%;max-width:none}',
            'html.cu-dar #cu-icra .i-panel{position:absolute;inset:0;width:auto;border-left:0;z-index:2}',
            'html.cu-dar #cu-icra .i-iki{grid-template-columns:1fr}',
            'html.cu-dar #cu-icra table.liste thead{display:none}',
            'html.cu-dar #cu-icra table.liste tr{display:block;border-top:1px solid var(--i-kenar);padding:6px 0}',
            'html.cu-dar #cu-icra table.liste tr:first-child{border-top:0}',
            'html.cu-dar #cu-icra table.liste td{display:block;border:0;padding:2px 12px}',
        ].join('\n');
        document.head.appendChild(e('style', { id: 'cu-icra-stil', metin: css }));
    }

    // ── Durum ──
    const d = {
        sekme: 'surec', filtre: { q: '', asama: '', acik: true, sayfa: 1 }, ozet: null, liste: null,
        secili: null, panelSekme: 'borclular', hata: ''
    };
    let kok = null, govde = null, araZaman = null;
    let gorunum = 'kapali';                     // 'kapali' | 'acik' | 'kucuk'
    const icradanAcilan = new Set();             // bu sayfadan "UYAP'ta aç" ile açılan dosya kartları (anahtar)
    function kartAcikMi() {
        return !!Array.from(document.querySelectorAll('.dosya-sorgula-popup .dx-popup-title'))
            .find(function(x) { return x.getClientRects().length > 0; });
    }
    function durumSakla() {
        try {
            if (gorunum === 'kapali') sessionStorage.removeItem(ACIK_ANAHTAR);
            else sessionStorage.setItem(ACIK_ANAHTAR, gorunum === 'acik' ? '1' : 'k');
        } catch (x) {}
    }

    // ── Menü girişi ──
    function menuGirisi() {
        const yan = document.getElementById('av-sidebar-wrapper');
        if (!yan || document.getElementById('cu-menu-icra')) return;
        const ust = yan.querySelector('.sidebar-top-part') || yan.firstElementChild || yan;
        const ul = e('ul', { id: 'cu-menu-icra', sinif: 'menu-list' + (gorunum === 'acik' ? ' on' : '') }, [
            e('li', { sinif: 'SidebarLink' }, [
                e('a', { href: '#', title: 'Büro veritabanındaki icra dosyalarının takip aşamaları, kesinleşme ve tebliğ durumları',
                         onclick: function(ev) { ev.preventDefault(); ac(true); } },
                  [e('span', { sinif: 'SidebarLabel', metin: 'İcra Takip Süreçleri' })])
            ])
        ]);
        ust.insertBefore(ul, ust.firstChild);
    }

    // ── Sayfa ──
    function yerlestir() {
        if (!kok) return;
        const ic = document.getElementById('page-content-wrapper');
        let sol = 0;
        if (ic && !dar()) sol = Math.max(0, Math.round(ic.getBoundingClientRect().left));
        if (kok.style.left !== sol + 'px') kok.style.left = sol + 'px';
    }

    // one: kullanıcı açtı (menü, görev çubuğu) → ekrandaki dosya kartı görev çubuğuna küçülür, sayfa görünsün.
    function ac(one) {
        stilKur();
        gorunum = 'acik';
        durumSakla();
        if (!kok) {
            kok = e('div', { id: 'cu-icra', role: 'region', 'aria-label': 'İcra Takip Süreçleri' });
            document.body.appendChild(kok);
            ciz();
            yukle();
        }
        kok.style.display = '';
        if (one && kartAcikMi() && window.__cuGorunurKartiKucult) window.__cuGorunurKartiKucult();
        const m = document.getElementById('cu-menu-icra');
        if (m) m.classList.add('on');
        yerlestir();
        gorevGuncelle(true);
    }

    // Görev çubuğuna küçült: sayfa silinmez, yalnız gizlenir (durum yerinde kalır).
    function kucult() {
        if (gorunum === 'kapali') return;
        gorunum = 'kucuk';
        durumSakla();
        icradanAcilan.clear();                   // kullanıcı kendisi indirdi: kart kapanınca geri getirme
        if (kok) kok.style.display = 'none';
        const m = document.getElementById('cu-menu-icra');
        if (m) m.classList.remove('on');
        gorevGuncelle(true);
    }

    function kapat() {
        gorunum = 'kapali';
        durumSakla();
        icradanAcilan.clear();
        if (kok) kok.remove();
        kok = null; govde = null;
        const m = document.getElementById('cu-menu-icra');
        if (m) m.classList.remove('on');
        gorevGuncelle(true);
    }

    // ── Görev çubuğu düğmesi ──
    // "on" (vurgulu): sayfa açık ve üstünde dosya kartı yok. Tıklama: öndeyse küçült, değilse öne getir.
    let gorevImza = null;
    function onde() { return gorunum === 'acik' && !kartAcikMi(); }
    function gorevTikla() { if (onde()) kucult(); else ac(true); }
    function gorevGuncelle(zorla) {
        if (!window.CU || !window.CU.gorev) return;
        const on = onde();
        const imza = gorunum + '|' + on;
        if (!zorla && imza === gorevImza) return;
        gorevImza = imza;
        if (gorunum === 'kapali') { window.CU.gorev.ayarla([], 'icra'); return; }
        window.CU.gorev.ayarla([{
            kimlik: 'icra', ad: 'İcra Takip Süreçleri', ikon: '📋', on: on, kucuk: !on,
            tikla: gorevTikla, kapat: kapat,
            menu: function() { return [[[onde() ? 'Küçült' : 'Öne getir', gorevTikla]]]; },
        }], 'icra');
    }

    function ciz() {
        if (!kok) return;
        bos(kok);
        const durum = e('div', { sinif: 'i-durum', id: 'cu-icra-durum' });
        kok.appendChild(e('div', { sinif: 'i-bas' }, [
            e('h1', { metin: 'İcra Takip Süreçleri' }), durum, e('div', { sinif: 'i-bosluk' }),
            e('button', { sinif: 'i-d', metin: '–', title: 'Görev çubuğuna küçült (kaldığınız yer korunur)', 'aria-label': 'Küçült',
                          stil: 'min-width:38px;font-weight:700', onclick: kucult }),
            e('button', { sinif: 'i-d', metin: 'Kapat', title: 'Sayfayı kapat (görev çubuğundan da çıkar)', onclick: kapat })
        ]));
        const sekmeler = [['surec', 'Süreçler'], ['sure', 'Kesinleşme Süreleri'], ['tatil', 'Resmî Tatiller']];
        kok.appendChild(e('div', { sinif: 'i-sekmeler', role: 'tablist' }, sekmeler.map(function(s) {
            return e('button', { sinif: d.sekme === s[0] ? 'on' : '', role: 'tab', metin: s[1],
                                 onclick: function() { d.sekme = s[0]; ciz(); yukle(); } });
        })));
        govde = e('div', { sinif: 'i-govde' });
        kok.appendChild(govde);
        durumYaz();
    }

    function durumYaz() {
        const el = document.getElementById('cu-icra-durum');
        if (!el) return;
        const o = d.ozet;
        el.textContent = o ? (o.borclu + ' borçlu · ' + o.acik_icra_dosya + ' açık icra dosyası'
            + (o.son_guncelleme ? ' · son güncelleme ' + gunMetni(o.son_guncelleme) + o.son_guncelleme.slice(10) : '')) : '';
    }

    async function yukle() {
        if (!govde) return;
        d.hata = '';
        try {
            if (d.sekme === 'surec') {
                const sorgu = new URLSearchParams({ q: d.filtre.q, asama: d.filtre.asama, acik: d.filtre.acik ? '1' : '',
                                                    sayfa: String(d.filtre.sayfa), boyut: '50' });
                const sonuc = await Promise.all([istek('ozet'), istek('borclular?' + sorgu.toString())]);
                d.ozet = sonuc[0]; d.liste = sonuc[1];
                durumYaz();
                surecCiz();
            } else if (d.sekme === 'sure') {
                sureCiz(await istek('kurallar'));
            } else {
                tatilCiz(await istek('tatiller'));
            }
        } catch (x) {
            d.hata = x.message;
            bos(govde).appendChild(e('div', { sinif: 'i-ana' }, [e('div', { sinif: 'i-hata', metin: d.hata }),
                e('button', { sinif: 'i-d', metin: 'Yeniden dene', onclick: yukle })]));
        }
    }

    function chip(kod, tarih) {
        return e('span', {}, [e('span', { sinif: 'chip', stil: '--r:' + (ASAMA_RENK[kod] || '#8a877e'), metin: ASAMA_KISA[kod] || kod }),
                              tarih ? e('span', { sinif: 'tarih', metin: gunMetni(tarih) }) : null]);
    }

    // ── Süreçler ──
    function surecCiz() {
        bos(govde);
        const ana = e('div', { sinif: 'i-ana' });
        govde.appendChild(ana);
        const o = d.ozet;
        if (o && !o.icra_dosya) {
            ana.appendChild(e('div', { sinif: 'i-bilgi' }, [
                e('div', { stil: 'font-weight:600;color:var(--i-yazi);margin-bottom:4px', metin: 'Büro veritabanında henüz icra dosyası yok.' }),
                'Dosyalar ve tebligatlar UYAP taraması yapıldığında burada görünecek; takip aşaması, kesinleşme ve tebliğ durumu o zaman hesaplanır.'
            ]));
            return;
        }
        ana.appendChild(e('div', { sinif: 'i-asamalar' }, (o ? o.asamalar : []).map(function(a) {
            return e('button', {
                sinif: 'i-asama' + (d.filtre.asama === a.kod && a.kod ? ' on' : ''), title: a.etiket,
                onclick: function() { d.filtre.asama = d.filtre.asama === a.kod ? '' : a.kod; d.filtre.sayfa = 1; yukle(); }
            }, [e('div', { sinif: 'n', metin: String(a.adet) }), e('div', { sinif: 't', metin: ASAMA_KISA[a.kod] || a.etiket }),
                e('i', { stil: 'background:' + (ASAMA_RENK[a.kod] || '#8a877e') })]);
        })));

        const ara = e('input', { type: 'search', placeholder: 'Dosya no, birim, borçlu ya da alacaklı ara', value: d.filtre.q,
            oninput: function() {
                clearTimeout(araZaman);
                araZaman = setTimeout(function() { d.filtre.q = ara.value; d.filtre.sayfa = 1; yukle(); }, 300);
            } });
        const kutu = e('input', { type: 'checkbox' });
        kutu.checked = d.filtre.acik;
        kutu.addEventListener('change', function() { d.filtre.acik = kutu.checked; d.filtre.sayfa = 1; yukle(); });
        const hesapla = e('button', { sinif: 'i-d', metin: 'Yeniden hesapla',
            title: 'Kesinleşme, tebliğ durumu ve takip aşamasını kayıtlı tebligatlardan yeniden hesaplar (UYAP\'a gitmez)',
            onclick: async function() {
                hesapla.disabled = true;
                try { const s = await istek('hesapla', {}); bildir(s.guncellenen ? s.guncellenen + ' borçlunun durumu güncellendi.' : 'Değişen durum yok.'); yukle(); }
                catch (x) { bildir(x.message); hesapla.disabled = false; }
            } });
        ana.appendChild(e('div', { sinif: 'i-arac' }, [ara, e('label', { sinif: 'i-onay' }, [kutu, 'Yalnız açık dosyalar']),
            e('div', { sinif: 'i-bosluk' }), hesapla]));
        if (d.filtre.q && document.activeElement !== ara) setTimeout(function() {
            ara.focus(); ara.setSelectionRange(ara.value.length, ara.value.length);
        }, 0);

        const l = d.liste;
        if (!l || !l.satirlar.length) {
            ana.appendChild(e('div', { sinif: 'i-bilgi', metin: 'Bu ölçütlere uyan borçlu yok.' }));
        } else {
            const tablo = e('table', { sinif: 'liste' }, [
                e('thead', {}, [e('tr', {}, ['Dosya', 'Borçlu', 'Alacaklı', 'Takip aşaması', 'Kesinleşme', 'Tebliğ'].map(function(b) {
                    return e('th', { metin: b });
                }))]),
                e('tbody', {}, l.satirlar.map(function(s) {
                    return e('tr', { sinif: 'tik' + (d.secili && d.secili.id === s.dosya ? ' sec' : ''),
                                     onclick: function() { dosyaAc(s.dosya); } }, [
                        e('td', {}, [e('div', { sinif: 'dno', metin: s.dosya_no }), e('div', { sinif: 'alt', metin: s.birim })]),
                        e('td', {}, [s.borclu, s.uyapta_gorulmedi ? e('div', { sinif: 'uyari', metin: 'UYAP\'ta görülmedi' }) : null]),
                        e('td', { metin: s.alacakli || '—' }),
                        e('td', {}, [chip(s.asama, s.asama_tarihi)]),
                        e('td', { metin: s.kesinlesme || '—' }),
                        e('td', { metin: s.tebligat || '—' }),
                    ]);
                }))
            ]);
            ana.appendChild(tablo);
            const son = Math.max(1, Math.ceil(l.toplam / l.boyut));
            const git = function(n) { return function() { d.filtre.sayfa = n; yukle(); }; };
            ana.appendChild(e('div', { sinif: 'i-sayfa' }, [
                e('span', { metin: l.toplam + ' borçlu · ' + ((l.sayfa - 1) * l.boyut + 1) + '–' + Math.min(l.toplam, l.sayfa * l.boyut) + ' gösteriliyor' }),
                son > 1 ? e('span', {}, [
                    e('button', { sinif: 'i-d kucuk', metin: '‹ Önceki', disabled: l.sayfa <= 1, onclick: git(l.sayfa - 1) }), ' ',
                    l.sayfa + ' / ' + son, ' ',
                    e('button', { sinif: 'i-d kucuk', metin: 'Sonraki ›', disabled: l.sayfa >= son, onclick: git(l.sayfa + 1) })
                ]) : null
            ]));
        }
        if (d.secili) panelCiz();
    }

    // ── Dosya paneli ──
    async function dosyaAc(id) {
        try {
            const v = await istek('dosya?id=' + id);
            const g = await istek('gecmis?dosya=' + id);
            d.secili = { id: id, veri: v, gecmis: g };
            surecCiz();
        } catch (x) { bildir(x.message); }
    }
    async function seciliyiTazele() {
        if (!d.secili) return;
        const id = d.secili.id;
        await dosyaAc(id);
        try {
            const sorgu = new URLSearchParams({ q: d.filtre.q, asama: d.filtre.asama, acik: d.filtre.acik ? '1' : '', sayfa: String(d.filtre.sayfa), boyut: '50' });
            const sonuc = await Promise.all([istek('ozet'), istek('borclular?' + sorgu.toString())]);
            d.ozet = sonuc[0]; d.liste = sonuc[1];
            durumYaz(); surecCiz();
        } catch (x) {}
    }

    async function uyaptaAc(kart) {
        if (!window.__uyapTazeKart || !window.__uyapKartiAcDogrudan) { bildir('Dosya kartı bu sayfadan açılamıyor.'); return; }
        // Sayfa kapanmaz: kart üstünde açılır, kart küçültülünce / kapatılınca kullanıcı buraya döner.
        if (!document.getElementById('searchId')) {
            history.pushState({}, '', '/');
            window.dispatchEvent(new PopStateEvent('popstate', { state: {} }));
            const bas = Date.now();
            while (!document.getElementById('searchId') && Date.now() - bas < 8000) await new Promise(function(r) { setTimeout(r, 150); });
            if (!document.getElementById('searchId')) { bildir('UYAP ana sayfası açılamadı.'); return; }
        }
        bildir('Dosya UYAP\'ta aranıyor…');
        const t = await window.__uyapTazeKart(kart).catch(function() { return null; });
        if (!t || !t.dosyaId) { bildir('Dosya UYAP\'ta bulunamadı: ' + kart.birimAdi + ' ' + kart.dosyaNo); return; }
        if (window.__uyapDosyaAnahtari) icradanAcilan.add(window.__uyapDosyaAnahtari(t));
        if (!window.__uyapKartiAcDogrudan(t)) bildir('Dosya kartı açılamadı (UYAP sayfası değişmiş olabilir).');
    }

    function panelCiz() {
        const s = d.secili, v = s.veri, k = v.kunye, t = v.takip;
        const panel = e('div', { sinif: 'i-panel' });
        const rozetler = [k.acik ? 'UYAP: Açık' : 'UYAP: ' + (k.durum || 'Kapalı'), 'İşlem: ' + k.islem_durumu_etiket];
        if (t.okundu) rozetler.push('Takip kodu ' + [t.turu, t.yolu, t.sekli].map(function(x) { return x || '—'; }).join(' / '));
        rozetler.push(t.itiraz_suresi_gun ? 'İtiraz süresi ' + t.itiraz_suresi_gun + ' gün' : 'Kesinleşme süresi tanımlı değil');
        panel.appendChild(e('div', { sinif: 'p-bas' }, [
            e('div', { stil: 'display:flex;justify-content:space-between;gap:10px;align-items:flex-start' }, [
                e('div', {}, [e('h2', { metin: k.dosya_no }), e('div', { sinif: 'alt', metin: k.birim + (k.tur ? ' · ' + k.tur : '') + (k.acilis_tarihi ? ' · Açılış ' + gunMetni(k.acilis_tarihi) : '') })]),
                e('div', { stil: 'display:flex;gap:6px' }, [
                    e('button', { sinif: 'i-d kucuk', metin: 'UYAP\'ta aç', title: 'Dosyayı UYAP\'ta bulup kartını açar', onclick: function() { uyaptaAc(k.uyap); } }),
                    e('button', { sinif: 'i-d kucuk', metin: '✕', title: 'Paneli kapat', 'aria-label': 'Paneli kapat', onclick: function() { d.secili = null; surecCiz(); } })
                ])
            ]),
            e('div', { sinif: 'i-rozet' }, rozetler.map(function(r) { return e('span', { metin: r }); })),
            e('div', { sinif: 'i-sekmeler', stil: 'padding:0;margin:0 -18px;padding-left:18px' }, [['borclular', 'Borçlular'], ['tebligat', 'Tebligatlar (' + v.tebligatlar.length + ')'], ['gecmis', 'Geçmiş'], ['kunye', 'Dosya']].map(function(x) {
                return e('button', { sinif: d.panelSekme === x[0] ? 'on' : '', metin: x[1], onclick: function() { d.panelSekme = x[0]; surecCiz(); } });
            }))
        ]));
        const ic = e('div', { sinif: 'p-ic' });
        panel.appendChild(ic);
        if (d.panelSekme === 'borclular') borclularCiz(ic, v);
        else if (d.panelSekme === 'tebligat') tebligatlarCiz(ic, v);
        else if (d.panelSekme === 'gecmis') gecmisCiz(ic, s.gecmis);
        else kunyeCiz(ic, v);
        govde.appendChild(panel);
    }

    function borclularCiz(ic, v) {
        const borclular = v.taraflar.filter(function(x) { return x.rol === 'borclu'; });
        const digerleri = v.taraflar.filter(function(x) { return x.rol !== 'borclu'; });
        if (!borclular.length) ic.appendChild(e('div', { sinif: 'i-bilgi', metin: 'Dosyada kayıtlı borçlu yok.' }));
        borclular.forEach(function(b) {
            const adim = SIRA.indexOf(b.asama);
            const sonuc = { iade: 5, itiraz: 6, durduruldu: 6 }[b.asama];
            const dolu = adim >= 0 ? adim + 1 : (sonuc || 0);
            const tarih = e('input', { type: 'date', value: b.itiraz_tarihi || '' });
            const elle = e('select', { title: 'Elle girilen kesinleşme değeri (hesap ezmez)' }, [
                ['', 'Elle değer yok'], ['konkordato', 'Konkordato'], ['vefat', 'Vefat'], ['vasi', 'Vasi']
            ].map(function(o) { const op = e('option', { value: o[0], metin: o[1] }); if (b.kesinlesme_durumu === o[0]) op.selected = true; return op; }));
            if (['konkordato', 'vefat', 'vasi'].indexOf(b.kesinlesme_durumu) < 0) elle.value = '';
            elle.addEventListener('change', async function() {
                try { await istek('kesinlesme_elle', { bag: b.bag, durum: elle.value }); bildir('Kaydedildi.'); seciliyiTazele(); }
                catch (x) { bildir(x.message); }
            });
            const itirazDugme = e('button', { sinif: 'i-d kucuk', metin: b.itiraz ? 'İtirazı kaldır' : 'İtiraz edildi',
                onclick: async function() {
                    try { await istek('itiraz', { bag: b.bag, edildi: !b.itiraz, tarih: tarih.value || null }); bildir('Kaydedildi.'); seciliyiTazele(); }
                    catch (x) { bildir(x.message); }
                } });
            ic.appendChild(e('div', { sinif: 'i-kart' }, [
                e('div', { sinif: 'b' }, [
                    e('div', {}, [e('div', { sinif: 'ad', metin: b.ad }), e('div', { sinif: 'alt', metin: 'Borçlu' + (b.kimlik ? ' · ' + b.kimlik : '') }),
                                  b.uyapta_gorulmedi ? e('div', { sinif: 'uyari', metin: 'UYAP\'ın taraf listesinde artık görünmüyor' }) : null]),
                    chip(b.asama, null)
                ]),
                e('div', { sinif: 'i-ilerleme', title: 'Takip süreci' }, [0, 1, 2, 3, 4, 5, 6].map(function(i) { return e('i', { sinif: i < dolu ? 'd' : '' }); })),
                e('div', { sinif: 'sat' }, [e('span', { metin: 'Takip aşaması' }), e('b', { metin: b.asama_metni || '—' })]),
                e('div', { sinif: 'sat' }, [e('span', { metin: 'Tebliğ' }), e('b', { metin: b.tebligat || '—' })]),
                e('div', { sinif: 'sat' }, [e('span', { metin: 'Kesinleşme' }), e('b', { metin: b.kesinlesme || '—' })]),
                e('div', { sinif: 'i-eylem' }, [b.itiraz ? null : tarih, itirazDugme, elle])
            ]));
        });
        if (digerleri.length) {
            ic.appendChild(e('div', { sinif: 'bolum', metin: 'Diğer taraflar' }));
            digerleri.forEach(function(x) {
                ic.appendChild(e('div', { sinif: 'i-kart' }, [e('div', { sinif: 'ad', metin: x.ad }),
                    e('div', { sinif: 'alt', metin: x.rol_etiket + (x.vekiller ? ' · Vekil: ' + x.vekiller : '') })]));
            });
        }
    }

    function tebligatlarCiz(ic, v) {
        if (!v.tebligatlar.length) { ic.appendChild(e('div', { sinif: 'i-bilgi', metin: 'Bu dosyada kayıtlı tebligat yok.' })); return; }
        v.tebligatlar.forEach(function(t) {
            const hareket = e('div');
            const kart = e('div', { sinif: 'i-kart' }, [
                e('div', { sinif: 'b' }, [e('div', { sinif: 'ad', metin: t.tur || 'Tebligat' }), e('span', { sinif: 'alt', metin: t.evrak_tarihi })]),
                e('div', { sinif: 'alt', metin: t.muhatap || t.aciklama }),
                e('div', { sinif: 'sat' }, [e('span', { metin: t.elektronik ? 'E-tebligat' : 'Barkod' }), e('b', { metin: t.barkod || 'henüz yok' })]),
                e('div', { sinif: 'sat' }, [e('span', { metin: 'Durum' }), e('b', { metin: (t.ptt_durumu || '—') + (t.son_islem_tarihi ? ' · ' + t.son_islem_tarihi : '') })]),
                t.teslim_sekli ? e('div', { sinif: 'sat' }, [e('span', { metin: 'Teslim / iade' }), e('b', { metin: t.teslim_sekli })]) : null,
                e('div', { sinif: 'sat' }, [e('span', { metin: 'Mazbata' }), e('b', { metin: t.mazbata_var ? (t.mazbata || 'Dosyaya döndü') : 'Dönmedi' })]),
                t.borclu ? e('div', { sinif: 'sat' }, [e('span', { metin: 'Borçlu' }), e('b', { metin: t.borclu })]) : null,
                t.yeniden_teblig_talebi ? e('div', { sinif: 'alt', metin: 'Yeniden tebliğ talebi gönderildi' }) : null,
                t.hareket_sayisi ? e('div', { sinif: 'i-eylem' }, [e('button', { sinif: 'i-d kucuk', metin: 'PTT hareketleri (' + t.hareket_sayisi + ')',
                    onclick: async function() {
                        if (hareket.firstChild) { bos(hareket); return; }
                        try {
                            const h = await istek('ptt?tebligat=' + t.id);
                            h.forEach(function(x) {
                                hareket.appendChild(e('div', { sinif: 'sat' }, [e('span', { metin: (x.zaman ? gunMetni(x.zaman) + x.zaman.slice(10) : '') }),
                                    e('b', { metin: x.aciklama + (x.yer ? ' · ' + x.yer : '') + (x.detay ? ' · ' + x.detay : '') })]));
                            });
                        } catch (x) { bildir(x.message); }
                    } })]) : null,
                hareket
            ]);
            ic.appendChild(kart);
        });
    }

    function gecmisCiz(ic, g) {
        if (!g.length) { ic.appendChild(e('div', { sinif: 'i-bilgi', metin: 'Henüz kayıtlı durum değişikliği yok.' })); return; }
        g.forEach(function(o) {
            ic.appendChild(e('div', { sinif: 'i-olay' }, [
                e('div', { sinif: 'alt', metin: gunMetni(o.zaman) + o.zaman.slice(10) + ' · ' + o.tur_etiket + ' · ' + o.kaynak_etiket }),
                o.taraf ? e('div', { stil: 'font-weight:600', metin: o.taraf }) : null,
                e('div', {}, [o.eski ? e('span', { sinif: 'alt', metin: o.eski + '  →  ' }) : null, o.yeni || '—']),
                o.aciklama ? e('div', { sinif: 'alt', metin: o.aciklama }) : null
            ]));
        });
    }

    function kunyeCiz(ic, v) {
        const k = v.kunye, t = v.takip;
        const satir = function(a, b) { return e('div', { sinif: 'sat' }, [e('span', { metin: a }), e('b', { metin: b || '—' })]); };
        ic.appendChild(e('div', { sinif: 'i-kart' }, [
            satir('Birim', k.birim), satir('Dosya no', k.dosya_no), satir('Dosya türü', k.tur), satir('UYAP durumu', k.durum),
            satir('Açılış', gunMetni(k.acilis_tarihi)), satir('Son güncelleme', k.son_guncelleme)
        ]));
        ic.appendChild(e('div', { sinif: 'bolum', metin: 'Takip' }));
        ic.appendChild(e('div', { sinif: 'i-kart' }, t.okundu ? [
            satir('Takip kodu (tür / yol / şekil)', [t.turu, t.yolu, t.sekli].map(function(x) { return x || '—'; }).join(' / ')),
            satir('Uygulanan süre', t.itiraz_suresi_gun ? t.itiraz_suresi_gun + ' gün' + (t.kural ? ' (' + t.kural + ')' : '') : 'Tanımlı değil'),
            satir('Alacak toplamı', t.alacak_toplam), satir('Vekalet ücreti', t.vekalet_ucreti), satir('Tahsil harcı', t.tahsil_harci)
        ] : [e('div', { sinif: 'alt', metin: 'Takip türü henüz UYAP\'tan okunmadı.' })]));
        // İşlem durumu (büronun kendi işareti)
        const sec = e('select', {}, [['aktif', 'Aktif - işlem yapılabilir'], ['pasif', 'Pasif - işlem yapma'], ['beklemede', 'Beklemede']].map(function(o) {
            const op = e('option', { value: o[0], metin: o[1] }); if (k.islem_durumu === o[0]) op.selected = true; return op;
        }));
        const not = e('input', { type: 'text', placeholder: 'Not / gerekçe', value: k.islem_durumu_notu || '', stil: 'flex:1;min-width:140px' });
        const bitis = e('input', { type: 'date', value: k.bekleme_bitis || '', title: 'Bekleme bitişi (yalnız Beklemede)' });
        ic.appendChild(e('div', { sinif: 'bolum', metin: 'İşlem durumu' }));
        ic.appendChild(e('div', { sinif: 'i-form' }, [sec, bitis, not, e('button', { sinif: 'i-d ana', metin: 'Kaydet', onclick: async function() {
            try { await istek('islem_durumu', { dosya: k.id, durum: sec.value, notu: not.value, bekleme_bitis: bitis.value || null }); bildir('Kaydedildi.'); seciliyiTazele(); }
            catch (x) { bildir(x.message); }
        } })]));
    }

    // ── Kesinleşme süreleri ──
    function sureCiz(v) {
        bos(govde);
        const ana = e('div', { sinif: 'i-ana' });
        govde.appendChild(ana);
        const kutu = e('div', { sinif: 'i-kutu' }, [e('h3', { metin: 'Kesinleşme süreleri' }),
            e('p', { metin: 'İtiraz süresi takip türüne göre girilir; boş kod "hepsi" demektir, üç kodu boş olan satır varsayılandır. Bir dosyaya en çok kodu tutan süre uygulanır. Süre değişince bütün borçlular yeniden hesaplanır.' })]);
        ana.appendChild(kutu);
        let duzenlenen = null;
        const form = e('div');
        function formCiz(k) {
            bos(form);
            duzenlenen = k ? k.id : null;
            const alan = function(ph, deger, gen) { return e('input', { type: 'text', placeholder: ph, value: deger || '', stil: 'width:' + gen }); };
            const tur = alan('Tür kodu', k && k.takibin_turu, '86px'), yol = alan('Yol kodu', k && k.takibin_yolu, '86px'), sek = alan('Şekil kodu', k && k.takibin_sekli, '92px');
            const ad = alan('Ad (ör. İlamsız — Genel haciz)', k && k.ad, '230px');
            const gun = e('input', { type: 'number', min: '1', max: '365', placeholder: 'Gün (boş: hesaplanmaz)', value: k && k.gun != null ? String(k.gun) : '', stil: 'width:170px' });
            const aktif = e('input', { type: 'checkbox' }); aktif.checked = !k || k.aktif;
            form.appendChild(e('div', { sinif: 'i-form' }, [tur, yol, sek, ad, gun, e('label', { sinif: 'i-onay' }, [aktif, 'Etkin']),
                e('button', { sinif: 'i-d ana', metin: k ? 'Kaydet' : 'Ekle', onclick: async function() {
                    try {
                        const s = await istek('kural', { id: duzenlenen, takibin_turu: tur.value, takibin_yolu: yol.value, takibin_sekli: sek.value,
                                                         ad: ad.value, gun: gun.value ? Number(gun.value) : null, aktif: aktif.checked });
                        bildir('Kaydedildi' + (s.guncellenen ? '; ' + s.guncellenen + ' borçlunun durumu güncellendi.' : '.'));
                        yukle();
                    } catch (x) { bildir(x.message); }
                } }),
                k ? e('button', { sinif: 'i-d', metin: 'Vazgeç', onclick: function() { formCiz(null); } }) : null]));
        }
        if (!v.kurallar.length) kutu.appendChild(e('div', { sinif: 'i-sarı', metin: 'Henüz süre girilmedi: hiçbir dosyada kesinleşme hesaplanmaz. Önce varsayılan süreyi (üç kod boş) ekleyin.' }));
        else {
            kutu.appendChild(e('table', {}, [
                e('thead', {}, [e('tr', {}, ['Ad', 'Kod (tür / yol / şekil)', 'Süre', ''].map(function(b) { return e('th', { metin: b }); }))]),
                e('tbody', {}, v.kurallar.map(function(k) {
                    const sil = e('button', { sinif: 'i-d kucuk tehlike', metin: 'Sil' });
                    sil.addEventListener('click', async function() {
                        if (!sil.dataset.onay) { sil.dataset.onay = '1'; sil.textContent = 'Emin misiniz?'; setTimeout(function() { delete sil.dataset.onay; sil.textContent = 'Sil'; }, 4000); return; }
                        try { await istek('kural_sil', { id: k.id }); bildir('Silindi.'); yukle(); } catch (x) { bildir(x.message); }
                    });
                    const kod = [k.takibin_turu, k.takibin_yolu, k.takibin_sekli].map(function(x) { return x || '—'; }).join(' / ');
                    return e('tr', {}, [
                        e('td', {}, [k.ad || (kod === '— / — / —' ? 'Varsayılan' : ''), k.aktif ? null : e('span', { sinif: 'alt', metin: ' (devre dışı)' })]),
                        e('td', { metin: kod }),
                        e('td', {}, [e('span', { sinif: 'gun', metin: k.gun ? k.gun + ' gün' : '—' })]),
                        e('td', { stil: 'text-align:right;white-space:nowrap' }, [e('button', { sinif: 'i-d kucuk', metin: 'Düzenle', onclick: function() { formCiz(k); } }), ' ', sil])
                    ]);
                }))
            ]));
        }
        kutu.appendChild(form);
        formCiz(null);
        // Veritabanındaki takip kodları: kural girerken hangi kodun kaç dosyada olduğu
        const dag = e('div', { sinif: 'i-kutu', stil: 'margin-top:14px' }, [e('h3', { metin: 'Veritabanındaki takip kodları' }),
            e('p', { metin: 'İcra dosyalarının UYAP\'tan okunan takip kodları ve dosya sayıları. Bir koda süre girmek için "Süre gir"e basın.' })]);
        if (!v.dagilim.length) dag.appendChild(e('div', { sinif: 'alt', metin: 'Henüz takip türü okunmuş dosya yok.' }));
        else dag.appendChild(e('table', {}, [
            e('thead', {}, [e('tr', {}, ['Kod (tür / yol / şekil)', 'Dosya', 'Örnek', ''].map(function(b) { return e('th', { metin: b }); }))]),
            e('tbody', {}, v.dagilim.map(function(x) {
                return e('tr', {}, [e('td', { metin: [x.turu, x.yolu, x.sekli].map(function(y) { return y || '—'; }).join(' / ') }),
                    e('td', { metin: String(x.adet) }), e('td', { sinif: 'alt', metin: x.ornek || '' }),
                    e('td', { stil: 'text-align:right' }, [e('button', { sinif: 'i-d kucuk', metin: 'Süre gir', onclick: function() {
                        formCiz({ id: null, takibin_turu: x.turu, takibin_yolu: x.yolu, takibin_sekli: x.sekli, ad: '', gun: null, aktif: true });
                        form.scrollIntoView({ block: 'center' });
                    } })])]);
            }))
        ]));
        if (v.takip_turu_bilinmeyen) dag.appendChild(e('div', { sinif: 'alt', stil: 'margin-top:8px', metin: 'Takip türü henüz okunmamış icra dosyası: ' + v.takip_turu_bilinmeyen + ' (bunlara varsayılan süre uygulanır).' }));
        ana.appendChild(dag);
    }

    // ── Resmî tatiller ──
    function tatilCiz(liste) {
        bos(govde);
        const ana = e('div', { sinif: 'i-ana' });
        govde.appendChild(ana);
        const kutu = e('div', { sinif: 'i-kutu', stil: 'max-width:640px' }, [e('h3', { metin: 'Resmî tatiller' }),
            e('p', { metin: 'Kesinleşme süresinin son günü hafta sonuna ya da buradaki bir güne denk gelirse ilk iş gününe kayar. Dinî bayramlar her yıl Diyanet ilanına göre girilmeli. Değişince bütün borçlular yeniden hesaplanır.' })]);
        ana.appendChild(kutu);
        const tarih = e('input', { type: 'date' });
        const aciklama = e('input', { type: 'text', placeholder: 'Açıklama (ör. Cumhuriyet Bayramı)', stil: 'flex:1;min-width:160px' });
        kutu.appendChild(e('div', { sinif: 'i-form' }, [tarih, aciklama, e('button', { sinif: 'i-d ana', metin: 'Ekle', onclick: async function() {
            if (!tarih.value) { bildir('Tarih seçin.'); return; }
            try { await istek('tatil', { tarih: tarih.value, aciklama: aciklama.value }); bildir('Eklendi.'); yukle(); } catch (x) { bildir(x.message); }
        } })]));
        if (!liste.length) { kutu.appendChild(e('div', { sinif: 'alt', metin: 'Kayıtlı tatil yok.' })); return; }
        kutu.appendChild(e('table', {}, [e('tbody', {}, liste.map(function(t) {
            const sil = e('button', { sinif: 'i-d kucuk tehlike', metin: 'Sil', onclick: async function() {
                try { await istek('tatil_sil', { tarih: t.tarih }); bildir('Silindi.'); yukle(); } catch (x) { bildir(x.message); }
            } });
            return e('tr', {}, [e('td', { metin: gunMetni(t.tarih), stil: 'white-space:nowrap' }), e('td', { metin: t.aciklama || '' }),
                                e('td', { stil: 'text-align:right' }, [sil])]);
        }))]));
    }

    // ── Bağlantılar ──
    // Menüde başka bir bağlantıya basınca sayfa görev çubuğuna küçülür (UYAP'ın kendi sayfası görünür).
    document.addEventListener('click', function(ev) {
        if (gorunum !== 'acik') return;
        const a = ev.target.closest && ev.target.closest('#av-sidebar-wrapper a, #av-sidebar-wrapper button.sideLink');
        if (a && !a.closest('#cu-menu-icra') && !a.closest('.fav-menu-icon')) kucult();
    }, true);
    document.addEventListener('keydown', function(ev) {
        if (onde() && ev.key === 'Escape' && d.secili && !ev.target.closest('input,select')) { d.secili = null; surecCiz(); }
    });
    // Bu sayfadan açılan dosya kartı küçültüldü / kapatıldı: sayfa küçükse yeniden öne gelir (açıksa zaten kartın
    // altında duruyor, kart gidince görünür).
    function kartDinle() {
        if (!window.__cuKartKapaninca) return false;
        window.__cuKartKapaninca(function(anahtar) {
            if (!icradanAcilan.has(anahtar)) return;
            icradanAcilan.delete(anahtar);
            if (gorunum === 'kucuk') ac(false);
        });
        return true;
    }
    if (!kartDinle()) setTimeout(kartDinle, 1000);
    window.addEventListener('resize', yerlestir);
    setInterval(function() { menuGirisi(); yerlestir(); gorevGuncelle(false); }, 700);
    menuGirisi();
    try {
        const v = sessionStorage.getItem(ACIK_ANAHTAR);
        if (v === '1') setTimeout(function() { ac(false); }, 300);
        else if (v === 'k') { gorunum = 'kucuk'; setTimeout(function() { gorevGuncelle(true); }, 300); }
    } catch (x) {}
    window.__cuIcraTakipAc = function() { ac(true); };
})();
