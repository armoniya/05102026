# Sunucu üzerinden aktarma (WS-relay) uçtan uca şifreleme: gerçek webapp/js/tunnel.js + wire.js, sahte signaling
# ve sahte ofis (tarayıcıda, aynı wire.js kriptosuyla) — 20.11.2026.
# Sınananlar: relay_hello açık anahtar taşır; ofis anahtarıyla tünel şifreli açılır; istemcinin çerçevesi şifreli
# ("e", "b64" değil) ve çözülünce ping; ofisin şifreli pong'u kabul edilir; değiştirilmiş çerçeve tüneli kapatır;
# eski ofis (relay_ready'de pub yok) ile geçiş dönemi şifresiz aktarma çalışır.
# Çalıştırma: python araclar/relay_sifre_tarayici_test.py webapp/js
import json, os, sys
from playwright.sync_api import sync_playwright

JS = sys.argv[1]

SAYFA = """<!doctype html><html><head><meta charset=utf-8></head><body>
<script type=module>
import { relayAnahtarlari, RelayYon, Reassembler, encodeFrames } from "/__app__/wire.js";
const ESKI = new URLSearchParams(location.search).has("eski");
const b64 = (u) => { let s = ""; for (const x of u) s += String.fromCharCode(x); return btoa(s); };
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
window.__log = []; window.__durum = [];
window.__uyapStatus = (t) => window.__durum.push(t);
// P2P hiç kurulmaz: teklif gider, cevap gelmez → bekçi (12 sn) sunucu aktarmasına düşürür.
window.RTCPeerConnection = class { constructor() { this.connectionState = "new"; this.iceGatheringState = "new"; this.signalingState = "stable"; }
  createDataChannel() { return { readyState: "connecting", close() {} }; } addEventListener() {} getStats() { return Promise.resolve(new Map()); }
  createOffer() { return Promise.resolve({ type: "offer", sdp: "v=0" }); }
  setLocalDescription(d) { this.localDescription = d; this.iceGatheringState = "complete"; return Promise.resolve(); } close() {} };
class SahteWS {
  constructor() { this.readyState = 0; this.bufferedAmount = 0; window.__ws = this;
    setTimeout(() => { this.readyState = 1; this.onopen && this.onopen(); }, 10); }
  gonder(m) { setTimeout(() => this.onmessage && this.onmessage({ data: JSON.stringify(m) }), 0); }
  async send(txt) {
    const m = JSON.parse(txt); window.__log.push(m);
    if (m.role === "home") { this.gonder({ type: "joined", cid: "c1", peer_present: true }); this.gonder({ type: "start", cid: "c1" }); return; }
    if (m.type === "relay_hello") {
      if (ESKI || !m.pub) { this.sifresiz = true; this.gonder({ type: "relay_ready", cid: "c1" }); return; }
      const s = crypto.subtle, ecdh = { name: "ECDH", namedCurve: "P-256" };
      const cift = await s.generateKey(ecdh, false, ["deriveBits"]);
      const opub = new Uint8Array(await s.exportKey("raw", cift.publicKey));
      const ipub = unb64(m.pub);
      const karsi = await s.importKey("raw", ipub, ecdh, false, []);
      const sir = new Uint8Array(await s.deriveBits({ name: "ECDH", public: karsi }, cift.privateKey, 256));
      const [c2o, o2c] = await relayAnahtarlari(sir, ipub, opub);
      this.al = await RelayYon.yeni(c2o); this.ver = await RelayYon.yeni(o2c); this.reasm = new Reassembler();
      this.gonder({ type: "relay_ready", cid: "c1", pub: b64(opub), v: 1 });
      return;
    }
    if (m.type === "relay") {
      const ham = m.e ? await this.al.coz(unb64(m.e)) : unb64(m.b64);
      const rec = (this.reasm || (this.reasm = new Reassembler())).feed(ham.buffer);
      if (!rec) return;
      window.__log.push({ cozulen: rec.kind, sifreli: !!m.e });
      if (rec.kind === "ping") for (const f of encodeFrames(rec.id, "pong", {}, new Uint8Array(0))) {
        if (this.sifresiz) this.gonder({ type: "relay", cid: "c1", b64: b64(new Uint8Array(f)) });
        else this.gonder({ type: "relay", cid: "c1", e: b64(await this.ver.sifrele(new Uint8Array(f))) });
      }
    }
  }
  async bozukGonder() { const c = await this.ver.sifrele(new Uint8Array([1, 2, 3])); c[0] ^= 1; this.gonder({ type: "relay", cid: "c1", e: b64(c) }); }
  close() { this.readyState = 3; }
}
SahteWS.OPEN = 1;
window.WebSocket = SahteWS;
// Service Worker (UYAP içeriğini tünelleyen köprü) bu denemede gerekmez.
Object.defineProperty(navigator, "serviceWorker", { value: { register: async () => ({}), ready: Promise.resolve(), controller: {}, addEventListener() {} } });
await import("/__app__/tunnel.js");   // kendi kendini başlatır
</script></body></html>"""

