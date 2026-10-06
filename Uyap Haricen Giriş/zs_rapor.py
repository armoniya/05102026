"""
/zs İş Takip — raporlar (zs_rapor.py)
------------------------------------
Kişi bazında ve zaman bazında rapor. Ham veriyi ZsStore.report_source() verir; hesap
burada saf Python ile yapılır (veritabanından bağımsız, test edilebilir).

Kapsam: yalnız ANA işler. Özel işlerden yalnız raporu alan kişinin kendi açtıkları girer (özel iş
yalnız açanına görünür). Silinmiş işler veritabanında olmadığı için sayılmaz.
Yönetici: kişi / iş / zaman raporu, herkes için. Kullanıcı: iş / zaman raporu, yalnız kendi işleri.

Tanımlar
- Biten iş: şu an onaylandı/tamamlandı durumundaki iş; bitiş anı son onay/tamamlama olayıdır.
- Süresinde / geç: onaya gönderilme (özel işte tamamlanma) anı son tarihe göre. Son tarihsiz
  iş "tarihsiz" sayılır.
- Kişinin işi: asıl atanan + ek atananlar (bir iş birden çok kişide sayılabilir).
- Onay kararı: onay/ret olayı; bekleme = karar anı - son onaya gönderilme anı. "Onay gecikti":
  iş süresinde gönderilmiş, karar son tarihten sonra verilmiş.

Gün sınırları Türkiye saatine (UTC+3, yaz saati yok) göre.
"""

import datetime as _dt

TR = _dt.timezone(_dt.timedelta(hours=3))
GROUPS = ("gun", "hafta", "ay")
MAX_DAYS = 3 * 366
_AY = ("Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")


class RaporError(ValueError):
    pass


def parse_range(bas: str, bit: str):
    """'YYYY-MM-DD' iki tarih → (başlangıç epoch, bitiş epoch [bitiş gününün sonu, hariç])."""
    try:
        d1 = _dt.date.fromisoformat(bas)
        d2 = _dt.date.fromisoformat(bit)
    except (TypeError, ValueError):
        raise RaporError("Tarih aralığı geçersiz.")
    if d2 < d1:
        raise RaporError("Bitiş tarihi başlangıçtan önce olamaz.")
    if (d2 - d1).days > MAX_DAYS:
        raise RaporError("Rapor aralığı en fazla 3 yıl olabilir.")
    return _epoch(d1), _epoch(d2 + _dt.timedelta(days=1))


def _epoch(d: _dt.date) -> int:
    return int(_dt.datetime(d.year, d.month, d.day, tzinfo=TR).timestamp())


def _date(ts: int) -> _dt.date:
    return _dt.datetime.fromtimestamp(ts, TR).date()


def _buckets(start: int, end: int, group: str):
    """[(etiket, başlangıç, bitiş)] — ilk/son dönem aralık sınırına kırpılır."""
    d, last = _date(start), _date(end - 1)
    if group == "hafta":
        d = d - _dt.timedelta(days=d.weekday())
    elif group == "ay":
        d = d.replace(day=1)
    out = []
    while d <= last:
        if group == "gun":
            nxt = d + _dt.timedelta(days=1)
            label = f"{d.day} {_AY[d.month - 1]}"
        elif group == "hafta":
            nxt = d + _dt.timedelta(days=7)
            e = nxt - _dt.timedelta(days=1)
            label = f"{d.day} {_AY[d.month - 1]} – {e.day} {_AY[e.month - 1]}"
        else:
            nxt = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
            label = f"{_AY[d.month - 1]} {d.year}"
        out.append((label, max(_epoch(d), start), min(_epoch(nxt), end)))
        d = nxt
    return out


