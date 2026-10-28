"""
İş Listesi köprüsü (uyap_core/is_listesi.py)
--------------------------------------------
UYAP dosya kartındaki "İş Ata" düğmesi ve "İş Listesi" bağlantısı (uyap_proxy'nin enjekte ettiği
JS) bu yerel uçları çağırır; köprü, isteği KULLANICININ KENDİ CokluUyap hesabıyla cokluuyap.com
/is-listesi/api/* uçlarına iletir.

Neden yerel: /__uyap_agent__/* yolları tünelle OFİSE gider ve ofis ajanı isteği kimin yaptığını
bilmez. /__uyap_kisi__/* ise tünele HİÇ girmez; tarayıcının bağlı olduğu bilgisayardaki süreçte
(istemcide home_client, ofiste uyap_proxy) işlenir — kimlik o bilgisayarda giriş yapan kişidir.

    GET  /__uyap_kisi__/is-listesi/me                → {me, people, office}
    GET  /__uyap_kisi__/is-listesi/tasks?birim_id=…&dosya_no=…[&dosya_id=…]  → bu dosyaya bağlı işler
    GET  /__uyap_kisi__/is-listesi/tasks?filtre=…    → sağdaki iş paneli: benim | onay | tumu
    POST /__uyap_kisi__/is-listesi/tasks             {title, description, assignee_id, due_at, priority, dosya,
                                                      sablon, etiketler}  → süreç/aşama şablondan
    GET  /__uyap_kisi__/is-listesi/surecler          → hazır süreçler (aşamalarıyla)
    GET  /__uyap_kisi__/is-listesi/etiketler         → {hazir, buro, kisisel}
    POST /__uyap_kisi__/is-listesi/tasks/<id>/durum  {action: gonder|basla|tamamla|onayla|reddet, reason, sonuc?, sonraki?,
                                                      kesinlesme?: "YYYY-AA-GG"}
                                                     (sonuc verilince sunucu sonraki aşamanın işini, tebliğde
                                                      kesinleşme günü verildiyse o güne kesinleşme işini açar)
    POST /__uyap_kisi__/is-listesi/tasks/<id>/ek-sure {new_due, reason}  → "Ertesi güne aktar" (yönetici onaylar)
    POST /__uyap_kisi__/is-listesi/ek-sure/<id>/karar {approve, note}  → aktarma talebini kabul / ret
    GET  /__uyap_kisi__/is-listesi/sablonlar         → {hazir, buro} iş şablonları
    POST /__uyap_kisi__/is-listesi/sablonlar         {title, description, due_days, priority, taraf, kontrol, sonuclar}
    POST /__uyap_kisi__/is-listesi/sablonlar/<id>    {title, description, due_days, priority, taraf}  → düzenle
    DELETE /__uyap_kisi__/is-listesi/sablonlar/<id>
    POST /__uyap_kisi__/is-listesi/tasks/<id>/hatirlatici {remind_at, note}  → kendine hatırlatıcı
    GET  /__uyap_kisi__/is-listesi/ac[?is=<id> | ?sekme=sablonlar]  → 303 → cokluuyap.com'da şifresiz oturum
                                                     (tek kullanımlık kod)

Kimlik uyap_app girişinde configure() ile verilir, çıkışta clear() ile silinir. Jeton bellekte
tutulur, süresi dolmadan yenilenir; sunucu 401 dönerse bir kez yeniden alınır.
"""

import re
import json
import time
import asyncio

import httpx

PREFIX = "/__uyap_kisi__/is-listesi"
_TIMEOUT = 20.0

