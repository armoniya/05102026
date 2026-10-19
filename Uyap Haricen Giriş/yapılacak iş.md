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
yazı alanları) ve **Shift / Ctrl + sağ tık**ta tarayıcının menüsü açılır.

**Dosya türüne göre menü:** `DOSYA_TURLERI` (arabuluculuk, icra, ceza, idare, hukuk; eşleşmezse diğer)
dosya türü açıklamasından, yoksa birim adından türü bulur. Her türün `menu(c)` işlevi kendi maddelerini
verir; yalnız icranınki tanımlı. Diğer türlerin menüsü maddeleri belirlenince eklenecek.

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
