-- Kesinleşme kuralları: takip türüne göre hazır varsayılanlar (20.11.2026, kullanıcı kuralı).
--
-- Kullanıcı kuralı: kesinleşme, icra işlemi yapılabilecek gündür; itiraz süresi ile ödeme süresinden UZUN olanın
-- bitişi (kambiyo haciz yolu 5 gün itiraz / 10 gün ödeme → 10; kira ödeme emri 7 / 30 → 30). Bu yüzden mevcut
-- `itiraz_suresi_gun` sütunu artık "kesinleşme süresi" anlamındadır. Adı, eski exe'ye geri dönülürse bozulmasın
-- diye DEĞİŞTİRİLMEDİ; Rust tarafında `kesinlesme_suresi_gun` alanına okunur.
--
-- Yeni alanlar:
--   itiraz_gun       itiraz / başvuru süresi (yalnız bilgi; hesapta kullanılmaz)
--   itiraz_durdurur  elle işaretlenen itiraz kesinleşmeyi durdurur mu (ilamlı ve kambiyo haciz yolunda hayır);
--                    UYAP'taki "Durdurulmuş : …" durumu bundan bağımsız olarak her türde durdurur
--   dayanak          İİK maddesi vb.
--   elle_degisti     kullanıcı hazır değeri değiştirdi; program açılışında varsayılanlar bu satıra yazılmaz
ALTER TABLE kesinlesme_kurali ADD COLUMN IF NOT EXISTS itiraz_gun smallint NULL CHECK (itiraz_gun >= 0);
ALTER TABLE kesinlesme_kurali ADD COLUMN IF NOT EXISTS itiraz_durdurur boolean NOT NULL DEFAULT true;
ALTER TABLE kesinlesme_kurali ADD COLUMN IF NOT EXISTS dayanak varchar(200) NOT NULL DEFAULT '';
ALTER TABLE kesinlesme_kurali ADD COLUMN IF NOT EXISTS elle_degisti boolean NOT NULL DEFAULT false;
