-- İş türü etiketleri (kullanıcı isteği 2026-11-16): İş Listesi'nde bu başlıklarla süzülür.
-- Anahtar 'is-' ile başlayanlar sayfada ve iş formunda öne çıkar. Varsa (aynı ad) eklenmez.
INSERT INTO etiket (anahtar, ad, renk)
SELECT v.anahtar, v.ad, v.renk
FROM (VALUES
    ('is-uyap',     'UYAP İşi',          '#4f6fb0'),
    ('is-adliye',   'Adliye İşi',        '#8a5a2b'),
    ('is-muvekkil', 'Müvekkil Görüşme',  '#2f8a5f'),
    ('is-borclu',   'Borçlu Araması',    '#b0503f')
) AS v (anahtar, ad, renk)
WHERE NOT EXISTS (SELECT 1 FROM etiket e WHERE e.anahtar = v.anahtar OR (e.sahip_id IS NULL AND lower(e.ad) = lower(v.ad)));
