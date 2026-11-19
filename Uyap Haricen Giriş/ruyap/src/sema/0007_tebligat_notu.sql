-- Tebligat zarfı (Kapalı Tebligat / PTT MTS Tebligat Belgesi) alınamadıysa nedeni (19.11.2026, kullanıcı bulgusu:
-- zarf belgelerde vardı ama kaydedilmemişti; neden yalnız geçici uyarılarda kalıyordu). Dosya panelinde gösterilir;
-- zarf sonradan alınınca boşaltılır.
ALTER TABLE evrak ADD COLUMN IF NOT EXISTS tebligat_notu varchar(300) NOT NULL DEFAULT '';
