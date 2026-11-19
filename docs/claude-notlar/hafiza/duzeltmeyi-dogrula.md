---
name: duzeltmeyi-dogrula
description: "UYAP klonu düzeltmeleri gerçek main.js yapısına karşı test edilmeden \"çözüldü\" denmemeli; kurulu exe de güncellenmeli"
metadata:
  node_type: memory
  type: feedback
  originSessionId: ede7a23b-bc0e-4d01-9197-5354d451a697
  modified: 2026-11-11T07:09:52.672Z
---

2026-11-10'da "belgede seçili metni kopyala" düzeltmesi 4 kez denendi ama çalışmadı; kullanıcı 2026-11-11'de sinirlendi ("dört defa söyledim, hala çözmedin").
Kök neden: görüntüleyicinin zustand deposu `useStore` prop'u olarak bir İŞLEV, kod yalnız nesneleri tarıyordu. Tahmine dayalı yazılmış, UYAP main.js'te (static_cache) doğrulanmamıştı.

**Why:** Kullanıcı aynı hatayı tekrar tekrar bildirmekten bıkıyor; çalışmayan düzeltme güveni zedeliyor.
**How to apply:** Sayfa içi kancaları önce static_cache'teki UYAP main.js'teki gerçek koda bakarak yaz, Node'da sahte fiber/DOM ile dene; kullanıcı kurulu CokluUyap.exe (%LOCALAPPDATA%\CokluUyap) çalıştırıyor — derleyip oraya kurmadan düzeltme ona ulaşmaz. Bkz. [[kod-imzalama]].
