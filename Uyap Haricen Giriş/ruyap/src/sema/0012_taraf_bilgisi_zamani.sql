-- Dosyanın taraf bilgileri (UYAP `dosya_taraf_bilgileri_brd`: taraflar ve VEKİLLERİ) en son ne zaman okundu (20.11.2026).
-- Boşsa "Tarafları güncelle" bu dosyaya bir kez taraf bilgileri isteği atar: Dosya Listesi'ndeki "Rolüm" sütunu
-- e-imza avukatını bütün tarafların (borçlu dahil) vekil listelerinde arar. Bu sürümden önce borçlu kaydı olan dosyalarda
-- borçlunun vekilleri yazılmıyordu; o yüzden eski kayıtların hepsi boş başlar.
ALTER TABLE dosya ADD COLUMN IF NOT EXISTS taraf_bilgisi_zamani timestamp with time zone NULL;
