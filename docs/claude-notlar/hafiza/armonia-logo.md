---
name: armonia-logo
description: "Yeni logo Armonia \"A\" işareti; kaynak sayfa ve kırpılmış varlıklar ruyap/assets'te"
metadata:
  node_type: memory
  type: project
  originSessionId: c01c5ebc-9e1e-4a69-901f-6e84975a9683
  modified: 2026-11-14T12:32:26.365Z
---

2026-11-14: Kullanıcının yeni logosu `ruyap/Armonia Logo.jpg` (tek JPG'de üç varyant: kare simge, açık/koyu yatay logo — vektör yok). Kırpılmış saydam varlıklar `ruyap/assets/`: `armonia-isaret-{acik,koyu,buyuk}.png`, `armonia-simge-{32,64,256}.png` (kırık beyaz kart #FBFCF6 + işaret), `armonia.ico`, `armonia-simge-68.rgba` (egui giriş rozeti + pencere simgesi, include_bytes). Renkler: adaçayı #7D9A7D, koyu #1F2A24; İş Listesi sitesi vurgusu #567559 (beyaz yazı kontrastı için koyulaştırıldı).

Uygulandığı yerler: tema.js üst çubuk logosu + sekme simgesi (base64), uyap_app.rs giriş rozeti/pencere simgesi, uyap_proxy.rs `_oturum_yok_sayfasi`, İş Listesi simgeleri/logo. Yazı adı hâlâ "Çoklu UYAP" — Armonia adına geçiş kullanıcıya sorulmadı/karar yok.

**How to apply:** Yeni boyut gerekirse `armonia-isaret-buyuk.png`'den üret (JPG'den yeniden kesme). exe dosya simgesi + sürüm bilgisi: `ruyap/build.rs` `windows_kaynagi()` MinGW `windres` ile (crate yok; .ico/.rc ASCII yollu OUT_DIR'e kopyalanır, Türkçe yol GNU araçlarını bozuyor); windres yoksa uyarıyla simgesiz derler. İlgili: [[uyap-arayuz-sadelestirme]], [[enjekte-kanonik-ruyap]].
