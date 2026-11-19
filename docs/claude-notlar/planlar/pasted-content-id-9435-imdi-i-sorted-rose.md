# İş Listesi — Süreç / Aşama / Etiket ile filtreleme planı

## Bağlam

Büroda icra takibinden sonraki kontroller sabit bir zincir: **tebligat oluşturuldu mu → PTT'ye teslim edildi mi
(barkodda "kayıt yok" → "kabul") → tebliğ edildi mi → mazbata icraya döndü mü → itiraz var mı → kesinleştirme
talebi kabul edildi mi → kesinleşme icra dairesinde gerçekleşti mi (sorgu ekranında "kesinleşmediği için sorgu
yapılamaz" uyarısı gelmiyor mu)**. Kullanıcı bu adımlar için işler ve büro şablonları açtı; ama İş Listesi'nde
işler yalnız durum (açık/devam/onayda…) ve serbest aramayla süzülebiliyor. Bir dosyanın/borçlunun zincirin
neresinde olduğu görülemiyor.

Kodda bugünkü durum (incelendi):
- `zs_store.py`: `zs_tasks` işin **hangi şablondan açıldığını tutmuyor**; şablon başlıktan tahmin ediliyor
  (`uyap_proxy.py` `sablonBul`: "HAMZA OĞUZ – Tebligat kontrol" → "Tebligat kontrol"). Sonraki iş zinciri
  (`sonuclar` → `sonrakiIsiAc`) de yalnız başlık + gün ile kuruluyor.
- `HAZIR_SABLONLAR` (kodda, değişmez) + `zs_templates` (büro, DB). `action` alanı Aşama 3 için anahtar taşıyor
  ama yürütülmüyor — aynı desen otomatik kontrol için de kullanılabilir.
- `zs.js` süzgeçleri (`FILTERS`) yalnız durum/gecikme; liste istemcide süzülüyor (`shownTasks`).
- Sonraki aşamada bağlanacak veri UyapMasa'nın **yerel** PostgreSQL'inde (Django `models/icra_models`):
  `TebligatBarkod` (barkod, ptt_durumu, tebligat_mazbatasi_var, tebligat_turu, borclu),
  `DosyaTaraf` (tebligat_durumu, itiraz_edildi_mi, kesinlesme_durumu/tarihi), `kesinlesme_core`.
  İş Listesi ise cokluuyap.com'daki PostgreSQL'de. Ortak anahtar: **birim_id + dosya_no (+ taraf adı)** —
  `zs_tasks` zaten `(office_id, birim_id, dosya_no)` indeksli.

Kullanıcı kararları: (1) **Süreç + aşama + serbest etiket**; (2) ana süreç, şablonlar ve etiketler **programda
hazır** gelir, yönetici ve kullanıcılar **kendileri için** özel etiket ve şablon ekleyebilir; (3) eski işler
**başlıktan şablon bulunarak** otomatik bağlanır.

## Kavramlar

| Kavram | Nerede tanımlı | Kim ekler | İşe nasıl gelir |
|---|---|---|---|
| **Süreç** (ör. "İcra takibi → Kesinleşme") | Kodda `HAZIR_SURECLER` | Yalnız program | Şablondan |
| **Aşama** (sürecin sıralı adımı) | Süreç tanımının içinde | Yalnız program | Şablondan (işte saklanır, elle değiştirilebilir) |
| **Şablon** | Hazır: kodda; büro: `zs_templates` | Herkes (bugünkü gibi) | İş Ata'da seçilince `sablon` anahtarı işe yazılır |
| **Etiket** | Hazır: kodda `HAZIR_ETIKETLER`; özel: `zs_tags` | Herkes; kişisel ya da büroyla paylaşılan | Şablonun varsayılan etiketleri + elle |

### Hazır süreç: `icra-kesinlesme` — "İcra takibi → Kesinleşme" (borçlu bazlı)

| # | Aşama anahtarı | Ad | Hazır şablon | `otomatik` (Aşama 4 kancası, şimdi yürütülmez) | Sonraki aşamada veri kaynağı |
|---|---|---|---|---|---|
| 1 | `tebligat` | Tebligat oluşturuldu | h-odeme-emri (Ödeme emri kontrol) | `db:tebligat_olustu` | `TebligatBarkod` (tür "Ödeme Emri") var mı |
| 2 | `ptt` | PTT'ye teslim | **yeni** h-ptt-teslim "PTT teslim kontrol" (barkod) + h-barkod | `db:ptt_kabul` | `TebligatBarkod.ptt_durumu` "kayıt yok" dışı |
| 3 | `teblig` | Tebliğ edildi | h-teblig-durum, h-tebligat, h-21-2, h-adres | `db:teblig` | `DosyaTaraf.tebligat_durumu` |
| 4 | `mazbata` | Mazbata döndü | **yeni** h-mazbata "Mazbata kontrol" | `db:mazbata` | `TebligatBarkod.tebligat_mazbatasi_var` |
| 5 | `itiraz` | İtiraz kontrolü | **yeni** h-itiraz "İtiraz kontrol" | `db:itiraz` | `itiraz_edildi_mi`, `kesinlesme_durumu=itiraz/durd_*`, `kesinlesme_core` tarihi |
| 6 | `kes-talep` | Kesinleştirme talebi kabul | **yeni** h-kes-talep "Kesinleştirme talebi kontrol" | `uyap:kesinlestirme_talebi` | Henüz yok (evrak/safahat) |
| 7 | `kesinlesti` | Kesinleşti | h-kesinlesme (Kesinleşme kontrol) | `uyap:sorgu_kesinlesme` | Sorgu ekranı denemesi (UYAP'ta) |

- Hazır şablonların `sonuclar` dalları zinciri izleyecek şekilde güncellenir (olumlu → sonraki aşamanın
  şablonu; ör. Tebliğ durumu kontrol olumlu → **Mazbata kontrol**, mazbata olumlu → İtiraz kontrol (+7 gün,
  ileride `kesinlesme_core` tarihinden), itiraz yok → Kesinleştirme talebi kontrol …). Böylece mevcut "Onaya
  gönder → sonraki işi aç" akışı aşamayı kendiliğinden ilerletir; ayrı bir motor gerekmez.
- Haciz talepleri, Reddiyat/Tahsilat, Müvekkile bildirim gibi şablonlar süreçsiz kalır ya da ileride ikinci
  bir hazır süreç ("Haciz") altına alınır — şimdilik süreçsiz.

### Hazır etiketler (`HAZIR_ETIKETLER`, ör.)
`Tebligat`, `Kesinleşme`, `Haciz`, `Tahsilat`, `Müvekkil bildirimi`, `Acil dosya`. Hazır şablonların
varsayılan etiketleri bunlardan seçilir (ör. süreçteki 1–4 → `Tebligat`, 5–7 → `Kesinleşme`).

## Veri değişiklikleri — `Uyap Haricen Giriş/zs_store.py`

Mevcut desen korunur (`_ADDED_COLS`, `_SCHEMA`, `_POST_INDEXES`; SQLite + PostgreSQL).

- `zs_tasks` ek sütunlar: `sablon TEXT` ("h-tebligat" | büro şablon kimliği "b-12" | '' = eşleşmedi),
  `surec TEXT`, `asama TEXT`. İndeks: `(office_id, surec, asama)`.
- `zs_templates` ek sütunlar: `asama TEXT` ("icra-kesinlesme/teblig" biçiminde), `etiketler TEXT` (JSON anahtar listesi).
- Yeni tablo `zs_tags`: `id, office_id, owner_id (NULL = büroya açık, dolu = yalnız sahibine), name, color,
  created_by, created_at` (+ büro/ad benzersizliği Python'da, `_template_name_taken` gibi Türkçe duyarlı).
- Yeni tablo `zs_task_tags`: `task_id, tag TEXT ("h-tebligat" | "12"), added_by, created_at`,
  `PRIMARY KEY (task_id, tag)`; yalnız ana işler.
- Sabitler: `HAZIR_SURECLER`, `HAZIR_ETIKETLER`; `HAZIR_SABLONLAR` öğelerine `asama` + `etiketler`; 4 yeni hazır şablon.
- Yeni/değişen yöntemler:
  - `create_task(..., sablon=None, etiketler=None)`: şablon anahtarı çözülür (hazır ya da büronun şablonu),
    `surec/asama` şablondan; şablonun etiketleri + verilenler `zs_task_tags`'e. Olay: `created` detayına sablon/asama.
  - `update_task`: `asama` alanı (elle düzeltme; yalnız tanımlı aşama) → `edited` olayı.
  - `set_task_tags(actor, task_id, add, remove)` → olay `tags` (eklenen/çıkarılan adlar).
  - `list_tags(user)` → `{hazir, buro, kisisel}`; `create_tag / update_tag / delete_tag` (ekleyen ya da
    yönetici; kişisel etiketi yalnız sahibi). Silinen etiket `zs_task_tags`'ten de silinir.
  - `list_tree` / `task_detail`: her ana işe `tags` (izleyiciye görünenler: hazır + büro + kendi kişiselleri),
    `sablon`, `surec`, `asama` döner. `delete_task` `zs_task_tags`'i de siler; `snapshot_task` ekler.
  - `backfill_sablon()`: `sablon IS NULL` ana işler için başlığın " – " sonrası (yoksa tümü) Türkçe duyarlı
    anahtarla hazır + büro şablon adlarıyla eşlenir → `sablon/surec/asama` + şablonun etiketleri yazılır;
    eşleşmeyene `''` (bir daha taranmaz). `zs.py` açılışında bir kez çağrılır. `_template_name_taken`'daki
    `key()` ortak yardımcıya (`_ad_key`) çıkarılıp ikisi de onu kullanır.
  - `list_templates` / `_template_fields`: `asama`, `etiketler` alanları; `HAZIR` dönüşüne `surecler` eklenir.

## API — `Uyap Haricen Giriş/zs.py`

- `GET /is-listesi/api/surecler` → `HAZIR_SURECLER`.
- `GET|POST /api/etiketler`, `POST|DELETE /api/etiketler/{id}`.
- `POST /api/tasks/{id}/etiketler` `{add: [...], remove: [...]}` (işi görebilen ve atanan/yönetici).
- `api_task_create` / `api_task_update`: `sablon`, `etiketler`, `asama` alanlarını geçirir.
- `api_template_create/update`: `asama`, `etiketler`.
- Denetim kaydı (`_api` sarmalayıcısı) yeni uçlarda da mevcut desenle.

## Masaüstü köprüsü — `uyap_core/is_listesi.py` ve `uyap_core/uyap_proxy.py`

- `is_listesi.py`: `POST tasks` izinli alanlara `sablon`, `etiketler`; `_sablon_alanlari`'na `asama`, `etiketler`;
  `GET etiketler`, `GET surecler` aktarımı.
- `uyap_proxy.py` (İş Ata penceresi): şablon seçilince `sablon: s.id` gönderilir; `sonrakiIsiAc` sonraki işi
  bulduğu şablonun kimliğiyle açar. İş Ata'da şablonun etiketleri küçük çipler olarak görünür (değiştirme
  web'de). `sablonBul` önce `t.sablon`'u kullanır, yoksa başlıktan tahmine düşer.

## Web arayüzü — `webapp/is_listesi/zs.js` (+ `zs.css`)

- **Süzgeç satırı (ikinci sıra)**: `Süreç` seçimi → seçilince o sürecin aşamaları sayılarla çip olarak
  (`Tebligat 4 · PTT 2 · Tebliğ 7 · Mazbata 3 …`); `Etiket` çok seçimli açılır liste (seçilenler çip olarak,
  "hepsi bulunsun" mantığı). Mevcut durum süzgeçleriyle birlikte çalışır; seçim `store` ile hatırlanır.
  `shownTasks()` bu iki süzgeci ekler; arama etiket adlarını da tarar.
- **Gruplama**: `Sırala` listesine "Aşamaya göre" eklenir (`grouped()`): süreç seçiliyken aşama sırasıyla başlıklar.
- **İş kartı / ayrıntı**: aşama rozeti ("Kesinleşme · 4/7 Mazbata") ve etiket çipleri; ayrıntıda etiket
  ekle/çıkar ve aşama düzelt (düzenle formunda). Olay metinleri `EVENTS`'e `tags`.
- **Süreç görünümü (dosya × aşama)**: yeni sekme "Süreçler": her satır bir dosya + borçlu (birim_id, dosya_no,
  taraf_adi), sütunlar aşamalar; hücrede o aşamadaki son işin durumu (✓ onaylandı / ● açık / boş). Hangi
  dosyanın neresinde takıldığını tek bakışta gösterir. Veri mevcut `tasks?filtre=tumu|benim` yanıtından
  istemcide kurulur, yeni uç gerekmez.
- **Şablonlar sekmesi** (`templateForm`): aşama seçimi (süreç › aşama) ve varsayılan etiketler.
- **Etiketler**: Şablonlar sekmesinin altında ya da yanında küçük yönetim bölümü (ad, renk, "yalnız bana /
  büroya açık"); kullanıcı kendi etiketini, yönetici büronunkileri düzenler.

## Sonraki aşama (otomatik kontrol) için bırakılan zemin — bu planda yazılmaz

- Her aşamanın `otomatik` anahtarı (yukarıdaki tablo) ileride ofis ajanının (UyapMasa) çalıştıracağı denetimi
  adlandırır. Öngörülen akış: UyapMasa barkod/kesinleşme turundan sonra `POST /is-listesi/api/otomatik`
  `{birim_id, dosya_no, taraf_adi, sinyal, deger, tarih, kanit}` gönderir → sunucu o dosya+taraftaki **açık
  ve aşaması eşleşen** işe not + `auto_check` olayı yazar, önerilen sonucu (olumlu/olumsuz/bekliyor) işaretler;
  karar ve onaya gönderme yine kullanıcıda ("yapılacak iş.md" §4 kuralı).
- Bilinen risk: `TebligatBarkod.borclu` yalnız tek borçlu dosyada dolu; çok borçlulu dosyada taraf eşlemesi
  `taraf_adi` metniyle yapılacak — o aşamada ele alınacak.

## Belgeler
- `Uyap Haricen Giriş/yapılacak iş.md`: yeni bölüm "Süreç, aşama, etiket" + §6 aşama tablosuna satır + §7 veri.

## Uygulama sırası
1. `zs_store.py` şema + sabitler + yöntemler + `backfill_sablon` (+ testler).
2. `zs.py` uçları.
3. `zs.js` süzgeç/rozet/etiket düzenleme → Şablonlar sekmesi → Süreçler görünümü.
4. `is_listesi.py` + `uyap_proxy.py` (şablon kimliğiyle iş açma).
5. Belge güncellemesi.

## Doğrulama
- `cd "Uyap Haricen Giriş" && python -m unittest discover -s tests -v` — `tests/test_zs.py`'ye yeni testler:
  şablonla iş açınca `sablon/surec/asama` + varsayılan etiketler; etiket ekle/çıkar ve olay; kişisel etiketin
  başka kullanıcıya görünmemesi; başka büronun etiketinin kullanılamaması; `backfill_sablon` ("Ad – Tebligat
  kontrol" eşleşir, eşleşmeyen `''` olur, ikinci çalıştırmada dokunulmaz); şablon silinince işin aşaması kalır;
  iş silinince `zs_task_tags` temizlenir. Mevcut `test_is_sablonlari_ve_taraf` geçmeye devam etmeli.
- Yerelde SQLite ile sunucuyu açıp (`zs.py`/`vendor_server.py` yerel çalıştırma) tarayıcıda: süreç seç →
  aşama çip sayıları doğru; etiket süzgeci + arama birlikte; "Aşamaya göre" gruplama; Süreçler görünümü.
- UYAP içinden (uyap_app): İş Ata'da şablon seç → iş web'de doğru aşama/etiketle görünür; "Onaya gönder" olumlu
  → sonraki iş bir sonraki aşamada açılır.
- PostgreSQL'de `ALTER TABLE … ADD COLUMN IF NOT EXISTS` ve yeni tabloların sorunsuz kurulduğunu canlıya
  çıkmadan bir kopya veritabanında denemek (sunucu açılışında `_ensure_schema` + `backfill_sablon`).
