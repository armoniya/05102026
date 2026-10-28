"""
/zs İş Takip — veri katmanı (zs_store.py)
-----------------------------------------
Ayrı kullanıcı sistemi + iş/alt iş + not + belge kaydı + olay geçmişi.

Kalıcılık: DATABASE_URL varsa PostgreSQL (hesaplarla aynı veritabanı, `zs_` önekli AYRI
tablolar — uyap_kv'ye dokunulmaz); yoksa yerel SQLite (zs.sqlite3, yalnız geliştirme için).
SQL '?' yer tutucusuyla yazılır; PostgreSQL'de '%s'e çevrilir.

Tüm zamanlar epoch saniye (int) olarak saklanır; gösterimde Europe/Istanbul'a çevrilir.
Yöntemler senkron; zs.py bunları asyncio.to_thread ile çağırır (tek bağlantı + kilit).
"""

import os
import re
import json
import time
import sqlite3
import datetime
import threading

import accounts

ROLES = ("admin", "user")
# Ana iş akışı: açık → devam → onayda → onaylandı (reddedilen iş devam'a döner).
# Özel iş onaya gönderilmez: sahibi doğrudan "tamamlandi" yapar.
STATUSES = ("acik", "devam", "onayda", "onaylandi", "tamamlandi")
DONE_STATUSES = ("onaylandi", "tamamlandi")
# Alt işler yalnız işaretlenir.
SUB_STATUSES = ("acik", "tamam")
# Öncelik: 0 düşük, 1 normal, 2 acil (yalnız ana işte).
PRIORITIES = (0, 1, 2)

SQLITE_PATH = os.path.join(accounts.DATA_DIR, "zs.sqlite3")


class ZsError(Exception):
    """Kullanıcıya gösterilebilir hata (mesajı Türkçe)."""


# Güncellemede "bu alan verilmedi, eskisi kalsın" işareti.
KEEP = object()


def _now() -> int:
    return int(time.time())


def _gun_sonra(gun, saat=17):
    """Bugünden gun gün sonra saat:00 (Türkiye saati, UTC+3) — sonraki işin son tarihi."""
    tr = 3 * 3600
    gece = (_now() + tr) // 86400 * 86400 - tr
    return gece + int(gun) * 86400 + saat * 3600


def _tarih_17(s, saat=17):
    """"YYYY-AA-GG" → o gün saat:00 (Türkiye saati, UTC+3); geçersizse ZsError."""
    try:
        gun = datetime.date.fromisoformat(str(s or "").strip()[:10])
    except ValueError:
        raise ZsError("Kesinleşme günü geçersiz.")
    if not 2000 <= gun.year <= 2100:
        raise ZsError("Kesinleşme günü geçersiz.")
    return (gun - datetime.date(1970, 1, 1)).days * 86400 + (saat - 3) * 3600


