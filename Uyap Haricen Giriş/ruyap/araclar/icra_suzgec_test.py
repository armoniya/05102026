# İcra Takip Süreçleri: başlık süzgeçleri (künye + tebligat) + borçlunun tebligat satırları — sahte uçlarla tarayıcı denemesi.
import json, sys, os, re, urllib.parse
from playwright.sync_api import sync_playwright

JS = sys.argv[1]
CIKTI = sys.argv[2]

def tk(x):
    x = (x or "").replace("İ", "i").replace("I", "ı").lower()
    for a, b in zip("ışğüöç", "isguoc"):
        x = x.replace(a, b)
    return x

def teb(i, tur, ptt, mazbata, eteb=False):
    return {"id": i, "tur": tur, "evrak_tarihi": "0%d/10/2026" % (i % 9 + 1), "barkod": "" if eteb else "40076635%05d" % i,
            "elektronik": eteb, "ptt_durumu": ptt, "son_islem_tarihi": "05/10/2026 14:30" if ptt else "",
            "teslim_sekli": "Muhataba bizzat teslim" if ptt == "TESLİM EDİLDİ" else ("Tanınmıyor" if ptt == "İADE" else ""),
            "mazbata_var": mazbata, "mazbata": "Tebliğ mazbatası" if mazbata else ""}

KES = {"kesinlesti": "Kesinleşti", "kesinlesme_bekliyor": "Kesinleşme Bekliyor", "itiraz": "İtiraz", "": ""}
BORCLULAR = [
    (1, "2026/101", "AHMET YILMAZ", "X BANKASI A.Ş.", "kesinlesti", "kesinlesti",
     [teb(1, "Ödeme Emri", "TESLİM EDİLDİ", True), teb(2, "103 Davetiyesi", "İADE", False), teb(3, "Kıymet Takdiri Raporu", "", False),
      teb(4, "Satış İlanı", "TESLİM EDİLDİ", True), teb(5, "Ödeme Emri", "İADE", False)]),
    (2, "2026/102", "IŞIL ÇİÇEK", "X BANKASI A.Ş.", "teblig", "kesinlesme_bekliyor",
     [teb(6, "Ödeme Emri", "TESLİM EDİLDİ", False)]),
    (3, "2026/103", "MEHMET DEMİR", "Y FİNANS A.Ş.", "iade", "",
     [teb(7, "Ödeme Emri", "İADE", True)]),
    (4, "2026/104", "ZEYNEP KAYA", "Y FİNANS A.Ş.", "takip_talebi", "", []),
    (5, "2026/105", "ÖRNEK İNŞAAT LTD. ŞTİ.", "X BANKASI A.Ş.", "teblig", "itiraz",
     [teb(8, "Ödeme Emri", "E-Tebligat: tebliğ edildi (süre 5 gün sonra başlar)", False, True)]),
    (None, "2025/77", "", "Y FİNANS A.Ş.", "", "", []),
]
# bag → (daire, dosya türü, açılış, açık mı)
KUNYE = {1: ("İzmir 5. İcra Dairesi", "İcra Dosyası", "2026-01-10", True), 2: ("İzmir 5. İcra Dairesi", "İcra Dosyası", "2026-02-10", True),
         3: ("Ankara 3. İcra Dairesi", "MTS", "2026-03-10", True), 4: ("Ankara 3. İcra Dairesi", "MTS", "2026-04-10", True),
         5: ("İzmir 5. İcra Dairesi", "İcra Dosyası", "2026-05-10", True), None: ("Ankara 3. İcra Dairesi", "MTS", "2025-03-15", False)}
son_sorgu = {}

def metin_uyar(f, deger):
    if f.startswith("="):
        return any(tk(x.strip()) == tk(deger.strip()) for x in f[1:].split("\n"))
    return not f.strip() or tk(f.strip()) in tk(deger)

