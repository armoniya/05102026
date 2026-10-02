# Veritabanı Bulguları — Cbs (Cumhuriyet Başsavcılığı)

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## 1. Yargı Birimi

Diğer türlerden FARKLI: sabit bir "Yargı Birimi" listesi YOK, bunun yerine
önce **İl** seçilir, sonra o ildeki savcılıklar (`cbs_birim_sorgula.ajx`,
`{"ilKodu": ...}`) listelenir.

## 2. Test edilen: İzmir Cumhuriyet Başsavcılığı

Dosya türü: **CBS Soruşturma Dosyası** (`dosyaTurKod=16`). Sonuç: **YAPISAL
OLARAK HİÇBİR VERİ YOK** — `dosya_islem_turleri_sorgula_brd.ajx` yanıtı
yalnız `{"16":"evrak_bilgileri,evrak_gonderme,kisisel_not_alani,odeme"}`
döndürüyor (ne `ayrinti_bilgileri`, ne `taraf_bilgileri`, ne
`safahat_bilgileri`). Evrak listesi bile açıklama döndürüyor: *"Cumhuriyet
savcısının onayı sonrası evrak görüntülenebilecektir."*

**Sebep:** soruşturma gizliliği — Türk ceza usulünde soruşturma aşamasında
taraf/dosya bilgisi avukat portalına açılmıyor. Bu YAPISAL bir kısıtlama,
kod hatası değil. `CbsDetay` gibi bir model YAZILMADI, gerek yok.

## 3. Kalan iş

- İzmir dışı savcılıklar test edilmedi.
- `isSorusturmaDosyasiIncelemeTalebiKabulEdilmis:true` olan bir dosya hiç
  bulunamadı — eğer böyle bir dosya varsa (inceleme talebi kabul edilmiş),
  taraf/ayrıntı verisinin açılıp açılmadığı HENÜZ BİLİNMİYOR.
