---
name: mobil-arayuz
description: "Çoklu UYAP mobil arayüz istekleri (enjekte/mobil.js, mobil.css) — tam sayfa menüler, dosya penceresinde yuvarlak sekme düğmesi"
metadata:
  node_type: memory
  type: project
  originSessionId: d409ce03-dcc9-4986-ba11-7e073b6da77e
  modified: 2026-11-13T14:09:38.492Z
---

2026-11-11 kullanıcı istekleri (oturum 5 saat sınırında yarım kaldı, 2026-11-13'te tamamlandı):
- Sol menü ve sağ İş Listesi telefonda tüm sayfayı kaplar (simetri); kapatma görev çubuğundaki ☰ / ✓ ile.
- Dosya penceresinde UYAP sekme şeridi gizli; sol altta temaya uygun yuvarlak "sekmeler" düğmesi, dokununca sekmeler yuvarlak düğmeler (speed-dial). Seçim React prop'u onSelectionChanged ile anında (main.js AdaletTabPanel denetimli).
- Önceki oturumda açık kalan konu: kartın "küçült (–)" düğmesinin tıklamaya tepki vermemesi incelenmemişti.

**Why:** Kullanıcı mobilde sıkışan / simetri dışı görünümden rahatsız.
**How to apply:** Yeni mobil özellikler mobil.js/mobil.css'e; değişiklik sonrası ruyap/uyap_core/enjekte kopyası da eşitlenir. Test: scratchpad'te sahte DOM + Playwright (390×664, viewport meta şart). İlgili: [[tek-arayuz]], [[kaynaktan-calisiyor]]
