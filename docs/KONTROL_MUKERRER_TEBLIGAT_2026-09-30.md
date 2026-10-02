# Kontrol Edilecek Kayıtlar — Mükerrer Görünen Tebligat / Ödeme Emri + Konkordato

Tespit tarihi: **30.09.2026** · Durum: ⏳ kullanıcı kontrolü bekliyor
İlgili: `docs/KONTROL_COKLU_ODEME_EMRI_2026-09-30.md`, `docs/YAPILACAKLAR_2026-09-28.md` §4, §8

## Hangi aşamada tespit edildi

30.09.2026, aynı borçluya birden çok başarılı ödeme emri analizinde (bkz. `KONTROL_COKLU_ODEME_EMRI`) iki
borçluda iki tebliğin aynı gün olduğu görüldü (HAKTAN DİKİCİGİL, ATEM ORMAN); bunun üzerine tüm
`TebligatBarkod` tablosu (62.606 kayıt) yerel veritabanında tarandı. UYAP'a istek atılmadı.

## Denenen ölçütler ve sonuç

| Ölçüt | Sonuç | Değerlendirme |
|---|---|---|
| Aynı dosya + borçlu + tür + evrak tarihi + açıklama | 1.121 grup / 3.482 kayıt | **Mükerrerlik göstergesi DEĞİL** — ör. aynı "Banka", "Tensip zaptı", "Kıymet Takdiri Raporu" tebligatı farklı muhataplara (bankalar, taraflar) ayrı zarflarla gidiyor; barkodlar farklı. Listelenmedi. |
| **A) Aynı PTT barkodu birden çok kayıtta** | **65 barkod / 130 kayıt** | Aşağıda tablo A. Hepsi **iki bağlantılı dosya çiftinde**; gerçek tekrar zarf değil, **aynı evrakın iki dosyada sayılması**. |
| **B) Aynı borçluya aynı evrak tarihli birden çok ödeme emri** | **21 grup / 43 kayıt** | Aşağıda tablo B. Barkodların HEPSİ farklı → büyük olasılıkla **farklı adreslere paralel gönderim** (bir kısmı "35. Maddeye Göre Tebligat"); tekrar kayıt değil. |

### A) Aynı barkod — bağlantılı iki dosyada aynı evrak

