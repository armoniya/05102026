-- Hatırlatıcı bildirimi (plan L6, 16.11.2026).
-- goruldu   : kişi hatırlatmayı web arayüzünde gördü ("Tamam" dedi). Sayfa uyarısı bununla susar.
-- gonderildi: (0003'ten) ofisin buluta gönderdiği içeriksiz telefon/Telegram sinyali için ayrılmıştır.
ALTER TABLE hatirlatici ADD COLUMN goruldu timestamptz NULL;

-- Buluttan taşınan, zamanı çoktan geçmiş hatırlatıcılar ilk açılışta toplu uyarı çıkarmasın.
UPDATE hatirlatici SET goruldu = now() WHERE zaman < now() - interval '1 day';

CREATE INDEX hatirlatici_gorulmemis ON hatirlatici (kullanici_id, zaman) WHERE goruldu IS NULL;
