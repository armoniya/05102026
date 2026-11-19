---
name: kod-imzalama
description: "Dağıtılan CokluUyapKur.msi imzasız; Defender yanlış pozitif veriyor, kod imzalama sertifikası henüz yok"
metadata:
  node_type: memory
  type: project
  originSessionId: db9ad7ee-0efd-4c90-83f3-d6e7b7ffd34c
  modified: 2026-11-10T17:29:35.312Z
---

2026-10-26: Kullanıcının arkadaşında Windows Defender, cokluuyap.com/indir'den inen imzasız MSI'yı sildi. Kullanıcının şirketi yok; sertifika (bireysel OV, ör. Certum SimplySign) henüz alınmadı.
Kodda hazırlık yapıldı: msi_hazirla.py UYAP_IMZA_SHA1 / UYAP_IMZA_PFX / UYAP_IMZA_DLIB ile imzalıyor; cokluuyap_app.spec'e sürüm bilgisi eklendi (CompanyName "Çoklu UYAP" — sertifika gelince sertifikadaki adla aynı yapılmalı).
Aynı gün kullanıcı kararı: e-imza PIN'i diske kaydedilmez, her bağlantıda yeniden girilir.

**Why:** Kullanıcı "ciddi güven sorunu" yaşayacağını söyledi; güvenilirlik öncelik.
**How to apply:** Yeni sürüm yayınlarken imza durumunu sor; sertifika gelince CompanyName'i güncelle ve imzalı derlemeyi kur. PIN'i kalıcı saklayan bir özellik önerme.

2026-10-28: Sertifika ertelendi; 26.10.28 MSI imzasız yayınlandı (yerel Defender taraması temiz). Sonraki imzasız önlem: PyInstaller bootloader'ı kaynaktan derlemek — taşınabilir MinGW (araclar/mingw) ve pyinstaller 6.21.0 kaynağı (araclar/pyi_src) indirildi, derleme adımı izin sınıflandırıcısınca engellendi; kullanıcı onayı bekleniyor.
Yayın akışı: PyInstaller spec → msi_hazirla.py → MSI'ı vendor_deploy/downloads'a kopyala + zs*.py/webapp değişenleri kopyala → vendor_deploy ayrı depo (armoniya/uyap-vendor) push → Render otomatik deploy.

2026-11-04: 26.11.4 light MSI imzasız yayınlandı (Defender temiz). Kullanıcı kararı: /indir ana düğmesi light CokluUyap (MASA_YAYINDA=False), UyapMasa değil.
2026-11-09: 26.11.9 (UDF->PDF yeniden yazımı) imzasız yayınlandı, Defender temiz. Kullanıcı: "UyapMasa ile çalışmıyoruz şimdilik, Uyap Haricen Giriş klasöründen ilerliyoruz." Build venv depo kökünde (cokluuyap/.venv), "Uyap Haricen Giriş/.venv" değil. İndirme doğrulama: https://www.cokluuyap.com/indir/dosya (Range 0-0 ile boyut kontrolü); Render push sonrası ~1 dk.
2026-11-10: 26.11.10 (kopyalama/tek kutu/kısayollar) imzasız yayınlandı, Defender temiz, 73.3 MB. Kullanıcı "PyInstaller değil" dedi ama sorulunca mevcut WiX MSI (içinde PyInstaller exe) yolunu seçti — "PyInstaller'sız MSI" isteği gelirse önce sor.
Derleme tuzağı: cokluuyap_app.spec uyap_core/static_cache'i pakete katıyor; UYAP'ın ana JS'i (~27-34 MB) her sürümde yeni dosya olarak birikiyor → derlemeden önce eski sürümleri (aynı content-type, eski last-modified) geçici dışarı al, sonra geri koy. Yoksa MSI 100 MB GitHub sınırına dayanır.
