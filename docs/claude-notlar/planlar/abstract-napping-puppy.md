# UYAP içinde sade sağ tık menüsü

## Context
UYAP sayfalarında sağ tıklayınca tarayıcının uzun menüsü çıkıyor (Geri, Farklı kaydet, Yazdır, Çevir, İncele…).
Kullanıcı bunun yerine UYAP'ta işine yarayan az seçenekli bir CokluUyap menüsü istiyor: İş Ata / belgeye iş
bağla, Bu dosyanın işleri, Kopyala (dosya no, metin), Belgeyi indir / seçime ekle. Tarayıcı menüsünü
değiştirmek eklenti ister; ama UYAP sayfalarına zaten kendi JS'imizi enjekte ediyoruz
(`uyap_core/uyap_proxy.py`, `script = r"""…`), o yüzden `contextmenu` olayını yakalayıp kendi menümüzü
gösteriyoruz. Yalnız masaüstü uygulaması değişir (sunucu yüklemesi gerekmez; uygulamayı yeniden başlatmak yeter).

## Yaklaşım
Tek dosya: `uyap_core/uyap_proxy.py`, İş Listesi IIFE'sinin (satır ~1721'den başlayan blok) sonuna
`// ── Sağ tık menüsü ──` bölümü. Diğer bloklarla iletişim mevcut `window.__uyap*` köprüleriyle.

### Ne zaman bizim menü, ne zaman tarayıcınınki
- `document.addEventListener('contextmenu', …, true)` yalnız üst pencerede (`window.top === window`, panel gibi).
- **Tarayıcı menüsü kalır:** Shift ya da Ctrl basılıysa; hedef `input`, `textarea`, `select`,
  `[contenteditable]` içindeyse (yapıştır / yazım denetimi gerekir); hedef bizim pencere/panel içindeyse
  (`#uyap-ispanel`, ort/kutu); `localStorage['cokluuyapSagMenu']==='kapali'` ise (try/catch).
- Aksi halde `preventDefault()` + bizim menü. Menünün altında küçük not: "Tarayıcı menüsü: Shift + sağ tık".

### Bağlam → menü maddeleri (yalnız uygun olanlar gösterilir)
1. **Evrak ağacında belge** (`.dosya-sorgula-popup .evrak-treeview .dx-treeview-node`; düğüm verisi
   `window.__uyapEvrakAgaci.veri/harita/ozet` ile, `belgeleriIsaretle` ile aynı yol):
   - "Bu belgeye iş bağla" → `pencereAc(d, kart, belgeAnahtari(ozet))`
   - Belgenin bitmemiş işi varsa "Belgenin işi: <başlık>" → `isAc(id)` (birden çoksa `panelDosyaAc(d)`)
   - "İndirme seçimine ekle / seçimden çıkar" → satırdaki mevcut `input[data-tumevrak-sec]` kutusunu
     `click()` ile değiştirir (sayaç ve "Seçili Belgeleri İndir (N)" kendiliğinden güncellenir)
   - "Bu belgeyi indir" → indirme bloğunda yeni köprü `window.__uyapEvrakIndir(kart, [ozet])`
     (`secenekAc`'ın `evraklar` hazır verilen hali; `baslat(dosyaId, ad, cikti, 'secili', evraklar)`)
2. **Dosya kartının içinde** (`.dosya-sorgula-popup`, `window.__uyapKartVerisi(kart)`):
   - "İş Ata" → `pencereAc(d, kart)`
   - "Bu dosyanın işleri (N)" → `panelDosyaAc(d)` (N: `acikIsler(d).length`)
   - "Dosya no'yu kopyala" → `window.__uyapDosyaAdi(d)` ("İzmir 15. İcra Dairesi 2025/123 …")
   - "Tüm evrakı indir" → kart başlığındaki mevcut `[data-tumevrak]` düğmesine `click()`
3. **Ana sayfa bildirimi** (`li.notify-item` ve `BILDIRIM_RE` eşleşiyorsa): "Bu bildirimden iş aç" →
   `bildirimdenIs(...)` (mevcut "+ İş" düğmesiyle aynı).
4. **Her yerde:**
   - Seçili metin varsa "Kopyala: “…”" (ilk 30 karakter)
   - Bağlantı (`a[href]`) üstündeyse "Yeni sekmede aç"
   - "İşlerim" → `panelAc()`; "Geri", "Sayfayı yenile"

Kopyalama: `navigator.clipboard.writeText` (127.0.0.1 güvenli bağlam), olmazsa gizli textarea +
`document.execCommand('copy')`; kısa "Kopyalandı" bildirimi.

### Menü görünümü / davranışı
- Tek `div#uyap-sagmenu` (yeniden kullanılır), panelle aynı koyu tema (`rgba(18,18,24,.97)`, 12px,
  `z-index:2147483646`), madde başına satır + gruplar arasında ince çizgi.
- Konum fare noktası, ekrandan taşarsa sola/yukarı kaydırılır.
- Kapanma: dışarı tık (`mousedown` yakalama), Esc, kaydırma, `blur`, madde seçimi.
- Klavye: ↑/↓ ve Enter (basit).
- CSS mevcut `kur()` stil enjeksiyonunun yanına eklenir (`.uyap-sagmenu-madde:hover` vb.).

### İndirme bloğuna küçük ekleme (`uyap_proxy.py` ~satır 1590)
`secenekAc(kart, dosyaId, ad, kapsam, hazirEvraklar)`: `hazirEvraklar` verilirse kapsam metni
"1 belge: <tür>" olur ve İndir onu kullanır. `window.__uyapEvrakIndir = function(kart, evraklar)`
kartVerisi'nden dosyaId/ad'ı alıp bunu çağırır.

## Doküman
`yapılacak iş.md`'ye kısa "Sağ tık menüsü" notu (hangi bağlamda ne çıkar, Shift + sağ tık).

## Doğrulama
1. JS sözdizimi: `up._rewrite_body('text/html', …)` ile betiği çıkarıp `node --check` (önceki yol).
2. `python -m unittest tests.test_zs` (değişiklik yalnız JS; regresyon için).
3. Kullanıcı uygulamayı yeniden başlatır; birlikte denenecekler: dosya kartında sağ tık → İş Ata;
   evrak ağacında belge → seçime ekle / indir / iş bağla; metin seçip kopyala; arama kutusunda sağ tık
   → tarayıcı menüsü (yapıştır) gelmeli; Shift + sağ tık → tarayıcı menüsü.
4. Commit (push / sunucu yüklemesi yok — bu değişiklik yalnız masaüstü uygulamasında).
   Not: önceki commit 2e1ba5c'nin (yan panelden onay) sunucu yüklemesi hâlâ kullanıcı onayı bekliyor.
