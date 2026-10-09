"""
/zs İş Takip — Telegram grubundan iş açma: mesaj ayrıştırma (zs_tg_grup.py)
---------------------------------------------------------------------------
Yalnız Telegram mesaj sözlüğünü çözer; veritabanına/ağa dokunmaz (iş mantığı zs.py'de).

Kullanım (izin verilen grupta, botla özel sohbetten bağlanmış kişi):
  /is @ayse Tebligatı dosyaya işle           → Ayşe'ye iş açar
  (bir fotoğrafa/belgeye YANIT olarak) /is @ayse ...  → iş açılır, o dosya işe belge olarak eklenir
  /is Kendime not                            → etiket yoksa iş yazan kişiye açılır
  Botun "İş açıldı" mesajına yanıt (belge/fotoğraf/metin) → o işe belge / not eklenir

Telegram varlık (entity) konumları UTF-16 birimiyle verilir; Türkçe harfler tek birim ama
emoji iki birim olduğundan metin UTF-16'ya çevrilerek kesilir.
"""

import os
import re
import time

COMMAND = "is"
TG_MAX_DOWNLOAD = 20 * 1024 * 1024    # Bot API getFile sınırı


def allowed_groups():
    """ZS_TELEGRAM_GROUPS="-1001234,-1005678" → {-1001234, -1005678}. Boşsa grup özelliği kapalı."""
    out = set()
    for p in re.split(r"[,\s;]+", os.environ.get("ZS_TELEGRAM_GROUPS") or ""):
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out


def _u16(text):
    return (text or "").encode("utf-16-le")


def _slice(raw, off, ln):
    return raw[off * 2:(off + ln) * 2].decode("utf-16-le", errors="ignore")


def text_and_entities(msg):
    """Mesajın metni ve varlıkları (fotoğraf/belge mesajında açıklama = caption)."""
    if msg.get("text") is not None:
        return msg.get("text") or "", msg.get("entities") or []
    return msg.get("caption") or "", msg.get("caption_entities") or []


def parse_command(msg, bot_username):
    """'/is' komutuysa {"title", "description", "mentions"} döndürür, değilse None.
    mentions: sıralı, tekrarsız [("username", "ayse") | ("id", 12345, "Ayşe")]. Botun kendisi atlanır."""
    text, ents = text_and_entities(msg)
    raw = _u16(text)
    cmd = next((e for e in ents if e.get("type") == "bot_command" and e.get("offset") == 0), None)
    if not cmd:
        return None
    name = _slice(raw, 0, cmd["length"])[1:]
    base, _, target = name.partition("@")
    if base.lower() != COMMAND:
        return None
    bot = (bot_username or "").lower()
    if target and target.lower() != bot:
        return None             # başka bota yazılmış
    cut = [(0, cmd["length"])]
    mentions, seen = [], set()
    for e in sorted(ents, key=lambda e: e.get("offset", 0)):
        t = e.get("type")
        if t == "mention":
            uname = _slice(raw, e["offset"], e["length"]).lstrip("@").lower()
            if uname == bot:
                cut.append((e["offset"], e["length"]))
                continue
            key = ("username", uname)
        elif t == "text_mention" and (e.get("user") or {}).get("id"):
            u = e["user"]
            key = ("id", int(u["id"]))
            if u.get("is_bot"):
                continue
        else:
            continue
        cut.append((e["offset"], e["length"]))
        if key not in seen:
            seen.add(key)
            mentions.append(key if key[0] == "username" else
                            ("id", key[1], " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x)))
    # Komut ve etiketler çıkarılınca kalan metin: ilk satır başlık, gerisi açıklama.
    keep, pos = bytearray(), 0
    for off, ln in sorted(cut):
        keep += raw[pos * 2:off * 2]
        pos = max(pos, off + ln)
    keep += raw[pos * 2:]
    rest = keep.decode("utf-16-le", errors="ignore")
    lines = [re.sub(r"[ \t]+", " ", ln).strip(" \t:,-") for ln in rest.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    title = lines[0] if lines else ""
    desc = "\n".join(lines[1:]).strip()
    return {"title": title, "description": desc, "mentions": mentions}


def attachment(msg):
    """Mesajdaki dosya: {"file_id", "filename", "size"} ya da None. Desteklenmeyen tür (video, ses vb.)
    için {"unsupported": "video"}."""
    if not msg:
        return None
    if msg.get("document"):
        d = msg["document"]
        return {"file_id": d["file_id"], "filename": d.get("file_name") or "",
                "size": d.get("file_size") or 0}
    if msg.get("photo"):
        # Aynı fotoğrafın boyutları küçükten büyüğe gelir; en büyüğü alınır.
        p = max(msg["photo"], key=lambda x: (x.get("file_size") or 0, x.get("width", 0) * x.get("height", 0)))
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(msg.get("date") or time.time()))
        return {"file_id": p["file_id"], "filename": f"telegram_foto_{stamp}.jpg", "size": p.get("file_size") or 0}
    for kind in ("video", "animation", "audio", "voice", "video_note", "sticker"):
        if msg.get(kind):
            return {"unsupported": kind}
    return None


def sender_name(user):
    user = user or {}
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    return name or (("@" + user["username"]) if user.get("username") else "?")
