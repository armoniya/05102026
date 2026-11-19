# İş Listesi: yeni veritabanı modeli ve büroya taşınma (ruyap)

## Context
İstek listesi B bölümü (özellikle B-1, 2, 13, 18): İş Listesi bugün Render'daki (bulut) `zs_*` tablolarında duruyor. Bu yüzden yavaş çalışıyor, ayrı bir sitede açılıyor ve "UYAP'ta aç" cokluuyap.com'a gidiyor (B-16). Model de yetersiz:
- Sonraki iş yalnız "gün sayısı" olarak tanımlanıyor (B-8) ve metinle bağlanıyor (B-6).
- Süreçler kodun içine gömülü (B-20).
- Tekrar eden işler yok (B-10, 11).
- Duruşma, keşif ve haciz işleri yok (B-12).
- Arama yok (B-5).
- Her iş onaya gidiyor (B-3).

İncelemede ayrıca şu bulundu: bildirim katmanının beklediği olay türleri (`assigned`, `submitted`…) ile kaydın yazdıkları (`status`, `created`…) uyuşmuyor. Durum, atama ve ek süre bildirimleri muhtemelen hiç gitmiyor (`zs_bildirim.rs:61` ↔ `zs_store.rs:3116`).

**Kullanıcı kararları (15.11):**
- Yeni model büro PostgreSQL'inde (ofis bilgisayarı) kurulur; buluta veri çıkmaz.
- Render'daki **açık işler** taşınır. Tamamlananlar bir kez arşiv dosyasına aktarılır, sonra bulut kopyası (onayla) silinir.
- Onay **iş başına seçimli** olur: "onay gerekir" işaretli işler onaya gider, diğerleri doğrudan tamamlanır.
- Ekran, web arayüzümüzün sol menüsünden açılan bir sayfa olur ("İcra Takip Süreçleri" ile aynı yapı). Ayrı site ya da sekme olmaz.
- Telegram ve Chrome bildirimleri **içeriksiz** olur ("Yeni bir işiniz var"); ayrıntı yalnız büroda görünür.

## Mimari (mevcut parçaların yeniden kullanımı)
- **Veritabanı:** `veritabani::baglanti()` (`src/veritabani.rs:499`). Yeni şema `src/sema/0003_is_listesi.sql` olur ve `SEMALAR`'a (3) eklenir. Testlerdeki `SEMALAR.len()` kendiliğinden uyar.
- **Uçlar:** `/__cu__/veri/is/...`, `src/icra_api.rs` ile aynı kalıpta yeni `src/is_api.rs`. Bağlantı yerleri `uyap_proxy.rs:6820` (require_auth sonrası) ve `office_agent.rs:360` (tünel). Fark şu: `handle`'a istek sahibinin kimliği geçirilir.
- **İş günü ve resmî tatil hesabı:** `src/icra_durum.rs` ile `resmi_tatil` tablosu yeniden kullanılır. Ayrı bir tatil listesi tutulmaz.
- **Dosya bağı:** işler icra şemasındaki `dosya` / `taraf` / `evrak` / `tebligat_barkod` satırlarına yabancı anahtarla bağlanır. Dosya henüz yoksa `icra_veri::dosya_kunyesi_kaydet` ile künye satırı açılır. İcra ve İş Listesi'nin "ortak hareket etmesi" bu bağ sayesinde mümkün olur.
- **Sayfa:** yeni `uyap_core/enjekte/isler.js`, `icra.js` kalıbıyla (yardımcılar `e`, `istek`, `bildir`, `stilKur`, `menuGirisi`, `uyaptaAc`). `_EK_BETIK_DOSYALARI`'na (`uyap_proxy.rs:5587`) eklenir.

