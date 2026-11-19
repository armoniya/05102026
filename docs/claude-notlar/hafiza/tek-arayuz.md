---
name: tek-arayuz
description: "Ürün planı tek arayüz — UYAP'ın kendi arayüzü + Çoklu UYAP eklentileri; webapp'te UyapMasa/Geçit ayrı ekranları yok"
metadata:
  node_type: memory
  type: project
  originSessionId: ede7a23b-bc0e-4d01-9197-5354d451a697
  modified: 2026-11-11T17:03:46.488Z
---

2026-11-11 kullanıcı: Webapp'te önceden UYAP arayüzü + UyapMasa arayüzü + panel/geçit gibi birden çok ekran düşünülmüştü; **şimdiki plan tek arayüz**: üzerinde çalıştığımız, UYAP'ın kendi ekranı üzerine enjekte edilen Çoklu UYAP tasarımı (enjekte/*.js, tema.css). Girişten sonraki "Paylaşılan bağlantı aktif / UYAP'ı Aç / Geçidi Aç" ara ekranı kaldırıldı; tünel hazır olunca UYAP kendiliğinden açılır.

**Why:** Kullanıcı ara ekranları gereksiz buluyor; tek, kendi tasarımımız olan arayüz isteniyor.
**How to apply:** Ayrı pano/geçit/UyapMasa ekranı önerme; yeni modüller (tebligat, 21/2 vb.) bu arayüzün içine (çekmece/Araçlar, görev çubuğu) eklenir. İlgili: [[uyap-karti-korunur]], [[uyap-arayuz-sadelestirme]]
