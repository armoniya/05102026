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
//
// Liste (kullanıcı isteği 16.11.2026): her borçlunun BÜTÜN tebligatları (barkodları) satırında görünür. Kesinleşme,
// tebligat türü, tebliğ durumu ve mazbata başlıklarında "yaz ya da seç" kutusu var (UyapMasa'daki başlık kutuları):
// yazınca içinde geçenleri, listeden seçince tam o değeri süzer. Seçenekler adetleriyle sunucudan gelir ve diğer
// süzgeçlere göre daralır (boş sonuç veren seçenek görünmez). Tür + tebliğ + mazbata aynı tebligatta birlikte aranır.
// Seçili süzgeçler listenin üstünde etiket olarak durur; tek tıkla kalkar.
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
            '#cu-icra tbody.tik{cursor:pointer}#cu-icra tbody.tik:hover td{background:var(--i-serit)}',
            '#cu-icra tbody.sec td{background:var(--i-vurgu-zemin)!important}',
            // Başlıktaki "yaz ya da seç" kutusu + açılır seçenek listesi
            '#cu-icra table.liste th{vertical-align:top}#cu-icra th.suz{min-width:150px}',
            '#cu-icra .i-kutu-s{position:relative;display:flex;margin-top:5px}',
            '#cu-icra .i-kutu-s input{flex:1;min-width:0;width:100%;font-weight:400;font-size:12.5px;padding:5px 44px 5px 8px;border-radius:8px;text-overflow:ellipsis}',
            '#cu-icra .i-kutu-s.dolu input{border-color:var(--i-vurgu);background:var(--i-vurgu-zemin);color:var(--i-vurgu-yazi);font-weight:600}',
            '#cu-icra .i-kutu-s button{position:absolute;top:1px;bottom:1px;width:20px;border:0;background:none;color:var(--i-silik);cursor:pointer;padding:0;font-size:11px}',
            '#cu-icra .i-kutu-s .ok{right:3px}#cu-icra .i-kutu-s .sil{right:22px;font-size:13px}',
            '#cu-icra .i-kutu-s button:hover{color:var(--i-yazi)}',
            '#cu-icra .i-acilir{position:fixed;z-index:5;background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:10px;box-shadow:0 10px 28px rgba(0,0,0,.18);',
            'max-height:320px;overflow:auto;padding:4px;min-width:220px;max-width:min(460px,calc(100vw - 16px));font-size:13px}',
            '#cu-icra .i-acilir .sec{display:flex;justify-content:space-between;gap:14px;padding:6px 9px;border-radius:7px;cursor:pointer;align-items:baseline}',
            '#cu-icra .i-acilir .sec:hover,#cu-icra .i-acilir .sec.imlec{background:var(--i-vurgu-zemin)}',
            '#cu-icra .i-acilir .sec.on{font-weight:600;color:var(--i-vurgu-koyu)}',
            '#cu-icra .i-acilir .sec .kt{flex:none;width:15px;height:15px;border:1.5px solid var(--i-kenar);border-radius:4px;align-self:center;display:flex;align-items:center;justify-content:center;font-size:11px;line-height:1;color:#fff}',
            '#cu-icra .i-acilir .sec.on .kt{background:var(--i-vurgu-koyu);border-color:var(--i-vurgu-koyu)}',
            '#cu-icra .i-acilir .sec .ad{flex:1;min-width:0}',
            '#cu-icra .i-acilir .sec span:last-child{color:var(--i-silik);font-size:12px;white-space:nowrap;font-weight:400}',
            '#cu-icra .i-acilir .yok{padding:8px 9px;color:var(--i-silik)}',
            '#cu-icra .i-secili{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:-2px 0 12px;font-size:12.5px;color:var(--i-soluk)}',
            '#cu-icra .i-secili .et{display:inline-flex;gap:6px;align-items:center;background:var(--i-vurgu-zemin);color:var(--i-vurgu-yazi);border:0;border-radius:999px;padding:3px 6px 3px 11px;cursor:pointer;max-width:100%}',
            '#cu-icra .i-secili .et b{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:280px}',
            '#cu-icra .i-secili .et i{font-style:normal;opacity:.7;padding:0 4px}',
            '#cu-icra .i-suzgecler{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}',
            '#cu-icra .i-suzgecler .i-kutu-s{flex:1 1 150px;margin:0}',
            '#cu-icra .i-suzgecler.ust .i-kutu-s{flex:0 1 220px}',
            '#cu-icra .i-gt{background:var(--i-yuzey);border:1px solid var(--i-kenar);border-radius:12px;padding:12px 14px;margin-bottom:12px}',
            '#cu-icra .i-gt-ust{display:flex;align-items:center;gap:8px;margin-bottom:6px}',
            '#cu-icra .i-gt-form{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:8px}',
            '#cu-icra .i-gt-form select{max-width:340px}',
            '#cu-icra .i-gt-cubuk{height:8px;border-radius:5px;background:var(--i-serit);overflow:hidden;margin:8px 0 4px;border:1px solid var(--i-kenar)}',
            '#cu-icra .i-gt-cubuk i{display:block;height:100%;background:var(--i-vurgu);transition:width .4s}',
            '#cu-icra .i-gt-not{color:var(--i-soluk);font-size:12.5px}',
            '#cu-icra .i-gt-uyari{color:#a15c00;font-size:12.5px;margin-top:6px}',
            'html[data-cu-tema="koyu"] #cu-icra .i-gt-uyari{color:#f0b35a}',
            '#cu-icra .i-gt-rapor{margin:6px 0 0;padding:0;list-style:none;font-size:13px}',
            '#cu-icra .i-gt-rapor li{margin:2px 0}',
            '#cu-icra .i-gt-uyarilar{max-height:160px;overflow:auto;font-size:12px;color:var(--i-soluk);margin:4px 0 0;padding-left:18px}',
            '#cu-icra .i-gt details summary{cursor:pointer;color:var(--i-soluk);font-size:12.5px;margin-top:6px}',
            // Borçlunun tebligat satırları
            '#cu-icra td.tb{font-size:13px}',
            '#cu-icra tr.ek td{border-top:1px dashed var(--i-kenar)}',
            '#cu-icra td.tb.sol{opacity:.45}',
            '#cu-icra td.tb .uzun{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:300px}',
            '#cu-icra .mz{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12.5px;white-space:nowrap;background:color-mix(in srgb,#4f8a57 18%,var(--i-yuzey));color:color-mix(in srgb,#4f8a57 80%,var(--i-yazi))}',
            '#cu-icra .silik{color:var(--i-silik)}',
            '#cu-icra .i-dahasi{border:0;background:none;color:var(--i-vurgu-koyu);cursor:pointer;padding:0;font-size:12.5px}',
            '#cu-icra .i-dahasi:hover{text-decoration:underline}',
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
            'html.cu-dar #cu-icra table.liste tbody{display:block;border-top:1px solid var(--i-kenar);padding:6px 0}',
            'html.cu-dar #cu-icra table.liste tbody:first-of-type{border-top:0}',
            'html.cu-dar #cu-icra table.liste tr{display:block}',
            'html.cu-dar #cu-icra table.liste td{display:block;border:0!important;padding:2px 12px}',
            'html.cu-dar #cu-icra table.liste td.tb{margin-left:12px;padding-left:10px;border-left:2px solid var(--i-kenar)!important}',
            'html.cu-dar #cu-icra table.liste td.tb.tb0{margin-top:8px}',
        ].join('\n');
        document.head.appendChild(e('style', { id: 'cu-icra-stil', metin: css }));
    }

    // ── Durum ──
    const d = {
        sekme: 'surec', filtre: { q: '', asama: '', durum: 'acik', sayfa: 1, kesinlesme: '', tur: '', teblig: '', mazbata: '',
                                  birim: '', alacakli: '', borclu: '', yil: '', dosya_tur: '', acilis_bas: '', acilis_son: '' },
        ozet: null, liste: null, secili: null, panelSekme: 'borclular', hata: '',
        kutu: null, kutuMetin: '', kutuYazildi: false,   // açık başlık kutusu (anahtar), içindeki yazı
        tumTebligat: {},                                 // bag → true: borçlunun bütün tebligatları açık
        gt: null, gtAcik: false,                         // UYAP'tan güncelleme: son tarama, panel açık mı
        gtForm: { birim: '', bas: '', son: '' }          // güncelleme kapsamı (yeniden çizimde korunur)
    };
    let kok = null, govde = null, araZaman = null, kutuZaman = null;
    // Başlık süzgeçleri. kodlu: değer sabit bir koddur (yalnız listeden seçilir); değilse yazıyla "içinde geçen" de süzülür.
    // baslik: sütun başlığının adı (kutunun adından farklıysa). Sıra = tablo sütunları (Takip aşaması 3. kutudan sonra).
    const SUZGEC = [
        { k: 'birim', ad: 'İcra dairesi', baslik: 'Dosya', kodlu: false },
        { k: 'borclu', ad: 'Borçlu', kodlu: false },
        { k: 'alacakli', ad: 'Alacaklı', kodlu: false },
        { k: 'kesinlesme', ad: 'Kesinleşme', kodlu: true },
        { k: 'tur', ad: 'Tebligat türü', kodlu: false },
        { k: 'teblig', ad: 'Tebliğ durumu', kodlu: false },
        { k: 'mazbata', ad: 'Mazbata', kodlu: true }
    ];
    // Tablo üstündeki künye kutuları (sütunu olmayanlar).
    const UST_SUZGEC = [
        { k: 'yil', ad: 'Dosya yılı', kodlu: true },
        { k: 'dosya_tur', ad: 'Dosya türü', kodlu: true }
    ];
    const TUM_SUZGEC = SUZGEC.concat(UST_SUZGEC);
    const DURUMLAR = [['acik', 'Açık dosyalar'], ['kapali', 'Kapalı dosyalar'], ['', 'Bütün dosyalar']];
    function listeSorgusu() {
        const f = d.filtre;
        const s = { q: f.q, asama: f.asama, durum: f.durum, acilis_bas: f.acilis_bas, acilis_son: f.acilis_son,
                    sayfa: String(f.sayfa), boyut: '50' };
        TUM_SUZGEC.forEach(function(z) { s[z.k] = f[z.k]; });
        return new URLSearchParams(s).toString();
    }
    const TEBLIGAT_GORUNEN = 3;                  // satırda ilk gösterilen tebligat sayısı (kalanı "+N tebligat daha")
    function trKucuk(x) {
        return String(x || '').replace(/İ/g, 'i').replace(/I/g, 'ı').toLowerCase()
            .replace(/ı/g, 'i').replace(/ş/g, 's').replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ö/g, 'o').replace(/ç/g, 'c');
    }
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
            gtYokla(true);
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
        d.kutu = null; d.kutuMetin = ''; d.kutuYazildi = false;
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
                const sonuc = await Promise.all([istek('ozet'), istek('borclular?' + listeSorgusu())]);
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
                'Dosyalar ve tebligatlar UYAP taraması yapıldığında burada görünecek; takip aşaması, kesinleşme ve tebliğ durumu o zaman hesaplanır.',
                e('div', { stil: 'margin-top:10px' }, [gtDugme()])
            ]));
            if (gtGorunsun()) ana.appendChild(gtPaneli());
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
        const durumSec = e('select', { 'aria-label': 'Dosya durumu', title: 'UYAP\'taki dosya durumu' },
            DURUMLAR.map(function(x) { const o = e('option', { value: x[0], metin: x[1] }); if (d.filtre.durum === x[0]) o.selected = true; return o; }));
        durumSec.addEventListener('change', function() { d.filtre.durum = durumSec.value; d.filtre.sayfa = 1; yukle(); });
        const tarihKutusu = function(k, ad) {
            const t = e('input', { type: 'date', value: d.filtre[k], title: ad, 'aria-label': ad });
            t.addEventListener('change', function() { d.filtre[k] = t.value; d.filtre.sayfa = 1; yukle(); });
            return t;
        };
        const hesapla = e('button', { sinif: 'i-d', metin: 'Yeniden hesapla',
            title: 'Kesinleşme, tebliğ durumu ve takip aşamasını kayıtlı tebligatlardan yeniden hesaplar (UYAP\'a gitmez)',
            onclick: async function() {
                hesapla.disabled = true;
                try { const s = await istek('hesapla', {}); bildir(s.guncellenen ? s.guncellenen + ' borçlunun durumu güncellendi.' : 'Değişen durum yok.'); yukle(); }
                catch (x) { bildir(x.message); hesapla.disabled = false; }
            } });
        ana.appendChild(e('div', { sinif: 'i-arac' }, [ara, durumSec,
            e('label', { sinif: 'i-onay', title: 'Açılış tarihi aralığı (iki gün dahil)' },
              ['Açılış', tarihKutusu('acilis_bas', 'Açılış tarihi: başlangıç'), '–', tarihKutusu('acilis_son', 'Açılış tarihi: bitiş')]),
            e('div', { sinif: 'i-bosluk' }), gtDugme(), hesapla]));
        if (gtGorunsun()) ana.appendChild(gtPaneli());
        if (d.filtre.q && document.activeElement !== ara) setTimeout(function() {
            ara.focus(); ara.setSelectionRange(ara.value.length, ara.value.length);
        }, 0);

        seciliCiz(ana);
        ana.appendChild(e('div', { sinif: 'i-suzgecler' + (dar() ? '' : ' ust') }, (dar() ? TUM_SUZGEC : UST_SUZGEC).map(function(z) { return suzgecKutusu(z, true); })));

        const l = d.liste;
        const suzBaslik = function(z) { return e('th', { sinif: 'suz' }, [z.baslik || z.ad, dar() ? null : suzgecKutusu(z, false)]); };
        const basliklar = SUZGEC.slice(0, 3).map(suzBaslik).concat([e('th', { metin: 'Takip aşaması' })]).concat(SUZGEC.slice(3).map(suzBaslik));
        const tablo = e('table', { sinif: 'liste' }, [e('thead', {}, [e('tr', {}, basliklar)])]);
        if (!l || !l.satirlar.length) {
            tablo.appendChild(e('tbody', {}, [e('tr', {}, [e('td', { colspan: '8', sinif: 'silik', stil: 'text-align:center;padding:22px',
                metin: 'Bu ölçütlere uyan dosya yok.' })])]));
            ana.appendChild(tablo);
        } else {
            l.satirlar.forEach(function(s) { tablo.appendChild(borcluGovdesi(s)); });
            ana.appendChild(tablo);
            const son = Math.max(1, Math.ceil(l.toplam / l.boyut));
            const git = function(n) { return function() { d.filtre.sayfa = n; yukle(); }; };
            ana.appendChild(e('div', { sinif: 'i-sayfa' }, [
                e('span', { metin: l.toplam + ' satır (' + (l.dosya_sayisi != null ? l.dosya_sayisi : '?') + ' dosya) · '
                    + ((l.sayfa - 1) * l.boyut + 1) + '–' + Math.min(l.toplam, l.sayfa * l.boyut) + ' gösteriliyor' }),
                son > 1 ? e('span', {}, [
                    e('button', { sinif: 'i-d kucuk', metin: '‹ Önceki', disabled: l.sayfa <= 1, onclick: git(l.sayfa - 1) }), ' ',
                    l.sayfa + ' / ' + son, ' ',
                    e('button', { sinif: 'i-d kucuk', metin: 'Sonraki ›', disabled: l.sayfa >= son, onclick: git(l.sayfa + 1) })
                ]) : null
            ]));
        }
        ana.addEventListener('scroll', acilirKapat);
        // Yeniden çizimden sonra: yazılmakta olan başlık kutusu odağını ve açık listesini korur.
        if (d.kutu) {
            const kutu = kok.querySelector('.i-kutu-s[data-k="' + d.kutu + '"] input');
            if (kutu) { kutu.focus(); kutu.setSelectionRange(kutu.value.length, kutu.value.length); acilirCiz(); }
            else acilirKapat();
        }
        if (d.secili) panelCiz();
    }

    // ── Başlık süzgeçleri ("yaz ya da seç") ──
    function secenekler(k) { return (d.liste && d.liste.secenekler && d.liste.secenekler[k]) || []; }
    function suzgecBul(k) { return TUM_SUZGEC.find(function(z) { return z.k === k; }); }
    // Seçili süzgecin ekranda görünen karşılığı (listeden seçildiyse etiketi, yazıldıysa yazının kendisi).
    // Listeden seçilen değerler (birden çok seçilebilir; süzgeçte satır sonuyla ayrılır). Yazıyla süzülüyorsa boş.
    function seciliDegerler(z) {
        const v = d.filtre[z.k];
        if (!v) return [];
        if (z.kodlu) return v.split('\n');
        return v.charAt(0) === '=' ? v.slice(1).split('\n') : [];
    }
    function degerEtiketi(z, deger) {
        const sec = secenekler(z.k).find(function(o) { return o.deger === deger; });
        return sec ? sec.etiket : (deger || '(boş)');
    }
    function suzgecEtiketi(z) {
        const v = d.filtre[z.k];
        if (!v) return '';
        if (!z.kodlu && v.charAt(0) !== '=') return v;
        const s = seciliDegerler(z);
        return degerEtiketi(z, s[0]) + (s.length > 1 ? ' +' + (s.length - 1) : '');
    }
    // Açılır listede bir seçeneği seçer / bırakır; liste açık kalır, başka seçenekler de işaretlenebilir.
    function secimDegistir(z, deger) {
        const s = seciliDegerler(z);
        const i = s.indexOf(deger);
        if (i >= 0) s.splice(i, 1); else s.push(deger);
        clearTimeout(kutuZaman);
        d.filtre[z.k] = s.length ? (z.kodlu ? '' : '=') + s.join('\n') : '';
        d.filtre.sayfa = 1;
        yukle();
    }
    function suzgecUygula(k, deger) {
        clearTimeout(kutuZaman);
        d.filtre[k] = deger;
        d.filtre.sayfa = 1;
        yukle();
    }
    function suzgecKutusu(z, yerTutucuAd) {
        const dolu = !!d.filtre[z.k];
        const acik = d.kutu === z.k;
        const giris = e('input', { type: 'text', autocomplete: 'off', spellcheck: 'false',
            // Liste açıkken (seçim sonrası yeniden çizim) kutu boş, seçim yer tutucuda: yazı seçimin sonuna eklenmez.
            placeholder: acik && dolu ? suzgecEtiketi(z) : (yerTutucuAd ? z.ad : (z.kodlu ? 'Seçin' : 'Yazın ya da seçin')),
            'aria-label': z.ad + ' süzgeci', title: z.ad + ': ' + (z.kodlu ? 'listeden seçin' : 'yazın (içinde geçenler) ya da listeden seçin; birden çok seçilebilir'),
            value: acik ? (d.kutuYazildi ? d.kutuMetin : '') : suzgecEtiketi(z) });
        giris.addEventListener('focus', function() {
            if (d.kutu !== z.k) { d.kutu = z.k; d.kutuMetin = ''; d.kutuYazildi = false; giris.select(); }
            acilirCiz();
        });
        giris.addEventListener('input', function() {
            d.kutu = z.k; d.kutuMetin = giris.value; d.kutuYazildi = true;
            acilirCiz();
            // Listeden seçim yapılmışsa yazı yalnız açılır listeyi daraltır (seçimler kaybolmasın).
            if (z.kodlu || seciliDegerler(z).length) return;
            // Yazarak süzme: içinde geçenler (kısa bir beklemeyle, her tuşta sorgu gitmesin).
            clearTimeout(kutuZaman);
            kutuZaman = setTimeout(function() { d.filtre[z.k] = giris.value.trim(); d.filtre.sayfa = 1; yukle(); }, 350);
        });
        giris.addEventListener('keydown', function(ev) {
            if (ev.key === 'Escape') { ev.preventDefault(); kutuBirak(); giris.blur(); surecCiz(); return; }
            if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') { ev.preventDefault(); imlecOynat(ev.key === 'ArrowDown' ? 1 : -1); return; }
            if (ev.key !== 'Enter') return;
            ev.preventDefault();
            const imlec = kok.querySelector('.i-acilir .sec.imlec');
            if (imlec) { imlec.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })); return; }
            if (z.kodlu) {
                const ilk = gorunenSecenekler(z)[0];
                if (ilk) secimDegistir(z, ilk.deger);
            } else { const yazi = giris.value.trim(); kutuBirak(); suzgecUygula(z.k, yazi); }
        });
        giris.addEventListener('blur', function() {
            // Liste satırına basış mousedown ile işlenir; burada yalnız kutu bırakılır.
            setTimeout(function() {
                // Liste yeniden çizildiyse bu kutu artık sayfada değil (yenisi odağı aldı): dokunma.
                if (!giris.isConnected || d.kutu !== z.k || document.activeElement === giris) return;
                const yazim = d.kutuYazildi && !z.kodlu;
                kutuBirak();
                if (!yazim && kok) giris.value = suzgecEtiketi(z);
            }, 120);
        });
        return e('div', { sinif: 'i-kutu-s' + (dolu ? ' dolu' : ''), 'data-k': z.k, onclick: function(ev) { ev.stopPropagation(); } }, [
            giris,
            dolu ? e('button', { sinif: 'sil', type: 'button', metin: '✕', title: z.ad + ' süzgecini kaldır', 'aria-label': z.ad + ' süzgecini kaldır',
                onmousedown: function(ev) { ev.preventDefault(); kutuBirak(); suzgecUygula(z.k, ''); } }) : null,
            e('button', { sinif: 'ok', type: 'button', metin: '▾', tabindex: '-1', title: 'Seçenekleri göster', 'aria-label': z.ad + ' seçenekleri',
                onmousedown: function(ev) {
                    ev.preventDefault();
                    if (d.kutu === z.k && kok.querySelector('.i-acilir')) { kutuBirak(); giris.blur(); }
                    else { d.kutu = null; giris.focus(); }
                } })
        ]);
    }
    function kutuBirak() { d.kutu = null; d.kutuMetin = ''; d.kutuYazildi = false; acilirKapat(); }
    function acilirKapat() { if (!kok) return; const a = kok.querySelector('.i-acilir'); if (a) a.remove(); }
    // Kutuya yazılan kelimelerin hepsi etikette geçiyor mu (açılır listeyi daraltmak için).
    function yaziyaUyar(etiket) {
        const aranan = d.kutuYazildi ? trKucuk(d.kutuMetin).split(/\s+/).filter(Boolean) : [];
        const m = trKucuk(etiket);
        return aranan.every(function(a) { return m.indexOf(a) >= 0; });
    }
    function gorunenSecenekler(z) {
        return secenekler(z.k).filter(function(o) { return yaziyaUyar(o.etiket); });
    }
    function imlecOynat(yon) {
        const hepsi = Array.from(kok.querySelectorAll('.i-acilir .sec'));
        if (!hepsi.length) return;
        let i = hepsi.findIndex(function(x) { return x.classList.contains('imlec'); });
        if (i >= 0) hepsi[i].classList.remove('imlec');
        i = (i + yon + hepsi.length + (i < 0 && yon < 0 ? 1 : 0)) % hepsi.length;
        hepsi[i].classList.add('imlec');
        hepsi[i].scrollIntoView({ block: 'nearest' });
    }
    // Açık kutunun seçenek listesi: adetleriyle, yazılan metne göre daralır. Sayfanın üstünde sabit konumda
    // (tablonun köşe yuvarlaması taşanı kestiği için başlığın içine konmaz).
    function acilirCiz() {
        acilirKapat();
        const z = suzgecBul(d.kutu);
        const kap = z && kok.querySelector('.i-kutu-s[data-k="' + z.k + '"]');
        if (!kap) return;
        const secili = d.filtre[z.k];
        const seciler = seciliDegerler(z);
        const liste = gorunenSecenekler(z);
        // Seçilip de listede olmayan değerler (sınırın dışında kalan ya da artık adedi olmayan) de görünsün, bırakılabilsin.
        seciler.forEach(function(s) {
            const etiket = degerEtiketi(z, s);
            if (yaziyaUyar(etiket) && !liste.some(function(o) { return o.deger === s; })) liste.unshift({ deger: s, etiket: etiket, adet: null });
        });
        const kutu = e('div', { sinif: 'i-acilir', role: 'listbox', 'aria-label': z.ad, 'aria-multiselectable': 'true' });
        if (secili) kutu.appendChild(e('div', { sinif: 'sec', role: 'option',
            onmousedown: function(ev) { ev.preventDefault(); kutuBirak(); suzgecUygula(z.k, ''); } },
            [e('span', { sinif: 'ad', metin: 'Hepsi (süzgeci kaldır)' })]));
        liste.forEach(function(o) {
            const on = seciler.indexOf(o.deger) >= 0;
            kutu.appendChild(e('div', { sinif: 'sec' + (on ? ' on' : ''), role: 'option', 'aria-selected': on ? 'true' : 'false',
                onmousedown: function(ev) { ev.preventDefault(); secimDegistir(z, o.deger); } },
                [e('span', { sinif: 'kt', metin: on ? '✓' : '' }), e('span', { sinif: 'ad', metin: o.etiket }),
                 e('span', { metin: o.adet === null ? '' : String(o.adet) })]));
        });
        if (!liste.length) kutu.appendChild(e('div', { sinif: 'yok', metin: secenekler(z.k).length ? 'Yazdığınıza uyan seçenek yok.' : 'Bu ölçütlerde seçenek yok.' }));
        const sinir = d.liste && d.liste.secenek_siniri;
        if (sinir && secenekler(z.k).length >= sinir) kutu.appendChild(e('div', { sinif: 'yok', metin: 'En çok kullanılan ' + sinir + ' seçenek gösteriliyor; diğerleri için yazın.' }));
        kok.appendChild(kutu);
        const r = kap.getBoundingClientRect();
        const g = kutu.getBoundingClientRect();
        kutu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - g.width - 8)) + 'px';
        const altta = window.innerHeight - r.bottom - 8;
        if (altta < 160 && r.top > altta) { kutu.style.maxHeight = Math.min(320, r.top - 8) + 'px'; kutu.style.bottom = (window.innerHeight - r.top + 2) + 'px'; }
        else { kutu.style.maxHeight = Math.min(320, altta) + 'px'; kutu.style.top = (r.bottom + 2) + 'px'; }
        kutu.style.minWidth = Math.max(220, r.width) + 'px';
    }

    // Seçili süzgeçler: tek satırda etiketler; tıklayınca o süzgeç kalkar.
    function seciliCiz(ana) {
        const etiketler = [];
        if (d.filtre.asama) etiketler.push(['Aşama', ASAMA_KISA[d.filtre.asama] || d.filtre.asama, function() { d.filtre.asama = ''; }]);
        if (d.filtre.acilis_bas || d.filtre.acilis_son) etiketler.push(['Açılış',
            (d.filtre.acilis_bas ? gunMetni(d.filtre.acilis_bas) : '…') + ' – ' + (d.filtre.acilis_son ? gunMetni(d.filtre.acilis_son) : '…'),
            function() { d.filtre.acilis_bas = ''; d.filtre.acilis_son = ''; }]);
        TUM_SUZGEC.forEach(function(z) {
            const v = d.filtre[z.k];
            if (!v) return;
            const yazi = !z.kodlu && v.charAt(0) !== '=';
            const adlar = yazi ? [] : seciliDegerler(z).map(function(s) { return degerEtiketi(z, s); });
            const metin = adlar.join(', ');
            etiketler.push([z.ad, yazi ? '“' + v + '” geçen' : (metin.length > 70 ? adlar[0] + ' ve ' + (adlar.length - 1) + ' seçim daha' : metin),
                            function() { d.filtre[z.k] = ''; }]);
        });
        if (!etiketler.length) return;
        const kaldir = function(fn) { return function() { fn(); kutuBirak(); d.filtre.sayfa = 1; yukle(); }; };
        ana.appendChild(e('div', { sinif: 'i-secili' }, [e('span', { metin: 'Süzgeç:' })].concat(etiketler.map(function(x) {
            return e('button', { sinif: 'et', type: 'button', title: x[0] + ' süzgecini kaldır', onclick: kaldir(x[2]) },
                [e('span', { metin: x[0] + ':' }), e('b', { metin: x[1] }), e('i', { metin: '✕' })]);
        })).concat(etiketler.length > 1 ? [e('button', { sinif: 'i-dahasi', type: 'button', metin: 'Hepsini kaldır',
            onclick: kaldir(function() {
                d.filtre.asama = ''; d.filtre.acilis_bas = ''; d.filtre.acilis_son = '';
                TUM_SUZGEC.forEach(function(z) { d.filtre[z.k] = ''; });
            }) })] : [])));
    }

    // ── Borçlu satırı: künye + borçlunun bütün tebligatları (tebligat başına bir alt satır) ──
    function tebligatHucreleri(t, suzgecVar, ilk) {
        const sol = suzgecVar && !t.uyan ? ' sol' : '';
        const sinif = 'tb' + sol + (ilk ? ' t1' : '');
        const alt1 = [t.evrak_tarihi, t.elektronik ? 'E-tebligat' : (t.barkod || 'barkod yok')].filter(Boolean).join(' · ');
        const alt2 = [t.son_islem_tarihi, t.teslim_sekli].filter(Boolean).join(' · ');
        return [
            e('td', { sinif: sinif + ' tb0' }, [e('div', { metin: t.tur || 'Tebligat' }), e('div', { sinif: 'alt', metin: alt1 })]),
            e('td', { sinif: sinif }, [t.ptt_durumu ? e('div', { sinif: 'uzun', title: t.ptt_durumu, metin: t.ptt_durumu })
                                                     : e('div', { sinif: 'silik', metin: 'Henüz sorgulanmadı' }),
                                       alt2 ? e('div', { sinif: 'alt', metin: alt2 }) : null]),
            e('td', { sinif: sinif }, [t.mazbata_var ? e('span', { sinif: 'mz', title: t.mazbata || 'Mazbata dosyaya döndü', metin: 'Döndü' })
                                                     : e('span', { sinif: 'silik', metin: 'Dönmedi' })])
        ];
    }
    function borcluGovdesi(s) {
        const suzgecVar = !!(d.filtre.tur || d.filtre.teblig || d.filtre.mazbata);
        // Süzgece uyan tebligatlar öne alınır (sıra içinde yeniden eskiye kalır).
        const hepsi = (s.tebligatlar || []).slice().sort(function(a, b) { return suzgecVar ? (b.uyan ? 1 : 0) - (a.uyan ? 1 : 0) : 0; });
        const tumu = !!d.tumTebligat[s.bag];
        const gorunen = tumu ? hepsi : hepsi.slice(0, TEBLIGAT_GORUNEN);
        const fazla = hepsi.length - TEBLIGAT_GORUNEN;
        const satirSayisi = Math.max(1, gorunen.length) + (fazla > 0 ? 1 : 0);
        const kunye = [
            e('td', { rowspan: String(satirSayisi) }, [e('div', { sinif: 'dno', metin: s.dosya_no }), e('div', { sinif: 'alt', metin: s.birim }),
                e('div', { sinif: 'alt', metin: [s.dosya_tur, s.acilis_tarihi ? 'açılış ' + gunMetni(s.acilis_tarihi) : ''].filter(Boolean).join(' · ') }),
                s.acik ? null : e('div', { sinif: 'alt', metin: s.dosya_durumu || 'Kapalı' })]),
            e('td', { rowspan: String(satirSayisi) }, s.bag == null ? [e('span', { sinif: 'silik', metin: 'Borçlu kaydı yok' })]
                : [s.borclu, s.uyapta_gorulmedi ? e('div', { sinif: 'uyari', metin: 'UYAP\'ta görülmedi' }) : null]),
            e('td', { rowspan: String(satirSayisi), metin: s.alacakli || '—' }),
            e('td', { rowspan: String(satirSayisi) }, s.bag == null ? [] : [chip(s.asama, s.asama_tarihi)]),
            e('td', { rowspan: String(satirSayisi), metin: s.kesinlesme || '—' })
        ];
        const govdeEl = e('tbody', { sinif: 'tik' + (d.secili && d.secili.id === s.dosya ? ' sec' : ''), onclick: function() { dosyaAc(s.dosya); } });
        if (!gorunen.length) {
            govdeEl.appendChild(e('tr', {}, kunye.concat([e('td', { colspan: '3', sinif: 'tb t1 tb0 silik',
                metin: s.bag == null ? 'Dosyanın tarafları henüz taranmadı' : 'Bu borçluya gönderilmiş tebligat kaydı yok' })])));
        } else {
            gorunen.forEach(function(t, i) {
                govdeEl.appendChild(e('tr', { sinif: i ? 'ek' : '' }, (i ? [] : kunye).concat(tebligatHucreleri(t, suzgecVar, i === 0))));
            });
        }
        if (fazla > 0) {
            govdeEl.appendChild(e('tr', { sinif: 'ek' }, [e('td', { colspan: '3', sinif: 'tb' }, [e('button', { sinif: 'i-dahasi', type: 'button',
                metin: tumu ? 'Daha az göster' : '+' + fazla + ' tebligat daha',
                title: tumu ? '' : 'Bu borçlunun bütün tebligatlarını göster (' + hepsi.length + ')',
                onclick: function(ev) { ev.stopPropagation(); if (tumu) delete d.tumTebligat[s.bag]; else d.tumTebligat[s.bag] = true; surecCiz(); } })])]));
        }
        return govdeEl;
    }

    // ── UYAP'tan güncelleme ──
    // İstekler ofis bilgisayarından, sırayla ve yavaş hızla UYAP'a gider (icra_guncelle.rs). Sayfa yalnız başlatır,
    // izler, durdurur ve doğrulama (captcha) çıkarsa kullanıcıya çözdürür.
    const GT_UC = '/__uyap_agent__/icra_guncelle';
    const GT_SON = ['bitti', 'hata', 'iptal'];
    const GT_ASAMA = { liste: 'Dosya listesi alınıyor', kunye: 'Künyeler yazılıyor', taraf: 'Taraflar ve borçlu kimlikleri alınıyor',
                       kapanan: 'Kapanan dosyalar kontrol ediliyor', hesap: 'Durumlar hesaplanıyor', bitti: 'Bitti' };
    let gtZaman = null, tsYukleniyor = null;
    function gtSuruyor() { return !!(d.gt && GT_SON.indexOf(d.gt.durum) < 0); }
    function gtGorunsun() { return d.gtAcik || gtSuruyor(); }
    async function gtIstek(yol, govde) {
        let r;
        try {
            r = await fetch(GT_UC + yol, govde === undefined
                ? { cache: 'no-store', credentials: 'same-origin' }
                : { method: 'POST', cache: 'no-store', credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(govde) });
        } catch (x) { throw new Error('Ofis bilgisayarına ulaşılamadı.'); }
        let j = null;
        try { j = await r.json(); } catch (x) {}
        if (!j) throw new Error('Ofis bilgisayarından beklenmeyen yanıt (' + r.status + '). Program güncel olmayabilir.');
        if (j.error || j.ok === false) throw new Error(j.error || 'İşlem yapılamadı.');
        return j.tarama || null;
    }
    // Tarama sürerken yazılan dosyalar listede görünsün: sayaçlar değişince liste sessizce yenilenir
    // (en çok 10 sn'de bir). Kullanıcı başlık kutusunda yazıyor ya da bir alana odaklanmışsa beklenir;
    // kaydırma yeri korunur.
    const CANLI_ARA_MS = 10000;
    let canliIz = '', canliSon = 0, canliSuruyor = false;
    async function canliYenile(t) {
        const iz = [t.yazilan, t.taraf_cekilen, t.kapandi, t.yeni_dosya].join('/');
        if (iz === canliIz || canliSuruyor || t.captcha || !govde || d.sekme !== 'surec' || d.kutu) return;
        if (Date.now() - canliSon < CANLI_ARA_MS) return;
        const odak = document.activeElement;
        if (odak && kok && kok.contains(odak) && /^(INPUT|SELECT|TEXTAREA)$/.test(odak.tagName)) return;
        canliSuruyor = true;
        try {
            const sonuc = await Promise.all([istek('ozet'), istek('borclular?' + listeSorgusu())]);
            if (!govde || d.sekme !== 'surec' || d.kutu) return;
            const ana = govde.querySelector('.i-ana');
            const yer = ana ? ana.scrollTop : 0;
            d.ozet = sonuc[0]; d.liste = sonuc[1];
            durumYaz();
            surecCiz();
            const yeniAna = govde.querySelector('.i-ana');
            if (yeniAna) yeniAna.scrollTop = yer;
            canliIz = iz;
        } catch (x) {
            // sessiz: bir sonraki yoklamada yeniden denenir
        } finally {
            canliSon = Date.now(); canliSuruyor = false;
        }
    }
    // Süren taramayı izler (2 sn'de bir); bitince liste ve özet yenilenir.
    async function gtYokla(ilk) {
        clearTimeout(gtZaman);
        let t;
        try { t = await gtIstek(''); }
        catch (x) { if (!ilk && gtSuruyor()) gtZaman = setTimeout(gtYokla, 10000); return; }
        const onceSuruyordu = gtSuruyor();
        d.gt = t;
        if (gtSuruyor()) gtZaman = setTimeout(gtYokla, 2000);
        if (onceSuruyordu && !gtSuruyor()) { d.gtAcik = true; canliIz = ''; yukle(); return; }
        gtCiz();
        if (gtSuruyor()) canliYenile(t);
    }
    function gtCiz() {
        if (!kok) return;
        const dugme = kok.querySelector('.i-gt-dugme');
        if (dugme) dugme.replaceWith(gtDugme());
        const eski = kok.querySelector('.i-gt');
        // Doğrulama kutusu açıkken yeniden çizilmez (kullanıcının çözdüğü kutu kaybolmasın).
        if (eski && d.gt && d.gt.captcha && eski.getAttribute('data-captcha') === String(d.gt.captcha.n)) return;
        if (!gtGorunsun()) { if (eski) eski.remove(); return; }
        const yeni = gtPaneli();
        if (eski) { eski.replaceWith(yeni); return; }
        const arac = kok.querySelector('.i-arac') || kok.querySelector('.i-bilgi');
        if (arac) arac.after(yeni);
    }
    function gtDugme() {
        const t = d.gt;
        const metin = gtSuruyor()
            ? 'Güncelleniyor' + (t.toplam ? ' · ' + Math.min(100, Math.round(t.i * 100 / t.toplam)) + '%' : '…')
            : 'UYAP\'tan güncelle';
        return e('button', { sinif: 'i-d i-gt-dugme' + (gtSuruyor() ? ' ana' : ''), type: 'button', metin: metin,
            title: 'UYAP\'taki açık icra dosyalarını büro veritabanına alır: dosya listesi ya da taraf bilgileri',
            onclick: function() { d.gtAcik = gtSuruyor() ? true : !d.gtAcik; gtCiz(); if (d.gtAcik) gtYokla(true); } });
    }
    function mesaiMi() {
        const s = new Date(), gun = s.getDay(), dk = s.getHours() * 60 + s.getMinutes();
        return gun >= 1 && gun <= 5 && dk >= 8 * 60 + 30 && dk < 18 * 60;
    }
    function sureMetni(sn) {
        if (sn == null) return '';
        if (sn < 60) return '1 dakikadan az';
        const dk = Math.round(sn / 60);
        return dk < 60 ? '≈ ' + dk + ' dk' : '≈ ' + Math.floor(dk / 60) + ' sa ' + (dk % 60) + ' dk';
    }
    function gtPaneli() {
        const t = d.gt;
        const kutu = e('div', { sinif: 'i-gt', role: 'region', 'aria-label': 'UYAP\'tan güncelleme' });
        kutu.appendChild(e('div', { sinif: 'i-gt-ust' }, [e('b', { metin: 'UYAP\'tan güncelle' }), e('div', { sinif: 'i-bosluk' }),
            gtSuruyor() ? null : e('button', { sinif: 'i-d kucuk', type: 'button', metin: 'Kapat', onclick: function() { d.gtAcik = false; gtCiz(); } })]));
        if (gtSuruyor()) { gtSurenCiz(kutu, t); return kutu; }
        if (t) gtSonucCiz(kutu, t);
        gtFormCiz(kutu, t);
        return kutu;
    }
    function gtTurAdi(t) { return t && t.mod === 'taraf' ? 'Taraf bilgileri' : 'Dosya listesi'; }
    function gtSurenCiz(kutu, t) {
        kutu.appendChild(e('div', { stil: 'font-weight:600', metin: gtTurAdi(t) + ' güncelleniyor' + (t.kapsam ? ' — ' + t.kapsam : '') }));
        kutu.appendChild(e('div', { metin: GT_ASAMA[t.asama] || t.asama }));
        kutu.appendChild(e('div', { sinif: 'i-gt-not', metin: t.mesaj || '' }));
        if (t.toplam) {
            kutu.appendChild(e('div', { sinif: 'i-gt-cubuk', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': String(t.toplam), 'aria-valuenow': String(t.i) },
                [e('i', { stil: 'width:' + Math.min(100, t.i * 100 / t.toplam).toFixed(1) + '%' })]));
        }
        const parcalar = [];
        if (t.toplam) parcalar.push(t.i + ' / ' + t.toplam);
        if (t.kalan_sn != null) parcalar.push('kalan ' + sureMetni(t.kalan_sn));
        if (t.mola_kalan_sn != null) parcalar.push('mola: ' + t.mola_kalan_sn + ' sn');
        parcalar.push(t.alinan + ' dosya alındı', t.yeni_dosya + ' yeni');
        if (t.taraf_cekilen) parcalar.push(t.taraf_cekilen + ' dosyanın tarafları / borçlu kimlikleri alındı');
        if (t.kapandi) parcalar.push(t.kapandi + ' kapandı');
        kutu.appendChild(e('div', { sinif: 'i-gt-not', metin: parcalar.join(' · ') }));
        if (t.captcha) gtCaptchaCiz(kutu, t);
        if (t.uyarilar && t.uyarilar.length) {
            kutu.appendChild(e('ul', { sinif: 'i-gt-uyarilar' }, t.uyarilar.slice(-5).map(function(u) { return e('li', { metin: u }); })));
        }
        const dur = e('button', { sinif: 'i-d tehlike', type: 'button', metin: 'Durdur', stil: 'margin-top:8px',
            onclick: async function() {
                dur.disabled = true;
                try { d.gt = await gtIstek('/' + t.id + '/iptal', {}); } catch (x) { bildir(x.message); }
                gtYokla();
            } });
        kutu.appendChild(dur);
    }
    function gtSonucCiz(kutu, t) {
        const tur = t.mod === 'taraf' ? 'Son taraf güncellemesi' : 'Son dosya listesi güncellemesi';
        const ad = tur + ({ bitti: ' tamamlandı', iptal: ' durduruldu', hata: ' hatayla bitti' }[t.durum] || '');
        kutu.appendChild(e('div', { stil: 'font-weight:600', metin: ad + (t.kapsam ? ' (' + t.kapsam + ')' : '') }));
        if (t.mesaj) kutu.appendChild(e('div', { sinif: 'i-gt-not', metin: t.mesaj }));
        if (t.rapor && t.rapor.length) kutu.appendChild(e('ul', { sinif: 'i-gt-rapor' }, t.rapor.map(function(s) { return e('li', { metin: s }); })));
        if (t.uyarilar && t.uyarilar.length) {
            kutu.appendChild(e('details', {}, [e('summary', { metin: t.uyari_sayisi + ' uyarı / not' + (t.uyari_sayisi > t.uyarilar.length ? ' (son ' + t.uyarilar.length + ')' : '') }),
                e('ul', { sinif: 'i-gt-uyarilar' }, t.uyarilar.map(function(u) { return e('li', { metin: u }); }))]));
        }
    }
    function gtFormCiz(kutu, t) {
        const f = d.gtForm;
        kutu.appendChild(e('div', { sinif: 'i-gt-not', stil: 'margin-top:6px',
            metin: 'İstekler ofis bilgisayarından sırayla ve yavaş gider; sayfayı kapatsanız da sürer. Kapsamı daraltmak için daire ya da açılış tarihi aralığı seçin.' }));
        const sec = e('select', { 'aria-label': 'Güncelleme kapsamı: daire' }, [e('option', { value: '', metin: 'Bütün açık dosyalar' })]
            .concat(((d.ozet && d.ozet.birimler) || []).map(function(b) {
                const o = e('option', { value: b.birim_id, metin: b.ad + ' (' + b.acik + ' açık)' });
                if (b.birim_id === f.birim) o.selected = true;
                return o;
            })));
        sec.addEventListener('change', function() { f.birim = sec.value; });
        const tarih = function(k, ad) {
            const x = e('input', { type: 'date', value: f[k], title: ad, 'aria-label': ad });
            x.addEventListener('change', function() { f[k] = x.value; });
            return x;
        };
        const basK = tarih('bas', 'Açılış tarihi: başlangıç'), sonK = tarih('son', 'Açılış tarihi: bitiş');
        const iso = function(dt) { return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0'); };
        const buAy = e('button', { sinif: 'i-d kucuk', type: 'button', metin: 'Bu ay', title: 'Bu ay açılan dosyalar',
            onclick: function() { const s = new Date(); f.bas = basK.value = iso(new Date(s.getFullYear(), s.getMonth(), 1)); f.son = sonK.value = iso(s); } });
        const temizle = e('button', { sinif: 'i-d kucuk', type: 'button', metin: 'Tarihsiz', title: 'Tarih aralığını kaldır',
            onclick: function() { f.bas = basK.value = ''; f.son = sonK.value = ''; } });
        kutu.appendChild(e('div', { sinif: 'i-gt-form' }, [e('label', { sinif: 'i-gt-not', metin: 'Kapsam' }), sec,
            e('label', { sinif: 'i-gt-not', metin: 'Açılış' }), basK, '–', sonK, buAy, temizle]));
        const baslat = function(mod, dugme) {
            return async function() {
                dugme.disabled = true;
                const govde = { mod: mod };
                if (f.birim) { govde.birim_id = f.birim; govde.birim_adi = sec.options[sec.selectedIndex].textContent.replace(/ \(\d+ açık\)$/, ''); }
                if (f.bas) govde.acilis_bas = f.bas;
                if (f.son) govde.acilis_son = f.son;
                try { d.gt = await gtIstek('', govde); gtYokla(); }
                catch (x) { bildir(x.message); dugme.disabled = false; }
            };
        };
        const liste = e('button', { sinif: 'i-d ana', type: 'button', metin: 'Dosya listesini güncelle',
            title: 'Açık dosyaların listesi: yeni dosyalar eklenir, kapanan dosyalar işaretlenir (taraf istenmez)' });
        liste.addEventListener('click', baslat('liste', liste));
        const taraf = e('button', { sinif: 'i-d', type: 'button', metin: 'Taraf bilgilerini güncelle',
            title: 'Yalnız tarafı ya da borçlu TCKN / vergi no\'su eksik dosyalar sorgulanır' });
        taraf.addEventListener('click', baslat('taraf', taraf));
        kutu.appendChild(e('div', { sinif: 'i-gt-form' }, [liste, taraf,
            e('span', { sinif: 'i-gt-not', metin: 'Tebligat bilgileri: sonraki aşamada.' })]));
        kutu.appendChild(e('div', { sinif: 'i-gt-not', stil: 'margin-top:6px',
            metin: 'Taraf bilgileri dosya başına 1–2 istek atar (~3 sn arayla); bütün açık dosyalarda saatler sürebilir, önce dar bir kapsam seçin.' }));
        if (mesaiMi()) {
            kutu.appendChild(e('div', { sinif: 'i-gt-uyari', role: 'note',
                metin: 'Mesai saatindesiniz: tarama UYAP\'ı yavaşlatabilir ve büronun sorgularıyla çakışabilir. Akşam 18:00\'den sonra çalıştırmanız önerilir.' }));
        }
    }
    // Güvenlik doğrulaması: UYAP'ın kendi Turnstile site anahtarıyla, kullanıcı çözer (Gerekçeli Kararlar paneliyle aynı).
    function tsYukle() {
        if (window.turnstile) return Promise.resolve(window.turnstile);
        if (tsYukleniyor) return tsYukleniyor;
        tsYukleniyor = new Promise(function(res, rej) {
            const sc = document.createElement('script');
            sc.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
            sc.async = true;
            sc.onload = function() { res(window.turnstile); };
            sc.onerror = function() { tsYukleniyor = null; rej(new Error('doğrulama betiği yüklenemedi')); };
            document.head.appendChild(sc);
        });
        return tsYukleniyor;
    }
    function gtCaptchaCiz(kutu, t) {
        const c = t.captcha;
        kutu.setAttribute('data-captcha', String(c.n));
        const yer = e('div', { stil: 'margin-top:6px;min-height:65px' });
        const cevap = async function(jeton) {
            try { d.gt = await gtIstek('/' + t.id + '/captcha', { token: jeton }); } catch (x) { bildir(x.message); }
            kutu.removeAttribute('data-captcha');
            gtYokla();
        };
        kutu.appendChild(e('div', { sinif: 'i-gt-uyari', metin: c.mesaj || 'UYAP güvenlik doğrulaması istiyor.' }));
        kutu.appendChild(yer);
        kutu.appendChild(e('button', { sinif: 'i-d kucuk', type: 'button', metin: 'Tekrar dene', onclick: function() { cevap(null); } }));
        tsYukle().then(function(ts) {
            ts.render(yer, { sitekey: c.site_key, callback: cevap,
                'error-callback': function(kod) {
                    yer.appendChild(e('div', { sinif: 'i-gt-uyari', metin: 'Doğrulama açılamadı (kod: ' + kod + '). Biraz bekleyip "Tekrar dene"ye basın.' }));
                    return true;
                } });
        }).catch(function(x) { yer.appendChild(e('div', { sinif: 'i-gt-uyari', metin: 'Doğrulama gösterilemedi (' + x.message + ').' })); });
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
            const sonuc = await Promise.all([istek('ozet'), istek('borclular?' + listeSorgusu())]);
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
