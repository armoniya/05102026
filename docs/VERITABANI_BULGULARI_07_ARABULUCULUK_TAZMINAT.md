# Veritabanı Bulguları — Arabuluculuk ve Tazminat Komisyonu Başkanlığı

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## Arabuluculuk

Yargı Birimi seçenekleri: **ARABULUCULUK MERKEZİ**, **Arabuluculuk Daire
Başkanlığı**. İKİSİNDE DE, hem Açık hem Kapalı durum filtresiyle,
kullanıcının **hiç dosyası bulunamadı** (`[[],0]`).

**Dolaylı iz:** Bir Hukuk (Sulh Hukuk Mahkemesi) dosyasının "İlgili
Dosyalar" alanında "Arabuluculuk Daire Başkanlığı 2025/184109" adında bir
çapraz referans görüldü — yani sistemde Arabuluculuk dosyaları VAR, ama
kullanıcının kendi vekili olduğu bir Arabuluculuk dosyası şu an yok (ya da
avukat portalından bu şekilde görünmüyor).

## Tazminat Komisyonu Başkanlığı

Yargı Birimi seçeneği yok (tek, örtük). Kullanıcının **hiç dosyası
bulunamadı** (`[[],0]`). Ayrıca bu tarama sırasında UYAP'ın "eşzamanlı
sorgulama" reddi canlı gözlemlendi: `{"errorCode":"PRTL_GNL_10001-61",
"error":"Eş zamanlı olarak birden fazla sorgulama yapamazsınız! "}` — bu,
`dosya_core._yanit_hata_zarfi_mi`'nin varsayımıyla birebir örtüşüyor,
mevcut koddaki tespit mantığı doğrulandı.

## Kalan iş

İkisi için de gerçek bir dosya (kullanıcının veya ofisteki başka bir
avukatın) bulunduğunda yeniden test edilmeli — şu an hiçbir model/alan
YAZILAMADI çünkü canlı veri yok.
