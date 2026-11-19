# Python → Rust (ruyap): is it worth it? Assessment and proposed path

## Context
The user had Gemini translate the 11 November state of the code into Rust as-is (`Uyap Haricen Giriş/ruyap/`, 46k lines, `CokluUyap.exe` built 13 Nov 15:05). Question: would continuing in Rust instead of Python help with speed and security? (They already know network speed won't change.)

## Findings (read-only inspection)
- **Size:** Python `uyap_core` + `uyap_app.py` ≈ 20.8k lines → Rust ≈ 46.4k lines. Only 5 tests on the Rust side (`lan_tls`, `runtime`, `yerel_kapi`); the Python tests (`test_*.py`, ~1.7k lines) were not carried over.
- **Not really "1:1":** example — `ruyap/src/udf_converter.rs:593 extract_pdf_text_lines` pulls text with a simple `Tj` pattern scan. The Python side does this with MuPDF (`fitz`). Compressed streams and font-encoded PDFs will come out empty or garbled. The other parts that depend on a library (e-signature PKCS#11 `akis_pkcs11.rs`, WebRTC `webrtc` crate instead of `aiortc`, tkinter → egui) carry similar risks.
- **Already behind:** after 11 November, the Python side got the mobile work (`enjekte/mobil.*`), the ~745-line uncommitted change in `uyap_proxy.py`, the single-interface changes, and so on. The injected JS is embedded as strings inside `uyap_proxy.rs`, so every UI change would have to be made twice.
- **Where the time goes:** the app mostly waits on the network (UYAP proxy, tunnel, WebRTC) and on in-browser JS (the enjekte files). Rust doesn't change either one.

## Answer
**Speed — small but real gains:**
- Startup: a PyInstaller exe unpacks itself first and loads the interpreter; Rust starts instantly. RAM use is also lower.
- CPU-heavy work (UDF→PDF, zipping bulk documents, crypto, large JSON) is several times faster. But these already take seconds, so the user may barely notice.
- What feels slow to the user (UYAP's response, the tunnel, how fast the page draws) stays the same.

**Security — mostly not where you'd expect:**
- Python is already memory-safe; Rust's famous advantage is over C/C++, not over Python.
- Real gains: (1) PyInstaller exes can be unpacked in minutes and the Python code read almost as-is; a Rust binary is much harder to reverse-engineer. (2) A single exe, no bundled interpreter. (3) Antivirus/SmartScreen false alarms are common with PyInstaller and rarer with Rust (useful while the MSI is unsigned).
- Real security bugs live in the logic: login, the local gate, the tunnel, tokens. A machine translation carries those bugs over unchanged and can add new ones in translation. A 46k-line untested translation is a **security risk in its own right** right now.

**Cost:** two code bases moving in parallel, each change done twice, and a port that hasn't been tested from end to end (e-signature login, WebRTC, PDF, the UI).

## Recommendation
Don't switch wholesale now; keep going on Python. Treat Rust as a measured, gradual option:
1. **Measure (1 session):** run the Python exe and the Rust exe side by side; compare startup time, RAM, and UDF→PDF time on the same 10 UDF files. If the gain is small, close the question.
2. **Check parity:** for the same inputs, compare UDF→PDF/UDF converter outputs and the proxy's injected HTML byte for byte; run the e-signature login and the tunnel once by hand.
3. **If Rust still makes sense, move one piece at a time:** start with the piece where speed or reverse-engineering resistance matters most and that can be tested on its own (e.g. `office_agent` tunnel/proxy, or `udf_pdf`). Keep the injected JS outside the Rust code too, as files under `enjekte/` (`include_str!` or read at runtime), so the UI keeps a single source.
4. Until then, mark `ruyap/` as "experimental" and keep it out of releases.

## Verification
- Startup: `Measure-Command` from launching each exe until port 8800 starts listening.
- RAM: `Get-Process` working set after 5 minutes of use.
- UDF→PDF: same input set, timing + visual comparison of the PDFs.
- Login/tunnel: e-signature login on a real card, then open UYAP from the phone via cokluuyap.com.
