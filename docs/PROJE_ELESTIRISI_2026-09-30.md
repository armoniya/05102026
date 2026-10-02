# Proje Eleştirisi ve İyileştirme Planı — 30.09.2026

Kullanıcı isteği: "projeyi başından sonuna inceleyip acımasızca eleştir" → ardından "tüm önerileri uygulayalım".
Bu dosya yeni bir oturumun TEK BAŞINA okuyup uygulamaya geçebilmesi için yazıldı. Rakamlar 30.09.2026'da
ölçüldü (`.venv` ve migration'lar hariç ~65.000 satır, 174 kaynak dosya).

**Uygulamada uyulacak kullanıcı kuralları (bellekte de var):** iki arayüz (Tkinter masaüstü + web) KALIR,
ortak servis katmanı kullanılır · toplu turlara kayıt başına ek iş/istek ekleme (performans; veri çok büyük)
· canlı UYAP bulgusu "muhtemelen ortak" diye genellenmez · UYAP'tan canlı görülmeden alan uydurulmaz ·
İcra'da Safahat ücretli/kotalı · hardcoded/yıkıcı işlemlerden önce onay · veri listelerinde satır sınırı yok.
Bekleyen kullanıcı işleri ayrıca: `docs/YAPILACAKLAR_2026-09-28.md` §0.
**Güvenlik taraması (01.10, pip-audit/Gitleaks/Semgrep/elle/ZAP):** `docs/GUVENLIK_TARAMASI_2026-09-30.md` —
oradaki "Önerilen sıra" (önce aiohttp vb. paket yükseltmeleri, sonra yerel jeton atlatma 4.1) bu planın
Adım 1'inden hemen sonra (git + yedek kurulunca) yapılmalı.

---

## A. En ağır üç sorun

### A1. Sürüm kontrolü YOK
- Klasörde git deposu yok (`.gitignore` özenle yazılmış ama `.git` yok). 65 bin satırlık, hukuki sonuç doğuran
  programın tek kopyası masaüstü klasöründe.
- 30.09'da 2 migration (0029, 0030) uygulandı, 2 dosya silindi (`itiraz_kontrol.py`, `static/itiraz.js`) —
  geri alma yolu yok, "dün ne değişti" sorusunun cevabı yok.
- cokluuyap.com'un eski sürümü çalıştırması ve deploy yolunun bilinmemesi de bunun sonucu.
- `.gitignore`'da `**/vendor_deploy/` "ayrı git deposu olan alt proje" olarak geçiyor ama diskte bulunamadı.

### A2. Programın asıl çıktısı (kesinleşme) yanlış olabilir ve bu görünmüyor
- `ResmiTatil` tablosu **boş** → bayrama denk gelen süre sonunda erken "Kesinleşti" yazılabilir.
- Her dosyaya Örnek 7 (7 gün) uygulanıyor; kambiyo (Örnek 10/12) 5 gün. Takip türü 6.786 açık İcra dosyasının
  yalnız **22**'sinde var (kök neden: `IcraTakipDetay` yalnız "Dosya Görüntüle → canlı" yolunda yazılıyor).
- Açık İcra borçlularının ~%55'inin (4.211 / ~7.700) kesinleşmesi boş.
- Arayüz belirsizliği göstermiyor: "Kesinleşti" yazıyor, "tatil tablosu boş / takip türü varsayıldı" demiyor.
- Ayrıntılı plan: `YAPILACAKLAR_2026-09-28.md` §6, §8; süreler `KESINLESME_SURELERI_TAKIP_TURU.md`.

### A3. Test neredeyse yok
- Tüm projede 3 test dosyası: `UDF Converter GUI/test_gui_backend.py`, `UDF Converter GUI/test_imza.py`,
  `UyapMasa/modules/test_kesinlesme_core.py` (4 test, yalnız tarih matematiği; `python -m unittest
  test_kesinlesme_core` → OK).
- `dosya_core._borclu_kesinlesme_hesapla`, `durdurma_kesinlesme_kodu`, `_borclu_tebligat_hesapla`,
  `_mazbata_sonucu`, `tebligat_borclusunu_bul` gibi HUKUKİ KURALLARIN testi yok. Kurallar 30.09'da üç kez
  değişti; doğrulama elle yazılmış tek seferlik DB sorgularıyla yapıldı.

## B. Mimari

### B1. Kopya kod
- **Birebir aynı** (MD5 eşit): `UDF Converter GUI/converter.py` = `Uyap Haricen Giriş/uyap_core/udf_converter.py`;
  `UDF Converter GUI/cades.py` = `uyap_core/cades.py`; `UDF Converter GUI/akis_pkcs11.py` = `uyap_core/akis_pkcs11.py`.
- SGK sorgusu 3 yerde: `Sorgu/SGK Sorgu/sgk_sorgu_gui.py`, `Uyap Haricen Giriş/uyap_gecit/core/sgk.py`
  (552 ortak satır), `UyapMasa/modules/sgk_core.py` (+ `uyap_core/mts/sgk.py`).
- MTS en az 3 yerde: `Dosya Açılış/MTS Takip Açılış/*`, `UyapMasa/modules/mts_takip.py`, `uyap_core/mts/*`.
- 4 ayrı `takip.py` (`uyap_core/ipotek`, `uyap_core/mts`, `uyap_core/xml_takip`, `uyap_gecit/core`).
- UYAP'a istek atan kod dağınık (yol haritası Aşama 2 "tek UYAP istemcisi" başlanmamış).

### B2. Her şeyi yapan dev dosyalar
- `UyapMasa/modules/dosya_core.py` ~2.600 satır / 100 fonksiyon: UYAP istemcisi + DB ingest + kesinleşme
  hukuk kuralları + zamanlayıcı + gece turu + eski-kod koruması (`kod_eski_mi`).
- `UyapMasa/web/server.py` ~2.550 satır, elle yazılmış `if path == ...` yönlendirme zinciri.
- Diğer büyükler: `barkod_sorgu.py` ~1.760, `barkod_sorgu_ekrani.py` ~1.550, `uyapmasa.py` ~1.470,
  `mts_takip.py` ~1.350, `logger_core.py` ~1.260, `icra_dosyalarim.py` ~1.150, `icra_core.py` ~1.090.
- Hukuki kural ile ağ kodu aynı dosyada → kural tek başına test edilemiyor.

### B3. İki arayüz = iki kat iş
- Her özellik Tkinter + web'de ayrı yazılıyor (ör. "İtiraz Kontrolü" 29.09'da ikisine eklendi, 30.09'da
  ikisinden silindi). Karar: iki arayüz KALIR (kullanıcı) → çözüm iş mantığının ortak servise çekilmesi,
  arayüzlerin ince kalması.

