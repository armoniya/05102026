# -*- coding: utf-8 -*-
"""Derlenmiş .pyd'leri çökmeye karşı denetler (kurulum/uyapmasa_paketle.py çağırır).

Her modül AYRI bir süreçte, kurulumdaki gibi sys.path ile import edilir. Python
istisnası (ör. UYAP/DB yok) sorun sayılmaz; yalnız süreç ÇÖKERSE (access violation)
modül bozuk derlenmiştir — 2026-10-08'de paralel Nuitka derlemesinde ara sıra görüldü.
"""
import os
import subprocess
import sys

CRASH = {3221225477, -1073741819, 139, -11}   # 0xC0000005 (access violation)


def modul_adi(yol):
    """.../pkg/alt/x.cp314-win_amd64.pyd → ('<paketsiz kök>', 'pkg.alt.x')."""
    parcalar = [os.path.basename(yol).split(".")[0]]
    d = os.path.dirname(yol)
    while os.path.exists(os.path.join(d, "__init__.py")) or any(
            f.startswith("__init__.") for f in os.listdir(d)):
        parcalar.insert(0, os.path.basename(d))
        d = os.path.dirname(d)
    return d, ".".join(parcalar)


def bozuklar(app, python):
    kokler = sorted({k for k, _d, f in os.walk(app) if any(x.endswith(".pyd") for x in f)})
    pydler = [os.path.join(k, a) for k in kokler for a in os.listdir(k) if a.endswith(".pyd")]
    bozuk = []
    for yol in pydler:
        kok, ad = modul_adi(yol)
        kod = ("import sys; sys.path[:0] = %r\n"
               "try:\n    import %s\nexcept BaseException:\n    pass\n") % ([kok] + kokler, ad)
        try:
            r = subprocess.run([python, "-s", "-c", kod], cwd=kok, capture_output=True,
                               timeout=120, stdin=subprocess.DEVNULL)
            if r.returncode in CRASH or r.returncode < 0:
                bozuk.append(yol)
        except subprocess.TimeoutExpired:
            pass   # asılı kalan import (ör. GUI/ağ bekleme) çökme değildir
    return bozuk


if __name__ == "__main__":
    b = bozuklar(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))
    print(f"bozuk: {len(b)}")
    for y in b:
        print("  ", y)
