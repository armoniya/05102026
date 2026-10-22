# Yapılacak İş — "Kontrol et, aksiyon al" modeli

Bu belge İş Listesi'ndeki işlerin, UYAP dosya kartından açılan **iş türleri (şablonlar)** üzerinden
nasıl tanımlanacağını ve bir işin "kontrol → karar → aksiyon → onay" akışıyla nasıl kapanacağını
anlatır. Aşama 1 ve 2 yapıldı; sonraki aşamalar bu modele göre eklenecek.

---

## 1. Temel fikir

Büroda işlerin çoğu aynı kalıptadır: *bir şeyi kontrol et, sonuca göre UYAP'ta bir işlem yap*.

```
İŞ ATANIR ──► KONTROL ──► KARAR ──► AKSİYON ──► ONAYA GÖNDER ──► ONAY
(şablon +     (UYAP'ta     (olumlu /   (UYAP işlemi   (atanan kişi)    (yönetici /
 taraf +       neye         olumsuz /   ya da yeni                      onay yetkilisi)
 belge)        bakılacak)   bekliyor)   takip işi)
```

- **İş türü (şablon)** işin ne olduğunu, ne kadar sürede yapılacağını, neyin kontrol edileceğini ve
  sonuca göre hangi aksiyonların önerileceğini tanımlar.
- **Taraf**: icra dosyasında birden çok borçlu olabilir; iş, dosya kartının taraf listesinden
  seçilen kişiye bağlanır (başlık "HAMZA OĞUZ – Tebligat kontrol" gibi oluşur).
- **Belge**: iş, evrak ağacındaki bir belgeye bağlanabilir (ağaçta mor görünür).
- **Aksiyon**: UYAP'ta yapılacak işlem. Sistem hiçbir talebi kendiliğinden **göndermez**; ilgili
  UYAP ekranını açar ve bildiği alanları doldurur. İmza ve gönderme her zaman kullanıcıdadır.

---

## 2. İş türü (şablon)

İki kaynaktan gelir:

| Kaynak | Kim tanımlar | Nerede durur |
|---|---|---|
| **Hazır** | Uygulama ile gelir | Sunucu kodu (`zs_store.HAZIR_SABLONLAR`), değiştirilemez |
| **Büro** | Bürodaki her kullanıcı ekler; ekleyen ya da yönetici siler | Veritabanı (`zs_templates`, büroya özel) |

Alanlar:

| Alan | Açıklama | Örnek |
|---|---|---|
| `title` | İş adı (başlığın taraf adından sonraki kısmı) | Tebligat kontrol |
| `description` | İşe varsayılan açıklama / kontrol talimatı | Safahat'tan tebligat barkodunu ve sonucunu kontrol et |
| `due_days` | Son tarih: bugünden kaç gün sonra (boş = tarih yok) | 3 |
| `priority` | 0 düşük, 1 normal, 2 acil | 1 |
| `taraf` | İş bir tarafa mı yapılır (`borclu`, `alacakli`, `herhangi`, boş) | borclu |
| `action` | Önerilen UYAP aksiyonu anahtarı (Aşama 3) | `talep:adres_arastirmasi` |

Kullanıcı İş Ata penceresinde şablona tıklayınca başlık, açıklama, son tarih ve öncelik dolar;
dilediği gibi değiştirip kaydeder. Formdaki değerler **"☆ Bu işi şablon olarak kaydet"** ile büroya
yeni şablon olarak eklenebilir (bağlantı yalnız şablonu olmayan bir iş yazılınca görünür).

**Pencere şişmez:** şablon sayısı kaç olursa olsun İş Ata penceresinde arama boşken en çok
kullanılan 6 şablon, aramaya yazınca en iyi 8 eşleşme görünür; Enter ilkini seçer. Kullanım sayısı
o bilgisayarda tutulur.

**Yönetim İş Listesi'nde:** web'de **Şablonlar** sekmesi (pencerede "Şablonları yönet") büronun
şablonlarını ekler, düzenler, siler; hazır şablonlar orada salt okunur listelenir. Düzenleme ve
silme şablonu ekleyene ya da yöneticiye açıktır. Hazır şablonların adları büroya verilemez.

---

## 3. Kontrol ve karar (Aşama 2)