### B4. Sessiz hata yutma
- 959 geniş `except` (`except:` / `except Exception[: pass|continue|return]`); `server.py`'de 102,
  `mts_bot.py` 73, `logger_core.py` 43, `uyapmasa.py` 41, `uyap_app.py` 40, `dosya_core.py` 35.
- Ör. `kesinlesme_durumlarini_guncelle` DB hazırlanamazsa sessizce 0 döner; hata yalnız log penceresinde.

### B5. Paketleme yok
- 40 dosyada `sys.path.insert/append`; modüller "bare import" ile birbirini çağırıyor.
- `requirements.txt`: dev + prod karışık (pyinstaller, pyflakes), iki PostgreSQL sürücüsü (psycopg +
  psycopg2-binary), 4 web altyapısı (http.server, Django, FastAPI/uvicorn, aiohttp), `PyKCS11>=` sabitlenmemiş.

## C. Veri modeli (`models/icra_models/models.py`)

### C1. Şema hazır, veri yok
- 6 model tanımlı ama yazan kod yok: `IdariYargiDetay`, `DosyaSafahat`, `DosyaTahsilatHarc`,
  `DosyaReddiyat`, `IcraDosyaHesabi`, `BorcluSorgu`. `IcraTakipDetay` 22/6.786. `ResmiTatil` 0 satır.

