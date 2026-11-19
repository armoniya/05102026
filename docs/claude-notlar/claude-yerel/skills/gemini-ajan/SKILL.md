---
name: gemini-ajan
description: Gemini'ye (Google Antigravity CLI `agy`) etkileşimsiz görev devretme. Çok sayıda config taslağı (AppArmor, systemd, nftables, live-build), uzun doküman/referans repo özeti, ikinci görüş gibi toplu veya bağlamı geniş işlerde kullan. Güvenlik kararları ve son dosyalar Claude'da kalır.
---

# Gemini ajanı (`agy -p`)

Bu makinede Antigravity CLI kurulu ve oturumu açık (2026-11-13'te test edildi, `status: SUCCESS`).

## Komut

```bash
AGY="/c/Users/WİN10/AppData/Local/Microsoft/WinGet/Packages/Google.AntigravityCLI_Microsoft.Winget.Source_8wekyb3d8bbwe/agy"
SP="<oturum scratchpad dizini>/gemini"; mkdir -p "$SP"; cd "$SP"
timeout 900 "$AGY" -p "<GÖREV>" \
  --model gemini-4-argon-high \
  --mode plan \
  --output-format json \
  --print-timeout 600s
```

- JSON çıktı: `{"conversation_id", "status", "response", "usage"}`. `status` `SUCCESS` değilse sonucu kullanma.
- Aynı konuşmaya devam: `--conversation <conversation_id>`.
- Her zaman **scratchpad içinden** çalıştır (proje kökünden değil), böylece Gemini'nin çalışma alanı projeye açılmaz.
- Proje dosyası gerekiyorsa içeriği komut metnine göm veya kısa alıntı ver; `--add-dir` yalnız gerektiğinde ve salt-okuma talimatıyla.

## Model seçimi (`agy models`)
| İş | Model |
|---|---|
| Hızlı/basit: paket listesi, özet, biçim dönüştürme | `gemini-3.8-flash-medium` |
| Derin taslak: AppArmor/systemd/nftables, live-build config | `gemini-4-argon-high` |
| En zor analiz / ikinci görüş | `gemini-4-hydrogen-high` |

## Kurallar (zorunlu)
1. **Gemini taslak üretir, dosyayı Claude yazar.** Gemini'den dosya içeriğini yanıt metninde (kod bloğu olarak) iste; diske yazdırma.
2. **`--mode plan`** varsayılan. `--dangerously-skip-permissions` **asla** kullanılmaz.
3. **Gizlilik:** müvekkil verisi, e-imza PIN'i, parola, kişisel veri, kullanıcı e-postası Gemini'ye **gönderilmez**. Yalnız proje kodu/config.
4. **Maliyet:** her çağrıda ~12.500 token sabit yük var. Küçük işleri **tek çağrıda topla** (ör. 8 AppArmor profilini tek istekte iste).
5. **Doğrulama:** Gemini çıktısındaki her güvenlik iddiası ve config, `guvenlik-denetci` alt-ajanından geçmeden kullanılmaz. Gemini mutlak dil eğilimindedir ("%100", "imkansız", "askeri seviye") — bu ifadeler ayıklanır.
6. **Paralel iş:** Bağımsız taslaklar için birden çok `agy -p` çağrısı aynı mesajda paralel çalıştırılabilir (her biri ayrı alt klasörde).
7. Sonucu kullanıcıya aktarırken hangi kısmın Gemini taslağı, hangisinin Claude doğrulaması olduğunu belirt.

## Görev metni şablonu
```
Rol: Debian 13 sistem sertleştirme uzmanı.
Bağlam: <kısa bağlam, ilgili dosya alıntıları>
Görev: <net çıktı tanımı>
Biçim: Her dosyayı ayrı ```dosya:<yol>``` kod bloğunda ver. Açıklama en fazla 5 satır.
Kısıtlar: Dosya yazma/okuma yapma. Varsayım yaptığın yerleri "VARSAYIM:" ile işaretle.
Mutlak güvenlik ifadeleri kullanma.
```
