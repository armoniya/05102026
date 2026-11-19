---
name: uyap-tasarimindan-kopus
description: "TEMEL İLKE — UYAP'ın hiçbir tasarım/tema öğesi kalmayacak (gri tonlar dahil); kendi arayüzümüz; \"UYAP'ın teması/CSS'i\" diye konuşma"
metadata:
  node_type: memory
  type: feedback
  originSessionId: c01c5ebc-9e1e-4a69-901f-6e84975a9683
  modified: 2026-11-14T13:02:40.706Z
---

2026-11-14, kullanıcı defalarca ve sertçe vurguladı: proje bittiğinde UYAP'ın HİÇBİR tasarım ve tema öğesi kullanılmamış olacak — renkler, gri tonlamalar, zemin/fon renkleri, yazı tipleri, simgeler, logo, düzen, pencere biçimleri, hata mesajları, yükleme animasyonları. Fikri mülkiyete ve tasarımcının emeğine saygı. İlke kullanıcının dosyasına yazıldı: `Uyap Haricen Giriş/Rust Program İyileştirme..md` en üstte "TEMEL İLKE".

**Why:** Kullanıcı "temayı UYAP'tan uzaklaştır" dedikçe ben UYAP'ın gri fonlarını yerinde bıraktım (ör. dosya durumu "Haricen Tahsil" kutusunun gri arka planı) ve yanıtlarımda "UYAP'ın teması / UYAP'ın CSS'i / UYAP'ın tema.css'i" diye konuştum. İkisi de kullanıcıyı çok rahatsız etti.

**İstisna (kullanıcı kararı 2026-11-14 akşam):** UYAP'ın lacivert ("UYAP") ve koyu ("Koyu") paletleri SEÇENEK olarak kalır (web tema.js + masaüstü uyap_theme TEMALAR); varsayılan Armonia. Bu iki tema seçiliyken UYAP görünümü kullanılır — kaldırma.

**How to apply:**
- Yanıtlarda arayüzden "bizim arayüzümüz / tasarımımız" diye söz et; tema.css'i "UYAP'ın teması" diye anlatma.
- Herhangi bir ekranda iş yaparken gri/UYAP'tan kalan zemin, renk, simge gördüğünde bizim paletimize çevir; "nötr gri" fon bırakma. Palet: Armonia adaçayı #7D9A7D, koyu #1F2A24, kırık beyaz #FBFCF6 ([[armonia-logo]]).
- Yeni özellik = kendi ekranımız; UYAP sayfasına yama yalnız geçiş dönemi aracı. Ama işlev kaybı yok ([[uyap-karti-korunur]]).
