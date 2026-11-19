# Takip türü + Tebligatlar bölümü (onay: 19.11.2026)

Kullanıcı onayladı ("Onaylıyorum, A'dan başla"). Teslim iki parça: A+B, sonra C+D. Her exe değişikliği ve commit ayrı onay.
Canlı UYAP: takip türü listesi çekme (birkaç on istek) ve eski dosyaların toplu ayrıntısı (~6.800 istek, ~6 saat) ayrı onay,
18:00 sonrası.

## Bulgular
- `icra_kayit::dosya_ayrinti_kaydet` yalnız testlerden çağrılıyor; `icra_guncelle` `dosyaAyrintiBilgileri_brd`'ye hiç gitmiyor.
- Yanıtta adlar var: `takibinTuruAciklama`, `takibinYoluAciklama`, `takibinSekliAciklama` (" ÖRNEK: 7 İlamsız … - Eski No: 49 ").
- Kodlar bağlama göre: şekil "0" ilamsızda Örnek 7, ilamlıda Örnek 4-5.
- Açılış ekranı listeleri: `icra_takip_turu.ajx` {} → `icra_takip_yolu.ajx` {takipTuru} → `icra_takip_sekli.ajx`
  {takipTuru, takipYolu}; öğeler `value` / `name`.

## A. Takip türü
- Şema 0008: `icra_takip_detay` + `ornek_no`, `ayrinti_zamani` (ad sütunları zaten var, yazılır). Yeni `takip_turu_listesi`
  (tür/yol/şekil ağacı, kod + ad).
- Toplu taramada yeni eklenen / takip türü okunmamış dosyada bir ayrıntı isteği. Mod::Dosya her zaman. Yeni toplu tür
  "Takip türü bilgisi (eksikler)" (`Mod::Takip`), seçime uyar.
- Kesinleşme Süreleri: kod kutuları yerine Takip türü → yolu → şekli açılır listeleri (adlarla, "Hepsi"); ad önerisi;
  kural tablosu ve dağılım adlarla. "UYAP'tan takip türü listesini al" düğmesi.
- Dosya paneli: adlar + Örnek no.

## B. Süreçler listesi
- "Dosya" sütunu → "İcra dairesi" + "Dosya no" (ayrı başlık süzgeçleri).
- Listeli başlık süzgeçlerinde "Tümünü seç"; tümü seçilip bazıları kaldırılınca sunucuya "hariç" (`!` öneki) gider.

## C. Tebligatlar sekmesi
- Tebligat başına satır; borçluya bağlanmayan zarflar muhatap adı + türüyle.
- Sütunlar: daire, dosya no, dosya türü, durum, açılış, takip şekli (Örnek), alacaklı, borçlu/muhatap, muhatap türü, barkod,
  tür, evrak tarihi, tebliğ/son durum, tebliğ/iade tarihi, teslim şekli/iade nedeni, mazbata, yeniden tebliğ talebi,
  e-tebligat, kesinleşme.
- Her sütunda başlık süzgeci (adetli, daralan, Tümünü seç; tarihlerde aralık), her sütunda sıralama (dosya bölünmez).
- Seçim + Tümünü seç / bırak; toplu düğmeler seçili satırların dosyalarına. Excel'e aktar (.xlsx, zip ile). Sayaç.

## D. Tür eşleştir
- Tebligatlar sekmesinde pencere: sabit türler dışı yazılışlar gruplu (anahtar), adet, çeşit, örnek dosya; tür ata;
  "Kaydet ve uygula" → `tebligat_tur_eslesmesi` + mevcut kayıtlar + yeniden hesap.

## Testler
Birim (Örnek no, ayrıntı/liste kaydı, hariç süzgeci, tebligat listesi süzme/sıralama, eşleştirme uygulama), uçtan uca
(sahte UYAP ayrıntı + liste uçları), Playwright (yeni sekme, Tümünü seç, kural formu).
