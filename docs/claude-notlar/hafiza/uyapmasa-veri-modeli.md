---
name: uyapmasa-veri-modeli
description: "Aşama 2 (PostgreSQL) için hazır temel — UyapMasa'nın Django/PostgreSQL icra modelleri, barkod sınıflandırma, kesinleşme"
metadata:
  node_type: memory
  type: project
  originSessionId: c01c5ebc-9e1e-4a69-901f-6e84975a9683
  modified: 2026-11-14T19:41:40.434Z
---

Kullanıcı (2026-11-14): kesinleştirme ve barkod için UyapMasa'da yapılmış veritabanı çalışması ileride kullanılacak. Yeni şemayı sıfırdan yazma; buradan başla.

- **Modeller:** `cokluuyap/models/icra_models/models.py` (Django ORM → PostgreSQL, ayarlar `models/uyapdata/settings.py`, `UYAP_DB_*` ortam değişkenleri). Sınıflar: Birim, YargiBirimi, Taraf (TCKN/MERSİS ile tekil), Vekil, Dosya, DosyaTaraf (rol + `kesinlesme_durumu`/`tebligat_durumu`), IcraTakipDetay, HukukDavaDetay, IdariYargiDetay, DosyaSafahat, DosyaTahsilatHarc, DosyaReddiyat, IcraDosyaHesabi, BorcluSorgu, **TebligatBarkod**, Evrak, ResmiTatil. UYAP yanıtı → upsert: `icra_models/ingest.py`.
- **Barkod:** `UyapMasa/modules/barkod_sorgu.py` — "Kapalı Tebligat" PDF'inden Code-39 `*…*` barkodu regex ile çıkarır, PTT `POST api.ptt.gov.tr/api/ShipmentTracking` (gövde ham JSON string) ile sorgular, `_tebligat_turunu_belirle` ile türe ayırır (Ödeme Emri / Banka-Maaş Haciz İhbarnamesi / Maaş Haciz Müzekkeresi / 103 Davetiyesi / Kıymet Takdiri / Bilirkişi…), TebligatBarkod'a (dosya, birim_evrak_no) anahtarıyla yazar. Elektronik tebligatta PTT sorgusu atılmaz. Mazbata eşleştirme ve e-tebliğ mazbatası çözümleme de burada.
- **Kesinleşme:** `UyapMasa/modules/dosya_core.py` → `kesinlesme_durumlarini_guncelle`, `sure_baslangici` (tebliğ tarihinden süre); `modules/kesinlesme_core.py`; tür adları `modules/tur_eslestirme.py`.

**Kullanıcı kararları 2026-11-14:** her ofisin KENDİ veritabanı var (büro sütunu gerekmez). Aynı gün yapılanlar (migration 0034): TCKN `dosya_borclu_list.ajx`'ten (`dosya_core.taraflari_tazele`/`borclu_kimliklerini_kaydet`, eşleştirme dosya bağı üzerinden `_dosya_tarafi_yaz`; kimlikli kişi başka dosyaların kimliksiz isim kaydına bağlanmaz); UYAP'tan kalkan taraf silinmez → `DosyaTaraf.uyapta_gorulmedi` + kartta/web'de uyarı; itiraz süresi `KesinlesmeKurali` tablosundan (Ayarlar > Kesinleşme Süreleri; varsayılan 7 gün, kullanıcı türlere göre girecek); ham tarih metinlerinin yanına türetilen tarih alanları (`HamTarihliModel`, `icra_models/tarih.py`). Migration 0035 (aynı gün): `PttHareket` (hareketDongu → satır, gerçek tarih; barkod kaydında yeniden yazılır), `DurumOlayi` + `durum_olayi_yaz` (kesinleşme/tebliğ/taraf/dosya durumu/işlem durumu; dosya kartında "Geçmiş" sekmesi), tür eşleştirmeleri `TebligatTurEslesmesi`/`TebligatTurKategorisi`'nde (yerel JSON bir kez aktarılır, 60 sn önbellek). Mazbata ve 21/2 alanları hâlâ TebligatBarkod'da (ayrılmadı). (0036 İş Listesi bağlantısı ve 0037 takip aşaması 15.11'de GERİ ALINDI: UyapMasa yalnız örnek, bu iş ruyap'a taşınıyor → [[uyapmasa-yalniz-ornek]]; plan: gömülü PostgreSQL ofis bilgisayarında, İş Listesi de büroya. Render'daki /is-listesi/api/otomatik ucu taşıma bitince kaldırılacak.) Dikkat: tebliğ hesabı PTT 'kabul'ü de Dağıtımda sayar.

**How to apply:** Aşama 2'de (plan: ana bilgisayarda tek PostgreSQL) bu modelleri genişlet; icra durum makinesi (Takip Talebi → Ödeme Emri → Tebligat → Tebliğ/İade/İtiraz → Kesinleşme) TebligatBarkod + DosyaTaraf.kesinlesme_durumu üstüne kurulur, iş listesi silsilesi bu durumlara bağlanır. Yorumlardaki "CANLI doğrulandı" notları gerçek UYAP davranışıdır, yeniden keşfetme. Rust'a taşınırken ruyap `ptt_sorgu.rs` zaten var. İlgili: [[armonia-logo]].
