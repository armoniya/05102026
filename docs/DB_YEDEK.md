# Veritabanı yedeği (uyap_icra)

**Ne zaman:** UyapMasa her açılışta veritabanı hazır olunca, o gün yedek alınmamışsa arka planda bir yedek alır
(`db_yedek.gunluk_yedek_arkaplanda`, `UyapMasa/uyapmasa.py` → `_veritabani_hazirla`). Program o gün hiç
açılmazsa o günün yedeği olmaz. İstemci modundaki makine yedek almaz (DB'si yok).

**Nerede:** `%LOCALAPPDATA%\UyapIcra\yedek\uyap_icra_YYYYMMDD_HHMMSS.dump`. Klasör yalnız bu Windows kullanıcısına
açık. Son **14** yedek tutulur (`UYAP_DB_YEDEK_SAKLA` ile değişir). İlk yedek 02.10.2026: 112 MB DB → 8.3 MB.

**Sınır:** Yedek aynı diskte. Yanlış silme, bozuk migration ya da eski kodun veriyi ezmesi gibi durumlara karşı korur.
Disk arızasına, hırsızlığa ya da fidye yazılımına karşı **korumaz**. Haftada bir en son `.dump` dosyasını şifreli bir
harici diske kopyalayın. Dosyada kişi verisi var; bulut klasörüne şifresiz koymayın.

## Komutlar (proje kökünde, `.venv\Scripts\python.exe` ile)

| İş | Komut |
|---|---|
| Şimdi yedek al | `python db_yedek.py` |
| Yedekleri listele | `python db_yedek.py --liste` |
| Yedeği **ayrı** DB'ye aç (incelemek için, güvenli) | `python db_yedek.py --geri-yukle <dosya>` → `uyap_icra_geri` |
| **Gerçek** DB'yi yedekten geri döndür | UyapMasa'yı KAPAT, sonra `python db_yedek.py --geri-yukle <dosya> --hedef uyap_icra --uzerine-yaz` |

Gerçek DB'ye geri dönerken araç önce mevcut hâlin yedeğini alır. Bu yedek alınamazsa işlem iptal olur.
Sonra eski DB'yi silip yedeği yükler. Ardından UyapMasa açılınca `migrate` şemayı günceller.

## Geri yükleme denemesi (02.10.2026)

`uyap_icra_20261002_120749.dump` dosyası ayrı bir `uyap_icra_geri_test` veritabanına yüklendi. 21 tablonun satır
sayıları canlı DB ile birebir aynı çıktı. Var olan DB üzerine `--uzerine-yaz` olmadan yazma reddedildi.
Test DB'si sonra silindi. Saklama (en eski yedeklerin silinmesi) geçici bir klasörde denendi.
