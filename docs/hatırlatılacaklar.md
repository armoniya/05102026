# Hatırlatılacaklar (sabah bakılacak)

17.11.2026 akşamı yazıldı. Sabah ilk iş bunlara bakılacak.

## 1. L6 bildirimini dene
- Bulut yayını 17.11 akşamı yapıldı (commit `1e7c546`); site ayakta, uçtan uca denenmedi.
- Deneme: Telegram'ı ya da telefon bildirimini bağlamış bir çalışana İş Listesi'nden iş ata.
  Birkaç saniye içinde "Çoklu UYAP'ta yeni bir işiniz var" gelmeli.
- Gelmezse: ofisin buluta sinyal gönderip göndermediğine ve bulutun iletip iletmediğine bakılacak.

## 2. Toplu indirme telefona ulaşmıyor
- Kullanıcıya sorulacak: telefonda ne oldu?
  - hata mesajı mı çıktı ("Dosya indirilemedi …", "Ofis zamanında yanıt vermedi")?
  - hiçbir şey mi olmadı?
  - yanlış dosya mı indi (.txt)?
  - Hangi düğme (Evrak İndir / Gerekçeli Kararlar), kaç belge?
- Kodda bulunanlar: dosya ofiste klasöre yazılıyor VE istemciye indiriliyor. Olası nedenler:
  1. Tarayıcı tünelinde istek başına 2 dakika sınırı (`vendor_deploy/webapp/js/tunnel.js`, REQUEST_TIMEOUT).
  2. İndirme yalnız sayfa açıkken kendiliğinden başlıyor (telefon ekranı kapanırsa gelmez).
  3. Dosyanın tamamı telefon belleğine alınıyor (çok büyük dosyada sorun).
- Olası düzeltme: büyük dosyada süre sınırını kaldırma / parça parça indirme + panelde "Telefona tekrar indir".

## 3. Programı yeniden başlat ve kontrol et
- Yeni exe yerinde (19:49), çalışan program eski sürüm. Yeniden açınca devreye girer (PIN gerekir).
- Kontrol: dosya kartında mor bant ve "İş ata", çekmecedeki işler, belge görüntüleyicide Paylaş (panoya kopyalama).

## 4. Karar bekleyenler
- Kesinleştirme talebi düğmesi: yeni işlerde hiç görünmüyordu, kodu silindi. Yeni İş Listesi'ne yeniden yazılsın mı?
- Buluttaki denetim kaydı (zs_audit) silinmiş işlerin eski kopyalarını içeriyor. Silinsin mi?
- Chrome UDF'yi paylaşım penceresine vermiyor: UDF için "PDF olarak paylaş" mı, yalnız indir mi? (Şimdilik pano kullanılıyor.)

## 5. L7 (icra durumundan otomatik iş) — bitti, exe yerinde
- Uçtan uca test geçti; exe 18.11 02:18'de kopyalandı (program yeniden açılınca devreye girer).
- Kontrol: bir sonraki icra taramasında tebliğ / iade / kesinleşme değişen borçlu için İş Listesi'nde
  "… – Kesinleşme tarihi kontrol" / "Mernis adresine tebliğ" / "Kesinleştirme talebi gönder" işi açılmalı
  (açıklamasında "Otomatik açıldı"). Fazla ya da yanlış iş açılırsa söyleyin; şablonu pasif yapmak o olayı kapatır.

## 6. Yeni: İş panelinde "Ekler" (18.11 gece)
- İş Listesi'nde bir işi açın → "Ekler" kutusu → "+ Dosya ekle". İndirme ve silme de orada. Program yeniden açılınca gelir.

## 7. L8 (duruşmalar takvime) için canlı bakış gerekiyor
- Claude tarayıcı uzantısı bağlı değildi. UYAP ana sayfası açıkken uzantı bağlanırsa, "Yaklaşan Duruşmalarım"ın hangi
  UYAP yanıtından geldiğine ağ kaydından (yeni istek atmadan) bakılıp L8 yazılacak.

## 8. Anlamı netleşmeyen madde: "Varsayılan birim / taranacak birim ayarları web görünümüne"
- Plan dosyasında tek satır; kodda (ruyap, UyapMasa) böyle bir ayar bulunamadı, eski konuşmalarda da kaynağı yok.
- Sorulacak: hangi ekrandaki hangi ayar kastediliyor (masaüstü penceresi mi, UyapMasa mı, icra taraması mı)?
