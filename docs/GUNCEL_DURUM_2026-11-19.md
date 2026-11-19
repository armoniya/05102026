# Güncel Durum — 19.11.2026

> Bu dosya, proje Raspberry Pi'ye taşınırken tüm notlardan derlendi. Ayrıntı için aşağıdaki kaynaklara bakın.
> Kullanıcıyla **Türkçe** konuşulur.

## Kaynaklar (önce bunları okuyun)

| Konu | Dosya |
|---|---|
| Windows Rust uygulaması (ruyap), icra veritabanı, İş Listesi — en güncel | `docs/18.11.2026 durum ve kalan işler.md` |
| Aynı konunun ayrıntılı günlüğü | `docs/16.11.2026 yapılacaklar.md` |
| Armonia Linux planı — "▶ DEVAM NOKTASI" bölümü | `docs/ARMONIA_LINUX_YOL_HARITASI.md` |
| Armonia ilk gerçek donanım denemesi | `docs/ARMONIA_GERCEK_DONANIM_SORUNLARI.md` |
| Armonia gece günlüğü (14.11) | `docs/ARMONIA_GECE_DURUM.md` |
| Gemini taslakları (AppArmor, USBGuard, Faz 3, tarayıcı) | `docs/armonia_taslaklar/` |
| Temel tasarım ilkesi + Rust iyileştirme listesi | `Uyap Haricen Giriş/Rust Program İyileştirme..md` |
| Claude'un kalıcı notları (hafıza) | `docs/claude-notlar/hafiza/` (dizin: `MEMORY.md`) |
| Onaylı plan dosyaları | `docs/claude-notlar/planlar/` |
| Proje ajanları/skill'leri (guvenlik-denetci, gemini, iso-derle) | `docs/claude-notlar/claude-yerel/` |

## Projenin parçaları

- **`Uyap Haricen Giriş/ruyap/`** — ana ürün. Rust ile yazılmış Çoklu UYAP (egui masaüstü + UYAP vekil sunucusu,
  port 8800). Arayüz eklentileri `ruyap/uyap_core/enjekte/` altında. **Asıl kopya burası.** Üst dizindeki
  `Uyap Haricen Giriş/uyap_core/enjekte` Python dönemine ait; artık düzenlenmiyor.
- **`Uyap Haricen Giriş/` (Python)** — eski sürüm, rafta. Canlı web sunucusu (`vendor_deploy`, Render) hâlâ Python.
- **`UyapMasa/`, `models/icra_models/`** — yalnız örnek model. Özellikler ikinci program olmadan ruyap'ta çalışmalı.
- **`armonia-linux/`** — ruyap'ı sertleştirilmiş, canlı (live) bir Debian imajına koyan proje. Live-build yapılandırması,
  sertleştirme, LUKS kasa, Plymouth teması ve testler burada. **Durum: BEKLEMEDE** (kullanıcı Windows uygulamasını
  öne aldı).

## Armonia Linux — özet

- **Bitti:** Faz 1 (Hyper-V'de 8–15 sn açılış), Faz 3 ilk katman (nftables, lockdown, sandbox), Faz 2 kasa
  (LUKS + kasada PostgreSQL + AppArmor complain; Hyper-V'de doğrulandı), Chromium ayrı birimde, USBGuard A, PipeWire,
  e-imza katmanı (`adalet-eimza-tray` .deb, 5975, Hyper-V'de getCertificates → 200).
- **İlk gerçek donanım denemesi (14.11):** UYAP girişi kurulmadı. Kök nedenler bulundu ve Rust'ta düzeltildi
  (sertifika seçimi stdin'i bekliyordu; geçici hatada giriş kapısı kilitleniyordu). Bu düzeltmelerle
  **uyap_app ve ISO yeniden derlenmedi, denenmedi.**
- **Sıradaki iş (yol haritası §D.4):**
  1. Linux'ta e-imza girişi: pcscd + AKİS PKCS#11, gerçek kartla deneme.
  2. Wi-Fi.
  3. UEFI açılış.
  4. Kasa ilk-kurulum akışı.
  5. Gerçek GPU.
  6. USBGuard saha ayarı.
  7. E-Duruşma için tarayıcıya ağ izni.
- **Açık kullanıcı kararları:** PostgreSQL zorunluluğu, kasa kurtarma anahtarı, otomatik kilit süresi,
  AppArmor enforce, rust-review (3 orta bulgu) ve cargo-audit (10 uyarı) bulguları.

## Windows Rust uygulaması (ruyap) — özet (18.11 akşamı)

- İş Listesi büro veritabanına taşındı (L1–L7: şema, kimlik, bildirim, otomatik işler). Bulut kopyası silindi.
- İcra veritabanı: liste ekranı, çoklu seçim, UYAP'tan güncelleme (`src/icra_guncelle.rs`), canlı liste.
- Testler: birim 65/65; uçtan uca ve Playwright testleri geçti.
- **Sıradaki işler:**
  1. Tebligat bilgileri (`barkod_sorgu.py` → Rust).
  2. Mernis + 21/2. Gönderim ücretli; her gönderim ayrı onay ister.
  3. Borçlu TCKN doldurma gece işi (onay bekliyor).
  4. Varsayılan birim ayarı web görünümüne taşınacak.

## Raspberry Pi'de çalışırken bilinmesi gerekenler

- **İşlemci farkı:** Pi ARM64, Armonia imajı ve e-imza paketi **amd64**. Pi'de kod ve yapılandırma düzenlenir.
  Şunlar Pi'de yapılamaz ya da pratik değildir:
  - ISO derleme (live-build) ve Hyper-V testi,
  - amd64 `uyap_app` derleme,
  - gerçek UYAP/e-imza girişi.
  Bunlar Windows makinede (WSL Debian 13 + Hyper-V) yapılır.
- `ruyap` Pi'de **ARM için** derlenip birim testleri çalıştırılabilir:
  `cargo test --lib` (Linux'ta Türkçe yol sorunu yok).
  Windows'a özgü parçalar (tepsi, `paylas_win.rs`) Linux'ta derlenmez; `cfg` korumaları vardır.
- **Depoda olmayan dosya:** `armonia-linux/config/packages.chroot/adalet-eimza-tray_1.0.17_amd64.deb` (70 MB,
  `.gitignore`'da). ISO derlenecekse resmî kaynaktan yeniden indirilir:
  `https://cdn.uyap.gov.tr/framework/public/adalet-eimza-tray_1.0.17_amd64.deb`
  (md5 `a1a4b3c3099df599c2d5193f2259c1aa`).
- **Notlardaki Windows yolları** (`C:\dev\...`, `%LOCALAPPDATA%`, `C:\Users\WİN10\...`) Windows makineye aittir;
  Pi'de karşılıkları yoktur.
- **`agy` (Gemini CLI) Pi'de kurulu değil.** `gemini` ajanı ve `gemini-ajan` skill'i orada çalışmaz.
  `guvenlik-denetci` ajanı çalışır.

## Değişmez kurallar

- Buluta müvekkil verisi çıkmaz. UYAP'tan robotla veri çekilmez. PIN saklanmaz, Gemini'ye gönderilmez.
- Commit, push, exe kopyalama ve yayın: her biri ayrı kullanıcı onayı ister.
- UYAP'ın hiçbir tasarım öğesi kullanılmaz (bkz. hafıza: `uyap-tasarimindan-kopus.md`).
  İstisna: "UYAP" ve "Koyu" temaları seçenek olarak kalır.
- Büyük işlerde önce plan yapılır, kod yazmadan önce onay alınır.
