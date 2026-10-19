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
import json
import time
import sqlite3
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


def _now() -> int:
    return int(time.time())


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
                 ("taraf_adi", "TEXT")),
    "zs_reminders": (("sub_id", "BIGINT"), ("title", "TEXT NOT NULL DEFAULT ''")),
    # Aşama 2: kontrol sorusu + sonuç dalları (JSON, bkz. TEMPLATE_SONUCLAR).
    "zs_templates": (("kontrol", "TEXT"), ("sonuclar", "TEXT")),
    # Telegram kullanıcı adı (küçük harf, @ siz) — grupta @etiketle kişi bulmak için.
    "zs_telegram": (("tg_username", "TEXT"),),
    # Ana CokluUyap hesabına bağ (ahmet@ofis) + o hesabın bürosu. Boşsa eski /zs kullanıcısı.
    "zs_users": (("account_username", "TEXT"), ("office_id", "TEXT")),
}
# Sütunlar eklendikten sonra kurulan indeksler.
_POST_INDEXES = (
    "CREATE UNIQUE INDEX IF NOT EXISTS zs_users_account ON zs_users(account_username) "
    "WHERE account_username IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS zs_tasks_office_dosya ON zs_tasks(office_id, dosya_id)",
    "CREATE INDEX IF NOT EXISTS zs_tasks_office_birim_dosya ON zs_tasks(office_id, birim_id, dosya_no)",
)
# UYAP dosya alanları (görevde).
DOSYA_FIELDS = ("dosya_id", "birim_id", "birim_adi", "dosya_no", "dosya_tur", "dosya_uyap", "evrak_id", "evrak_adi",
                "taraf_adi")
# UYAP'ın dosya kartı (dosya-sorgula-popup) bu alanlarla açılır — UYAP'ın kendi arama sonucundan
# kartı açarken verdiği nesneyle aynı anahtarlar.
UYAP_KART_ALANLARI = ("dosyaId", "dosyaNo", "birimId", "birimAdi", "dosyaTurKod", "dosyaTurKodAciklama",
                      "dosyaDurum", "dosyaDurumKod", "birimTuru3")

# Hazır iş şablonları (her büroya açık, değiştirilemez). Büro şablonları zs_templates'te.
# taraf: işin bir tarafa yapılıp yapılmadığı (borclu | alacakli | herhangi | None).
# action: önerilen UYAP aksiyonu (Aşama 3, henüz yürütülmez) — bkz. "yapılacak iş.md".
# kontrol + sonuclar (Aşama 2): iş bitirilirken sorulan soru ve sonuca göre önerilen sonraki iş
# {olumlu|olumsuz|bekliyor: {baslik, gun}}; baslik None = aynı iş yeniden (ör. bekliyor → +3 gün).
def _sonuc(olumlu=None, olumsuz=None, bekliyor=None):
    def one(x):
        if x is None:
            return None
        if isinstance(x, int):
            return {"baslik": None, "gun": x}
        return {"baslik": x[0], "gun": x[1]}
    return {"olumlu": one(olumlu), "olumsuz": one(olumsuz), "bekliyor": one(bekliyor)}


