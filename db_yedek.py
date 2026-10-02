"""
Gömülü PostgreSQL'in (uyap_icra) günlük yedeği ve geri yüklemesi.

Yedekler:  %LOCALAPPDATA%\\UyapIcra\\yedek\\uyap_icra_YYYYMMDD_HHMMSS.dump
  - pg_dump özel biçim (-Fc, sıkıştırılmış); uygulama açıkken alınabilir (tutarlı anlık görüntü).
  - Önce .tmp'ye yazılır, `pg_restore --list` ile okunabildiği doğrulanınca adı verilir —
    yarım kalan/bozuk dosya yedek sayılmaz.
  - Son SAKLA adet yedek tutulur, eskiler silinir.
  - Klasör yalnız bu Windows kullanıcısına açık (icacls) — içinde borçlu/kişi verisi var.
  - Yedek AYNI diskte: silinen kayda/bozulan migration'a karşı korur, disk arızasına karşı
    KORUMAZ. Dışarı kopyalamak kullanıcının işi (bkz. docs/DB_YEDEK.md).

Kullanım:
  python db_yedek.py                     # şimdi yedek al
  python db_yedek.py --liste             # yedekleri listele
  python db_yedek.py --geri-yukle DOSYA  # uyap_icra_geri adlı AYRI veritabanına yükler (güvenli)
  python db_yedek.py --geri-yukle DOSYA --hedef uyap_icra --uzerine-yaz
        # GERÇEK veritabanını değiştirir: önce mevcut hali yedekler, sonra siler+yükler.
        # UyapMasa KAPALIYKEN çalıştırın.
"""

import argparse
import datetime as _dt
import glob
import os
import subprocess
import sys
import threading

import db_baslat as _db

YEDEK_DIR = os.path.join(_db.DB_BASE, "yedek")
SAKLA = int(os.environ.get("UYAP_DB_YEDEK_SAKLA", "14"))
_ON_EK = f"{_db.DB_ADI}_"


def _env():
    return dict(os.environ, PGPASSWORD=_db._db_parolasi())


def _baglanti():
    return ["-U", _db.DB_USER, "-h", _db.DB_HOST, "-p", _db.DB_PORT]


