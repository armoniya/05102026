# Çoklu UYAP / Armonia — Claude için başlangıç notu

- Kullanıcıyla **her zaman Türkçe** konuş: ara mesajlar, planlar ve soru pencereleri dahil.
- Oturuma başlarken **`docs/GUNCEL_DURUM_2026-11-19.md`** dosyasını oku. Projenin parçaları, en güncel durum ve
  hangi ayrıntının hangi dosyada olduğu orada.
- Kalıcı notlar `docs/claude-notlar/hafiza/` altında. Dizin: `MEMORY.md`. Konuyla ilgili notu, iş yapmadan önce oku.
- "Yol haritasından devam et" denirse: `docs/ARMONIA_LINUX_YOL_HARITASI.md` → "▶ DEVAM NOKTASI" bölümü.
- Ana kod: `Uyap Haricen Giriş/ruyap/` (Rust). Arayüz eklentileri `ruyap/uyap_core/enjekte/` altında; asıl kopya bu.
- Commit, push ve yayın yalnız kullanıcı isteyince yapılır. Büyük işte önce plan yapılır ve onay alınır.

## Raspberry Pi'ye kurulum (bir kez)

Hafıza notlarını Claude Code'un yerel hafızasına kopyalamak isteğe bağlıdır; `CLAUDE.md` tek başına yeter:

```sh
D=~/.claude/projects/$(pwd | sed 's#[/._]#-#g')/memory
mkdir -p "$D" && cp docs/claude-notlar/hafiza/*.md "$D"/
mkdir -p .claude && cp -r docs/claude-notlar/claude-yerel/* .claude/
```
