---
name: eimza-5975-teshis
description: "Çoklu UYAP PIN girişi başarısızsa önce 5975 kapısına bak; 2026-11-14'te eksik ArkSignerService.exe yüzündendi, yeni \"Adalet E-imza\" tutuyor"
metadata:
  node_type: memory
  type: project
  originSessionId: e0725ac4-9963-414a-a02e-8caf295d0ac8
  modified: 2026-11-14T05:54:03.222Z
---

Çoklu UYAP (Python ve Rust) PIN girişinde `http://localhost:5975/api/v1/signature/getCertificates` adresine gider.
2026-11-14'te giriş "Could not reach the local e-imza client on port 5975" ile düştü. Sebep: eski UYAP e-imza 2.2.12
(ArkSigner) kurulumunda `ArkSignerService.exe` yoktu (Application günlüğü, ArkSignerManagementService: "Cannot start
ArkSignerService application"). Kullanıcı e-imza yükleyicisini yeniden kurdu; 5975'i artık "Adalet E-imza" süreci tutuyor.
Kartın takılı olması yetmiyordu, UYAP'ın kendi girişi de okuyucuyu bulamıyordu.

**Why:** Belirti "program bozulmuş" gibi görünüyor (tarayıcıda oturumsuz UYAP giriş sayfası), kullanıcı sinirlendi.
**How to apply:** Giriş başarısızsa önce `Get-NetTCPConnection -LocalPort 5975` ve getCertificates isteğine 200 dönüyor
mu bak; kurulumu kullanıcı yapar. Kontrol Ekranı başarısız girişte de "Paylaşım Aktif" gösteriyor (düzeltilmedi).
