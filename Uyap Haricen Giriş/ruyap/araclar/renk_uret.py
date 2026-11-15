"""UYAP ana CSS'indeki lacivert/mavi ve gri renkleri bizim tema değişkenlerimize bağlayan katmanı üretir.
Kullanım: python renk_uret.py <uyap.css> <cikti.css>"""
import re, sys, colorsys, collections, importlib.util, os

spec = importlib.util.spec_from_file_location('ra', os.path.join(os.path.dirname(__file__), 'renk_analiz.py'))
sys.argv = [sys.argv[0], sys.argv[1], sys.argv[2]]
ra = importlib.util.module_from_spec(spec)
_kaynak, _cikti = sys.argv[1], sys.argv[2]
sys.argv = [sys.argv[0], _kaynak]
spec.loader.exec_module(ra)

ATLA_SECICI = re.compile(
    r'data-theme=dark|data-bs-theme=dark|\.dx-map|\.dx-colorview|\.dx-color-|\.dx-chat|\.e-speeddial|\.dx-gantt|\.dx-diagram|'
    r'\.dx-scheduler|\.dx-pivot|\.dx-vectormap|\.dx-sankey|\.dx-funnel|\.dx-gallery|\.dx-scrollable-native|'
    r'\.cu-|#uyap-|swal2|::-webkit-scrollbar|\.dx-loadindicator|\.dx-progressbar|\.dx-slider|\.dx-rangeslider|'
    r'\.dx-filemanager|\.dx-htmleditor|\.dx-splitter|\.dx-resize-handle|\.color-picker|\.dx-switch', re.I)

def gri_zemin(l):
    if l >= 0.985: return 'var(--cu-t-yuzey)'
    if l >= 0.935: return 'var(--cu-t-serit)'
    if l >= 0.84: return 'var(--cu-t-kenar)'
    if l >= 0.55: return 'var(--cu-vurgu-acik)'
    if l >= 0.08: return 'var(--cu-vurgu-koyu)'
    return None                                    # siyah zemin (karartma) dokunulmaz

def gri_kenar(l):
    if l >= 0.97: return None                      # beyaz kenar (boşluk görevi) dokunulmaz
    if l >= 0.70: return 'var(--cu-t-kenar)'
    if l >= 0.08: return 'var(--cu-vurgu-kenar)'
    return None

def gri_yazi(l):
    if l <= 0.06 or l >= 0.97: return None         # saf siyah/beyaz yazı nötr: dokunulmaz
    if l < 0.30: return 'var(--cu-t-yazi)'
    if l < 0.62: return 'var(--cu-t-soluk)'
    return 'var(--cu-t-silik)'

def mavi(l, prop):
    if prop == 'color':
        return 'var(--cu-vurgu-yazi)' if l < 0.5 else 'var(--cu-vurgu-orta)'
    if l < 0.22: return 'var(--cu-vurgu-koyu)'
    if l < 0.50: return 'var(--cu-vurgu)'
    if l < 0.66: return 'var(--cu-vurgu-orta)'
    if l < 0.80: return 'var(--cu-vurgu-parlak)'
    if l < 0.90: return 'var(--cu-vurgu-acik)'
    return 'var(--cu-vurgu-zemin)'

DEGISKEN_RE = re.compile(r'(?:^|;)\s*(--bs-[\w-]+)\s*:\s*([^;]+)')
YALIN_RENK = re.compile(r'^\s*(' + ra.RENK_RE.pattern + r')\s*(!important)?\s*$', re.I)

def hedef_ozellik(prop, deger):
    """(çıktı özelliği, renk metni) — eşlenemezse None."""
    if prop in ('background', 'background-color'):
        m = YALIN_RENK.match(deger)
        if not m: return None                      # gradyan / resim / karma kısaltma: dokunulmaz
        return 'background-color', m.group(1)
    if prop.startswith('border'):
        if prop.endswith('-color'):
            m = YALIN_RENK.match(deger)
            return (prop, m.group(1)) if m else None
        rm = ra.RENK_RE.search(deger)
        if not rm or re.search(r'\b(none|hidden)\b', deger): return None
        return prop + '-color', rm.group(0)
    if prop in ('color', 'outline-color', 'fill', 'stroke'):
        m = YALIN_RENK.match(deger)
        return (prop, m.group(1)) if m else None
    return None

def onekle(sec):
    out = []
    for s in [x.strip() for x in sec.split(',') if x.strip()]:
        if s.startswith(':root'):
            s = 'html[data-cu-deri]' + s[5:]
            out.append(s); continue
        if s.startswith('html'):
            s = 'html[data-cu-deri]' + s[4:]
        else:
            s = 'html[data-cu-deri] ' + s
        out.append(s)
    return ','.join(out)

