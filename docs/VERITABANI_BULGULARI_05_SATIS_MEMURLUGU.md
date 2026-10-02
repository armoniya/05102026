# Veritabanı Bulguları — Satış Memurluğu

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## 1. Yargı Birimi

Tek seçenek: **SATIŞ MEMURLUĞU**.

## 2. Dosya Bilgileri — yapısal olarak boş

Test edilen dosyada ("Ort. Gid. Satış Dosyası", 2025/46, Yunak (Sulh Hukuk
Mah.) Satış Memurluğu) `dosyaAyrintiBilgileri_brd.ajx` ek alan
DÖNDÜRMEDİ (`sonuc:false`). Bu durum **hem Açık hem Kapalı** dosya
durumunda test edildi — ikisinde de aynı (boş) sonuç. `SatisMemurlugu
Detay` gibi bir model YAZILMADI, gerek yok.

## 3. Taraf rolleri — burada yeni roller bulundu

- **Hissedar** (zaten vardı)
- **Vasi** (YENİ — kısıtlının yasal temsilcisi)
- **Haciz Alacaklısı** (YENİ — "Alacaklı"dan ayrı bir rol)
- **Üçüncü Şahıs** (zaten vardı)

Örnek dosyada (Ortaklığın Giderilmesi satışı) çok sayıda "Haciz Alacaklısı"
(bankalar, telekom şirketleri gibi üçüncü şahıs alacaklıları) ve
"Hissedar" görüldü — bu tür dosyalarda taraf sayısı çok yüksek olabiliyor
(20+).

## 4. Kalan iş

- Safahat/Tahsilat-Reddiyat sekmeleri Satış Memurluğu için AYRICA
  doğrulanmadı.
