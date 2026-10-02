# Veritabanı Bulguları — Ceza

Tarih: 2026-09-22 · Genel/ortak modeller için bkz.
[VERITABANI_BULGULARI_00_GENEL.md](VERITABANI_BULGULARI_00_GENEL.md).

## 1. Taranan yargı birimleri (11/11 — TAMAMI)

| Birim | Dosya var mı | Not |
|---|---|---|
| Asliye Ceza Mahkemesi | Evet | Talimat Dosyası + Ceza Dava Dosyası |
| Ağır Ceza Mahkemesi | Evet | Ceza Dava Dosyası — yeni durum kodları burada bulundu (Yargıtayda, Yargıtaydan Döndü, İstinaftan Kısmi Döndü) |
| Bölge Adliye Mah. Ceza Dairesi (istinaf) | Evet | Ceza Dava Dosyası — **Safahat sekmesi YOK** |
| Fikrî ve Sınai Haklar Ceza Mahkemesi | Evet | Ceza Dava Dosyası — Safahat VAR |
| İcra Ceza Hakimliği | Evet | Ceza Dava Dosyası — Safahat + Tahsilat/Reddiyat/Harç VAR, "Sanık" bir Kurum olarak görüldü |
| Sulh Ceza Hakimliği | Hayır (0 sonuç) | |
| Yargıtay Ceza Dairesi (İlk Derece) | Hayır (0 sonuç) | |
| Çocuk Ağır Ceza Mahkemesi | Hayır (0 sonuç) | |
| Çocuk Mahkemesi | Hayır (0 sonuç) | |
| İnfaz Hakimliği | Hayır (0 sonuç) | |
| İstinaf Ceza Dairesi (İlk Derece) | Hayır (0 sonuç) | |

## 2. Dosya türleri

- **Talimat Dosyası** (`dosyaTurKod=1`) — "Dosya Bilgileri" sekmesi VAR ama
  içeriği neredeyse boş (`sonuc:false`, ek alan yok).
- **Ceza Dava Dosyası** (`dosyaTurKod=3`) — "Dosya Bilgileri" sekmesi HİÇ
  YOK (`dosya_islem_turleri_sorgula_brd.ajx` yanıtında "ayrinti_bilgileri"
  anahtarı bulunmuyor). Bu yüzden `CezaDetay` gibi bir model YAZILMADI —
  UYAP'ın kendisi bu veriyi sunmuyor, kanıtlandı.

## 3. Taraf rolleri

Görülenler: Sanık (Kişi VEYA Kurum olabilir), Müşteki, Katılan (YENİ —
`DosyaTaraf.Rol.KATILAN` eklendi, mağdur/şikayetçi anlamında).

## 4. Safahat / Tahsilat-Harç-Reddiyat

Bkz. [00_GENEL.md §4-5](VERITABANI_BULGULARI_00_GENEL.md). Ceza'da Safahat
İcra Ceza Hakimliği'nde ÜCRETSİZ/kotasız gibi görünüyordu (test edilen
örnekte bir sorgu ücreti/limit uyarısı ÇIKMADI) — ama bu da yalnız o birim
için gözlemlendi, genel kural sayılmaz.

## 5. Kalan iş

- Sulh Ceza Hakimliği/Çocuk Mahkemeleri gibi 0 sonuç veren birimlerde
  kullanıcının veya ofisteki başka bir avukatın ileride dosyası olursa
  yeniden test edilmeli (özellikle Çocuk Mahkemesi'nin farklı bir taraf
  rol kümesi — "Çocuk" gibi — taşıyabileceği TAHMİN edilir ama
  doğrulanmadı).
