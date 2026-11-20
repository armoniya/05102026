-- Elle kesinleşme tarihi (20.11.2026, kullanıcı: "kesinleşme için tarih giriyorum ama kesinleşme olarak o tarihi
-- kaydedemiyorum"). Kullanıcının UYAP'ta gördüğü kesinleşme günü; hesap bunu ezmez. Gün gelmişse Kesinleşti,
-- gelmemişse Kesinleşme Bekliyor (o gün). Etkili itiraz ya da durdurma varsa onlar önce gelir. Boş: hesaba bırak.
ALTER TABLE dosya_taraf ADD COLUMN IF NOT EXISTS kesinlesme_elle_tarihi date NULL;