def _klasor_hazirla():
    yeni = not os.path.isdir(YEDEK_DIR)
    os.makedirs(YEDEK_DIR, exist_ok=True)
    if yeni and sys.platform.startswith("win"):
        try:
            subprocess.run(
                ["icacls", YEDEK_DIR, "/inheritance:r",
                 "/grant:r", f"{os.environ.get('USERNAME', '')}:(OI)(CI)F"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                creationflags=_db._NO_WINDOW,
            )
        except Exception:
            pass


def yedekler():
    """Tamamlanmış yedekler, en yeni en sonda."""
    return sorted(glob.glob(os.path.join(YEDEK_DIR, _ON_EK + "*.dump")))


def _temizle(log):
    fazla = yedekler()[:-SAKLA] if SAKLA > 0 else []
    for y in fazla:
        try:
            os.remove(y)
            log(f"  eski yedek silindi: {os.path.basename(y)}")
        except OSError as e:
            log(f"⚠️ eski yedek silinemedi: {y}: {e}")
    for t in glob.glob(os.path.join(YEDEK_DIR, "*.tmp")):   # önceki yarım kalmış denemeler
        try:
            os.remove(t)
        except OSError:
            pass


def yedek_al(log=print):
    """Şimdi yedek al. Başarılıysa dosya yolunu, değilse None döner."""
    if not _db.sunucu_ayakta():
        log("⛔ DB yedeği alınamadı: PostgreSQL çalışmıyor")
        return None
    _klasor_hazirla()
    ad = f"{_ON_EK}{_dt.datetime.now():%Y%m%d_%H%M%S}.dump"
    hedef = os.path.join(YEDEK_DIR, ad)
    tmp = f"{hedef}.{os.getpid()}.tmp"
    rc, out = _db._calistir(
        [_db._exe("pg_dump"), *_baglanti(), "-d", _db.DB_ADI, "-Fc", "-Z", "6", "-f", tmp],
        env=_env(),
    )
    if rc != 0:
        log("⛔ pg_dump başarısız:\n" + out)
        _sil(tmp)
        return None
    rc, out = _db._calistir([_db._exe("pg_restore"), "--list", tmp])
    if rc != 0 or "TABLE DATA" not in out:
        log("⛔ yedek doğrulanamadı (pg_restore --list):\n" + out[-2000:])
        _sil(tmp)
        return None
    os.replace(tmp, hedef)
    log(f"✓ DB yedeği: {ad} ({os.path.getsize(hedef) / 1e6:.1f} MB)")
    _temizle(log)
    return hedef


def _sil(yol):
    try:
        os.remove(yol)
    except OSError:
        pass


def bugun_yedek_var_mi():
    bugun = f"{_ON_EK}{_dt.date.today():%Y%m%d}_"
    return any(os.path.basename(y).startswith(bugun) for y in yedekler())


def gunluk_yedek(log=print):
    """Bugün yedek yoksa al (açılışta çağrılır; günde en fazla bir yedek)."""
    try:
        if bugun_yedek_var_mi():
            return None
        return yedek_al(log)
    except Exception as e:
        log(f"⚠️ Günlük DB yedeği alınamadı: {e}")
        return None


def gunluk_yedek_arkaplanda(log=print):
    """Açılışı bekletmemek için ayrı thread'de (pg_dump 100+ MB'ta birkaç saniye sürer)."""
    t = threading.Thread(target=gunluk_yedek, args=(log,), name="db-gunluk-yedek", daemon=True)
    t.start()
    return t


def _psql(sql, db="postgres"):
    return _db._calistir(
        [_db._exe("psql"), *_baglanti(), "-d", db, "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        env=_env(),
    )


def geri_yukle(dosya, hedef="uyap_icra_geri", uzerine_yaz=False, log=print):
    """Yedeği `hedef` veritabanına yükler. Gerçek DB'ye yükleme yalnız uzerine_yaz=True ile."""
    hedef = _db._guvenli_sql_kimlik(hedef, "hedef")
    if not os.path.isfile(dosya):
        log(f"⛔ yedek dosyası yok: {dosya}")
        return False
    if not _db.sunucu_ayakta():
        log("⛔ PostgreSQL çalışmıyor")
        return False
    rc, out = _psql(f"SELECT 1 FROM pg_database WHERE datname='{hedef}'")
    var = rc == 0 and out.strip() == "1"
    if var:
        if not uzerine_yaz:
            log(f"⛔ '{hedef}' zaten var; üzerine yazmak için --uzerine-yaz verin")
            return False
        if hedef == _db.DB_ADI:
            log("… gerçek veritabanı değişecek; önce mevcut hali yedekleniyor")
            if not yedek_al(log):
                log("⛔ güvenlik yedeği alınamadı — geri yükleme iptal")
                return False
        _psql(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
              f"WHERE datname='{hedef}' AND pid<>pg_backend_pid()")
        rc, out = _psql(f"DROP DATABASE {hedef}")
        if rc != 0:
            log("⛔ eski veritabanı silinemedi:\n" + out)
            return False
    rc, out = _db._calistir(
        [_db._exe("createdb"), *_baglanti(), "-E", "UTF8", "-T", "template0", hedef], env=_env())
    if rc != 0:
        log("⛔ createdb başarısız:\n" + out)
        return False
    rc, out = _db._calistir(
        [_db._exe("pg_restore"), *_baglanti(), "-d", hedef, "--no-owner", "--exit-on-error", dosya],
        env=_env())
    if rc != 0:
        log("⛔ pg_restore başarısız:\n" + out)
        return False
    log(f"✓ '{os.path.basename(dosya)}' → '{hedef}' yüklendi")
    return True


def main(argv=None):
    p = argparse.ArgumentParser(description="uyap_icra veritabanı yedeği")
    p.add_argument("--liste", action="store_true")
    p.add_argument("--geri-yukle", metavar="DOSYA")
    p.add_argument("--hedef", default="uyap_icra_geri")
    p.add_argument("--uzerine-yaz", action="store_true")
    a = p.parse_args(argv)
    if a.liste:
        for y in yedekler():
            print(f"{os.path.getsize(y) / 1e6:8.1f} MB  {y}")
        return 0
    if a.geri_yukle:
        return 0 if geri_yukle(a.geri_yukle, a.hedef, a.uzerine_yaz) else 1
    return 0 if yedek_al() else 1


if __name__ == "__main__":
    sys.exit(main())