### C2. Tarihler metin
- `TebligatBarkod.son_islem_tarihi` (kesinleşmenin asıl girdisi = tebliğ tarihi) ve `evrak_tarihi`
  "GG/AA/YYYY" CharField; her hesapta regex ile ayrıştırılıyor (`dosya_core._teblig_tarihini_coz`).
  DB'de tarih aralığı sorgusu yapılamıyor. Öneri: ham metin kalsın + yanına çözülmüş `DateField`
  (ör. `teblig_tarihi`, `evrak_tarihi_d`), ingest'te doldurulsun, bir kerelik geri doldurma.
- Aynı durum: `DosyaSafahat.safahat_tarihi`, `DosyaTahsilatHarc.tahsilat_tarihi`, `ItirazBelgesi` (silindi).

### C3. Eskimiş yorumlar (yanlış yorum, yorumsuzluktan zararlı)
- 489 satır yorum tarih ya da "kullanıcı bulgusu/isteği/kararı" içeriyor — kod günlük gibi.
- Doğrulanmış YANLIŞ yorumlar:
  - `DosyaTaraf.KesinlesmeDurumu` docstring: "girişi UYAP'tan OTOMATİK ÇEKİLMİYOR … varsayılan boş" →
    artık her gün `kesinlesme_durumlarini_guncelle` ile hesaplanıyor.
  - `DosyaTaraf.kesinlesme_durumu` üstü: "Şimdilik hep boş yazılır … ileride kesinleşme-girişi fonksiyonu".
  - `dosya_core.dosya_ayrinti_kaydet` docstring: "Dosya'da yargi_turu alanı YOK" → alan var, zorunlu (0020/0025).
  - `TebligatBarkod.borclu` üstü: "yalnız dosyada TEK borçlu varsa otomatik atar" → 24.09'dan beri evrak
    başına `tebligat_borclusunu_bul`.
  - `DosyaTaraf.TebligatDurumu` docstring: "OTOMATİK türetilmiyor … kurallar henüz belirlenmedi" →
    `_borclu_tebligat_hesapla` türetiyor.

## D. Süreç ve operasyon

### D1. Karar çalkantısı
- Kural netleşmeden arayüz + DB'yi kapsayan özellik kuruluyor, sonra sökülüyor: ItirazBelgesi (2 gün),
  gece turu (1 gün sonra rafa), 20 sn oto-tazeleme (kuruldu → imza bekçisi → kaldırıldı), "durdurma tüm
  borçlulara" kuralı (aynı saat "Diğer"de yanlış çıktı). Her tur: migration + iki arayüz + DB yeniden hesap.
- Kural: önce kural yazılı hale gelsin → 2–3 canlı örnekle doğrulansın → sonra kod.

### D2. Bilgi üç yerde, birbirini tutmuyor
- Kod yorumları + `docs/` (30 dosya; 404 KB konuşma dökümü `KONUSMA_2026-09-23_24_…md`; birden çok
  tarihli YAPILACAKLAR: `_2026-09-25`, `_2026-09-28` …) + bellek notları.

### D3. Operasyon kör noktaları
- Eski kodla açık pencere 30.09'da DB'yi ezdi → yama: `dosya_core.kod_eski_mi` (dosya mtime). Asıl çözüm
  sürüm/dağıtım yönetimi.
- DB yedek düzeni görülmedi; yalnız işlem öncesi elle alınmış JSON yedekleri (`%LOCALAPPDATA%\UyapMasa\yedek`).
  44 bin dosyalık tek yerel PostgreSQL (`db_baslat.py`, parola `%LOCALAPPDATA%\UyapIcra\dbpass.secret`).
