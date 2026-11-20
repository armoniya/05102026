-- Tebligat grupları (20.11.2026, kullanıcı isteği: "ödeme emirleri, 89/1-2-3, bankalar, 103, kıymet takdiri, satış ilanı
-- ayrı olarak veritabanına kaydedilsin, muhatabı ile birlikte; tek bir arayüzde tek bakışta kontrol").
--
--   zarf_icerigi      memurun zarfa yazdığı "BU ZARFTA … VARDIR" içeriği (tür artık sabit gruplardan biri; asıl yazı burada)
--   muhatap_adi/turu  açıklamadaki muhatap ("[ÜÇÜNCÜ ŞAHIS X]" → X / ucuncu; borclu, vekil)
--   haciz_sirasi      haciz ihbarnamesi 89/1-2-3; belgede yazmıyorsa BOŞ (tahmin yok, kullanıcıya sorulur)
--   haciz_sirasi_elle kullanıcı girdi; tarama ezmez
ALTER TABLE tebligat_barkod ADD COLUMN IF NOT EXISTS zarf_icerigi varchar(500) NOT NULL DEFAULT '';
ALTER TABLE tebligat_barkod ADD COLUMN IF NOT EXISTS muhatap_adi varchar(255) NOT NULL DEFAULT '';
ALTER TABLE tebligat_barkod ADD COLUMN IF NOT EXISTS muhatap_turu varchar(10) NOT NULL DEFAULT '';
ALTER TABLE tebligat_barkod ADD COLUMN IF NOT EXISTS haciz_sirasi smallint NULL CHECK (haciz_sirasi BETWEEN 1 AND 3);
ALTER TABLE tebligat_barkod ADD COLUMN IF NOT EXISTS haciz_sirasi_elle boolean NOT NULL DEFAULT false;