def liste(q):
    f = {k: q.get(k, [""])[0] for k in ("q", "asama", "tur", "teblig", "mazbata", "kesinlesme", "durum", "birim", "alacakli", "borclu",
                                        "yil", "dosya_tur", "acilis_bas", "acilis_son")}
    tv, pv, mv = bool(f["tur"].strip()), bool(f["teblig"].strip()), bool(f["mazbata"])
    def tu(t, a, b, c):
        return ((not a or metin_uyar(f["tur"], t["tur"])) and (not b or metin_uyar(f["teblig"], t["ptt_durumu"]))
                and (not c or any(t["mazbata_var"] if x == "var" else not t["mazbata_var"] for x in f["mazbata"].split("\n"))))
    say = {"tur": {}, "teblig": {}, "mazbata": {}, "kesinlesme": {}, "birim": {}, "alacakli": {}, "borclu": {}, "yil": {}, "dosya_tur": {}}
    satirlar = []
    for bag, no, ad, alacakli, asama, kes, tebs in BORCLULAR:
        birim, dtur, acilis, acik = KUNYE[bag]
        yil = no.split("/")[0]
        if f["asama"] and asama != f["asama"]:
            continue
        if (f["durum"] == "acik" and not acik) or (f["durum"] == "kapali" and acik):
            continue
        if (f["acilis_bas"] and acilis < f["acilis_bas"]) or (f["acilis_son"] and acilis > f["acilis_son"]):
            continue
        yazi = lambda s: s and not s.startswith("=")
        def ku_(haric):
            u = lambda k, v: haric != k or yazi(v)
            return ((not u("birim", f["birim"]) or metin_uyar(f["birim"], birim))
                    and (not u("alacakli", f["alacakli"]) or metin_uyar(f["alacakli"], alacakli))
                    and (not u("borclu", f["borclu"]) or metin_uyar(f["borclu"], ad))
                    and (haric == "yil" or not f["yil"] or yil in f["yil"].split("\n"))
                    and (haric == "dosya_tur" or not f["dosya_tur"] or dtur in f["dosya_tur"].split("\n")))
        for k, v in (("birim", birim), ("alacakli", alacakli), ("borclu", ad), ("yil", yil), ("dosya_tur", dtur)):
            if v and ku_(k):
                say[k][v] = say[k].get(v, 0) + 1
        if not ku_(None):
            continue
        if f["q"] and not all(k in tk(no + " " + ad + " " + alacakli) for k in tk(f["q"]).split()):
            continue
        ku = (not f["kesinlesme"]) or any((x == "-" and not kes) or x == kes for x in f["kesinlesme"].split("\n"))
        if ku:
            for anahtar, deg in (("tur", {t["tur"] for t in tebs if tu(t, False, pv, mv)}),
                                 ("teblig", {t["ptt_durumu"] for t in tebs if tu(t, tv, False, mv)}),
                                 ("mazbata", {"var" if t["mazbata_var"] else "yok" for t in tebs if tu(t, tv, pv, False)})):
                for x in deg:
                    say[anahtar][x] = say[anahtar].get(x, 0) + 1
        tamam = not (tv or pv or mv) or any(tu(t, tv, pv, mv) for t in tebs)
        if tamam:
            say["kesinlesme"][kes or "-"] = say["kesinlesme"].get(kes or "-", 0) + 1
        if not (ku and tamam):
            continue
        satirlar.append({"bag": bag, "dosya": bag or 99, "birim": birim, "dosya_no": no, "dosya_durumu": "Açık" if acik else "Kapalı", "acik": acik,
                         "dosya_tur": dtur, "acilis_tarihi": acilis,
                         "borclu": ad, "alacakli": alacakli, "asama": asama, "asama_tarihi": "2026-10-05",
                         "kesinlesme": KES[kes], "kesinlesme_durumu": kes, "tebligat": "", "uyapta_gorulmedi": False,
                         "tebligatlar": [dict(t, uyan=(tv or pv or mv) and tu(t, True, True, True)) for t in tebs]})
    def sec(anahtar, etiket):
        return [{"deger": d, "etiket": etiket(d), "adet": n} for d, n in sorted(say[anahtar].items(), key=lambda x: (-x[1], x[0]))]
    return {"toplam": len(satirlar), "dosya_sayisi": len(satirlar), "sayfa": 1, "boyut": 50, "satirlar": satirlar, "secenek_siniri": 300, "secenekler": {
        "birim": sec("birim", str), "alacakli": sec("alacakli", str), "borclu": sec("borclu", str), "yil": sec("yil", str),
        "dosya_tur": sec("dosya_tur", str),
        "tur": sec("tur", lambda d: d or "(türü yok)"), "teblig": sec("teblig", lambda d: d or "Henüz sorgulanmadı"),
        "mazbata": sec("mazbata", lambda d: "Döndü" if d == "var" else "Dönmedi"),
        "kesinlesme": sec("kesinlesme", lambda d: "Henüz hesaplanmadı" if d == "-" else KES[d])}}