- Bozuk modül adları (Logger'ın ürettiği, Türkçe karakter düşmüş): `UyapMasa/modules/htiyati_Haciz.py`,
  `potek_takip.py`, `XML_TAK_P_A_ILI_2807.py`, `Mts_evirme.py`, `Vekalet_Sunma.py`.

## E. İyi olanlar (korunacak)
- Web güvenliği: CSRF (`server.py _csrf_ok`), DNS rebinding koruması, varsayılan 127.0.0.1, HttpOnly/SameSite
  çerez, hassas dosyalar `.gitignore`'da; vendor admin TOTP'li.
- "Canlı görülmeden alan uydurma" disiplini, bulgu bazlı doğrulama.
- UYAP eşzamanlı-istek koruması, Safahat ücret farkındalığı, performans bilinci.

---

## F. Uygulama planı (sıralı; her adımdan önce kullanıcıya kısa özet + onay)

### Adım 1 — Güvenlik ağı (önce bu, diğerleri buna dayanıyor)
1. **git init** + ilk commit. Önce `.gitignore`'u gözden geçir (hassas dosyalar: çerezler, `accounts.json`,
   `uyap_app_config.json`, `gw_local_token`, tarayıcı profilleri, çıktı Excel/PDF'leri). `git status` ile
   izlenecek dosya listesini kullanıcıya göster; kişisel veri/sır içeren dosya girmediğini doğrula. Uzak
   depo (GitHub özel repo) kullanıcı kararı.
2. **Otomatik DB yedeği:** günlük `pg_dump` (sıkıştırılmış, N gün saklama) — `db_baslat.py`'deki PostgreSQL
   ikilileriyle; Windows Görev Zamanlayıcı ya da uygulama açılışında günde bir kez. Geri yükleme adımı
   belgelenip bir kez denenir.

> **Adım 1 TAMAM (02.10.2026):** (1) yerel git deposu, ilk commit b8f1431. Uzak depo yok (kullanıcı kararı).
> TCKN'ler temizlendi; Logger TCKN'yi çalıştırırken soruyor. (2) `db_yedek.py`: açılışta günde bir yedek,
> 14 adet saklanıyor. Ayrı DB'ye geri yükleme denendi, satır sayıları aynı çıktı. Ayrıntı: `docs/DB_YEDEK.md`.

### Adım 2 — Kesinleşmenin doğruluğu ve görünürlüğü
1. Kesinleşme kurallarını `dosya_core`'dan saf bir modüle taşı (ör. `kesinlesme_kurallari.py`: DB/ağ yok,
   girdi = borçlu + tebligat listesi + dosya durumu + tatiller + takip türü). `dosya_core` yalnız çağırır.
2. **Senaryo testleri** (unittest, mevcut `test_kesinlesme_core.py` deseniyle): tebliğ+mazbata+süre →
   Kesinleşti; mazbata dönmemiş → Bekliyor; İADE; e-tebligat evrak+5; elle itiraz önceliği; tüm durdurma
   nedenleri + tanımsız; Konkordato/Vefat/Vasi korunur; kapalı dosya; birden çok ödeme emri; hafta sonu/
   tatil/arife kaydırması. Mevcut davranış önce testle SABİTLENİR, sonra değişiklik yapılır.
3. Sonra `YAPILACAKLAR` §6 (kapalı dosya, "Kesinleşmeden kapandı"), §8.2 (takip türü verisi), §8.3 (süre
   tablosu — kullanıcı onayından sonra), §8.1 (tatiller — deploy/yerel giriş kararından sonra).
4. **Görünürlük:** kesinleşmenin "varsayımlı" olduğu durumlar ekranda belirtilsin (tatil tablosu boş/eksik
   yıl, takip türü henüz yok → Örnek 7 varsayıldı). Biçim (ikon/not sütunu) kullanıcıya sorulur.

### Adım 3 — Kopyaları birleştir
1. Üç birebir dosya: tek kaynak (`Uyap Haricen Giriş/uyap_core/`), `UDF Converter GUI` oradan import etsin
   (ya da ortak bir `ortak/` paketi) — önce iki tarafın import yolları ve PyInstaller paketlemesi kontrol.
2. SGK / MTS / `takip.py` kopyaları: önce farklar çıkarılır (hangisi güncel?), kullanıcıya sunulur,
   sonra birleştirilir. Genelleme yok — her kopyanın hangi ekrandan kullanıldığı tek tek doğrulanır.
