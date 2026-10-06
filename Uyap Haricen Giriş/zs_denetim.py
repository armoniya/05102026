"""
/zs İş Takip — denetim kaydı okuyucu (YALNIZ GELİŞTİRİCİ)
--------------------------------------------------------
zs_audit tablosu arayüzde ve API'de görünmez; bu araçla sunucuda (ya da veritabanına erişen
makinede) okunur. Sunucuyla aynı ortam değişkenleri kullanılır (DATABASE_URL varsa PostgreSQL,
yoksa UYAP_DATA_DIR/zs.sqlite3).

Örnekler
  python zs_denetim.py                         son 50 kayıt
  python zs_denetim.py --gun 7 --islem sil     son 7 günün silme işlemleri (is_sil, alt_is_sil, belge_sil)
  python zs_denetim.py --kisi ali --hatalar    ali'nin reddedilen/başarısız istekleri
  python zs_denetim.py --ayrinti 123           123 numaralı kaydın tamamı (gövde + önceki hâl)
  python zs_denetim.py --gun 30 --dosya denetim.csv     dosyaya yaz (.csv Excel için ; ayraçlı, .jsonl tam)
"""

import argparse
import csv
import datetime as dt
import json
import sys
import time

from zs_store import ZsStore

TR = dt.timezone(dt.timedelta(hours=3))


def _ts(t):
    return dt.datetime.fromtimestamp(t, TR).strftime("%Y-%m-%d %H:%M:%S") if t else ""


def _who(r):
    return f"{r['username'] or '?'}" + (f"({r['role']})" if r["role"] else "")


def _short(r):
    st = "OK " if r["ok"] else "RED"
    tgt = f"#{r['target_id']}" if r["target_id"] is not None else ""
    if not tgt and r["result"]:  # oluşturma: yeni kaydın kimliği yanıttan
        try:
            tgt = "→#" + str(next(iter(json.loads(r["result"]).values()))["id"])
        except (ValueError, StopIteration, KeyError, TypeError):
            pass
    err = f"  — {r['error']}" if r["error"] else ""
    return f"{r['id']:>6}  {_ts(r['at'])}  {_who(r):<20} {r['action']:<26} {tgt:<7} {st} {r['status']}  {r['ip'] or ''}{err}"


def _full(r):
    out = dict(r)
    out["at"] = _ts(r["at"])
    for k in ("data", "result", "before"):
        if out.get(k):
            try:
                out[k] = json.loads(out[k])
            except ValueError:
                pass
    return json.dumps(out, ensure_ascii=False, indent=2)


def main(argv=None):
    ap = argparse.ArgumentParser(description="İş Takip denetim kaydı (geliştirici)")
    ap.add_argument("--son", type=int, help="en fazla kaç kayıt (varsayılan 50; --dosya ile sınırsız; 0 = sınırsız)")
    ap.add_argument("--gun", type=float, help="son N gün")
    ap.add_argument("--kisi", help="kullanıcı adı ya da kimliği")
    ap.add_argument("--islem", help="işlem adında geçen metin (ör. sil, giris, is_durum:onayla) ya da yöntem (DELETE)")
    ap.add_argument("--hatalar", action="store_true", help="yalnız reddedilen/başarısız istekler")
    ap.add_argument("--ayrinti", type=int, metavar="ID", help="tek kaydın tamamını yazdır")
    ap.add_argument("--dosya", help="sonucu dosyaya yaz (.csv ya da .jsonl)")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    store = ZsStore()
    if a.ayrinti:
        rows = store.audit_rows(audit_id=a.ayrinti)
        if not rows:
            print("Kayıt bulunamadı.")
            return 1
        print(_full(rows[0]))
        return 0
    limit = a.son if a.son is not None else (0 if a.dosya else 50)
    since = int(time.time() - a.gun * 86400) if a.gun else None
    rows = store.audit_rows(since=since, user=a.kisi, action_like=a.islem, only_errors=a.hatalar, limit=limit)
    if a.dosya:
        if a.dosya.lower().endswith(".jsonl"):
            with open(a.dosya, "w", encoding="utf-8") as f:
                for r in reversed(rows):
                    f.write(json.dumps({**r, "at_tr": _ts(r["at"])}, ensure_ascii=False) + "\n")
        else:
            with open(a.dosya, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                cols = ["id", "at_tr"] + [c for c in ZsStore._AUDIT_COLS if c != "at"]
                w.writerow(cols)
                for r in reversed(rows):
                    w.writerow([_ts(r["at"]) if c == "at_tr" else r.get(c) for c in cols])
        print(f"{len(rows)} kayıt yazıldı: {a.dosya}")
        return 0
    for r in reversed(rows):
        print(_short(r))
    print(f"— {len(rows)} kayıt ({store.backend_name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