Her iş türü bir **kontrol sorusu** ve olası **sonuçlar** taşır. Kullanıcı işi bitirirken sonucu seçer;
sonuca bağlı **sonraki iş** önerilir (tek tıkla açılır, aynı dosya/taraf/belgeyle).

| İş türü | Kontrol | Olumlu → | Olumsuz → | Bekliyor → |
|---|---|---|---|---|
| Tebligat kontrol | Tebligat tebliğ edildi mi? | Kesinleşme kontrol (+7 gün) | Adres araştırması / 21-2 tebligat talebi | Aynı iş, +3 gün |
| Ödeme emri kontrol | Ödeme emri gönderildi mi? | Tebligat kontrol (+10 gün) | Ödeme emri yeniden gönder | +3 gün |
| Kesinleşme kontrol | Takip kesinleşti mi? | Haciz talepleri (SGK, banka, araç, tapu) | Tebligat kontrol | +3 gün |
| Takyidat kontrol | Araç/tapu takyidat sonucu geldi mi? | Haciz / yakalama talebi | Başka mal varlığı sorgusu | +2 gün |
| Barkod takip | PTT barkodunda son durum | Tebligat kontrol | Adres araştırması | +2 gün |
| 21/2 akıbet kontrol | 21/2 tebligatın akıbeti | Kesinleşme kontrol | Yeni adres / ilanen tebligat | +5 gün |
| Reddiyat / tahsilat kontrol | Para hesaba geçti mi? | Müvekkile bildirim | Muhasebe ile görüş | +2 gün |
| Borçlu aranacak | Borçluya ulaşıldı mı? | Ödeme planı notu | Tekrar ara (+2 gün) | — |

Ek hazır şablonlar (zincirin devamı): **Adres araştırması** (kontrollü), **Haciz talepleri**,
**Müvekkile bildirim**. Tabloda şablonu olmayan sonraki işler (ör. "İlanen tebligat talebi") yalnız
başlık + süre olarak açılır.

Nasıl çalışır (UYAP'ta **Onaya gönder** penceresi):

1. İşin şablonu başlıktan bulunur ("Hamza Oğuz – Tebligat kontrol" → *Tebligat kontrol*).
2. Şablonun kontrol sorusu sorulur; **Olumlu / Olumsuz / Bekliyor** seçilir (kontrol sorusu olan
   şablonda seçim zorunlu, şablonsuz işte isteğe bağlı).
3. Seçime göre **Sonraki işi aç** kutusu dolar (iş adı + gün; değiştirilebilir, kapatılabilir).
   "Bekliyor" aynı işi N gün sonraya önerir.
4. Gönderince iş onaya gider; sonraki iş aynı dosya, taraf ve belgeyle açılır (yöneticide aynı
   kişiye, üyede kendine). Özel işlerde sonraki iş önerilmez.

Sonuç ve karar işe **not** olarak yazılır ("Onaya gönderme notu: Kontrol: olumsuz (Tebligat tebliğ
edildi mi?) — tebligat iade, adres yetersiz — Sonraki iş: Adres araştırması"). Böylece onaylayan
kişi neye bakıldığını görür.

Büro şablonlarının kontrol sorusu ve dalları İş Listesi → **Şablonlar**'da düzenlenir
(`kontrol`, `sonuclar = {olumlu|olumsuz|bekliyor: {baslik, gun}}`; boş başlık = aynı iş).

### Ertesi güne aktar

İş bugün yetişmiyorsa atanan kişi UYAP'tan **Ertesi güne aktar** der: yeni tarih (varsayılan: son
tarihten — geçmişse bugünden — sonraki iş günü 17:00) ve **zorunlu not** girilir. Bu bir ek süre
talebidir: yönetici / onay yetkilisi İş Listesi'nde kabul ederse son tarih değişir, reddederse
eski tarih kalır. Karar bekleyen talepler **Onay bekleyenler** listesine düşer; UYAP'ta işin
yanında "aktarım onayda" görünür.

---

## 4. UYAP aksiyonları (Aşama 3)

Aksiyon, şablona ve kontrol sonucuna bağlı **önerilen UYAP işlemi**dir. Türler:

| Tür | Ne yapar | Örnek anahtar |
|---|---|---|
| `ekran` | Dosya kartında ilgili sekmeyi açar | `ekran:safahat`, `ekran:evrak` |
| `sorgu` | UYAP sorgu ekranını açar (sorgu ücretlidir; kullanıcı onaylar) | `sorgu:takyidat`, `sorgu:sgk`, `sorgu:banka` |
| `talep` | Evrak Gönderme → Talep ekranını açar, talep türünü ve tarafı seçer | `talep:adres_arastirmasi`, `talep:maas_haczi`, `talep:arac_haczi` |
| `is` | Aynı dosyada yeni takip işi açar | `is:tebligat_kontrol` |

Kurallar:

1. **Hiçbir talep otomatik gönderilmez, imzalanmaz.** Sistem ekranı açar ve bildiği alanları
   (dosya, taraf, talep türü) doldurur. Kontrol edip "Gönder"e basmak kullanıcıdadır.
2. Ücretli sorgularda (takyidat, SGK vb.) sorgu bakiyesi gösterilir, kullanıcı onaylamadan sorgu
   yapılmaz.
3. Yapılan aksiyon işe not düşer ("Talep hazırlandı: Adres araştırması — HAMZA OĞUZ"). UYAP'ın
   gönderim sonucu (evrak no) okunabiliyorsa o da eklenir.
4. Aksiyonu sonradan değişebilecek UYAP ekranlarına bağlamak kırılgandır; her aksiyon, ekran
   bulunamazsa "Elle yapın: Evrak Gönderme → Talep → Adres Araştırması" yönergesine düşer.

---

## 5. Onay

İşi atanan kişi UYAP'tan çıkmadan **Onaya gönder** der (dosya kartındaki mor bantta ve sağdaki
iş panelinde). İsteğe bağlı not yazılır (kontrol sonucu). Yönetici / onay yetkilisi onaylar ya da
gerekçeyle reddeder (iş "devam"a döner). Özel işler onaya gitmez, "Tamamla" ile kapanır.

Karar UYAP'tan çıkmadan da verilir: sağdaki iş panelinde (**Onay bekleyen**) ve dosya kartı bandında
onay yetkilisine **Onayla / Reddet** ile **Aktarımı kabul et / reddet** düğmeleri görünür. Satırda
gönderenin notu ("Kontrol: olumlu …") ya da aktarma talebinin tarihi ve nedeni yazar; ret gerekçesi
zorunludur. Yeniden açma, devir gibi işlemler İş Listesi'nde kalır.

---

### Sağ tık menüsü

