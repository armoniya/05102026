# Güvenlik Taraması — 30.09.2026

Kullanıcı isteği: 5 adımlı kontrol (npm audit, Gitleaks, Semgrep, kod incelemesi, OWASP ZAP).
Araçlar proje `.venv`'ine DOKUNMADAN, oturumun geçici klasöründe ayrı bir ortama kuruldu.
İlgili: `docs/PROJE_ELESTIRISI_2026-09-30.md` (genel eleştiri + plan), eski raporlar `guvenlik_analizi.md`,
`GUVENLIK_ANALIZI_2026-07-09.md`.

## Özet

| Adım | Araç | Sonuç |
|---|---|---|
| 1 | npm audit → **pip-audit 2.10.1** (projede `package.json` yok, Python projesi) | **9 pakette 45 bilinen açık** (kurulu `.venv` üzerinden); `requirements.txt` taranamadı: `PyKCS11>=1.5.18` sabitlenmemiş |
| 2 | **Gitleaks 8.30.1** (SHA256 doğrulandı; `gitleaks dir`, git deposu yok) | **Sızıntı bulunmadı** (4,3 MB metin; `.venv`/`__pycache__`/`.claude` hariç) |
| 3 | **Semgrep 1.178.0** (`p/python`, `p/security-audit`, `p/secrets`, `p/javascript`; `--metrics=off`) | 234 dosya, 47 bulgu (10 ERROR) — **incelenen önemlilerin hepsi yanlış alarm / düşük** |
| 4 | Elle kod incelemesi | **2 orta bulgu** (yerel jeton atlatma, LAN'da düz HTTP + deneme sınırı yok) + 1 tasarım riski |
| 5 | **OWASP ZAP 2.17.0** (01.10, kullanıcı seçimi: yerel PASİF tarama) | 61 istek / 19 URL: **1 orta** (CSP başlığı yok), **1 düşük** (Server başlığı sürüm sızdırıyor), 2 bilgi; yol aşımı denemeleri engellendi. Elle incelemede ek bulgu: **durum değiştiren GET** (4.5) |

## 1. Bağımlılık açıkları (pip-audit, kurulu paketler)

| Paket | Kurulu | Açık | En az yükseltilecek sürüm | Bu projede etkisi |
|---|---|---|---|---|
| **aiohttp** | 3.14.1 | 3 (WebSocket upgrade'de request smuggling, RSV1 sıkıştırma, C ayrıştırıcıda taşma okuma) | **3.14.3** | **YÜKSEK** — `vendor_server.py` (cokluuyap.com, internete açık, `/ws` signaling) aiohttp ile çalışıyor. Render'daki sürüm ayrıca kontrol edilmeli (deploy yolu bilinmiyor). |
| **urllib3** | 2.7.0 | 3 (proxy TLS ayarı, streaming API) | 2.8.0 | Orta — `requests` üzerinden PTT/UYAP istemcileri |
| **cryptography** | 48.0.1 | 3 (sertifika zinciri doğrulama, PKCS7) | 50.0.0 | Orta — e-imza/CAdES, Fernet |
| **pypdf** | 6.13.2 | 11 (özel hazırlanmış PDF ile sonsuz döngü / bellek) | 6.16.1 | Orta — UYAP'tan inen PDF'ler (tebligat/mazbata) okunuyor |
| **pillow** | 12.2.0 | 13 (font/görüntü ayrıştırma taşmaları, WindowsViewer komut) | 12.3.0 | Düşük-orta |
| **anyio** | 4.13.0 | 2 | 4.14.2 | Düşük — FastAPI/uvicorn (proxy) |
| **django** | 6.0.6 | 4 (cache middleware, GIS, DomainNameValidator) | 6.0.8 | Düşük — yalnız ORM kullanılıyor, etkilenen bileşenler yok |
| **sqlparse** | 0.5.5 | 5 (ReDoS/DoS) | 0.6.0 | Düşük — Django bağımlılığı |
| **setuptools** | 82.0.1 | 1 (sdist) | 83.0.0 | Düşük — derleme aracı |

Ayrıca: `requirements.txt` hash'siz ve `PyKCS11>=1.5.18` sabitlenmemiş (tedarik zinciri; pip-audit bu yüzden
dosyayı taramayı reddetti).

## 2. Gitleaks — sızıntı yok
- Kaynak kod, belgeler ve yapılandırmada API anahtarı/jeton/parola bulunmadı.
- Kapsam sınırı: git geçmişi YOK (depo yok) → geçmiş taranamadı. Proje dışındaki gizli dosyalar
  (`%LOCALAPPDATA%\UyapIcra\dbpass.secret`, `gw_local_token`, `icra_secret_key`, `uyap_app_config.json`) taramaya
  dahil değil — bunlar zaten `.gitignore`'da / proje dışında.

## 3. Semgrep — değerlendirme
| Kural | Adet | Karar |
|---|---|---|
| `dynamic-urllib-use-detected` | 23 | Yanlış alarm — adresler kod içi sabit (UYAP/PTT/127.0.0.1), kullanıcıdan gelmiyor |
| `use-defused-xml(-parse)` | 9 | Düşük — Python `xml.etree` dış varlık (XXE) yüklemez; XML'ler yerel dosya/UYAP. İstenirse `defusedxml`'e geçilir |
| `raw-html-format` / `directly-returned-format-string` (`vendor_server.py`) | 7 | **Yanlış alarm** — tüm kullanıcı değerleri `html.escape` ile kaçışlanıyor; Host başlığı `UYAP_CANONICAL_HOST` ile ele alınmış (canlıda ayarlı olduğu DOĞRULANMADI) |
| `dangerous-subprocess-use-tainted-env-args` (`kurucu.py:165`) | 1 | Yanlış alarm — liste argümanlı, shell yok, yol kurucunun kendi hedef klasörü |
| `insecure-hash-algorithm-md5` | 4 | Düşük — önbellek/ETag amaçlı görünüyor (güvenlik amaçlı değil; tek tek doğrulanmalı) |
| `avoid-bind-to-all-interfaces` | 2 | Bkz. bulgu 4.2 |
| `var-in-href` (gecit dashboard şablonu) | 1 | Düşük — Django şablonu otomatik kaçışlar; `javascript:` şeması kontrol edilmeli |

## 4. Elle inceleme

### 4.1 [ORTA] Yerel yetki jetonu tek bir başlıkla atlatılabiliyor
`Uyap Haricen Giriş/uyap_core/uyap_proxy.py` — `require_auth` (satır ~1480-1491) aynı makineden gelen
isteklerde jetonu ister, AMA `_looks_like_local_browser` (satır ~1427-1442) isteği "tarayıcı" sayarsa jetonsuz
geçirir. Ölçütler istemcinin kendi gönderdiği başlıklar: `Origin` (yerel IP), `Sec-Fetch-*`, yerel `Referer`,
ya da GET + `Accept: text/html`. Tarayıcı olmayan her yerel program bu başlıkları ekleyebilir → örn.
`Origin: http://127.0.0.1:8800` ile **e-imzalı UYAP oturumuna jetonsuz tam erişim**. Jetonun koruması
gereken senaryo (aynı makinede başka süreç/kullanıcı) tam da bu.
Öneri: tarayıcı-dışı istemcilere jeton ZORUNLU kalsın; yerel tarayıcı arayüzü için jeton, sayfa
yüklenirken HttpOnly/SameSite=Strict çerezle verilsin ve API istekleri çerez + başlık (double-submit) ile
doğrulansın. Başlık tahmini yetki kararı olmamalı.

### 4.2 [ORTA] Proxy (8800) tüm ağı dinliyor; ağ parolası düz HTTP'de, deneme sınırı yok
`host="0.0.0.0"` (`UyapMasa/modules/baglanti.py:269`, `web/server.py:438`, `uyap_app.py:1671`,
`uyap_gecit/core/connection.py:185`). LAN'dan gelen istekler Basic-Auth / LAN bileti ile korunuyor ama:
(a) TLS yok → aynı ağdaki biri parolayı/bileti okuyabilir; (b) proxy'de başarısız giriş sınırlaması yok
(`vendor_server` ve UyapMasa web'de var). Gerçek risk Windows Güvenlik Duvarı'nın 8800'e LAN erişimine
izin verip vermediğine bağlı — **kontrol edilmeli**.
Öneri: LAN-direct gerekmiyorsa 127.0.0.1'e bağla; gerekiyorsa TLS + deneme sınırı + yalnız özel ağ
(Private profile) güvenlik duvarı kuralı.

### 4.3 [TASARIM RİSKİ] Kayıtlı e-imza PIN'i + otomatik bağlanma
PIN DPAPI ile doğru şifreleniyor (fail-closed, düz metne düşmüyor — iyi). Ama kart takılıyken, aynı Windows
kullanıcısıyla çalışan her kod PIN'i çözüp avukat adına imza/UYAP işlemi yapabilir (DPAPI yalnız başka
kullanıcılara ve çalınan diske karşı korur). 4.1 ile birleşince etkisi artar. Kabul edilmiş bir kolaylık
ise belgelenmeli; değilse PIN oturum başına sorulmalı.

### 4.4 [DÜŞÜK] Diğer
- `%LOCALAPPDATA%\UyapIcra\dbpass.secret` (yerel PostgreSQL parolası) düz metin; yalnız yerel DB, ACL
  kısıtlaması yapılıp yapılmadığı kontrol edilmeli.
- `Dosya Açılış/İcra Takip Açılış - XML/Uyap_Xml_Takip_Açan.py:18` `os.system("taskkill /f /im chrome.exe")`
  — kullanıcının TÜM Chrome pencerelerini kapatır (güvenlik değil, veri kaybı riski).
- 959 geniş `except` (bkz. eleştiri B4) güvenlik olaylarının da sessizce yutulmasına yol açabilir.

### 4.5 [DÜŞÜK-ORTA] Durum değiştiren GET uç noktası (CSRF korumasının dışında)
`UyapMasa/web/server.py` `GET /api/senkron-kapsami/yenile?yargi_turu=…` (satır ~2329) → `senkron_kapsami_yenile`
UYAP'a CANLI istek atıp Yargı Birimi listesini DB'ye yazıyor (`dosya_core.yargi_birimleri_getir`). CSRF denetimi
(`_csrf_ok`) yalnız POST'lara uygulanıyor; oturum çerezi `SameSite=Lax` olduğundan çapraz-site resim/fetch
çerez taşımaz, ama **üst-düzey gezinme** (kullanıcının tıkladığı bir bağlantı) çerezi taşır → başka bir site
kullanıcıya UYAP isteği + DB yazımı yaptırabilir (DNS-rebinding koruması Host'u kontrol ettiği için etkisi
sınırlı; yine de ilke ihlali). Öneri: POST'a çevir (CSRF korumasına girsin). `GET /api/icra/yerel-tazele` de
sunucuda iş nesnesi oluşturuyor (yalnız yerel DB okuma — düşük).

### 4.6 İyi olanlar
CSRF + DNS-rebinding korumaları (UyapMasa web ve proxy), `secrets.compare_digest`, HttpOnly/SameSite
çerezler, web'de giriş deneme sınırı, `html.escape` disiplini (vendor), DPAPI fail-closed, owner admin TOTP'li,
hassas dosyalar `.gitignore`'da.

## 5. OWASP ZAP — yerel pasif tarama (01.10.2026)
Kullanıcı seçimi: **yerel pasif tarama** (aktif tarama UYAP'a ücretli/hukuki işlem tetikleyebileceği ve
cokluuyap.com canlı olduğu için yapılmadı).
**Güvenli koşullar:** DB yedeği alındı (`%LOCALAPPDATA%\UyapMasa\yedek\uyap_icra_zap_oncesi_20261001_074150.dump`,
7,4 MB, pg_dump -Fc). Sunucu, proje koduna dokunmayan bir başlatıcıyla açıldı: açılış otomatik UYAP
bağlantısı ve senkron zamanlayıcısı devre dışı, paylaşım/alım başlatılamaz, alt süreç başlatılamaz, ağ
bekçisi yalnız 127.0.0.1:5432/8090'a izin verdi (tarama boyunca 14 girişim engellendi — hepsi
127.0.0.1:8800 UYAP proxy durum yoklaması; dış adrese hiç girişim yok). ZAP 2.17.0 (taşınabilir, JRE 17,
SHA256 doğrulandı) — örümcek form işleme/POST KAPALI, yalnız GET; giriş yapılmış sayfalar için test oturumu
çerezi. Tarama sonrası ZAP ve sunucu kapatıldı.

