"""
/zs İş Takip — bildirimler (zs_bildirim.py)
------------------------------------------
Kanallar: Web Push (telefon/bilgisayar; zs_push.py) ve Telegram botu. Bir kişi ikisini birden açabilir;
her bildirim kişinin açık tüm kanallarına gider.

Nasıl tetiklenir: değiştiren her API isteğinden sonra zs_events'e eklenen YENİ olaylar okunur ve
kurallara göre alıcı + metin üretilir (tek yerde; uçlara tek tek bildirim kodu serpiştirilmez).
Gönderim arka planda yapılır; isteği yavaşlatmaz, başarısız olursa iş akışı etkilenmez.

Kurallar (işlemi yapan kişiye bildirim gitmez; özel işler bildirim üretmez):
  atandı / alt işe atandı      → atanan
  ek atanan / onay yetkilisi   → eklenen kişi
  onaya gönderildi             → işin onay yetkilileri (yoksa yöneticiler)
  onaylandı / reddedildi / yeniden açıldı → atananlar
  ek süre istendi              → onay yetkilileri (yoksa yöneticiler)
  ek süre kabul / ret          → isteyen
  devredildi                   → devralan
  hatırlatıcı (zamanı gelince)  → hatırlatıcının alıcısı (kendine kurduysa da gider)

Hatırlatıcılar: REMIND_EVERY saniyede bir zamanı gelenler alınır (zs_reminders.sent_at koşullu
işaretlenir → her biri bir kez gider). Sunucu kapalıyken zamanı geçenler açılışta gönderilir.

Telegram ayarları (ortam değişkenleri):
  ZS_TELEGRAM_TOKEN   BotFather'dan alınan bot jetonu (yoksa Telegram kapalı)
  ZS_PUBLIC_URL       ör. https://cokluuyap.com — Telegram mesajındaki bağlantı + webhook adresi
  ZS_TELEGRAM_GROUPS  grupta /is ile iş açmaya izin verilen grup kimlikleri (zs_tg_grup.py)
  Webhook: ZS_PUBLIC_URL https ise Telegram güncellemeleri /zs/telegram/hook'a gelir (gizli başlıkla
  doğrulanır). Değilse (yerel geliştirme) sunucu Telegram'ı kendisi yoklar (long polling).
"""

import os
import html
import time
import asyncio
import hashlib
import datetime as _dt

import aiohttp

import zs_push

TR = _dt.timezone(_dt.timedelta(hours=3))
TG_API = "https://api.telegram.org"
REMIND_EVERY = 30


def _fmt(ts):
    return _dt.datetime.fromtimestamp(ts, TR).strftime("%d.%m.%Y %H:%M") if ts else "tarihsiz"


def build_messages(events, admins, names):
    """Olaylardan [(alıcı, {title, body, task_id, tag})] üretir. Saf fonksiyon (test edilebilir).
    Aynı istekte aynı kişiye aynı iş için birden çok olay varsa yalnız ilki gider."""
    out, seen = [], set()

    def add(uid, e, title, body):
        if not uid or uid == e["actor_id"] or uid not in names or (uid, e["task_id"]) in seen:
            return
        seen.add((uid, e["task_id"]))
        out.append((uid, {"title": title, "body": body, "task_id": e["task_id"], "tag": f"is-{e['task_id']}"}))

    for e in events:
        if e["is_private"]:
            continue
        d, k, t = e["detail"], e["kind"], e["title"]
        who = names.get(e["actor_id"], "Biri")
        deciders = e["approvers"] or admins
        if k == "assigned":
            sub = d.get("title") and d.get("title") != t
            add(d.get("assignee_id"), e, "Size yeni bir iş atandı",
                (f"{d['title']} (alt iş) — {t}" if sub else t) + f"\nAtayan: {who}" +
                (f" · Son tarih: {_fmt(e['due_at'])}" if e["due_at"] and not sub else ""))
        elif k == "member_added":
            role = "onay yetkilisi" if d.get("role") == "approver" else "ek atanan"
            add(d.get("user_id"), e, f"Bir işe {role} olarak eklendiniz", f"{t}\nEkleyen: {who}")
        elif k == "submitted":
            for uid in deciders:
                add(uid, e, "Onayınızı bekleyen bir iş var", f"{t}\n{who} onaya gönderdi")
        elif k in ("approved", "rejected", "reopened"):
            title = {"approved": "İşiniz onaylandı", "rejected": "İşiniz reddedildi",
                     "reopened": "İş yeniden açıldı"}[k]
            body = f"{t}\n{who}" + (f" · Gerekçe: {d['reason']}" if d.get("reason") else "")
            for uid in e["assignees"]:
                add(uid, e, title, body)
        elif k == "ext_requested":
            for uid in deciders:
                add(uid, e, "Ek süre talebi", f"{t}\n{who}: {_fmt(d.get('old_due'))} → {_fmt(d.get('new_due'))}")
        elif k in ("ext_approved", "ext_rejected"):
            ok = k == "ext_approved"
            add(d.get("requester_id"), e, "Ek süre talebiniz kabul edildi" if ok else "Ek süre talebiniz reddedildi",
                f"{t}\n" + (f"Yeni son tarih: {_fmt(d.get('new_due'))}" if ok else f"Karar: {who}") +
                (f" · {d['note']}" if d.get("note") else ""))
        elif k == "transferred":
            add(d.get("to_id"), e, "Size bir iş devredildi", f"{t}\nDevreden: {who}")
    return out