def _prepare(src):
    """Her işe: assignees, finished_at, ontime (True/False/None), olay listesi."""
    tasks = {t["id"]: dict(t) for t in src["tasks"]}
    for t in tasks.values():
        t["assignees"] = [t["assignee_id"]] if t["assignee_id"] else []
        t["events"] = []
    for tid, uid in src["members"]:
        t = tasks.get(tid)
        if t and uid not in t["assignees"]:
            t["assignees"].append(uid)
    for e in src["events"]:
        t = tasks.get(e["task_id"])
        if t:
            t["events"].append(e)
    for t in tasks.values():
        done = t["status"] in ("onaylandi", "tamamlandi")
        fin = [e["created_at"] for e in t["events"] if e["kind"] in ("approved", "completed")]
        t["finished_at"] = fin[-1] if done and fin else None
        sent = t.get("submitted_at")
        t["ontime"] = None if not t["due_at"] or not sent else sent <= t["due_at"]
    return tasks


def _decisions(tasks):
    """Onay/ret kararları: [{actor, at, wait, late, kind}]."""
    out = []
    for t in tasks.values():
        last_sub = None
        for e in t["events"]:
            if e["kind"] == "submitted":
                last_sub = e["created_at"]
            elif e["kind"] in ("approved", "rejected") and last_sub:
                due = t["due_at"]
                out.append({"actor": e["actor_id"], "at": e["created_at"], "kind": e["kind"],
                            "wait": e["created_at"] - last_sub,
                            "late": bool(due and last_sub <= due < e["created_at"])})
    return out


def _avg(xs):
    return int(sum(xs) / len(xs)) if xs else None


def kisi_raporu(src, users, start, end, now):
    tasks = _prepare(src)
    decisions = [d for d in _decisions(tasks) if start <= d["at"] < end]
    rows = []
    for u in users:
        uid = u["id"]
        mine = [t for t in tasks.values() if uid in t["assignees"]]
        fin = [t for t in mine if t["finished_at"] and start <= t["finished_at"] < end]
        open_ = [t for t in mine if t["status"] not in ("onaylandi", "tamamlandi")]
        dec = [d for d in decisions if d["actor"] == uid]
        r = {
            "user_id": uid, "name": u["display_name"], "active": u["active"],
            "yeni": sum(1 for t in mine if start <= t["created_at"] < end),
            "biten": len(fin),
            "suresinde": sum(1 for t in fin if t["ontime"] is True),
            "gec": sum(1 for t in fin if t["ontime"] is False),
            "tarihsiz": sum(1 for t in fin if t["ontime"] is None),
            "reddedilen": sum(1 for t in mine for e in t["events"]
                              if e["kind"] == "rejected" and start <= e["created_at"] < end),
            "ort_sure": _avg([t["finished_at"] - t["created_at"] for t in fin]),
            "acik": len(open_),
            "geciken": sum(1 for t in open_ if t["due_at"] and t["due_at"] < now),
            "onayladi": sum(1 for d in dec if d["kind"] == "approved"),
            "reddetti": sum(1 for d in dec if d["kind"] == "rejected"),
            "ort_karar": _avg([d["wait"] for d in dec]),
            "gec_karar": sum(1 for d in dec if d["late"]),
        }
        keys = ("yeni", "biten", "reddedilen", "acik", "onayladi", "reddetti")
        if u["active"] or any(r[k] for k in keys):
            rows.append(r)
    return rows


def zaman_raporu(src, start, end, group, now, person=None):
    if group not in GROUPS:
        raise RaporError("Gruplama gün, hafta ya da ay olmalı.")
    tasks = _prepare(src)
    if person:
        tasks = {k: t for k, t in tasks.items() if person in t["assignees"]}
    rows = []
    for label, b0, b1 in _buckets(start, end, group):
        fin = [t for t in tasks.values() if t["finished_at"] and b0 <= t["finished_at"] < b1]
        rows.append({
            "label": label, "start": b0, "end": b1,
            "acilan": sum(1 for t in tasks.values() if b0 <= t["created_at"] < b1),
            "biten": len(fin),
            "suresinde": sum(1 for t in fin if t["ontime"] is True),
            "gec": sum(1 for t in fin if t["ontime"] is False),
            "reddedilen": sum(1 for t in tasks.values() for e in t["events"]
                              if e["kind"] == "rejected" and b0 <= e["created_at"] < b1),
            # Dönem sonunda açık: o ana kadar açılmış ve henüz bitmemiş. Gelecekteki dönemde boş.
            "acik_sonda": None if b0 > now else sum(
                1 for t in tasks.values() if t["created_at"] < b1 and not (t["finished_at"] and t["finished_at"] < b1)),
        })
    return rows