| Risk | Bulgu | Nerede | Öneri |
|---|---|---|---|
| Orta | Content-Security-Policy başlığı yok | `/`, `/login` (HTML sayfalar) | `server.py _send`'e CSP ekle (inline script/style varsa önce dışarı al ya da nonce) |
| Düşük | `Server: UyapUyapMasa/1.0 Python/3.13.15` sürüm sızdırıyor | tüm yanıtlar (44 URL) | `BaseHTTPRequestHandler.server_version`/`sys_version` boşalt |
| Bilgi | JS'te "şüpheli" yorumlar | `static/barkod.js`, `dosyalarim_genel.js`, `hukuk.js` | gözden geçir (iç bilgi içermesin) |
| Bilgi | Modern web uygulaması (JS ile çizilen sayfa — örümcek sınırlı) | `/`, `/login` | — |

Elle denenen yol aşımı (`/static/..%2F..%2Fserver.py`, `%2e%2e`, ters bölü) → hepsi **404** (korunuyor).
Mevcut güvenlik başlıkları: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
`Referrer-Policy: same-origin`. **Kapsam sınırı:** pasif tarama POST uçlarını ve enjeksiyon türü açıkları
test etmez; aktif tarama ancak UYAP'tan tamamen yalıtılmış bir test kopyasında yapılmalı.