3. Tek UYAP istemcisi (yol haritası Aşama 2): istek atma + eşzamanlı-red koruması + hata zarfı tespiti tek
   yerde (`dosya_core._post_eszamanli_korumali` / `_yanit_hata_zarfi_mi` çekirdek alınabilir).

### Adım 4 — Dev dosyaları böl (davranış değişmeden, testler yeşil kalarak)
- `dosya_core.py` → UYAP istemcisi / ingest / kesinleşme / zamanlayıcı ayrı modüller; eski adlar geçiş
  süresince yeniden dışa aktarılır (import kırılmasın).
- `server.py` → uç noktalar gruplara (icra, barkod, sgk, auth …) ayrılır; yönlendirme tablo ile.

### Adım 5 — Hata yönetimi
- 959 geniş `except`'i tara: (a) gerçekten yutulması gerekenler kalsın ama loglansın, (b) veri doğruluğunu
  etkileyenler (kesinleşme, ingest, barkod DB yazımı) kullanıcıya görünür hata/uyarı versin. Önce
  `dosya_core`, `barkod_sorgu`, `icra_core`, `server.py`.

### Adım 6 — Veri modeli temizliği
1. Tarih alanları: `TebligatBarkod`'a çözülmüş `DateField`'lar + ingest + geri doldurma (migration; önce yedek).
2. Eskimiş yorumları düzelt (C3 listesi) ve tarihli "kullanıcı bulgusu" anlatımlarını yorumlardan çıkar;
   gerekçe gerekiyorsa kısa "neden" cümlesi kalsın, tarihçe `docs/`'a.
3. Veri yazılmayan 6 model: kullanıcıyla konuşulur — ingest yazılacak mı, yoksa kullanılana dek kaldırılsın mı.

### Adım 7 — Paketleme ve bağımlılıklar
- `requirements.txt` → çalışma (`requirements.txt`) + geliştirme (`requirements-dev.txt`); tek PostgreSQL
  sürücüsü (hangisinin nerede kullanıldığı önce grep ile doğrulanır); `PyKCS11` sabitlenir.
- `sys.path` hack'lerini azaltmak için proje paket yapısına geçiş planı (büyük iş; Adım 4 ile birlikte).
- Bozuk adlı modüller (D3) doğru adlara taşınır; Logger'ın ürettiği modüller ayrı klasöre.

### Adım 8 — Belgeler ve süreç
- `docs/` düzeni: tek güncel `YAPILACAKLAR.md` (eski tarihliler `docs/arsiv/`), konuşma dökümleri
  `docs/arsiv/`'e; `VERITABANI_BULGULARI_*` model değişiklikleriyle güncellenir (30.09 değişiklikleri:
  ItirazBelgesi kaldırıldı — 0029; durdurma nedenleri kesinleşmede — 0030).
- Yeni özellik kuralı (D1): kural yazılı → canlı örnek → kod.
- Dağıtım: masaüstü/web süreçleri güncelleme sonrası yeniden başlatılır (`kod_eski_mi` uyarısı);
  cokluuyap.com deploy yolu netleşince `docs/`'a yazılır.

---

## G. Bu oturumda (30.09) yapılan değişiklikler — bağlam
- ItirazBelgesi + İtiraz Kontrolü ekranları kaldırıldı (migration 0029; `itiraz_kontrol.py`, `itiraz.js` silindi).
- Kesinleşmeye "Durdurulmuş: …" değerleri eklendi (migration 0030; `dosya_core.DURDURMA_NEDENLERI`,
  `durdurma_kesinlesme_kodu`); DB yeniden hesaplandı, tutarlılık kontrolü 0 hata.
- Yeni belgeler: `KESINLESME_SURELERI_TAKIP_TURU.md`, `KONTROL_COKLU_ODEME_EMRI_2026-09-30.md`,
  `KONTROL_MUKERRER_TEBLIGAT_2026-09-30.md`; `YAPILACAKLAR_2026-09-28.md` §0, §5–§8 güncellendi.
- Değişiklikler arayüzde (masaüstü/web) açılıp denenmedi; uygulamalar yeniden başlatılmalı.
