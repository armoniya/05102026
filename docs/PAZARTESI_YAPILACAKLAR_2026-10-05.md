# Pazartesi (05.10.2026) sabahı yapılacaklar

Hafta sonu tam turunun (02–04.10.2026) kalan işleri.

## Tur özeti

- **Tam tarama** (02.10 18:52): UYAP 6838 açık icra dosyası bildirdi, tamamı alındı;
  18 yeni dosya eklendi, 291 kapanan dosya Kapalı + Pasif işaretlendi.
- **Barkod + PTT turu** (02.10 18:54 → 03.10 18:50): 6515 açık icra dosyasının tamamı;
  63.662 evrak satırı işlendi, 227 atlandı, 0 hatalı. UYAP "insan kullanımına uygun
  olmayan etkinlik" uyarısı hiç gelmedi. Tempo: istek arası 1–3 sn, dosya arası 2–6 sn,
  ~40 dosyada bir 30–90 sn mola (dosya başına ~12 sn).
- **Tur sonrası** (04.10 00:04): DB yedeği `uyap_icra_20261004_000435.dump`, migration 0033
  (tebligat türü 60 → 120 karakter, commit `df330a8`), kesinleşme tüm dosyalar için yerelden
  yeniden hesaplandı (1 borçlu değişti).

## 1. Kalan 4 dosyayı yeniden işle

Tebligat türü 60 karakteri aştığı için barkod kaydı düşen 15 dosyanın 11'i 04.10 gecesi
yeniden işlendi (kayıt hatası tekrarlanmadı). Bilgisayarın belleği azalınca iş
kesildi; kalan dosyalar:

- İzmir 21. İcra Dairesi 2021/8657 (kesildiği dosya)
- Antalya Gayrimenkul Satış İcra Dairesi 2020/603
- Çardak İcra Dairesi 2020/58
- İzmir 4. İcra Dairesi 2014/9037

En kolayı: bu 4 dosyayı UyapMasa'da (yeniden başlattıktan sonra) seçip "Seçilenleri
UYAP'tan Güncelle". Hafta sonu kullanılan betik (`tekrar_isle.py`) oturuma özel geçici
klasördeydi, silinmiş olabilir.

## 2. "324 dosya DB'ye yazılamadı" uyarısını incele

Tam taramanın sayım raporu: UYAP'tan 6838 satır alındı ama 6514 farklı dosya yazıldı.
Logda hiç "Künye kaydedilemedi" satırı yok, yani gerçek bir yazma hatası değil gibi.
İki olasılık:

- UYAP aynı dosyayı listede birden fazla satırla döndürüyor (aynı birim/yıl/sıra/tür), ya da
- bazı satırlarda `dosyaId` yok ve sessizce atlanıyor (`icra_core.py` `IcraSorgu.ara`).

Anlamak için tek bir tam liste sorgusu (14 sayfa) yetiyor. Başka bir toplu tur
sürmüyorken yapılmalı.

## 3. Hatırlatmalar

- UyapMasa'yı yeniden başlat: 02.10'dan beri açık olan pencere eski kodla çalışıyordu
  (kesinleşme koruması devredeydi).
- Uzun turlar için belleği boşalt: arka plan işleri bellek azlığı yüzünden üç kez
  kapatıldı.
- Tempo ayarı (rastgele bekleme) şimdilik yalnız tur betiğinde. Kalıcı olarak programa
  (barkod_sorgu / Barkod'u da Güncelle) eklenmesi konuşulacak.
