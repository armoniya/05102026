# İcra dosyalarını UYAP'tan güncelleme (ruyap)

## Bağlam
Büro veritabanındaki icra verisi 4 Ekim'de UyapMasa'dan aktarıldı ve o günden beri güncellenmedi. Rust'ta veritabanına
yazan parçalar var (`icra_veri::dosya_kunyesi_kaydet`, `icra_kayit::dosya_taraf_kaydet` / `borclu_kimliklerini_kaydet`),
ama UYAP'a istek atıp bunları çalıştıran katman hiç yazılmamış. Python'da bu işi `UyapMasa/modules/icra_core.py`
içindeki `IcraSorgu.ara` yapıyordu. Amaç: aynı işi Rust'ta, İcra Takip Süreçleri ekranındaki bir düğmeyle yapmak.
**Gerçek UYAP denemesi yalnız 18:00 sonrası ve kullanıcının onayıyla.** Gündüz kod, sahte yanıtlı testler ve exe hazırlanır.

## Yaklaşım: `gerekceli_karar.rs` kalıbında yeni iş modülü
Yeni dosya **`src/icra_guncelle.rs`**. Gerekçeli Kararlar'daki `Tarama` yapısının aynısı: içinde bir
`evrak_toplu::Indirme` tutar, UYAP'a her istek **`evrak_toplu::_uyap`** üzerinden gider. Böylece şunlar hazır gelir:
- eş zamanlı sorgulama reddinde 3/6/9/12 sn arayla yeniden deneme,
- ağ hatasında geri çekilme, oturum düşerse yeniden giriş,
- doğrulama (captcha) çıkarsa kullanıcıya sorma,
- iptal ve yavaşlatma,
- `evrak_toplu::sira_kilidi()`: Tüm Evrakı İndir ve Gerekçeli Kararlar ile aynı anda UYAP'a gitmez,
- kullanıcının kendi tıklamaları her zaman önce gider (`runtime::uyap_istek_izi`).

Hız: iki istek arası **3 sn** (yavaşlayınca en çok 15 sn), her **50 dosyada 60 sn mola**. Sabitler dosyanın başında durur.

### Adımlar (Python `IcraSorgu.ara` ile aynı sıra)
1. **Liste:** `search_phrase_detayli.ajx`, gövde: `dosyaDurumKod: 0`, `pageSize: 500`, `pageNumber`,
   `birimTuru2: "1101"`, `birimTuru3: "2"`, isteğe bağlı `birimId`. En çok 200 sayfa.
   - Yanıt `[kayıtlar, toplam]` biçimindeyse UYAP'ın bildirdiği toplam alınır (sayım raporu için).
   - UYAP hata zarfı (`errorCode`) ya da eş zamanlı red denemeleri tükenirse sayfa **"son sayfa" sanılmaz**: tarama
     "eksik" işaretlenir, kapanan dosya kontrolü yapılmaz.
   - Kayıt ayrıştırma için `gerekceli_karar::kayitlar_listesi` paylaşıma açılır (`pub`), kopyalanmaz.
2. **Künye:** her kayıt `icra_veri::dosya_kunyesi_kaydet(rec, Some(Icra))` ile eklenir ya da güncellenir
   (kalıcı anahtar: birim, yıl, sıra, tür). Görülen dosya kimlikleri tutulur. Yeni açılan dosyalar sayılır.
