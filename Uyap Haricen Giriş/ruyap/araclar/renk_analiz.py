"""UYAP ana CSS'inde lacivert/mavi ve gri tonlu renk bildirimlerini çıkarır.
Kullanım: python renk_analiz.py <css> [--uret <cikti.css>]"""
import re, sys, colorsys, collections

css = open(sys.argv[1], encoding='utf-8', errors='ignore').read()
css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)

# --- basit CSS ayrıştırıcı (iç içe @media/@supports destekli) ---
def kurallar(metin, ust_at=None):
    i, n = 0, len(metin)
    while i < n:
        j = metin.find('{', i)
        if j < 0:
            return
        bas = metin[i:j].strip()
        # eşleşen kapanış
        d, k = 1, j + 1
        while k < n and d:
            c = metin[k]
            if c == '{': d += 1
            elif c == '}': d -= 1
            k += 1
        govde = metin[j + 1:k - 1]
        if bas.startswith('@'):
            if bas.startswith(('@media', '@supports', '@layer')):
                yield from kurallar(govde, bas)
            # @font-face, @keyframes, @import… atlanır
        elif bas:
            yield ust_at, bas, govde
        i = k

RENK_RE = re.compile(r'#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|\b(?:white|gray|grey|silver|gainsboro|whitesmoke|lightgray|lightgrey|darkgray|navy)\b')
ADLAR = {'white': (255, 255, 255), 'gray': (128, 128, 128), 'grey': (128, 128, 128), 'silver': (192, 192, 192),
         'gainsboro': (220, 220, 220), 'whitesmoke': (245, 245, 245), 'lightgray': (211, 211, 211),
         'lightgrey': (211, 211, 211), 'darkgray': (169, 169, 169), 'navy': (0, 0, 128)}

def rgb(t):
    t = t.lower()
    if t in ADLAR: return ADLAR[t], 1.0
    if t.startswith('#'):
        h = t[1:]
        if len(h) in (3, 4): h = ''.join(c * 2 for c in h)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), a
    p = [x.strip() for x in re.split(r'[,\s/]+', t[t.index('(') + 1:-1]) if x.strip()]
    try:
        v = [float(x.rstrip('%')) for x in p[:3]]
        a = float(p[3].rstrip('%')) / (100 if p[3].endswith('%') else 1) if len(p) > 3 else 1.0
        return tuple(int(x) for x in v), a
    except Exception:
        return None, None

def sinif(c):
    r, g, b = [x / 255 for x in c]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h *= 360
    if s < 0.14 or (max(c) - min(c)) < 22:
        return 'gri', l
    if 180 <= h <= 260 and s < 0.24:          # mavi-gri (ör. Bootstrap secondary #90a4ae): gri sayılır
        return 'gri', l
    if 195 <= h <= 255 and s >= 0.18:
        return 'mavi', l
    return None, l

PROP_RE = re.compile(r'(?:^|;)\s*(background-color|background|border(?:-top|-bottom|-left|-right)?(?:-color)?|color|outline-color|fill|stroke|box-shadow)\s*:\s*([^;]+)', re.I)

def tara():
    for at, sec, govde in kurallar(css):
        for m in PROP_RE.finditer(govde):
            prop, deger = m.group(1).lower(), m.group(2)
            for rm in RENK_RE.finditer(deger):
                c, a = rgb(rm.group(0))
                if not c or len(c) != 3 or a is None or a < 0.15:
                    continue
                k, l = sinif(c)
                if k:
                    yield at, sec, prop, deger.strip(), rm.group(0), c, a, k, l

if __name__ == '__main__':
    say = collections.Counter(); ornek = {}
    for at, sec, prop, deger, ham, c, a, k, l in tara():
        anahtar = (k, prop.split('-')[0] if prop.startswith('border') else prop, '#%02x%02x%02x' % c)
        say[anahtar] += 1; ornek.setdefault(anahtar, sec[:70])
    toplam = sum(say.values())
    print('toplam renk bildirimi (mavi+gri):', toplam)
    for (k, p, h), n in say.most_common(45):
        print(f'{n:5d} {k:4s} {p:17s} {h}  örn: {ornek[(k, p, h)]}')
