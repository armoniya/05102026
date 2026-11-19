-- İcra durum olayından otomatik iş (plan L7, 17.11.2026): hazır şablonların olay bağı.
-- Kullanıcı bir şablonun olay bağını değiştirdiyse dokunulmaz (yalnız boş olanlar dolar).
-- Eşleme is_otomatik::HAZIR_OLAY_SABLONLARI ile aynı olmalı.
UPDATE sablon SET olay_turu = 'tebligat:teblig_edildi' WHERE anahtar = 'h-kes-tarih' AND olay_turu = '';
UPDATE sablon SET olay_turu = 'tebligat:iade' WHERE anahtar = 'h-mernis' AND olay_turu = '';
UPDATE sablon SET olay_turu = 'kesinlesme:kesinlesti' WHERE anahtar = 'h-kes-gonder' AND olay_turu = '';
