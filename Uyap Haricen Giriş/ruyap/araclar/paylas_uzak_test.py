# -*- coding: utf-8 -*-
"""paylas.js — uzak cihaz (cokluuyap.com) paylaşım testi (19.11.2026).

Sahte UDF okuyucusu (UYAP'ın "İndir" ve "PDF Olarak İndir" düğmeleri) üzerinde:
  1) Masaüstü + yerel yardımcı açık  → belge 127.0.0.1:8801'e gider, "Kopyalandı" kutusu; ofis ucu çağrılmaz.
  2) Masaüstü + yardımcı yok         → belge indirilir; tarayıcının paylaşım penceresi açılmaz.
  3) Telefon                          → cihazın paylaş ekranı; UDF yerine PDF paylaşılır.
Kullanım: PYTHONUTF8=1 python araclar/paylas_uzak_test.py uyap_core/enjekte/paylas.js
"""
import base64
import sys

from playwright.sync_api import sync_playwright

JS = sys.argv[1] if len(sys.argv) > 1 else "uyap_core/enjekte/paylas.js"

SAYFA = """<!doctype html><html><head><meta charset=utf-8></head><body>
<div class="ude-document-viewer-toolbar"><span>Ödeme Emri</span><div></div>
  <button title="İndir" id="u-indir">i</button><button title="PDF Olarak İndir" id="u-pdf">p</button></div>
<script>
  document.getElementById('u-indir').onclick = function() {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob(['PK-udf'], {type: 'application/octet-stream'}));
    a.download = 'evrak.udf'; a.click();
  };
  document.getElementById('u-pdf').onclick = async function() {
    const h = await window.showSaveFilePicker({suggestedName: 'evrak.pdf'});
    const w = await h.createWritable(); await w.write(new Blob(['%PDF-1.4'])); await w.close();
  };
</script></body></html>"""

# paylas.js yüklenmeden önce: dosya seçici (başsız Chromium'da yok) ve paylaş ekranı kaydı.
ON_BETIK = """
window.showSaveFilePicker = async function(o) { return { name: o.suggestedName, createWritable: async () => ({
  write: async () => {}, close: async () => {} }) }; };
window.__paylasilan = null; window.__bildirim = [];
window.__uyapBildirim = function(m) { window.__bildirim.push(m); };
if (window.__MOBIL__) {
  navigator.share = async function(d) { window.__paylasilan = d.files.map(f => f.name); };
  navigator.canShare = function(d) { return d.files.every(f => !/\\.udf$/i.test(f.name)); };
} else {
  navigator.share = async function() { window.__paylasilan = 'TARAYICI-PENCERESI'; };
  navigator.canShare = function() { return true; };
}
"""

hatalar = []


def kur(tarayici, mobil, yardimci):
    baglam = tarayici.new_context(**({"user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148",
                                      "has_touch": True, "viewport": {"width": 390, "height": 844}} if mobil else {}),
                                  accept_downloads=True)
    s = baglam.new_page()
    s.on("pageerror", lambda e: hatalar.append(str(e)))
    istekler = {"yardimci": [], "ofis": []}

    def yardimci_karsila(route):
        istekler["yardimci"].append(route.request)
        if not yardimci:
            route.abort("connectionrefused")
            return
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers={"Access-Control-Allow-Origin": "http://sahte.test",
                                               "Access-Control-Allow-Headers": "X-Cu-Ad", "Access-Control-Allow-Methods": "POST",
                                               "Access-Control-Allow-Private-Network": "true"})
        else:
            route.fulfill(status=200, body="pano", headers={"Access-Control-Allow-Origin": "http://sahte.test"})

    s.route("http://127.0.0.1:8801/**", yardimci_karsila)
    s.route("**/__uyap_yerel__/**", lambda r: (istekler["ofis"].append(r.request), r.fulfill(status=500, body="olmamalı")))
    s.route("http://sahte.test/", lambda r: r.fulfill(status=200, content_type="text/html", body=SAYFA))
    s.add_init_script(("window.__MOBIL__ = true;" if mobil else "") + ON_BETIK)
    s.goto("http://sahte.test/")
    s.add_script_tag(path=JS)
    s.wait_for_selector(".cu-bk button.cu-bk-paylas")
    return baglam, s, istekler


def udf_paylas(s):
    return s.locator(".cu-bk-orj button.cu-bk-paylas")


with sync_playwright() as p:
    t = p.chromium.launch()

    # 1) Masaüstü + yardımcı
    b, s, ist = kur(t, mobil=False, yardimci=True)
    udf_paylas(s).click(); s.wait_for_timeout(800)
    post = [r for r in ist["yardimci"] if r.method == "POST"]
    assert len(post) == 1, ist["yardimci"]
    assert base64.b64decode(post[0].headers["x-cu-ad"]).decode("utf-8").endswith(".udf"), post[0].headers
    assert s.locator("#cu-pano-uyari").count() == 1 and "Kopyalandı" in s.locator("#cu-pano-uyari").inner_text()
    assert not ist["ofis"], "uzak cihazda ofis ucu çağrılmaz"
    assert s.evaluate("window.__paylasilan") is None, "tarayıcı paylaşım penceresi açılmaz"
    b.close()

    # 2) Masaüstü, yardımcı yok → indirme
    b, s, ist = kur(t, mobil=False, yardimci=False)
    with s.expect_download() as indirme:
        udf_paylas(s).click()
    assert indirme.value.suggested_filename.endswith(".udf"), indirme.value.suggested_filename
    s.wait_for_timeout(300)
    assert "İndirildi" in udf_paylas(s).inner_text(), udf_paylas(s).inner_text()
    assert any("Çoklu UYAP programı açık olsaydı" in m for m in s.evaluate("window.__bildirim"))
    assert s.evaluate("window.__paylasilan") is None, "tarayıcı paylaşım penceresi açılmaz"
    b.close()

    # 3) Telefon → paylaş ekranı, UDF yerine PDF
    b, s, ist = kur(t, mobil=True, yardimci=True)
    udf_paylas(s).click(); s.wait_for_timeout(800)
    paylasilan = s.evaluate("window.__paylasilan")
    assert paylasilan and all(a.endswith(".pdf") for a in paylasilan), paylasilan
    assert not [r for r in ist["yardimci"] if r.method == "POST"], "telefonda yardımcı denenmez"
    b.close()
    t.close()

assert not hatalar, hatalar
print("TAMAM — uzak paylaşım: yardımcı → pano, yardımcı yok → indir, telefon → paylaş ekranı (PDF)")
