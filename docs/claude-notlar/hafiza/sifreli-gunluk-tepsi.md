---
name: sifreli-gunluk-tepsi
description: "Masaüstü program (14.11): şifreli günlük (gizli anahtar yeri!), tepsi, hata kodları, canlı sunucu Python"
metadata:
  node_type: memory
  type: project
  originSessionId: c01c5ebc-9e1e-4a69-901f-6e84975a9683
  modified: 2026-11-14T14:11:31.335Z
---

2026-11-14 masaüstü (ruyap uyap_app) yeniden yapılandı: "Bağlantı Al" + home_client modülü/bin'i silindi (yedek yalnız o günkü scratchpad'deydi), Kullanıcılar sekmesi kalktı (yerine "Kullanıcıları web panelinde yönet" → {api}/ofis), günlük kutusu yerine tek satır durum (src/hata_kodlari.rs: E-1xx e-imza, E-2xx UYAP, E-3xx sunucu, E-4xx yerel, E-900).

- **Şifreli günlük:** src/sifreli_gunluk.rs → %LOCALAPPDATA%/CokluUyap/gunluk/*.cul (P-256 ECDH + AES-256-GCM, açık anahtar kodda gömülü). **GİZLİ ANAHTAR: `C:\Users\WİN10\CokluUyap-Gizli\gunluk_gizli_anahtar.pem`** — repoda/sunucuda yok; kaybolursa günlükler okunamaz (kullanıcıya yedekle dendi). Çözme: `python -I -X utf8 ruyap/araclar/gunluk_coz.py coz <pem> <paket.zip|.cul>`.
- **Paylaşım:** tepsi menüsü → Masaüstüne zip, ya da `POST /destek/gunluk` (vendor_deploy/vendor_server.py; R2_* env yoksa Render'da 503 → dosyaya kaydet).
- **Tepsi:** src/tepsi.rs (tray-icon 0.26, yalnız Windows). Gizleme SW_HIDE DEĞİL (gizlide eframe update durur); ekran dışı (-32000) + WS_EX_TOOLWINDOW — ölçüldü, update ~12 kare/sn sürüyor.
- **Windows stdout yakalama:** CreatePipe+SetStdHandle (println → şifreli günlük + durum tespiti); exe artık windows_subsystem (konsol yok, release'de).
- **Canlı sunucu Python** (vendor_deploy/, Render Dockerfile). @ofis düzeltmesi: accounts.resolve_username + _login_id; anahtarlar yeniden adlandırılmaz (zs_users.account_username bağlı). Yayını kullanıcı yapar.

**How to apply:** Günlük biçimini değiştirirsen gunluk_coz.py'yi de güncelle. Çerçevesiz pencere stil düzeltmesi + kendi büyüt/boyutlandırma için bkz. uyap_app.rs `cercevesiz_pencere_stilini_duzelt`, `dash_buyut_degistir`. İlgili: [[uyap-tasarimindan-kopus]], [[armonia-logo]], [[uyapmasa-veri-modeli]].