OZET = {"dosya": 5, "icra_dosya": 5, "acik_icra_dosya": 5, "tebligat": 8, "son_guncelleme": "2026-10-04 12:00", "borclu": 5,
        "birimler": [{"ad": "Ankara 3. İcra Dairesi", "birim_id": "2002", "acik": 2}, {"ad": "İzmir 5. İcra Dairesi", "birim_id": "5005", "acik": 3}],
        "asamalar": [{"kod": "takip_talebi", "etiket": "Takip Talebi", "adet": 1}, {"kod": "teblig", "etiket": "Tebliğ", "adet": 2},
                     {"kod": "iade", "etiket": "İade", "adet": 1}, {"kod": "kesinlesti", "etiket": "Kesinleşti", "adet": 1}]}
istekler = []
# UYAP'tan güncelleme sahte ucu: sıradaki GET yanıtları kuyruktan, POST'lar kaydedilir.
GT = {"sira": [], "son": None, "postlar": []}
def gt_tarama(**k):
    v = {"id": "t1", "durum": "calisiyor", "mesaj": "", "asama": "taraf", "kapsam": "Bütün açık dosyalar", "i": 0, "toplam": 0,
         "kalan_sn": None, "mola_kalan_sn": None, "alinan": 0, "yeni_dosya": 0, "taraf_cekilen": 0, "kapandi": 0,
         "uyari_sayisi": 0, "uyarilar": [], "rapor": [], "captcha": None}
    v.update(k)
    return v

def gt_karsila(route):
    u = urllib.parse.urlparse(route.request.url)
    yol = u.path.split("/__uyap_agent__/icra_guncelle")[1]
    if route.request.method == "POST":
        govde = json.loads(route.request.post_data or "{}")
        GT["postlar"].append((yol, govde))
        if yol == "":
            GT["son"] = gt_tarama(mod=govde.get("mod", "liste"), kapsam=govde.get("birim_adi") or "Bütün açık dosyalar", asama="liste", mesaj="Listeleniyor…")
        elif yol.endswith("/iptal"):
            GT["son"] = gt_tarama(mod=GT["son"].get("mod", "liste"), durum="iptal", asama="taraf", mesaj="Durduruldu.", rapor=["⚠️ Tarama durduruldu: 4/10 dosya veritabanına yazıldı."])
            GT["sira"] = []
        elif yol.endswith("/captcha"):
            GT["son"] = dict(GT["son"], durum="calisiyor", captcha=None)
    elif GT["sira"]:
        GT["son"] = GT["sira"].pop(0)
    route.fulfill(status=201 if route.request.method == "POST" and yol == "" else 200, content_type="application/json",
                  body=json.dumps({"tarama": GT["son"]}))

def karsila(route):
    u = urllib.parse.urlparse(route.request.url)
    uc = u.path.split("/__cu__/veri/")[1]
    q = urllib.parse.parse_qs(u.query, keep_blank_values=True)
    if uc == "ozet":
        veri = OZET
    elif uc == "borclular":
        son_sorgu.clear(); son_sorgu.update({k: v[0] for k, v in q.items()})
        istekler.append({k: v[0] for k, v in q.items() if v[0] and k not in ("sayfa", "boyut", "durum")})
        veri = liste(q)
    else:
        veri = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "veri": veri}))

SAYFA = """<!doctype html><html><head><meta charset=utf-8><style>body{margin:0;font-family:Segoe UI}</style></head>
<body><div id=av-sidebar-wrapper style="position:fixed;left:0;top:60px;width:220px;bottom:0"><div class=sidebar-top-part></div></div>
<div id=page-content-wrapper style="margin-left:220px;height:100vh"></div></body></html>"""