def calistir(p, eski):
    b = p.chromium.launch()
    s = b.new_page()
    hatalar = []
    s.on("pageerror", lambda x: hatalar.append(str(x)))
    def dosya(route):
        ad = route.request.url.split("/__app__/")[1].split("?")[0]
        yol = os.path.join(JS, ad)
        if os.path.exists(yol):
            route.fulfill(status=200, content_type="application/javascript", body=open(yol, encoding="utf-8").read())
        else:
            route.fulfill(status=404, body="")
    s.route("https://sahte.test/__app__/**", dosya)
    s.route(lambda u: u.startswith("https://sahte.test/") and "/__app__/" not in u, lambda r: r.fulfill(status=200, content_type="text/html", body=SAYFA))
    konsol = []
    s.on("console", lambda m: konsol.append(m.text))
    s.goto("https://sahte.test/?user=deneme&pw=x" + ("&eski=1" if eski else ""))
    # P2P zaman aşımı (12 sn) → aktarma; ardından kalp atışı ping'i (15 sn)
    try:
        s.wait_for_function("() => window.__log.some(m => m.cozulen === 'ping')", timeout=60000)
    except Exception:
        print("GÜNLÜK:", json.dumps(s.evaluate("window.__log"), ensure_ascii=False)[:1500])
        print("DURUM:", s.evaluate("window.__durum"), "HATA:", hatalar, "KONSOL:", konsol[-8:])
        raise
    log = s.evaluate("window.__log")
    hello = next(m for m in log if m.get("type") == "relay_hello")
    cerceveler = [m for m in log if m.get("type") == "relay"]
    ping = next(m for m in log if m.get("cozulen") == "ping")
    if eski:
        assert any("şifresiz" in d for d in s.evaluate("window.__durum")), s.evaluate("window.__durum")
        assert all("b64" in m and "e" not in m for m in cerceveler) and not ping["sifreli"]
    else:
        assert len(hello.get("pub", "")) > 80, hello                    # 65 bayt ham P-256, base64
        assert any("uçtan uca şifreli" in d for d in s.evaluate("window.__durum")), s.evaluate("window.__durum")
        assert cerceveler and all("e" in m and "b64" not in m for m in cerceveler), cerceveler[:2]
        assert ping["sifreli"]
        # Şifreli pong kabul edildi mi: tünel açık kalır; değiştirilmiş çerçeve tüneli kapatır
        s.wait_for_timeout(300)
        assert not any("doğrulanamadı" in d for d in s.evaluate("window.__durum"))
        s.evaluate("window.__ws.bozukGonder()")
        s.wait_for_function("() => window.__durum.some(d => d.includes('doğrulanamadı'))", timeout=5000)
    assert not hatalar, hatalar
    b.close()

with sync_playwright() as p:
    calistir(p, eski=False)
    print("şifreli aktarma: geçti")
    calistir(p, eski=True)
    print("eski ofis (şifresiz, geçiş dönemi): geçti")
print("TAMAM")
