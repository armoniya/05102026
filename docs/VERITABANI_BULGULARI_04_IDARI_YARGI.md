# Veritabanı Bulguları — İdari Yargı

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## 1. Taranan yargı birimleri (3/3 — TAMAMI)

| Birim | Dosya var mı |
|---|---|
| İdare Mahkemesi | Evet |
| Bölge İdare Mahkemesi | Hayır (0 sonuç) |
| Vergi Mahkemesi | Hayır (0 sonuç) |

## 2. `IdariYargiDetay` (`dosyaAyrintiBilgileri_brd.ajx`)

Yalnız İdare Mahkemesi'nde canlı doğrulandı (dosya 2022/2474, İzmir 2.
İdare Mahkemesi — "İdare Dava Dosyası"):

```json
{"davaKonusu":"Disiplin İşlemleri-Cezaları/İlişik Kesme Sonucunu Doğurmayan",
 "heyetli":"Evet","adliMuzaheret":"Hayır","delilTespiti":"Hayır",
 "durusma":"Hayır","yd":"Hayır","iliskiliDosyaIliskiTuru":"",
 "iliskiliDosyaEsasNo":"/","karar":"İptal","kararNo":"2023/360",
 "kararTarihi":"09/03/2023 12:33", ...}
```

Model: `IdariYargiDetay` — `dava_konusu`, `heyetli`, `adli_muzaheret`,
`delil_tespiti`, `durusma`, `yd` (Yürütmeyi Durdurma), `iliskili_dosya_
iliski_turu`, `iliskili_dosya_esas_no`, `karar`, `karar_no`, `karar_tarihi`
(ham string — İcra/Hukuk'un `{date,time}` nesnesinden FARKLI biçim).

## 3. Taraf rolleri

Görülenler: Davacı (Kişi), Davalı (Kurum, ör. bir Valilik) — zaten var olan
`DosyaTaraf.Rol` değerleri.

## 4. Kalan iş

- Bölge İdare Mahkemesi ve Vergi Mahkemesi'nde şu an dosya yok — ileride
  Safahat/Tahsilat sekmelerinin bu birimlerde de var olup olmadığı ayrıca
  doğrulanmalı (İdare Mahkemesi'nde bu sekmeler AYRICA açılıp
  incelenmedi — yalnız Dosya Bilgileri doğrulandı).