# ── İş Listesi'nden UYAP dosya kartını açma ──────────────────────────────────────────
# Web arayüzü (cokluuyap.com) "UYAP'ta aç" ile 127.0.0.1:8800/__uyap_kisi__/dosya-ac#<kart JSON>
# açar. Yerel kapı çerezi SameSite=Strict olduğundan başka siteden gelen bu ilk istekte gönderilmez;
# bu yüzden sayfa yerel yetki İSTEMEDEN verilir ve hiçbir şey yapmaz/göstermez: kart verisini
# (# kısmı — sunucuya hiç gitmez) bu sekmenin sessionStorage'ına koyar ve UYAP ana sayfasına geçer.
# Bu geçişi 127.0.0.1'in kendisi başlattığı için çerez gönderilir; kartı UYAP'ın kendi arama
# bileşeni açar (uyap_proxy enjekte betiği, bkz. "İŞ LİSTESİ" bloğu → bekleyenDosyaAc).
DOSYA_AC_PATH = "/__uyap_kisi__/dosya-ac"
DOSYA_AC_SAYFASI = (
    "<!doctype html><html lang='tr'><head><meta charset='utf-8'><title>UYAP dosyası açılıyor…</title>"
    "<meta name='referrer' content='no-referrer'></head><body style='font:14px sans-serif;padding:24px'>"
    "UYAP dosyası açılıyor…<script>(function(){try{var h=decodeURIComponent((location.hash||'').slice(1));"
    "var d=JSON.parse(h);if(d&&typeof d==='object'&&d.dosyaId){sessionStorage.setItem('cokluuyapDosyaAc',"
    "JSON.stringify({t:Date.now(),dosya:d}));}}catch(e){}location.replace('/');})();</script></body></html>"
).encode("utf-8")

# ── İş Listesi'nden PTT sorgusu ──────────────────────────────────────────────────────
# Web arayüzündeki "PTT sorgula" 127.0.0.1:8800/__uyap_kisi__/ptt#<barkod> açar. Sayfa yerel yetki
# istemeden verilir (çerez çapraz-site gezinmede gelmez); sorguyu kullanıcı "Sorgula"ya basınca
# kendisi (aynı köken → çerez gider) /__uyap_agent__/ptt_sorgu ile yapar — UYAP içindeki "PTT barkod
# sorgula" ile aynı uç. Kendiliğinden sorgulamaz: başka bir site bu adresi açtırıp ofiste Chrome
# başlatamasın.
PTT_PATH = "/__uyap_kisi__/ptt"
PTT_SAYFASI = r"""<!doctype html><html lang='tr'><head><meta charset='utf-8'>
<meta name='referrer' content='no-referrer'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>PTT Gönderi Takip</title>
<style>
body{margin:0;background:#111827;color:#e5e7eb;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
main{max-width:560px;margin:0 auto;padding:20px 16px}
h1{font-size:17px;margin:0 0 12px}
.s{display:flex;gap:10px;margin-top:4px}.s .a{flex:none;width:120px;color:#9ca3af}.s .d{font-weight:600;word-break:break-word}
.h{margin-top:6px;padding:7px 10px;border-radius:8px;background:rgba(255,255,255,.06)}
.h .t{color:#9ca3af;font-size:12px}.h .x{color:#cbd5e1;font-size:12px}
.hata{color:#fca5a5;word-break:break-word}
button{margin:14px 8px 0 0;padding:7px 14px;border:0;border-radius:8px;background:#4f46e5;color:#fff;font:inherit;cursor:pointer}
button.ik{background:rgba(255,255,255,.15)}
</style></head><body><main><h1 id='bas'>PTT Gönderi Takip</h1><div id='govde'></div></main>
<script>
(function(){
  var barkod = decodeURIComponent((location.hash || '').slice(1)).replace(/\D/g, '');
  var govde = document.getElementById('govde');
  function el(t, c, m){ var e = document.createElement(t); if (c) e.className = c; if (m != null) e.textContent = m; return e; }
  function dugme(ad, ik, fn){ var b = el('button', ik ? 'ik' : null, ad); b.onclick = fn; govde.appendChild(b); }
  function satir(ad, deger, renk){
    if (!deger) return;
    var s = el('div', 's'); s.appendChild(el('div', 'a', ad));
    var d = el('div', 'd', deger); d.style.color = renk || '#f3f4f6'; s.appendChild(d); govde.appendChild(s);
  }
  function sorgula(){
    govde.replaceChildren(el('div', null, 'PTT sorgulanıyor… (ptt.gov.tr, 10–20 sn sürebilir)'));
    fetch('/__uyap_agent__/ptt_sorgu?barkod=' + encodeURIComponent(barkod), {cache: 'no-store', credentials: 'same-origin'})
      .then(function(r){ return r.json().catch(function(){ return {ok: false, error: r.status === 401 || r.status === 403
          ? 'Bu tarayıcıda CokluUyap girişi yok. CokluUyap penceresinden "Tarayıcıda aç" ile bir kez açıp yeniden deneyin.'
          : 'PTT sorgusu yanıt vermedi (HTTP ' + r.status + ').'}; }); })
      .catch(function(){ return {ok: false, error: 'CokluUyap\'a ulaşılamadı. Program bu bilgisayarda açık mı?'}; })
      .then(goster);
  }
  function goster(j){
    govde.replaceChildren();
    if (!j || !j.ok) {
      govde.appendChild(el('div', 'hata', (j && j.error) || 'PTT sorgusu başarısız.'));
      dugme('Yeniden dene', false, sorgula);
      return;
    }
    var d = (j.durum || '').toLocaleLowerCase('tr');
    satir('Durum', j.durum || '—', /teslim edildi/.test(d) ? '#86efac' : /iade/.test(d) ? '#fca5a5' : '#fde68a');
    satir(/teslim edildi/.test(d) ? 'Teslim tarihi' : 'Son işlem', j.tarih);
    satir(/iade/.test(d) ? 'İade nedeni' : 'Teslim şekli', j.teslim_sekli);
    if (j.kabul) { satir('Kabul tarihi', j.kabul.tarih); satir('Gönderici', j.kabul.gonderici); satir('Alıcı', j.kabul.alici); }
    if (j.hareketler && j.hareketler.length) {
      var b = el('div', null, 'Hareketler'); b.style.cssText = 'margin-top:14px;font-weight:600;color:#cbd5e1'; govde.appendChild(b);
      j.hareketler.slice().reverse().forEach(function(hr){      // en yeni üstte
        var s = el('div', 'h'); s.appendChild(el('div', 'd', hr.aciklama || '—'));
        s.appendChild(el('div', 't', [hr.tarih, hr.yer].filter(Boolean).join(' · ')));
        if (hr.detay) s.appendChild(el('div', 'x', hr.detay));
        govde.appendChild(s);
      });
    }
    dugme('Yeniden sorgula', true, sorgula);
    dugme('Barkodu kopyala', true, function(){ navigator.clipboard && navigator.clipboard.writeText(barkod); });
  }
  if (!/^\d{13}$/.test(barkod)) { govde.appendChild(el('div', 'hata', 'Geçersiz barkod.')); return; }
  document.getElementById('bas').textContent = 'PTT Gönderi Takip · ' + barkod;
  document.title = 'PTT · ' + barkod;
  // Sayfa yerel yetkisiz açılır (başka bir site de bu adrese yönlendirebilir); sorgu ofiste Chrome
  // açtırdığı için kendiliğinden başlamaz, kullanıcı düğmeye basar. Sayfa başka siteye gömülemez
  // (çapraz-site iframe'i yerel kapı reddeder) → tıklama da dışarıdan yaptırılamaz.
  govde.appendChild(el('div', null, 'Barkod ' + barkod + ' ptt.gov.tr\'de sorgulanacak.'));
  dugme('Sorgula', false, sorgula);
})();
</script></body></html>""".encode("utf-8")

