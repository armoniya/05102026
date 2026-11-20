# İcra Takip Süreçleri → Dosya Listesi sekmesi: sahte uçlarla tarayıcı denemesi (20.11.2026).
# Çalıştırma: python araclar/icra_dosya_listesi_test.py uyap_core/enjekte/icra.js <çıktı klasörü> [uyap_core/enjekte/tema.css]
import json, sys, os, urllib.parse
from playwright.sync_api import sync_playwright

JS = sys.argv[1]
CIKTI = sys.argv[2]
TEMA = sys.argv[3] if len(sys.argv) > 3 else None
os.makedirs(CIKTI, exist_ok=True)

DAIRELER = ["İstanbul 11. İcra Dairesi", "İstanbul 2. İcra Dairesi", "Çorum 1. İcra Dairesi", "Cizre 1. İcra Dairesi",
            "Ankara Banka Alacakları İcra Dairesi", "İstanbul Abonelik Sözleşmeleri İcra Dairesi", "Ankara 3. İcra Dairesi"]

def satir(i, birim, acik=True):
    return {"dosya": i, "birim": birim, "dosya_no": "2026/%d" % (100 + i), "dosya_tur": "İcra Dosyası",
            "durum": "Açık" if acik else "Kapalı (Haricen Tahsil - 12.03.2026)", "acik": acik,
            "acilis_tarihi": "2026-0%d-10" % (i % 9 + 1), "kapanis_tarihi": None if acik else "2026-03-12",
            "kapanis_sebebi": "" if acik else "Haricen Tahsil",
            "alacaklilar": ["X BANKASI A.Ş."] if i % 2 else ["Y A.Ş.", "Z A.Ş."], "borclular": ["AHMET YILMAZ", "MEHMET YILMAZ", "ALİ KAYA"][: 1 + i % 3],
            "rol": ["alacakli", "borclu", "diger_alacakli", "ucuncu", ""][i % 5],
            "rol_etiket": ["Alacaklı vekili", "Borçlu vekili", "Diğer haciz/rehin alacaklısı vekili", "3. kişi vekili", ""][i % 5],
            "rol_taraflar": [] if i % 5 == 4 else (["X BANKASI A.Ş."] if i % 5 == 0 else ["AHMET YILMAZ", "MEHMET YILMAZ"] if i % 5 == 1 else ["Z A.Ş."]),
            "takip_turu": "İlamsız", "takip_yolu": "Genel Haciz Yoluyla", "takip_sekli": "Örnek 7"}

SORGULAR, POSTLAR, TAHMINLER = [], [], []

def karsila(route):
    u = urllib.parse.urlparse(route.request.url)
    uc = u.path.split("/__cu__/veri/")[1]
    q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    if uc == "dosyalar":
        SORGULAR.append(q)
        durum = q.get("durum", "")
        satirlar = [satir(i, b, acik=(durum != "kapali")) for i, b in enumerate(DAIRELER, 1)]
        veri = {"toplam": len(satirlar), "sayfa": 1, "boyut": 100, "satirlar": satirlar, "secenek_siniri": 300, "avukat_bilinmiyor": False, "genel_toplam": 5312,
                "secenekler": {"birim": [{"deger": b, "etiket": b, "adet": 1} for b in DAIRELER],
                               "rol": [{"deger": "alacakli", "etiket": "Alacaklı vekili", "adet": 2}, {"deger": "borclu", "etiket": "Borçlu vekili", "adet": 2}],
                               "alacakli": [], "borclu": [], "dosya_no": [], "takip_turu": [], "takip_yolu": [], "takip_sekli": []}}
    elif uc == "dosyalar_kimlikleri":
        TAHMINLER.append(("kimlik", q.get("mod")))
        veri = [1, 2, 3] if q.get("mod") != "takip" else []
    elif uc == "dosyalar_istek":
        TAHMINLER.append(("istek", q.get("mod")))
        hedef = 0 if q.get("mod") == "takip" else 3
        veri = {"mod": q.get("mod"), "dosya": 7, "hedef": hedef, "takip": 1, "taraf_tum": 1, "taraf_kimliksiz": 2, "sayfa": 3,
                "istek": 9 if hedef else 0, "sure_sn": 27, "en_az": q.get("mod") in ("liste", "hepsi"), "acik_toplam": 1200}
    elif uc == "ozet":
        veri = {"borclu": 0, "acik_icra_dosya": 0, "icra_dosya": 7, "asamalar": [], "birimler": []}
    else:
        veri = None
    route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "veri": veri}))

