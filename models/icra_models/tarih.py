# -*- coding: utf-8 -*-
"""
UYAP / PTT tarih metinlerini tek yerden datetime'a çevirir (2026-11-14).

Kaynaklarda görülen biçimler (hepsi canlı veride var):
  • "28/09/2026", "28/09/2026 14:05"       — barkod_sorgu (evrak tarihi, son işlem)
  • "28.09.2026", "28.09.2026 14:05:09"    — UYAP listeleri
  • "20260727"                              — PTT `sondurum` tarihleri
  • "Mar 13, 2026 11:44:45 AM"              — safahat / tahsilat / reddiyat
  • "2026-09-28", "2026-09-28T14:05:09"     — ISO
  • {"date": {...}, "time": {...}}          — UYAP JSON tarih nesnesi
Çözülemeyen değer None döner; tarih UYDURULMAZ. Modeller ham metni olduğu gibi
saklar, bu modülle türetilen tarih alanı sorgu/sıralama/hesap için kullanılır.
"""
import datetime
import re

_AYLAR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_GAY_RE = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?")
_SIKISIK_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_ING_RE = re.compile(r"^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})"
                     r"(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?)?")


def _kur(y, a, g, s=0, d=0, sn=0):
    try:
        return datetime.datetime(int(y), int(a), int(g), int(s or 0), int(d or 0), int(sn or 0))
    except (TypeError, ValueError):
        return None


def tarih_coz(deger):
    """Değeri naive datetime'a çevirir; olmazsa None."""
    if deger is None or deger == "":
        return None
    if isinstance(deger, datetime.datetime):
        return deger
    if isinstance(deger, datetime.date):
        return datetime.datetime(deger.year, deger.month, deger.day)
    if isinstance(deger, dict):
        t, s = deger.get("date") or {}, deger.get("time") or {}
        if not t:
            return None
        return _kur(t.get("year"), t.get("month"), t.get("day"),
                    s.get("hour"), s.get("minute"), s.get("second"))
    m_str = str(deger).strip()
    if not m_str or m_str == "0":
        return None
    m = _GAY_RE.match(m_str)
    if m:
        g, a, y, s, d, sn = m.groups()
        return _kur(y, a, g, s, d, sn)
    m = _ISO_RE.match(m_str)
    if m:
        return _kur(*m.groups())
    m = _SIKISIK_RE.match(m_str)
    if m:
        return _kur(*m.groups())
    m = _ING_RE.match(m_str)
    if m:
        ay, g, y, s, d, sn, ogle = m.groups()
        ay_no = _AYLAR.get(ay.lower()[:3])
        if not ay_no:
            return None
        s = int(s or 0)
        if ogle:
            ogle = ogle.lower()
            if ogle == "pm" and s < 12:
                s += 12
            elif ogle == "am" and s == 12:
                s = 0
        return _kur(y, ay_no, g, s, d, sn)
    return None


def gun_coz(deger):
    """Yalnız gün (datetime.date) ya da None."""
    t = tarih_coz(deger)
    return t.date() if t else None
