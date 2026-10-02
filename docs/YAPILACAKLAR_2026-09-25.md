# Yapılacaklar — 25.09.2026

Durum işaretleri: ✅ yapıldı · 🔎 canlı doğrulama bekliyor · ⏳ yapılacak

---

## 1. İcra dosyası veri modeli — bugün yapılanlar ✅

Kullanıcı hedefi: veritabanında İcra olarak işaretlenmiş dosyalar programın her yerinden
(İcra Dosyalarım, Tüm Dosyalarım, Barkod Sorgu, ileride iş listesi / toplu aksiyon / dosya kartı)
AYNI tanımla ve AYNI türetilmiş değerlerle erişilebilir olsun.

- **Kalıcı yargı türü:** `Dosya.yargi_turu` artık zorunlu (migration 0025). "İcra mı" = `Dosya.objects.icra()`
  (`.acik()`, `.kapali()`, `.yargi(kod)` ile zincirlenir). Eski "boşsa birimden / birimTuru2=1101'den tanı"
  yedekleri kaldırıldı (`icra_core._icra_dosyasi_q`). `ingest.dosya_kunyesi_kaydet` türü bilinmeyen dosyayı
  reddeder. Listeler dosyanın kendi alanını kullanır (birim üzerinden değil).
- **Borçlu başına Tebliğ Durumu:** `DosyaTaraf.tebligat_durumu` + yeni `tebligat_durum_tarihi`, borçluya bağlı
  ÖDEME EMRİ barkodlarından hesaplanıp saklanır (`dosya_core._borclu_tebligat_hesapla`, kesinleşme ile aynı
  anda `kesinlesme_durumlarini_guncelle` içinde). Listeler (`borclu_durum_metinleri`) tek borçluda
  "Tebliğ Edildi (25.08.2025)", çok borçluda "AD: …; AD2: …" gösterir. Eski "dosyanın en yeni barkodu"
  özeti kaldırıldı.
- **MTS ödeme emri → borçlu:** açıklamada muhatap olmayan MTS ödeme emirleri PTT yanıtındaki maskeli alıcıyla
  ("B* V* M* F* B*") bağlanıyor (`_ptt_alici_borclusunu_bul`). Yalnız-PTT turu artık kesinleşme/tebliğ hesaplıyor.
- DB'ye uygulandı: 1.318 barkod bağı, 1.553 borçlu satırı (tebliğ edildi 960 / iade 585 / dağıtımda 8).
  Yedek: `%LOCALAPPDATA%\UyapMasa\yedek\mts_borclu_kesinlesme_yedek_20260925_140505.json`.

🔎 Doğrulanacak: UyapMasa yeniden başlatılınca İcra Dosyalarım'da Kesinleşme/Tebliğ sütunları (masaüstü + web).
⏳ Süren gece PTT turu (ptt_turu2) eski kodla çalışıyor — bitince `kesinlesme_durumlarini_guncelle()` bir kez daha.

## 2. Veri modeli — sıradaki adımlar ⏳

- Ortak "İcra dosyası servisi": dosya + borçlu durumu tek yerden okunur (listeler, dosya kartı, iş listesi,
  toplu aksiyon aynı fonksiyonu kullanır); dosya her yerde kalıcı `dbId` (Dosya.pk) ile tanınır, oturumluk
  `dosyaId` ile değil.
- Hesaplanan durumların (kesinleşme, tebliğ, ileride haciz/21-2) saklanması ve DB tarafında süzülebilmesi
  (toplu aksiyon süzgeçleri için).
- Takip türüne göre kesinleşme süresi (şu an hep Örnek 7) — kullanıcı türleri tek tek anlatacak.

## 3. Aksiyon tanımları (öneri 4) — ⏳ yapılacaklar listesinde, veri modeli oturduktan sonra

Her aksiyon (toplu sorgu, toplu haciz, toplu kesinleştirme, …) TEK yerde tanımlanır: adı, hangi dosya/borçluda
uygun olduğu kuralı, çalıştıran fonksiyon. Dosya kartı düğmeleri ve toplu aksiyon listesi aynı tanımları kullanır.
Kullanıcı kararı (25.09): şimdilik aksiyonlara ilişkin fonksiyonlar yazılacak; ortak tanım/kayıt yapısı veri
modeli tam oturunca.
