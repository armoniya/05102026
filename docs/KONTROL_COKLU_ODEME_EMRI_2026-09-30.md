# Kontrol Edilecek Dosyalar — Aynı Borçluya Birden Çok Başarılı Ödeme Emri Tebliği

Tespit tarihi: **30.09.2026** · Durum: ⏳ kullanıcı kontrolü / karar bekliyor
İlgili: `docs/YAPILACAKLAR_2026-09-28.md` §4 (karar bekleyenler), §8

## Ne tespit edildi

Aynı borçluya birden çok ödeme emri gönderilmiş ve **ikisi (ya da daha fazlası) de başarıyla tebliğ
edilmiş** borçlular. Program kesinleşme gününü şu an **en yeni** tebliğden hesaplıyor; hukuken
esas olanın **usulüne uygun ilk tebliğ** olması muhtemel (ikinci tebliğ süreyi yeniden başlatmaz — ama ilk
tebliğ usulsüzse ikincisi esas olur; bunu program bilemez). Hangisinin esas alınacağı kullanıcı kararı.

Not: borçlu BAŞINA ayrım zaten çalışıyor — buradaki her satır tek bir borçlunun KENDİ ödeme emirleri.
Aynı borçluya birden çok ödeme emri giden **814 borçlunun 805'inde yalnız biri başarılı** (tipik:
önce İADE, sonra yeniden tebliğ → program doğru tebliği alıyor). Karar gereken yalnız aşağıdaki **9 borçlu**;
bunların **7'sinde** ilk / en yeni tebliğe göre kesinleşme günü değişiyor.

## Hangi aşamada tespit edildi

