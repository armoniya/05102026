---
name: gemini-agy-ajan
description: "Gemini bu makinede Antigravity CLI `agy` ile etkileşimsiz ajan olarak kullanılabiliyor (test edildi); Linux dağıtımı yol haritası docs/ARMONIA_LINUX_YOL_HARITASI.md"
metadata:
  node_type: memory
  type: project
  originSessionId: e0725ac4-9963-414a-a02e-8caf295d0ac8
  modified: 2026-11-13T19:18:05.861Z
---

Kullanıcı Gemini'yi Antigravity ile kullanıyor. `agy` 1.2.8 kurulu (WinGet: `C:\Users\WİN10\AppData\Local\Microsoft\WinGet\Packages\Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe\agy.exe`), oturum açık. `agy -p "<görev>" --model <m> --mode plan --output-format json` 2026-11-13'te test edildi → `status: SUCCESS`, ~12.500 token sabit yük/çağrı. Modeller: Gemini 4 Argon / 4 Hydrogen / 3.8 Flash, Claude Opus 5.5 / Sonnet 5.5, GPT-OSS. Gemini CLI kişisel planlarda 2026-06-18'de emekli → PAL/zen MCP yolu geçersiz; doğrudan `agy` ana yol.

Kurulu araçlar (2026-11-13): Context7 MCP (global, `npx ctx7 setup --claude`); Trail of Bits eklentileri insecure-defaults, sharp-edges, rust-review, second-opinion (`agy` ile Gemini ikinci görüş). `zeroize-audit` bilerek KURULMADI: .mcp.json'u her oturumda sabitlenmemiş `git+https://github.com/oraios/serena`'yı uvx ile çalıştırıyor. `/plugin` Remote Control'de çalışmaz → `claude plugin install <ad>@trailofbits` CLI ile kurulur. WSL2 Debian 13 hazır (kullanıcı `utku`, sudo; root için `wsl -d Debian -u root`); `wsl.exe -- bash -c '...'` içinde değişken atamaları boşalıyor → komutları `C:\dev\*.sh` betik dosyasına yazıp `bash /mnt/c/dev/x.sh` ile çalıştır.

Proje skill'i `.claude/skills/gemini-ajan/SKILL.md` ve alt-ajan `.claude/agents/guvenlik-denetci.md` yazıldı (`.claude/` gitignore'da, yalnız yerel).

**Why:** Kullanıcı tüm skill/agent/MCP avantajlarını ve Gemini'yi ajan olarak kullanmak istiyor; Gemini gerçek dışı vaatlerde bulunur, çıktısı denetlenmeli.
**How to apply:** Toplu taslak işini `agy -p` ile Gemini'ye ver, dosyayı Claude yazsın, güvenlik çıktısı `guvenlik-denetci`'den geçsin; Gemini'ye müvekkil verisi/PIN gönderme. Plan: [[ruyap-port-guncel]] bitmiş ikili Linux imajına konacak; Rust dönüşümü plandan çıkarıldı.