## Yeni veri modeli (`0003_is_listesi.sql`, Türkçe ve öneksiz adlar)
| Tablo | İçerik |
|---|---|
| `kullanici` | hesap_adi (benzersiz), ad, rol (`yonetici`/`uye`), aktif. Buluttaki hesaplardan **yalnız ad ve rol** eşitlenir |
| `gorev` | ust_gorev_id (alt iş), baslik, aciklama, **tur** (`is`/`durusma`/`kesif`/`haciz`/`rutin`), **durum** (`acik`/`devam`/`onayda`/`tamamlandi`/`iptal`), **onay_gerekir**, oncelik, gizli, sorumlu_id, olusturan_id, **son_tarih DATE + son_saat TIME NULL** (saat isteğe bağlı, B-9), yer, sonuc (`olumlu`/`olumsuz`/`bekliyor`), onaya_sunulma, tamamlanma. Bağlar: dosya_id, taraf_id, evrak_id, tebligat_id, sablon_id, **onceki_gorev_id** (silsile zinciri), tekrar_id, eski_id (Render kimliği; taşımada tekrar önleme), arama_metni (Türkçe katlanmış; B-5) |
| `gorev_kisi` | ek sorumlu / onaylayıcı / izleyici |
| `gorev_not`, `gorev_ek` | Notlar. Ek dosyalar ofis diskinde (`%LOCALAPPDATA%\CokluUyap\ekler`, sha256 ile); R2 kullanılmaz |
| `gorev_erteleme` | "Ertesi güne aktar" / ek süre. Onay gerektirmeyen işte doğrudan uygulanır; onaylı işte onaylayıcıya gider (B-15) |
| `gorev_olay` | Geçmiş ve bildirim kaynağı; **tek olay türü listesi** (bugünkü uyuşmazlık biter) |
| `hatirlatici` | Kişiye özel zamanlı hatırlatma |
| `etiket`, `gorev_etiket` | Gerçek yabancı anahtarlarla (bugün metin anahtarı) |
| `surec`, `surec_asama` | Süreçler tabloda (B-20). Hazır "icra-kesinleşme" süreci tohum veri olarak girer |
| `sablon` | baslik, aciklama, tur, onay_gerekir, oncelik, etiketler, **sure_turu** (`takvim_gunu`/`is_gunu`/`ayin_gunu`/`tarih_sor`) + sure_deger (B-8). Ayrıca **evrak_deseni** (belge adı eşleşmesi, ör. "avukat portal tebligat talebi", B-6), **olay_turu** (icra durum olayı tetikleyicisi), surec_asama_id |
| `sablon_sonraki` | Şablon + sonuç → sonraki şablon (yabancı anahtar, metin değil) ve süre; **varsayılan değerlerle** (B-6, 7, 19) |
| `tekrar_kurali` | Rutin ve tekrar eden işler (B-10, 11): `gunluk`, `is_gunu`, `haftalik` (günler), `aylik` (ayın günü), `ay_basi_is_gunu`; saat, sorumlu, başlangıç ve bitiş. Her kuralın **tek açık örneği** olur; tamamlanınca sonraki tarih hesaplanır. Bitmeyen örnek gecikmiş olarak kalır; her gün elle aktarma gerekmez |

**Kurallar:**
- Sonraki iş önerisi tamamlamada da onaya sunmada da hemen gelir (B-7). Önerilen tarih gösterilir ve elle değiştirilebilir (B-8).
- Gecikmiş işlerin hepsi listelenir, yalnız dün değil (B-17).

## Aşamalar (her aşama ayrı onayla)

> **Durum 16.11.2026:** L1–L5 ✅ yapıldı (ruyap commit `70b071d`, `de8259e`; bulut `60ece8a`, `f76cacb`, `b37c065`). Sıradaki: L6. Ayrıntı: `Desktop/UYAP/cokluuyap/docs/16.11.2026 yapılacaklar.md`.
**L1: Şema ve veri katmanı.** ✅ YAPILDI. `0003_is_listesi.sql` ve `src/is_veri.rs`: CRUD, durum geçişleri (onay seçimli), süre hesabı, tekrar kuralı, sonraki iş üretimi, arama. Birim testleri ve gömülü PostgreSQL uçtan uca testi yazılır. Hiçbir yere bağlanmaz.

**L2: Kimlik.** ✅ YAPILDI (`src/kimlik.rs`, bulut `f76cacb`: hesap + hesap_bilet; LAN imzalı çerez).
- **Ofis bilgisayarı ve LAN:** `require_auth` dönüşündeki kullanıcı (`uyap_proxy.rs:6772`) kullanılır.
- **Tünelden gelen istekler:** sinyal sunucusunun verdiği HMAC bileti (`office_agent.rs:712 _ticket_ok`) bugün yalnız `cid:exp` alanlarını imzalıyor. Bilete **hesap adı da eklenir** (`cid:exp:hesap`), ofis bunu doğrular ve bağlantı kimliğini hesap adına eşler. Bu değişiklik `vendor_server`'a dokunur, yani **bulut yayını gerektirir**. Değişiklik yalnız giriş bilgisiyle ilgilidir; buluta veri çıkmaz.
- **Kullanıcı listesi:** ofisin üye listesi (ad ve rol) buluttaki hesaplardan alınıp `kullanici` tablosuna eşitlenir.

**L3: Uçlar.** ✅ YAPILDI (ek/belge yükleme ucu hariç). `src/is_api.rs` → `/__cu__/veri/is/…`: liste/bugün/takvim/ara, iş aç, güncelle, durum, ertele, not, ek, kişi, şablon, süreç, silsile, tekrar, hatırlatıcı. Yetki kuralları: gizli iş yalnız sahibine görünür; onay işlemlerini yalnız yönetici veya onaylayıcı yapar. Çapraz siteden gelen yazma isteği reddedilir.