HAZIR_SABLONLAR = (
    {"id": "h-tebligat", "title": "Tebligat kontrol", "due_days": 3, "priority": 1, "taraf": "borclu",
     "description": "Safahat / tebligat barkodundan tebliğ sonucunu kontrol et.", "action": "ekran:safahat",
     "kontrol": "Tebligat tebliğ edildi mi?",
     "sonuclar": _sonuc(("Kesinleşme kontrol", 7), ("Adres araştırması", 2), 3)},
    {"id": "h-odeme-emri", "title": "Ödeme emri kontrol", "due_days": 3, "priority": 1, "taraf": "borclu",
     "description": "Ödeme emrinin gönderilip gönderilmediğini kontrol et.", "action": "ekran:evrak",
     "kontrol": "Ödeme emri gönderildi mi?",
     "sonuclar": _sonuc(("Tebligat kontrol", 10), ("Ödeme emri yeniden gönder", 1), 3)},
    {"id": "h-kesinlesme", "title": "Kesinleşme kontrol", "due_days": 7, "priority": 1, "taraf": "borclu",
     "description": "Takibin borçlu yönünden kesinleşip kesinleşmediğini kontrol et.", "action": "ekran:safahat",
     "kontrol": "Takip kesinleşti mi?",
     "sonuclar": _sonuc(("Haciz talepleri", 2), ("Tebligat kontrol", 3), 3)},
    {"id": "h-takyidat", "title": "Takyidat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "Araç / tapu takyidat sorgu sonucunu kontrol et.", "action": "sorgu:takyidat",
     "kontrol": "Borçlu adına araç / taşınmaz var mı?",
     "sonuclar": _sonuc(("Haciz / yakalama talebi", 2), ("Mal varlığı sorgusu", 3), 2)},
    {"id": "h-barkod", "title": "Barkod takip", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "PTT barkodunda tebligatın son durumunu kontrol et.", "action": None,
     "kontrol": "Barkodda teslim görünüyor mu?",
     "sonuclar": _sonuc(("Tebligat kontrol", 3), ("Adres araştırması", 2), 2)},
    {"id": "h-21-2", "title": "21/2 akıbet kontrol", "due_days": 5, "priority": 1, "taraf": "borclu",
     "description": "21/2 tebligatın akıbetini kontrol et.", "action": "ekran:safahat",
     "kontrol": "21/2 tebligat tebliğ edildi mi?",
     "sonuclar": _sonuc(("Kesinleşme kontrol", 7), ("İlanen tebligat talebi", 2), 5)},
    {"id": "h-reddiyat", "title": "Reddiyat kontrol", "due_days": 2, "priority": 1, "taraf": None,
     "description": "Reddiyatın hesaba geçip geçmediğini kontrol et.", "action": None,
     "kontrol": "Reddiyat hesaba geçti mi?",
     "sonuclar": _sonuc(("Müvekkile bildirim", 1), ("Muhasebe ile görüş", 1), 2)},
    {"id": "h-tahsilat", "title": "Tahsilat kontrol", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "Tahsilatı kontrol et, müvekkile bildir.", "action": None,
     "kontrol": "Tahsilat hesaba geçti mi?",
     "sonuclar": _sonuc(("Müvekkile bildirim", 1), ("Muhasebe ile görüş", 1), 2)},
    {"id": "h-aranacak", "title": "Borçlu aranacak", "due_days": 1, "priority": 1, "taraf": "borclu",
     "description": "Borçluya ulaş, sonucu nota yaz.", "action": None,
     "kontrol": "Borçluya ulaşıldı mı?",
     "sonuclar": _sonuc(("Ödeme planı takibi", 7), 2, None)},
    {"id": "h-adres", "title": "Adres araştırması", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "MERNİS / SGK / GSM adres araştırması yap, yeni adrese tebligat çıkar.",
     "action": "talep:adres_arastirmasi",
     "kontrol": "Yeni adres bulundu mu?",
     "sonuclar": _sonuc(("Tebligat kontrol", 10), ("İlanen tebligat talebi", 2), 3)},
    {"id": "h-haciz", "title": "Haciz talepleri", "due_days": 2, "priority": 1, "taraf": "borclu",
     "description": "SGK, banka, araç ve tapu haciz taleplerini hazırla.", "action": None,
     "kontrol": None, "sonuclar": _sonuc()},
    {"id": "h-muvekkil", "title": "Müvekkile bildirim", "due_days": 1, "priority": 1, "taraf": None,
     "description": "Dosyadaki gelişmeyi müvekkile bildir.", "action": None,
     "kontrol": None, "sonuclar": _sonuc()},
)
TEMPLATE_TARAF = ("borclu", "alacakli", "herhangi")
TEMPLATE_SONUCLAR = ("olumlu", "olumsuz", "bekliyor")


