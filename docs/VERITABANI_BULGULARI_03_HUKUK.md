# Veritabanı Bulguları — Hukuk

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## 1. Taranan yargı birimleri (12/12 — TAMAMI)

| Birim | Dosya var mı | Not |
|---|---|---|
| Asliye Hukuk Mahkemesi | Evet | Standart `HukukDavaDetay` yapısı |
| Asliye Ticaret Mahkemesi | Evet | Standart yapı |
| Aile Mahkemesi | Evet | Hem Hukuk Dava Dosyası hem **Hukuk Değişik İş Dosyası** görüldü |
| Sulh Hukuk Mahkemesi | Evet | "İlgili Dosyalar"da bir Arabuluculuk dosyasına çapraz referans görüldü |
| İcra Hukuk Mahkemesi | Evet | "İlgili Dosyalar"da bir İcra dosyasına çapraz referans görüldü |
| İş Mahkemesi | Evet | Standart yapı |
| Bölge Adliye Mah. Hukuk Dairesi (istinaf) | Evet | "Dava Açılış Türü: İstinaf Başvurusu" (yeni değer) — **Safahat sekmesi YOK** (Ceza'daki istinaf deseniyle tutarlı) |
| BAM Hukuk Dairesi (İlk Derece) | Hayır (0 sonuç) | |
| Fikrî ve Sınai Haklar Hukuk Mahkemesi | Hayır (0 sonuç) | |
| Kadastro Mahkemesi | Hayır (0 sonuç) | |
| Kadastro Mahkemesi (MÜS) | Hayır (0 sonuç) | |
| Tüketici Mahkemesi | Hayır (0 sonuç) | |

## 2. Dosya türleri — ÖNEMLİ BULGU: "Hukuk Değişik İş Dosyası" yapısal olarak BOŞ

- **Hukuk Dava Dosyası** (`dosyaTurKod=15`) — zengin `HukukDavaDetay` yapısı:
  Dava Açılış Türü, Başvuruya Bırakılma Sayısı/Tarihi, Dava Türleri, İlgili
  Dosyalar/Davalar/Seri Davalar, Birleşen Dosyalar, Dosya Durumu, Duruşma
  Tarihi, Keşif Tarihi, ... Tüm sekmeler (Taraf, Evrak, Safahat, Evrak
  Gönderme, Ödeme, Tah./Red. Bilgileri, Kişisel Notlar) mevcut.
- **Hukuk Değişik İş Dosyası** (`dosyaTurKod=14`, Aile Mahkemesi'nde
  görüldü, "Karara Çıkmış") — `dosya_islem_turleri_sorgula_brd.ajx` yanıtı
  YALNIZ `{"14":"evrak_gonderme,kisisel_not_alani,odeme"}` döndürüyor.
  **Ne Dosya Bilgileri, ne Taraf Bilgileri, ne Evrak, ne Safahat, ne
  Tah./Red. — HİÇBİRİ YOK.** Bu, uydurma değil, UYAP'ın kendi cevabı.

## 3. Taraf rolleri

Görülenler: Davacı, Davalı — hepsi zaten var olan `DosyaTaraf.Rol`
değerleriyle örtüşüyor, yeni rol bulunmadı.

## 4. Safahat / Tahsilat-Harç-Reddiyat

BAĞIMSIZ olarak Hukuk'ta da doğrulandı (bkz.
[00_GENEL.md §4-5](VERITABANI_BULGULARI_00_GENEL.md)) — Asliye Hukuk
Mahkemesi, dosya 2026/117 (Çeşme 2. Asliye Hukuk Mahkemesi). Ücret/kota
uyarısı ÇIKMADI (İcra'daki gibi).

## 5. Kalan iş

- BAM Hukuk Dairesi, Fikrî-Sınai Hukuk, Kadastro (2 tür), Tüketici
  Mahkemesi'nde kullanıcının şu an hiç dosyası yok — ileride dosya
  açılırsa yeniden taranmalı.
- **"Keşif Tarihi" / "Ön İnceleme Tarihi" / "Başvuruya Bırakılma Sayısı"
  — UI'de GERÇEK alanlar, JSON key'leri HENÜZ görülmedi (2026-09-22,
  canlı doğrulama).** `dosyaAyrintiBilgileri_brd.ajx` yanıtı `fetch`/`XHR`
  hook'lanarak 2 farklı Hukuk Dava Dosyasında (İzmir 27. Asliye Hukuk
  Mahkemesi 2024/113 — İstinafta; Çeşme 2. Asliye Hukuk Mahkemesi 2026/117
  — Açık) ham olarak yakalandı: `{"davaAcilisTuru","davaTurleriStr",
  "ilgiliDosyaListesiStr","ilgiliDavaListesiStr","ilgiliSeriDavaListesiStr",
  "birlesenDosyaListStr","durusmaTarihi","davaAcilisTuruStr",
  "basvuruyaBirakilmaTarihiStr","durusmaTarihiStr","dosyaDurumu",
  "dosyaDurumuInt","sonuc","id","isCompressed","htmlHataKodList","isMock"}`.
  Bu 2 örnekte "Keşif Tarihi"/"Ön İnceleme Tarihi"/"Başvuruya Bırakılma
  Sayısı" için HİÇBİR anahtar yoktu (UI'de sabit şablon olarak boş
  gösteriliyor olabilir — dosyada hiç keşif/ön inceleme yapılmamış).
  `HukukDavaDetay`'e TAHMİNİ bir alan (`kesif_tarihi` vb.) EKLENMEDİ —
  gerçek JSON key adını görmek için keşif/ön inceleme fiilen YAPILMIŞ bir
  Hukuk Dava Dosyası bulunup ayrıca doğrulanmalı.