## 6. Düzeltmeler (01.10.2026) ✅
Önce yedek: `%LOCALAPPDATA%\UyapMasa\yedek\guvenlik_duzeltme_20261001_091509\` (pip freeze, tüm requirements
dosyaları, değişen kod dosyalarının eski hâli). Çalışan uygulama yoktu.

| # | Düzeltme | Doğrulama |
|---|---|---|
| 1 | **Paketler** (proje `.venv`): aiohttp 3.14.3, anyio 4.15.1, cryptography 50.0.2, Django 6.0.8, pillow 12.3.0, pypdf 6.19.0, setuptools 84.0.0, sqlparse 0.6.0, urllib3 2.8.0 + **pyOpenSSL 26.4.0** (26.2.0 cryptography<49 istiyordu; yalnız aiortc kullanıyor) + typing_extensions 4.16.0 | `pip check` temiz; **pip-audit: 72 paket, 0 açık**; duman testi: aiortc, Fernet, x509/RSA imza, cades, udf_converter, pypdf, pillow, Django ORM (44.696 dosya okundu), barkod_sorgu, icra_core, uyap_proxy/office_agent/home_client, vendor_server, web server içe aktarımı OK |
| 2 | **Gereksinim dosyaları**: `requirements.txt` + `Uyap Haricen Giriş/requirements.txt` yeni sürümlere sabitlendi (PyKCS11 kurulu gerçek sürüm **1.5.20**'ye sabitlendi); `requirements-vendor.txt` / `-p2p.txt` → `aiohttp>=3.14.3`; `-convert.txt` → `cryptography>=50.0.0`, `pypdf>=6.16.1`, `Pillow>=12.3.0` | — |
| 3 | **YENİ BULGU + düzeltme — üye tarafı yerel proxy (`home_client.make_app`)**: kimlik doğrulama da, Host/Origin denetimi de YOKTU → bir web sitesi üyenin tarayıcısı üzerinden (DNS rebinding / çapraz-site) ofis tüneliyle UYAP'a erişebilirdi. Yeni saf modül `uyap_core/yerel_kapi.py` (`tarayici_reddi` — ofis `require_auth` ile aynı kurallar) + `home_client` istek işleyicisinin başında 403 | `uyap_core/test_yerel_kapi.py` 13 test OK; gerçek aiohttp sunucusunda: Python istemcisi/yerel tarayıcı 200, rebinding Host/yabancı Origin/çapraz-site fetch 403 |
| 4 | **4.2 kısmi** — ofis proxy Basic-Auth'a IP başına deneme sınırı (15 dk'da 10 hata → 429; kimliksiz ilk istek sayılmaz; yerel istekler etkilenmez) — `uyap_proxy._auth_engelli_mi/_auth_hata_kaydet` | TestClient: doğru 200, 10 yanlış 401, 11. 429, sıfırlanınca 200 |
| 5 | **4.5** — `/api/senkron-kapsami/yenile` GET → **POST** (CSRF korumalı); `static/senkron_kapsami.js` güncellendi | eski GET 404, yeni POST 200, çapraz-site POST 403 |
| 6 | **ZAP: CSP** — tüm yanıtlara `Content-Security-Policy` (script-src 'self'; style 'unsafe-inline' — index.html style="" öznitelikleri), güvenlik başlıkları `end_headers`'a taşındı (yönlendirme/indirme/hata da kapsanıyor); index.html'deki tek satır içi script → `static/fab.js` | Gerçek (headless) Chrome'da /login ve / yüklendi: **CSP ihlali yok**, FAB menüsü çalışıyor; tek konsol hatası `favicon.ico` 404 (önceden de vardı) |
| 7 | **ZAP: Server başlığı** → `UyapMasa` (Python sürümü yok) | doğrulandı |

| 8 | **4.1 yerel jeton atlatma — TAM DÜZELTME** (kullanıcı kararı 01.10: "giriş akışı değişir"). Başlıklara bakıp "tarayıcı" tahmini (`_looks_like_local_browser`) KALDIRILDI. Yerel tarayıcı artık yalnız süreç başına üretilen gizli değeri taşıyan çerezle (`uyap_yerel`, HttpOnly, SameSite=Strict) kabul edilir. Çerez, jetonu bilen programın `POST /__uyap_yerel__/kod` ile aldığı **60 sn'lik tek kullanımlık kodla** açılan `GET /__uyap_yerel__/giris?k=…&hedef=/giris` bağlantısında verilir (asıl jeton URL'ye düşmez; hedef yalnız göreli yol — açık yönlendirme yok). Çerezsiz tarayıcı → açıklayıcı 403 sayfası ("UYAP'ı programdan açın"). Ortak yardımcı `uyap_core/yerel_giris.py: tarayici_giris_url(port)` — kod alınamazsa (alıcı modu/home_client, proxy kapalı) düz `/giris`'e düşer. Başlatıcılar güncellendi: `UyapMasa/modules/baglanti.py open_browser`, `uyap_app.py on_open_browser_click`, `uyap_gecit/core/connection.py open_browser`, UyapMasa web (`POST /api/conn/tarayici-url` + `static/conn.js openUyap`); günlükteki "127.0.0.1:…/giris'ten girin" metinleri ve gecit panosundaki düz bağlantı kaldırıldı | 17 uçtan uca kontrol (TestClient + gerçek uvicorn + gerçek Chrome): eski sahte Origin/Sec-Fetch/Referer atlatmaları 403; Python jetonla 200; kod jetonsuz/ağdan 403; tek kullanım, süre aşımı, sahte çerez, açık yönlendirme engelli; Chrome'da güvenli bağlantı → /giris, diğer sayfalar ve XHR çerezle 200; yer imi/elle adres → 403 açıklama sayfası. Web ucu: oturumlu 200, çapraz-site 403, oturumsuz 401 |
| 9 | **Yeni bulgu + düzeltme — yerel jeton UYAP'a sızıyordu**: `X-Uyap-Local-Token` başlığı UYAP'a iletilen isteklerden silinmiyordu → `DROP_REQUEST`'e eklendi | — |

**Kullanıcıya etkisi (4.1):** `http://127.0.0.1:8800/giris` artık yer iminden/elle açılamaz; UYAP her zaman
programdaki **"Tarayıcıda Aç / Tarayıcıyı Aç"** düğmesiyle açılır. Proxy (paylaşım) yeniden başlatılınca açık
UYAP sekmeleri "programdan açın" sayfasına düşer → düğmeyle yeniden açılır. Yerel ağdaki diğer
bilgisayarlar (parolalı) ve dış ağ üyeleri ETKİLENMEZ.

