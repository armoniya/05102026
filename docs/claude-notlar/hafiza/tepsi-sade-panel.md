---
name: tepsi-sade-panel
description: Kullanıcı programı/tepsiyi HAFİFLETMEK istiyor; ayar penceresi ekleme — ayarlar (varsayılan birim vb.) UYAP web görünümüne
metadata:
  type: feedback
---

16.11: Ayrı "Ayarlar" penceresi (yargı birimi, tema, paylaşım) yazdım; kullanıcı reddetti: "programı şişiriyorsun, ben tepsi ve programı hafifletmeye çalışıyorum". Varsayılan birim gibi ayarlar UYAP web görünümüne (enjekte) gidecek. Pencere kaldırıldı.

Onaylanan yapı (ruyap uyap_app.rs): tepsi tık → giriş kartı; girişten sonra SADE PANEL (`ui_kompakt`: büyük durum kutusu + sunucuda PIN/"Bağlantıyı paylaş" ya da "UYAP'ı aç"/"Paylaşımı durdur", istemcide "UYAP'ı aç"; "Ayrıntılı görünüm" eski Kontrol Ekranı). Tepsi menüsü: (sunucu+paylaşım kapalıysa PIN + Bağlantıyı paylaş), UYAP'ı aç, Pencereyi aç, Tema ▸ alt listesi, günlük, Çıkış. Durum `durum_yokla` ile gw.logged_in / giris_hatasi / uyap_proxy::SON_GECICI_HATA'dan (Windows'ta println arayüze ulaşmıyor); paylaşım başlayınca `oturumu_hemen_ac`. Durum renkleri temadan bağımsız (yeşil/sarı/kırmızı); "✓" glifi bu fontta kutucuk çıkıyor.

**Why:** Kullanıcı sade, az tıklamalı, hafif bir tepsi uygulaması istiyor.
**How to apply:** Programa yeni pencere/menü eklemeden önce web arayüzüne konabilir mi diye düşün; tepsi menüsünü kısa tut.
