# Yapılacaklar — 23.09.2026

Durum işaretleri: ✅ yapıldı · 🔎 canlı doğrulama bekliyor · ⏳ yapılacak

---

## 1. 43.000 dosyalık "UYAP'tan Tebligat Bilgisi Çek" turu — ⏳ yeniden çalıştırılacak

- Program kapatıldığı için tur yarıda kaldı. O ana kadar işlenen evraklar veritabanında
  (`TebligatBarkod`) — kayıt evrak evrak, anında yazılıyor; kaybolan bir şey yok.
- Yeniden başlatınca tur tekrar çalıştırılır. Türü ve barkodu zaten bilinen evraklar için PDF
  yeniden indirilmez, bu yüzden daha önce işlenen dosyalar hızlı geçer.
- Tur sonunda log'da kontrol edilecekler:
  - "Yerel veritabanı aktif değil" (tur başında) → hiçbir şey yazılmamış demektir.
  - "Dosya künyesi kaydedilemedi" / "DB'ye kaydedilemedi" → o dosya/evrak yazılmamış.
  - Son satırdaki "✅ İşlenen / ⚠️ Atlanan / ❌ Hatalı" sayıları.
- Tur sürerken: masaüstü yeniden başlatılmaz, veritabanı migration'ı çalıştırılmaz.

## 2. [ÖNCELİK 1] İcra Dosyalarım → Dosya Görüntüle'de barkodlar görünmüyor — ✅ kodda düzeltildi, 🔎 canlı doğrulama

**Kök neden (kod okumasıyla bulundu, canlı doğrulanmadı):**
Dosya Görüntüle (`dosya_core.dosya_detay_goster_ve_kaydet`, `dosya_core.py:1323-1327`) dosyayı
veritabanında `birimId` + `dosyaTurKod` ile arıyor. İcra Dosyalarım listesinin veritabanından gelen
satırları (`icra_core.db_dosyalari_getir`) bu iki alanı taşımıyordu. Sonuç zinciri:
1. Sorgula'ya basılmadan açılan satırda dosya bulunamıyor → "Dosya yerel veritabanında bulunamadı".
2. Detay penceresi (masaüstü `icra_dosyalarim._detay_goster`, web `server.icra_detay` + `icra.js`)
   hata olunca barkodları **göstermeden** çıkıyordu.
3. Barkod listesi (`dosya_core.tebligat_barkod_gecmisi_listele`) de doğal anahtar için `birimId`
   istiyor; ayrıca tüm hataları sessizce boş listeye çeviriyordu.
Sorgula'dan sonra canlı kayıtla birleşen satırlarda alanlar dolu olduğu için sorun "bazen" görünüyordu.

**Yapılan değişiklikler (masaüstü + web):**
- `UyapMasa/modules/icra_core.py` `db_dosyalari_getir`: satırlara `birimId` ve `dosyaTurKod` eklendi.
- `UyapMasa/modules/dosya_core.py` `tebligat_barkod_gecmisi_listele`: `log_fn` parametresi; tanınmayan
  tür kodu hata fırlatmıyor; okuma hatası artık log'a yazılıyor.
- `UyapMasa/modules/icra_dosyalarim.py`: ayrıntı alınamasa da (UYAP kapalı vb.) barkodlar gösteriliyor
  (başta ⚠️ hata satırı); barkod satırına **tebligat türü** ve **evrak tarihi** eklendi.
- `UyapMasa/web/server.py` `icra_detay` + `UyapMasa/web/static/icra.js`: aynı davranış web'de.

- **Ek (aynı gün, kullanıcı bulgusu):** "Dosyalarım (Tümü)" ekranında 2024/11435 → "Dosya ayrıntısı
  alınamadı ve yerel veritabanında da önbelleğe alınmış bir kayıt yok". Sebep: UYAP bağlantısı kapalıydı
  (8800) ve bu ekran da hata olunca barkodları göstermeden çıkıyordu. `dosyalarim_genel.py` +
  `server.dosyalarim_detay` + `dosyalarim_genel.js` aynı şekilde düzeltildi: hata olsa da pencere açılıyor,
  üstte ⚠️ uyarı, "Barkod / Tebligat" sekmesi (Tür + Evrak Tarihi sütunları eklendi).

**Veritabanı etkisi: YOK** — yalnız okuma; şema, migration ya da veri yazımı değişmedi.

**Canlı doğrulama:**
- İzmir 7. İcra 2024/11435 → Sorgula'ya BASMADAN listeden seç → Dosya Görüntüle → "Barkod / Tebligat
  Bilgileri" bölümünde kayıtlar türleriyle görünmeli (tur bu dosyaya ulaştıysa 9 Kapalı Tebligat;
  3'ü "Maaş (Haciz Müzekkeresi)", 4'ü Banka, 1 Haciz İhbarnamesi, 1 Ödeme Emri).
- UYAP bağlantısı kapalıyken de barkodlar ⚠️ satırıyla birlikte görünmeli.
- Web arayüzünde aynı senaryo.

**Sonraki adım:** messagebox/alert yerine sekmeli Dosya Kartı (yol haritası Aşama 5).

## 3. [ÖNCELİK 2] Borçlu Kartı — ⏳ plan

Ayrıntı: `docs/BORCLU_KARTI_PLANI.md` §6 (23.09'da eklendi — tebligat/barkod gösterimi). Kod yazılmadan
önce §5 ve §6.5 açık soruları karara bağlanacak.

## 4. Bugün yapılıp canlı doğrulaması bekleyenler — 🔎

| İş | Nasıl doğrulanır |
|---|---|
| PTT sorgusu artık Playwright ile (`modules/ptt_tarayici.py`) | İlk barkod turunda log'da "🌐 PTT tarayıcısı hazır" ve PTT durumları; profil `%LOCALAPPDATA%\UyapMasa\ptt_tarayici_profili` |
| PTT hatası eski PTT durumunu silmiyor | Hata alınan barkodda eski "TESLİM EDİLDİ" korunmalı; satırda "⚠️ PTT sorgulanamadı" |
| Barkod Sorgu ekranında 3 düğme (UYAP / PTT / İkisi) | "Barkodları PTT'de Sorgula" ve "İkisini Birden Yap" canlı denenmedi |
| Maaş haczi tebligat türü "Maaş (Haciz Müzekkeresi)" | 2024/11435'te doğrulandı; **diğer icra dairelerinde ayrı ayrı** doğrulanacak (genelleme yok) |
| E-imza kutusu imzadan sonra ✅ düğmeye küçülüyor | UYAP ekranında bir belge imzala |
| Aşama 0 güvenlik yamaları | UDF'yi PIN ile bir kez imzala; web arayüzü normal açılıyor mu |

## 5. Bilinen, henüz dokunulmamış sorunlar — ⏳

- Web'de İcra "Barkod'u da Güncelle" ile Barkod Sorgu ekranı aynı iş adını ("Barkod Sorgulama")
  kullanıyor; ikisi aynı anda çalışırsa biri bitince diğerinin kaydını silebilir.
- Hukuk Dosyalarım detayında barkod/tebligat bölümü yok.
- Web'deki tüm detay pencereleri `alert()` (sekmeli kart Aşama 5).

## 6. Yol haritasında sıradaki adım — ⏳

Aşama 1 — otomatik test ağı (pytest): sayfalama, veritabanına yazma, karma sırası, barkod
sınıflandırması (bugünkü maaş örneği test verisi olarak eklenecek).