3. **Taraflar** (yalnız eksik olanlar için, gereksiz istek yok):
   - Dosyada alacaklı bağı yoksa (yeni dosya ya da eksik kayıt):
     - `dosya_borclu_list.ajx {dosyaId}` → `icra_kayit::borclu_kimliklerini_kaydet` (TCKN'li borçlular),
     - `dosya_taraf_bilgileri_brd.ajx {dosyaId}` → borçlu dışındaki roller `dosya_taraf_kaydet`'e verilir.
   - Alacaklı var ama kimliksiz borçlu varsa (`icra_kayit::borclu_kimligi_eksik_mi`): yalnız `dosya_borclu_list`.
   - `dosya_taraf_kaydet`'e **rol süzgeci** parametresi eklenir (`roller: Option<&[&str]>`). Borçlu dışı liste
     yazılırken borçlular "UYAP'ta görülmedi" diye işaretlenmesin diye; Python'daki `haric_roller=["borclu"]` karşılığı.
     Mevcut çağıranlar `None` geçer, davranışları değişmez.
   - Tek dosyadaki hata taramayı durdurmaz: uyarıya yazılır, sonraki dosyaya geçilir. Oturum hatası taramayı durdurur.
4. **Kapanan dosyalar:** yalnız liste eksiksiz alındıysa ve kapsam bütün dosyalar ya da tek daireyse.
   - Veritabanında Açık olup UYAP'ın açık listesinde görünmeyen icra dosyaları aday olur.
   - **Emniyet freni:** aday sayısı `max(50, görülen dosya sayısı)`'nı aşarsa bu adım atlanır (liste şüpheli).
   - Her aday için `search_phrase_detayli` (`dosyaDurumKod: 1`, `dosyaYil`, `dosyaSira`, `birimId`).
     Dosya no ve tür kodu tutarsa künye güncellenir; Kapalı'ya geçiş `dosya_kunyesi_kaydet`'in mevcut kuralıyla
     işlem durumunu Pasif yapar ve geçmişe yazar.
   - Hiçbir listede bulunamayan dosyaya **dokunulmaz**, yalnız sayılır.
5. **Sayım raporu:** UYAP toplamı ↔ alınan; alınan ↔ veritabanına yazılan; veritabanındaki açık dosya sayısı ↔ UYAP.
   Python `_sayim_raporu` ile aynı üç satır.
6. **Hesap:** `icra_durum::durumlari_guncelle(c, None, bugün, log)`. Kesinleşme / tebliğ / takip aşaması yeniden
   hesaplanır ve L7 otomatik işleri (`is_otomatik`) aynı yoldan açılır.

Veritabanı yazımları `tokio::task::spawn_blocking` içinde `veritabani::baglanti()` ile yapılır (icra_api kalıbı).
UYAP çağrıları test edilebilsin diye çekirdek akış, istek atan işlevi parametre olarak alır. Gerçekte bu
`_uyap` sarmalayıcısıdır, testte hazır yanıt veren sahte işlev.

### Uçlar (`jobs.rs`'e `/__uyap_agent__/icra_guncelle` öneki; gerekceli ile aynı `toplu_is_acik` denetimi)
- `POST  /icra_guncelle` `{"birim_id"?: "…"}` → 201 {tarama} (sürmekte olan varsa onu döner)
- `GET   /icra_guncelle` → {tarama|null} (süren ya da son tarama)
- `POST  /icra_guncelle/<id>/iptal` · `POST /icra_guncelle/<id>/captcha {"token"|null}`

Durum sözlüğü:
- `asama`: liste / künye / taraf / kapanan / hesap / bitti
- ilerleme: `i`, `toplam`, `kalan_sn`, `mola_kalan_sn`
- sayılar: `uyap_toplam`, `alinan`, `yazilan`, `yeni_dosya`, `taraf_cekilen`, `kapandi`, `bulunamadi`
- `tam` (liste eksiksiz mi), `uyarilar` (son 20), `rapor` (sayım satırları), `captcha`

### Ekran (`uyap_core/enjekte/icra.js`)
- "Yeniden hesapla"nın yanına **"UYAP'tan güncelle"** düğmesi. Tıklanınca küçük bir panel açılır:
  - Kapsam: "Bütün açık dosyalar" ya da tek icra dairesi seçimi (mevcut daire seçenekleri listesinden).
  - Saat 08:30–18:00 arasında uyarı gösterilir: "Mesai saatinde UYAP'ı yavaşlatabilir." Engellemez.
  - Başlat.
- Çalışırken panelde:
  - aşama, ilerleme çubuğu (`i / toplam`), kalan süre, mola sayacı, **Durdur** düğmesi,
  - son uyarılar.
  - Sayfa kapatılıp açılınca süren tarama `GET` ile yeniden bulunur.
- Doğrulama çıkarsa Turnstile kutusu gösterilir; gerekceli panelindeki `captchaGoster` mantığının küçük bir kopyası.
- Bitince sayım raporu gösterilir, liste ve özet yenilenir; durum satırındaki "son güncelleme" tarihi değişir.
- Durdurulan tarama yeniden başlatılabilir: künye ekleme/güncelleme tekrar çalışınca zarar vermez, tarafları tamam
  olan dosyalara yeniden istek atılmaz.

## Değişecek dosyalar
- **yeni** `src/icra_guncelle.rs` (iş, akış, uçlar, birim + sahte UYAP testleri) · `src/lib.rs` (modül)
- `src/jobs.rs` (önek ve yönlendirme)
- `src/icra_kayit.rs` (`dosya_taraf_kaydet`'e rol süzgeci; çağıranlar `None`)
- `src/gerekceli_karar.rs` (`kayitlar_listesi` → `pub`)
- `uyap_core/enjekte/icra.js` (düğme, panel, captcha)
- `araclar/icra_suzgec_test.py`'ye güncelleme paneli adımları (sahte uçlarla)
- `docs/16.11.2026 yapılacaklar.md` (ilerleme notu)

## Doğrulama
1. **Birim testleri:**
   - kayıt / toplam ayrıştırma, hata zarfının "son sayfa" sayılmaması,
   - kapsam kontrolü ve emniyet freni,
   - sayım satırları.
2. **Uçtan uca** (`bash /c/dev/test_uctan.sh`; `veritabani::tests::uctan_uca` içinden yeni senaryo), sahte UYAP ile:
   - 2 sayfalık liste; bir sayfada eş zamanlı red (tekrar dener, sonra başarır); hata zarfı → `tam=false` ve
     kapanan kontrolü yok,
   - yeni dosya → borçlu (TCKN'li) + alacaklı yazılır; borçlular "görülmedi" işaretlenmez,
   - tarafları tam dosyaya ikinci kez istek atılmaz,
   - açık listede olmayan dosya Kapalı listede bulunursa Kapalı + Pasif; bulunamayana dokunulmaz,
   - fren: aday çoksa adım atlanır,
   - sonunda kesinleşme hesabı çalışır.
3. `cargo test --release --lib` (bütün birim testleri) ve tarayıcı denemesi: panel, ilerleme, durdurma, captcha
   kutusu, bitiş raporu (sahte uçlar).
4. Exe derlenir. Yerine koymak ayrıca onayla, kullanıcı programı kapatınca.
5. **18:00 sonrası, kullanıcıyla:**
   - önce **tek ve küçük bir icra dairesi**: sayım raporu ile UYAP ekranındaki dosya sayısı karşılaştırılır, birkaç
     dosyanın tarafları ekranda kontrol edilir;
   - doğruysa **bütün açık dosyalar**. Tahmini süre: liste ~14 sayfa; yeni ve kapanan dosya sayısına göre birkaç
     on dakika ile 1–2 saat. Kalan süre panelde görünür.
   - "Eş zamanlı sorgulama" uyarısı sık çıkarsa durdurulur ve hız düşürülür.

## Kapsam dışı (sonraki adım)
- Barkod / tebligat güncellemesi (`barkod_sorgu.py` → `icra_tarama`'nın istek atan katmanı): 3. madde.
- Kapalı dosyaların taraflarının toplu çekilmesi (36 bin dosya; gerekirse ayrı gece işi).
- Gece kendiliğinden çalışan güncelleme turu.
