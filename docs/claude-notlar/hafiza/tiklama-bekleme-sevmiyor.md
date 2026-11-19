---
name: tiklama-bekleme-sevmiyor
description: Kullanıcı UYAP klonunda gereksiz tık ve beklemeyi sevmiyor; seçimler fare taklidiyle değil anında olmalı
metadata:
  node_type: memory
  type: feedback
  originSessionId: 74e63287-cac0-40fe-9bf4-73e03a794efb
  modified: 2026-11-10T16:12:25.088Z
---

UYAP klon ekranlarına (uyap_proxy.py enjekte JS) eklenen kolaylıklarda seçimler **anında** olmalı: DevExtreme kutularını fare olayı taklidiyle tek tek açıp seçmek "yine bekliyoruz" diye geri çevrildi. Doğrudan React form durumu (formData.setters…) / DevExtreme instance ile değer verilmeli. İstenmeden açılan dropdown da istenmiyor (kısayol sonrası Mahkeme açılmasın, sadece odak).

**Why:** Kullanıcı: "Gereksiz tıklamaları hiç sevmiyorum", "yine fare tıklaması gibi; yani yine bekliyoruz".
**How to apply:** UYAP ekranına otomasyon eklerken önce UYAP chunk'larından (avukat.uyap.gov.tr/static/js) bileşenin durum işlevlerini bul; DevExtreme'in oluşturduğu input'larda __reactFiber yoktur, React'in çizdiği ataya çık.
