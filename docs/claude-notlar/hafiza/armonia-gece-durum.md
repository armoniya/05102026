---
name: armonia-gece-durum
description: "Armonia Linux 2026-11-14 gece: Faz 1 kapandı, Faz 3 ilk katman, Faz 2 kasa Hyper-V doğrulandı; bellek kısıtı WSL derlemesini öldürüyor"
metadata:
  node_type: memory
  type: project
  originSessionId: e0725ac4-9963-414a-a02e-8caf295d0ac8
  modified: 2026-11-14T06:24:52.257Z
---

2026-11-14 gecesi kullanıcı "planlı devam et, sabaha kadar bitir" dedi. Durum ve kararlar `docs/ARMONIA_GECE_DURUM.md`
("Sabah özeti" bölümü) içinde; tek doğru plan `docs/ARMONIA_LINUX_YOL_HARITASI.md`. Dosyalar `armonia-linux/`
(derle_uyap.sh, iso_derle.sh [TEST=1], config/, sertlestirme/, kasa/, plymouth/, test/). Proje skill'i `iso-derle`.

Sonuç: Faz 1 kapandı (Hyper-V'de 8,1 sn açılış); Faz 3 ilk katman (nftables, lockdown, sandbox 1,9) doğrulandı;
Faz 2 kasa kodu + Plymouth teması hazır, düzeltilmiş sürümün Hyper-V testi yapılmadı. rust-review (3 orta) ve
cargo-audit (10 açık) bulguları için kod/Cargo.toml DEĞİŞTİRİLMEDİ — kullanıcı kararı bekliyor.

**Why:** Bu makine 7,9 GB RAM; WSL live-build/cargo derlemesi + Hyper-V + Claude Code birlikte belleği tüketiyor ve
Claude Code arka plan işini "low memory" ile öldürüyor (gece 2 kez). Öldürülen işi kullanıcı onayı olmadan yeniden
başlatma kuralı var.
**How to apply:** Sabah önce `docs/ARMONIA_GECE_DURUM.md` "Kullanıcı kararı gerekenler"i sor; derleme öncesi Hyper-V
kapalı + `wsl -u root sh -c 'echo 3 > /proc/sys/vm/drop_caches'`; kalıcı çözüm için `.wslconfig memory=` sınırı öner.
Bash heredoc `\\` → `\` yapıyor: ters eğik çizgili dosyaları Write ile yaz. `.wslconfig` (memory=3GB) yazıldı ama
yetmedi: elle deneyden sonra **ArmoniaTest VM'sini mutlaka kapat** (03:10'da açık unutulan VM derlemeyi öldürttü).
05:15: kasa + PostgreSQL (kasada, Unix soketi) + AppArmor (complain) Hyper-V'de doğrulandı; üretim ISO 12,9 sn.
Kararlar `docs/ARMONIA_GECE_DURUM.md` "SABAH ÖZETİ (güncel)" + "Kullanıcı kararı gerekenler (güncel)": tarayıcı (imajda
yok!), USBGuard A/B/C, PG zorunluluğu (uygulama kodu), AppArmor enforce. Parola kuralı kararlaştı (≥10, 4 sınıf, ASCII).
**Devam:** kullanıcı "yol haritasından devam et" derse `docs/ARMONIA_LINUX_YOL_HARITASI.md` → "▶ DEVAM NOKTASI"
bölümü (D.3 soruları, D.4 iş listesi; 1. madde Linux'ta e-imza). Gemini taslakları `docs/armonia_taslaklar/`.
14 Kasım sabah kararları: tarayıcı = Debian chromium AYRI birimde (armonia-tarayici.socket/@.service, xdg-open→nc→soket;
kiosk sandbox gevşetilmedi), USBGuard A (match-all; kamera+mikrofon E-Duruşma için izinli), PipeWire eklendi.
guvenlik-denetci K1/K2/Y1-Y3/EK bulguları uygulandı; tarayıcı IPAddressDeny=any → E-Duruşma medya sunucusu
gerçek testte açılacak. USB kuralları yalnız gerçek donanımda sınanabilir (Hyper-V'de USB yok).
Kullanıcı tercihi: gece işlerinde agent/skill/MCP kullan (gemini taslak → guvenlik-denetci → Hyper-V). İlgili: [[gemini-agy-ajan]],
[[yarin-sabah-hatirlatma]].