_cfg ={"base": "", "office_code": "", "username": "", "password": ""}
_tok = {"value": "", "exp": 0.0}
# Kilit oluşturulduğu olay döngüsüne bağlanır; uyap_app paylaşım/alıcı için ayrı (ve yeniden
# başlatmada yeni) döngüler açtığından döngü değişince yenisi kurulur.
_lock = {"lock": None, "loop": None}


def _get_lock():
    loop = asyncio.get_running_loop()
    if _lock["loop"] is not loop:
        _lock.update(lock=asyncio.Lock(), loop=loop)
    return _lock["lock"]


def configure(api_base, office_code, username, password):
    """api_base: https://www.cokluuyap.com (uyap_app._api_base())."""
    _cfg.update(base=(api_base or "").rstrip("/"), office_code=office_code or "",
                username=username or "", password=password or "")
    _tok.update(value="", exp=0.0)


def clear():
    configure("", "", "", "")


def is_path(path) -> bool:
    p = "/" + (path or "").lstrip("/")
    return p == PREFIX or p.startswith(PREFIX + "/")


def _json(status, data):
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}, \
        json.dumps(data, ensure_ascii=False).encode("utf-8")


def _err(status, msg):
    return _json(status, {"ok": False, "error": msg})


async def _token(client, force=False):
    async with _get_lock():
        if not force and _tok["value"] and _tok["exp"] - 60 > time.time():
            return _tok["value"]
        r = await client.post(f"{_cfg['base']}/is-listesi/api/token", json={
            "office_code": _cfg["office_code"], "username": _cfg["username"], "password": _cfg["password"]})
        d = _safe_json(r)
        if r.status_code != 200 or not d.get("token"):
            raise _Fail(r.status_code if r.status_code >= 400 else 502, d.get("error") or "İş Listesi'ne giriş yapılamadı.")
        _tok.update(value=d["token"], exp=time.time() + int(d.get("expires_in") or 3600))
        return _tok["value"]