def gt_karsila(route):
    if route.request.method == "POST":
        POSTLAR.append(json.loads(route.request.post_data or "{}"))
        t = {"id": 1, "durum": "suruyor", "mod": POSTLAR[-1]["mod"], "asama": "taraf", "i": 0, "toplam": 3, "alinan": 0, "yeni_dosya": 0}
    else:
        t = {"id": 1, "durum": "bitti", "mod": POSTLAR[-1]["mod"] if POSTLAR else "liste", "mesaj": "Tamamlandı."}
    route.fulfill(status=200, content_type="application/json", body=json.dumps({"tarama": t}))

SAYFA = """<!doctype html><html lang=tr data-cu-tema=armonia><head><meta charset=utf-8></head>
<body style="margin:0"><div id=av-sidebar-wrapper style="position:fixed;left:0;top:60px;width:220px;bottom:0"><div class=sidebar-top-part></div></div>
<div id=page-content-wrapper style="margin-left:220px;height:100vh"></div></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    s = b.new_page(viewport={"width": 1500, "height": 900}, locale="tr-TR")
    hatalar = []
    s.on("pageerror", lambda x: hatalar.append(str(x)))
    s.route("**/__cu__/veri/**", karsila)
    s.route("**/__uyap_agent__/icra_guncelle**", gt_karsila)
    s.route("http://sahte.test/", lambda r: r.fulfill(status=200, content_type="text/html", body=SAYFA))
    s.goto("http://sahte.test/")
    if TEMA:
        s.add_style_tag(path=TEMA)
    s.add_script_tag(path=JS)
    s.evaluate("window.__cuIcraTakipAc()")
    s.wait_for_selector("#cu-icra table.dl tbody tr")

    # Varsayılan sekme Dosya Listesi, açık dosyalar; Kapanış sütunu yok
    assert s.inner_text("#cu-icra .i-sekmeler button.on") == "Dosya Listesi"
    assert SORGULAR[-1].get("durum") == "acik", SORGULAR[-1]
    basliklar = s.eval_on_selector_all("#cu-icra table.dl th", "x => x.map(t => t.innerText.trim())")
    assert basliklar[:5] == ["İcra Dairesi", "Dosya No", "Alacaklı", "Borçlu", "Rolüm"], basliklar
    assert "Kapanış" not in basliklar
    # Birden çok alacaklı/borçlu: ilki + "+N"
    assert s.locator("#cu-icra table.dl .fazla").count() > 0
    # Rolüm: tür + vekili olunan taraf
    assert s.locator("#cu-icra table.dl td.rol .rol-taraf").count() > 0
    assert "AHMET YILMAZ" in s.inner_text("#cu-icra table.dl td.rol")
    s.screenshot(path=os.path.join(CIKTI, "dl1_liste.png"))

    # İcra dairesi penceresi: alfabetik (Türkçe), Tümünü seç + gruplar
    s.click("#cu-icra table.dl th button[data-dl=birim]")
    s.wait_for_selector("#cu-icra .i-dlp")
    adlar = s.eval_on_selector_all("#cu-icra .i-dlp .liste label:not(.grup) .ad", "x => x.map(t => t.innerText)")
    assert adlar == ["Ankara 3. İcra Dairesi", "Ankara Banka Alacakları İcra Dairesi", "Cizre 1. İcra Dairesi", "Çorum 1. İcra Dairesi",
                     "İstanbul 2. İcra Dairesi", "İstanbul 11. İcra Dairesi", "İstanbul Abonelik Sözleşmeleri İcra Dairesi"], adlar
    gruplar = s.eval_on_selector_all("#cu-icra .i-dlp .liste label.grup .ad", "x => x.map(t => t.innerText)")
    assert gruplar == ["Tümünü seç", "Banka", "Abonelik", "Merkezi Takip (MTS)", "Gayrimenkul Satış"], gruplar
    assert s.evaluate("document.activeElement === document.querySelector('#cu-icra .i-dlp input[type=text]')"), "metin kutusu odakta"
    s.screenshot(path=os.path.join(CIKTI, "dl2_daire_penceresi.png"))

    # Tümünü seç → Banka'yı çıkar: süzgeç "!" + banka daireleri
    s.click("#cu-icra .i-dlp label.grup:has-text('Tümünü seç') input")
    s.wait_for_function("() => { const k = document.querySelector('#cu-icra .i-dlp label.grup input'); return k && k.checked; }")
    assert SORGULAR[-1]["birim"] == "!", SORGULAR[-1]
    s.click("#cu-icra .i-dlp label.grup:has-text('Banka') input")
    s.wait_for_function("() => document.querySelector('#cu-icra .i-dlp') !== null")
    s.wait_for_timeout(200)
    assert SORGULAR[-1]["birim"] == "!Ankara Banka Alacakları İcra Dairesi", SORGULAR[-1]
    assert not s.is_checked("#cu-icra .i-dlp label.grup:has-text('Banka') input")
    assert s.locator("#cu-icra table.dl th button[data-dl=birim].dolu").count() == 1, "süzgeç işareti"
    s.screenshot(path=os.path.join(CIKTI, "dl3_banka_haric.png"))
    # Temizle
    s.click("#cu-icra .i-dlp .alt-cubuk .i-dahasi")
    s.wait_for_timeout(200)
    assert "birim" not in SORGULAR[-1], SORGULAR[-1]

    # Yazarak süzme: tablo süzülür (bekleme sonrası), odak korunur
    if not s.locator("#cu-icra .i-dlp").count():
        s.click("#cu-icra table.dl th button[data-dl=birim]")
    s.fill("#cu-icra .i-dlp input[type=text]", "istanbul")
    s.wait_for_timeout(600)
    assert SORGULAR[-1].get("birim") == "istanbul", SORGULAR[-1]
    assert s.evaluate("document.activeElement === document.querySelector('#cu-icra .i-dlp input[type=text]')"), "yeniden çizimde odak korunur"
    gorunen = s.eval_on_selector_all("#cu-icra .i-dlp .liste label .ad", "x => x.map(t => t.innerText)")
    assert all("İstanbul" in g for g in gorunen) and len(gorunen) == 3, gorunen
    # Sıralama Z → A
    s.click("#cu-icra .i-dlp .sirala button:has-text('Z → A')")
    s.wait_for_timeout(200)
    assert (SORGULAR[-1].get("sirala"), SORGULAR[-1].get("yon")) == ("birim", "azalan"), SORGULAR[-1]
    # Dışarı tıklayınca kapanır
    s.click("#cu-icra .i-dl-ipucu span")
    s.wait_for_timeout(100)
    assert s.locator("#cu-icra .i-dlp").count() == 0

    # Rolüm: kodlu, metin kutusu yok
    s.click("#cu-icra table.dl th button[data-dl=rol]")
    assert s.locator("#cu-icra .i-dlp input[type=text]").count() == 0
    s.click("#cu-icra .i-dlp label:has-text('Borçlu vekili') input")
    s.wait_for_timeout(200)
    assert SORGULAR[-1].get("rol") == "borclu", SORGULAR[-1]
    s.keyboard.press("Escape")
    s.click("#cu-icra .i-dl-ipucu span")

    # Kapalı: Kapanış sütunu ve kapanış tarihi aralığı
    s.click("#cu-icra .i-seg button:has-text('Kapalı')")
    s.wait_for_selector("#cu-icra input[aria-label='Kapanış tarihi: başlangıç']")
    assert SORGULAR[-1].get("durum") == "kapali"
    s.fill("#cu-icra input[aria-label='Kapanış tarihi: başlangıç']", "2026-03-01")
    s.wait_for_timeout(300)
    assert SORGULAR[-1].get("kapanis_bas") == "2026-03-01", SORGULAR[-1]
    basliklar = s.eval_on_selector_all("#cu-icra table.dl th", "x => x.map(t => t.innerText.trim())")
    assert basliklar[-1] == "Kapanış", basliklar
    assert "12.03.2026" in s.inner_text("#cu-icra table.dl tbody")
    s.screenshot(path=os.path.join(CIKTI, "dl4_kapali.png"))
    # Açık'a dönünce kapanış aralığı gönderilmez
    s.click("#cu-icra .i-seg button:has-text('Açık')")
    s.wait_for_timeout(300)
    assert "kapanis_bas" not in SORGULAR[-1], SORGULAR[-1]

    # Dosya sayısı belirgin: listelenen + veritabanındaki toplam
    assert "7 dosya listeleniyor" in s.inner_text("#cu-icra .i-dl-sayi") and "5.312" in s.inner_text("#cu-icra .i-dl-sayi")
    dugmeler = s.eval_on_selector_all("#cu-icra .i-arac > button", "x => x.map(t => t.innerText)")
    assert dugmeler == ["Tüm verileri güncelle", "Dosya listesini güncelle", "Takip türlerini güncelle", "Tarafları güncelle"], dugmeler

    # Takip türleri: eksik yok → istek gitmez, yalnız "Kapat"
    s.click("#cu-icra .i-arac button:has-text('Takip türlerini güncelle')")
    s.wait_for_selector("#cu-icra .i-gt[aria-label='Takip türlerini güncelle']")
    assert "hiçbirinde eksik yok" in s.inner_text("#cu-icra .i-gt")
    assert s.locator("#cu-icra .i-gt button:has-text('Başlat')").count() == 0
    s.click("#cu-icra .i-gt button:has-text('Kapat')")
    assert not POSTLAR

    # Taraflar: önce tahmin (dosya ve istek sayısı), Başlat → yalnız eksiği olan dosyalar
    s.click("#cu-icra .i-arac button:has-text('Tarafları güncelle')")
    s.wait_for_selector("#cu-icra .i-gt[aria-label='Tarafları güncelle']")
    metin = s.inner_text("#cu-icra .i-gt")
    assert "7 dosyadan 3 dosyaya istek gidecek" in metin and "9 istek" in metin and "borçlu kimliği eksik: 2" in metin, metin
    assert not POSTLAR, "Başlat'a basmadan istek gitmez"
    s.screenshot(path=os.path.join(CIKTI, "dl5_tahmin.png"))
    s.click("#cu-icra .i-gt button:has-text('Başlat')")
    s.wait_for_timeout(300)
    assert POSTLAR and POSTLAR[-1] == {"mod": "taraf", "dosyalar": [1, 2, 3]}, POSTLAR
    assert ("kimlik", "taraf") in TAHMINLER, TAHMINLER
    s.wait_for_selector("#cu-icra .i-gt")
    s.screenshot(path=os.path.join(CIKTI, "dl6_guncelleme.png"))

    # Tüm veriler: önce tahmin ("en az"), Başlat → dosya listesi + hedefler (süzgeçteki eksik dosyalar)
    s.wait_for_function("() => !document.querySelector('#cu-icra .i-arac button').disabled")
    s.click("#cu-icra .i-arac button:has-text('Tüm verileri güncelle')")
    s.wait_for_selector("#cu-icra .i-gt[aria-label='Tüm verileri güncelle']")
    metin = s.inner_text("#cu-icra .i-gt[aria-label='Tüm verileri güncelle']")
    assert "en az 9 istek" in metin and "Yeni ve kapanan" in metin, metin
    s.click("#cu-icra .i-gt button:has-text('Başlat')")
    s.wait_for_timeout(300)
    assert POSTLAR[-1] == {"mod": "hepsi", "hedefler": [1, 2, 3]}, POSTLAR
    # Dosya listesi: süzgeç uygulanmaz, kimlik istenmez
    s.wait_for_function("() => !document.querySelector('#cu-icra .i-arac button').disabled")
    s.click("#cu-icra .i-arac button:has-text('Dosya listesini güncelle')")
    s.wait_for_selector("#cu-icra .i-gt[aria-label='Dosya listesini güncelle']")
    assert "süzgeç uygulanmaz" in s.inner_text("#cu-icra .i-gt[aria-label='Dosya listesini güncelle']")
    s.click("#cu-icra .i-gt button:has-text('Başlat')")
    s.wait_for_timeout(300)
    assert POSTLAR[-1] == {"mod": "liste"}, POSTLAR
    # Diğer sekmeler yerinde
    sekmeler = s.eval_on_selector_all("#cu-icra .i-sekmeler button", "x => x.map(t => t.innerText)")
    assert sekmeler == ["Dosya Listesi", "Süreçler", "Tebligatlar", "Kesinleşme Süreleri", "Resmî Tatiller"], sekmeler

    # Koyu tema
    s.evaluate("document.documentElement.setAttribute('data-cu-tema', 'koyu')")
    s.screenshot(path=os.path.join(CIKTI, "dl7_koyu.png"))

    assert not hatalar, hatalar
    print("TAMAM:", len(SORGULAR), "sorgu,", len(POSTLAR), "güncelleme isteği")
    b.close()
