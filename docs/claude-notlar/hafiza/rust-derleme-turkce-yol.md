---
name: rust-derleme-turkce-yol
description: "ruyap (Rust port) Windows'ta GNU araç zinciriyle derlenmiyor; ev klasöründeki İ karakteri sebep"
metadata:
  node_type: memory
  type: project
  originSessionId: e0725ac4-9963-414a-a02e-8caf295d0ac8
  modified: 2026-11-13T16:41:30.661Z
---

`Uyap Haricen Giriş/ruyap/` = projenin %100 Rust portu (56 dosya, ~46.5k satır, egui/eframe GUI; Ring-0/eBPF YOK — plan dokümanlarındaki Iced ve çekirdek zırhı gerçeklenmemiş). `cargo check` bu makinede portun kendi koduna sıra gelmeden bağımlılıklarda patlıyor.

**Kök neden:** Windows kullanıcı klasörü `C:\Users\WİN10` içindeki Türkçe `İ`. GNU araç zincirinin C-yardımcıları (`dlltool`, `ar`, mingw `gcc`/`ld`) bu karaktere takılıyor. Üç katman da `İ` içeriyor: kaynak+target, CARGO_HOME (`~/.cargo`), RUSTUP_HOME (`~/.rustup` → sysroot). Sırasıyla `getrandom`(dlltool), `ring`(ar), `serde_derive`(mingw link) patlıyor. Kaynak/target/CARGO_HOME'u ASCII yola taşımak ilk ikisini çözdü; RUSTUP_HOME sysroot kaldığı için serde_derive linking yine patladı.

**Why:** Derleme hatası sanılanın aksine kodda değil; bu ortama özgü. Linux hedefinde bu sorun YOK (gcc/ld UTF-8 yolları yerel kabul eder, dlltool/nasm dansı yok).

**ÇÖZÜLDÜ (2026-11-13):** Profil klasörü değiştirilmeden düzeltildi. Üç kalıcı User ortam değişkeni kuruldu: `CARGO_HOME=C:\dev\cargo`, `RUSTUP_HOME=C:\dev\rustup`, `CARGO_TARGET_DIR=C:\dev\target`. Mevcut `~/.cargo` registry ve `~/.rustup` toolchain bu ASCII yollara taşındı/kopyalandı. Sonuç: `cargo check` yerinde (kaynak hâlâ `...\ruyap` Türkçe yolunda) **0 hata** ile derlendi (`Compiling ruyap v26.11.13 ... Finished in 3m52s`, 30 kozmetik uyarı). Kaynak yolu Türkçe kalabilir.

**How to apply:** Yeni açılan shell'ler bu User değişkenlerini otomatik alır (zaten açık shell'leri yeniden başlat). Eski `C:\Users\WİN10\.cargo` / `.rustup` artık kullanılmıyor, silinebilir (~1.7 GB). Geçici `C:\cuyap_t`, `C:\cuyap_t2` de silinebilir. Alternatif: MSVC araç zinciri (VS Build Tools gerekir). İlgili: [[duzeltmeyi-dogrula]] (iddiaya değil gerçeğe karşı test), [[kaynaktan-calisiyor]].
