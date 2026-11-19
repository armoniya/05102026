---
name: kaynaktan-calisiyor
description: "Kullanıcı bazen kurulu exe yerine kaynaktan `python uyap_app.py` çalıştırıyor; 8800'ü o tutuyorsa exe kurulumu etkisiz"
metadata:
  node_type: memory
  type: project
  originSessionId: ede7a23b-bc0e-4d01-9197-5354d451a697
  modified: 2026-11-11T16:15:07.916Z
---

2026-11-11: Port 127.0.0.1:8800'ü kurulu CokluUyap.exe değil, kullanıcının kaynaktan başlattığı `python uyap_app.py` (Uyap Haricen Giriş/, Django uyap_gecit 8000'de) tutuyordu. Exe'yi derleyip kurmak hiçbir şey değiştirmedi; telefon (cokluuyap.com) de bu süreçten paylaşılıyordu. Enjekte dosyaları (enjekte/*.js, tema.css) süreç açılışında bir kez okunur.

**Why:** "kuruldu" dediğim değişiklikler sayfaya gelmedi; yeni exe 8800'e bağlanamadı.
**How to apply:** Kurduktan sonra `Get-NetTCPConnection -LocalPort 8800 -State Listen` ile sahibini kontrol et. Sahibi python uyap_app.py ise o süreci kendin öldürme (paylaşımı/oturumu taşıyor). Kullanıcıdan yeniden başlatmasını iste; exe'yi de başlatma. İlgili: [[duzeltmeyi-dogrula]]
