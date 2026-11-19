# İş Takip (/zs) — Telegram Grubu Kurulumu

Bu belge, cokluuyap.com/zs İş Takip'e bağlı Telegram botunun bir grupta kullanılabilmesi için
yapılacakları anlatır. Kod canlıda (09.10.2026, vendor_deploy `f8b251d`); aşağıdaki adımlar
yapılmadan grup özelliği çalışmaz.

---

## Ne işe yarar?

- Grupta `/is @kişi ne yapılacağı` yazılınca o kişiye İş Takip'te iş açılır.
- Komut bir **fotoğrafa veya belgeye yanıt** olarak yazılırsa o dosya işe belge olarak eklenir
  (programdaki "Belge Ekle" ile aynı: şifreli saklanır).
- Botun "✅ İş açıldı" mesajına **yanıt** olarak gönderilen belge/fotoğraf aynı işe eklenir,
  yazılan metin işe **not** olarak düşer.
- Atanan kişiye her zamanki bildirim (telefon / Telegram özel mesaj) gider.

---

## Adım 0 — Ön kontrol (bir kez)

Render → servis → **Environment** bölümünde şunların dolu olduğunu kontrol edin:

| Değişken | Değer |
|---|---|
| `ZS_TELEGRAM_TOKEN` | BotFather'dan alınan bot jetonu |
| `ZS_PUBLIC_URL` | `https://www.cokluuyap.com` (www'lu olmalı) |
| `ZS_FILE_KEY` | Belge şifreleme anahtarı (belge eklemek için şart) |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Belge deposu |

Kontrol: İş Takip'e girin → hesap menüsü → **Bildirimler**. "Telegram'ı bağla" düğmesi
görünüyorsa bot çalışıyor demektir.

---

## Adım 1 — Grubu kurun ve botu ekleyin

1. Telegram'da **Yeni Grup** oluşturun, çalışanları ekleyin.
2. Grup → **Üye ekle** → botun kullanıcı adını aratın (`@...bot`) → ekleyin.
3. Botu **yönetici yapmanıza gerek yok.** Bot, gruptaki sıradan sohbeti okumaz; yalnız
   `/is` komutlarını ve kendi mesajlarına verilen yanıtları görür (Telegram'ın "gizlilik modu").
   BotFather'da gizlilik ayarını **değiştirmeyin**.

---

## Adım 2 — Grup kimliğini öğrenin

1. Gruba şunu yazın:
   ```
   /is deneme
   ```
2. Bot şöyle yanıt verir:
   > Bu grupta iş açma kapalı. Grup kimliği: `-1001234567890` (sunucuda ZS_TELEGRAM_GROUPS'a eklenmeli).
3. Bu numarayı (başındaki eksi işaretiyle birlikte) kopyalayın.

> Bot hiç yanıt vermiyorsa: botun grupta olduğundan ve Adım 0'daki `ZS_TELEGRAM_TOKEN` /
> `ZS_PUBLIC_URL` değerlerinden emin olun.

---

## Adım 3 — Render'a grup kimliğini girin

1. Render → servis → **Environment** → **Add Environment Variable**
2. Anahtar: `ZS_TELEGRAM_GROUPS`
   Değer: `-1001234567890` (Adım 2'deki numara)
   Birden fazla grup için virgülle ayırın: `-1001111111111,-1002222222222`
3. **Save Changes** → servis kendiliğinden yeniden başlar (1–2 dk).
4. Gruba tekrar `/is deneme` yazın. Bu kez "Sizi İş Takip'te tanımıyorum" ya da "İş açıldı"
   yanıtı gelir — ikisi de grubun yetkilendirildiğini gösterir.

> **Dikkat:** Telegram bazen grubu "süper gruba" çevirir (ör. grup ayarlarını değiştirince,
> geçmişi herkese açınca). Bu olursa grup kimliği değişir ve bot yine "iş açma kapalı" der.
> O zaman Adım 2–3'ü yeni numarayla tekrarlayın.

---

## Adım 4 — Her çalışan kendini bota bağlamalı (kişi başı bir kez)

Bot, gruptaki kişiyi İş Takip'teki hesabıyla eşleştirmek için bu bağlantıya ihtiyaç duyar.
Bağlanmamış kişi komut yazamaz, etiketlenemez de.

1. **Telegram kullanıcı adı edinin:** Telegram → Ayarlar → **Kullanıcı adı** → ör. `ayse_demir`.
   (Etiketle bulunabilmek için gerekli. Kullanıcı adı olmayan kişiyi etiketlerken listeden
   adını seçmek de işe yarar.)
2. İş Takip'e (cokluuyap.com/zs) kendi hesabınızla girin.
3. Hesap menüsü → **Bildirimler** → **Telegram'ı bağla**.
4. Açılan Telegram sohbetinde **Başlat (Start)**'a basın.
5. Bot "Bağlandı: *Adınız*" yazar. Bitti.

> **Daha önce bağlanmış olanlar:** Bağlantıları geçerli, ama kullanıcı adları sistemde kayıtlı
> değil. Botla özel sohbete herhangi bir şey yazmaları ya da grupta bir kez `/is` kullanmaları
> yeterli; kullanıcı adı o anda kaydedilir. Kullanıcı adını sonradan değiştiren de aynısını yapmalı.

---

## Kullanım

### İş açma
```
/is @ayse_demir Tebligatı dosyaya işle
Müvekkile de bilgi ver
```
- İlk satır → **iş başlığı**, sonraki satırlar → **açıklama**.
- Kimse etiketlenmezse iş **yazan kişiye** açılır.
- Birden çok kişi etiketlenirse: **ilk kişi asıl atanan**, diğerleri **ek atanan** olur.
- Tarih metinden okunmaz; son tarihi gerekirse İş Takip'ten girin.

### Belgeyle iş açma
1. Gruptaki fotoğrafa / belgeye uzun basın → **Yanıtla**.
2. `/is @ayse_demir Bu evrakı işle` yazıp gönderin.
3. Dosya işe belge olarak eklenir. Yanıtlanan mesaj yazı ise, yazı açıklamaya alıntılanır.

### Açılmış işe belge / not ekleme
- Botun **"✅ İş açıldı"** (ya da "📎") mesajına **yanıt** verin:
  - Belge/fotoğraf gönderirseniz → işe belge eklenir.
  - Yazı yazarsanız → işe not eklenir.
  - İkisi birlikte (açıklamalı fotoğraf) → ikisi de eklenir.

### Kabul edilen dosyalar
pdf, udf, doc/docx, xls/xlsx, jpg/png, tif, zip — en fazla **20 MB** (Telegram botları daha
büyüğünü indiremez). Video, ses kaydı, çıkartma eklenmez.

> **İpucu:** Fotoğrafı "fotoğraf" olarak gönderirseniz Telegram sıkıştırır. Taranmış evrakın
> tam kalitesi için **Dosya olarak gönder** seçin. Albümle gönderilen fotoğraflardan yalnız
> yanıtlanan tek fotoğraf eklenir.

---

## Yetkiler (web ile aynı)

| Kim | Ne yapabilir |
|---|---|
| Yönetici | Herkese iş açar, birden çok kişi etiketleyebilir |
| Kullanıcı | Yalnız **kendine** iş açar (`/is metin`, etiketsiz). Başkasını etiketlerse reddedilir |
| Herkes | Yalnız **görebildiği** işe yanıtla belge/not ekler. Onaydaki/onaylanmış işe belgeyi yalnız yönetici ekler |

Bot yalnız `ZS_TELEGRAM_GROUPS`'taki gruplarda çalışır; başka bir gruba eklenirse iş açmaz.
Telegram'dan yapılan her işlem denetim kaydına yazılır.

---

## Bot yanıtları ve anlamları

| Yanıt | Anlamı / Çözüm |
|---|---|
| "Bu grupta iş açma kapalı. Grup kimliği: …" | Adım 3 yapılmamış ya da grup kimliği değişmiş |
| "Sizi İş Takip'te tanımıyorum" | Yazan kişi Adım 4'ü yapmamış |
| "İş Takip'e bağlı değil: @x" | Etiketlenen kişi Adım 4'ü yapmamış ya da kullanıcı adı henüz kaydedilmemiş |
| "Başkasına iş yalnız yönetici atayabilir" | Kullanıcı hesabıyla başkası etiketlendi |
| "Bu dosya türü kabul edilmiyor" / "… MB sınırını aşıyor" | Dosyayı pdf/jpg vb. olarak ya da küçülterek gönderin. İş açılmaz |
| "İş bulunamadı" | Yanıt verilen işi bu kişi göremiyor |
| Hiç yanıt yok | Komut `/is` ile başlamıyor, ya da bot gruptan çıkarılmış / jeton hatalı |

---

## Gizlilik notu

Telegram grup mesajları uçtan uca şifreli değildir; gruba atılan belge Telegram sunucularından
geçer. İş Takip'e eklenen kopya şifreli saklanır, ancak Telegram'daki asıl mesaj orada kalır.
Müvekkil belgelerinin gruba atılıp atılmayacağı büro kararıdır.