**L4: Web arayüzü.** ✅ YAPILDI (`isler.js`; eski gömülü blok yeni uçlara bağlandı ama henüz silinmedi; ek yükleme yok).
- **Sol menüde "İş Listesi" sayfası (`isler.js`):** sekmeler Bugün (gecikenler dahil) / Takvim (iş, duruşma, rutin) / Tüm işler (arama ve süzgeç) / Onay bekleyenler / Rutinlerim / Şablon ve Süreçler. İş kartı sayfa içinde açılan sağ panel olur, üst üste binmez; "Ertesi güne aktar" içinde yer alır (B-15). Renkler bizim temamızdan gelir (A-10).
- **Dosya kartındaki "İş ata" penceresi:** yeni uçlara bağlanır, "onay gerekir" kutusu eklenir, belge adına göre şablon önerilir (B-6). Kaydedince onay uyarısıyla kapanır (B-14).
- **"UYAP'ta aç":** `icra.js`'deki `uyaptaAc` yolu kullanılır; aynı sayfada kaldığı için B-16 kendiliğinden çözülür.
- **Eski kod:** `uyap_proxy.rs` içindeki gömülü İş Listesi bloğu (2894–4620) ve `is_listesi.rs` köprüsü, yeni sayfa çalışınca kaldırılır. Bu, A-10 (yeşil ve mor düğmeler) sorununu da çözer.

**L5: Taşıma.** ✅ YAPILDI 16.11 13:22 (65 açık iş; bulut kopyası salt okunur, SİLİNMEDİ: silme ayrı onay ister).
1. `vendor_server`'a yalnız ofis yöneticisinin kullanabileceği bir **dışa aktarma ucu** eklenir (bulut yayını, onayla). Dönüş: açık işler, notları, alt işleri, şablonları ve etiketleri; tamamlananlar ise ayrı arşiv JSON'u.
2. ruyap'ta "Bulut İş Listesi'ni içe aktar" komutu çalıştırılır: `zs` alanları yeni modele eşlenir, `eski_id` sayesinde tekrar çalıştırmak güvenlidir. Arşiv ofis diskine kaydedilir.
3. Sayılar karşılaştırılır. Kullanıcı onay verdikten sonra bulut kopyası silinir ve Render'daki İş Listesi (ve `/is-listesi/api/otomatik`) kapatılır.

**L6: Bildirim.** ⏳ SIRADAKİ.
- **Web arayüzü:** rozet ve yeni iş uyarısı ofisten yoklanır.
- **Telegram ve push:** ofis buluta yalnız "kullanıcı X'e bildirim var" sinyali gönderir, içerik göndermez. Bulut bunu "Yeni bir işiniz var" diye iletir. Abonelikler bulutta kalır (yalnız hesap bilgisi). Bunun için `vendor_server` ucu gerekir ve bulut yayını onayla yapılır.

**L7: İcra ile ortak hareket** ⏳ bekliyor (eski plandaki Aşama 6). `icra_durum::durumlari_guncelle` bir `durum_olayi` yazdığında, aynı veritabanı işleminde `olay_turu` eşleşen şablondan iş açılır:
- tebliğ → kesinleşme tarihi işi
- iade → MERNİS
- kesinleşti → kesinleştirme talebi

Aynı dosya, borçlu ve şablon için ikinci açık iş açılmaz. Bu aşama taramanın canlıya alınmasından sonra çalışır.

**L8: Duruşma, keşif ve haciz takvimde (B-12).** ⏳ bekliyor. Ana sayfadaki "Yaklaşan Duruşmalarım" verisi, tarayıcının zaten yüklediği sayfadan okunur (`anasayfa.js:521 kartlariSusle`). UYAP'a ek istek atılmaz. Bu kayıtlar `tur=durusma` işleri olarak eklenir ya da güncellenir. Bildirimler ekranının İş Listesi ve takvimle birleşmesi bu aşamanın devamıdır.

## Riskler / açık noktalar
- **Ofis bilgisayarı kapalıyken** İş Listesi'ne erişilemez (bugün UYAP erişimi de böyle).
- **Bulut yayını:** L2 bileti ile L5 ve L6 uçları bulut yayını ister; her biri ayrı onaya tabidir.
- **Telefon erişimi:** P2P tünel üzerinden çalışır, sonraki iş.
- **Eski Render İş Listesi:** taşıma bitene kadar dokunulmaz ve çalışmaya devam eder.

## Doğrulama
- **Testler:** `cargo test --release --lib -- is_veri is_api veritabani` (yeni birim testleri: durum geçişleri, onay seçimi, süre türleri ve resmî tatil, tekrar kuralı, sonraki iş, arama Türkçe katlama). Gömülü PostgreSQL uçtan uca testi için `COKLUUYAP_PGSQL='C:\dev\pgsql_ince' … -- --ignored`.
- **Derleme:** `cargo build --release --bin uyap_app` (CARGO_* `C:\dev` altında).
- **Sayfa:** sahte UYAP sayfası ve sahte `/__cu__/veri/is` uçlarıyla Playwright (`scratchpad/taslak/icra_sayfa_test.py` kalıbı); ekran görüntüleri kullanıcıya gönderilir. Gerçek arayüz denemesini kullanıcı yapar; fare taklidi yapılmaz.
- **Taşıma:** Render ile ruyap arasında açık iş sayısı eşleşmeli; içe aktarma iki kez çalıştırılınca tekrar kayıt oluşmamalı.
- **Exe ve commit:** exe kopyalama, commit ve bulut yayınının her biri ayrı onayla yapılır.
