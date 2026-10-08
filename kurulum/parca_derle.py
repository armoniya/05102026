"""Kalan .py modüllerini süre sınırıyla derler (yeni iş 'sure' saniyeden sonra başlatılmaz)."""
import concurrent.futures
import importlib.util
import os
import sys
import time

paket = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uyapmasa_paketle.py")
spec = importlib.util.spec_from_file_location("pk", paket)
pk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pk)

sure = float(sys.argv[1]) if len(sys.argv) > 1 else 420
isci = 2
bitis = time.time() + sure
app = os.path.join(pk.STAGE, "app")
isler = sorted((os.path.join(k, a) for k, _d, f in os.walk(app) for a in f
                if a.endswith(".py") and not pk._acik_kalir(os.path.join(k, a))),
               key=os.path.getsize, reverse=True)
print(f"kalan: {len(isler)}", flush=True)
for n in range(isci):
    pk._ISCI_YUVALARI.put(n)


def is_(t):
    if time.time() > bitis:
        return t[1], "ATLANDI"
    return pk._derle_tek(t)


ok = hata = atla = 0
with concurrent.futures.ThreadPoolExecutor(isci) as ex:
    for yol, h in ex.map(is_, [(2000 + i, y) for i, y in enumerate(isler)]):
        if h is None:
            ok += 1
        elif h == "ATLANDI":
            atla += 1
        else:
            hata += 1
            print("HATA", yol, h[-1500:], flush=True)
print(f"derlendi={ok} hata={hata} sonraya={atla}", flush=True)
