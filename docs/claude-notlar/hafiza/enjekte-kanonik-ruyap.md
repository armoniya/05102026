---
name: enjekte-kanonik-ruyap
description: Python rafa kalktı (2026-11-14); arayüz dosyalarının asıl kopyası artık ruyap/uyap_core/enjekte — çalışan exe onu okur
metadata:
  type: project
---

2026-11-14: Kullanıcı Python "Uyap Haricen Giriş" tarafını rafa kaldırdı, iş Rust (ruyap) üzerinden sürüyor. `ruyap/CokluUyap.exe` exe klasöründeki `uyap_core/enjekte`'yi ÖNCE okur (`_enjekte_klasoru`), üst dizindeki `../uyap_core/enjekte` değil. İki kopya ayrışmıştı (ruyap kopyası 13 Kasım düzeltmelerinden eskiydi); eşitlendi ve o günden beri yalnız **ruyap/uyap_core/enjekte** düzenleniyor. Dosyalar açılışta bir kez okunur (Lazy) → değişiklik için exe yeniden başlatılır, derleme gerekmez.

UYAP uyarıları SweetAlert2 11.26.24 (showAdaletAlert/showAdaletNotify); pencereler DevExtreme Popup. Görünüm/animasyon değişiklikleri tema.css'te ("PENCERE GEÇİŞ EFEKTLERİ KAPALI", "UYARI PENCERELERİ" bölümleri). UYAP paketi: ruyap/uyap_core/static_cache (en büyük .js) — dev tek satır, grep'te `.{300}` kullanma, python find ile ara.

**How to apply:** [[ruyap-port-guncel]]'deki "enjekte paylaşımlı, taşıma gerekmez" notu artık geçersiz: üst dizine değil ruyap kopyasına yaz. Gemini taslaklarını (agy) tarayıcıda test etmeden ekleme. İlgili: [[gemini-agy-ajan]], [[duzeltmeyi-dogrula]].