Tüm 65 barkod yalnız şu iki dosya çiftinde; iki taraftaki **birim evrak no da aynı**:
- **İzmir Gayrimenkul Satış İcra D. 2025/256 ↔ İzmir 27. İcra D. 2023/19210** (54 barkod)
- **Milas İcra D. 2024/3957 ↔ İzmir 23. İcra D. 2023/22965** (11 barkod) — İzmir 23. 2023/22965
  "Durdurulmuş : Diğer" listesinde de var (#11, HASAN PEKER).

Olası açıklama (DOĞRULANMADI): Satış dosyası esas dosyadan talimat/satış için açıldığında ya da dosya
yetkisizlik vb. ile başka daireye gönderildiğinde UYAP, esas dosyanın evraklarını yeni dosyada da
gösteriyor → barkod turu aynı evrakı iki dosyada ayrı kayıt olarak yazıyor.
**Etkisi:** (1) aynı zarf PTT'de iki kez sorgulanabilir (gereksiz istek), (2) kesinleşme/tebliğ iki
dosyada ayrı hesaplanıyor, (3) borçlu bağlaması iki tarafta farklı olabiliyor (ör. Milas tarafında bazı
ödeme emirleri hiçbir borçluya bağlanmamış — tabloda "—").
**Kontrol:** UYAP'ta iki dosya arasındaki ilişki (talimat / yetkisizlik / birleştirme) — "İlgili Dosyalar"
alanı. Karar sonrası: barkod turu aynı barkodu ikinci kez PTT'ye sormasın; kesinleşme hangi dosyada
hesaplanacak belirlensin.

| # | Barkod | Dosya | Evrak no | Tür | Evrak tarihi | Borçlu | PTT durumu |
|---|---|---|---|---|---|---|---|
| 1 | 3000105794634 | Milas İcra Dairesi 2024/3957 | 636987 | Ödeme Emri | 12/12/2023 | AYCAN ELİF ÇİNAR | — |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 636987 | Ödeme Emri | 12/12/2023 | — | — |
| 2 | 3000105794641 | Milas İcra Dairesi 2024/3957 | 636988 | Ödeme Emri | 12/12/2023 | — | — |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 636988 | Ödeme Emri | 12/12/2023 | HASAN PEKER | — |
| 3 | 3000105935075 | Milas İcra Dairesi 2024/3957 | 659225 | Ödeme Emri | 25/12/2023 | — | — |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 659225 | Ödeme Emri | 25/12/2023 | HASAN PEKER | — |
| 4 | 3000105947177 | Milas İcra Dairesi 2024/3957 | 659720 | Ödeme Emri | 26/12/2023 | AYCAN ELİF ÇİNAR | — |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 659720 | Ödeme Emri | 26/12/2023 | — | — |
| 5 | 4006394292706 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537068 | Ödeme Emri | 19/10/2023 | ÇUKUROVA KMT YAPI İNŞAAT TAAHHÜT KUYUMC… | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537068 | Ödeme Emri | 19/10/2023 | ÇUKUROVA KMT YAPI İNŞAAT TAAHHÜT KUYUMC… | KAYIT YOK |
| 6 | 4006394292713 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537069 | Ödeme Emri | 19/10/2023 | ÇUKUROVA TEKİNALP KUYUMCULUK GIDA İNŞAA… | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537069 | Ödeme Emri | 19/10/2023 | ÇUKUROVA TEKİNALP KUYUMCULUK GIDA İNŞAA… | KAYIT YOK |
| 7 | 4006394292720 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537070 | Ödeme Emri | 19/10/2023 | ÇUKUROVA TEKİNALP OTO KİRALAMA GALERİ K… | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537070 | Ödeme Emri | 19/10/2023 | ÇUKUROVA TEKİNALP OTO KİRALAMA GALERİ K… | KAYIT YOK |
| 8 | 4006394292737 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537071 | Ödeme Emri | 19/10/2023 | EMRAH TEKİNALP | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537071 | Ödeme Emri | 19/10/2023 | EMRAH TEKİNALP | KAYIT YOK |
| 9 | 4006394292744 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537072 | Ödeme Emri | 19/10/2023 | KAMURAN TEKİNALP | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537072 | Ödeme Emri | 19/10/2023 | KAMURAN TEKİNALP | KAYIT YOK |
| 10 | 4006394292751 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 537073 | Ödeme Emri | 19/10/2023 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 537073 | Ödeme Emri | 19/10/2023 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | KAYIT YOK |
| 11 | 4006412345537 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558014 | Haciz İhbarnamesi | 02/11/2023 | — | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558014 | Haciz İhbarnamesi | 02/11/2023 | — | KAYIT YOK |
| 12 | 4006416301300 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 562081 | Haciz İhbarnamesi | 06/11/2023 | — | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 562081 | Haciz İhbarnamesi | 06/11/2023 | — | KAYIT YOK |
| 13 | 4006419034953 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 564412 | Ödeme Emri | 08/11/2023 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | KAYIT YOK |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 564412 | Ödeme Emri | 08/11/2023 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | KAYIT YOK |
| 14 | 4006626651318 | Milas İcra Dairesi 2024/3957 | 176546 | 103 Davetiyesi | 24/04/2024 | — | — |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 176546 | 103 Davetiyesi | 24/04/2024 | HASAN PEKER | — |
| 15 | 4006729813026 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 327191 | Kıymet Takdiri Raporu | 19/07/2024 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | TESLİM EDİLDİ |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 327191 | Kıymet Takdiri Raporu | 19/07/2024 | NOVADA OTO KİRALAMA GALERİ TURİZM TEKST… | TESLİM EDİLDİ |
| 16 | 4006935894437 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6068 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | — |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6068 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | — |
| 17 | 4006997837380 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 72218 | Haciz İhbarnamesi | 20/02/2025 | — | — |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 72218 | Haciz İhbarnamesi | 20/02/2025 | — | — |
| 18 | 5000612244422 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533132 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533132 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 19 | 5000612262433 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533133 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533133 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 20 | 5000612262518 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533134 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533134 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 21 | 5000612262556 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533135 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533135 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 22 | 5000612262631 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533137 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533137 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 23 | 5000612262730 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533138 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533138 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 24 | 5000612262983 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533139 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533139 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 25 | 5000612263126 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533140 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533140 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 26 | 5000612263218 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533142 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533142 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 27 | 5000612263300 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533143 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533143 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 28 | 5000612263393 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533144 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533144 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 29 | 5000612263508 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533145 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533145 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 30 | 5000612263645 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 533146 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 533146 | Banka | 17/10/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 31 | 5000639655614 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 555916 | Belirsiz | 01/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 555916 | Belirsiz | 01/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 32 | 5000642779079 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558305 | Haciz İhbarnamesi | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558305 | Haciz İhbarnamesi | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 33 | 5000642779499 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558306 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558306 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 34 | 5000642779529 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558307 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558307 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 35 | 5000642779567 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558308 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558308 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 36 | 5000642779611 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 558309 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 558309 | Banka | 02/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 37 | 5000650473785 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 564321 | 103 Davetiyesi | 08/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 564321 | 103 Davetiyesi | 08/11/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 38 | 5000735516543 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 642065 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 642065 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 39 | 5000735516567 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 642066 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 642066 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 40 | 5000735516574 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 642067 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 642067 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 41 | 5000735516581 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 642068 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 642068 | Banka | 25/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 42 | 5000737487896 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 643570 | MUHTIRA | 26/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 643570 | MUHTIRA | 26/12/2023 | — | E-Tebligat: tebliğ edilmiş sa… |
| 43 | 5000762309613 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 15389 | Belirsiz | 10/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 15389 | Belirsiz | 10/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 44 | 5000775213747 | Milas İcra Dairesi 2024/3957 | 22405 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22405 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 45 | 5000775213822 | Milas İcra Dairesi 2024/3957 | 22406 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22406 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 46 | 5000775213853 | Milas İcra Dairesi 2024/3957 | 22408 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22408 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 47 | 5000775213907 | Milas İcra Dairesi 2024/3957 | 22409 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22409 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 48 | 5000775213952 | Milas İcra Dairesi 2024/3957 | 22410 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22410 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 49 | 5000775214003 | Milas İcra Dairesi 2024/3957 | 22412 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 23. İcra Dairesi 2023/22965 | 22412 | Banka | 17/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 50 | 5000788500902 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 42900 | Kıymet Takdiri Raporu | 25/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 42900 | Kıymet Takdiri Raporu | 25/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 51 | 5000788500933 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 42901 | Kıymet Takdiri Raporu | 25/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 42901 | Kıymet Takdiri Raporu | 25/01/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 52 | 5000948107392 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 197511 | Banka | 26/04/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 197511 | Banka | 26/04/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 53 | 5001103231488 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 322955 | Ödeme Emri | 18/07/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 322955 | Ödeme Emri | 18/07/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 54 | 5001108063893 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 327236 | Kıymet Takdiri Raporu | 19/07/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 327236 | Kıymet Takdiri Raporu | 19/07/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 55 | 5001139705670 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 352846 | 103 Davetiyesi | 07/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 352846 | 103 Davetiyesi | 07/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 56 | 5001178190345 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 391803 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 391803 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 57 | 5001178190420 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 391804 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 391804 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 58 | 5001178190505 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 391805 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 391805 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 59 | 5001178190567 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 391806 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 391806 | Banka (Haciz İhbarnamesi) | 28/08/2024 | — | E-Tebligat: tebliğ edilmiş sa… |
| 60 | 5001444742230 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6072 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6072 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
| 61 | 5001444742285 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6073 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6073 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
| 62 | 5001444742346 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6074 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6074 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
| 63 | 5001444742421 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6075 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6075 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
| 64 | 5001444742476 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 6076 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 6076 | 07/01/2025 TARİHLİ SIRA CETVE… | 07/01/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
| 65 | 5001536127389 | İzmir Gayrimenkul Satış İcra Dairesi 2025/256 | 63509 | 103 Davetiyesi | 14/02/2025 | — | E-Tebligat: tebliğ edilmiş sa… |
|  |  | İzmir 27. İcra Dairesi 2023/19210 | 63509 | 103 Davetiyesi | 14/02/2025 | — | E-Tebligat: tebliğ edilmiş sa… |

### B) Aynı borçluya aynı gün birden çok ödeme emri (farklı barkod)

13 grupta açıklama birebir aynı, diğerlerinde biri "Normal Tebligat", diğeri "35.Maddeye Gore Tebligat"
ya da mazbata eki farklı. Barkodların hiçbiri aynı değil. Olası açıklama (DOĞRULANMADI): ödeme emri
borçlunun farklı adreslerine (MERNİS adresi + takip talebindeki adres, ya da TK 35'e göre bilinen adres)
aynı gün ayrı zarflarla gönderilmiş. **Kesinleşmeye etkisi:** program bunları aynı borçlunun ayrı
tebligatları olarak zaten doğru işliyor (biri teslim, biri iade ise teslim olan esas). Dikkat: **Ordu
2023/27172 ATEM ORMAN** — iki **e-tebligat** aynı gün aynı adrese; paralel adres açıklaması burada geçerli
değil, gerçek tekrar olabilir.
**Kontrol:** birkaç örnekte UYAP'ta iki zarfın adresleri.

| # | Dosya | Borçlu | Evrak no | Evrak tarihi | Kanal | Barkod | PTT durumu / son işlem | Açıklama |
|---|---|---|---|---|---|---|---|---|
| 1 | Ayvalık İcra Dairesi 2021/1734 | AHMET MUSTAFA ÇİFTÇİ | 72837 | 30/04/2021 | Fiziki | 4004636208560 | —  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET MUSTAFA ÇİFTÇİ] |
|  |  |  | 72840 | 30/04/2021 | Fiziki | 4004636290947 | —  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET MUSTAFA ÇİFTÇİ]… |
| 2 | Ayvalık İcra Dairesi 2022/3123 | AHMET MUSTAFA ÇİFTÇİ | 212782 | 27/05/2022 | Fiziki | 3000096444198 | —  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET MUSTAFA ÇİFTÇİ]… |
|  |  |  | 212784 | 27/05/2022 | Fiziki | 3000096444211 | KAYIT YOK  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET MUSTAFA ÇİFTÇİ] |
| 3 | Bursa 4. Genel İcra Dairesi 2023/152971 | NAZLI YAVUZ | 24686 | 20/01/2023 | Fiziki | 4006095845430 | —  | Normal Tebligat[BORÇLU VE MÜFLİS NAZLI YAVUZ] |
|  |  |  | 24749 | 20/01/2023 | Fiziki | 3000100772903 | —  | Normal Tebligat[BORÇLU VE MÜFLİS NAZLI YAVUZ] - Tebliğ… |
| 4 | Büyükçekmece İcra Dairesi 2024/163011 | MURAT ESKİ | 317761 | 30/06/2021 | Fiziki | 4004759166761 | —  | Normal Tebligat[BORÇLU VE MÜFLİS MURAT ESKİ] |
|  |  |  | 317781 | 30/06/2021 | Fiziki | 4004759186059 | —  | Normal Tebligat[BORÇLU VE MÜFLİS MURAT ESKİ] |
| 5 | Büyükçekmece İcra Dairesi 2024/163011 | SEÇKİN ORAL NAS | 317762 | 30/06/2021 | Fiziki | 4004759166778 | —  | Normal Tebligat[BORÇLU VE MÜFLİS SEÇKİN ORAL NAS] |
|  |  |  | 317782 | 30/06/2021 | Fiziki | 4004759186080 | —  | Normal Tebligat[BORÇLU VE MÜFLİS SEÇKİN ORAL NAS] |
| 6 | Büyükçekmece İcra Dairesi 2024/163011 | COİNSPACE İNTERNET HİZMETLERİ SANAYİ VE… | 362699 | 31/07/2021 | Fiziki | 4004825221158 | —  | 35.Maddeye Gore Tebligat[BORÇLU VE MÜFLİS COİNSPACE İN… |
|  |  |  | 362700 | 31/07/2021 | Fiziki | 4004825221172 | —  | 35.Maddeye Gore Tebligat[BORÇLU VE MÜFLİS COİNSPACE İN… |
| 7 | Büyükçekmece İcra Dairesi 2024/167109 | ERDEM BASTEM | 610710 | 17/11/2021 | Fiziki | 4005111710776 | —  | Normal Tebligat[BORÇLU VE MÜFLİS ERDEM BASTEM] |
|  |  |  | 610725 | 17/11/2021 | Fiziki | 4005111712893 | —  | Normal Tebligat[BORÇLU VE MÜFLİS ERDEM BASTEM] |
| 8 | Ordu İcra Dairesi 2023/27172 | ATEM ORMAN ÜRÜNLERİ SANAYİ VE TİCARET L… | 1228484 | 30/11/2023 | E-tebligat | 5000692160704 | E-Tebligat: tebliğ edilm… 05/12/2023 | Elektronik Tebligat[BORÇLU VE MÜFLİS ATEM ORMAN ÜRÜNLE… |
|  |  |  | 1229107 | 30/11/2023 | E-tebligat | 5000692489669 | E-Tebligat: tebliğ edilm… 05/12/2023 | Elektronik Tebligat[BORÇLU VE MÜFLİS ATEM ORMAN ÜRÜNLE… |
| 9 | Rize İcra Dairesi 2023/7976 | BÜNYAMİN GENÇ | 271162 | 13/07/2023 | Fiziki | 4006290216059 | —  | Normal Tebligat[BORÇLU VE MÜFLİS BÜNYAMİN GENÇ] |
|  |  |  | 271320 | 13/07/2023 | Fiziki | 4006290271928 | —  | Normal Tebligat[BORÇLU VE MÜFLİS BÜNYAMİN GENÇ] |
| 10 | Çan İcra Dairesi 2024/234 | JEO ESER KAYHAN MADENCİLİK JEOLOJİ MÜHE… | 18685 | 21/03/2024 | Fiziki | 4006594744937 | —  | Normal Tebligat[BORÇLU VE MÜFLİS JEO ESER KAYHAN MADEN… |
|  |  |  | 18698 | 21/03/2024 | Fiziki | 3000107409765 | —  | Normal Tebligat[BORÇLU VE MÜFLİS JEO ESER KAYHAN MADEN… |
| 11 | İzmir 1. İcra Dairesi 2024/6844 | ENERCON ENERJİ DANIŞMANLIK MAKİNA SANAY… | 337503 | 24/07/2024 | Fiziki | 3000108995502 | —  | Normal Tebligat[BORÇLU VE MÜFLİS ENERCON ENERJİ DANIŞM… |
|  |  |  | 337507 | 24/07/2024 | Fiziki | 3000108995519 | —  | Normal Tebligat[BORÇLU VE MÜFLİS ENERCON ENERJİ DANIŞM… |
| 12 | İzmir 15. İcra Dairesi 2025/10628 | GİZEM ŞİRİNKEÇECİ | 569834 | 23/12/2025 | Fiziki | 4007380741956 | TESLİM EDİLDİ 02/01/2026 | Normal Tebligat[BORÇLU VE MÜFLİS GİZEM ŞİRİNKEÇECİ] - … |
|  |  |  | 570506 | 23/12/2025 | Fiziki | 4007381380499 | KAYIT YOK  | Normal Tebligat[BORÇLU VE MÜFLİS GİZEM ŞİRİNKEÇECİ] |
| 13 | İzmir 18. İcra Dairesi 2024/6252 | MUHAMMET POLAT | 317381 | 18/07/2024 | Fiziki | 4006727997490 | KAYIT YOK  | Normal Tebligat[BORÇLU VE MÜFLİS MUHAMMET POLAT] |
|  |  |  | 317385 | 18/07/2024 | Fiziki | 3000108941028 | TESLİM EDİLDİ 25/07/2024 | Normal Tebligat[BORÇLU VE MÜFLİS MUHAMMET POLAT] |
| 14 | İzmir 20. İcra Dairesi 2021/5643 | MYSPİNE SAĞLIK ÜRÜNLERİ VE HAYVANCILIK … | 261745 | 10/06/2021 | Fiziki | 4004713823037 | —  | Normal Tebligat[BORÇLU VE MÜFLİS MYSPİNE SAĞLIK ÜRÜNLE… |
|  |  |  | 261749 | 10/06/2021 | Fiziki | 4004713823327 | —  | Normal Tebligat[BORÇLU VE MÜFLİS MYSPİNE SAĞLIK ÜRÜNLE… |
| 15 | İzmir 25. İcra Dairesi 2026/1774 | HAMZA OĞUZ | 467207 | 10/09/2026 | Fiziki | 4007717612058 | İADE 14/09/2026 | Normal Tebligat[BORÇLU VE MÜFLİS HAMZA OĞUZ] - Tebliğ … |
|  |  |  | 467212 | 10/09/2026 | Fiziki | 4007717612171 | İADE 15/09/2026 | Normal Tebligat[BORÇLU VE MÜFLİS HAMZA OĞUZ] - Tebliğ … |
| 16 | İzmir 26. İcra Dairesi 2022/13031 | AHMET YAVUZ | 629424 | 19/10/2022 | Fiziki | 4005973660301 | —  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET YAVUZ] |
|  |  |  | 629619 | 19/10/2022 | Fiziki | 3000099046337 | —  | Normal Tebligat[BORÇLU VE MÜFLİS AHMET YAVUZ] |
| 17 | İzmir 9. İcra Dairesi 2023/2294 | İBRAHİM KOPARAN | 194259 | 06/04/2023 | Fiziki | 4006184078350 | —  | Normal Tebligat[BORÇLU VE MÜFLİS İBRAHİM KOPARAN] |
|  |  |  | 194264 | 06/04/2023 | Fiziki | 4006184078466 | —  | Normal Tebligat[BORÇLU VE MÜFLİS İBRAHİM KOPARAN] |
|  |  |  | 194265 | 06/04/2023 | Fiziki | 4006184078510 | —  | Normal Tebligat[BORÇLU VE MÜFLİS İBRAHİM KOPARAN] |
| 18 | İzmir Abonelik Sözleşmeleri İcra Dairesi 2024/4751 | BY ONBİN YAPI İÇ VE DIŞ TİCARET SANAYİ … | 147668 | 04/04/2024 | Fiziki | 4006611715742 | —  | Normal Tebligat[BORÇLU VE MÜFLİS BY ONBİN YAPI İÇ VE D… |
|  |  |  | 147670 | 04/04/2024 | Fiziki | 4006611715926 | —  | 35.Maddeye Gore Tebligat[BORÇLU VE MÜFLİS BY ONBİN YAP… |
| 19 | İzmir Abonelik Sözleşmeleri İcra Dairesi 2024/981 | FEMAK DEMİR ÇELİK MAKİNE İMALAT SANAYİ … | 61749 | 13/02/2024 | Fiziki | 4006544725177 | —  | Normal Tebligat[BORÇLU VE MÜFLİS FEMAK DEMİR ÇELİK MAK… |
|  |  |  | 61754 | 13/02/2024 | Fiziki | 4006544725559 | —  | 35.Maddeye Gore Tebligat[BORÇLU VE MÜFLİS FEMAK DEMİR … |
| 20 | İzmir Abonelik Sözleşmeleri İcra Dairesi 2026/11612 | HAVİN YILMAZ | 784724 | 23/09/2026 | Fiziki | 4007733836599 | KAYIT YOK  | Normal Tebligat[BORÇLU VE MÜFLİS HAVİN YILMAZ] |
|  |  |  | 784749 | 23/09/2026 | Fiziki | 4007733898337 | İL İÇİ AKTARMADA 25/09/2026 | Normal Tebligat[BORÇLU VE MÜFLİS HAVİN YILMAZ] |
| 21 | İzmir Gayrimenkul Satış İcra Dairesi 2026/412 | YILDIRAY SARITAŞ | 103085 | 20/08/2026 | Fiziki | 4007696276111 | İADE 02/09/2026 | Normal Tebligat[BORÇLU VE MÜFLİS YILDIRAY SARITAŞ] |
|  |  |  | 103092 | 20/08/2026 | Fiziki | 4007696320838 | İADE 28/08/2026 | Normal Tebligat[BORÇLU VE MÜFLİS YILDIRAY SARITAŞ] - T… |

## Konkordato

30.09.2026 itibarıyla veritabanında:
- Kesinleşme durumu elle **"Konkordato"** (ya da Vefat/Vasi) girilmiş borçlu: **YOK (0)**.
- Dosya durum metninde "konkordato" geçen dosya: **YOK (0)**.
- Unvanında "konkordato" geçen borçlu: **YOK (0)**.
Yani UYAP'ın konkordatoyu dosya durumunda nasıl yazdığı henüz bilinmiyor. İlk örnek geldiğinde program
kesinleşmeyi "Durdurulmuş: Tanımsız Neden" yazar ve logda "Tanımsız durdurma nedeni … DURDURMA_NEDENLERI'ne
eklenmeli" uyarısı çıkar → o metin `dosya_core.DURDURMA_NEDENLERI`'ne eklenir (Konkordato değerine mi,
ayrı "Durdurulmuş: Konkordato" değerine mi bağlanacağı o zaman kullanıcıya sorulur).
