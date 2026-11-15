# -*- coding: utf-8 -*-
"""
İcra şemasını (src/sema/0002_icra.sql) örnek Django modelinden üretir.

Kullanıcı kararı (15.11.2026): büro veritabanının modeli models/icra_models/models.py
ile ALAN ALAN aynı olacak (sadeleştirme yok). Bu yüzden tablo tanımları elle
yazılmaz; Django'nun kendi şema üreticisinden (PostgreSQL arka ucu) alınır.

Yapılan tek dönüşümler:
  • tablo adları: icra_models_dosyataraf → dosya_taraf (uygulama öneki atılır)
  • Django'nun Python tarafında verdiği varsayılanlar (default=…, blank metin → '',
    auto_now(_add) → now()) sütuna DEFAULT olarak yazılır — Rust'ta ORM yok.
  • Django'nun Python tarafında uyguladığı on_delete (CASCADE / SET_NULL) yabancı
    anahtara ON DELETE olarak yazılır; PROTECT veritabanının olağan davranışıdır.
Bizim eklediklerimiz dosyanın sonundaki EKLER bölümündedir (bu betikte sabit).

Çalıştırma (cokluuyap kökünden, UyapMasa .venv'i ile; çalışan bir PostgreSQL'e
yalnız sürüm sorgusu için bağlanır, hiçbir şey yazmaz):
    .venv/Scripts/python.exe "Uyap Haricen Giriş/ruyap/araclar/sema_uret.py"          # yazar
    .venv/Scripts/python.exe "Uyap Haricen Giriş/ruyap/araclar/sema_uret.py" --denetle # yalnız karşılaştırır
"""
import decimal
import os
import re
import sys

BURASI = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.abspath(os.path.join(BURASI, "..", "..", ".."))
HEDEF = os.path.join(BURASI, "..", "src", "sema", "0002_icra.sql")
sys.path[:0] = [KOK, os.path.join(KOK, "models")]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "uyapdata.settings")
if not os.environ.get("UYAP_DB_PASSWORD"):
    import db_baslat
    os.environ["UYAP_DB_PASSWORD"] = db_baslat._db_parolasi()

import django  # noqa: E402

django.setup()
from django.apps import apps  # noqa: E402
from django.db import connection, models  # noqa: E402


def yilan(ad):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", ad).lower()


# Evrak: örnek modelde belge önbelleği; biz evrak LİSTESİNİ de burada tutacağız
# (içerik indirilmeden) — bu iki alan o durumda boş kalır.
EK_VARSAYILAN = {("evrak", "sha256"): "''", ("evrak", "dosya_yolu"): "''"}


