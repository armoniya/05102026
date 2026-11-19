---
name: uyap-arayuz-sadelestirme
description: "UYAP klonuna eklenen arayüzü sadeleştirme planı (2026-11-11): tek giriş = sağ kenar tutamacı, koyu tema, UYAP düğmelerini örtme yasağı"
metadata:
  node_type: memory
  type: project
  originSessionId: ede7a23b-bc0e-4d01-9197-5354d451a697
  modified: 2026-11-11T07:36:27.163Z
---

2026-11-11 kullanıcı kararı: Ürünün yönü, UyapMasa yerine UYAP arayüzüne enjekte edilen eklentiler (Uyap Haricen Giriş/uyap_core/uyap_proxy.py). UyapMasa özellikleri (barkod/tebligat, 21/2, toplu analiz) zamanla buraya taşınacak.
- Ekranda sürekli görünen TEK eklenti: sağ kenardaki "Çoklu UYAP" tutamacı. Çekmecede İşler | Araçlar sekmeleri var. Sol alttaki hap düğmeler ve 🖋️ düğmesi kaldırıldı.
- Panellerde koyu tema kalacak, ama tek ve tutarlı bir stile indirilecek.
- Kesin kural: eklentiler UYAP'ın kendi düğmelerini örtmeyecek. Canlı ölçüm: "Loglamayı Başlat" sağ kenarda top:50%, erişilebilirlik sağ altta (~84 px), sol menü 290 px. Tutamaç bu yüzden sekmeYerlestir() ile elementsFromPoint taraması yaparak boş bir yere konuyor.
- Plan dosyası: ~/.claude/plans/imdi-uyapmasa-program-ndan-ok-sunny-pond.md. Aşama 1 2026-11-11'de kuruldu; kalan aşamalar: 2 kart başlığı/bant, 3 ortak CSS/bildirim/z-index, 4 sağ tık + ayarlar.

**Why:** Kullanıcı "sağdan soldan çok fazla buton giriyor, UYAP'ın düğmelerini kapatıyor" dedi; UYAP'a alışmış kullanıcının yeni bir şey öğrenmesi zor.
**How to apply:** Yeni bir özelliği sabit düğme olarak ekleme; çekmecenin Araçlar sekmesine ya da bağlama (dosya kartı) koy. Bkz. [[duzeltmeyi-dogrula]], [[tiklama-bekleme-sevmiyor]].
