---
name: ruyap-port-guncel
description: ruyap (Rust port) bugünkü Python çalışma ağacıyla tam senkron; enjekte/ ve webapp/ diskten paylaşımlı okunur
metadata:
  node_type: memory
  type: project
  originSessionId: e0725ac4-9963-414a-a02e-8caf295d0ac8
  modified: 2026-11-13T16:57:40.448Z
---

2026-11-13 doğrulaması: `uyap_proxy.py`'nin satır içi enjekte JS'i (`r"""<script>...`) ile Rust `INLINE_INJECT_SCRIPT` **birebir aynı** (ikisi de 5011 satır, `diff` 0 fark). `KESILEN_UCLAR`/`kesilen_uc_yaniti`, `_tema_css`/`_tema_on_eki`/`_ek_betikler`/sürüm damgası, `office_agent.py` kesik kontrolü — hepsi Rust'ta mevcut (uyap_proxy.rs:248/5540+/6779, office_agent.rs:366).

**Kritik mimari:** Hem Python hem Rust, arayüz içeriğini iki yerden alır: (1) koda gömülü `INLINE_INJECT_SCRIPT`, (2) **çalışma anında diskten** `uyap_core/enjekte/` (tema/mobil .css+.js, gorevcubugu/kartlar/anasayfa .js) ve `webapp/*.html`. (2) paylaşımlı olduğundan bu dosyalardaki değişiklik **porta elle taşınmaz** — ruyap `_enjekte_klasoru()` / `vendor_server` `base_dir/webapp` ile aynı dosyaları okur.

**Why:** Python tarafında yapılan arayüz güncellemeleri çoğunlukla enjekte/*.js ve webapp/*.html içinde; bunlar paylaşımlı. Yalnız satır içi JS ya da inline-dışı Python mantığı (KESILEN_UCLAR gibi) değişirse Rust'a elle taşımak gerekir — onu da kaynak-karşı-kaynak `diff` ile denetle, git geçmişine değil.

**How to apply:** "Python güncellemelerini Rust'a aktar" istenince önce `py_inline.js` vs `rs_inline.js` diff'i al (uyap_proxy.py 351-5361 `r"""..."""` ↔ uyap_proxy.rs 527-5537 `r#####"..."#####`); boşsa inline iş yok. enjekte/ ve webapp/ değişiklikleri paylaşımlı, taşıma gerekmez. UyapMasa (baglanti.py/uyapmasa.py) ve cokluuyap_app.spec kapsam dışı (UyapMasa `tek-arayuz` ile kalkıyor; .spec yalnız PyInstaller). İlgili: [[rust-derleme-turkce-yol]], [[tek-arayuz]], [[duzeltmeyi-dogrula]].
