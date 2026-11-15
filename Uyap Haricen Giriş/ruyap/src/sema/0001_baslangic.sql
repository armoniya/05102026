-- Büro veritabanı — başlangıç (Aşama 1, 15.11.2026).
-- İcra tabloları (UyapMasa models/icra_models modeline alan alan sadık) 0002'de gelir.

-- Büro geneli ayarlar (ör. "Durum değişince İş Listesi'ne iş aç", tarama aralığı).
CREATE TABLE buro_ayar (
    anahtar     varchar(100) PRIMARY KEY,
    deger       jsonb        NOT NULL,
    guncellendi timestamptz  NOT NULL DEFAULT now()
);
