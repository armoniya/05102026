# Çoklu UYAP — Rust portu (ruyap) Davranışsal Denklik Testleri

> Hazırlayan: Claude (Opus 4.8) · Tarih: 2026-11-13 · Yarın sabah oturumda hatırlatılacak.
> Amaç: Rust ikilisi (`uyap_app.exe`), Python/`main.js` sürümüyle **birebir** mi çalışıyor?

## Bugün kanıtlanan (2026-11-13)
- ✅ `cargo build --bins` → 10 ikili 0 hatayla üretildi (`C:/dev/target/debug/`). 30 kozmetik uyarı.
- ✅ `uyap_app.exe` fareyle çalıştırıldı, e-imza PIN girildi, **UYAP girişi başarılı**.
- ✅ 8800 portunu **Rust** `uyap_app.exe` tutuyor (eski Python değil), bellekte ~74 MB.
- ✅ Auth kapısı çalışıyor (kimliksiz istek 403).
- ✅ Kullanıcı gözlemi: "**açık ara daha hızlı**" (Rust kazancı somut).
- ✅ Python→Rust güncelliği kaynak-karşı-kaynak doğrulandı: taşınacak kod yoktu; enjekte/ ve webapp/ diskten paylaşımlı.

## ÖNEMLİ: Düzeltmeden sonra
Enjekte dosyaları (tema/mobil .css+.js) açılışta BİR KEZ okunur. Değişiklik sonrası:
1. `uyap_app.exe`'yi kapat, yeniden aç.
2. Tarayıcıda **sert yenileme** (Ctrl/⌘+Shift+R; telefonda sekmeyi kapat-aç).

## Tarayıcıda gözle doğrulanacak denklik listesi
(Her biri bir enjekte modülünden gelir; hepsi Python ile aynı dosya olduğundan sonuç iki tarafta da aynı olmalı.)

### Sonuç 2026-11-14 (Claude, Chrome eklentisiyle, Rust uyap_app debug)
Ön koşul sorunu: eski ArkSigner kurulumunda `ArkSignerService.exe` 23.02.2026'dan beri eksikti → 5975 kapalı →
PIN girişi başarısız. Kullanıcı e-imza yükleyicisini yeniden kurdu; 5975'i artık "Adalet E-imza" tutuyor, giriş OK.

- [x] **1. Sekme başlığı** "Çoklu UYAP" (UYAP'ın kendi başlığı değil)
- [x] **2. Görev çubuğu + Başlat düğmesi** altta görünüyor (`gorevcubugu.js`)
- [ ] **3. Dosya kartları** görev çubuğunda açılıp kapanıyor (`kartlar.js`) — kullanıcı bir dosya açarak bakacak
- [x] **4. E-İmza alanı:** sürükleme (DataTransfer 'Files') ile sağ altta 260x180 beliriyor, "Belgeyi buraya bırakın" görünüyor, sürükleme bitince 600 ms'de kapanıyor
- [x] **5. Tema değişimi:** Güneş → Okyanus → Koyu → Güneş denendi, renkler değişiyor. Seçicide 9 tema var (Koyu, UYAP, Armonia, Gül, Okyanus, Lavanta, Gün Batımı, Güneş, Grafit)
- [ ] **6. Dar ekran/telefon kipi:** pencere daraltılamadı (eklenti/MoveWindow etkisiz; iframe'de enjekte bilerek çalışmıyor) — kullanıcı telefondan bakacak (Bug A/B ile birlikte)
- [x] **7. Ana sayfa sorgu kısayolları:** 10 kısayol var, "Asliye Hukuk" → Yargı türü Hukuk + birim ASLİYE HUKUK + mahkeme kutusu etkin; `__uyapSorgu` tanımlı
- [ ] **8. Toplu evrak indirme** — indirme yaptığı için kullanıcı deneyecek
- [x] **9. İş Listesi paneli** sarı temada okunuyor (zemin rgba(66,52,20), yazı #f3f4f6, seçili çip sarı zemin koyu yazı) — (`#uyap-ispanel`, gorevcubugu.js) — özellikle **sarı (Güneş) temada** yazı okunur mu? (mobil sekme çipleriyle aynı tuzak olabilir; kontrol et, gerekirse `--cu-panel-yazi` ile düzelt)

## Bugün düzeltilen mobil hatalar (yeniden başlatınca doğrula)
- [ ] **Bug A:** Sarı (Güneş) temada mobil dosya penceresi sekme düğmeleri okunaksızdı (koyu kahve zemin + koyu yazı). Düzeltme: `--cu-panel-yazi` (her temada açık) + seçili çipte `--cu-cubuk-yazi`. → Artık tüm temalarda okunur olmalı.
- [ ] **Bug B:** Sekme adları ikileniyordu ("EvrakEvrak"). Sebep: DevExtreme'in gizli ölçüm span'i. Düzeltme: `sekmeAdi()` kopyayı atarak okuyor. → Tek yazılmalı.

## Sonraki aşama (konuşulacak)
- Release derleme (`--release`): ikili boyutu/hızı.
- Linux paketleme fazı (Debian + toram/EROFS + LUKS2 + sertleştirme + bitmiş Rust ikilisi).
- PDF hızı: `mupdf` crate ile ölç (hibrit gerekir mi?).
- `uyap_proxy.rs` (7.245 satır) ayrı kod incelemesi.