UYAP'ta **icra dosyası kartında** sağ tık, tarayıcının uzun menüsü yerine sade bir CokluUyap menüsü
açar (`uyap_proxy.py`, "Sağ tık menüsü"). Şimdilik tek madde var: **Dosya bilgilerini kopyala**.
"İzmir 15. İcra Dairesi 2025/123" gibi birim adı ve dosya numarası tek tıkla panoya kopyalanır;
menünün başlığında da kopyalanacak metin görünür. Başka her yerde (diğer dosya türleri, kart dışı,
yazı alanları), **metin seçiliyken** (Kopyala, Google'da ara, Çevir) ve **Shift / Ctrl + sağ tık**ta
tarayıcının menüsü açılır.

**Dosya türüne göre menü:** `DOSYA_TURLERI` (arabuluculuk, icra, ceza, idare, hukuk; eşleşmezse diğer)
dosya türü açıklamasından, yoksa birim adından türü bulur. Her türün `menu(c)` işlevi kendi maddelerini
verir; yalnız icranınki tanımlı. Diğer türlerin menüsü maddeleri belirlenince eklenecek.

---

## 5b. Süreç, aşama ve etiket ağacı

İşler **dosya türü → süreç → aşama** sırasıyla süzülür, gruplanır ve raporlanır. İcra takibi açıldıktan
sonraki ilk kısım (tebligat → kesinleştirme) aşama aşama izlenir; her kontrol bitince sıradaki aşamanın
işi kendiliğinden açılır.

**Etiket ağacı** (en çok 3 katman, `zs_store.HAZIR_ETIKETLER` + büronun `zs_tags`):

```
İcra                                  ← 1. dosya türü (ana etiket; Hukuk, Ceza, CBS, İdare, Arabuluculuk da var)
 ├ Tebligat – Kesinleştirme           ← 2. süreç
 │  ├ Tebligat oluşturuldu            ← 3. aşama etiketleri (işin aşamasından gelir, elle takılmaz)
 │  ├ Tebligat PTT'ye verildi
 │  ├ …                               (13 aşama, aşağıda)
 │  └ Kesinleşme
 ├ Haciz                              ← 2. grup
 └ Tahsilat
Müvekkil bildirimi, Acil dosya        ← genel (her dosya türünde)
```

- Dosya türü işin UYAP dosyasından bulunur (`dosya_turu`: tür açıklaması, yoksa birim adı) ve **ana
  etiket kendiliğinden takılır**; elle değiştirilemez. Bir türün altındaki etiket yalnız o türdeki
  dosyanın işine takılır. Dosyasız işe her etiket uyar.
- **Aşama etiketi** işin aşamasından gelir: aşama değişince eskisi kalkar, yenisi takılır
  (`_asama_yaz`); elle takılmaz / çıkarılmaz, şablona konmaz.
- Üst etiketle süzünce altındaki etiketlerin takılı olduğu işler de gelir (İcra → bütün icra işleri).
- Herkes etiket ekler: **kişisel** (yalnız kendisi görür; olay geçmişine yazılmaz) ya da **büroya açık**.
  Büro etiketini ekleyen ya da yönetici düzenler/siler; altında etiket olan silinmez.

**Süreç "Tebligat → Kesinleştirme"** (`HAZIR_SURECLER`, kodda; borçlu bazlı). Her aşamanın bir hazır
kontrol şablonu, şablonun kontrol sorusu ve sonuç dalları vardır. `otomatik`: ileride ofis ajanının
(UyapMasa) yapacağı denetimin anahtarı (henüz yürütülmez).

| # | Aşama | Şablon | Sonuç → sonraki iş | `otomatik` |
|---|---|---|---|---|
| 1 | Tebligat oluşturuldu | Tebligat oluşturuldu kontrol | Oluşturuldu → 2 | `db:tebligat_olustu` |
| 2 | Tebligat PTT'ye verildi | PTT'ye verildi kontrol | PTT'ye verildi → 3 | `db:ptt_kabul` |
| 3 | Tebligat yola çıktı | Yola çıktı kontrol | Yola çıktı → 4 | `db:ptt_yolda` |
| 4 | Tebliğ kontrol | Tebliğ kontrol | Sonuç belli → 5 | `db:teblig` |
| 5 | Mazbata dönüşü | Mazbata dönüşü kontrol | **Tebliğ edildi → 9**; **İade → 6**; Henüz dönmedi → 5 | `db:mazbata` |
| 6 | Mernis adresine tebliğ *(iade)* | Mernis adresine tebliğ | Talep gönderildi → 8; Mernis adresi aynı → 7 | `uyap:talep_mernis` |
| 7 | 21/2 adresine tebliğ *(iade)* | 21/2 adresine tebliğ | Talep gönderildi → 8 | `uyap:talep_21_2` |
| 8 | Talep kabul *(iade)* | Talep kabul kontrol | **Kabul edildi → 1 (yeni tebligat turu)**; Reddedildi → 6 | `uyap:talep_kabul` |
| 9 | İtiraz kontrol | İtiraz kontrol | İtiraz yok → 10; İtiraz var → Müvekkile bildirim | `db:itiraz` |
| 10 | Kesinleşme tarihi | Kesinleşme tarihi kontrol | Tarih geldi → 11 | `db:kesinlesme_tarihi` |
| 11 | Kesinleştirme talebi gönderme | Kesinleştirme talebi gönder | Gönderildi → 12 | `uyap:kesinlestirme_gonder` |
| 12 | Kesinleştirme talebi kabul | Kesinleştirme talebi kabul kontrol | Kabul edildi → 13; Reddedildi → 11 | `uyap:kesinlestirme_talebi` |
| 13 | Kesinleşme | Kesinleşme kontrol | Kesinleşti → Haciz talepleri; Kesinleşmedi → 12 | `uyap:sorgu_kesinlesme` |

"Bekliyor" dalı (ör. barkodda henüz sonuç yok) aynı kontrolü birkaç gün sonraya yeniden açar.

- **Sıradaki aşamaya geçiş sunucuda:** "Onaya gönder" / "Tamamla"da kontrol sonucu (`sonuc`: olumlu |
  olumsuz | bekliyor; ekranda şablondaki adıyla, ör. "Tebliğ edildi" / "İade") seçilir → işe yazılır,
  şablonun o dalındaki sonraki iş aynı dosya / taraf / atananla, bugünden gün sayısı sonra 17:00'ye
  açılır (`transition(…, sonuc, sonraki)`). Kullanıcı öneriyi değiştirebilir ya da açmayabilir
  (`sonraki: {baslik, gun} | false`). Yeni tebligat turunda eski barkod / belge bağı taşınmaz.
  `sonuc` gönderilmezse sonraki iş açılmaz — güncellenmemiş UYAP penceresi kendisi açar (çift iş olmaz).
- İş, açıldığı şablonu `sablon` alanında tutar ("h-mazbata" | büro şablonu "b-12"); seçilmediyse başlığın
  şablon kısmından (hazır şablonların eski adları da: "Mazbata kontrol", "Tebliğ durumu kontrol" …)
  bulunur. Eski işler sunucu açılışında bir kez başlıktan bağlanır (`backfill_sablon`).
- Web:
  - **Benim işlerim / Tüm işler / Özel**: üstte süzgeç — *Dosya türü* (İcra, Hukuk davası, Arabuluculuk,
    CBS, …) → *Süreç* (Tebligat – Kesinleştirme, Haciz, Tahsilat) → *Aşama* (13 aşama, iade dalı ayrı),
    her çipte iş sayısı; ayrıca serbest etiket. "Aşamaya göre" sıralama.
  - **Süreçler**: her satır dosya + borçlu, her sütun aşama (iade dalı ayrı renkte); hücrede o aşamadaki
    son iş (✓ olumlu, ↺ iade/olumsuz, saat: bekliyor, ! gecikti). İade sonrası yalnız son tur gösterilir
    ("2. tebligat turu"). Üstte **"Şu an"** raporu: kaç dosyanın hangi aşamada beklediği, kaçının
    kesinleştiği; çipe basınca yalnız o dosyalar kalır.
  - Kontrol şablonlu işte "Onaya gönder" önce sonucu ve sonraki aşamayı soran pencereyi açar.
- API: `GET /surecler`, `GET|POST /etiketler`, `POST|DELETE /etiketler/{id}` (`parent` ile),
  `POST /tasks/{id}/etiketler {add, remove}`, `POST /tasks/{id}/durum {action, reason, sonuc?, sonraki?}`
  (yanıtta `sonraki`); iş açma/düzenlemede `sablon`, `etiketler`, `asama`.

---

## 6. Aşamalar

| Aşama | İçerik | Durum |
|---|---|---|
| 1 | Hazır + büro şablonları, "Şablon olarak kaydet", taraf seçimi, süre düğmeleri, UYAP'tan onaya gönderme, bildirimden iş, bugün özeti | **Yapıldı** |
| 2 | Kontrol sonucu (olumlu / olumsuz / bekliyor) + sonraki iş önerisi; şablonda sonuç dalları; Ertesi güne aktar (yönetici onaylı) | **Yapıldı** |
| 3 | UYAP aksiyonları (`ekran`, `sorgu`, `talep`, `is`) — ekranı açıp doldurma | Planlandı |
| 4 | Otomatik kontrol: uygun işlerde (barkod, safahat) sorguyu sistem yapar, sonucu işe not düşer; karar yine kullanıcıda | Fikir |

---

## 7. Veri (Aşama 1–2)

- `zs_templates`: `id, office_id, title, description, due_days, priority, taraf, action, kontrol, sonuclar (JSON), created_by, created_at`
- `zs_tasks`: mevcut dosya alanlarına ek `taraf_adi` (işin yapıldığı taraf)
- API (`/is-listesi/api`): `GET /sablonlar` → `{hazir, buro}`, `POST /sablonlar`, `POST /sablonlar/{id}` (düzenle),
  `DELETE /sablonlar/{id}`; ertesi güne aktarma mevcut `POST /tasks/{id}/ek-sure` ucunu kullanır
- Masaüstü köprüsü (`/__uyap_kisi__/is-listesi`): `GET/POST /sablonlar`, `POST/DELETE /sablonlar/{id}`,
  `POST /tasks/{id}/durum` (`gonder`, `basla`, `tamamla`, `onayla`, `reddet`), `POST /tasks/{id}/ek-sure` (talep),
  `POST /ek-sure/{id}/karar` (kabul / ret); görev listesinde `ext` (bekleyen talep) ve `submit_note`