def sql_deger(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, decimal.Decimal, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def varsayilan(tablo, alan):
    if (tablo, alan.column) in EK_VARSAYILAN:
        return EK_VARSAYILAN[(tablo, alan.column)]
    if getattr(alan, "auto_now", False) or getattr(alan, "auto_now_add", False):
        return "now()"
    if alan.primary_key or alan.is_relation:
        return None
    if alan.has_default():
        return sql_deger(alan.get_default())
    if isinstance(alan, (models.CharField, models.TextField)) and alan.blank and not alan.null:
        return "''"
    return None


def ust_duzey_bol(govde):
    parcalar, derinlik, bas = [], 0, 0
    for i, c in enumerate(govde):
        if c == "(":
            derinlik += 1
        elif c == ")":
            derinlik -= 1
        elif c == "," and derinlik == 0:
            parcalar.append(govde[bas:i].strip())
            bas = i + 1
    parcalar.append(govde[bas:].strip())
    return parcalar


def tablo_bicimle(sql, tablo, vars_):
    m = re.match(r'^CREATE TABLE "([^"]+)" \((.*)\);?$', sql, re.S)
    assert m and m.group(1) == tablo, sql
    satirlar = []
    for p in ust_duzey_bol(m.group(2)):
        s = re.match(r'^"([^"]+)" ', p)
        if s and vars_.get(s.group(1)) is not None:
            p += " DEFAULT " + vars_[s.group(1)]
        satirlar.append("    " + p)
    return 'CREATE TABLE "%s" (\n%s\n);' % (tablo, ",\n".join(satirlar))


def uret():
    uyg = apps.get_app_config("icra_models")
    modeller = list(uyg.get_models(include_auto_created=True))
    for mo in modeller:  # önce tüm adlar (FK'ler yeni adı görsün)
        ad = mo._meta.object_name
        if mo._meta.auto_created:  # DosyaTaraf_vekiller
            sahip, alan = ad.split("_", 1)
            mo._meta.db_table = yilan(sahip) + "_" + alan
        else:
            mo._meta.db_table = yilan(ad)
    cikti = []
    with connection.schema_editor(collect_sql=True, atomic=False) as ed:
        for mo in modeller:
            if mo._meta.auto_created:
                continue  # create_model sahibinin içinde üretir
            once = len(ed.collected_sql)
            ed.create_model(mo)
            yeni = ed.collected_sql[once:]
            del ed.collected_sql[once:]
            cikti.append(("tablo", mo, yeni))
        ertelenen_bas = len(ed.collected_sql)
    ertelenen = ed.collected_sql[ertelenen_bas:]
    tablo_modeli = {mo._meta.db_table: mo for mo in modeller}

    s = []
    for _t, mo, sqller in cikti:
        s.append("")
        s.append("-- %s (%s)" % (mo._meta.object_name, mo._meta.verbose_name))
        for q in sqller:
            q = q.strip()
            if q.startswith("--"):
                continue
            m = re.match(r'^CREATE TABLE "([^"]+)"', q)
            if m:
                t = m.group(1)
                vars_ = {a.column: varsayilan(t, a) for a in tablo_modeli[t]._meta.local_fields}
                q = tablo_bicimle(q, t, vars_)
            s.append(q if q.endswith(";") else q + ";")
    s.append("")
    s.append("-- Yabancı anahtarlar ve indeksler")
    silme = {}
    for mo in modeller:
        for a in mo._meta.local_fields:
            if a.is_relation and a.remote_field is not None:
                od = a.remote_field.on_delete
                silme[(mo._meta.db_table, a.column)] = (
                    " ON DELETE CASCADE" if od is models.CASCADE else
                    " ON DELETE SET NULL" if od is models.SET_NULL else
                    "" if od is models.PROTECT else None)
    for q in ertelenen:
        q = q.strip()
        m = re.match(r'^ALTER TABLE "([^"]+)" ADD CONSTRAINT "[^"]+" FOREIGN KEY \("([^"]+)"\)', q)
        if m:
            ek = silme[(m.group(1), m.group(2))]
            assert ek is not None, q
            q = q.replace(" DEFERRABLE", ek + " DEFERRABLE")
        if q and not q.startswith("--"):
            s.append(q if q.endswith(";") else q + ";")
    govde = "\n".join(s)
    assert "icra_models_" not in govde, "uygulama öneki kaldı"
    return govde


BASLIK = """-- İcra veri modeli (Aşama 2, 15.11.2026).
-- ELLE DÜZENLEME: «ÜRETİLEN» bölümü araclar/sema_uret.py örnek modelden
-- (models/icra_models/models.py, 0035 hâli) üretir; alan alan aynıdır.
-- Uygulanmış bir şema dosyası sonradan DEĞİŞTİRİLMEZ; değişiklik yeni dosyayla gelir.
-- Seçenek (choices) değerleri veritabanında zorlanmaz; src/icra_veri.rs'te sabittir.

-- >>> ÜRETİLEN"""

EKLER = """-- <<< ÜRETİLEN

-- EKLER (örnek modelde yok; bizim eklediklerimiz)

-- Borçlu başına takip aşaması (Takip Talebi → Ödeme Emri → … → Kesinleşti) ve günü.
ALTER TABLE "dosya_taraf" ADD COLUMN "takip_asamasi" varchar(20) NOT NULL DEFAULT '';
ALTER TABLE "dosya_taraf" ADD COLUMN "takip_asamasi_tarihi" date NULL;
CREATE INDEX "dosya_taraf_takip_asamasi_idx" ON "dosya_taraf" ("takip_asamasi");
"""


def main():
    metin = BASLIK + "\n" + uret() + "\n\n" + EKLER
    if "--denetle" in sys.argv:
        with open(HEDEF, encoding="utf-8") as f:
            eski = f.read()
        if eski.replace("\r\n", "\n") != metin:
            print("FARK VAR: 0002_icra.sql örnek modelle aynı değil")
            sys.exit(1)
        print("AYNI: 0002_icra.sql örnek modelle alan alan aynı")
        return
    with open(HEDEF, "w", encoding="utf-8", newline="\n") as f:
        f.write(metin)
    print("yazıldı:", os.path.normpath(HEDEF), "(%d satır)" % metin.count("\n"))


if __name__ == "__main__":
    main()