30.09.2026, "Kesinleşme — kalan işler" planlaması sırasında yerel veritabanı üzerinde yapılan analizde
(UYAP'a istek atılmadı). Yöntem:
- `TebligatBarkod` → türü "Ödeme Emri" ve bir borçluya bağlı kayıtlar, borçlu başına gruplandı.
- "Başarılı tebliğ" = programın kesinleşmede kullandığı ölçüt: `dosya_core._tebligat_gerceklesti_mi`
  (fizikide PTT "TESLİM EDİLDİ", e-tebligatta evrak + 5 gün geçmiş) ve mazbata "Tebliğ Edilemedi" değil.
- Kesinleşme günleri Örnek 7 kuralıyla (7 gün) hesaplandı; **tatil tablosu o an boş olduğundan yalnız
  hafta sonu atlandı** (bkz. YAPILACAKLAR §8.1).

## Dosyalar

| # | Birim / Dosya | Borçlu | Başarılı tebliğler | Kesinleşme günü: ilk → en yeni | Fark | Şu anki durum |
|---|---|---|---|---|---|---|
| 1 | İzmir Banka Alacakları İcra D. 2026/120272 | ATİLLA YOLÇİ | 28.02.2026 fiziki PTT (mazbata dönmüş) · 26.09.2026 e-tebligat | 10.03.2026 → 06.10.2026 | ✔ | İtiraz (dosya "Takibe İtiraz") |
| 2 | Ayvalık İcra D. 2024/1638 | AYŞE PESENT ÇİFTÇİ | 07.03.2024 fiziki PTT · 13.03.2024 fiziki PTT (ikisinde de mazbata dönmemiş) | 15.03.2024 → 21.03.2024 | ✔ | Kesinleşme Bekliyor (21.03.2024) |
| 3 | Ayvalık İcra D. 2024/1638 | AHMET MUSTAFA ÇİFTÇİ | 07.03.2024 fiziki PTT · 13.03.2024 fiziki PTT (ikisinde de mazbata dönmemiş) | 15.03.2024 → 21.03.2024 | ✔ | Kesinleşme Bekliyor (21.03.2024) |
| 4 | İzmir Abonelik Sözleşmeleri İcra D. 2025/21479 | HAKTAN DİKİCİGİL | 25.09.2025 PTT MTS · 25.09.2025 PTT MTS (aynı gün, mazbata dönmemiş) | 03.10.2025 → 03.10.2025 | — | Kesinleşme Bekliyor (03.10.2025) |
| 5 | İzmir Abonelik Sözleşmeleri İcra D. 2026/10738 | SİNEM ÇALIŞKAN | 20.05.2026 PTT MTS (mazbata dönmemiş) · 12.07.2026 e-tebligat | 28.05.2026 → 21.07.2026 | ✔ | Kesinleşti (21.07.2026) |
| 6 | Ordu İcra D. 2023/27172 | ATEM ORMAN ÜRÜNLERİ SAN. VE TİC. LTD. ŞTİ. | 05.12.2023 e-tebligat · 05.12.2023 e-tebligat (aynı gün) | 13.12.2023 → 13.12.2023 | — | Kesinleşti (13.12.2023) |
| 7 | İzmir 20. İcra D. 2024/4978 | VİVEN GRUP İNŞAAT TURİZM SAN. VE TİC. LTD. ŞTİ. | 28.05.2024 e-tebligat · 08.07.2025 e-tebligat | 05.06.2024 → 16.07.2025 | ✔ | Kesinleşti (16.07.2025) |
| 8 | İzmir 27. İcra D. 2025/141 | GÜZEL AYAKKABI VE MALZEMELERİ DERİ SAN. TİC. LTD. ŞTİ. | 07.12.2024 e-tebligat · 13.01.2025 e-tebligat | 17.12.2024 → 21.01.2025 | ✔ | İtiraz (dosya "Takibe İtiraz") |
| 9 | İzmir 22. İcra D. 2026/2072 | VİVEN YAPI İNŞAAT TAAHHÜT TURİZM OTO SAN. VE TİC. A.Ş. | 25.02.2026 e-tebligat · 08.03.2026 e-tebligat | 05.03.2026 → 17.03.2026 | ✔ | İtiraz (dosya "Takibe İtiraz") |

## Kontrolde dikkat

- **#1, #8, #9** dosya durumu "Takibe İtiraz" → kesinleşme zaten "İtiraz"; hangi tebliğin esas olduğu şu an
  sonucu değiştirmiyor. Ama itirazın **süresinde** olup olmadığı hangi tebliğin esas alındığına bağlı
  (ör. #1: ilk tebliğe göre süre 10.03.2026'da dolmuş; itiraz ikinci tebliğden sonra yapıldıysa süre
  geçmiş olabilir) — UYAP'ta itiraz tarihine bakılmalı.
- **#5 ve #7**: şu an en yeni tebliğe göre "Kesinleşti"; ilk tebliğ esas alınırsa kesinleşme günü çok daha
  erken (#7'de 1 yıldan fazla). #5'te ilk tebliğin (MTS) mazbatası dönmemiş — ilk tebliğ esas alınsa bile
  program kuralına göre "Kesinleşti" demek için mazbata gerekir.
- **#2, #3** (Ayvalık 2024/1638): iki fiziki tebliğ de PTT'de "teslim edildi" ama hiçbirinin mazbatası
  dosyaya dönmemiş → 2024'ten beri "Kesinleşme Bekliyor". Mazbata UYAP'ta var mı bakılmalı. (Aynı borçlular
  Burhaniye 2022/1062-1063'te de "Durdurulmuş : Diğer" dosyalarında geçiyor.)
- **#4, #6**: iki tebliğ aynı gün — sonuç değişmiyor; muhtemelen aynı ödeme emrinin iki kaydı (mükerrer
  kayıt mı, iki ayrı evrak mı UYAP'ta bakılabilir).

## Karar sonrası

Kullanıcı "ilk geçerli tebliğ" derse: `dosya_core._borclu_kesinlesme_hesapla` en yeni yerine en eski
başarılı (ve mazbatası dönmüş) tebliği esas alacak şekilde değiştirilir; bu 9 borçlu yeniden hesaplanır.