**Yedek notu:** `uyap_app.py`, `UyapMasa/modules/baglanti.py`, `uyap_gecit/core/connection.py`,
`uyap_gecit/web/gecit/templates/gecit/dashboard.html`, `UyapMasa/web/static/conn.js` düzenlenmeden önce
yedeklenmedi (diğerleri yedekte); değişiklikleri küçük ve bu bölümde tarif edildi.

**Açık kalanlar:**
- **4.2 TLS** — LAN'dan doğrudan tarayıcı erişimi (http://ofis-ip:8800/giris) tasarım gereği düz HTTP + Basic-Auth;
  TLS ayrı iş. Windows Güvenlik Duvarı'nın 8800'e izni kontrol edilmeli.
- **cokluuyap.com** — `requirements-vendor.txt` artık aiohttp ≥3.14.3 istiyor ama Render'a yeniden yükleme
  yapılmadan canlı sunucu açıklı sürümde kalır (deploy yolu bilinmiyor — YAPILACAKLAR §0 madde 4).
- Arayüzler (masaüstü/web) bu değişikliklerle elle açılıp denenmedi; uygulamalar yeniden başlatılmalı.

## 7. İkinci tur (01.10.2026) — vendor_server elle inceleme, LAN girişi, üye tarafı, düşük bulgular, aktif tarama ✅
Yedek: `%LOCALAPPDATA%\UyapMasa\yedek\guvenlik_duzeltme2_20261001_100140\` (tüm dokunulan dosyalar, işe başlamadan).

**Düzeltme (4.2 abartılıydı):** ofis proxy'si tüm ağa YALNIZ "Yerel ağdan doğrudan erişim" seçeneği açıkken
(`UYAP_LAN_SHARE=1`, `uyap_app` onay kutusu; vars. KAPALI) açılır — `_resolve_bind_host` aksi halde 127.0.0.1'e düşürür.

### 7.1 vendor_server.py (cokluuyap.com) — elle tam okuma + düzeltmeler
| # | Bulgu | Önem | Düzeltme | Doğrulama |
|---|---|---|---|---|
| V1 | `_client_ip` XFF'in İLK (istemcinin yazdığı) değerini alıyordu → her denemede sahte IP ile TÜM oran sınırları (owner/ofis/ws/api/sıfırlama/üyelik) atlatılabiliyordu | **Yüksek** | Güvenilen proxy'nin eklediği SAĞDAN değer (`UYAP_PROXY_HOPS`, vars. 1) ya da `UYAP_CLIENT_IP_HEADER`; zincir uzunluğu ilk istekte bir kez loglanır (Render'da doğrulamak için) | 12 sahte-XFF denemesi sınıra takıldı; başka IP etkilenmedi |
| V2 | `/ws`'de `role: office` için yalnız istemci beyanı; ofisin HER kullanıcısı açık ofis bağlantısını kapatıp tüm üyelerin WebRTC tekliflerini kendine çekebiliyordu | **Yüksek** | Açık ofis bağlantısını yalnız AYNI kullanıcı devralabilir (yeniden bağlanma); farklı kullanıcı reddedilir; ofis kapanınca üye paylaşım başlatabilir (ürün kararı: `UyapMasa/Sonra Eklenecekler.md`). Owner hesabı /ws'e (TOTP'siz yol) giremez | master katıldı; üye devralamadı; master yeniden bağlandı; ofis kapanınca üye başlattı |
| V3 | `/ice` + `/__app__/config.js` efemeral TURN kimliğini kimliksiz/sınırsız dağıtıyordu (TURN açık aktarıcı/maliyet) | Orta | IP başına 10 dk'da 60 (`UYAP_ICE_RATE`); aşınca /ice 429, config.js TURN'süz | 61. istek 429; config.js TURN'süz |
| V4 | Form POST'ları CSRF'e karşı yalnız SameSite çereze dayanıyordu | Düşük | `Sec-Fetch-Site: cross-site` olan durum-değiştiren istekler 403 (Origin'e bakılmaz — no-referrer yüzünden meşru POST'ta "null") | çapraz-site 403; aynı-kaynak (Origin: null) giriş OK; webhook etkilenmedi |
| V5 | PBKDF2 200k tur (OWASP 600k önerir) | Düşük | **YAPILMADI — karar bekliyor** (Render ücretsiz katmanda her girişi ~3× yavaşlatır) | — |
| — | `Server` başlığı aiohttp/Python sürümü (ZAP aktif) | Düşük | `Server: cokluuyap` | doğrulandı |
Sağlam bulunanlar: owner TOTP + replay koruması, imzalı/epoch'lu oturumlar (owner Strict, ofis Lax), master yetki + aynı-ofis
kontrolleri, `html.escape` disiplini, webhook paylaşılan sır (yoksa kapalı), sıfırlamada enumeration savunması, pbkdf2 + sabit-zaman kukla hash.
Not: eski `signaling_server.py` aynı V2 zaafını taşıyor ama Docker imajına girmiyor (yalnız vendor_server yayında) — düzeltilmedi.

**✅ YAYINLANDI (01.10.2026, Render otomatik):** depo `github.com/armoniya/uyap-vendor` (Render servisi `uyap-vendor`,
`srv-d8mobr3tqb8s73cbota0`, autoDeploy On Commit) bu PC'ye `Uyap Haricen Giriş/vendor_deploy/` olarak klonlandı
(Git 2.55 kuruldu). Commit'ler: `24f2c2e` (V1–V4 + aiohttp ≥3.14.3; requirements'ta `psycopg2-binary` KORUNDU —
yerel kopyada yoktu, düz kopyalansaydı canlı hesap DB'si kopardı; `usage_logger._client_ip` da düzeltildi),
`ec1576b` (XFF teşhis satırı), `1b968c0` (IP kaynağı vars. **CF-Connecting-IP**). **Canlı teşhis:** Render önünde
Cloudflare var; XFF'de 3 değer, en sağdaki iç adres → HOPS=1 herkesi tek anahtarda topluyordu (DoS riski) →
CF-Connecting-IP (Cloudflare yazar, istemci değiştiremez). Canlıda doğrulandı: çapraz-site POST 403, /ice çalışıyor,
hesap deposu PostgreSQL'e bağlandı (2 ofis/15 kullanıcı), ofis ajanı yeniden katıldı. `Server` başlığını Cloudflare
kendi değeriyle (cloudflare) değiştiriyor.

**Eski not — canlıya çıkış:** belgelenmiş yol `docs/CANLIYA_HAZIRLIK.md` Faz 1: değişiklik ana kopyada →
`Uyap Haricen Giriş/vendor_deploy/` (ayrı git deposu) altına birebir kopya → commit + push → Render `autoDeploy`.
**`vendor_deploy/` bu bilgisayarda YOK** → yayın için o depoya erişim gerekir (kullanıcı). Yayına gidecek dosyalar:
`vendor_server.py` (+ `accounts.py` değişmedi), `requirements-vendor.txt` (aiohttp ≥3.14.3). Yayın sonrası Render
günlüğünde "[i] X-Forwarded-For zincirinde N değer var" satırına bakılmalı: N=1 ise vars. ayar doğru; daha fazlaysa
`UYAP_PROXY_HOPS` ya da `UYAP_CLIENT_IP_HEADER` ayarlanmalı.

### 7.2 Ofis proxy'si — yerel ağ (LAN) tarayıcı giriş sayfası (kullanıcı isteği)
LAN açıkken programdan bağlanmayan tarayıcıya ham Basic-Auth penceresi yerine **giriş sayfası**
(`/__uyap_lan__/giris`): ofis programına girilen kullanıcı adı + parola → `uyap_lan` çerezi (HttpOnly, Strict).
Program (home_client) eskisi gibi LAN biletiyle girer; programlar (JSON) 401 + Basic alır. Ortak deneme sınırı
(15 dk/10), açık yönlendirme yok, DNS-rebinding/çapraz-site 403. Not: LAN bağlantısı hâlâ düz HTTP (TLS ayrı iş;
sayfada uyarı var). Doğrulama: 9 TestClient kontrolü + **gerçek Chrome LAN IP'den (192.168.1.161) giriş → UYAP sayfası**.

### 7.3 Üye tarafı yerel proxy (home_client) — yerel yetki
Eskiden üye bilgisayarındaki HER program ofis tüneliyle UYAP'ı kullanabiliyordu. Artık ofisle AYNI kural:
program jetonu (açılışta `_init_local_token` — jeton dosyası + süreç içi urllib otomatik ekleme) ya da "Tarayıcıyı Aç"
tek kullanımlık bağlantısının çerezi; yalnız 127.0.0.1. Üyenin jetonu/çerezi/Authorization başlığı ofise
(tünel + LAN) İLETİLMEZ. Logger'lar (`logger_core.py`, eski `Logger/Uyap_session_logger.py`) da tek kullanımlık
bağlantıya geçirildi (eskisinde eksik `import sys` da eklendi). 8800'e giden tüm UyapMasa modülleri urllib
kullanıyor (requests/httpx yok) → jeton otomatik. Doğrulama: 10 kontrol + gerçek Chrome üye akışı.

### 7.4 Düşük bulgular (madde 5)
- `dbpass.secret`: ACL zaten yalnız kullanıcıya (kalıtım yok, SYSTEM/Administrators bile yok); PostgreSQL yalnız
  127.0.0.1/::1 + scram-sha-256 → **doğrulandı, değişiklik gerekmedi**.
- XML: expat 2.8.2 (billion-laughs korumalı ≥2.4.0), ElementTree dış varlık yüklemez → **XXE yok, doğrulandı**.
- MD5: 4 kullanım da önbellek dosya adı/sürüm etiketi → `usedforsecurity=False` işaretlendi.

### 7.5 Aktif taramalar (yalıtılmış kopyalar)
- **vendor_server** (geçici hesap deposu, 127.0.0.1:18080; form gönderme açık): 8.593 istek — **Yüksek yok**;
  Orta: CSRF jetonu yok (Sec-Fetch-Site + SameSite ile korunuyor), CSP unsafe-inline (eski kabul, #15);
  Düşük: Server başlığı (düzeltildi); Bilgi: /reset token URL'de (tasarım, no-referrer), yansıyan değer kaçışlı.
- **UyapMasa web** (DB'nin ayrı test kopyası `uyap_icra_zaptest`, ağ/süreç bekçisi, ayar/PIN/e-imza/mağaza/Logger
  uçları hem etkisiz hem tarama dışı — e-imza kartı PIN denemesiyle kilitlenmesin diye): **Yüksek yok**; Orta: CSP
  style-src unsafe-inline (bilinçli). **Kapsam sınırlı:** 20 dk sınırında yalnız 611 istek (uçlar yavaş: 44 bin
  dosyalık sorgular + bekçinin kestiği 390 UYAP çağrısı). Tarama sonrası **gerçek `uyap_icra` 19 tablonun içerik
  özeti birebir aynı, `uyap_app_config.json` özeti aynı**; test DB silindi.
- ✅ (01.10 akşam) Gerçek UYAP/e-imza oturumuyla kullanıcı denemesi: UyapMasa yeni kodla açıldı, e-imza girişi
  başarılı; "Tarayıcıda Aç" → UYAP portalı; Dosya Görüntüle canlı sorgu çalıştı (jeton); Logger UYAP'a bağlandı.
  Proxy canlı: jetonsuz/sahte-Origin 403, jetonlu 200, yer imi /giris 403.
- ✅ (02.10) **Paket yükseltmeleri çalışan programa da uygulandı:** UyapMasa sistem Python 3.14 ile çalışıyor
  (`AppData\Local\Programs\Python\Python314`); 01.10'da yalnız `.venv` güncellenmişti. UyapMasa kapalıyken aynı
  yükseltmeler (+ pyOpenSSL 26.4.0) kuruldu; yedek `%LOCALAPPDATA%\UyapMasa\yedek\sistem_python_20261002_103328\`.
  `pip check` temiz, **pip-audit 82 paket / 0 açık**, duman testi (sistem Python ile) OK.
- Kalan: UyapMasa POST uçlarının daha uzun aktif
  taraması (uç başına süre sınırıyla, gece).

## Önerilen sıra
1. Bağımlılık yükseltmeleri (önce aiohttp — cokluuyap.com; sonra urllib3/cryptography/pypdf/pillow), sonra
   `requirements.txt` sabitleme; testler/uygulama açılışı ile doğrulama. Render sürümü ayrıca.
2. 4.1 yerel jeton atlatma düzeltmesi.
3. 4.2 güvenlik duvarı kontrolü → 127.0.0.1 ya da TLS + deneme sınırı.
4. 4.5 durum değiştiren GET → POST; ZAP bulguları (CSP, Server başlığı) — küçük işler.
5. İleride: UYAP'tan yalıtılmış test kopyasında ZAP aktif tarama.

## 8. Light sürüm (CokluUyapKur.msi) — tüm düzeltmeler (13.10.2026)
**Bulgu:** cokluuyap.com/indir/light'taki MSI (01.10 17:04) düzeltme ÖNCESİ kodla derlenmişti: `_looks_like_local_browser`
duruyordu; `yerel_giris`/`yerel_kapi`, deneme sınırı, LAN giriş sayfası yoktu; cryptography 48.0.1.

| # | Yapılan | Doğrulama |
|---|---|---|
| 1 | §6–7 düzeltmeleri light'a girdi (yeniden derleme, commit `1ebb6b6`) | paketin bayt kodu: tüm işaretler var, eski atlatma yok, cryptography 50.0.2 |
| 2 | **pip-audit (13.10) yeni açık: multidict 6.7.1 CVE-2026-104874** → `.venv` 6.9.1, iki requirements.txt; sunucu `requirements-vendor.txt` `multidict>=6.9.1` (vendor_deploy `cfcd70a`, push → Render) | `pip check` temiz; sunucu gereksinim çözümü pip-audit: açık yok |
| 3 | **4.2 LAN TLS** — `uyap_core/lan_tls.py`: LAN açıkken düz HTTP 8800 yalnız 127.0.0.1; ağa HTTPS kapısı **8843** (kendinden imzalı EC P-256, 3 yıl, `%LOCALAPPDATA%\UyapIcra\lan_tls\`). Parmak izi LAN biletine eklenir (`<bilet>.<sha256>`, sunucu değişikliği yok); `home_client` önce sertifikayı çekip izi doğrular, bileti yalnız o sertifikaya sabitli bağlamla gönderir. İzsiz bilet (eski ofis) → LAN denenmez, tünel. LAN giriş çerezi HTTPS'te `Secure`. Light/gecit günlüğü https adresini + parmak izini yazar | `test_lan_tls` 8 test; uçtan uca 13/13 (düz HTTP yalnız yerelde, yanlış iz → tünel + bilet gitmez, eski ofis → tünel, LAN giriş 200, Secure çerez, deneme sınırı 429) |
| 4 | **Toplu iş light'ta kapalı** (kullanıcı kararı): `uyap_app` → `UYAP_TOPLU_IS=0`; `jobs` uçları 404, `job_handlers` + mts/teblig_212/ipotek/xml_takip/esas_no/dosya_kaydet yüklenmez ve spec'te dışlanır. UyapMasa'da varsayılan açık | light: 404 + modül yok, `sign_udf` hâlâ yönlendiriliyor; varsayılan: 12 tür, 201 |
| 5 | **UDF e-imza köprüsü AÇIK** (kullanıcı kararı 13.10): bağlanan üye ofis e-imzasıyla belge imzalatabilir — bilinçli kabul | — |
| 6 | **4.3 kayıtlı PIN + otomatik giriş — kabul edilmiş risk** (kullanıcı kararı 13.10): kur-unut paylaşım için PIN DPAPI ile saklanmaya devam eder; aynı Windows kullanıcısıyla çalışan kod PIN'i çözebilir. Telafi: ofis bilgisayarında ayrı/kısıtlı Windows hesabı, yabancı program kurulmaması | — |

**Etki:** eski sürüm üyeler (UyapMasa 0.0.1 dahil) yeni ofise LAN'dan bağlanamaz → otomatik dış ağ tüneli (çalışır, yalnız
daha yavaş). Tarayıcıyla LAN girişi `https://ofis-ip:8843/__uyap_lan__/giris` — ilk girişte sertifika uyarısı (günlükteki
parmak iziyle karşılaştırılır). Güvenlik duvarı 8843'e izin sorabilir.
**Bekleyen:** derlenen exe bu PC'de açılmadı (8800'deki çalışan UyapMasa'yı kapatırdı) → Masaüstü
`CokluUyapKur_light_26.10.13.msi` ofiste kurulup denenecek; sonra `vendor_deploy/downloads/CokluUyapKur.msi` + push.
Gerçek LAN (iki bilgisayar) denenmedi. Sistem Python314 / kurulu UyapMasa'da multidict hâlâ 6.7.1 (UyapMasa kapalıyken yükselt).

