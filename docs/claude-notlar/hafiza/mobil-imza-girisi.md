---
name: mobil-imza-girisi
description: "Planned UYAP mobil imza login via e-Devlet OAuth — flow observed 2026-10-23, waiting for a test user"
metadata:
  node_type: memory
  type: project
  originSessionId: b23dd417-f40e-4c71-a8c5-04f8c818f4a4
  modified: 2026-10-23T11:37:25.815Z
---

Kullanıcı programa (şu an yalnız e-imza ile giriş var) e-Devlet üzerinden **mobil imza ile giriş** eklemek istiyor. 2026-10-23'te Chrome'da akış incelendi, test edecek mobil imzalı kullanıcı olmadığı için **beklemede**.

Görülen akış:
- avukat.uyap.gov.tr → "E-Devlet ile Giriş → Mobil İmza ile Giriş" →
  `https://giris.turkiye.gov.tr/OAuth2AuthorizationServer/AuthorizationController?response_type=code&scope=Kimlik-Dogrula&redirect_uri=https://avukat.uyap.gov.tr/login.uyap&client_id=27e1dfc0-8536-11e4-b4a9-0800200c9a66&state=<n>&loginTypeIndex=2`
- → `https://giris.turkiye.gov.tr/Giris/Mobil-Imza?oauthClientId=…&continue=…` (form: tridField, gsmField, gsmtype radio turktelekom/turkcell/vodafone, gizli currentPageToken, actionName=sozlesme; POST aynı adrese).
- Kullanıcıya göre: GSM'e kod geliyor, kod girilince UYAP'a bağlanıyor. Dönüş `login.uyap?code=…` olmalı (henüz görülmedi).

**Why:** Ofiste e-imza kartı olmadan giriş yolu.
**How to apply:** Konu yeniden açılınca test kullanıcısıyla form POST'u, kod adımı ve login.uyap dönüşünü ağ isteklerinden yakala; TC/GSM'i kullanıcı kendisi girer.
