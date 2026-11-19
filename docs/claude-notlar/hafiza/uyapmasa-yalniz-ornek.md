---
name: uyapmasa-yalniz-ornek
description: "UyapMasa yalnız örnek model; özellikler ikinci program olmadan ruyap'ta (Çoklu UYAP) çalışmalı"
metadata:
  node_type: memory
  type: feedback
  originSessionId: c01c5ebc-9e1e-4a69-901f-6e84975a9683
  modified: 2026-11-15T08:50:51.238Z
---

UyapMasa'nın (Python/Django, models/icra_models) veri modeli ve mantığı yalnız ÖRNEK alınmak için anılıyor. İstenen özellikler (İş Listesi'ne otomatik iş açma, takip aşaması, kesinleşme/tebliğ takibi vb.) ikinci bir program kurulu olmadan, ana program ruyap içinde çalışmalı.

**Why:** 15.11.2026'da DurumOlayi→İş Listesi ve takip aşamasını UyapMasa'ya yazdım; kullanıcı "uyapmasa modeli örnek alman için söylediğim bir şey. İkinci program olmadan çalışması gerekiyor" dedi ve plan moduna geçilmesini istedi.

**How to apply:** "UyapMasa'daki gibi" denince kodu UyapMasa'ya ekleme; mantığı ruyap'a (Rust) taşı. Büyük işlerde önce plan modu, kod yazmadan onay al. İlgili: [[uyapmasa-veri-modeli]], [[ruyap-port-guncel]].