def is_listesi(src, start, end, person=None):
    """İş bazında rapor: aralıkta açılan, aralıkta biten ya da şu an açık işler."""
    out = []
    for t in _prepare(src).values():
        if person and person not in t["assignees"]:
            continue
        fin = t["finished_at"]
        open_ = t["status"] not in ("onaylandi", "tamamlandi")
        if open_ or start <= t["created_at"] < end or (fin and start <= fin < end):
            out.append({k: t[k] for k in ("id", "title", "status", "created_at", "due_at", "submitted_at",
                                          "finished_at", "ontime", "assignees", "is_private")})
    out.sort(key=lambda x: (x["due_at"] or 9e12, x["id"]))
    return out


# ── kişi etkinlik raporu: bir günde / haftada / ayda ne yaptı ─────────────────────────
# Olay türü → özet sütunu. Listede olmayanlar "diger" sayılır.
_ACT_CAT = {"created": "acti", "note": "not", "file": "belge", "subtask": "alt_is", "submitted": "onaya",
            "completed": "tamamladi", "approved": "onayladi", "rejected": "reddetti", "ext_requested": "ek_sure"}
ACT_CATS = ("acti", "not", "belge", "alt_is", "onaya", "tamamladi", "onayladi", "reddetti", "ek_sure", "diger")


def _act_cat(it):
    if it["kind"] == "created" and it["detail"].get("sub"):
        return "alt_is"
    if it["kind"] == "subtask" and not it["detail"].get("done"):
        return "diger"
    return _ACT_CAT.get(it["kind"], "diger")


def etkinlik(items, start, end):
    """items: ZsStore.activity_source() çıktısı (zamana göre sıralı).
    → {ozet: {tür: sayı, toplam, is_sayisi, aktif_gun}, gunler: [{label, start, end, toplam, ...}],
       isler: [{task_id, title, status, exists, is_private, toplam, ilk, son, <tür>: sayı}]}"""
    ozet = dict.fromkeys(ACT_CATS, 0)
    days = [{"label": lbl, "start": a, "end": b, "toplam": 0, **dict.fromkeys(ACT_CATS, 0)}
            for lbl, a, b in _buckets(start, end, "gun")]
    tasks = {}
    for it in items:
        c = _act_cat(it)
        it["cat"] = c
        ozet[c] += 1
        i = (_date(it["at"]) - _date(start)).days
        if 0 <= i < len(days):
            days[i][c] += 1
            days[i]["toplam"] += 1
        t = tasks.get(it["task_id"])
        if t is None:
            t = tasks[it["task_id"]] = {"task_id": it["task_id"], "title": it["title"], "status": it["status"],
                                        "exists": it["exists"], "is_private": it["is_private"], "toplam": 0,
                                        "ilk": it["at"], "son": it["at"], **dict.fromkeys(ACT_CATS, 0)}
        t[c] += 1
        t["toplam"] += 1
        t["son"] = max(t["son"], it["at"])
    ozet["toplam"] = len(items)
    ozet["is_sayisi"] = len(tasks)
    ozet["aktif_gun"] = sum(1 for d in days if d["toplam"])
    return {"ozet": ozet, "gunler": days, "isler": sorted(tasks.values(), key=lambda t: (-t["son"], t["task_id"]))}