## 9. Kalan öneriler (13.10.2026, /zs HARİÇ — kullanıcı kararı)
| # | Yapılan | Doğrulama |
|---|---|---|
| 1 | **UyapMasa 0.0.2** = 0.0.1 kodu + multidict 6.9.1 (stage site-packages yamalandı, yeniden derleme yok); GitHub Release v0.0.2 (`armoniya/uyapmasa-indir`, latest); sistem Python314 multidict 6.9.1 | MSI kurmadan açıldı: multidict 6.9.1, açık `.py` 0, `--selftest` **SELFTEST OK**; indirme bağlantısı yeni dosyayı veriyor |
| 2 | **PBKDF2 200k → 600k** (`accounts.py`, ana + vendor_deploy `102b4dc`): eski özet ilk başarılı girişte aynı parolayla yeniden özetlenir. /zs aynı `_hash_password`'ı kullanır (kod değişmedi) | geçici depo: 200k kayıt girişte 600k'ya yükseldi, tekrar giriş/yanlış parola/olmayan kullanıcı doğru; giriş ~0,6 sn (yerel) |
| 3 | Sunucudan eski `downloads/CokluUyapKur.exe` kaldırıldı (`102b4dc`) | — |
| 4 | Eski `signaling_server.py` (yayında değil): V2 ofis devralma + owner /ws engeli (`96c1581`) | gerçek websockets: üye devralamadı (hata), usta bağlı kaldı, usta yeniden bağlandı |
| 5 | **Semgrep 1.180** (p/python, security-audit, secrets, javascript) 01.10 sonrası değişen 36 dosya (/zs hariç) + sira_kapisi: 16 bulgu — hepsi yanlış alarm (html.escape'li f-string, port yoklaması bind, Windows parmak izi SHA1, yerel urllib). `_base_url` Host: Cloudflare yabancı Host'u 403 ile kesiyor | — |
| 6 | **Gitleaks 8.30.1** (SHA doğrulandı) iki deponun TÜM git geçmişi: tek bulgu `tests/test_zs.py` sahte push anahtarı (push.example) → yanlış alarm | — |
| 7 | **UyapMasa web ZAP 2.17.0 aktif tarama** — yalıtılmış düzenek (DB kopyası `uyap_icra_zaptest`, Python soket bekçisi yalnız 127.0.0.1:5432 + kendi portları, alt süreç/tarayıcı/PyKCS11 yasak, ayar/conn/udf/store/logger/mts/sgk/uretilmis/barkod-sorgula/login/logout 403 + ZAP dışı) | bekçi: UYAP (212.175.130.30:443) ve 8800 istekleri engellendi. **%20de DURDURULDU** (kullanıcı: kapsam yalnız light) — test DB silindi, sonuç yok |
| — | **Kod imzalama** yapılamadı: sertifika satın alınması gerekir (kullanıcı) | — |