def clean_sonuclar(d):
    """{olumlu|olumsuz|bekliyor: {baslik, gun} | None} → temiz sözlük (bilinmeyen anahtarlar atılır)."""
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
        out[k] = {"baslik": baslik, "gun": gun} if baslik or gun is not None else None
    return out


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
                    n = self._q(cur, "SELECT COUNT(*) FROM zs_users WHERE role = 'admin' AND active = 1 AND id <> ?",
                                (user_id,)).fetchone()[0]
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
                n = self._q(cur, "SELECT COUNT(*) FROM zs_users WHERE role = 'admin' AND active = 1 AND id <> ?",
                            (user_id,)).fetchone()[0]
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
                for tid, body in self._q(cur, f"SELECT task_id, body FROM zs_notes WHERE task_id IN ({om}) "
                                              "AND body LIKE 'Onaya gönderme notu: %' ORDER BY id", onayda).fetchall():
                    gnot[tid] = body[len("Onaya gönderme notu: "):]
            rem = dict(self._q(cur, f"SELECT task_id, MIN(remind_at) FROM zs_reminders WHERE user_id = ? AND sent_at IS NULL "
                                    f"AND task_id IN ({marks}) GROUP BY task_id", [uid] + ids).fetchall())
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
                    private=False, priority=1, dosya=None):
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
            tid = self._insert(cur, "INSERT INTO zs_tasks (parent_id, title, description, assignee_id, created_by, "
                                    "due_at, status, sort_order, created_at, updated_at, is_private, priority, office_id, "
                                    f"{', '.join(DOSYA_FIELDS)}) "
                                    f"VALUES ({', '.join('?' * (13 + len(DOSYA_FIELDS)))})",
                               (parent_id, title[:300], (description or "")[:5000], aid, actor["id"], due_at,
                                status, self._next_sort(cur, parent_id), now, now,
                                1 if (private and not parent_id) else 0, 1 if parent_id else priority,
                                office_of(actor) or None, *(d.get(k) for k in DOSYA_FIELDS)))
            root = parent_id or tid
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
                changed = [k for k in ("title", "description", "due_at", "priority", "dosya") if k in fields]
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
                      "DELETE FROM zs_tasks WHERE parent_id = ?", "DELETE FROM zs_tasks WHERE id = ?"):
                self._q(cur, q, (task_id,))
            # Silme izi kalsın: olaylar silinmez, task_id'si silinmiş işe işaret eder.
            self._event(cur, task_id, actor["id"], "deleted", title=t["title"])
            return keys
        return self._run(fn)

    # ── onay akışı ───────────────────────────────────────────────────────────────────
    def transition(self, actor, task_id, action, reason=""):
        """action: basla | gonder | tamamla | onayla | reddet | yeniden_ac. Yetki kontrolü burada."""
        is_admin = actor["role"] == "admin"

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
                self._q(cur, "UPDATE zs_tasks SET submitted_at = ? WHERE id = ?", (now, task_id))
            reason_txt = (reason or "").strip()[:2000]
            self._event(cur, task_id, actor["id"], kind, old=st, new=new, reason=reason_txt)
            if reason_txt:
                label = {"rejected": "Ret gerekçesi", "approved": "Onay notu", "submitted": "Onaya gönderme notu",
                         "completed": "Tamamlama notu", "reopened": "Yeniden açma gerekçesi"}.get(kind, "Not")
                self._q(cur, "INSERT INTO zs_notes (task_id, author_id, body, note_at, created_at) "
                             "VALUES (?, ?, ?, NULL, ?)", (task_id, actor["id"], f"{label}: {reason_txt}", now))
            return self._one(self._q(cur, "SELECT * FROM zs_tasks WHERE id = ?", (task_id,)))
        return self._run(fn)

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
        """{hazir: [...], buro: [...]} — büro şablonları kullanıcının bürosununkiler (ada göre)."""
        def fn(cur):
            return self._rows(self._q(cur, "SELECT t.*, u.display_name AS created_by_name FROM zs_templates t "
                                           "LEFT JOIN zs_users u ON u.id = t.created_by "
                                           "WHERE COALESCE(t.office_id, '') = ? ORDER BY LOWER(t.title), t.id",
                                      (office_of(user),)))
        buro = self._run(fn)
        for t in buro:
            try:
                t["sonuclar"] = clean_sonuclar(json.loads(t.get("sonuclar") or "{}"))
            except (ValueError, ZsError):
                t["sonuclar"] = clean_sonuclar({})
        return {"hazir": [dict(t) for t in HAZIR_SABLONLAR], "buro": buro}

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
        # Karşılaştırma Python'da: SQLite LOWER() Türkçe harfleri (Ş/ş, İ/i) eşlemez.
        def key(s):
            return s.replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
        rows = self._q(cur, "SELECT title FROM zs_templates WHERE COALESCE(office_id, '') = ? AND id <> ?",
                       (office_of(user), except_id or 0)).fetchall()
        return key(title) in {key(t["title"]) for t in HAZIR_SABLONLAR} | {key(r[0]) for r in rows}

    def _own_template(self, cur, user, template_id, verb):
        t = self._one(self._q(cur, "SELECT * FROM zs_templates WHERE id = ? AND COALESCE(office_id, '') = ?",
                              (template_id, office_of(user))))
        if not t:
            raise ZsError("Şablon bulunamadı.")
        if user["role"] != "admin" and t["created_by"] != user["id"]:
            raise ZsError(f"Şablonu yalnız ekleyen ya da yönetici {verb}.")
        return t

    def _template_out(self, cur, tid):
        t = self._one(self._q(cur, "SELECT * FROM zs_templates WHERE id = ?", (tid,)))
        t["sonuclar"] = clean_sonuclar(json.loads(t.get("sonuclar") or "{}"))
        return t

    def create_template(self, user, title, description="", due_days=None, priority=1, taraf=None, action=None,
                        kontrol=None, sonuclar=None):
        title, description, due_days, priority, taraf, action, kontrol, sonuclar = self._template_fields(
            title, description, due_days, priority, taraf, action, kontrol, sonuclar)

        def fn(cur):
            n = self._q(cur, "SELECT COUNT(*) FROM zs_templates WHERE COALESCE(office_id, '') = ?",
                        (office_of(user),)).fetchone()[0]
            if n >= 200:
                raise ZsError("Bir büroda en çok 200 şablon olabilir.")
            if self._template_name_taken(cur, user, title):
                raise ZsError("Bu adla bir şablon zaten var.")
            tid = self._insert(cur, "INSERT INTO zs_templates (office_id, title, description, due_days, priority, "
                                    "taraf, action, kontrol, sonuclar, created_by, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (office_of(user) or None, title, description, due_days, priority, taraf, action,
                                kontrol, sonuclar, user["id"], _now()))
            return self._template_out(cur, tid)
        return self._run(fn)

    def update_template(self, user, template_id, title, description="", due_days=None, priority=1, taraf=None,
                        action=None, kontrol=None, sonuclar=None):
        """Şablonu ekleyen ya da büro yöneticisi düzenler (İş Listesi → Şablonlar)."""
        title, description, due_days, priority, taraf, action, kontrol, sonuclar = self._template_fields(
            title, description, due_days, priority, taraf, action, kontrol, sonuclar)

        def fn(cur):
            self._own_template(cur, user, template_id, "düzenleyebilir")
            if self._template_name_taken(cur, user, title, except_id=template_id):
                raise ZsError("Bu adla bir şablon zaten var.")
            self._q(cur, "UPDATE zs_templates SET title = ?, description = ?, due_days = ?, priority = ?, taraf = ?, "
                         "action = ?, kontrol = ?, sonuclar = ? WHERE id = ?",
                    (title, description, due_days, priority, taraf, action, kontrol, sonuclar, template_id))
            return self._template_out(cur, template_id)
        return self._run(fn)

    def delete_template(self, user, template_id):
        """Şablonu ekleyen ya da büro yöneticisi siler."""
        def fn(cur):
            t = self._own_template(cur, user, template_id, "silebilir")
            self._q(cur, "DELETE FROM zs_templates WHERE id = ?", (template_id,))
            return t
        return self._run(fn)

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