kurallar = collections.OrderedDict()   # (at, secici) -> {prop: deger}
istat = collections.Counter()
for at, sec, govde in ra.kurallar(ra.css):
    if ATLA_SECICI.search(sec):
        continue
    bildirimler = {}
    mavi_zemin = False
    beyaz_yazi = False
    for m in ra.PROP_RE.finditer(govde):
        prop, deger = m.group(1).lower(), m.group(2).strip()
        if prop == 'box-shadow':
            continue
        h = hedef_ozellik(prop, deger)
        if not h: continue
        cikis, renk = h
        c, a = ra.rgb(renk)
        if not c or len(c) != 3 or a is None or a < 0.15: continue
        if prop == 'color' and c == (255, 255, 255): beyaz_yazi = True
        k, l = ra.sinif(c)
        if not k: continue
        if k == 'mavi':
            v = mavi(l, 'color' if cikis in ('color', 'fill', 'stroke') else 'zemin')
            if cikis == 'background-color' and l < 0.66: mavi_zemin = True
        elif cikis == 'background-color':
            v = gri_zemin(l)
        elif cikis.startswith('border') or cikis == 'outline-color':
            v = gri_kenar(l)
        else:
            v = gri_yazi(l)
        if not v: continue
        if a < 0.999:
            v = 'color-mix(in srgb, %s %d%%, transparent)' % (v, round(a * 100))
        bildirimler[cikis] = v
        istat[k] += 1
    # Bootstrap 5 renk değişkenleri (--bs-*): UYAP lacivert paleti, gri palet, düğme/tablo renkleri.
    for m in DEGISKEN_RE.finditer(govde):
        ad, deger = m.group(1).lower(), m.group(2).strip()
        if ad.endswith('-rgb'):
            uc = [x.strip() for x in deger.split(',')]
            if len(uc) == 3 and all(x.isdigit() for x in uc):
                k, l = ra.sinif(tuple(int(x) for x in uc))
                if k == 'mavi':
                    bildirimler[ad] = 'var(--cu-vurgu-rgb)'; istat['bs-rgb'] += 1
            continue
        mm = YALIN_RENK.match(deger)
        if not mm: continue
        c, a = ra.rgb(mm.group(1))
        if not c or len(c) != 3 or a is None or a < 0.15: continue
        k, l = ra.sinif(c)
        if not k: continue
        if 'border' in ad: rol = 'kenar'
        elif ad.endswith('-color') or 'emphasis' in ad or ad.endswith('-text'): rol = 'yazi'
        elif 'bg' in ad or 'background' in ad: rol = 'zemin'
        elif k == 'gri' and l < 0.84: rol = 'yazi'
        else: rol = 'zemin'
        if k == 'mavi':
            v = mavi(l, 'color' if rol == 'yazi' else 'zemin')
        elif rol == 'zemin': v = gri_zemin(l)
        elif rol == 'kenar': v = gri_kenar(l)
        else: v = gri_yazi(l)
        if not v: continue
        if a < 0.999: v = 'color-mix(in srgb, %s %d%%, transparent)' % (v, round(a * 100))
        bildirimler[ad] = v; istat['bs-' + k] += 1
    # Lacivert zeminli Bootstrap düğmesinin beyaz yazısı: temanın vurgu-üzeri rengi (Güneş'te koyu).
    if bildirimler.get('--bs-btn-bg', '').startswith('var(--cu-vurgu'):
        for ad in ('--bs-btn-color', '--bs-btn-hover-color', '--bs-btn-active-color', '--bs-btn-disabled-color'):
            bildirimler[ad] = 'var(--cu-vurgu-uzeri)'
    if mavi_zemin and beyaz_yazi:
        bildirimler['color'] = 'var(--cu-vurgu-uzeri)'
    if not bildirimler: continue
    yeni = onekle(sec)
    if not yeni: continue
    kurallar.setdefault((at, yeni), {}).update(bildirimler)

# grupla: aynı @media altındakiler bir arada
satirlar = []
gruplar = collections.OrderedDict()
for (at, sec), b in kurallar.items():
    gruplar.setdefault(at, []).append((sec, b))
for at, liste in gruplar.items():
    govdeler = []
    for sec, b in liste:
        govdeler.append(sec + '{' + ';'.join('%s:%s!important' % kv for kv in b.items()) + '}')
    if at:
        satirlar.append(at + '{' + ''.join(govdeler) + '}')
    else:
        satirlar.extend(govdeler)

bas = """/* ══ ARMONIA RENK KATMANI — OTOMATİK ÜRETİLDİ (2026-11-14), ELLE DÜZENLEMEYİN ══
   Kaynak: UYAP ana stil dosyası (static_cache). Oradaki lacivert/mavi ve gri tonlu her zemin, kenar ve yazı
   rengi bizim tema değişkenlerimize bağlanır; böylece ana menüden açılan bütün sayfalar seçili temamızın
   renklerini alır (kullanıcı isteği: UYAP tasarımından kopuş, gri/lacivert kalmasın).
   Yeniden üretmek için: renk_uret.py <uyap.css> armonia-renk.css. Elle ince ayarlar tema.css'te (bundan sonra
   yüklenir, eşit özgüllükte onlar kazanır). Koyu tema (data-cu-deri yok) bu katmandan etkilenmez. */
html[data-cu-deri] {
    --cu-t-soluk: color-mix(in srgb, var(--cu-t-yazi) 72%, var(--cu-t-yuzey));
    --cu-t-silik: color-mix(in srgb, var(--cu-t-yazi) 48%, var(--cu-t-yuzey));
}
"""
open(_cikti, 'w', encoding='utf-8', newline='\n').write(bas + '\n'.join(satirlar) + '\n')
print('kural', len(kurallar), 'bildirim', sum(len(b) for b in kurallar.values()), dict(istat),
      'boyut', os.path.getsize(_cikti))
