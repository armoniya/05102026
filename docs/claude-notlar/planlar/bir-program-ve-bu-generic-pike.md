# icraMatik'e kayıtMatik ile aktarılacak Excel dosyalarını üreten modül — Plan

## Bağlam
Kullanıcı büroda Martı Yazılım'ın **icraMatik** programını kullanıyor. Kendi otomasyonundaki (Çoklu UYAP + müvekkil listeleri) verileri icraMatik'e aktarmak istiyor. Bunu etik ve hukuki açıdan temiz bir yolla yapmak istiyor: veritabanına doğrudan yazmadan, tersine mühendislik yapmadan, yalnızca ofis içinde ve ticari dağıtım olmadan.

Yardım sayfası (martiyazilim.com.tr/kayitmatik/yardim) incelendi. Temel bulgu:
- **kayıtMatik Rahat**, şirketin bu iş için sunduğu resmî modül. icraMatik içinden açılıyor: Program menüsü > kayıtMatik Rahat.
- Herhangi bir Excel dosyasının **sütunları**, kayıtMatik ekranında icraMatik tablo ve alanlarıyla **eşleniyor**. Örnek tablolar: Föy, Föy Gider, Föy Özel Kod, karşı yan (borçlu) tabloları. Föy tablosu alanlarından örnekler: Klasör Kodu, Föy Tipi, İcra Dairesi, İcra No, Dosya No, Takip Tarihi, Takip Türü.
- Zorunlu alanlar ekranda kırmızı gösteriliyor.
- Tablo bazında aktarım şekli seçiliyor: her zaman yeni kayıt / yoksa ekle, varsa güncelle / yoksa ekle, varsa dokunma / sadece güncelle.
- **Kılavuz alan:** Aynı föye ait birden çok satırı birleştiriyor.
- **Grup:** Aynı tabloya birden çok kayıt eklemeyi sağlıyor.
- **Kriter:** Hangi föylere aktarılacağını belirliyor.
- Eşleme bir kez **şablon** olarak kaydediliyor. Şablon, Excel dosyasının yolunu da hatırlıyor.
- **Aktarım Testi:** Excel'deki bir değerin icraMatik'te karşılığı yoksa (örneğin alacak türü) uyarı verip eşletiyor. Seçilen değeri Excel dosyasına da yazıyor.

**Sonuç:** Gizli bir dosya biçimini çözmeye gerek yok. Sütun adlarını biz belirliyoruz, eşlemeyi kullanıcı kayıtMatik'te bir kez yapıyor. Şirket de şablon hazırlarken yardım teklif ediyor. Bu, programın amaçlandığı şekilde kullanılması demek.

## İş akışı
1. Kullanıcının otomasyonu, kayıt türü başına **sabit sütun başlıklı** bir Excel dosyası üretir. Dosya her seferinde aynı yola yazılır, örneğin `…\icraMatik Aktarım\yeni_takip.xlsx`.
2. Kullanıcı, her tür için kayıtMatik'te bir kez eşleme yapıp şablon olarak kaydeder. Sonraki aktarımlarda yalnızca Şablon Seç > İleri > Aktar > Çalıştır yapar.
3. Değer karşılıkları (alacak türü, takip türü, icra dairesi adı vb.) ilk Aktarım Testi'nde ortaya çıkar. Bunlar otomasyonun eşleme tablosuna eklenir. Böylece sonraki dosyalar doğrudan icraMatik'in beklediği değerlerle üretilir.

## Adım 0 — Kullanıcıdan (yalnızca program ekranından, gözlemle)
- Kayıtmatik'te 4 tür için kullanılacak tabloların alan listelerinin ekran görüntüleri: Föy ve karşı yan / borçlu tabloları, tahsilat, adres, işlem/not tabloları. Özellikle hangi alanların kırmızı (zorunlu) olduğu görünmeli.
- Hangi tablonun hangi tür için kullanılacağı belirsizse, şirketin yardım teklifinden bir kez yararlanılır. Böylece her tür için doğru tablolar ve kılavuz alan seçimi netleşir.

## Adım 1 — Sütun sözleşmesi
- `docs/ICRAMATIK_AKTARIM.md` dosyasında her kayıt türü için şunlar tanımlanır: Excel sütun adları, bu sütunun eşleneceği icraMatik tablosu ve alanı, zorunluluk, biçim (tarih gg.aa.yyyy, tutar, TC/VKN), kılavuz alan, aktarım şekli.
- Eşleşmeyi sağlamak için föy anahtarı belirlenir: tahsilat, adres ve not dosyaları, mevcut föyü bulmak için kılavuz alan ya da kriter olarak İcra Dairesi + Esas No'yu taşır.

## Adım 2 — Üretici modül (onaydan sonra)
- Konum: `Dosya Açılış/` altında yeni bir klasör, örneğin `icraMatik Aktarım/`. Mevcut `İcra Takip Açılış - XML` ve `MTS Takip Açılış` klasörlerindeki veri okuma kodu yeniden kullanılır.
- Kaynaklar:
  - UYAP tarafı: `Uyap Haricen Giriş/uyap_core` verileri (`is_listesi.py`, `dosya_kaydet.py`, `esas_no.py`).
  - Müvekkil Excel listeleri: sütun eşleme ile okunur.
- Değer eşleme tablosu: Kod içine gömülmez, veri dosyası olarak tutulur. Aktarım Testi'nden öğrenilen karşılıklar buraya eklenir.
- Doğrulama: Zorunlu alan, biçim ve bilinmeyen değer kontrolleri yapılır. Hatalı satırlar ayrı bir "hatalar" sayfasına yazılır, aktarım dosyasına girmez.
- İcra programının veritabanına hiçbir bağlantı yapılmaz.

## Adım 3 — Doğrulama
- Birim testler: Uydurma veriyle üretilen dosyaların sütun sözleşmesine uygunluğu test edilir. `Uyap Haricen Giriş/tests/` kalıbı izlenir.
- Elle deneme: Her tür için 1-2 satırlık dosya üretilir. Aktarım şekli "yoksa ekle, varsa güncelleme" seçilir, Aktarım Testi çalıştırılır ve sonuç föyde gözle kontrol edilir.
- Her türün gerçek kullanıma alınmasından önce icraMatik yedeği alınır.