hatalar = []
def bekle(sayfa, n=450):
    sayfa.wait_for_timeout(n)

with sync_playwright() as p:
    b = p.chromium.launch()
    s = b.new_page(viewport={"width": 1500, "height": 900})
    s.on("pageerror", lambda e: hatalar.append(str(e)))
    # Doğrulama betiği testte bilerek engellenir (challenges.cloudflare.com); onun yüklenememe kaydı hata sayılmaz.
    s.on("console", lambda m: hatalar.append(m.text) if m.type == "error" and "challenges.cloudflare.com" not in (m.location or {}).get("url", "") else None)
    s.route("**/__cu__/veri/**", karsila)
    s.route("**/__uyap_agent__/icra_guncelle**", gt_karsila)
    s.route("https://challenges.cloudflare.com/**", lambda r: r.abort())
    s.route("http://sahte.test/", lambda r: r.fulfill(status=200, content_type="text/html", body=SAYFA))
    s.goto("http://sahte.test/")
    s.add_script_tag(path=JS)
    s.evaluate("window.__cuIcraTakipAc()")
    s.wait_for_selector("#cu-icra table.liste tbody.tik")
    say = lambda: s.locator("#cu-icra tbody.tik").count()
    kutu = lambda k: s.locator('#cu-icra .i-kutu-s[data-k="%s"] input' % k)

    # 1) Borçlunun tebligatları: ilk 3 + "+2 tebligat daha"; açınca 5
    assert say() == 5, say()
    ahmet = s.locator("#cu-icra tbody.tik").first
    assert ahmet.locator("tr").count() == 4, ahmet.locator("tr").count()
    assert "+2 tebligat daha" in ahmet.inner_text()
    ahmet.locator(".i-dahasi").click(); bekle(s, 100)
    ahmet = s.locator("#cu-icra tbody.tik").first
    assert ahmet.locator("tr").count() == 6 and "Daha az göster" in ahmet.inner_text()
    assert s.locator("#cu-icra .i-panel").count() == 0, "+N düğmesi dosya panelini açmamalı"
    assert "tebligat kaydı yok" in s.locator("#cu-icra tbody.tik").nth(3).inner_text()
    s.screenshot(path=os.path.join(CIKTI, "icra1_liste.png"))

    # 2) Kutuya tıklayınca adetli seçenekler
    kutu("tur").click(); bekle(s, 150)
    sec = s.locator("#cu-icra .i-acilir .sec").all_inner_texts()
    assert sec[0].split("\n") == ["Ödeme Emri", "4"], sec
    assert len(sec) == 4, sec
    s.screenshot(path=os.path.join(CIKTI, "icra2_acilir.png"))

    # 3) Yazarak süzme (içinde geçen, Türkçe-duyarsız) — odak ve yazı korunur
    kutu("tur").type("odeme", delay=40); bekle(s, 700)
    assert istekler[-1] == {"tur": "odeme"}, istekler[-1]
    assert say() == 4, say()
    assert kutu("tur").input_value() == "odeme"
    assert s.evaluate("document.activeElement === document.querySelector('#cu-icra .i-kutu-s[data-k=tur] input')"), "odak kaybolmamalı"
    assert s.locator("#cu-icra .i-acilir .sec").count() == 2, s.locator("#cu-icra .i-acilir .sec").all_inner_texts()  # Hepsi + Ödeme Emri
    assert "geçen" in s.locator("#cu-icra .i-secili").inner_text()

    # 4) Listeden seçme: tam eşleşme; uymayan tebligatlar soluk, uyanlar önde
    s.locator("#cu-icra .i-acilir .sec", has_text="Ödeme Emri").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"tur": "=Ödeme Emri"}, istekler[-1]
    assert s.locator("#cu-icra .i-acilir").count() == 1, "çoklu seçim: liste açık kalır"
    assert s.locator("#cu-icra .i-acilir .sec.on").all_inner_texts()[0].split("\n")[:2] == ["✓", "Ödeme Emri"]
    kutu("tur").press("Escape"); bekle(s, 200)
    assert kutu("tur").input_value() == "Ödeme Emri"
    assert s.locator("#cu-icra .i-acilir").count() == 0
    ahmet = s.locator("#cu-icra tbody.tik").first
    ilk = ahmet.locator("tr").first.locator("td.tb").first
    assert "Ödeme Emri" in ilk.inner_text() and "sol" not in (ilk.get_attribute("class") or "")
    assert ahmet.locator("td.tb.sol").count() > 0, "uymayan tebligatlar soluk olmalı"

    # 5) İkinci süzgeç: seçenekler diğerine göre daralır; aynı tebligatta birlikte
    kutu("teblig").click(); bekle(s, 150)
    sec = [x.split("\n")[0] for x in s.locator("#cu-icra .i-acilir .sec").all_inner_texts()]
    assert "Henüz sorgulanmadı" not in sec and "İADE" in sec, sec     # Ödeme Emri'lerde sorgulanmamış yok
    s.locator("#cu-icra .i-acilir .sec", has_text="İADE").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"tur": "=Ödeme Emri", "teblig": "=İADE"}, istekler[-1]
    assert say() == 2, say()                                           # Ahmet (5. tebligat) + Mehmet
    assert s.locator("#cu-icra .i-secili .et").count() == 2
    s.screenshot(path=os.path.join(CIKTI, "icra3_iki_suzgec.png"))

    # 6) Mazbata (kodlu): yazınca yalnız liste daralır, Enter ilk seçeneği seçer
    kutu("mazbata").click(); bekle(s, 150)
    kutu("mazbata").type("dön", delay=40); bekle(s, 500)
    assert "mazbata" not in istekler[-1], "kodlu kutuda yazı sunucuya gitmez"
    kutu("mazbata").press("ArrowDown"); kutu("mazbata").press("Enter"); bekle(s)
    assert istekler[-1].get("mazbata") in ("var", "yok"), istekler[-1]
    secilen = istekler[-1]["mazbata"]
    kutu("mazbata").press("Escape"); bekle(s, 200)
    assert kutu("mazbata").input_value() == ("Döndü" if secilen == "var" else "Dönmedi")

    # 7) Etiketten kaldırma ve "Hepsini kaldır"
    s.locator("#cu-icra .i-secili .et", has_text="Mazbata").click(); bekle(s)
    assert "mazbata" not in istekler[-1]
    s.locator("#cu-icra .i-secili .i-dahasi").click(); bekle(s)
    assert istekler[-1] == {}, istekler[-1]
    assert say() == 5 and s.locator("#cu-icra .i-secili").count() == 0

    # 8) Kesinleşme + aşama şeridi birlikte; ✕ ile kutudan kaldırma; boş sonuçta başlıklar yerinde
    kutu("kesinlesme").click(); bekle(s, 150)
    s.locator("#cu-icra .i-acilir .sec", has_text="Henüz hesaplanmadı").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"kesinlesme": "-"} and say() == 2
    s.locator("#cu-icra .i-asama", has_text="Kesinleşti").click(); bekle(s)
    assert say() == 0 and s.locator("#cu-icra th.suz .i-kutu-s").count() == 7, "boş sonuçta da başlık kutuları durmalı"
    assert "uyan dosya yok" in s.locator("#cu-icra table.liste").inner_text()
    s.locator('#cu-icra .i-kutu-s[data-k="kesinlesme"] .sil').dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"asama": "kesinlesti"} and say() == 1

    # 9) Escape kutuyu kapatır, satıra tıklama dosya panelini açmaya çalışır (uç boş döner → hata vermez)
    kutu("tur").click(); bekle(s, 150)
    kutu("tur").press("Escape"); bekle(s, 150)
    assert s.locator("#cu-icra .i-acilir").count() == 0

    s.locator("#cu-icra .i-secili .et", has_text="Aşama").click(); bekle(s)
    assert istekler[-1] == {} and say() == 5

    # 10) Künye süzgeçleri: varsayılan açık dosyalar; daire yazarak; alacaklı listeden; yıl ve tür (üst kutular)
    assert son_sorgu.get("durum") == "acik", son_sorgu
    assert s.locator("#cu-icra .i-suzgecler .i-kutu-s").count() == 2, "geniş ekranda üstte yıl + tür"
    kutu("birim").click(); bekle(s, 150)
    sec = [x.split("\n") for x in s.locator("#cu-icra .i-acilir .sec").all_inner_texts()]
    assert sec == [["İzmir 5. İcra Dairesi", "3"], ["Ankara 3. İcra Dairesi", "2"]], sec
    kutu("birim").type("ankara", delay=30); bekle(s, 700)
    assert istekler[-1] == {"birim": "ankara"} and say() == 2, (istekler[-1], say())
    kutu("birim").press("Escape"); bekle(s, 150)
    kutu("alacakli").click(); bekle(s, 150)
    s.locator("#cu-icra .i-acilir .sec", has_text="Y FİNANS").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"birim": "ankara", "alacakli": "=Y FİNANS A.Ş."} and say() == 2, istekler[-1]
    kutu("dosya_tur").click(); bekle(s, 150)
    assert [x.split("\n")[0] for x in s.locator("#cu-icra .i-acilir .sec").all_inner_texts()] == ["MTS"]
    s.locator("#cu-icra .i-acilir .sec", has_text="MTS").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1]["dosya_tur"] == "MTS" and kutu("dosya_tur").get_attribute("placeholder") == "MTS"
    # Kapalı dosyalar: borçlusuz dosya tek satır
    s.select_option("#cu-icra select[aria-label='Dosya durumu']", "kapali"); bekle(s)
    assert son_sorgu.get("durum") == "kapali" and say() == 1, (son_sorgu, say())
    satir = s.locator("#cu-icra tbody.tik").first.inner_text()
    assert "Borçlu kaydı yok" in satir and "henüz taranmadı" in satir and "açılış 15.03.2025" in satir, satir
    s.screenshot(path=os.path.join(CIKTI, "icra5_kunye.png"))
    # Açılış tarihi aralığı + etiket
    s.fill("#cu-icra input[aria-label='Açılış tarihi: başlangıç']", "2025-03-16"); bekle(s)
    assert istekler[-1]["acilis_bas"] == "2025-03-16" and say() == 0
    assert "16.03.2025" in s.locator("#cu-icra .i-secili").inner_text()
    s.locator("#cu-icra .i-secili .i-dahasi").click(); bekle(s)
    assert istekler[-1] == {} and son_sorgu.get("durum") == "kapali", "Hepsini kaldır durum seçimine dokunmaz"
    s.select_option("#cu-icra select[aria-label='Dosya durumu']", "acik"); bekle(s)
    assert say() == 5
    # Birden çok seçim: iki daire birlikte; etiket ikisini de yazar; biri bırakılınca kalan
    kutu("birim").click(); bekle(s, 150)
    s.locator("#cu-icra .i-acilir .sec", has_text="İzmir 5").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"birim": "=İzmir 5. İcra Dairesi"} and say() == 3, (istekler[-1], say())
    s.locator("#cu-icra .i-acilir .sec", has_text="Ankara 3").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"birim": "=İzmir 5. İcra Dairesi\nAnkara 3. İcra Dairesi"} and say() == 5, istekler[-1]
    assert s.locator("#cu-icra .i-acilir .sec.on").count() == 2
    assert kutu("birim").input_value() == "" and kutu("birim").get_attribute("placeholder") == "İzmir 5. İcra Dairesi +1"
    et = s.locator("#cu-icra .i-secili").inner_text()
    assert "İzmir 5. İcra Dairesi, Ankara 3. İcra Dairesi" in et, et
    s.screenshot(path=os.path.join(CIKTI, "icra6_coklu.png"))
    kutu("birim").type("izm", delay=30); bekle(s, 600)
    assert istekler[-1]["birim"].count("\n") == 1, "seçim varken yazı yalnız listeyi daraltır"
    assert [x.split("\n")[1] for x in s.locator("#cu-icra .i-acilir .sec.on").all_inner_texts()] == ["İzmir 5. İcra Dairesi"]
    s.locator("#cu-icra .i-acilir .sec.on", has_text="İzmir 5").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"birim": "=Ankara 3. İcra Dairesi"} and say() == 2, istekler[-1]
    kutu("birim").press("Escape"); bekle(s, 200)
    kutu("yil").click(); bekle(s, 150)
    s.locator("#cu-icra .i-acilir .sec", has_text="2026").dispatch_event("mousedown"); bekle(s)
    assert istekler[-1] == {"birim": "=Ankara 3. İcra Dairesi", "yil": "2026"}, istekler[-1]
    kutu("yil").press("Escape"); bekle(s, 200)
    s.locator("#cu-icra .i-secili .i-dahasi").click(); bekle(s)
    assert istekler[-1] == {} and say() == 5

    # 11) UYAP'tan güncelleme: panel, başlatma, ilerleme, doğrulama, bitiş raporu, durdurma
    gtd = s.locator("#cu-icra .i-gt-dugme")
    assert gtd.inner_text() == "UYAP'tan güncelle" and s.locator("#cu-icra .i-gt").count() == 0
    gtd.click(); bekle(s, 300)
    panel = s.locator("#cu-icra .i-gt")
    assert panel.count() == 1
    secenek = panel.locator("select option").all_inner_texts()
    assert secenek == ["Bütün açık dosyalar", "Ankara 3. İcra Dairesi (2 açık)", "İzmir 5. İcra Dairesi (3 açık)"], secenek
    GT["sira"] = [gt_tarama(asama="taraf", i=3, toplam=10, kalan_sn=125, alinan=6515, yeni_dosya=10, mesaj="Taraflar alınıyor: X 2026/1 (3/10)")]
    liste_once = len(istekler)
    panel.locator("button", has_text="Dosya listesini güncelle").click(); bekle(s, 2600)
    assert GT["postlar"][-1] == ("", {"mod": "liste"}), GT["postlar"]
    panel = s.locator("#cu-icra .i-gt")
    metin = panel.inner_text()
    assert "Dosya listesi güncelleniyor" in metin and "Taraflar ve borçlu kimlikleri alınıyor" in metin and "3 / 10" in metin and "≈ 2 dk" in metin and "6515 dosya alındı" in metin, metin
    assert s.locator("#cu-icra .i-gt-dugme").inner_text() == "Güncelleniyor · 30%"
    assert len(istekler) > liste_once, "tarama sürerken yazılan dosyalar listede görünsün diye liste yenilenir"
    assert panel.locator("button", has_text="Kapat").count() == 0, "sürerken panel kapatılamaz"
    s.screenshot(path=os.path.join(CIKTI, "icra7_guncelleme.png"))
    # Doğrulama istenir: kutu çizilir, betik yüklenemezse bilgi; "Tekrar dene" jetonsuz gönderir
    GT["sira"] = [gt_tarama(durum="captcha", asama="taraf", i=4, toplam=10, captcha={"n": 1, "site_key": "x", "mesaj": "UYAP doğrulama istiyor."})]
    bekle(s, 2600)
    panel = s.locator("#cu-icra .i-gt")
    assert "UYAP doğrulama istiyor." in panel.inner_text() and panel.get_attribute("data-captcha") == "1", panel.inner_text()
    GT["sira"] = [gt_tarama(durum="captcha", asama="taraf", i=4, toplam=10, captcha={"n": 1, "site_key": "x", "mesaj": "UYAP doğrulama istiyor."})]
    bekle(s, 2600)
    assert s.locator("#cu-icra .i-gt").get_attribute("data-captcha") == "1", "doğrulama açıkken panel yeniden çizilmez"
    GT["sira"] = [gt_tarama(asama="kapanan", i=1, toplam=2)]
    s.locator("#cu-icra .i-gt button", has_text="Tekrar dene").click(); bekle(s, 600)
    assert GT["postlar"][-1] == ("/t1/captcha", {"token": None}), GT["postlar"][-1]
    # Bitiş: rapor gösterilir, liste yenilenir
    GT["sira"] = [gt_tarama(durum="bitti", asama="bitti", mesaj="Tamamlandı: 6515 açık dosya güncellendi (10 yeni, 3 kapandı).",
                            rapor=["✔ İcra (Bütün açık dosyalar): UYAP 6515 dosya bildirdi, 6515 dosyanın tamamı alındı.", "Durum hesabı: 12 borçlunun durumu değişti, 2 otomatik iş açıldı."],
                            uyari_sayisi=1, uyarilar=["✔ İzmir 5. İcra Dairesi 2026/9 kapalı olarak güncellendi (Kapalı)."])]
    bekle(s, 2600)
    panel = s.locator("#cu-icra .i-gt")
    metin = panel.inner_text()
    assert "Son dosya listesi güncellemesi tamamlandı" in metin and "6515 dosyanın tamamı alındı" in metin and "1 uyarı / not" in metin, metin
    assert s.locator("#cu-icra .i-gt-dugme").inner_text() == "UYAP'tan güncelle"
    assert len(istekler) > liste_once, "bitince liste yenilenir"
    s.screenshot(path=os.path.join(CIKTI, "icra8_guncelleme_bitti.png"))
    # Tek daire + tarih aralığı ile taraf bilgileri, sonra durdur
    panel.locator("select").select_option("2002")
    panel.locator("input[aria-label='Açılış tarihi: başlangıç']").fill("2026-11-01")
    panel.locator("input[aria-label='Açılış tarihi: bitiş']").fill("2026-11-30")
    GT["sira"] = [gt_tarama(mod="taraf", asama="liste", kapsam="Ankara 3. İcra Dairesi", mesaj="2 dosya alındı")]
    panel.locator("button", has_text="Taraf bilgilerini güncelle").click(); bekle(s, 600)
    assert GT["postlar"][-1] == ("", {"mod": "taraf", "birim_id": "2002", "birim_adi": "Ankara 3. İcra Dairesi",
                                       "acilis_bas": "2026-11-01", "acilis_son": "2026-11-30"}), GT["postlar"][-1]
    assert "Taraf bilgileri güncelleniyor" in s.locator("#cu-icra .i-gt").inner_text()
    s.locator("#cu-icra .i-gt button", has_text="Durdur").click(); bekle(s, 600)
    assert GT["postlar"][-1][0] == "/t1/iptal"
    metin = s.locator("#cu-icra .i-gt").inner_text()
    assert "Son taraf güncellemesi durduruldu" in metin, metin
    # Kapsam seçimi yeniden çizimde korunur; "Bu ay" bu ayın ilk gününü yazar
    assert s.locator("#cu-icra .i-gt select").input_value() == "2002"
    assert s.locator("#cu-icra .i-gt input[aria-label='Açılış tarihi: bitiş']").input_value() == "2026-11-30"
    s.locator("#cu-icra .i-gt button", has_text="Bu ay").click()
    assert s.locator("#cu-icra .i-gt input[aria-label='Açılış tarihi: başlangıç']").input_value().endswith("-01")
    s.screenshot(path=os.path.join(CIKTI, "icra9_guncelleme_form.png"))
    s.locator("#cu-icra .i-gt button", has_text="Kapat").click(); bekle(s, 200)
    assert s.locator("#cu-icra .i-gt").count() == 0

    # 12) Dar ekran: kutular tablonun üstünde
    s.evaluate("window.__cuDar = function() { return true; }; document.documentElement.classList.add('cu-dar')")
    s.set_viewport_size({"width": 420, "height": 860})
    s.locator("#cu-icra .i-asama", has_text="Kesinleşti").click(); bekle(s)
    assert s.locator("#cu-icra .i-suzgecler .i-kutu-s").count() == 9 and s.locator("#cu-icra th.suz .i-kutu-s").count() == 0
    s.locator('#cu-icra .i-suzgecler .i-kutu-s[data-k="tur"] input').click(); bekle(s, 150)
    assert s.locator("#cu-icra .i-acilir .sec").count() >= 1
    kutu_r = s.locator("#cu-icra .i-acilir").bounding_box()
    assert kutu_r["x"] >= 0 and kutu_r["x"] + kutu_r["width"] <= 421, kutu_r
    s.screenshot(path=os.path.join(CIKTI, "icra4_dar.png"))
    b.close()

assert not hatalar, hatalar
print("TAMAM — 12 adım geçti (çoklu seçim ve UYAP'tan güncelleme dahil);", len(istekler), "liste isteği")
