# Armonia Linux — Gerçek Donanım Denemesi Sorunları (2026-11-14)

> Durum: **BEKLEMEDE.** Kullanıcı kararıyla Linux tarafına ara verildi; öncelik Windows'taki Rust uygulaması.
> Devam ederken önce bu dosyayı, sonra `ARMONIA_LINUX_YOL_HARITASI.md` (§D.0b) dosyasını okuyun.

## Deneme
- ISO: `C:\dev\armonia_seri.iso` (sha256 431837c3…), Rufus ile USB (DD), bilgisayar: i5-3470, 7,9 GB RAM, BIOS/MBR.

## Kullanıcının bildirdiği sorunlar
1. **Num Lock kapalı** (labwc `-C` düzeltmesinden sonra da).
2. **UYAP bağlantısı kurulmadı**; Rust arayüzünde çok uzun süre beklendi.
3. **Günlükte işe yarar bir yazı yok**; arayüzde yalnız "Paylaşım Aktif" yazıyor.
4. **Tarayıcıda birçok site yasak**: avukat.uyap.gov.tr, cokluuyap.com, eimza.adalet.gov.tr dahil.
5. **Arayüz geç tepki veriyor**: düğmeye tıklayınca bir süre yeşil (basılı) kalıyor; ancak tekrar tekrar
   basınca, çok geç, işlem yapılıyor.
6. (Önceki denemeden) Tarayıcı kapanınca ekran tamamen siyah kalıyor, Kontrol Ekranı geri gelmiyor;
   duvar kağıdı / dock / menü yok (Faz 5 başlanmadı).

## Bulunan nedenler
| # | Neden | Durum |
|---|-------|-------|
| 2 | `uyap_giris_disaridan.rs` `select_certificate`: kartta >1 sertifika varsa seçim **stdin'den** soruluyor; kiosk'ta stdin tty1 → sonsuza dek bekliyor, giriş kilidi tutuluyor, tüm UYAP istekleri askıda. (Windows GUI'de stdin olmadığı için anında ilk sertifika seçiliyor.) | Rust'ta düzeltildi: yalnız komut satırı aracı soruyor, diğer yerlerde süresi geçmemiş ilk sertifika. `oturum.sh` de `</dev/null` ile başlatıyor. |
| 2 | `uyap_proxy.rs` `relogin`: ilk giriş hatası ne olursa olsun kalıcı kilitleniyordu. E-imza istemcisi açılışta ~20-30 sn geç hazır → ilk deneme düşünce bir daha hiç denenmiyordu. | Rust'ta düzeltildi: PIN karta gitmeden önceki hatalar (UYAP'a ulaşılamadı, 5975 yok, sertifika yok) yeniden denenir; imza adımı ve sonrası (yanlış PIN, kart kilitlenme riski) kilitlenir. |
| 3 | Vekil/giriş mesajları `println!` → yalnız sistem günlüğü (journal); kullanıcı göremiyor. | Rust'ta düzeltildi (yalnız Linux): stdout/stderr "[" ile başlayan satırlar Kontrol Ekranı Günlüğü'ne akar; günlük 64 KB ile sınırlı. |
| — | Giriş kartında durum yazısı ("Bağlanılıyor", "Giriş Başarısız") düğmenin altında, pencere dışında kalıyordu. | Rust'ta düzeltildi: düğmenin üstüne alındı (Windows'u da etkiler). |
| 5 | `armonia-kiosk.service` `WLR_RENDERER=pixman` gerçek donanımda da yazılım çizimi zorluyordu. | İmaj ayarında kaldırıldı (wlroots kendisi seçer). |
| 4 | Tasarım gereği: Chromium politikası yalnız `127.0.0.1:8800`'e izin veriyor; UYAP vekil üzerinden açılır. Giriş kurulamayınca sayfalar gerçek alan adlarına yönlenip engellendi. | Kök neden #2. Giriş düzelince yeniden denenmeli. |
| 1 | Hyper-V'de aynı ISO'da tuş takımı rakam yazıyor (Num Lock etkin). Gerçek USB klavyede yalnız LED sönük kalıyor olabilir. | Doğrulanmadı: tuş takımıyla rakam yazılıyor mu, denenmeli. |
| 6 | Uygulama girişten sonra kendini gizliyor olabilir (Windows tepsi davranışı); görev çubuğu yok. | Açık. |

## Yapılmadı / bekleyen
- Yukarıdaki düzeltmelerle uyap_app ve ISO **derlenmedi, denenmedi** (Rust değişiklikleri kaynakta duruyor; Cargo.lock'a
  yalnız Linux için `libc` eklendi).
- E-imza kartlı giriş yalnız gerçek donanımda denenebilir (Hyper-V USB kart okuyucuyu geçirmiyor).
- Faz 5 masaüstü (duvar kağıdı, dock, "A" menü, widget'lar) başlanmadı. Duvar kağıdı için seçim bekleniyor:
  (a) adaçayı yeşili degrade + "Çoklu UYAP", (b) kullanıcının kendi görseli.