def reminder_message(r, now=None):
    """Hatırlatıcı satırından bildirim metni. Saf fonksiyon."""
    now = now or time.time()
    late = now - r["remind_at"] > 10 * 60
    # İşe bağlıysa işin başlığı, bağımsızsa hatırlatıcının kendi başlığı.
    head = r["title"] if not r.get("task_id") else \
        (f"{r['task_title']} › {r['sub_title']}" if r.get("sub_title") else r["task_title"])
    lines = [head]
    if r.get("note"):
        lines.append(r["note"])
    if r.get("due_at"):
        lines.append(f"Son tarih: {_fmt(r['due_at'])}")
    if r["created_by"] != r["user_id"] and r.get("by_name"):
        lines.append(f"Hatırlatan: {r['by_name']}")
    if late:
        lines.append(f"(Gecikmeli — kurulan saat: {_fmt(r['remind_at'])})")
    body = "\n".join(lines)
    return {"title": "Hatırlatma", "body": body, "task_id": r["task_id"], "tag": f"hat-{r['id']}"}


class Notifier:
    def __init__(self, store):
        self.store = store
        self.vapid_key, self.vapid_pub, self.vapid_src = zs_push.load_vapid()
        self.vapid_sub = (os.environ.get("ZS_VAPID_SUB") or "https://cokluuyap.com").strip()
        self.public_url = (os.environ.get("ZS_PUBLIC_URL") or "").strip().rstrip("/")
        self.tg_token = (os.environ.get("ZS_TELEGRAM_TOKEN") or "").strip()
        self.tg_secret = hashlib.sha256(("zs-tg|" + self.tg_token).encode()).hexdigest()[:48] if self.tg_token else ""
        self.tg_bot = None          # bot kullanıcı adı (getMe)
        self.tg_mode = "kapalı"
        self.last_id = store.max_event_id()
        self.lock = asyncio.Lock()
        self.session = None
        self.tasks = set()
        self.test_sink = None       # testlerde: gönderilecek yerine buraya eklenir
        self.group_handler = None   # grup mesajları (zs.py bağlar)

    # ── yaşam döngüsü ──
    def describe(self):
        push = f"Web Push açık ({self.vapid_src})" if self.vapid_key else f"Web Push KAPALI ({self.vapid_src})"
        return f"{push}; Telegram: {self.tg_mode}"

    async def _sess(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def start(self, _app=None):
        self._spawn(self._remind_loop())
        if not self.tg_token:
            return
        try:
            me = await self._tg("getMe")
            self.tg_bot = me.get("username")
        except Exception as e:
            self.tg_mode = f"HATA (getMe: {e})"
            print(f"[!] zs Telegram: {self.tg_mode}")
            return
        if self.public_url.startswith("https://"):
            try:
                await self._tg("setWebhook", url=f"{self.public_url}/zs/telegram/hook", secret_token=self.tg_secret,
                               allowed_updates=["message"], drop_pending_updates=False)
                self.tg_mode = f"@{self.tg_bot}, webhook"
            except Exception as e:
                self.tg_mode = f"@{self.tg_bot}, webhook HATASI: {e}"
        else:
            try:
                await self._tg("deleteWebhook")
            except Exception:
                pass
            self._spawn(self._poll())
            self.tg_mode = f"@{self.tg_bot}, yoklama (yerel)"
        print(f"[*] İş Takip bildirim: {self.describe()}")

    async def stop(self, _app=None):
        for t in list(self.tasks):
            t.cancel()
        if self.session and not self.session.closed:
            await self.session.close()

    def _spawn(self, coro):
        t = asyncio.ensure_future(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)
        return t

    async def drain(self):
        """Bekleyen gönderimleri bekler (testler için)."""
        while [t for t in self.tasks if not t.done() and getattr(t, "_zs_kick", False)]:
            await asyncio.gather(*[t for t in self.tasks if getattr(t, "_zs_kick", False)], return_exceptions=True)

    # ── olaylardan bildirim ──
    def kick(self):
        """Değiştiren her başarılı istekten sonra çağrılır."""
        t = self._spawn(self.process())
        t._zs_kick = True

    async def process(self):
        async with self.lock:
            try:
                events, admins, names = await asyncio.to_thread(self.store.events_for_notify, self.last_id)
            except Exception as e:
                print(f"[!] zs bildirim olayları okunamadı: {e}")
                return
            if not events:
                return
            self.last_id = max(e["id"] for e in events)
            msgs = build_messages(events, admins, names)
        for uid, m in msgs:
            await self.deliver(uid, m)

    async def deliver(self, uid, m):
        if self.test_sink is not None:
            self.test_sink.append((uid, m))
            return
        url = f"/zs#is-{m['task_id']}" if m.get("task_id") else "/zs"
        jobs = []
        if self.vapid_key:
            subs = await asyncio.to_thread(self.store.push_list, [uid])
            payload = {"title": m["title"], "body": m["body"], "url": url, "tag": m.get("tag", "zs")}
            jobs += [self._push_one(s, payload) for s in subs]
        if self.tg_token:
            links = await asyncio.to_thread(self.store.tg_get, [uid])
            text = f"<b>{html.escape(m['title'])}</b>\n{html.escape(m['body'])}"
            if self.public_url:
                text += f"\n<a href=\"{html.escape(self.public_url + url)}\">İşi aç</a>"
            jobs += [self._tg_send(link["chat_id"], text) for link in links]
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def _push_one(self, sub, payload):
        try:
            await zs_push.send(await self._sess(), sub, payload, self.vapid_key, self.vapid_pub, self.vapid_sub)
            await asyncio.to_thread(self.store.push_result, sub["id"], True)
        except zs_push.PushGone:
            await asyncio.to_thread(self.store.push_remove, sub["endpoint"])
        except Exception as e:
            print(f"[!] zs push gönderilemedi (abonelik {sub['id']}): {e}")
            await asyncio.to_thread(self.store.push_result, sub["id"], False)
            if (sub.get("fails") or 0) >= 20:
                await asyncio.to_thread(self.store.push_remove, sub["endpoint"])

    # ── hatırlatıcılar ──
    async def remind_tick(self, now=None):
        """Zamanı gelen hatırlatıcıları gönderir; gönderilen sayısını döndürür."""
        rows = await asyncio.to_thread(self.store.claim_due_reminders, now)
        n = 0
        for r in rows:
            if not r["user_active"]:
                continue
            await self.deliver(r["user_id"], reminder_message(r, now))
            n += 1
        return n

    async def _remind_loop(self):
        while True:
            try:
                await self.remind_tick()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[!] zs hatırlatıcı turu: {e}")
            await asyncio.sleep(REMIND_EVERY)

    # ── Telegram ──
    async def _tg(self, method, **params):
        s = await self._sess()
        async with s.post(f"{TG_API}/bot{self.tg_token}/{method}", json=params,
                          timeout=aiohttp.ClientTimeout(total=70)) as r:
            d = await r.json(content_type=None)
        if not d.get("ok"):
            raise RuntimeError(d.get("description") or f"HTTP {r.status}")
        return d.get("result")

    async def _tg_send(self, chat_id, text):
        try:
            await self._tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception as e:
            msg = str(e)
            print(f"[!] zs Telegram gönderilemedi: {msg}")
            if "blocked" in msg or "chat not found" in msg or "deactivated" in msg:
                await asyncio.to_thread(self.store.tg_unlink, None, chat_id)

    def tg_link_url(self, code):
        return f"https://t.me/{self.tg_bot}?start={code}" if self.tg_bot else None

    async def tg_reply(self, chat_id, text, reply_to=None):
        """Gruba yanıt yazar; gönderilen mesajın kimliği (ya da hata olursa None)."""
        params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            r = await self._tg("sendMessage", **params)
            return (r or {}).get("message_id")
        except Exception as e:
            print(f"[!] zs Telegram grup yanıtı gönderilemedi: {e}")
            return None

    async def tg_download(self, file_id, limit):
        """Telegram'daki dosyayı indirir (bayt). limit aşılırsa ValueError."""
        info = await self._tg("getFile", file_id=file_id)
        if (info.get("file_size") or 0) > limit:
            raise ValueError("büyük")
        s = await self._sess()
        async with s.get(f"{TG_API}/file/bot{self.tg_token}/{info['file_path']}",
                         timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            buf = bytearray()
            async for chunk in r.content.iter_chunked(256 * 1024):
                buf.extend(chunk)
                if len(buf) > limit:
                    raise ValueError("büyük")
        return bytes(buf)

    async def tg_update(self, upd):
        """Gelen mesaj. Özel sohbet: /start <kod> → bağla; /stop → bağlantıyı kaldır.
        Grup: group_handler'a (zs.py) devredilir."""
        msg = (upd or {}).get("message") or {}
        chat = msg.get("chat") or {}
        text = (msg.get("text") or "").strip()
        if chat.get("type") in ("group", "supergroup") and chat.get("id"):
            if self.group_handler:
                await self.group_handler(msg)
            return
        if chat.get("type") != "private" or not chat.get("id"):
            return
        cid = chat["id"]
        if text.startswith("/start"):
            code = text[6:].strip()
            uid = await asyncio.to_thread(self.store.tg_link, code, cid, chat.get("username") or chat.get("first_name") or "",
                                          chat.get("username")) if code else None
            if uid:
                u = await asyncio.to_thread(self.store.get_user, uid)
                await self._tg_send(cid, f"Bağlandı: <b>{html.escape(u['display_name'])}</b>\n"
                                         "İş Takip bildirimleri buraya gelecek. Durdurmak için /stop yazın.")
            elif (linked := await asyncio.to_thread(self.store.tg_user_by_chat, cid)):
                # Kodsuz ya da eski kodla /start (Telegram'ın "Başlat"ı, eski bağlantı): bağlantı zaten duruyor.
                await asyncio.to_thread(self.store.tg_touch, cid, chat.get("username"))
                await self._tg_send(cid, f"Zaten bağlısınız: <b>{html.escape(linked['display_name'])}</b>\n"
                                         "Yeniden bağlamanız gerekmez. Durdurmak için /stop yazın.")
            else:
                await self._tg_send(cid, "Bağlantı kodu geçersiz ya da süresi dolmuş.\n"
                                         "İş Takip'te Bildirimler → “Telegram'ı bağla” düğmesiyle yeniden deneyin.")
        elif text.startswith("/stop"):
            await asyncio.to_thread(self.store.tg_unlink, None, cid)
            await self._tg_send(cid, "Bildirimler durduruldu. Yeniden açmak için İş Takip'ten bağlayın.")
        else:
            await asyncio.to_thread(self.store.tg_touch, cid, chat.get("username"))
            await self._tg_send(cid, "Bu bot İş Takip bildirimlerini gönderir. Grupta iş açmak için "
                                     "<code>/is @kişi açıklama</code> yazın (belgeye yanıt verirseniz belge de eklenir).")

    async def _poll(self):
        offset = 0
        while True:
            try:
                ups = await self._tg("getUpdates", offset=offset, timeout=50, allowed_updates=["message"])
                for u in ups or []:
                    offset = max(offset, u["update_id"] + 1)
                    await self.tg_update(u)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[!] zs Telegram yoklama: {e}")
                await asyncio.sleep(10)

    async def test(self, uid):
        """Kişinin kendi kanallarına deneme bildirimi; (push cihaz sayısı, telegram bağlı mı)."""
        subs = await asyncio.to_thread(self.store.push_list, [uid])
        links = await asyncio.to_thread(self.store.tg_get, [uid])
        await self.deliver(uid, {"title": "Deneme bildirimi", "body": f"İş Takip bildirimleri bu cihaza geliyor.\n{_fmt(time.time())}",
                                 "task_id": None, "tag": "zs-deneme"})
        return len(subs), bool(links)