def _tg_uname(v):
    """Telegram kullanıcı adı karşılaştırma biçimi: @ siz, küçük harf; boşsa None."""
    v = (v or "").strip().lstrip("@").lower()
    return v[:64] or None


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS zs_users (
        id {pk}, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
        pw_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
        sess_epoch INTEGER NOT NULL DEFAULT 0, created_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS zs_tasks (
        id {pk}, parent_id BIGINT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        assignee_id BIGINT, created_by BIGINT, due_at BIGINT, status TEXT NOT NULL,
        sort_order BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_tasks_parent ON zs_tasks(parent_id)",
    """CREATE TABLE IF NOT EXISTS zs_notes (
        id {pk}, task_id BIGINT NOT NULL, author_id BIGINT, body TEXT NOT NULL,
        note_at BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_notes_task ON zs_notes(task_id)",
    """CREATE TABLE IF NOT EXISTS zs_files (
        id {pk}, task_id BIGINT NOT NULL, uploader_id BIGINT, filename TEXT NOT NULL,
        mime TEXT NOT NULL, size BIGINT NOT NULL, storage_key TEXT NOT NULL, sha256 TEXT NOT NULL,
        created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_files_task ON zs_files(task_id)",
    """CREATE TABLE IF NOT EXISTS zs_events (
        id {pk}, task_id BIGINT, actor_id BIGINT, kind TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{{}}',
        created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_events_task ON zs_events(task_id)",
    # Devir kaydı: devir anındaki en büyük kimlikler (cut_*) + hangi türlerin gizlendiği. Gizlenen
    # türden, kimliği cut_* değerine eşit/küçük kayıtları YALNIZ devreden (by_user) görür.
    """CREATE TABLE IF NOT EXISTS zs_transfers (
        id {pk}, task_id BIGINT NOT NULL, from_user BIGINT, to_user BIGINT NOT NULL, by_user BIGINT NOT NULL,
        hide_subtasks INTEGER NOT NULL DEFAULT 0, hide_notes INTEGER NOT NULL DEFAULT 0,
        hide_files INTEGER NOT NULL DEFAULT 0, hide_history INTEGER NOT NULL DEFAULT 0,
        cut_task BIGINT NOT NULL DEFAULT 0, cut_note BIGINT NOT NULL DEFAULT 0,
        cut_file BIGINT NOT NULL DEFAULT 0, cut_event BIGINT NOT NULL DEFAULT 0, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_transfers_task ON zs_transfers(task_id)",
    # Ek atananlar (role='assignee') ve o işe özel onay yetkilileri (role='approver').
    """CREATE TABLE IF NOT EXISTS zs_members (
        id {pk}, task_id BIGINT NOT NULL, user_id BIGINT NOT NULL, role TEXT NOT NULL,
        added_by BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_members_task ON zs_members(task_id)",
    # Ek süre talepleri: status = bekliyor | kabul | ret
    """CREATE TABLE IF NOT EXISTS zs_extensions (
        id {pk}, task_id BIGINT NOT NULL, requester_id BIGINT NOT NULL, old_due BIGINT, new_due BIGINT NOT NULL,
        reason TEXT NOT NULL, status TEXT NOT NULL, decided_by BIGINT, decision_note TEXT NOT NULL DEFAULT '',
        decided_at BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_extensions_task ON zs_extensions(task_id)",
    # Kişi başına "en son gördüğü olay" — listede "yeni" işareti için.
    """CREATE TABLE IF NOT EXISTS zs_seen (
        user_id BIGINT NOT NULL, task_id BIGINT NOT NULL, last_event BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, task_id))""",
    # Denetim kaydı (geliştirici için): arayüzde/API'de GÖRÜNMEZ, uygulama hiçbir yerde silmez/güncellemez.
    # Her değiştiren istek + giriş/çıkış + belge indirme. before = işlemden önceki hâl (silmede tam kopya).
    """CREATE TABLE IF NOT EXISTS zs_audit (
        id {pk}, at BIGINT NOT NULL, user_id BIGINT, username TEXT, role TEXT, ip TEXT, user_agent TEXT,
        method TEXT NOT NULL, path TEXT NOT NULL, action TEXT NOT NULL, target_id BIGINT,
        ok INTEGER NOT NULL, status INTEGER NOT NULL, error TEXT, data TEXT, result TEXT, before TEXT,
        ms INTEGER)""",
    "CREATE INDEX IF NOT EXISTS zs_audit_at ON zs_audit(at)",
    # Bildirim kanalları: cihaz başına Web Push aboneliği; kişi başına Telegram sohbeti.
    """CREATE TABLE IF NOT EXISTS zs_push_subs (
        id {pk}, user_id BIGINT NOT NULL, endpoint TEXT NOT NULL UNIQUE, p256dh TEXT NOT NULL, auth TEXT NOT NULL,
        user_agent TEXT, created_at BIGINT NOT NULL, last_ok BIGINT, fails INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS zs_push_subs_user ON zs_push_subs(user_id)",
    """CREATE TABLE IF NOT EXISTS zs_telegram (
        user_id BIGINT PRIMARY KEY, chat_id BIGINT NOT NULL, tg_name TEXT, linked_at BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS zs_tg_codes (
        code TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at BIGINT NOT NULL)""",
    # Botun grupta "İş açıldı" diye yazdığı mesaj → iş. Bu mesaja verilen yanıt (belge/not) o işe eklenir.
    """CREATE TABLE IF NOT EXISTS zs_tg_msgs (
        chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, task_id BIGINT NOT NULL, created_at BIGINT NOT NULL,
        PRIMARY KEY (chat_id, message_id))""",
    # Hatırlatıcılar: remind_at gelince user_id'ye bildirim; sent_at dolunca bir daha gönderilmez.
    # task_id boşsa işten bağımsız hatırlatıcı (title kendi başlığı). task_id = ana iş; alt işe kurulduysa
    # sub_id = alt işin kimliği.
    """CREATE TABLE IF NOT EXISTS zs_reminders (
        id {pk}, task_id BIGINT, sub_id BIGINT, user_id BIGINT NOT NULL, created_by BIGINT NOT NULL,
        remind_at BIGINT NOT NULL, note TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        sent_at BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_reminders_user ON zs_reminders(user_id)",
    "CREATE INDEX IF NOT EXISTS zs_reminders_task ON zs_reminders(task_id)",
    "CREATE INDEX IF NOT EXISTS zs_reminders_due ON zs_reminders(sent_at, remind_at)",
    # Masaüstünden (CokluUyap) web'e şifresiz geçiş: tek kullanımlık, kısa ömürlü kod.
    """CREATE TABLE IF NOT EXISTS zs_sso_codes (
        code TEXT PRIMARY KEY, user_id BIGINT NOT NULL, expires_at BIGINT NOT NULL)""",
    # Büro iş şablonları (hazır olanlar kodda: HAZIR_SABLONLAR). Model: "yapılacak iş.md".
    """CREATE TABLE IF NOT EXISTS zs_templates (
        id {pk}, office_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        due_days INTEGER, priority INTEGER NOT NULL DEFAULT 1, taraf TEXT, action TEXT,
        created_by BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_templates_office ON zs_templates(office_id)",
    # Özel etiketler (hazırlar kodda: HAZIR_ETIKETLER). owner_id boş = büroya açık; dolu = yalnız sahibine.
    """CREATE TABLE IF NOT EXISTS zs_tags (
        id {pk}, office_id TEXT, owner_id BIGINT, name TEXT NOT NULL, color TEXT NOT NULL DEFAULT '',
        created_by BIGINT, created_at BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS zs_tags_office ON zs_tags(office_id)",
    # Ana işin etiketleri: tag = hazır anahtar ("h-tebligat") ya da zs_tags kimliği ("12").
    """CREATE TABLE IF NOT EXISTS zs_task_tags (
        task_id BIGINT NOT NULL, tag TEXT NOT NULL, added_by BIGINT, created_at BIGINT NOT NULL,
        PRIMARY KEY (task_id, tag))""",
]

# Sonradan eklenen sütunlar: tablo → ((sütun, tür), ...)
_ADDED_COLS = {
    "zs_tasks": (("is_private", "INTEGER NOT NULL DEFAULT 0"), ("priority", "INTEGER NOT NULL DEFAULT 1"),
                 ("submitted_at", "BIGINT"),
                 # Büro (ana hesaplardaki office_id; eski /zs kayıtlarında boş) + bağlı UYAP dosyası.
                 ("office_id", "TEXT"), ("dosya_id", "TEXT"), ("birim_id", "TEXT"), ("birim_adi", "TEXT"),
                 ("dosya_no", "TEXT"), ("dosya_tur", "TEXT"),
                 # UYAP'ın dosya kartını açmak için kartın kendi verisi (JSON; türleri korunur).
                 ("dosya_uyap", "TEXT"),
                 # Dosyadaki belirli bir belgeye bağlı iş (UYAP evrakId + ağaçtaki adı).
                 ("evrak_id", "TEXT"), ("evrak_adi", "TEXT"),
                 # İşin yapıldığı taraf (dosya kartının taraf listesinden; ör. borçlulardan biri).
                 ("taraf_adi", "TEXT"),
                 # Tebligatın PTT barkodu (iş açılırken belgeden alınır; "Tebliğ durumu kontrol").
                 ("barkod", "TEXT"),
                 # İşin açıldığı şablon ("h-tebligat" | büro şablonu "b-12" | '' = eşleşmedi) ve
                 # şablondan gelen süreç + aşama (bkz. HAZIR_SURECLER; elle düzeltilebilir).
                 ("sablon", "TEXT"), ("surec", "TEXT"), ("asama", "TEXT"),
                 # Kontrol sonucu (olumlu | olumsuz | bekliyor) — onaya gönderirken / tamamlarken seçilir.
                 ("sonuc", "TEXT")),
    "zs_reminders": (("sub_id", "BIGINT"), ("title", "TEXT NOT NULL DEFAULT ''")),
    # Aşama 2: kontrol sorusu + sonuç dalları (JSON, bkz. TEMPLATE_SONUCLAR).
    # asama: "surec/asama" (ör. "icra-kesinlesme/teblig"); etiketler: JSON etiket anahtarı listesi.
    "zs_templates": (("kontrol", "TEXT"), ("sonuclar", "TEXT"), ("asama", "TEXT"), ("etiketler", "TEXT")),
    # Telegram kullanıcı adı (küçük harf, @ siz) — grupta @etiketle kişi bulmak için.
    "zs_telegram": (("tg_username", "TEXT"),),
    # Özel etiketin ana etiketi (dosya türü, ör. "e-icra"); boş = genel etiket.
    "zs_tags": (("parent", "TEXT"),),
    # Ana CokluUyap hesabına bağ (ahmet@ofis) + o hesabın bürosu. Boşsa eski /zs kullanıcısı.
    "zs_users": (("account_username", "TEXT"), ("office_id", "TEXT")),
}
# Sütunlar eklendikten sonra kurulan indeksler.
_POST_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS zs_users_account ON zs_users(account_username) "
    "WHERE account_username IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS zs_tasks_office_dosya ON zs_tasks(office_id, dosya_id)",
    "CREATE INDEX IF NOT EXISTS zs_tasks_office_birim_dosya ON zs_tasks(office_id, birim_id, dosya_no)",
    "CREATE INDEX IF NOT EXISTS zs_tasks_office_surec ON zs_tasks(office_id, surec, asama)",
)
# UYAP dosya alanları (görevde).
DOSYA_FIELDS = ("dosya_id", "birim_id", "birim_adi", "dosya_no", "dosya_tur", "dosya_uyap", "evrak_id", "evrak_adi",
                "taraf_adi", "barkod")
# UYAP'ın dosya kartı (dosya-sorgula-popup) bu alanlarla açılır — UYAP'ın kendi arama sonucundan
# kartı açarken verdiği nesneyle aynı anahtarlar.
UYAP_KART_ALANLARI = ("dosyaId", "dosyaNo", "birimId", "birimAdi", "dosyaTurKod", "dosyaTurKodAciklama",
                      "dosyaDurum", "dosyaDurumKod", "birimTuru3")

# Hazır iş şablonları (her büroya açık, değiştirilemez). Büro şablonları zs_templates'te.
# taraf: işin bir tarafa yapılıp yapılmadığı (borclu | alacakli | herhangi | None).
# action: önerilen UYAP aksiyonu (Aşama 3, henüz yürütülmez) — bkz. "yapılacak iş.md".
# kontrol + sonuclar (Aşama 2): iş bitirilirken sorulan soru ve sonuca göre önerilen sonraki iş
# {olumlu|olumsuz|bekliyor: {baslik, gun, ad}}; baslik None = aynı iş yeniden (ör. bekliyor → +3 gün);
# ad: sonucun bu şablondaki adı (ör. mazbatada olumlu "Tebliğ edildi", olumsuz "İade").
def _sonuc(olumlu=None, olumsuz=None, bekliyor=None):
    def one(x):
        if x is None:
            return None
        if isinstance(x, int):
            return {"baslik": None, "gun": x, "ad": None}
        return {"baslik": x[0], "gun": x[1], "ad": x[2] if len(x) > 2 else None}
    return {"olumlu": one(olumlu), "olumsuz": one(olumsuz), "bekliyor": one(bekliyor)}


# Hazır süreçler (kodda, değiştirilemez). Aşamalar sıralıdır; iş aşamasını şablondan alır. Her aşamanın
# alt etiketi (süreç etiketinin altında) işe aşamasıyla birlikte takılır. dal "iade": tebligat iade
# dönünce yeniden tebliğ için izlenen yan yol; talep kabul edilince süreç baştan (yeni tur) başlar.
# otomatik: ileride ofis ajanının (UyapMasa) yapacağı denetimin anahtarı (Aşama 4) — şimdi yürütülmez.
def _asama(key, title, otomatik, dal=None):
    return {"key": key, "title": title, "etiket": f"e-ik-{key}", "otomatik": otomatik, "dal": dal}


HAZIR_SURECLER = (
    {"key": "icra-kesinlesme", "title": "Tebligat → Kesinleştirme", "tur": "icra", "etiket": "e-icra-kesinlesme",
     "taraf": "borclu", "asamalar": (
        _asama("teb-olustu", "Tebligat oluşturuldu", "db:tebligat_olustu"),
        _asama("ptt", "Tebligat PTT'ye verildi", "db:ptt_kabul"),
        _asama("yolda", "Tebligat yola çıktı", "db:ptt_yolda"),
        _asama("teblig", "Tebliğ kontrol", "db:teblig"),
        _asama("mazbata", "Mazbata dönüşü", "db:mazbata"),
        _asama("mernis", "Mernis adresine tebliğ", "uyap:talep_mernis", "iade"),
        _asama("21-2", "21/2 adresine tebliğ", "uyap:talep_21_2", "iade"),
        _asama("talep-kabul", "Talep kabul", "uyap:talep_kabul", "iade"),
        _asama("itiraz", "İtiraz kontrol", "db:itiraz"),
        _asama("kes-tarih", "Kesinleşme tarihi", "db:kesinlesme_tarihi"),
        _asama("kes-gonder", "Kesinleştirme talebi gönderme", "uyap:kesinlestirme_gonder"),
        _asama("kes-kabul", "Kesinleştirme talebi kabul", "uyap:kesinlestirme_talebi"),
        _asama("kesinlesti", "Kesinleşme", "uyap:sorgu_kesinlesme"),
    )},
)
# Dosya türleri: her biri bir ANA etiket ("e-icra" …). Tür, dosyanın tür açıklamasından, yoksa birim adından
# bulunur (ilk eşleşen; uyap_proxy DOSYA_TURLERI ile aynı sıra, CBS ayrı). Dosyaya bağlı işe ana etiket
# kendiliğinden takılır; alt etiketler ve süreçler yalnız kendi türündeki işe takılır.
DOSYA_TURLERI = (
    ("arabuluculuk", "Arabuluculuk", "#0d9488", r"arabulucu"),
    ("icra", "İcra", "#4f46e5", r"icra dosya|icra dairesi|icra müdürlüğü|^icra"),
    ("cbs", "CBS", "#b45309", r"başsavcı|savcılı|soruşturma"),
    ("ceza", "Ceza", "#be123c", r"ceza"),
    ("idare", "İdare", "#0369a1", r"idare|vergi mahkeme|danıştay"),
    ("hukuk", "Hukuk", "#15803d", r"hukuk|asliye|sulh|aile|iş mahkeme|tüketici|ticaret|kadastro"),
)


def _tr_lower(s):
    return str(s or "").replace("İ", "i").replace("I", "ı").lower()


def dosya_turu(dosya_tur=None, birim_adi=None):
    """Dosyanın türü (DOSYA_TURLERI anahtarı) ya da None."""
    for metin in (_tr_lower(dosya_tur).strip(), _tr_lower(birim_adi).strip()):
        if metin:
            for key, _, _, desen in DOSYA_TURLERI:
                if re.search(desen, metin):
                    return key
    return None


def task_turu(t):
    """İşin dosya türü; dosyasız işte None (her etiket / süreç uyar)."""
    return dosya_turu(t.get("dosya_tur"), t.get("birim_adi")) if t and t.get("dosya_id") else None


# Hazır etiketler (her büroya açık). Anahtarlar "e-" önekli; özel etiketin anahtarı zs_tags kimliğidir ("12").
# parent: ana etiket (dosya türü) anahtarı; boş = her türde kullanılabilen genel etiket.
HAZIR_ETIKETLER = tuple({"key": f"e-{k}", "name": ad, "color": renk, "parent": None}
                        for k, ad, renk, _ in DOSYA_TURLERI) + (
    # 2. katman: icra süreçleri ve iş grupları; 3. katman: sürecin aşamaları (aşamadan gelir, elle takılmaz).
    {"key": "e-icra-kesinlesme", "name": "Tebligat – Kesinleştirme", "color": "#4338ca", "parent": "e-icra",
     "surec": "icra-kesinlesme"},
    *({"key": a["etiket"], "name": a["title"], "color": "#be123c" if a["dal"] else "#2563eb" if i < 5 else "#7c3aed",
       "parent": s["etiket"], "surec": s["key"], "asama": a["key"]}
      for s in HAZIR_SURECLER for i, a in enumerate(s["asamalar"])),
    {"key": "e-haciz", "name": "Haciz", "color": "#dc2626", "parent": "e-icra"},
    {"key": "e-tahsilat", "name": "Tahsilat", "color": "#059669", "parent": "e-icra"},
    {"key": "e-muvekkil", "name": "Müvekkil bildirimi", "color": "#d97706", "parent": None},
    {"key": "e-acil-dosya", "name": "Acil dosya", "color": "#e11d48", "parent": None},
)
ANA_ETIKETLER = {f"e-{k}": k for k, _, _, _ in DOSYA_TURLERI}
# Etiket ağacı en çok 3 katman: dosya türü → süreç / grup → alt etiket.
ETIKET_DERINLIK = 3


def tag_zinciri(key, tags):
    """[key, üstü, üstünün üstü, …] — tags: {anahtar: etiket}; döngüye karşı sınırlı."""
    out = []
    while key and key not in out and len(out) <= ETIKET_DERINLIK:
        out.append(key)
        key = (tags.get(key) or {}).get("parent")
    return out


def tag_turu(key, tags):
    """Etiketin ait olduğu dosya türü (kök ana etiket); genel etikette None."""
    return next((ANA_ETIKETLER[k] for k in tag_zinciri(key, tags) if k in ANA_ETIKETLER), None)


def tag_uyar(tag, tur):
    """Etiket (tur alanı hesaplanmış) bu türdeki işe takılabilir mi? Dosyasız işe (tur None) her etiket uyar."""
    return tur is None or tag.get("tur") is None or tag["tur"] == tur


_HAZIR_AGAC = {e["key"]: e for e in HAZIR_ETIKETLER}
HAZIR_ETIKETLER = tuple(dict(e, tur=tag_turu(e["key"], _HAZIR_AGAC)) for e in HAZIR_ETIKETLER)

ETIKET_RENKLERI = ("#2563eb", "#7c3aed", "#dc2626", "#059669", "#d97706", "#e11d48", "#0891b2", "#4b5563")

# asama: "surec/asama" (bkz. HAZIR_SURECLER); etiketler: işe varsayılan olarak eklenen etiket anahtarları
# (aşamanın alt etiketi ayrıca aşamadan gelir). eski: önceki adlar — eski işler başlıktan bunlarla da bağlanır.
# Zincir (Tebligat → Kesinleştirme): her kontrol olumlu çıkınca sıradaki aşamanın işi açılır; mazbata iade
# dönerse Mernis / 21/2 adresine tebliğ → talep kabul → süreç baştan (yeni tebligat turu).
_KES = "icra-kesinlesme/"


# kesinlesme: bu sonuç dalında kullanıcıya kesinleşme günü sorulur; girilirse o güne "Kesinleşme tarihi
# kontrol" işi de açılır (gün elle: ilamlı/ilamsız, e-tebligat ayrımı dosyadan güvenilir çıkmaz).
KESINLESME_SABLONU = "h-kes-tarih"


def _kontrol(id, title, asama, kontrol, sonuclar, description, action=None, due_days=2, eski=(), kesinlesme=None):
    return {"id": id, "title": title, "due_days": due_days, "priority": 1, "taraf": "borclu",
            "description": description, "action": action, "kontrol": kontrol, "asama": _KES + asama,
            "etiketler": [], "sonuclar": sonuclar, "eski": tuple(eski), "kesinlesme": kesinlesme}


HAZIR_SABLONLAR = (
    _kontrol("h-odeme-emri", "Tebligat oluşturuldu kontrol", "teb-olustu",
             "Ödeme emri tebligatı oluşturuldu mu?",
             _sonuc(("PTT'ye verildi kontrol", 2, "Oluşturuldu"), (None, 1, "Oluşturulmadı"), 2),
             "Ödeme emri tebligatının UYAP'ta oluşturulup gönderildiğini evrak listesinden kontrol et.",
             "ekran:evrak", 3, eski=("Ödeme emri kontrol",)),
    # action "ptt:barkod": iş açılırken tebligatın PTT barkodu da alınır (görevde barkod alanı);
    # UYAP'taki iş satırında tek tuşla PTT sorgusu yapılır (ofisin /__uyap_agent__/ptt_sorgu ucu).
    _kontrol("h-ptt-teslim", "PTT'ye verildi kontrol", "ptt",
             "Barkodda tebligat PTT'ye verilmiş görünüyor mu (“kayıt yok” değil)?",
             _sonuc(("Yola çıktı kontrol", 1, "PTT'ye verildi"), (None, 2, "Kayıt yok"), 2),
             "PTT barkodunda tebligatın “kayıt yok” durumundan çıkıp PTT'ye verildiğini kontrol et.",
             "ptt:barkod", 3, eski=("PTT teslim kontrol",)),
    _kontrol("h-yolda", "Yola çıktı kontrol", "yolda",
             "Barkodda tebligat yola çıkmış (dağıtımda) görünüyor mu?",
             _sonuc(("Tebliğ kontrol", 3, "Yola çıktı"), None, 2),
             "PTT barkodunda tebligatın dağıtıma çıktığını kontrol et.", "ptt:barkod", 2),
    _kontrol("h-teblig-durum", "Tebliğ kontrol", "teblig",
             "Barkodda tebliğ sonucu (tebliğ ya da iade) belli oldu mu?",
             _sonuc(("Mazbata dönüşü kontrol", 3, "Tebliğ edildi"), ("Mazbata dönüşü kontrol", 3, "İade"),
                    (None, 2, "Henüz sonuç yok")),
             "Tebligatın PTT barkodundan tebliğ durumunu sorgula. Tebliğ edildiyse kesinleşme gününü gir.",
             "ptt:barkod", 3, eski=("Tebliğ durumu kontrol", "Tebligat kontrol", "Barkod takip"),
             kesinlesme="olumlu"),
    _kontrol("h-mazbata", "Mazbata dönüşü kontrol", "mazbata",
             "Mazbata icra dosyasına döndü mü; tebliğ mi, iade mi?",
             _sonuc(("İtiraz kontrol", 7, "Tebliğ edildi"), ("Mernis adresine tebliğ", 1, "İade"),
                    (None, 3, "Henüz dönmedi")),
             "Tebliğ mazbatasının icra dosyasına dönüp dönmediğini evrak listesinden kontrol et.",
             "ekran:evrak", 3, eski=("Mazbata kontrol",)),
    # İade dalı: yeniden tebliğ talebi → talep kabul → süreç baştan.
    _kontrol("h-mernis", "Mernis adresine tebliğ", "mernis",
             "Mernis adresine tebligat talebi gönderildi mi?",
             _sonuc(("Talep kabul kontrol", 2, "Talep gönderildi"), ("21/2 adresine tebliğ", 1, "Mernis adresi aynı"), 1),
             "İade dönen tebligat için borçlunun Mernis adresine tebligat çıkarılmasını talep et. Tebligat zaten "
             "Mernis adresine yapıldıysa 21/2 tebliğe geç.", "talep:adres_arastirmasi", 1),
    _kontrol("h-21-2", "21/2 adresine tebliğ", "21-2",
             "Tebligat Kanunu 21/2'ye göre tebligat talebi gönderildi mi?",
             _sonuc(("Talep kabul kontrol", 2, "Talep gönderildi"), None, 1),
             "Mernis adresine iade dönen tebligat için 21/2 tebligat talebi gönder.", None, 1,
             eski=("21/2 akıbet kontrol",)),
    _kontrol("h-talep-kabul", "Talep kabul kontrol", "talep-kabul",
             "Yeniden tebligat talebi icra dairesince kabul edildi mi?",
             _sonuc(("Tebligat oluşturuldu kontrol", 2, "Kabul edildi"), ("Mernis adresine tebliğ", 1, "Reddedildi"), 2),
             "Talebin kabul edildiğini evrak listesinden kontrol et; kabul edilince tebligat süreci baştan izlenir.",
             "ekran:evrak", 2),
    _kontrol("h-itiraz", "İtiraz kontrol", "itiraz",
             "Süresinde itiraz edilmedi mi?",
             _sonuc(("Kesinleşme tarihi kontrol", 0, "İtiraz yok"), ("Müvekkile bildirim", 1, "İtiraz var"), 2),
             "İtiraz süresi dolunca borçlunun itiraz edip etmediğini evrak / safahattan kontrol et.", "ekran:evrak", 7),
    _kontrol(KESINLESME_SABLONU, "Kesinleşme tarihi kontrol", "kes-tarih",
             "Kesinleşme günü geldi ve süresinde itiraz edilmedi mi?",
             _sonuc(("Kesinleştirme talebi gönder", 0, "İtiraz yok"), ("Müvekkile bildirim", 1, "İtiraz var"),
                    (None, 2, "Henüz gelmedi")),
             "Kesinleşme gününe kadar itiraz edilip edilmediğini evrak / safahattan kontrol et; itiraz yoksa "
             "kesinleştirme talebini gönder.", "ekran:evrak", 1),
    _kontrol("h-kes-gonder", "Kesinleştirme talebi gönder", "kes-gonder",
             "Kesinleştirme talebi gönderildi mi?",
             _sonuc(("Kesinleştirme talebi kabul kontrol", 2, "Gönderildi"), None, 1),
             "İcra dairesine takibin kesinleştirilmesi talebini gönder.", "talep:kesinlestirme", 1),
    _kontrol("h-kes-talep", "Kesinleştirme talebi kabul kontrol", "kes-kabul",
             "Kesinleştirme talebi kabul edildi mi?",
             _sonuc(("Kesinleşme kontrol", 1, "Kabul edildi"), ("Kesinleştirme talebi gönder", 1, "Reddedildi"), 2),
             "Kesinleştirme talebinin icra dairesince kabul edilip edilmediğini kontrol et.", "ekran:evrak", 2,
             eski=("Kesinleştirme talebi kontrol",)),
    _kontrol("h-kesinlesme", "Kesinleşme kontrol", "kesinlesti",
             "Takip kesinleşti mi (sorgu yapılabiliyor mu)?",
             _sonuc(("Haciz talepleri", 2, "Kesinleşti"), ("Kesinleştirme talebi kabul kontrol", 2, "Kesinleşmedi"), 3),
             "Sorgu ekranında bir sorgu dene: “kesinleşmediği için sorgu yapılamaz” uyarısı gelmiyorsa takip "
             "borçlu yönünden kesinleşmiştir.", "ekran:safahat", 2),
    {"id": "h-adres", "title": "Adres araştırması", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "MERNİS / SGK / GSM adres araştırması yap, yeni adrese tebligat çıkar.",
     "action": "talep:adres_arastirmasi", "kontrol": "Yeni adres bulundu mu?", "asama": None, "etiketler": [],
     "sonuclar": _sonuc(("Talep kabul kontrol", 3), ("21/2 adresine tebliğ", 1), 3)},
    {"id": "h-takyidat", "title": "Takyidat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "Araç / tapu takyidat sorgu sonucunu kontrol et.", "action": "sorgu:takyidat",
     "kontrol": "Borçlu adına araç / taşınmaz var mı?", "asama": None, "etiketler": ["e-haciz"],
     "sonuclar": _sonuc(("Haciz / yakalama talebi", 2), ("Mal varlığı sorgusu", 3), 2)},
    {"id": "h-reddiyat", "title": "Reddiyat kontrol", "due_days": 2, "priority": 1, "taraf": None,
     "description": "Reddiyatın hesaba geçip geçmediğini kontrol et.", "action": None,
     "kontrol": "Reddiyat hesaba geçti mi?", "asama": None, "etiketler": ["e-tahsilat"],
     "sonuclar": _sonuc(("Müvekkile bildirim", 1), ("Muhasebe ile görüş", 1), 2)},
    {"id": "h-tahsilat", "title": "Tahsilat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "Tahsilatı kontrol et, müvekkile bildir.", "action": None,
     "kontrol": "Tahsilat hesaba geçti mi?", "asama": None, "etiketler": ["e-tahsilat"],
     "sonuclar": _sonuc(("Müvekkile bildirim", 1), ("Muhasebe ile görüş", 1), 2)},
    {"id": "h-aranacak", "title": "Borçlu aranacak", "due_days": 1, "priority": 1, "taraf": "borclu",
     "description": "Borçluya ulaş, sonucu nota yaz.", "action": None,
     "kontrol": "Borçluya ulaşıldı mı?", "asama": None, "etiketler": [],
     "sonuclar": _sonuc(("Ödeme planı takibi", 7), 2, None)},
    {"id": "h-haciz", "title": "Haciz talepleri", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "SGK, banka, araç ve tapu haciz taleplerini hazırla.", "action": None,
     "kontrol": None, "asama": None, "etiketler": ["e-haciz"], "sonuclar": _sonuc()},
    {"id": "h-muvekkil", "title": "Müvekkile bildirim", "due_days": 1, "priority": 1, "taraf": None,
     "description": "Dosyadaki gelişmeyi müvekkile bildir.", "action": None,
     "kontrol": None, "asama": None, "etiketler": ["e-muvekkil"], "sonuclar": _sonuc()},
)


TEMPLATE_TARAF = ("borclu", "alacakli", "herhangi")
TEMPLATE_SONUCLAR = ("olumlu", "olumsuz", "bekliyor")


def clean_sonuclar(d):
    """{olumlu|olumsuz|bekliyor: {baslik, gun, ad} | None} → temiz sözlük (bilinmeyen anahtarlar atılır)."""
    out = {}
    for k in TEMPLATE_SONUCLAR:
        x = d.get(k) if isinstance(d, dict) else None
        if not isinstance(x, dict):
            out[k] = None
            continue
        baslik = " ".join(str(x.get("baslik") or "").split())[:200] or None
        gun = x.get("gun")
        try:
            gun = None if gun in (None, "") else int(gun)
        except (TypeError, ValueError):
            raise ZsError("Sonraki iş süresi gün sayısı olmalı.")
        if gun is not None and not 0 <= gun <= 365:
            raise ZsError("Sonraki iş süresi 0–365 gün olmalı.")
        ad = " ".join(str(x.get("ad") or "").split())[:40] or None
        out[k] = {"baslik": baslik, "gun": gun, "ad": ad} if baslik or gun is not None or ad else None
    return out


_ASAMALAR = {f"{s['key']}/{a['key']}": (s["key"], a["key"]) for s in HAZIR_SURECLER for a in s["asamalar"]}
_HAZIR_ETIKET = {e["key"]: e for e in HAZIR_ETIKETLER}
_HAZIR_SABLON = {t["id"]: t for t in HAZIR_SABLONLAR}


def asama_coz(v):
    """"surec/asama" → (surec, asama); boş → (None, None); tanımsızsa ZsError."""
    v = str(v or "").strip()
    if not v:
        return None, None
    if v not in _ASAMALAR:
        raise ZsError("Tanımsız süreç aşaması.")
    return _ASAMALAR[v]


def asama_etiketi(surec, asama):
    """Aşamanın alt etiketi (ör. icra-kesinlesme/mazbata → "e-ik-mazbata") ya da None."""
    s = next((x for x in HAZIR_SURECLER if x["key"] == surec), None)
    return next((a.get("etiket") for a in (s or {}).get("asamalar", ()) if a["key"] == asama), None)


# Aşama etiketleri işin aşamasından gelir: elle takılmaz / çıkarılmaz, şablona konmaz.
ASAMA_ETIKETLERI = {a["etiket"] for s in HAZIR_SURECLER for a in s["asamalar"]}


def ad_key(s):
    """Ad karşılaştırma anahtarı. Python'da yapılır: SQLite LOWER() Türkçe harfleri (Ş/ş, İ/i) eşlemez."""
    return " ".join(str(s or "").replace("İ", "i").replace("I", "i").lower().replace("ı", "i").split())


def is_adi(title):
    """İş başlığının şablon kısmı: "HAMZA OĞUZ – Tebligat kontrol" → "Tebligat kontrol"."""
    title = title or ""
    i = title.rfind(" – ")
    return title[i + 3:] if i >= 0 else title


def office_of(user) -> str:
    """Kullanıcının bürosu; eski /zs kullanıcıları için '' (kendi ortak havuzları)."""
    return (user or {}).get("office_id") or ""


def clean_dosya(d):
    """{dosya_id, birim_id, birim_adi, dosya_no, dosya_tur, evrak_id, evrak_adi, taraf_adi, uyap: {kart verisi}} →
    temizlenmiş sözlük; dosya_id yoksa None. uyap'tan yalnız UYAP_KART_ALANLARI'ndaki düz değerler
    (metin/sayı) alınır. evrak_id isteğe bağlı: iş dosyanın o belgesine bağlanır."""
    if not isinstance(d, dict):
        return None
    out = {k: (str(d.get(k) or "").strip()[:200] or None) for k in DOSYA_FIELDS if k != "dosya_uyap"}
    out["barkod"] = re.sub(r"\D", "", out["barkod"] or "")[:30] or None
    u = d.get("uyap")
    kart = {k: (v[:200] if isinstance(v, str) else v) for k, v in (u.items() if isinstance(u, dict) else ())
            if k in UYAP_KART_ALANLARI and isinstance(v, (str, int, float)) and not isinstance(v, bool)}
    out["dosya_uyap"] = json.dumps(kart, ensure_ascii=False) if kart.get("dosyaId") else None
    if not out["evrak_id"]:
        out["evrak_adi"] = None
    return out if out["dosya_id"] else None

# Gizlenebilen türler: (zs_transfers bayrağı, kesim sütunu)
_HIDE_KINDS = {"task": ("hide_subtasks", "cut_task"), "note": ("hide_notes", "cut_note"),
               "file": ("hide_files", "cut_file"), "event": ("hide_history", "cut_event"),
               # Geçmiş gösterilse bile gizlenen türün olay satırları ("not ekledi" vb.) da gizlenir,
               # yoksa kaydın varlığı geçmişten anlaşılırdı.
               "ev_task": ("hide_subtasks", "cut_event"), "ev_note": ("hide_notes", "cut_event"),
               "ev_file": ("hide_files", "cut_event")}
_EVENT_KIND_OF = {"note": "ev_note", "file": "ev_file", "file_deleted": "ev_file",
                  "subtask": "ev_task", "subtask_deleted": "ev_task"}


class ZsStore:
    def __init__(self, dsn: str = None, sqlite_path: str = None):
        self._lock = threading.RLock()
        self._dsn = dsn if dsn is not None else accounts.DATABASE_URL
        self._sqlite_path = sqlite_path or SQLITE_PATH
        self._pg = bool(self._dsn)
        self._conn = None
        self._connect()
        self._ensure_schema()
        self.backfill_sablon()

    # ── bağlantı ─────────────────────────────────────────────────────────────────────
    def _connect(self):
        if self._pg:
            import psycopg2  # lazy: yalnız DB modunda gerekir
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = False
        else:
            self._conn = sqlite3.connect(self._sqlite_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

    @property
    def backend_name(self) -> str:
        return "PostgreSQL" if self._pg else f"SQLite ({self._sqlite_path})"

    def _sql(self, q: str) -> str:
        return q.replace("?", "%s") if self._pg else q

    def _run(self, fn):
        """fn(cur) işlemini tek transaction'da çalıştırır; PG bağlantısı düştüyse bir kez yeniden bağlanır."""
        with self._lock:
            for attempt in (0, 1):
                try:
                    cur = self._conn.cursor()
                    try:
                        out = fn(cur)
                        self._conn.commit()
                        return out
                    except Exception:
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        raise
                    finally:
                        try:
                            cur.close()
                        except Exception:
                            pass
                except ZsError:
                    raise
                except Exception as e:
                    if self._pg and attempt == 0 and _is_conn_error(e):
                        try:
                            self._conn.close()
                        except Exception:
                            pass
                        self._connect()
                        continue
                    raise

    def _rows(self, cur):
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _one(self, cur):
        rows = self._rows(cur)
        return rows[0] if rows else None

    def _q(self, cur, q, params=()):
        cur.execute(self._sql(q), tuple(params))
        return cur

    def _insert(self, cur, q, params=()):
        cur.execute(self._sql(q + " RETURNING id"), tuple(params))
        return int(cur.fetchone()[0])

    def _ensure_schema(self):
        pk = "BIGSERIAL PRIMARY KEY" if self._pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

        def fn(cur):
            for stmt in _SCHEMA:
                cur.execute(stmt.format(pk=pk))
            # Sonradan eklenen sütunlar (eski tablolara).
            for table, cols_added in _ADDED_COLS.items():
                if self._pg:
                    for col, typ in cols_added:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {typ}")
                else:
                    cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
                    for col, typ in cols_added:
                        if col not in cols:
                            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            for stmt in _POST_INDEXES:
                cur.execute(stmt)
        self._run(fn)

    # ── kullanıcılar ─────────────────────────────────────────────────────────────────
    @staticmethod
    def _public_user(u):
        if not u:
            return None
        return {"id": u["id"], "username": u["username"], "display_name": u["display_name"],
                "role": u["role"], "active": bool(u["active"]), "created_at": u["created_at"],
                "account_username": u.get("account_username"), "office_id": u.get("office_id")}

    def user_count(self) -> int:
        return self._run(lambda cur: self._q(cur, "SELECT COUNT(*) FROM zs_users").fetchone()[0])

    def get_user(self, user_id):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,))))

    def list_users(self, office=None):
        """office verilirse yalnız o büronun kullanıcıları ('' = eski /zs havuzu); None → hepsi."""
        q, params = "SELECT * FROM zs_users", ()
        if office is not None:
            q, params = q + " WHERE COALESCE(office_id, '') = ?", (office,)
        rows = self._run(lambda cur: self._rows(self._q(cur, q + " ORDER BY active DESC, display_name", params)))
        return [self._public_user(u) for u in rows]

    # ── ana hesaplarla (accounts) bağ ────────────────────────────────────────────────
    def user_by_account(self, account_username):
        return self._run(lambda cur: self._one(self._q(
            cur, "SELECT * FROM zs_users WHERE account_username = ?", (account_username,))))

    def sync_account(self, account_username, office_id, display_name, active, is_master):
        """Ana hesabın İş Listesi satırını bulur ya da açar ve ana hesapla eşitler (ad, büro, etkinlik).
        Rol: büro sahibi (master) her zaman yönetici; üye ilk açılışta kullanıcı, sonra yöneticinin
        verdiği rolde kalır. Satırı döndürür."""
        display_name = (display_name or "").strip()[:80] or account_username
        role_new = "admin" if is_master else "user"

        def fn(cur):
            u = self._one(self._q(cur, "SELECT * FROM zs_users WHERE account_username = ?", (account_username,)))
            if not u:
                # zs_users.username benzersiz ve [a-z0-9._-] — ana hesap adından türetilir, çakışırsa sayı eklenir.
                base = "".join(c if (c.isalnum() and c.isascii()) or c in "._-" else "." for c in
                               account_username.lower().replace("@", "."))[:36] or "hesap"
                name, i = base, 2
                while self._one(self._q(cur, "SELECT id FROM zs_users WHERE username = ?", (name,))):
                    name, i = f"{base}-{i}", i + 1
                uid = self._insert(cur, "INSERT INTO zs_users (username, display_name, pw_hash, role, active, sess_epoch, "
                                        "created_at, account_username, office_id) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                                   (name, display_name, self._AUTO_PW, role_new, 1 if active else 0, _now(),
                                    account_username, office_id))
                return self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (uid,)))
            sets, params = [], []
            if u["display_name"] != display_name:
                sets.append("display_name = ?"); params.append(display_name)
            if (u["office_id"] or "") != (office_id or ""):
                sets.append("office_id = ?"); params.append(office_id)
            if bool(u["active"]) != bool(active):
                sets.append("active = ?"); params.append(1 if active else 0)
            if is_master and u["role"] != "admin":
                sets.append("role = ?"); params.append("admin")
            if sets:
                self._q(cur, f"UPDATE zs_users SET {', '.join(sets)} WHERE id = ?", params + [u["id"]])
                u = self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (u["id"],)))
            return u
        return self._run(fn)

    def legacy_users(self):
        """Ana hesaba bağlanmamış eski /zs kullanıcıları (eşleştirme ekranı için)."""
        rows = self._run(lambda cur: self._rows(self._q(
            cur, "SELECT * FROM zs_users WHERE account_username IS NULL ORDER BY active DESC, display_name")))
        return [self._public_user(u) for u in rows]

    _USAGE_SQL = ("(SELECT COUNT(*) FROM zs_tasks WHERE created_by = {u} OR assignee_id = {u}) + "
                  "(SELECT COUNT(*) FROM zs_members WHERE user_id = {u}) + "
                  "(SELECT COUNT(*) FROM zs_events WHERE actor_id = {u}) + "
                  "(SELECT COUNT(*) FROM zs_reminders WHERE user_id = {u} OR created_by = {u})")

    # Ana hesaptan kendiliğinden açılan satırın parola özeti boştur; eski /zs kullanıcısınınki doludur.
    _AUTO_PW = "{}"

    def used_accounts(self):
        """Eşleştirmede seçilemeyen ana hesaplar: eski bir kullanıcıya bağlanmış ya da İş Listesi'nde
        izi (iş/olay/hatırlatıcı) olanlar. Yalnız girip hiçbir şey yapmamış hesabın boş satırı devralınır."""
        q = (f"SELECT u.account_username FROM zs_users u WHERE u.account_username IS NOT NULL "
             f"AND (u.pw_hash <> ? OR {self._USAGE_SQL.format(u='u.id')} > 0)")
        return {r[0] for r in self._run(lambda cur: self._q(cur, q, (self._AUTO_PW,)).fetchall())}

    def link_account(self, user_id, account_username, office_id):
        """Eski /zs kullanıcısını ana hesaba bağlar; görev geçmişi, Telegram ve cihaz kayıtları kalır.
        Kullanıcının açtığı ya da kendisine atanmış büro'suz işler (alt işleriyle) o büroya geçer.
        Ana hesap İş Listesi'ne önceden girip boş bir satır açtıysa o satır silinir; satırda iz
        varsa birleştirme yapılmaz (hata)."""
        def fn(cur):
            u = self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,)))
            if not u:
                raise ZsError("Kullanıcı bulunamadı.")
            if u["account_username"]:
                raise ZsError("Bu kullanıcı zaten bir ana hesaba bağlı.")
            other = self._one(self._q(cur, "SELECT * FROM zs_users WHERE account_username = ?", (account_username,)))
            if other:
                oid = other["id"]
                used = self._q(cur, "SELECT " + self._USAGE_SQL.format(u="?"), (oid,) * 6).fetchone()[0]
                if used or other["pw_hash"] != self._AUTO_PW:
                    raise ZsError(f"{account_username} İş Listesi'nde zaten kullanılmış; eşleştirilemez.")
                for q in ("DELETE FROM zs_push_subs WHERE user_id = ?", "DELETE FROM zs_telegram WHERE user_id = ?",
                          "DELETE FROM zs_tg_codes WHERE user_id = ?", "DELETE FROM zs_seen WHERE user_id = ?",
                          "DELETE FROM zs_sso_codes WHERE user_id = ?", "DELETE FROM zs_users WHERE id = ?"):
                    self._q(cur, q, (oid,))
            self._q(cur, "UPDATE zs_users SET account_username = ?, office_id = ?, sess_epoch = sess_epoch + 1 "
                         "WHERE id = ?", (account_username, office_id, user_id))
            self._q(cur, "UPDATE zs_tasks SET office_id = ? WHERE office_id IS NULL AND parent_id IS NULL "
                         "AND (created_by = ? OR assignee_id = ? OR id IN (SELECT task_id FROM zs_members WHERE user_id = ? AND role = 'assignee'))",
                    (office_id, user_id, user_id, user_id))
            self._q(cur, "UPDATE zs_tasks SET office_id = (SELECT p.office_id FROM zs_tasks p WHERE p.id = zs_tasks.parent_id) "
                         "WHERE parent_id IS NOT NULL AND office_id IS NULL")
            return self._public_user(self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,))))
        return self._run(fn)

    # ── masaüstünden web'e tek kullanımlık geçiş kodu ─────────────────────────────────
    def sso_code_new(self, user_id, ttl=60):
        import secrets
        code = secrets.token_urlsafe(24)

        def fn(cur):
            self._q(cur, "DELETE FROM zs_sso_codes WHERE expires_at < ?", (_now(),))
            self._q(cur, "INSERT INTO zs_sso_codes (code, user_id, expires_at) VALUES (?, ?, ?)",
                    (code, user_id, _now() + ttl))
        self._run(fn)
        return code

    def sso_code_use(self, code):
        """Kodu tüketir; geçerliyse kullanıcı kimliği, değilse None."""
        def fn(cur):
            r = self._one(self._q(cur, "SELECT * FROM zs_sso_codes WHERE code = ?", (code,)))
            self._q(cur, "DELETE FROM zs_sso_codes WHERE code = ?", (code,))
            return r["user_id"] if r and r["expires_at"] >= _now() else None
        return self._run(fn)

    def create_user(self, username, display_name, password, role="user"):
        username = (username or "").strip().lower()
        display_name = (display_name or "").strip() or username
        if not username or len(username) > 40 or not all(c.isalnum() or c in "._-" for c in username):
            raise ZsError("Kullanıcı adı yalnız harf, rakam ve . _ - içerebilir (en fazla 40 karakter).")
        if role not in ROLES:
            raise ZsError("Geçersiz rol.")
        err = accounts.password_policy_error(password)
        if err:
            raise ZsError(err)
        rec = json.dumps(accounts._hash_password(password))

        def fn(cur):
            if self._one(self._q(cur, "SELECT id FROM zs_users WHERE username = ?", (username,))):
                raise ZsError("Bu kullanıcı adı zaten var.")
            uid = self._insert(cur, "INSERT INTO zs_users (username, display_name, pw_hash, role, active, "
                                    "sess_epoch, created_at) VALUES (?, ?, ?, ?, 1, 0, ?)",
                               (username, display_name[:80], rec, role, _now()))
            return self._public_user(self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (uid,))))
        return self._run(fn)

    def authenticate(self, username, password):
        """Başarılıysa kullanıcı satırını döndürür; değilse None. Kullanıcı yokken de PBKDF2 maliyeti ödenir."""
        username = (username or "").strip().lower()
        u = self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_users WHERE username = ?", (username,))))
        if not u:
            accounts._verify_password(password or "", accounts._DUMMY_PW_REC)
            return None
        try:
            rec = json.loads(u["pw_hash"])
        except Exception:
            return None
        if not accounts._verify_password(password or "", rec):
            return None
        if not u["active"]:
            return None
        return u

    def set_password(self, user_id, password):
        err = accounts.password_policy_error(password)
        if err:
            raise ZsError(err)
        rec = json.dumps(accounts._hash_password(password))

        def fn(cur):
            self._q(cur, "UPDATE zs_users SET pw_hash = ?, sess_epoch = sess_epoch + 1 WHERE id = ?",
                    (rec, user_id))
            if cur.rowcount == 0:
                raise ZsError("Kullanıcı bulunamadı.")
        self._run(fn)

    def update_user(self, user_id, display_name=None, role=None):
        def fn(cur):
            u = self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,)))
            if not u:
                raise ZsError("Kullanıcı bulunamadı.")
            if display_name is not None:
                dn = display_name.strip()
                if not dn:
                    raise ZsError("Ad Soyad boş olamaz.")
                self._q(cur, "UPDATE zs_users SET display_name = ? WHERE id = ?", (dn[:80], user_id))
            if role is not None and role != u["role"]:
                if role not in ROLES:
                    raise ZsError("Geçersiz rol.")
                if u["role"] == "admin":
                    n = self._q(cur, "SELECT COUNT(*) FROM zs_users WHERE role = 'admin' AND active = 1 AND id <> ? "
                                     "AND COALESCE(office_id, '') = ?", (user_id, u["office_id"] or "")).fetchone()[0]
                    if n == 0:
                        raise ZsError("Son etkin yönetici kullanıcıya çevrilemez.")
                self._q(cur, "UPDATE zs_users SET role = ? WHERE id = ?", (role, user_id))
            return self._public_user(self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,))))
        return self._run(fn)

    def set_active(self, user_id, active: bool):
        def fn(cur):
            u = self._one(self._q(cur, "SELECT * FROM zs_users WHERE id = ?", (user_id,)))
            if not u:
                raise ZsError("Kullanıcı bulunamadı.")
            if not active and u["role"] == "admin":
                n = self._q(cur, "SELECT COUNT(*) FROM zs_users WHERE role = 'admin' AND active = 1 AND id <> ? "
                                 "AND COALESCE(office_id, '') = ?", (user_id, u["office_id"] or "")).fetchone()[0]
                if n == 0:
                    raise ZsError("Son etkin yönetici kapatılamaz.")
            self._q(cur, "UPDATE zs_users SET active = ?, sess_epoch = sess_epoch + 1 WHERE id = ?",
                    (1 if active else 0, user_id))
        self._run(fn)

    # ── olay günlüğü ─────────────────────────────────────────────────────────────────
    def _event(self, cur, task_id, actor_id, kind, **detail):
        self._q(cur, "INSERT INTO zs_events (task_id, actor_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, actor_id, kind, json.dumps(detail, ensure_ascii=False), _now()))

    # ── işler ────────────────────────────────────────────────────────────────────────
    def get_task(self, task_id):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,))))

    def root_of(self, task):
        """Alt işse ana işini, ana işse kendisini döndürür."""
        if task and task["parent_id"]:
            return self.get_task(task["parent_id"])
        return task

    def _roles(self, cur, task_id, primary=None):
        """{"assignees": [asıl atanan + ek atananlar], "approvers": [işe özel onay yetkilileri]}"""
        if primary is None:
            row = self._q(cur, "SELECT assignee_id FROM zs_tasks WHERE id = ?", (task_id,)).fetchone()
            primary = row[0] if row else None
        out = {"assignees": [primary] if primary else [], "approvers": []}
        for uid, role in self._q(cur, "SELECT user_id, role FROM zs_members WHERE task_id = ? ORDER BY id",
                                 (task_id,)).fetchall():
            key = "assignees" if role == "assignee" else "approvers"
            if uid not in out[key]:
                out[key].append(uid)
        return out

    def task_roles(self, task_id):
        return self._run(lambda cur: self._roles(cur, task_id))

    def visible_to(self, root, user) -> bool:
        """Ana iş bu kullanıcıya görünür mü? Özel iş yalnız açanına görünür. Yönetici diğer her
        şeyi görür; kullanıcı, kendisine atanan ana işi ya da altında kendisine atanmış bir alt
        iş bulunan ana işi görür."""
        if not root:
            return False
        if (root.get("office_id") or "") != office_of(user):
            return False                    # başka büronun işi
        if root.get("is_private"):
            return root["created_by"] == user["id"]
        if user["role"] == "admin" or root["assignee_id"] == user["id"]:
            return True
        n = self._run(lambda cur: self._q(
            cur, "SELECT (SELECT COUNT(*) FROM zs_tasks WHERE parent_id = ? AND assignee_id = ?) + "
                 "(SELECT COUNT(*) FROM zs_members WHERE task_id = ? AND user_id = ?)",
            (root["id"], user["id"], root["id"], user["id"])).fetchone()[0])
        return n > 0

    # ── devir gizliliği ──────────────────────────────────────────────────────────────
    def _cutoffs(self, cur, root_ids, viewer_id):
        """{root_id: {"task": n, "note": n, "file": n, "event": n}} — bu izleyiciden gizli olan
        en büyük kimlikler (kimliği bundan küçük/eşit kayıtlar gizli). Devreden kişi kendi
        gizlediğini görmeye devam eder."""
        out = {}
        if not root_ids:
            return out
        marks = ",".join("?" * len(root_ids))
        rows = self._rows(self._q(cur, f"SELECT * FROM zs_transfers WHERE task_id IN ({marks}) AND by_user <> ?",
                                  list(root_ids) + [viewer_id]))
        for r in rows:
            c = out.setdefault(r["task_id"], {k: 0 for k in _HIDE_KINDS})
            for kind, (flag, cut) in _HIDE_KINDS.items():
                if r[flag]:
                    c[kind] = max(c[kind], int(r[cut]))
        return out

    def item_hidden(self, root_id, kind, item_id, viewer_id) -> bool:
        cut = self._run(lambda cur: self._cutoffs(cur, [root_id], viewer_id)).get(root_id)
        return bool(cut and item_id <= cut[kind])

    def list_tree(self, user, filtre="benim", dosya_id=None, dosya_key=None):
        """Ana işleri alt işleriyle (ve sayılarla) döndürür; özel işler ve devirde gizlenen
        kayıtlar bu kullanıcıya göre süzülür. Yalnız kullanıcının bürosu; dosya_key=(birim_id,
        dosya_no) verilirse yalnız o UYAP dosyasına bağlı işler. UYAP'ın dosyaId'si oturuma bağlı
        (her girişte değişir) — kalıcı eşleştirme birim + dosya no ile; dosya_id eski istemciler için."""
        uid = user["id"]
        # Her sorgunun başı: büro (+ isteğe bağlı dosya) + özel iş süzgeci.
        base = "parent_id IS NULL AND COALESCE(office_id, '') = ? AND (is_private = 0 OR created_by = ?)"
        bparams = [office_of(user), uid]
        if dosya_key:
            base += " AND birim_id = ? AND dosya_no = ?"
            bparams += [str(dosya_key[0]), str(dosya_key[1])]
        elif dosya_id:
            base += " AND dosya_id = ?"
            bparams.append(str(dosya_id))

        def fn(cur):
            mine = ("(assignee_id = ? OR id IN (SELECT parent_id FROM zs_tasks WHERE parent_id IS NOT NULL "
                    "AND assignee_id = ?) OR id IN (SELECT task_id FROM zs_members WHERE user_id = ?))")
            sel = f"SELECT * FROM zs_tasks WHERE {base}"
            order = " ORDER BY sort_order, id"
            # Onay listesi: onaya gönderilen işler + karar bekleyen ek süre / ertesi güne aktarma talepleri.
            waiting = ("(status = 'onayda' OR id IN (SELECT task_id FROM zs_extensions WHERE status = 'bekliyor'))")
            if filtre == "onay" and user["role"] == "admin":
                roots = self._rows(self._q(cur, sel + f" AND {waiting}" + order, bparams))
            elif filtre == "onay":
                roots = self._rows(self._q(cur, sel + f" AND {waiting} AND {mine}" + order,
                                           bparams + [uid, uid, uid]))
            elif filtre == "tumu" and user["role"] == "admin":
                roots = self._rows(self._q(cur, sel + order, bparams))
            else:
                roots = self._rows(self._q(cur, sel + f" AND {mine}" + order, bparams + [uid, uid, uid]))
            if not roots:
                return []
            ids = [r["id"] for r in roots]
            marks = ",".join("?" * len(ids))
            cuts = self._cutoffs(cur, ids, uid)
            subs = self._rows(self._q(cur, f"SELECT * FROM zs_tasks WHERE parent_id IN ({marks}) "
                                           "ORDER BY sort_order, id", ids))
            notes = self._q(cur, f"SELECT task_id, id FROM zs_notes WHERE task_id IN ({marks})", ids).fetchall()
            files = self._q(cur, f"SELECT task_id, id FROM zs_files WHERE task_id IN ({marks})", ids).fetchall()
            nocut = {k: 0 for k in _HIDE_KINDS}

            def count(rows, kind):
                out = {}
                for tid, iid in rows:
                    if iid > cuts.get(tid, nocut)[kind]:
                        out[tid] = out.get(tid, 0) + 1
                return out
            nc, fc = count(notes, "note"), count(files, "file")
            last_foreign = dict(self._q(cur, f"SELECT task_id, MAX(id) FROM zs_events WHERE task_id IN ({marks}) "
                                             "AND (actor_id IS NULL OR actor_id <> ?) GROUP BY task_id",
                                        ids + [uid]).fetchall())
            seen = dict(self._q(cur, f"SELECT task_id, last_event FROM zs_seen WHERE user_id = ? AND task_id IN ({marks})",
                                [uid] + ids).fetchall())
            members = self._q(cur, f"SELECT task_id, user_id, role FROM zs_members WHERE task_id IN ({marks}) "
                                   "ORDER BY id", ids).fetchall()
            # Karar bekleyen ek süre / aktarma talebi ve onaya gönderme notu: UYAP'taki yan panel
            # İş Listesi'ne gitmeden onaylayabilsin diye.
            pend = {r["task_id"]: r for r in self._rows(self._q(
                cur, f"SELECT id, task_id, requester_id, old_due, new_due, reason, created_at FROM zs_extensions "
                     f"WHERE task_id IN ({marks}) AND status = 'bekliyor'", ids))}
            onayda = [r["id"] for r in roots if r["status"] == "onayda"]
            gnot = {}
            if onayda:
                om = ",".join("?" * len(onayda))
                # Desen parametreyle: sorgu metnindeki çıplak % psycopg2'de yer tutucu sayılır (IndexError → 500).
                for tid, body in self._q(cur, f"SELECT task_id, body FROM zs_notes WHERE task_id IN ({om}) "
                                              "AND body LIKE ? ORDER BY id",
                                         onayda + ["Onaya gönderme notu: %"]).fetchall():
                    gnot[tid] = body[len("Onaya gönderme notu: "):]
            rem = dict(self._q(cur, f"SELECT task_id, MIN(remind_at) FROM zs_reminders WHERE user_id = ? AND sent_at IS NULL "
                                    f"AND task_id IN ({marks}) GROUP BY task_id", [uid] + ids).fetchall())
            tags = self._task_tags(cur, ids, user)
            by_parent = {}
            for s_ in subs:
                if s_["id"] > cuts.get(s_["parent_id"], nocut)["task"]:
                    by_parent.setdefault(s_["parent_id"], []).append(s_)
            for r in roots:
                r["subtasks"] = by_parent.get(r["id"], [])
                r["note_count"] = nc.get(r["id"], 0)
                r["file_count"] = fc.get(r["id"], 0)
                lf = int(last_foreign.get(r["id"]) or 0)
                r["unseen"] = lf > int(seen.get(r["id"], 0)) and lf > cuts.get(r["id"], nocut)["event"]
                r["assignees"] = [r["assignee_id"]] if r["assignee_id"] else []
                r["approvers"] = []
                r["ext_pending"] = r["id"] in pend
                r["ext"] = pend.get(r["id"])
                r["submit_note"] = gnot.get(r["id"])
                r["reminder_at"] = rem.get(r["id"])
                r["tags"] = tags.get(r["id"], [])
                r["tur"] = task_turu(r)
            idx = {r["id"]: r for r in roots}
            for tid, muid, role in members:
                key = "assignees" if role == "assignee" else "approvers"
                if muid not in idx[tid][key]:
                    idx[tid][key].append(muid)
            return roots
        return self._run(fn)

    def task_detail(self, task_id, viewer=None):
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t:
                return None
            cut = (self._cutoffs(cur, [task_id], viewer["id"]).get(task_id) if viewer else None) or \
                {k: 0 for k in _HIDE_KINDS}
            t["subtasks"] = self._rows(self._q(cur, "SELECT * FROM zs_tasks WHERE parent_id = ? AND id > ? "
                                                    "ORDER BY sort_order, id", (task_id, cut["task"])))
            t["notes"] = self._rows(self._q(cur, "SELECT * FROM zs_notes WHERE task_id = ? AND id > ? "
                                                 "ORDER BY created_at DESC, id DESC", (task_id, cut["note"])))
            t["files"] = self._rows(self._q(cur, "SELECT id, task_id, uploader_id, filename, mime, size, created_at "
                                                 "FROM zs_files WHERE task_id = ? AND id > ? ORDER BY id DESC",
                                                 (task_id, cut["file"])))
            ev = self._rows(self._q(cur, "SELECT * FROM zs_events WHERE task_id = ? AND id > ? ORDER BY id DESC",
                                    (task_id, cut["event"])))
            out = []
            for e in ev:
                try:
                    e["detail"] = json.loads(e["detail"] or "{}")
                except Exception:
                    e["detail"] = {}
                k = _EVENT_KIND_OF.get(e["kind"]) or ("ev_task" if e["kind"] == "created" and e["detail"].get("sub") else None)
                if k and e["id"] <= cut[k]:
                    continue
                out.append(e)
            t["events"] = out
            t.update(self._roles(cur, task_id, t["assignee_id"]))
            t["extensions"] = self._rows(self._q(cur, "SELECT * FROM zs_extensions WHERE task_id = ? ORDER BY id DESC",
                                                 (task_id,)))
            t["tags"] = self._task_tags(cur, [task_id], viewer).get(task_id, [])
            t["tur"] = task_turu(t)
            return t
        return self._run(fn)

    # ── ek atanan / onay yetkilisi ───────────────────────────────────────────────────
    def add_member(self, actor, task_id, user_id, role, note):
        note = (note or "").strip()
        if role not in ("assignee", "approver"):
            raise ZsError("Geçersiz rol.")
        if not note:
            raise ZsError("Ekleme notu yazın.")

        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or t["parent_id"]:
                raise ZsError("Kişi yalnız ana işe eklenebilir.")
            if t["is_private"]:
                raise ZsError("Özel işe kişi eklenemez; önce görünür yapın ya da devredin.")
            uid = self._check_assignee(cur, user_id, actor)
            if uid is None:
                raise ZsError("Kişi seçin.")
            roles = self._roles(cur, task_id, t["assignee_id"])
            if uid in roles["assignees" if role == "assignee" else "approvers"]:
                raise ZsError("Bu kişi zaten bu rolde.")
            self._q(cur, "INSERT INTO zs_members (task_id, user_id, role, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
                    (task_id, uid, role, actor["id"], _now()))
            self._event(cur, task_id, actor["id"], "member_added", user_id=uid, role=role, note=note[:2000])
            self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (_now(), task_id))
            return self._roles(cur, task_id, t["assignee_id"])
        return self._run(fn)

    def remove_member(self, actor, task_id, user_id, role, note=""):
        def fn(cur):
            self._q(cur, "DELETE FROM zs_members WHERE task_id = ? AND user_id = ? AND role = ?", (task_id, user_id, role))
            if cur.rowcount == 0:
                raise ZsError("Bu kişi bu rolde değil (asıl atanan için “Düzenle” ya da “Devret” kullanın).")
            self._event(cur, task_id, actor["id"], "member_removed", user_id=user_id, role=role,
                        note=(note or "").strip()[:2000])
            return self._roles(cur, task_id)
        return self._run(fn)

    # ── ek süre talebi ───────────────────────────────────────────────────────────────
    def request_extension(self, actor, task_id, new_due, reason):
        reason = (reason or "").strip()
        if not reason:
            raise ZsError("Ek süre gerekçesini yazın.")
        if not new_due:
            raise ZsError("Yeni son tarihi seçin.")

        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or t["parent_id"]:
                raise ZsError("Ek süre yalnız ana iş için istenir.")
            if actor["id"] not in self._roles(cur, task_id, t["assignee_id"])["assignees"]:
                raise ZsError("Ek süreyi yalnız işe atanan kişi isteyebilir.")
            if t["status"] in DONE_STATUSES:
                raise ZsError("Bitmiş iş için ek süre istenmez.")
            if t["due_at"] and new_due <= t["due_at"]:
                raise ZsError("Yeni tarih mevcut son tarihten sonra olmalı.")
            if self._q(cur, "SELECT COUNT(*) FROM zs_extensions WHERE task_id = ? AND status = 'bekliyor'",
                       (task_id,)).fetchone()[0]:
                raise ZsError("Bu iş için karar bekleyen bir ek süre talebi zaten var.")
            now = _now()
            eid = self._insert(cur, "INSERT INTO zs_extensions (task_id, requester_id, old_due, new_due, reason, status, "
                                    "created_at) VALUES (?, ?, ?, ?, ?, 'bekliyor', ?)",
                               (task_id, actor["id"], t["due_at"], new_due, reason[:2000], now))
            self._event(cur, task_id, actor["id"], "ext_requested", ext_id=eid, old_due=t["due_at"], new_due=new_due,
                        reason=reason[:2000])
            self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (now, task_id))
            return self._one(self._q(cur, "SELECT * FROM zs_extensions WHERE id = ?", (eid,)))
        return self._run(fn)

    def get_extension(self, ext_id):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_extensions WHERE id = ?", (ext_id,))))

    def decide_extension(self, actor, ext_id, approve, note=""):
        note = (note or "").strip()

        def fn(cur):
            x = self._one(self._q(cur, "SELECT * FROM zs_extensions WHERE id = ?", (ext_id,)))
            if not x or x["status"] != "bekliyor":
                raise ZsError("Karar bekleyen talep bulunamadı.")
            if not self._can_approve(cur, actor, x["task_id"]):
                raise ZsError("Bu talebe yalnız yönetici ya da işin onay yetkilisi karar verir.")
            if not approve and not note:
                raise ZsError("Ret gerekçesi yazın.")
            now = _now()
            self._q(cur, "UPDATE zs_extensions SET status = ?, decided_by = ?, decision_note = ?, decided_at = ? "
                         "WHERE id = ?", ("kabul" if approve else "ret", actor["id"], note[:2000], now, ext_id))
            if approve:
                self._q(cur, "UPDATE zs_tasks SET due_at = ?, updated_at = ? WHERE id = ?", (x["new_due"], now, x["task_id"]))
            else:
                self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (now, x["task_id"]))
            self._event(cur, x["task_id"], actor["id"], "ext_approved" if approve else "ext_rejected",
                        ext_id=ext_id, new_due=x["new_due"], old_due=x["old_due"], note=note[:2000],
                        requester_id=x["requester_id"])
            return self._one(self._q(cur, "SELECT * FROM zs_extensions WHERE id = ?", (ext_id,)))
        return self._run(fn)

    def _can_approve(self, cur, actor, task_id):
        return actor["role"] == "admin" or actor["id"] in self._roles(cur, task_id)["approvers"]

    def mark_seen(self, user_id, task_id):
        def fn(cur):
            last = int(self._q(cur, "SELECT MAX(id) FROM zs_events WHERE task_id = ?", (task_id,)).fetchone()[0] or 0)
            self._q(cur, "INSERT INTO zs_seen (user_id, task_id, last_event) VALUES (?, ?, ?) "
                         "ON CONFLICT (user_id, task_id) DO UPDATE SET last_event = excluded.last_event",
                    (user_id, task_id, last))
        self._run(fn)

    def transfer(self, actor, task_id, to_user_id, hide: dict):
        """Ana işi başkasına devreder (to_user_id), ya da to_user_id None ise özel işi kimseye atamadan
        görünür yapar. hide: {"subtasks","notes","files","history"} → True ise o ana kadarki o türdeki
        kayıtlar işlemi yapan dışındaki herkesten gizlenir. Özel iş her iki durumda da görünür olur."""
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or t["parent_id"]:
                raise ZsError("Yalnız ana iş devredilebilir.")
            publish = to_user_id is None
            if publish:
                if not t["is_private"]:
                    raise ZsError("İş zaten görünür.")
                to = t["assignee_id"]
            else:
                to = self._check_assignee(cur, to_user_id, actor)
                if to == t["assignee_id"] and not t["is_private"]:
                    raise ZsError("İş zaten bu kişide.")

            def mx(q):
                return int(self._q(cur, q, (task_id,)).fetchone()[0] or 0)
            cuts = (mx("SELECT MAX(id) FROM zs_tasks WHERE parent_id = ?"),
                    mx("SELECT MAX(id) FROM zs_notes WHERE task_id = ?"),
                    mx("SELECT MAX(id) FROM zs_files WHERE task_id = ?"),
                    mx("SELECT MAX(id) FROM zs_events WHERE task_id = ?"))
            flags = [1 if hide.get(k) else 0 for k in ("subtasks", "notes", "files", "history")]
            now = _now()
            self._q(cur, "INSERT INTO zs_transfers (task_id, from_user, to_user, by_user, hide_subtasks, hide_notes, "
                         "hide_files, hide_history, cut_task, cut_note, cut_file, cut_event, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, t["assignee_id"], to or 0, actor["id"], *flags, *cuts, now))
            self._q(cur, "UPDATE zs_tasks SET assignee_id = ?, is_private = 0, updated_at = ? WHERE id = ?",
                    (to, now, task_id))
            gizli = [ad for ad, f in zip(("alt işler", "notlar", "belgeler", "geçmiş"), flags) if f]
            if publish:
                self._event(cur, task_id, actor["id"], "published", hidden=gizli)
            else:
                self._event(cur, task_id, actor["id"], "transferred", from_id=t["assignee_id"], to_id=to,
                            hidden=gizli, was_private=bool(t["is_private"]))
            return self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
        return self._run(fn)

    def _next_sort(self, cur, parent_id):
        if parent_id:
            v = self._q(cur, "SELECT MAX(sort_order) FROM zs_tasks WHERE parent_id = ?", (parent_id,)).fetchone()[0]
        else:
            v = self._q(cur, "SELECT MAX(sort_order) FROM zs_tasks WHERE parent_id IS NULL").fetchone()[0]
        return int(v or 0) + 1

    def _check_assignee(self, cur, assignee_id, actor):
        if assignee_id is None:
            return None
        u = self._one(self._q(cur, "SELECT id, active, office_id FROM zs_users WHERE id = ?", (assignee_id,)))
        if not u or not u["active"] or (u["office_id"] or "") != office_of(actor):
            raise ZsError("Atanacak kullanıcı bulunamadı ya da kapalı.")
        return int(assignee_id)

    def create_task(self, actor, title, parent_id=None, description="", assignee_id=None, due_at=None,
                    private=False, priority=1, dosya=None, sablon=None, etiketler=None):
        """sablon: işin açıldığı şablonun anahtarı; verilmezse başlıktan bulunur. Ana işin süreç/aşaması
        şablondan gelir, şablonun etiketleri + etiketler işe takılır (yalnız ana işte)."""
        title = (title or "").strip()
        if not title:
            raise ZsError("Başlık boş olamaz.")
        if priority not in PRIORITIES:
            raise ZsError("Geçersiz öncelik.")
        dosya = clean_dosya(dosya) if not parent_id else None   # dosya bağı yalnız ana işte

        def fn(cur):
            aid = self._check_assignee(cur, assignee_id, actor)
            if parent_id:
                p = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (parent_id,)))
                if not p or p["parent_id"]:
                    raise ZsError("Alt iş yalnız bir ana işin altına eklenebilir.")
                if p["is_private"] and aid not in (None, p["created_by"]):
                    raise ZsError("Özel işin alt işi başkasına atanamaz; önce işi devredin.")
            if private and not parent_id:
                aid = actor["id"]
            now = _now()
            status = "acik"
            d = dosya or {}
            skey, tpl, surec, asama, tags, tur = None, None, None, None, [], None
            if not parent_id:
                tur = task_turu(d)
                skey, tpl = self._sablon_bul(cur, office_of(actor), sablon, title)
                surec, asama, tags = self._sablondan(cur, office_of(actor), tpl, tur)
                given = self._check_tags(cur, actor, etiketler)
                self._check_fit(given, tur)
                tags += [t["key"] for t in given]
            tid = self._insert(cur, "INSERT INTO zs_tasks (parent_id, title, description, assignee_id, created_by, "
                                    "due_at, status, sort_order, created_at, updated_at, is_private, priority, office_id, "
                                    f"sablon, surec, asama, {', '.join(DOSYA_FIELDS)}) "
                                    f"VALUES ({', '.join('?' * (16 + len(DOSYA_FIELDS)))})",
                               (parent_id, title[:300], (description or "")[:5000], aid, actor["id"], due_at,
                                status, self._next_sort(cur, parent_id), now, now,
                                1 if (private and not parent_id) else 0, 1 if parent_id else priority,
                                office_of(actor) or None, skey, surec, asama, *(d.get(k) for k in DOSYA_FIELDS)))
            root = parent_id or tid
            self._tag_task(cur, tid, list(dict.fromkeys(tags)), actor["id"])
            if not parent_id:
                self._ana_etiket(cur, tid, tur, actor["id"])
            self._event(cur, root, actor["id"], "created", title=title[:300], sub=bool(parent_id))
            if aid:
                self._event(cur, root, actor["id"], "assigned", title=title[:300], assignee_id=aid)
            if parent_id:
                self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (now, parent_id))
            return self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (tid,)))
        return self._run(fn)

    def update_task(self, actor, task_id, fields: dict):
        """fields: title, description, assignee_id, due_at, done (alt iş), move (-1/+1)."""
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t:
                raise ZsError("İş bulunamadı.")
            root_id = t["parent_id"] or t["id"]
            sets, params = [], []
            if "title" in fields:
                title = (fields["title"] or "").strip()
                if not title:
                    raise ZsError("Başlık boş olamaz.")
                sets.append("title = ?"); params.append(title[:300])
            if "description" in fields:
                sets.append("description = ?"); params.append((fields["description"] or "")[:5000])
            if "due_at" in fields:
                sets.append("due_at = ?"); params.append(fields["due_at"])
            if "priority" in fields:
                if t["parent_id"] or fields["priority"] not in PRIORITIES:
                    raise ZsError("Öncelik yalnız ana işte ve Acil/Normal/Düşük olabilir.")
                sets.append("priority = ?"); params.append(fields["priority"])
            if "dosya" in fields:
                if t["parent_id"]:
                    raise ZsError("UYAP dosyası yalnız ana işe bağlanır.")
                d = clean_dosya(fields["dosya"]) or {}      # boş/None → bağ kaldırılır
                for k in DOSYA_FIELDS:
                    sets.append(f"{k} = ?"); params.append(d.get(k))
                self._ana_etiket(cur, task_id, task_turu(d), actor["id"])
            if "asama" in fields:
                if t["parent_id"]:
                    raise ZsError("Süreç aşaması yalnız ana işte olur.")
                surec, asama = asama_coz(fields["asama"])     # boş → süreçten çıkarılır
                tur = task_turu(clean_dosya(fields["dosya"]) or {}) if "dosya" in fields else task_turu(t)
                st = next((s.get("tur") for s in HAZIR_SURECLER if s["key"] == surec), None)
                if surec and tur and st and st != tur:
                    raise ZsError("Bu süreç bu dosya türüne ait değil.")
                sets += ["surec = ?", "asama = ?"]; params += [surec, asama]
                self._asama_yaz(cur, task_id, surec, asama, actor["id"])   # aşamanın alt etiketi
            if "assignee_id" in fields:
                aid = self._check_assignee(cur, fields["assignee_id"], actor)
                root = t if not t["parent_id"] else self._one(self._q(
                    cur, "SELECT * FROM zs_tasks WHERE id = ?", (t["parent_id"],)))
                if root["is_private"] and aid not in (None, root["created_by"]):
                    raise ZsError("Özel iş başkasına atanamaz; bunun için \u201cDevret\u201d kullanın.")
                if aid != t["assignee_id"]:
                    sets.append("assignee_id = ?"); params.append(aid)
                    self._event(cur, root_id, actor["id"], "assigned", title=t["title"], assignee_id=aid)
            if "done" in fields:
                if not t["parent_id"]:
                    raise ZsError("Ana iş işaretlenmez; onay akışını kullanın.")
                new = "tamam" if fields["done"] else "acik"
                if new != t["status"]:
                    sets.append("status = ?"); params.append(new)
                    self._event(cur, root_id, actor["id"], "subtask", title=t["title"], done=bool(fields["done"]))
            if fields.get("move") in (-1, 1):
                self._move(cur, t, fields["move"])
            if sets:
                now = _now()
                sets.append("updated_at = ?"); params.append(now)
                self._q(cur, f"UPDATE zs_tasks SET {', '.join(sets)} WHERE id = ?", params + [task_id])
                if t["parent_id"]:
                    self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (now, t["parent_id"]))
                changed = [k for k in ("title", "description", "due_at", "priority", "dosya", "asama") if k in fields]
                if changed:
                    self._event(cur, root_id, actor["id"], "edited", title=t["title"], fields=changed)
            return self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
        return self._run(fn)

    def _move(self, cur, t, direction):
        if t["parent_id"]:
            sibs = self._rows(self._q(cur, "SELECT id, sort_order FROM zs_tasks WHERE parent_id = ? "
                                           "ORDER BY sort_order, id", (t["parent_id"],)))
        else:
            sibs = self._rows(self._q(cur, "SELECT id, sort_order FROM zs_tasks WHERE parent_id IS NULL "
                                           "AND COALESCE(office_id, '') = ? ORDER BY sort_order, id",
                                      (t["office_id"] or "",)))
        ids = [s["id"] for s in sibs]
        i = ids.index(t["id"])
        j = i + direction
        if j < 0 or j >= len(ids):
            return
        ids[i], ids[j] = ids[j], ids[i]
        for n, sid in enumerate(ids, 1):
            self._q(cur, "UPDATE zs_tasks SET sort_order = ? WHERE id = ?", (n, sid))

    def delete_task(self, actor, task_id):
        """İşi (ana işse alt işleri, notları, belge kayıtları ve olaylarıyla) siler.
        Silinen belgelerin depolama anahtarlarını döndürür (çağıran depodan da siler)."""
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t:
                raise ZsError("İş bulunamadı.")
            if t["parent_id"]:
                self._q(cur, "DELETE FROM zs_reminders WHERE sub_id = ?", (task_id,))
                self._q(cur, "DELETE FROM zs_tasks WHERE id = ?", (task_id,))
                self._event(cur, t["parent_id"], actor["id"], "subtask_deleted", title=t["title"])
                return []
            keys = [r[0] for r in self._q(cur, "SELECT storage_key FROM zs_files WHERE task_id = ?",
                                          (task_id,)).fetchall()]
            for q in ("DELETE FROM zs_files WHERE task_id = ?", "DELETE FROM zs_notes WHERE task_id = ?",
                      "DELETE FROM zs_transfers WHERE task_id = ?", "DELETE FROM zs_seen WHERE task_id = ?",
                      "DELETE FROM zs_members WHERE task_id = ?", "DELETE FROM zs_extensions WHERE task_id = ?",
                      "DELETE FROM zs_reminders WHERE task_id = ?", "DELETE FROM zs_tg_msgs WHERE task_id = ?",
                      "DELETE FROM zs_task_tags WHERE task_id = ?",
                      "DELETE FROM zs_tasks WHERE parent_id = ?", "DELETE FROM zs_tasks WHERE id = ?"):
                self._q(cur, q, (task_id,))
            # Silme izi kalsın: olaylar silinmez, task_id'si silinmiş işe işaret eder.
            self._event(cur, task_id, actor["id"], "deleted", title=t["title"])
            return keys
        return self._run(fn)

    # ── onay akışı ───────────────────────────────────────────────────────────────────
    def transition(self, actor, task_id, action, reason="", sonuc=None, sonraki=None, kesinlesme=None):
        """action: basla | gonder | tamamla | onayla | reddet | yeniden_ac. Yetki kontrolü burada.
        sonuc (gonder / tamamla): kontrol sonucu (olumlu | olumsuz | bekliyor) işe yazılır ve şablonun o
        sonuç dalındaki sonraki iş (süreçte sıradaki aşama) aynı dosya / taraf / atananla açılır.
        sonraki: {baslik, gun} öneriyi değiştirir; False sonraki işi açmaz. sonuc verilmezse sonraki iş
        açılmaz (eski istemciler sonraki işi kendileri açar).
        kesinlesme: "YYYY-AA-GG" — şablonun kesinleşme dalı seçildiyse (Tebliğ kontrol → Tebliğ edildi) o
        güne "Kesinleşme tarihi kontrol" işi de açılır. Aynı dosya / taraf için aynı şablonda açık iş varsa
        yenisi açılmaz ({var: True, id, title}). Dönüş: (iş, sonraki iş | None, kesinleşme işi | None)."""
        is_admin = actor["role"] == "admin"
        if sonuc is not None and sonuc not in TEMPLATE_SONUCLAR:
            raise ZsError("Geçersiz kontrol sonucu.")
        if sonuc is not None and action not in ("gonder", "tamamla"):
            raise ZsError("Kontrol sonucu yalnız onaya gönderirken ya da tamamlarken seçilir.")
        kes_at = _tarih_17(kesinlesme) if kesinlesme else None

        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or t["parent_id"]:
                raise ZsError("Onay akışı yalnız ana işlerde geçerlidir.")
            st = t["status"]
            roles = self._roles(cur, task_id, t["assignee_id"])
            is_assignee = actor["id"] in roles["assignees"]
            can_approve = is_admin or actor["id"] in roles["approvers"]
            if action == "basla":
                if st != "acik" or not (is_assignee or is_admin):
                    raise ZsError("Bu iş başlatılamaz.")
                new, kind = "devam", "status"
            elif action == "gonder":
                if st not in ("acik", "devam") or not is_assignee:
                    raise ZsError("Yalnız işe atanan kişi, açık/devam eden işi onaya gönderebilir.")
                if t["is_private"]:
                    raise ZsError("Özel iş onaya gönderilmez; “Tamamla” ile kapatın.")
                self._check_subs_done(cur, actor, task_id)
                new, kind = "onayda", "submitted"
            elif action == "tamamla":
                if not t["is_private"] or t["created_by"] != actor["id"] or st not in ("acik", "devam"):
                    raise ZsError("Yalnız kendi özel işinizi tamamlayabilirsiniz; diğer işler onaya gönderilir.")
                self._check_subs_done(cur, actor, task_id)
                new, kind = "tamamlandi", "completed"
            elif action == "onayla":
                if st != "onayda" or not can_approve:
                    raise ZsError("Yalnız yönetici ya da işin onay yetkilisi onaylayabilir.")
                new, kind = "onaylandi", "approved"
            elif action == "reddet":
                if st != "onayda" or not can_approve:
                    raise ZsError("Yalnız yönetici ya da işin onay yetkilisi reddedebilir.")
                if not (reason or "").strip():
                    raise ZsError("Ret gerekçesi yazın.")
                new, kind = "devam", "rejected"
            elif action == "yeniden_ac":
                if st == "tamamlandi":
                    if not (is_admin or t["created_by"] == actor["id"]):
                        raise ZsError("Tamamlanan işi yalnız açanı ya da yönetici yeniden açabilir.")
                elif st != "onaylandi" or not can_approve:
                    raise ZsError("Yalnız yönetici ya da işin onay yetkilisi yeniden açabilir.")
                new, kind = "devam", "reopened"
            else:
                raise ZsError("Geçersiz işlem.")
            now = _now()
            self._q(cur, "UPDATE zs_tasks SET status = ?, updated_at = ? WHERE id = ?", (new, now, task_id))
            if kind in ("submitted", "completed"):
                self._q(cur, "UPDATE zs_tasks SET submitted_at = ?, sonuc = ? WHERE id = ?", (now, sonuc, task_id))
            reason_txt = (reason or "").strip()[:2000]
            self._event(cur, task_id, actor["id"], kind, old=st, new=new, reason=reason_txt,
                        **({"sonuc": sonuc} if sonuc else {}))
            if reason_txt:
                label = {"rejected": "Ret gerekçesi", "approved": "Onay notu", "submitted": "Onaya gönderme notu",
                         "completed": "Tamamlama notu", "reopened": "Yeniden açma gerekçesi"}.get(kind, "Not")
                self._q(cur, "INSERT INTO zs_notes (task_id, author_id, body, note_at, created_at) "
                             "VALUES (?, ?, ?, NULL, ?)", (task_id, actor["id"], f"{label}: {reason_txt}", now))
            nxt = kes = None
            if sonuc and not t["is_private"]:
                tpl = self._sablon_bul(cur, t["office_id"] or "", t["sablon"], t["title"])[1] or {}
                if sonraki is not False:
                    nxt = self._sonraki_oneri(cur, t, tpl, sonuc, sonraki)
                if kes_at is not None and tpl.get("kesinlesme") == sonuc:
                    kes = self._kesinlesme_oneri(cur, t, kes_at)
            return self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,))), nxt, kes
        task, nxt, kes = self._run(fn)
        return task, self._oneri_ac(actor, task, nxt), self._oneri_ac(actor, task, kes)

    def _oneri_ac(self, actor, task, n):
        """Önerilen işi biten işin atananıyla açar; zaten açıksa ya da öneri yoksa olduğu gibi döner."""
        if not n or n.get("var"):
            return n
        try:    # iş durumu değişti; sonraki iş açılamazsa (ör. atanan kapatıldı) yalnız bildirilir
            return self.create_task(actor, n["title"], description=n["description"],
                                    assignee_id=task["assignee_id"], due_at=n["due_at"],
                                    priority=n["priority"], dosya=n["dosya"], sablon=n.get("sablon"))
        except ZsError as e:
            return {"hata": str(e), "title": n["title"]}

    def _acik_ayni_is(self, cur, t, sablon):
        """Aynı dosya + taraf için bu şablonda henüz yapılmamış (açık / devam) başka ana iş ya da None.
        Onaydaki iş yapılmış sayılır (bekliyor / reddedildi döngüsü yeni işi açabilsin); dosyasız işte aranmaz."""
        if not sablon or not t["dosya_id"]:
            return None
        if t["birim_id"] and t["dosya_no"]:
            dosya_q, dosya_p = "birim_id = ? AND dosya_no = ?", (t["birim_id"], t["dosya_no"])
        else:
            dosya_q, dosya_p = "dosya_id = ?", (t["dosya_id"],)
        return self._one(self._q(cur, "SELECT id, title FROM zs_tasks WHERE parent_id IS NULL AND id <> ? "
                                      "AND COALESCE(office_id, '') = ? AND sablon = ? AND COALESCE(taraf_adi, '') = ? "
                                      f"AND status IN ('acik', 'devam') AND {dosya_q} ORDER BY id LIMIT 1",
                                 (t["id"], t["office_id"] or "", sablon, t["taraf_adi"] or "", *dosya_p)))

    @staticmethod
    def _dosya_kopya(t, yeni_tur=False):
        """Biten işin dosya bağı (sonraki işe); yeni tebligat turunda eski barkod / belge taşınmaz."""
        if not t["dosya_id"]:
            return None
        dosya = {k: t[k] for k in DOSYA_FIELDS if k != "dosya_uyap"}
        try:
            dosya["uyap"] = json.loads(t["dosya_uyap"]) if t["dosya_uyap"] else None
        except ValueError:
            dosya["uyap"] = None
        if yeni_tur:
            dosya.update(barkod=None, evrak_id=None, evrak_adi=None)
        return dosya

    def _kesinlesme_oneri(self, cur, t, due_at):
        """Tebliğ edilen tebligatın kesinleşme günü işi (Kesinleşme tarihi kontrol, o gün 17:00)."""
        ntpl = _HAZIR_SABLON[KESINLESME_SABLONU]
        var = self._acik_ayni_is(cur, t, KESINLESME_SABLONU)
        if var:
            return {"var": True, "id": var["id"], "title": var["title"]}
        return {"title": f"{t['taraf_adi']} – {ntpl['title']}" if t["taraf_adi"] else ntpl["title"],
                "description": ntpl["description"], "due_at": due_at, "priority": ntpl["priority"],
                "dosya": self._dosya_kopya(t), "sablon": KESINLESME_SABLONU}

    def _sonraki_oneri(self, cur, t, tpl, sonuc, sonraki):
        """Biten kontrolün sonraki işi: {title, description, due_at, priority, dosya, sablon} ya da None
        (aynı şablonda açık iş varsa {var: True, id, title}). Başlık şablonun sonuç dalından (boşsa aynı
        şablon) ya da istemcinin sonraki.baslik'ından; süre dalın gününden, yoksa sonraki şablonun
        süresinden (bugünden gün sayısı, 17:00)."""
        office = t["office_id"] or ""
        if isinstance(sonraki, dict):
            baslik = " ".join(str(sonraki.get("baslik") or "").split())[:200]
            gun = sonraki.get("gun")
            try:
                gun = None if gun in (None, "") else int(gun)
            except (TypeError, ValueError):
                raise ZsError("Sonraki iş süresi gün sayısı olmalı.")
        else:
            dal = ((tpl or {}).get("sonuclar") or {}).get(sonuc)
            if not dal:
                return None
            baslik = dal.get("baslik") or tpl["title"]
            gun = dal.get("gun")
        if not baslik:
            return None
        if gun is not None and not 0 <= gun <= 365:
            raise ZsError("Sonraki iş süresi 0–365 gün olmalı.")
        nkey, ntpl = self._sablon_bul(cur, office, None, baslik)
        var = self._acik_ayni_is(cur, t, nkey)
        if var:     # ör. Kesinleşme tarihi kontrol tebliğde zaten açıldı: itiraz kontrolünden yenisi açılmaz
            return {"var": True, "id": var["id"], "title": var["title"]}
        if gun is None:
            gun = (ntpl or {}).get("due_days")
        return {"title": f"{t['taraf_adi']} – {baslik}" if t["taraf_adi"] else baslik,
                "description": (ntpl or {}).get("description") or "",
                "due_at": None if gun is None else _gun_sonra(gun),
                "priority": t["priority"] if (ntpl or {}).get("priority") is None else ntpl["priority"],
                # yeni tebligat turu: eski barkod / belge kalmaz
                "dosya": self._dosya_kopya(t, (ntpl or {}).get("asama") == _KES + "teb-olustu"),
                "sablon": nkey or None}

    # ── rapor ham verisi (hesap zs_rapor.py'de) ──────────────────────────────────────
    def report_source(self, viewer_id, office=""):
        """Ana işler (izleyicinin bürosu; özel işlerden yalnız kendi açtıkları) + ek atananlar + akış olayları."""
        vis = "(t.is_private = 0 OR t.created_by = ?) AND COALESCE(t.office_id, '') = ?"
        vp = (viewer_id, office or "")

        def fn(cur):
            tasks = self._rows(self._q(cur, "SELECT t.id, t.title, t.assignee_id, t.created_at, t.due_at, t.status, "
                                            f"t.submitted_at, t.is_private FROM zs_tasks t WHERE t.parent_id IS NULL AND {vis}",
                                       vp))
            members = self._q(cur, "SELECT m.task_id, m.user_id FROM zs_members m JOIN zs_tasks t ON t.id = m.task_id "
                                   f"WHERE m.role = 'assignee' AND {vis} ORDER BY m.id", vp).fetchall()
            events = self._rows(self._q(cur, "SELECT e.task_id, e.actor_id, e.kind, e.created_at FROM zs_events e "
                                             f"JOIN zs_tasks t ON t.id = e.task_id WHERE t.parent_id IS NULL AND {vis} "
                                             "AND e.kind IN ('submitted', 'approved', 'rejected', 'completed', 'reopened') "
                                             "ORDER BY e.id", vp))
            return {"tasks": tasks, "members": [tuple(m) for m in members], "events": events}
        return self._run(fn)

    def activity_source(self, viewer_id, person_id, start, end):
        """Kişinin [start, end) aralığındaki işlemleri (olaylar; not olaylarına notun metni eklenir).
        İzleyiciye görünmeyenler düşer: başkasının özel işi, devirde gizlenen kayıtlar; silinmiş
        işlerin izi yalnız kişinin kendisine gösterilir (özel olup olmadığı artık bilinemez)."""
        def fn(cur):
            ev = self._rows(self._q(cur, "SELECT e.id, e.task_id, e.kind, e.detail, e.created_at, t.id AS tid, t.title, "
                                         "t.status, t.is_private, t.created_by FROM zs_events e "
                                         "LEFT JOIN zs_tasks t ON t.id = e.task_id WHERE e.actor_id = ? "
                                         "AND e.created_at >= ? AND e.created_at < ? ORDER BY e.created_at, e.id",
                                    (person_id, start, end)))
            notes = self._rows(self._q(cur, "SELECT id, task_id, body, created_at FROM zs_notes WHERE author_id = ? "
                                            "AND created_at >= ? AND created_at < ? ORDER BY id", (person_id, start, end)))
            cuts = self._cutoffs(cur, list({e["task_id"] for e in ev if e["tid"]}), viewer_id)
            nocut = {k: 0 for k in _HIDE_KINDS}
            by_key = {}
            for n in notes:
                if n["id"] > cuts.get(n["task_id"], nocut)["note"]:
                    by_key.setdefault((n["task_id"], n["created_at"]), []).append(n["body"])
            out = []
            for e in ev:
                try:
                    d = json.loads(e["detail"] or "{}")
                except Exception:
                    d = {}
                if not e["tid"]:
                    if viewer_id != person_id:
                        continue
                elif e["is_private"] and e["created_by"] != viewer_id:
                    continue
                cut = cuts.get(e["task_id"], nocut)
                k = _EVENT_KIND_OF.get(e["kind"]) or ("ev_task" if e["kind"] == "created" and d.get("sub") else None)
                if e["id"] <= cut["event"] or (k and e["id"] <= cut[k]):
                    continue
                body = None
                if e["kind"] == "note":
                    bodies = by_key.get((e["task_id"], e["created_at"]))
                    if not bodies:
                        continue  # not silinmiş ya da bu izleyiciden gizli
                    body = bodies.pop(0)
                out.append({"id": e["id"], "task_id": e["task_id"], "kind": e["kind"], "detail": d, "at": e["created_at"],
                            "title": e["title"] if e["tid"] else d.get("title", ""), "status": e["status"],
                            "is_private": bool(e["is_private"]), "exists": bool(e["tid"]), "body": body})
            return out
        return self._run(fn)

    # ── denetim kaydı (yalnız geliştirici; arayüze/API'ye açılmaz) ──────────────────────
    _AUDIT_COLS = ("at", "user_id", "username", "role", "ip", "user_agent", "method", "path", "action",
                   "target_id", "ok", "status", "error", "data", "result", "before", "ms")

    def audit(self, **row):
        """Tek satır ekler (kendi transaction'ında). JSON alanları sözlük olarak verilebilir."""
        for k in ("data", "result", "before"):
            if row.get(k) is not None and not isinstance(row[k], str):
                row[k] = json.dumps(row[k], ensure_ascii=False, default=str)
        row.setdefault("at", _now())
        vals = [row.get(c) for c in self._AUDIT_COLS]
        marks = ", ".join("?" * len(self._AUDIT_COLS))
        self._run(lambda cur: self._q(cur, f"INSERT INTO zs_audit ({', '.join(self._AUDIT_COLS)}) VALUES ({marks})", vals))

    def audit_rows(self, since=None, until=None, user=None, action_like=None, only_errors=False, limit=200, audit_id=None):
        """Geliştirici aracı (zs_denetim.py) için; en yeni önce."""
        where, params = [], []
        if audit_id is not None:
            where.append("id = ?"); params.append(int(audit_id))
        if since is not None:
            where.append("at >= ?"); params.append(since)
        if until is not None:
            where.append("at < ?"); params.append(until)
        if user:
            where.append("(username = ? OR CAST(user_id AS TEXT) = ?)"); params += [user, str(user)]
        if action_like:
            where.append("(action LIKE ? OR method = ?)"); params += [f"%{action_like}%", action_like.upper()]
        if only_errors:
            where.append("ok = 0")
        q = "SELECT * FROM zs_audit" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self._run(lambda cur: self._rows(self._q(cur, q, params)))

    def snapshot_task(self, task_id, full=True):
        """İşin o anki hâli. full=True: alt işler, notlar, belge kayıtları, kişiler, ek süreler, devirler dahil
        (silmeden önce alınır; silinen içerik denetim kaydında kalır)."""
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or not full:
                return t
            one = lambda q: self._rows(self._q(cur, q, (task_id,)))
            t["subtasks"] = one("SELECT * FROM zs_tasks WHERE parent_id = ? ORDER BY id")
            if not t["parent_id"]:
                t["notes"] = one("SELECT * FROM zs_notes WHERE task_id = ? ORDER BY id")
                t["files"] = one("SELECT id, uploader_id, filename, mime, size, storage_key, sha256, created_at "
                                 "FROM zs_files WHERE task_id = ? ORDER BY id")
                t["members"] = one("SELECT * FROM zs_members WHERE task_id = ? ORDER BY id")
                t["extensions"] = one("SELECT * FROM zs_extensions WHERE task_id = ? ORDER BY id")
                t["transfers"] = one("SELECT * FROM zs_transfers WHERE task_id = ? ORDER BY id")
                t["tags"] = one("SELECT * FROM zs_task_tags WHERE task_id = ? ORDER BY created_at, tag")
            return t
        return self._run(fn)

    def snapshot_file(self, file_id):
        return self._run(lambda cur: self._one(self._q(
            cur, "SELECT id, task_id, uploader_id, filename, mime, size, storage_key, sha256, created_at "
                 "FROM zs_files WHERE id = ?", (file_id,))))

    def snapshot_extension(self, ext_id):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_extensions WHERE id = ?", (ext_id,))))

    # ── bildirim kanalları ───────────────────────────────────────────────────────────
    def push_add(self, user_id, endpoint, p256dh, auth, user_agent=""):
        def fn(cur):
            # Aynı cihaz başka kullanıcıyla açılmışsa abonelik yeni kullanıcıya geçer.
            self._q(cur, "DELETE FROM zs_push_subs WHERE endpoint = ?", (endpoint,))
            self._q(cur, "INSERT INTO zs_push_subs (user_id, endpoint, p256dh, auth, user_agent, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (user_id, endpoint, p256dh, auth, (user_agent or "")[:300], _now()))
        self._run(fn)

    def push_remove(self, endpoint, user_id=None):
        q, params = "DELETE FROM zs_push_subs WHERE endpoint = ?", [endpoint]
        if user_id is not None:
            q += " AND user_id = ?"
            params.append(user_id)
        self._run(lambda cur: self._q(cur, q, params))

    def push_list(self, user_ids):
        if not user_ids:
            return []
        marks = ",".join("?" * len(user_ids))
        return self._run(lambda cur: self._rows(self._q(
            cur, f"SELECT * FROM zs_push_subs WHERE user_id IN ({marks})", list(user_ids))))

    def push_result(self, sub_id, ok):
        q = ("UPDATE zs_push_subs SET last_ok = ?, fails = 0 WHERE id = ?" if ok
             else "UPDATE zs_push_subs SET fails = fails + 1 WHERE id = ?")
        self._run(lambda cur: self._q(cur, q, (_now(), sub_id) if ok else (sub_id,)))

    def push_count(self, user_id):
        return self._run(lambda cur: self._q(cur, "SELECT COUNT(*) FROM zs_push_subs WHERE user_id = ?",
                                             (user_id,)).fetchone()[0])

    def tg_code_new(self, user_id, ttl=900):
        import secrets
        code = secrets.token_urlsafe(18)

        def fn(cur):
            self._q(cur, "DELETE FROM zs_tg_codes WHERE user_id = ? OR expires_at < ?", (user_id, _now()))
            self._q(cur, "INSERT INTO zs_tg_codes (code, user_id, expires_at) VALUES (?, ?, ?)",
                    (code, user_id, _now() + ttl))
        self._run(fn)
        return code

    def tg_link(self, code, chat_id, tg_name="", tg_username=None):
        """Kodu kullanır, sohbeti kişiye bağlar. Kişi kimliği ya da None (geçersiz/süresi dolmuş).
        Özel sohbette chat_id = kişinin Telegram kullanıcı kimliği (gruptaki from.id ile aynı)."""
        def fn(cur):
            r = self._one(self._q(cur, "SELECT * FROM zs_tg_codes WHERE code = ?", (code,)))
            self._q(cur, "DELETE FROM zs_tg_codes WHERE code = ?", (code,))
            if not r or r["expires_at"] < _now():
                return None
            u = self._one(self._q(cur, "SELECT id, active FROM zs_users WHERE id = ?", (r["user_id"],)))
            if not u or not u["active"]:
                return None
            # Bir sohbet tek kişiye bağlı olur.
            self._q(cur, "DELETE FROM zs_telegram WHERE user_id = ? OR chat_id = ?", (r["user_id"], chat_id))
            self._q(cur, "INSERT INTO zs_telegram (user_id, chat_id, tg_name, linked_at, tg_username) VALUES (?, ?, ?, ?, ?)",
                    (r["user_id"], chat_id, (tg_name or "")[:100], _now(), _tg_uname(tg_username)))
            return r["user_id"]
        return self._run(fn)

    def tg_user_by_chat(self, chat_id):
        """Telegram kullanıcı kimliğine bağlı etkin zs kullanıcısı (satırı) ya da None."""
        return self._run(lambda cur: self._one(self._q(
            cur, "SELECT u.* FROM zs_telegram t JOIN zs_users u ON u.id = t.user_id "
                 "WHERE t.chat_id = ? AND u.active = 1", (chat_id,))))

    def tg_user_by_username(self, username, office=None):
        """office verilirse yalnız o büronun kullanıcısı aranır ('' = eski /zs havuzu)."""
        uname = _tg_uname(username)
        if not uname:
            return None
        q, params = ("SELECT u.* FROM zs_telegram t JOIN zs_users u ON u.id = t.user_id "
                     "WHERE t.tg_username = ? AND u.active = 1"), [uname]
        if office is not None:
            q += " AND COALESCE(u.office_id, '') = ?"
            params.append(office)
        return self._run(lambda cur: self._one(self._q(cur, q, params)))

    def is_linked(self, user_id) -> bool:
        """Kullanıcı ana hesaba bağlı mı (bildirim bağlantısı hangi arayüze gitsin)?"""
        r = self._run(lambda cur: self._q(cur, "SELECT account_username FROM zs_users WHERE id = ?",
                                          (user_id,)).fetchone())
        return bool(r and r[0])

    def tg_touch(self, chat_id, username):
        """Bağlı kişinin Telegram kullanıcı adını tazeler (sonradan değiştirmiş/eklemiş olabilir)."""
        uname = _tg_uname(username)

        def fn(cur):
            if uname:  # aynı ad başka satırda kalmışsa (kişi adını devretmiş) oradan silinir
                self._q(cur, "UPDATE zs_telegram SET tg_username = NULL WHERE tg_username = ? AND chat_id <> ?",
                        (uname, chat_id))
            self._q(cur, "UPDATE zs_telegram SET tg_username = ? WHERE chat_id = ? AND "
                         "COALESCE(tg_username, '') <> COALESCE(?, '')", (uname, chat_id, uname))
        self._run(fn)

    def tg_msg_save(self, chat_id, message_id, task_id):
        self._run(lambda cur: self._q(
            cur, "INSERT INTO zs_tg_msgs (chat_id, message_id, task_id, created_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT (chat_id, message_id) DO UPDATE SET task_id = excluded.task_id",
            (chat_id, message_id, task_id, _now())))

    def tg_msg_task(self, chat_id, message_id):
        r = self._run(lambda cur: self._q(
            cur, "SELECT task_id FROM zs_tg_msgs WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id)).fetchone())
        return int(r[0]) if r else None

    def tg_get(self, user_ids):
        if not user_ids:
            return []
        marks = ",".join("?" * len(user_ids))
        return self._run(lambda cur: self._rows(self._q(
            cur, f"SELECT * FROM zs_telegram WHERE user_id IN ({marks})", list(user_ids))))

    def tg_unlink(self, user_id=None, chat_id=None):
        if user_id is not None:
            self._run(lambda cur: self._q(cur, "DELETE FROM zs_telegram WHERE user_id = ?", (user_id,)))
        elif chat_id is not None:
            self._run(lambda cur: self._q(cur, "DELETE FROM zs_telegram WHERE chat_id = ?", (chat_id,)))

    def max_event_id(self):
        return int(self._run(lambda cur: self._q(cur, "SELECT COALESCE(MAX(id), 0) FROM zs_events").fetchone()[0]))

    def events_for_notify(self, after_id):
        """Bildirim üretmek için yeni olaylar + iş bilgisi (başlık, özel mi, atananlar, onay yetkilileri).
        Ayrıca etkin yöneticiler (işe özel onay yetkilisi yoksa onay bildirimleri onlara)."""
        def fn(cur):
            ev = self._rows(self._q(cur, "SELECT e.id, e.task_id, e.actor_id, e.kind, e.detail, e.created_at, "
                                         "t.title, t.is_private, t.created_by, t.assignee_id, t.due_at, t.office_id "
                                         "FROM zs_events e JOIN zs_tasks t ON t.id = e.task_id "
                                         "WHERE e.id > ? ORDER BY e.id", (after_id,)))
            # Onay bildirimi işin bürosunun yöneticilerine gider (eski /zs işleri → eski havuzun yöneticileri).
            by_office = {}
            for uid, off in self._q(cur, "SELECT id, office_id FROM zs_users WHERE role = 'admin' AND active = 1").fetchall():
                by_office.setdefault(off or "", []).append(uid)
            roles = {}
            for e in ev:
                try:
                    e["detail"] = json.loads(e["detail"] or "{}")
                except Exception:
                    e["detail"] = {}
                if e["task_id"] not in roles:
                    roles[e["task_id"]] = self._roles(cur, e["task_id"], e["assignee_id"])
                e.update(roles[e["task_id"]])
                e["admins"] = by_office.get(e.pop("office_id") or "", [])
            admins = by_office.get("", [])
            active = {r[0]: r[1] for r in self._q(cur, "SELECT id, display_name FROM zs_users WHERE active = 1").fetchall()}
            return ev, admins, active
        return self._run(fn)

    def _check_subs_done(self, cur, actor, task_id):
        cut = (self._cutoffs(cur, [task_id], actor["id"]).get(task_id) or {}).get("task", 0)
        kalan = self._q(cur, "SELECT COUNT(*) FROM zs_tasks WHERE parent_id = ? AND status <> 'tamam' "
                             "AND id > ?", (task_id, cut)).fetchone()[0]
        if kalan:
            raise ZsError(f"Önce alt işleri tamamlayın ({kalan} alt iş açık).")

    # ── notlar ───────────────────────────────────────────────────────────────────────
    def add_note(self, actor, task_id, body, note_at=None):
        body = (body or "").strip()
        if not body:
            raise ZsError("Not boş olamaz.")

        def fn(cur):
            nid = self._insert(cur, "INSERT INTO zs_notes (task_id, author_id, body, note_at, created_at) "
                                    "VALUES (?, ?, ?, ?, ?)", (task_id, actor["id"], body[:5000], note_at, _now()))
            self._event(cur, task_id, actor["id"], "note")
            self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (_now(), task_id))
            return self._one(self._q(cur, "SELECT * FROM zs_notes WHERE id = ?", (nid,)))
        return self._run(fn)

    # ── belgeler ─────────────────────────────────────────────────────────────────────
    def add_file(self, actor, task_id, filename, mime, size, storage_key, sha256):
        def fn(cur):
            fid = self._insert(cur, "INSERT INTO zs_files (task_id, uploader_id, filename, mime, size, storage_key, "
                                    "sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (task_id, actor["id"], filename, mime, size, storage_key, sha256, _now()))
            self._event(cur, task_id, actor["id"], "file", filename=filename, size=size)
            self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (_now(), task_id))
            return self._one(self._q(cur, "SELECT id, task_id, uploader_id, filename, mime, size, created_at "
                                          "FROM zs_files WHERE id = ?", (fid,)))
        return self._run(fn)

    def get_file(self, file_id):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_files WHERE id = ?", (file_id,))))

    def delete_file(self, actor, file_id):
        def fn(cur):
            f = self._one(self._q(cur, "SELECT * FROM zs_files WHERE id = ?", (file_id,)))
            if not f:
                raise ZsError("Belge bulunamadı.")
            self._q(cur, "DELETE FROM zs_files WHERE id = ?", (file_id,))
            self._event(cur, f["task_id"], actor["id"], "file_deleted", filename=f["filename"])
            return f["storage_key"]
        return self._run(fn)

    # ── hatırlatıcılar ───────────────────────────────────────────────────────────────
    # task_id dolu → işe bağlı (iş başlığıyla gider); boş → işten bağımsız (kendi title'ı ile).
    def add_reminder(self, actor, task_id, user_id, remind_at, note="", title="", sub_id=None):
        now = _now()
        if not remind_at or remind_at < now - 60:
            raise ZsError("Hatırlatma zamanı geçmişte olamaz.")
        if remind_at > now + 2 * 366 * 86400:
            raise ZsError("Hatırlatma en fazla iki yıl sonrasına kurulabilir.")
        title = (title or "").strip()[:300]
        if task_id is None and not title:
            raise ZsError("Hatırlatıcıya bir başlık yazın.")

        def fn(cur):
            if task_id is None:
                n = self._q(cur, "SELECT COUNT(*) FROM zs_reminders WHERE task_id IS NULL AND user_id = ? AND sent_at IS NULL",
                            (user_id,)).fetchone()[0]
                if n >= 200:
                    raise ZsError("Bu kişinin en fazla 200 bekleyen bağımsız hatırlatıcısı olabilir.")
            else:
                n = self._q(cur, "SELECT COUNT(*) FROM zs_reminders WHERE task_id = ? AND user_id = ? AND sent_at IS NULL",
                            (task_id, user_id)).fetchone()[0]
                if n >= 20:
                    raise ZsError("Bu işte bu kişiye en fazla 20 bekleyen hatırlatıcı kurulabilir.")
            rid = self._insert(cur, "INSERT INTO zs_reminders (task_id, sub_id, user_id, created_by, remind_at, note, title, "
                                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (task_id, sub_id, user_id, actor["id"], int(remind_at), (note or "").strip()[:500], title, now))
            return self._one(self._q(cur, "SELECT * FROM zs_reminders WHERE id = ?", (rid,)))
        return self._run(fn)

    def list_reminders(self, task_id, viewer):
        """Yönetici işin tüm hatırlatıcılarını; diğerleri kendine kurulan ya da kendi kurduklarını görür."""
        sql = ("SELECT r.*, s.title AS sub_title FROM zs_reminders r LEFT JOIN zs_tasks s ON s.id = r.sub_id "
               "WHERE r.task_id = ?")
        args = (task_id,)
        if viewer["role"] != "admin":
            sql += " AND (r.user_id = ? OR r.created_by = ?)"
            args = (task_id, viewer["id"], viewer["id"])
        return self._run(lambda cur: self._rows(self._q(cur, sql + " ORDER BY r.remind_at, r.id", args)))

    def list_my_reminders(self, viewer, since):
        """Bana kurulan + benim kurduğum tüm hatırlatıcılar (işe bağlı ve bağımsız); gönderilmişler 'since'den beri.
        İş/alt iş başlıkları eklenir. Görünürlük denetimi çağıranda."""
        return self._run(lambda cur: self._rows(self._q(
            cur, "SELECT r.*, t.title AS task_title, t.is_private AS task_private, s.title AS sub_title "
                 "FROM zs_reminders r LEFT JOIN zs_tasks t ON t.id = r.task_id LEFT JOIN zs_tasks s ON s.id = r.sub_id "
                 "WHERE (r.user_id = ? OR r.created_by = ?) AND (r.sent_at IS NULL OR r.remind_at >= ?) "
                 "ORDER BY r.remind_at, r.id", (viewer["id"], viewer["id"], since))))

    def list_free_reminders(self, viewer, since):
        """İşten bağımsız hatırlatıcılar: kişiye kurulan ya da kişinin kurduğu; gönderilmişler 'since'den beri."""
        return self._run(lambda cur: self._rows(self._q(
            cur, "SELECT * FROM zs_reminders WHERE task_id IS NULL AND (user_id = ? OR created_by = ?) "
                 "AND (sent_at IS NULL OR remind_at >= ?) ORDER BY remind_at, id", (viewer["id"], viewer["id"], since))))

    # ── iş şablonları ────────────────────────────────────────────────────────────────
    def list_templates(self, user):
        """{hazir: [...], buro: [...], surecler: [...]} — büro şablonları kullanıcının bürosununkiler (ada göre)."""
        def fn(cur):
            return self._rows(self._q(cur, "SELECT t.*, u.display_name AS created_by_name FROM zs_templates t "
                                           "LEFT JOIN zs_users u ON u.id = t.created_by "
                                           "WHERE COALESCE(t.office_id, '') = ? ORDER BY LOWER(t.title), t.id",
                                      (office_of(user),)))
        buro = [self._template_row(t) for t in self._run(fn)]
        return {"hazir": [dict(t) for t in HAZIR_SABLONLAR], "buro": buro, "surecler": HAZIR_SURECLER}

    @staticmethod
    def _template_row(t):
        """Büro şablon satırı → istemci biçimi (sonuclar ve etiketler JSON'dan çözülür)."""
        try:
            t["sonuclar"] = clean_sonuclar(json.loads(t.get("sonuclar") or "{}"))
        except (ValueError, ZsError):
            t["sonuclar"] = clean_sonuclar({})
        try:
            et = json.loads(t.get("etiketler") or "[]")
        except ValueError:
            et = []
        t["etiketler"] = [str(k) for k in et if isinstance(k, (str, int))] if isinstance(et, list) else []
        return t

    @staticmethod
    def _template_fields(title, description, due_days, priority, taraf, action, kontrol=None, sonuclar=None):
        title = " ".join((title or "").split())[:200]
        if not title:
            raise ZsError("Şablon adı yazın.")
        if due_days is not None and not 0 <= int(due_days) <= 365:
            raise ZsError("Süre 0–365 gün olmalı.")
        if priority not in PRIORITIES:
            raise ZsError("Geçersiz öncelik.")
        return (title, (description or "").strip()[:2000], None if due_days is None else int(due_days), priority,
                taraf if taraf in TEMPLATE_TARAF else None, (str(action).strip()[:100] or None) if action else None,
                " ".join(str(kontrol or "").split())[:300] or None,
                json.dumps(clean_sonuclar(sonuclar), ensure_ascii=False))

    def _template_name_taken(self, cur, user, title, except_id=None):
        # Hazır şablonların adları da dolu sayılır: İş Ata penceresinde aynı adlı iki şablon görünmesin.
        rows = self._q(cur, "SELECT title FROM zs_templates WHERE COALESCE(office_id, '') = ? AND id <> ?",
                       (office_of(user), except_id or 0)).fetchall()
        return ad_key(title) in {ad_key(t["title"]) for t in HAZIR_SABLONLAR} | {ad_key(r[0]) for r in rows}

    def _own_template(self, cur, user, template_id, verb):
        t = self._one(self._q(cur, "SELECT * FROM zs_templates WHERE id = ? AND COALESCE(office_id, '') = ?",
                              (template_id, office_of(user))))
        if not t:
            raise ZsError("Şablon bulunamadı.")
        if user["role"] != "admin" and t["created_by"] != user["id"]:
            raise ZsError(f"Şablonu yalnız ekleyen ya da yönetici {verb}.")
        return t

    def _template_out(self, cur, tid):
        return self._template_row(self._one(self._q(cur, "SELECT * FROM zs_templates WHERE id = ?", (tid,))))

    def _template_tags(self, cur, user, etiketler):
        """Şablonun varsayılan etiketleri: yalnız hazır ve büroya açık etiketler (şablon büronundur)."""
        tags = self._check_tags(cur, user, etiketler)
        if any(t.get("owner_id") for t in tags):
            raise ZsError("Şablona kişisel etiket eklenemez; büroya açık bir etiket seçin.")
        if any(t["key"] in ANA_ETIKETLER for t in tags):
            raise ZsError("Dosya türü etiketi işin dosyasından gelir; şablona konmaz.")
        if any(t["key"] in ASAMA_ETIKETLERI for t in tags):
            raise ZsError("Aşama etiketi şablonun aşamasından gelir; aşamayı seçin.")
        return json.dumps([t["key"] for t in tags], ensure_ascii=False)

    def create_template(self, user, title, description="", due_days=None, priority=1, taraf=None, action=None,
                        kontrol=None, sonuclar=None, asama=None, etiketler=None):
        title, description, due_days, priority, taraf, action, kontrol, sonuclar = self._template_fields(
            title, description, due_days, priority, taraf, action, kontrol, sonuclar)
        asama = "/".join(asama_coz(asama)) if asama else None

        def fn(cur):
            n = self._q(cur, "SELECT COUNT(*) FROM zs_templates WHERE COALESCE(office_id, '') = ?",
                        (office_of(user),)).fetchone()[0]
            if n >= 200:
                raise ZsError("Bir büroda en çok 200 şablon olabilir.")
            if self._template_name_taken(cur, user, title):
                raise ZsError("Bu adla bir şablon zaten var.")
            tid = self._insert(cur, "INSERT INTO zs_templates (office_id, title, description, due_days, priority, "
                                    "taraf, action, kontrol, sonuclar, asama, etiketler, created_by, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (office_of(user) or None, title, description, due_days, priority, taraf, action,
                                kontrol, sonuclar, asama, self._template_tags(cur, user, etiketler), user["id"], _now()))
            return self._template_out(cur, tid)
        return self._run(fn)

    def update_template(self, user, template_id, title, description="", due_days=None, priority=1, taraf=None,
                        action=None, kontrol=None, sonuclar=None, asama=KEEP, etiketler=KEEP):
        """Şablonu ekleyen ya da büro yöneticisi düzenler (İş Listesi → Şablonlar). asama / etiketler
        verilmezse (KEEP) eskisi kalır — bu alanları bilmeyen UYAP penceresi düzenlerken silinmesin."""
        title, description, due_days, priority, taraf, action, kontrol, sonuclar = self._template_fields(
            title, description, due_days, priority, taraf, action, kontrol, sonuclar)
        if asama is not KEEP:
            asama = "/".join(asama_coz(asama)) if asama else None

        def fn(cur):
            old = self._own_template(cur, user, template_id, "düzenleyebilir")
            if self._template_name_taken(cur, user, title, except_id=template_id):
                raise ZsError("Bu adla bir şablon zaten var.")
            a = old["asama"] if asama is KEEP else asama
            e = old["etiketler"] if etiketler is KEEP else self._template_tags(cur, user, etiketler)
            self._q(cur, "UPDATE zs_templates SET title = ?, description = ?, due_days = ?, priority = ?, taraf = ?, "
                         "action = ?, kontrol = ?, sonuclar = ?, asama = ?, etiketler = ? WHERE id = ?",
                    (title, description, due_days, priority, taraf, action, kontrol, sonuclar, a, e, template_id))
            return self._template_out(cur, template_id)
        return self._run(fn)

    def delete_template(self, user, template_id):
        """Şablonu ekleyen ya da büro yöneticisi siler. Bu şablondan açılmış işler süreç ve aşamasını korur."""
        def fn(cur):
            t = self._own_template(cur, user, template_id, "silebilir")
            self._q(cur, "DELETE FROM zs_templates WHERE id = ?", (template_id,))
            return t
        return self._run(fn)

    def _sablon_bul(self, cur, office, sablon=None, title=None, cache=None):
        """İşin şablonu: önce verilen anahtar ("h-mazbata" | "b-12" | 12), yoksa başlığın şablon kısmı
        hazır + büro şablon adlarıyla, sonra hazır şablonların eski adlarıyla eşlenir.
        (anahtar, {asama, etiketler}) ya da ('', None)."""
        if cache is not None and office in cache:
            buro = cache[office]
        else:
            buro = self._rows(self._q(cur, "SELECT id, title, asama, etiketler FROM zs_templates "
                                           "WHERE COALESCE(office_id, '') = ?", (office or "",)))
            if cache is not None:
                cache[office] = buro
        s = str(sablon or "").strip()
        if s in _HAZIR_SABLON:
            return s, _HAZIR_SABLON[s]
        m = re.fullmatch(r"(?:b-)?(\d{1,12})", s)
        for t in buro:
            if m and t["id"] == int(m.group(1)):
                return f"b-{t['id']}", self._template_row(dict(t))
        k = ad_key(is_adi(title))
        if k:
            for t in HAZIR_SABLONLAR:
                if ad_key(t["title"]) == k:
                    return t["id"], t
            for t in buro:
                if ad_key(t["title"]) == k:
                    return f"b-{t['id']}", self._template_row(dict(t))
            for t in HAZIR_SABLONLAR:
                if k in {ad_key(e) for e in t.get("eski", ())}:
                    return t["id"], t
        return "", None

    def _tag_task(self, cur, task_id, keys, actor_id):
        for k in keys:
            self._q(cur, "INSERT INTO zs_task_tags (task_id, tag, added_by, created_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT (task_id, tag) DO NOTHING", (task_id, k, actor_id, _now()))

    def _asama_yaz(self, cur, task_id, surec, asama, actor_id):
        """İşin aşama etiketi: eski aşamanınki kalkar, yenisi takılır (aşama boşsa hiçbiri)."""
        self._q(cur, f"DELETE FROM zs_task_tags WHERE task_id = ? AND tag IN ({','.join('?' * len(ASAMA_ETIKETLERI))})",
                (task_id, *sorted(ASAMA_ETIKETLERI)))
        if asama:
            self._tag_task(cur, task_id, [asama_etiketi(surec, asama)], actor_id)

    def _sablondan(self, cur, office, tpl, tur):
        """Şablonun işe verdikleri: (süreç, aşama, etiket anahtarları). Süreç ve alt etiketler yalnız
        işin dosya türüne uyuyorsa gelir (ör. icra süreci hukuk dosyasındaki işe verilmez)."""
        surec = asama = None
        if tpl and tpl.get("asama") in _ASAMALAR:
            sk, ak = _ASAMALAR[tpl["asama"]]
            st = next((s.get("tur") for s in HAZIR_SURECLER if s["key"] == sk), None)
            if tur is None or st is None or st == tur:
                surec, asama = sk, ak
        keys = list((tpl or {}).get("etiketler") or []) + ([asama_etiketi(surec, asama)] if asama else [])
        return surec, asama, self._known_tags(cur, office, keys, tur)

    def _ana_etiket(self, cur, task_id, tur, actor_id):
        """Dosyaya bağlı işin ana etiketi (dosya türü) kendiliğinden takılır; başka türün ana etiketi kalkar."""
        for k in ANA_ETIKETLER:
            if tur and k != f"e-{tur}":
                self._q(cur, "DELETE FROM zs_task_tags WHERE task_id = ? AND tag = ?", (task_id, k))
        if tur:
            self._tag_task(cur, task_id, [f"e-{tur}"], actor_id)

    def backfill_sablon(self):
        """Şablon bağı olmayan (bu özellikten önce açılmış) ana işleri başlıktan şablonlarına bağlar:
        süreç, aşama, şablonun etiketleri ve dosya türünün ana etiketi yazılır. Eşleşmeyen iş '' olur,
        bir daha taranmaz."""
        def fn(cur):
            rows = self._rows(self._q(cur, "SELECT id, title, office_id, dosya_id, dosya_tur, birim_adi FROM zs_tasks "
                                           "WHERE parent_id IS NULL AND sablon IS NULL"))
            cache, n = {}, 0
            for r in rows:
                tur = task_turu(r)
                key, tpl = self._sablon_bul(cur, r["office_id"] or "", None, r["title"], cache)
                surec, asama, tags = self._sablondan(cur, r["office_id"] or "", tpl, tur)
                self._q(cur, "UPDATE zs_tasks SET sablon = ?, surec = ?, asama = ? WHERE id = ?",
                        (key, surec, asama, r["id"]))
                self._tag_task(cur, r["id"], tags, None)
                self._ana_etiket(cur, r["id"], tur, None)
                n += 1 if tpl else 0
            return n
        return self._run(fn)

    # ── etiketler ────────────────────────────────────────────────────────────────────
    # Ağaç (en çok 3 katman): dosya türü (ana etiket, kodda) → süreç / grup → alt etiket. Bir dosya türünün
    # altındaki etiket yalnız o türdeki dosyanın işine takılır; kökü dosya türü olmayan etiket geneldir.
    @staticmethod
    def _tag_out(t):
        return {"key": str(t["id"]), "id": t["id"], "name": t["name"], "color": t["color"] or ETIKET_RENKLERI[-1],
                "parent": t.get("parent") or None, "owner_id": t["owner_id"], "created_by": t["created_by"]}

    @staticmethod
    def _agac(tags):
        """{anahtar: etiket} sözlüğüne tur (kök dosya türü) ve derinlik (1 = kök) ekler."""
        for k, t in tags.items():
            t["tur"] = tag_turu(k, tags)
            t["derinlik"] = len(tag_zinciri(k, tags))
        return tags

    def _visible_tags(self, cur, user):
        """{anahtar: etiket} — hazır + büroya açık + kullanıcının kendi kişisel etiketleri."""
        out = {e["key"]: dict(e, owner_id=None, created_by=None) for e in HAZIR_ETIKETLER}
        for t in self._rows(self._q(cur, "SELECT * FROM zs_tags WHERE COALESCE(office_id, '') = ? "
                                         "AND (owner_id IS NULL OR owner_id = ?) ORDER BY LOWER(name), id",
                                    (office_of(user), user["id"]))):
            out[str(t["id"])] = self._tag_out(t)
        return self._agac(out)

    def _shared_tags(self, cur, office):
        """Hazır + büroya açık etiketler (ağaç bilgisiyle) — kullanıcıdan bağımsız işlemler için."""
        out = {e["key"]: dict(e, owner_id=None, created_by=None) for e in HAZIR_ETIKETLER}
        for t in self._rows(self._q(cur, "SELECT * FROM zs_tags WHERE COALESCE(office_id, '') = ? AND owner_id IS NULL",
                                    (office or "",))):
            out[str(t["id"])] = self._tag_out(t)
        return self._agac(out)

    def _known_tags(self, cur, office, keys, tur=None):
        """Büroda var olan, paylaşılan ve işin türüne uyan etiket anahtarları (şablondan gelen listede
        silinmişler düşer; dosya türü etiketi dosyadan geldiği için atlanır)."""
        keys = [str(k) for k in (keys or [])]
        if not keys:
            return []
        tags = self._shared_tags(cur, office)
        return [k for k in dict.fromkeys(keys) if k in tags and k not in ANA_ETIKETLER and tag_uyar(tags[k], tur)]

    def _check_tags(self, cur, user, keys):
        """Kullanıcının görebildiği etiketler (anahtar sırasıyla); tanımadığı anahtar varsa ZsError."""
        if keys is None:
            return []
        if not isinstance(keys, (list, tuple)):
            raise ZsError("Geçersiz etiket listesi.")
        vis = self._visible_tags(cur, user)
        out = []
        for k in dict.fromkeys(str(k) for k in keys):
            if k not in vis:
                raise ZsError("Etiket bulunamadı.")
            out.append(vis[k])
        return out

    @staticmethod
    def _check_fit(tags, tur):
        for g in tags:
            if g["key"] in ASAMA_ETIKETLERI:
                raise ZsError("Aşama etiketi işin aşamasından gelir; aşamayı “Düzenle”den değiştirin.")
            if g["key"] in ANA_ETIKETLER and tur is not None:
                raise ZsError("Dosya türü etiketi dosyadan gelir; elle değiştirilemez.")
            if not tag_uyar(g, tur):
                raise ZsError(f"“{g['name']}” etiketi bu dosya türüne ait değil.")

    def list_tags(self, user):
        def fn(cur):
            vis = self._visible_tags(cur, user)
            custom = [t for k, t in vis.items() if k not in _HAZIR_ETIKET]
            return {"hazir": [vis[e["key"]] for e in HAZIR_ETIKETLER], "buro": [t for t in custom if not t["owner_id"]],
                    "kisisel": [t for t in custom if t["owner_id"]], "renkler": ETIKET_RENKLERI,
                    "derinlik": ETIKET_DERINLIK,
                    "turler": [{"key": k, "name": ad, "etiket": f"e-{k}"} for k, ad, _, _ in DOSYA_TURLERI]}
        return self._run(fn)

    def _tag_name(self, cur, user, name, color, parent, except_key=None):
        """Ad aynı üst etiketin altında (ya da kökte) tekil olmalı; dosya türü adları her yerde dolu sayılır."""
        name = " ".join(str(name or "").split())[:40]
        if not name:
            raise ZsError("Etiket adı yazın.")
        taken = {ad_key(t["name"]) for k, t in self._visible_tags(cur, user).items()
                 if k != except_key and ((t.get("parent") or None) == parent or k in ANA_ETIKETLER)}
        if ad_key(name) in taken:
            raise ZsError("Bu adla bir etiket zaten var.")
        return name, color if color in ETIKET_RENKLERI else ETIKET_RENKLERI[0]

    def _parent(self, cur, user, parent, kisisel, tag_key=None):
        """Üst etiket: görünen bir etiket olmalı; büro etiketi kişisel etiketin altına konamaz; ağaç
        ETIKET_DERINLIK katmanı aşamaz ve etiket kendi altına taşınamaz."""
        parent = str(parent or "").strip() or None
        if not parent:
            return None
        vis = self._visible_tags(cur, user)
        p = vis.get(parent)
        if not p:
            raise ZsError("Üst etiket bulunamadı.")
        if p["owner_id"] and not kisisel:
            raise ZsError("Büro etiketi kişisel bir etiketin altına konamaz.")
        if tag_key and tag_key in tag_zinciri(parent, vis):
            raise ZsError("Etiket kendi altına taşınamaz.")
        alt = 0
        if tag_key:     # taşınan etiketin altındaki katman sayısı
            alt = max((len(tag_zinciri(k, vis)) - vis[tag_key]["derinlik"] for k in vis
                       if tag_key in tag_zinciri(k, vis)), default=0)
        if p["derinlik"] + 1 + alt > ETIKET_DERINLIK:
            raise ZsError(f"Etiket ağacı en çok {ETIKET_DERINLIK} katman olabilir.")
        return parent

    def create_tag(self, user, name, color=None, kisisel=False, parent=None):
        """Herkes etiket ekler: kişisel (yalnız kendisi görür) ya da büroya açık; bir etiketin altında
        (ör. İcra → İcra takibi – Kesinleşme → yeni etiket) ya da kökte (genel)."""
        def fn(cur):
            n = self._q(cur, "SELECT COUNT(*) FROM zs_tags WHERE COALESCE(office_id, '') = ?",
                        (office_of(user),)).fetchone()[0]
            if n >= 300:
                raise ZsError("Bir büroda en çok 300 etiket olabilir.")
            p = self._parent(cur, user, parent, kisisel)
            nm, col = self._tag_name(cur, user, name, color, p)
            tid = self._insert(cur, "INSERT INTO zs_tags (office_id, owner_id, name, color, parent, created_by, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (office_of(user) or None, user["id"] if kisisel else None, nm, col, p, user["id"], _now()))
            return self._visible_tags(cur, user)[str(tid)]
        return self._run(fn)

    def _own_tag(self, cur, user, tag_id, verb):
        t = self._one(self._q(cur, "SELECT * FROM zs_tags WHERE id = ? AND COALESCE(office_id, '') = ?",
                              (tag_id, office_of(user))))
        if not t or (t["owner_id"] and t["owner_id"] != user["id"]):
            raise ZsError("Etiket bulunamadı.")
        if not t["owner_id"] and user["role"] != "admin" and t["created_by"] != user["id"]:
            raise ZsError(f"Büro etiketini yalnız ekleyen ya da yönetici {verb}.")
        return t

    def update_tag(self, user, tag_id, name, color=None, parent=KEEP):
        """Ad / renk / üst etiket. Taşınan etiket artık uymadığı işlerde de kalır (iz silinmez)."""
        def fn(cur):
            t = self._own_tag(cur, user, tag_id, "düzenleyebilir")
            p = (t.get("parent") or None) if parent is KEEP else \
                self._parent(cur, user, parent, bool(t["owner_id"]), tag_key=str(tag_id))
            nm, col = self._tag_name(cur, user, name, color or t["color"], p, except_key=str(tag_id))
            self._q(cur, "UPDATE zs_tags SET name = ?, color = ?, parent = ? WHERE id = ?", (nm, col, p, tag_id))
            return self._visible_tags(cur, user)[str(tag_id)]
        return self._run(fn)

    def delete_tag(self, user, tag_id):
        """Etiketi siler; işlerdeki bağları da kalkar (şablonlardaki anahtar okurken düşer). Altında
        etiket olan etiket silinmez (önce alt etiketler silinir ya da taşınır)."""
        def fn(cur):
            t = self._own_tag(cur, user, tag_id, "silebilir")
            if self._q(cur, "SELECT COUNT(*) FROM zs_tags WHERE parent = ?", (str(tag_id),)).fetchone()[0]:
                raise ZsError("Bu etiketin altında etiket var; önce onları silin ya da taşıyın.")
            self._q(cur, "DELETE FROM zs_task_tags WHERE tag = ?", (str(tag_id),))
            self._q(cur, "DELETE FROM zs_tags WHERE id = ?", (tag_id,))
            return self._tag_out(t)
        return self._run(fn)

    def set_task_tags(self, actor, task_id, add=(), remove=(), can_shared=False):
        """Ana işe etiket ekler / çıkarır. Kişisel etiketi işi görebilen herkes kendisi için takar;
        hazır ve büro etiketleri için can_shared (atanan / yönetici — çağıran denetler) gerekir.
        Etiket işin dosya türüne uymalı; dosya türü (ana) etiketi dosyadan gelir.
        Olay yalnız paylaşılan etiketler için yazılır (kişisel etiket adları başkasına görünmesin)."""
        def fn(cur):
            t = self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
            if not t or t["parent_id"]:
                raise ZsError("Etiket yalnız ana işe eklenir.")
            tags_add, tags_rem = self._check_tags(cur, actor, list(add or ())), self._check_tags(cur, actor, list(remove or ()))
            if not can_shared and any(not x["owner_id"] for x in tags_add + tags_rem):
                raise ZsError("Bu işe büro etiketi yalnız işe atanan ya da yönetici ekler; kişisel etiket kullanın.")
            tur = task_turu(t)
            self._check_fit(tags_add, tur)
            if tur is not None and any(x["key"] in ANA_ETIKETLER for x in tags_rem):
                raise ZsError("Dosya türü etiketi dosyadan gelir; elle değiştirilemez.")
            if any(x["key"] in ASAMA_ETIKETLERI for x in tags_rem):
                raise ZsError("Aşama etiketi işin aşamasından gelir; aşamayı “Düzenle”den değiştirin.")
            have = {r[0] for r in self._q(cur, "SELECT tag FROM zs_task_tags WHERE task_id = ?", (task_id,)).fetchall()}
            added = [x for x in tags_add if x["key"] not in have]
            removed = [x for x in tags_rem if x["key"] in have]
            self._tag_task(cur, task_id, [x["key"] for x in added], actor["id"])
            for x in removed:
                self._q(cur, "DELETE FROM zs_task_tags WHERE task_id = ? AND tag = ?", (task_id, x["key"]))
            shared_add = [x["name"] for x in added if not x["owner_id"]]
            shared_rem = [x["name"] for x in removed if not x["owner_id"]]
            if shared_add or shared_rem:
                self._event(cur, task_id, actor["id"], "tags", added=shared_add, removed=shared_rem)
                self._q(cur, "UPDATE zs_tasks SET updated_at = ? WHERE id = ?", (_now(), task_id))
            vis = self._visible_tags(cur, actor)
            return [r[0] for r in self._q(cur, "SELECT tag FROM zs_task_tags WHERE task_id = ? ORDER BY created_at, tag",
                                          (task_id,)).fetchall() if r[0] in vis]
        return self._run(fn)

    def _task_tags(self, cur, ids, viewer):
        """{task_id: [görünen etiket anahtarları]}"""
        if not ids:
            return {}
        vis = self._visible_tags(cur, viewer) if viewer else None
        marks = ",".join("?" * len(ids))
        out = {}
        for tid, tag in self._q(cur, f"SELECT task_id, tag FROM zs_task_tags WHERE task_id IN ({marks}) "
                                     "ORDER BY created_at, tag", list(ids)).fetchall():
            if vis is None or tag in vis:
                out.setdefault(tid, []).append(tag)
        return out

    def get_reminder(self, rid):
        return self._run(lambda cur: self._one(self._q(cur, "SELECT * FROM zs_reminders WHERE id = ?", (rid,))))

    def delete_reminder(self, rid):
        self._run(lambda cur: self._q(cur, "DELETE FROM zs_reminders WHERE id = ?", (rid,)))

    def claim_due_reminders(self, now=None, limit=100):
        """Zamanı gelmiş, gönderilmemiş hatırlatıcıları 'gönderildi' işaretleyip döndürür. İşaretleme
        satır satır koşullu yapılır; iki süreç aynı anda çalışsa da her hatırlatıcı bir kez gider."""
        now = now or _now()

        def fn(cur):
            rows = self._rows(self._q(cur, "SELECT r.*, t.title AS task_title, t.due_at, t.status, s.title AS sub_title "
                                           "FROM zs_reminders r LEFT JOIN zs_tasks t ON t.id = r.task_id "
                                           "LEFT JOIN zs_tasks s ON s.id = r.sub_id "
                                           "WHERE r.sent_at IS NULL AND r.remind_at <= ? ORDER BY r.remind_at LIMIT ?",
                                      (now, limit)))
            out = []
            for r in rows:
                cur.execute(self._sql("UPDATE zs_reminders SET sent_at = ? WHERE id = ? AND sent_at IS NULL"),
                            (now, r["id"]))
                # İşe bağlıyken iş silinmişse gönderilmez (iş silinince satırları da silinir; yarış durumu için).
                if cur.rowcount == 1 and (r["task_id"] is None or r["task_title"] is not None):
                    out.append(r)
            names = {r[0]: r[1] for r in self._q(cur, "SELECT id, display_name FROM zs_users WHERE active = 1").fetchall()}
            return [dict(r, user_active=r["user_id"] in names, by_name=names.get(r["created_by"], "")) for r in out]
        return self._run(fn)


def _is_conn_error(e) -> bool:
    name = type(e).__name__
    return name in ("OperationalError", "InterfaceError")
