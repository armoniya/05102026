"""
uyap_gecit — UYAP Ağ Geçidi için yeniden tasarlanmış arayüz katmanı.

Tek bir UI-bağımsız çekirdek (`uyap_gecit.core`) iki ön yüz tarafından paylaşılır:

  • uyap_gecit.gui  — ttkbootstrap masaüstü kontrol ekranı
  • uyap_gecit.web  — Django yerel (localhost) kontrol ekranı

Her iki ön yüz de mevcut `uyap_core` paketini (office_agent / home_client /
yerel proxy 8800 / iş kuyruğu) çekirdek üzerinden sarmalar; UYAP iş mantığı
`uyap_core` içinde kalır, burada SADECE arayüz + ince servis katmanı yaşar.
"""

__all__ = ["core"]