class _Fail(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


def _obj(body):
    """İstek gövdesi JSON nesnesi ise sözlük, değilse None."""
    try:
        d = json.loads(body or b"{}")
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _sablon_alanlari(d):
    return {k: d[k] for k in ("title", "description", "due_days", "priority", "taraf", "kontrol", "sonuclar",
                              "asama", "etiketler") if k in d}


def _safe_json(r):
    try:
        d = r.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


async def _call(method, api_path, query="", body=None):
    """Sunucu ucunu jetonla çağırır; 401'de jetonu bir kez yeniler. (status, dict)"""
    url = f"{_cfg['base']}/is-listesi/api/{api_path}" + (f"?{query}" if query else "")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in (0, 1):
            tok = await _token(client, force=attempt == 1)
            r = await client.request(method, url, json=body, headers={"Authorization": f"Bearer {tok}"})
            if r.status_code != 401 or attempt == 1:
                return r.status_code, _safe_json(r)
    return 502, {}


async def handle(method, path, query, body):
    """(status, headers, body) — uyap_proxy / home_client yanıta çevirir."""
    if not (_cfg["base"] and _cfg["username"] and _cfg["password"]):
        return _err(503, "CokluUyap'a giriş yapılmamış; İş Listesi kullanılamıyor.")
    sub = "/" + (path or "").lstrip("/")
    sub = sub[len(PREFIX):].strip("/")
    try:
        if sub == "me" and method == "GET":
            return _json(*await _call("GET", "me"))
        if sub == "tasks" and method == "GET":
            from urllib.parse import parse_qs, urlencode
            q = parse_qs(query or "")
            dosya_id = (q.get("dosya_id") or [""])[0][:200]
            birim = (q.get("birim_id") or [""])[0][:200]
            dosya_no = (q.get("dosya_no") or [""])[0][:200]
            if dosya_id or (birim and dosya_no):
                # Yönetici büronun tüm işlerini, kullanıcı kendisininkileri görür (sunucu süzer).
                # Kalıcı anahtar birim + dosya no (UYAP dosyaId'si oturumla değişir); dosya_id de
                # gönderilir: birim/no'yu tanımayan eski sunucu dosyaya göre süzmeye devam etsin.
                p = {"filtre": "tumu"}
                if dosya_id:
                    p["dosya_id"] = dosya_id
                if birim and dosya_no:
                    p.update(birim_id=birim, dosya_no=dosya_no)
                return _json(*await _call("GET", "tasks", urlencode(p)))
            # Dosyasız: sağdaki iş paneli (benim / onay / tümü — "tümü" yalnız yöneticide geniş).
            filtre = (q.get("filtre") or ["benim"])[0]
            if filtre not in ("benim", "onay", "tumu"):
                return _err(400, "Geçersiz süzgeç.")
            return _json(*await _call("GET", "tasks", urlencode({"filtre": filtre})))
        if sub == "tasks" and method == "POST":
            d = _obj(body)
            if d is None:
                return _err(400, "Geçersiz istek verisi.")
            keep = {k: d[k] for k in ("title", "description", "assignee_id", "due_at", "priority", "dosya", "sablon",
                                      "etiketler") if k in d}
            return _json(*await _call("POST", "tasks", body=keep))
        # İş şablonları (hazır + büro): İş Ata penceresi listeler, "Şablon olarak kaydet" ekler.
        if sub == "sablonlar" and method == "GET":
            return _json(*await _call("GET", "sablonlar"))
        # Süreçler (hazır, kodda) ve etiketler (hazır + büro + kişisel): İş Ata penceresi gösterir.
        if sub in ("surecler", "etiketler") and method == "GET":
            return _json(*await _call("GET", sub))
        if sub == "sablonlar" and method == "POST":
            d = _obj(body)
            if d is None:
                return _err(400, "Geçersiz istek verisi.")
            return _json(*await _call("POST", "sablonlar", body=_sablon_alanlari(d)))
        m = re.fullmatch(r"sablonlar/(\d{1,12})", sub)
        if m and method == "POST":
            d = _obj(body)
            if d is None:
                return _err(400, "Geçersiz istek verisi.")
            return _json(*await _call("POST", f"sablonlar/{m.group(1)}", body=_sablon_alanlari(d)))
        if m and method == "DELETE":
            return _json(*await _call("DELETE", f"sablonlar/{m.group(1)}"))
        # UYAP'tan çıkmadan işi ilerletme: onaya gönder (özel işte tamamla) / başla; yönetici ve onay
        # yetkilisi yan panelden onaylar ya da gerekçeyle reddeder (yetkiyi sunucu denetler).
        # Yeniden açma ve devir İş Listesi'nde kalır.
        m = re.fullmatch(r"tasks/(\d{1,12})/durum", sub)
        if m and method == "POST":
            d = _obj(body)
            if d is None or d.get("action") not in ("gonder", "basla", "tamamla", "onayla", "reddet"):
                return _err(400, "Geçersiz işlem.")
            body = {"action": d["action"], "reason": str(d.get("reason") or "")[:2000]}
            # Kontrol sonucu + sonraki iş: sunucu süreçte sıradaki aşamanın işini açar.
            if d.get("sonuc") in ("olumlu", "olumsuz", "bekliyor"):
                body["sonuc"] = d["sonuc"]
                s = d.get("sonraki")
                if s is False:
                    body["sonraki"] = False
                elif isinstance(s, dict):
                    body["sonraki"] = {"baslik": str(s.get("baslik") or "")[:200], "gun": s.get("gun")}
                # Tebliğ edildiyse kullanıcının girdiği kesinleşme günü: sunucu o güne kesinleşme işi açar.
                k = d.get("kesinlesme")
                if isinstance(k, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", k):
                    body["kesinlesme"] = k
            return _json(*await _call("POST", f"tasks/{m.group(1)}/durum", body=body))
        # "Ertesi güne aktar": son tarihi ileri alma talebi (gerekçe zorunlu); yönetici / onay yetkilisi
        # yan panelden ya da İş Listesi'nde kabul eder veya reddeder (ek-sure/<id>/karar).
        m = re.fullmatch(r"tasks/(\d{1,12})/ek-sure", sub)
        if m and method == "POST":
            d = _obj(body)
            if d is None or not isinstance(d.get("new_due"), int) or isinstance(d.get("new_due"), bool):
                return _err(400, "Geçersiz istek verisi.")
            return _json(*await _call("POST", f"tasks/{m.group(1)}/ek-sure",
                                      body={"new_due": d["new_due"], "reason": str(d.get("reason") or "")[:2000]}))
        # Hatırlatıcı: UYAP penceresinden yalnız kendine (başkasına kurma İş Listesi'nde; kuralı sunucu denetler).
        m = re.fullmatch(r"tasks/(\d{1,12})/hatirlatici", sub)
        if m and method == "POST":
            d = _obj(body)
            if d is None or not isinstance(d.get("remind_at"), int) or isinstance(d.get("remind_at"), bool):
                return _err(400, "Geçersiz istek verisi.")
            return _json(*await _call("POST", f"tasks/{m.group(1)}/hatirlatici",
                                      body={"remind_at": d["remind_at"], "note": str(d.get("note") or "")[:500]}))
        m = re.fullmatch(r"ek-sure/(\d{1,12})/karar", sub)
        if m and method == "POST":
            d = _obj(body)
            if d is None or not isinstance(d.get("approve"), bool):
                return _err(400, "Geçersiz istek verisi.")
            return _json(*await _call("POST", f"ek-sure/{m.group(1)}/karar",
                                      body={"approve": d["approve"], "note": str(d.get("note") or "")[:2000]}))
        if sub == "ac" and method == "GET":
            status, d = await _call("POST", "sso-kod", body={})
            if status != 200 or not d.get("url"):
                return _err(status if status >= 400 else 502, d.get("error") or "İş Listesi açılamadı.")
            from urllib.parse import parse_qs
            q = parse_qs(query or "")
            task = (q.get("is") or [""])[0]
            extra = f"&is={task}" if task.isdigit() and len(task) < 12 else ""
            if (q.get("sekme") or [""])[0] == "sablonlar":
                extra = "&sekme=sablonlar"
            return 303, {"Location": _cfg["base"] + d["url"] + extra, "Cache-Control": "no-store",
                         "Referrer-Policy": "no-referrer"}, b""
    except _Fail as e:
        return _err(e.status, str(e))
    except httpx.HTTPError as e:
        return _err(503, f"cokluuyap.com'a ulaşılamadı: {type(e).__name__}")
    return _err(404, "Bulunamadı.")
