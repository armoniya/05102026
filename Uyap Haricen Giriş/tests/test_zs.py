"""/zs İş Takip testleri — geçici SQLite + yerel klasör deposu ile, ağ/DB gerektirmez.

Çalıştırma:  python -m unittest discover -s tests -v   (Uyap Haricen Giriş klasöründen)
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp import web, FormData
from aiohttp.test_utils import TestClient, TestServer

import zs
import zs_r2
import zs_push
import accounts

# Testler veri klasörüne bildirim anahtarı dosyası yazmasın.
os.environ.setdefault("ZS_VAPID_PRIVATE", zs_push.b64u(zs_push.new_private_raw()))
from zs_store import ZsStore

PW = "Deneme.Parola1"
H = {"X-ZS": "1"}
PDF = b"%PDF-1.4\n" + b"x" * 2000


class _Guard:
    def __init__(self):
        self.fails = {}

    def blocked_for(self, key):
        return 60 if self.fails.get(key, 0) >= 3 else 0

    def record_fail(self, key):
        self.fails[key] = self.fails.get(key, 0) + 1

    def record_ok(self, key):
        self.fails.pop(key, None)


class ZsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        store = ZsStore(dsn="", sqlite_path=os.path.join(self.tmp, "zs.sqlite3"))
        store.create_user("yonetici", "Yönetici", PW, "admin")
        self.ali = store.create_user("ali", "Ali", PW, "user")
        self.ayse = store.create_user("ayse", "Ayşe", PW, "user")
        app = web.Application()
        zs.register(app, session_secret="test-secret", guard=_Guard(), client_ip=lambda r: "1.2.3.4", store=store)
        zs.C.storage = zs_r2.LocalStorage(os.path.join(self.tmp, "files"))
        zs.C.file_key = os.urandom(32)
        self.store = store
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            await c.close()
        self.store._conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def login(self, username):
        app = web.Application()
        # Her istemci kendi uygulamasını alır ama aynı C (depo/anahtar) durumunu paylaşır.
        zs_app_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        self.clients.append(c)
        r = await c.post("/zs/login", data={"username": username, "password": PW}, allow_redirects=False)
        self.assertEqual(r.status, 303)
        return c

    async def j(self, resp, status=200):
        data = await resp.json()
        self.assertEqual(resp.status, status, data)
        return data

    # ── testler ──────────────────────────────────────────────────────────────────
    async def test_giris_ve_oturum(self):
        app = web.Application()
        zs_app_routes(app)
        c = TestClient(TestServer(app)); await c.start_server(); self.clients.append(c)
        r = await c.get("/zs/api/me")
        self.assertEqual(r.status, 401)
        r = await c.post("/zs/login", data={"username": "ali", "password": "yanlis"}, allow_redirects=False)
        self.assertEqual(r.status, 401)
        c2 = await self.login("ali")
        me = await self.j(await c2.get("/zs/api/me"))
        self.assertEqual(me["me"]["username"], "ali")
        page = await c2.get("/zs")
        self.assertIn("zs.js", await page.text())

    async def test_x_zs_basligi_zorunlu(self):
        c = await self.login("yonetici")
        r = await c.post("/zs/api/tasks", json={"title": "x"})
        self.assertEqual(r.status, 403)

    async def test_kullanici_yalniz_kendi_isini_gorur(self):
        adm = await self.login("yonetici")
        t1 = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Ali'nin işi", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        t2 = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Ayşe'nin işi", "assignee_id": self.ayse["id"]}, headers=H)))["task"]
        ali = await self.login("ali")
        lst = await self.j(await ali.get("/zs/api/tasks?filtre=tumu"))
        self.assertEqual([t["id"] for t in lst["tasks"]], [t1["id"]])
        await self.j(await ali.get(f"/zs/api/tasks/{t2['id']}"), 400)
        # Ali kendine iş açabilir ama başkasına atayamaz; başkasının işine not ekleyemez.
        kendi = (await self.j(await ali.post("/zs/api/tasks", json={"title": "x", "assignee_id": self.ayse["id"]}, headers=H)))["task"]
        self.assertEqual(kendi["assignee_id"], self.ali["id"])
        await self.j(await ali.patch(f"/zs/api/tasks/{t1['id']}", json={"title": "y"}, headers=H), 400)
        await self.j(await ali.post(f"/zs/api/tasks/{t2['id']}/notes", json={"body": "x"}, headers=H), 400)
        await self.j(await ali.delete(f"/zs/api/tasks/{kendi['id']}", headers=H))
        # Ayşe'ye Ali'nin işinde alt iş atanınca Ayşe o işi görür.
        await self.j(await adm.post("/zs/api/tasks", json={"title": "alt", "parent_id": t1["id"], "assignee_id": self.ayse["id"]}, headers=H))
        ayse = await self.login("ayse")
        ids = {t["id"] for t in (await self.j(await ayse.get("/zs/api/tasks")))["tasks"]}
        self.assertEqual(ids, {t1["id"], t2["id"]})

    async def test_onay_akisi(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Dilekçe", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        ali = await self.login("ali")
        sub = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Taslak", "parent_id": t["id"]}, headers=H)))["task"]
        # Alt iş açıkken gönderilemez.
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H), 400)
        await self.j(await ali.patch(f"/zs/api/tasks/{sub['id']}", json={"done": True}, headers=H))
        # Yönetici atanan değil → gönderemez; Ali onaylayamaz.
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H), 400)
        r = await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
        self.assertEqual(r["task"]["status"], "onayda")
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "onayla"}, headers=H), 400)
        # Onaydayken kullanıcı alt işi değiştiremez.
        await self.j(await ali.patch(f"/zs/api/tasks/{sub['id']}", json={"done": False}, headers=H), 400)
        # Ret gerekçesiz olmaz; gerekçeyle devam'a döner ve not düşer.
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "reddet"}, headers=H), 400)
        r = await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "reddet", "reason": "Ek eksik"}, headers=H))
        self.assertEqual(r["task"]["status"], "devam")
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
        r = await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "onayla"}, headers=H))
        self.assertEqual(r["task"]["status"], "onaylandi")
        d = (await self.j(await ali.get(f"/zs/api/tasks/{t['id']}")))["task"]
        self.assertTrue(any("Ek eksik" in n["body"] for n in d["notes"]))
        kinds = [e["kind"] for e in d["events"]]
        for k in ("submitted", "rejected", "approved"):
            self.assertIn(k, kinds)

    async def test_not_tarih_saat(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Duruşma"}, headers=H)))["task"]
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/notes", json={"body": "Duruşma saati", "note_at": 1760000000}, headers=H))
        d = (await self.j(await adm.get(f"/zs/api/tasks/{t['id']}")))["task"]
        self.assertEqual(d["notes"][0]["note_at"], 1760000000)
        self.assertTrue(d["notes"][0]["created_at"] > 0)

    async def test_belge_sifreli_gidis_donus(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Belge", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        ali = await self.login("ali")
        fd = FormData(); fd.add_field("file", PDF, filename="vekalet ğüş.pdf", content_type="application/pdf")
        f = (await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/files", data=fd, headers=H)))["file"]
        # Depodaki bayt düz metin İÇERMEZ.
        key = self.store.get_file(f["id"])["storage_key"]
        with open(os.path.join(self.tmp, "files", key), "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"ZS1"))
        self.assertNotIn(b"%PDF", raw)
        r = await ali.get(f"/zs/api/files/{f['id']}")
        self.assertEqual(r.status, 200)
        self.assertEqual(await r.read(), PDF)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        # Başka kullanıcı indiremez.
        ayse = await self.login("ayse")
        self.assertEqual((await ayse.get(f"/zs/api/files/{f['id']}")).status, 404)

    async def test_belge_tur_ve_boyut_reddi(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Belge"}, headers=H)))["task"]
        fd = FormData(); fd.add_field("file", b"MZ\x90\x00", filename="virus.exe")
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/files", data=fd, headers=H), 400)
        fd = FormData(); fd.add_field("file", b"<html>", filename="sahte.pdf")
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/files", data=fd, headers=H), 400)
        old = zs.MAX_FILE
        zs.MAX_FILE = 1000
        try:
            fd = FormData(); fd.add_field("file", PDF, filename="buyuk.pdf")
            await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/files", data=fd, headers=H), 400)
        finally:
            zs.MAX_FILE = old

    async def test_is_silme_belgeyi_depodan_siler(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Silinecek"}, headers=H)))["task"]
        fd = FormData(); fd.add_field("file", PDF, filename="a.pdf")
        f = (await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/files", data=fd, headers=H)))["file"]
        key = self.store.get_file(f["id"])["storage_key"]
        await self.j(await adm.delete(f"/zs/api/tasks/{t['id']}", headers=H))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "files", key)))

    async def test_parola_degisince_oturum_duser(self):
        ali = await self.login("ali")
        adm = await self.login("yonetici")
        await self.j(await adm.post(f"/zs/api/users/{self.ali['id']}/sifre", json={}, headers=H))
        self.assertEqual((await ali.get("/zs/api/me")).status, 401)

    async def test_kullanici_yonetimi_yalniz_yonetici(self):
        ali = await self.login("ali")
        self.assertEqual((await ali.get("/zs/api/users")).status, 403)
        adm = await self.login("yonetici")
        r = await self.j(await adm.post("/zs/api/users", json={"username": "Veli", "display_name": "Veli"}, headers=H))
        self.assertEqual(r["user"]["username"], "veli")
        self.assertTrue(r["password"])
        await self.j(await adm.post("/zs/api/users", json={"username": "veli"}, headers=H), 400)

    async def test_ozel_is_yalniz_acanina_gorunur(self):
        self.store.create_user("yonetici2", "Yönetici 2", PW, "admin")
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Özel", "private": True,
                                                                   "assignee_id": self.ali["id"]}, headers=H)))["task"]
        self.assertEqual(t["is_private"], 1)
        self.assertEqual(t["assignee_id"], t["created_by"])  # özel iş açana atanır
        adm2 = await self.login("yonetici2")
        ids = [x["id"] for x in (await self.j(await adm2.get("/zs/api/tasks?filtre=tumu")))["tasks"]]
        self.assertNotIn(t["id"], ids)
        await self.j(await adm2.get(f"/zs/api/tasks/{t['id']}"), 400)
        # Özel iş düzenlemeyle başkasına verilemez; alt işi de başkasına atanamaz.
        await self.j(await adm.patch(f"/zs/api/tasks/{t['id']}", json={"assignee_id": self.ali["id"]}, headers=H), 400)
        await self.j(await adm.post("/zs/api/tasks", json={"title": "a", "parent_id": t["id"],
                                                           "assignee_id": self.ali["id"]}, headers=H), 400)

    async def test_devir_gorunurluk_sinirlari(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Devredilecek", "private": True}, headers=H)))["task"]
        tid = t["id"]
        eski_alt = (await self.j(await adm.post("/zs/api/tasks", json={"title": "eski alt", "parent_id": tid}, headers=H)))["task"]
        await self.j(await adm.post(f"/zs/api/tasks/{tid}/notes", json={"body": "gizli not"}, headers=H))
        await self.j(await adm.post(f"/zs/api/tasks/{tid}/notes", json={"body": "x"}, headers=H))
        fd = FormData(); fd.add_field("file", PDF, filename="eski.pdf")
        eski_belge = (await self.j(await adm.post(f"/zs/api/tasks/{tid}/files", data=fd, headers=H)))["file"]
        # Ali'ye devret: alt işler + notlar gizli, belgeler + geçmiş görünür.
        r = await self.j(await adm.post(f"/zs/api/tasks/{tid}/devret", json={
            "to_user_id": self.ali["id"], "show": {"subtasks": False, "notes": False, "files": True, "history": True}}, headers=H))
        self.assertEqual(r["task"]["assignee_id"], self.ali["id"])
        self.assertEqual(r["task"]["is_private"], 0)
        await self.j(await adm.post(f"/zs/api/tasks/{tid}/notes", json={"body": "devir sonrası"}, headers=H))
        ali = await self.login("ali")
        d = (await self.j(await ali.get(f"/zs/api/tasks/{tid}")))["task"]
        self.assertEqual(d["subtasks"], [])
        self.assertEqual([n["body"] for n in d["notes"]], ["devir sonrası"])
        self.assertEqual(len(d["files"]), 1)
        tr = [e for e in d["events"] if e["kind"] == "transferred"]
        self.assertEqual(len(tr), 1)
        self.assertNotIn("hidden", tr[0]["detail"])  # neyin gizlendiği devralana söylenmez
        lst = (await self.j(await ali.get("/zs/api/tasks")))["tasks"]
        self.assertEqual((lst[0]["note_count"], len(lst[0]["subtasks"])), (1, 0))
        # Gizli alt iş Ali'yi onaya göndermekten alıkoymaz; Ali onu değiştiremez.
        await self.j(await ali.patch(f"/zs/api/tasks/{eski_alt['id']}", json={"done": True}, headers=H), 400)
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "gonder"}, headers=H))
        # Devreden yönetici her şeyi görmeye devam eder.
        d = (await self.j(await adm.get(f"/zs/api/tasks/{tid}")))["task"]
        self.assertEqual(len(d["subtasks"]), 1)
        self.assertEqual(len(d["notes"]), 3)
        # Belgeler gizlenince indirme de kapanır.
        ayse_t = (await self.j(await adm.post(f"/zs/api/tasks/{tid}/devret", json={
            "to_user_id": self.ayse["id"], "show": {"files": False}}, headers=H)))["task"]
        self.assertEqual(ayse_t["assignee_id"], self.ayse["id"])
        ayse = await self.login("ayse")
        self.assertEqual((await ayse.get(f"/zs/api/files/{eski_belge['id']}")).status, 404)
        # Yalnız yönetici devredebilir.
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/devret", json={"to_user_id": self.ali["id"]}, headers=H), 403)

    async def test_kullanici_ozel_isi_kademeli_gorunur_yapar(self):
        ali = await self.login("ali")
        t = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Ali özel", "private": True}, headers=H)))["task"]
        tid = t["id"]
        await self.j(await ali.post("/zs/api/tasks", json={"title": "alt", "parent_id": tid}, headers=H))
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/notes", json={"body": "kişisel not"}, headers=H))
        await self.j(await ali.patch(f"/zs/api/tasks/{tid}", json={"title": "Ali özel iş"}, headers=H))
        adm = await self.login("yonetici")
        self.assertEqual((await self.j(await adm.get("/zs/api/tasks?filtre=tumu")))["tasks"], [])
        await self.j(await adm.get(f"/zs/api/tasks/{tid}"), 400)
        # Özel iş onaya gönderilemez.
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "gonder"}, headers=H), 400)
        # Görünür yap: notlar gizli, alt işler/geçmiş görünür. Atama değişmez.
        r = await self.j(await ali.post(f"/zs/api/tasks/{tid}/gorunur", json={"show": {"notes": False}}, headers=H))
        self.assertEqual((r["task"]["is_private"], r["task"]["assignee_id"]), (0, self.ali["id"]))
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/gorunur", json={}, headers=H), 400)  # zaten görünür
        d = (await self.j(await adm.get(f"/zs/api/tasks/{tid}")))["task"]
        self.assertEqual((len(d["subtasks"]), len(d["notes"])), (1, 0))
        pub = [e for e in d["events"] if e["kind"] == "published"][0]
        self.assertNotIn("hidden", pub["detail"])  # yönetici neyin gizlendiğini de görmez
        self.assertNotIn("note", [e["kind"] for e in d["events"]])  # gizli notun "not ekledi" satırı da yok
        self.assertIn("created", [e["kind"] for e in d["events"]])  # geçmişin geri kalanı görünür
        d = (await self.j(await ali.get(f"/zs/api/tasks/{tid}")))["task"]
        self.assertEqual(len(d["notes"]), 1)
        self.assertIn("hidden", [e for e in d["events"] if e["kind"] == "published"][0]["detail"])

    async def test_baskasinin_ozel_isi_gorunur_yapilamaz(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Ortak"}, headers=H)))["task"]
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/gorunur", json={}, headers=H), 400)  # özel değil

    async def test_oncelik_ve_yeni_isareti(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Acil iş", "priority": 2,
                                                                   "assignee_id": self.ali["id"]}, headers=H)))["task"]
        self.assertEqual(t["priority"], 2)
        await self.j(await adm.post("/zs/api/tasks", json={"title": "x", "priority": 7}, headers=H), 400)
        ali = await self.login("ali")
        lst = (await self.j(await ali.get("/zs/api/tasks")))["tasks"]
        self.assertTrue(lst[0]["unseen"])  # yönetici atadı, Ali henüz bakmadı
        await self.j(await ali.get(f"/zs/api/tasks/{t['id']}"))
        self.assertFalse((await self.j(await ali.get("/zs/api/tasks")))["tasks"][0]["unseen"])
        # Ali'nin kendi işlemi kendisine "yeni" sayılmaz; yöneticinin notu sayılır.
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/notes", json={"body": "ben"}, headers=H))
        self.assertFalse((await self.j(await ali.get("/zs/api/tasks")))["tasks"][0]["unseen"])
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/notes", json={"body": "yönetici"}, headers=H))
        self.assertTrue((await self.j(await ali.get("/zs/api/tasks")))["tasks"][0]["unseen"])
        # Önceliği yalnız yönetici (ya da kendi işinde kullanıcı) değiştirir.
        await self.j(await ali.patch(f"/zs/api/tasks/{t['id']}", json={"priority": 0}, headers=H), 400)
        await self.j(await adm.patch(f"/zs/api/tasks/{t['id']}", json={"priority": 0}, headers=H))

    async def test_coklu_atanan_ve_onay_yetkilisi(self):
        self.store.create_user("veli", "Veli", PW, "user")
        veli_id = [u["id"] for u in self.store.list_users() if u["username"] == "veli"][0]
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Ortak dilekçe", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        tid = t["id"]
        # Not zorunlu.
        await self.j(await adm.post(f"/zs/api/tasks/{tid}/kisiler", json={"user_id": self.ayse["id"], "role": "assignee"}, headers=H), 400)
        r = await self.j(await adm.post(f"/zs/api/tasks/{tid}/kisiler", json={"user_id": self.ayse["id"], "role": "assignee",
                                                                             "note": "Ekler için Ayşe de dahil"}, headers=H))
        self.assertEqual(r["assignees"], [self.ali["id"], self.ayse["id"]])
        await self.j(await adm.post(f"/zs/api/tasks/{tid}/kisiler", json={"user_id": veli_id, "role": "approver",
                                                                         "note": "Kontrolü Veli yapsın"}, headers=H))
        ayse = await self.login("ayse")
        lst = (await self.j(await ayse.get("/zs/api/tasks")))["tasks"]
        self.assertEqual([x["id"] for x in lst], [tid])
        # Ek atanan alt iş ekleyip onaya gönderebilir; onaylayamaz.
        await self.j(await ayse.post("/zs/api/tasks", json={"title": "ekler", "parent_id": tid}, headers=H))
        sub = (await self.j(await ayse.get(f"/zs/api/tasks/{tid}")))["task"]["subtasks"][0]
        await self.j(await ayse.patch(f"/zs/api/tasks/{sub['id']}", json={"done": True}, headers=H))
        await self.j(await ayse.post(f"/zs/api/tasks/{tid}/durum", json={"action": "gonder"}, headers=H))
        await self.j(await ayse.post(f"/zs/api/tasks/{tid}/durum", json={"action": "onayla"}, headers=H), 400)
        # Onay yetkilisi (normal kullanıcı) onaylar; "onay" listesinde görür.
        veli = await self.login("veli")
        self.assertEqual([x["id"] for x in (await self.j(await veli.get("/zs/api/tasks?filtre=onay")))["tasks"]], [tid])
        r = await self.j(await veli.post(f"/zs/api/tasks/{tid}/durum", json={"action": "onayla"}, headers=H))
        self.assertEqual(r["task"]["status"], "onaylandi")
        d = (await self.j(await adm.get(f"/zs/api/tasks/{tid}")))["task"]
        notes = [e["detail"].get("note") for e in d["events"] if e["kind"] == "member_added"]
        self.assertIn("Kontrolü Veli yapsın", notes)
        self.assertTrue(d["submitted_at"])
        # Özel işe kişi eklenemez.
        p = (await self.j(await adm.post("/zs/api/tasks", json={"title": "özel", "private": True}, headers=H)))["task"]
        await self.j(await adm.post(f"/zs/api/tasks/{p['id']}/kisiler", json={"user_id": veli_id, "role": "approver", "note": "x"}, headers=H), 400)

    async def test_ek_sure_talebi(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Süreli", "assignee_id": self.ali["id"],
                                                                   "due_at": 1800000000}, headers=H)))["task"]
        tid = t["id"]
        ali = await self.login("ali")
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/ek-sure", json={"new_due": 1800500000}, headers=H), 400)  # gerekçesiz
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/ek-sure", json={"new_due": 1700000000, "reason": "x"}, headers=H), 400)  # geri tarih
        x = (await self.j(await ali.post(f"/zs/api/tasks/{tid}/ek-sure", json={"new_due": 1800500000,
                                                                               "reason": "Bilirkişi raporu gecikti"}, headers=H)))["extension"]
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/ek-sure", json={"new_due": 1800600000, "reason": "y"}, headers=H), 400)  # ikinci bekleyen
        self.assertTrue((await self.j(await adm.get("/zs/api/tasks?filtre=tumu")))["tasks"][0]["ext_pending"])
        await self.j(await ali.post(f"/zs/api/ek-sure/{x['id']}/karar", json={"approve": True}, headers=H), 400)  # kendi talebini onaylayamaz
        await self.j(await adm.post(f"/zs/api/ek-sure/{x['id']}/karar", json={"approve": False}, headers=H), 400)  # ret gerekçesiz olmaz
        await self.j(await adm.post(f"/zs/api/ek-sure/{x['id']}/karar", json={"approve": True, "note": "Uygun"}, headers=H))
        d = (await self.j(await ali.get(f"/zs/api/tasks/{tid}")))["task"]
        self.assertEqual(d["due_at"], 1800500000)
        self.assertEqual(d["extensions"][0]["status"], "kabul")
        kinds = [e["kind"] for e in d["events"]]
        self.assertIn("ext_requested", kinds); self.assertIn("ext_approved", kinds)

    async def test_ozel_is_onaysiz_tamamlanir(self):
        ali = await self.login("ali")
        t = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Kişisel", "private": True}, headers=H)))["task"]
        tid = t["id"]
        sub = (await self.j(await ali.post("/zs/api/tasks", json={"title": "adım", "parent_id": tid}, headers=H)))["task"]
        # Özel iş onaya gönderilmez; alt iş açıkken tamamlanmaz.
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "gonder"}, headers=H), 400)
        await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "tamamla"}, headers=H), 400)
        await self.j(await ali.patch(f"/zs/api/tasks/{sub['id']}", json={"done": True}, headers=H))
        r = await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "tamamla"}, headers=H))
        self.assertEqual(r["task"]["status"], "tamamlandi")
        # Tamamlanan iş kilitli; sahibi yeniden açabilir.
        await self.j(await ali.patch(f"/zs/api/tasks/{tid}", json={"title": "y"}, headers=H), 400)
        r = await self.j(await ali.post(f"/zs/api/tasks/{tid}/durum", json={"action": "yeniden_ac"}, headers=H))
        self.assertEqual(r["task"]["status"], "devam")
        # Görünür olmuş (özel olmayan) iş "tamamla" ile kapatılamaz.
        adm = await self.login("yonetici")
        g = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Görünür", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{g['id']}/durum", json={"action": "tamamla"}, headers=H), 400)

    async def test_yonetici_kullanici_duzenler(self):
        adm = await self.login("yonetici")
        me = (await self.j(await adm.get("/zs/api/me")))["me"]
        u = (await self.j(await adm.post(f"/zs/api/users/{self.ali['id']}", json={"display_name": "Ali Veli", "role": "admin"}, headers=H)))["user"]
        self.assertEqual((u["display_name"], u["role"]), ("Ali Veli", "admin"))
        await self.j(await adm.post(f"/zs/api/users/{me['id']}", json={"role": "user"}, headers=H), 400)  # kendi rolü
        await self.j(await adm.post(f"/zs/api/users/{me['id']}", json={"display_name": " "}, headers=H), 400)
        ayse = await self.login("ayse")
        await self.j(await ayse.post(f"/zs/api/users/{self.ali['id']}", json={"display_name": "x"}, headers=H), 403)

    async def test_is_acilirken_kisi_eklenir(self):
        adm = await self.login("yonetici")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Ortak", "assignee_id": self.ali["id"],
                                                                   "assignees": [self.ali["id"], self.ayse["id"]],
                                                                   "approvers": [self.ayse["id"]]}, headers=H)))["task"]
        d = (await self.j(await adm.get(f"/zs/api/tasks/{t['id']}")))["task"]
        self.assertEqual(d["assignees"], [self.ali["id"], self.ayse["id"]])
        self.assertEqual(d["approvers"], [self.ayse["id"]])
        # Kullanıcı bu alanlarla kimseyi ekleyemez.
        ali = await self.login("ali")
        k = (await self.j(await ali.post("/zs/api/tasks", json={"title": "k", "assignees": [self.ayse["id"]]}, headers=H)))["task"]
        self.assertEqual((await self.j(await ali.get(f"/zs/api/tasks/{k['id']}")))["task"]["assignees"], [self.ali["id"]])

    async def test_rapor_kisi_ve_zaman(self):
        import time as _t
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        now = int(_t.time())
        # Süresinde biten (son tarih ileride) + geç biten (son tarih geçmiş) + açık gecikmiş + özel iş.
        a = (await self.j(await adm.post("/zs/api/tasks", json={"title": "A", "assignee_id": self.ali["id"], "due_at": now + 86400}, headers=H)))["task"]
        b = (await self.j(await adm.post("/zs/api/tasks", json={"title": "B", "assignee_id": self.ali["id"], "due_at": now - 3600}, headers=H)))["task"]
        await self.j(await adm.post("/zs/api/tasks", json={"title": "C", "assignee_id": self.ali["id"], "due_at": now - 60}, headers=H))
        await self.j(await ali.post("/zs/api/tasks", json={"title": "Özel", "private": True}, headers=H))
        for t in (a, b):
            await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
        await self.j(await adm.post(f"/zs/api/tasks/{b['id']}/durum", json={"action": "reddet", "reason": "eksik"}, headers=H))
        for t in (a, b):
            if t is b:
                await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
            await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "onayla"}, headers=H))
        import datetime as dt
        bugun = dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(hours=3))).date()
        q = f"bas={bugun - dt.timedelta(days=6)}&bit={bugun}&grup=gun"
        # Kullanıcı: yalnız kendi işleri (kendi özel işi dahil); kisi parametresi yok sayılır; kişi raporu boş.
        await self.j(await adm.post("/zs/api/tasks", json={"title": "Ayşe'nin", "assignee_id": self.ayse["id"]}, headers=H))
        ru = await self.j(await ali.get(f"/zs/api/rapor?{q}&kisi={self.ayse['id']}"))
        self.assertEqual(ru["kisiler"], [])
        self.assertEqual(sorted(t["title"] for t in ru["isler"]), ["A", "B", "C", "Özel"])
        self.assertEqual(sum(d["acilan"] for d in ru["donemler"]), 4)
        ayse = await self.login("ayse")
        self.assertNotIn("Özel", [t["title"] for t in (await self.j(await ayse.get(f"/zs/api/rapor?{q}")))["isler"]])
        r = await self.j(await adm.get(f"/zs/api/rapor?{q}"))
        k = {x["user_id"]: x for x in r["kisiler"]}[self.ali["id"]]
        self.assertEqual((k["yeni"], k["biten"], k["suresinde"], k["gec"], k["reddedilen"]), (3, 2, 1, 1, 1))
        self.assertEqual((k["acik"], k["geciken"]), (1, 1))  # özel iş sayılmaz
        y = {x["user_id"]: x for x in r["kisiler"]}[[u["id"] for u in self.store.list_users() if u["username"] == "yonetici"][0]]
        self.assertEqual((y["onayladi"], y["reddetti"]), (2, 1))
        self.assertEqual(len(r["donemler"]), 7)
        son = r["donemler"][-1]
        self.assertEqual((son["acilan"], son["biten"], son["acik_sonda"]), (4, 2, 2))  # Ali'nin özel işi yönetici raporunda yok
        self.assertNotIn("Özel", [t["title"] for t in r["isler"]])
        # Kişi süzgeci ve hatalı aralık.
        r2 = await self.j(await adm.get(f"/zs/api/rapor?{q}&kisi={self.ayse['id']}"))
        self.assertEqual(sum(d["acilan"] for d in r2["donemler"]), 1)
        self.assertEqual([t["title"] for t in r2["isler"]], ["Ayşe'nin"])
        await self.j(await adm.get("/zs/api/rapor?bas=2026-10-10&bit=2026-10-01"), 400)

    async def test_etkinlik_raporu(self):
        import time as _t
        import datetime as dt
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Dava", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/notes", json={"body": "Dilekçe yazıldı"}, headers=H))
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
        p = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Gizli", "private": True}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{p['id']}/notes", json={"body": "özel not"}, headers=H))
        bugun = dt.datetime.fromtimestamp(int(_t.time()), dt.timezone(dt.timedelta(hours=3))).date()
        q = f"bas={bugun - dt.timedelta(days=2)}&bit={bugun}"
        # Kullanıcı kendi etkinliğini görür (özel işi dahil); kisi parametresi yok sayılır.
        r = await self.j(await ali.get(f"/zs/api/rapor/etkinlik?{q}&kisi={self.ayse['id']}"))
        self.assertEqual(r["kisi"], self.ali["id"])
        self.assertEqual((r["ozet"]["not"], r["ozet"]["onaya"], r["ozet"]["acti"], r["ozet"]["is_sayisi"]), (2, 1, 1, 2))
        self.assertIn("Dilekçe yazıldı", [i["body"] for i in r["items"]])
        self.assertEqual(len(r["gunler"]), 3)
        self.assertEqual(r["gunler"][-1]["toplam"], r["ozet"]["toplam"])
        # Yönetici Ali'nin etkinliğini görür ama özel işini görmez.
        ra = await self.j(await adm.get(f"/zs/api/rapor/etkinlik?{q}&kisi={self.ali['id']}"))
        self.assertEqual([i["title"] for i in ra["items"]], ["Dava", "Dava"])
        self.assertNotIn("özel not", [i["body"] for i in ra["items"]])
        self.assertEqual(ra["isler"][0]["onaya"], 1)
        await self.j(await adm.get("/zs/api/rapor/etkinlik?bas=2024-01-01&bit=2026-01-01"), 400)

    async def test_denetim_kaydi(self):
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Silinecek", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/notes", json={"body": "önemli not"}, headers=H))
        await self.j(await ali.delete(f"/zs/api/tasks/{t['id']}", headers=H), 400)  # yetkisiz silme de kaydedilir
        await self.j(await adm.delete(f"/zs/api/tasks/{t['id']}", headers=H))
        await self.j(await ali.post("/zs/api/me/password", json={"old": "Deneme.Parola1", "new": "Yeni.Parola22"}, headers=H))
        await self.j(await adm.post("/zs/api/users", json={"username": "veli", "display_name": "Veli", "password": "Gizli.Parola33"}, headers=H))
        app = web.Application()
        zs_app_routes(app)
        anon = TestClient(TestServer(app))
        await anon.start_server()
        self.clients.append(anon)
        await anon.post("/zs/login", data={"username": "ali", "password": "yanlis"}, allow_redirects=False)
        rows = self.store.audit_rows(limit=0)
        by = {}
        for r in rows:
            by.setdefault(r["action"], []).append(r)
        sil = [r for r in by["is_sil"] if r["ok"]][0]
        before = json.loads(sil["before"])
        self.assertEqual(before["title"], "Silinecek")
        self.assertEqual([n["body"] for n in before["notes"]], ["önemli not"])  # silinen not kayıtta kalır
        self.assertEqual(sil["username"], "yonetici")
        self.assertTrue(any(not r["ok"] and r["status"] == 400 for r in by["is_sil"]))
        self.assertIn("not_ekle", by)
        self.assertTrue(json.loads(by["is_ac"][0]["result"])["task"]["id"])
        fail = [r for r in by["giris"] if not r["ok"]][0]
        self.assertEqual((fail["username"], fail["status"]), ("ali", 401))
        dump = json.dumps(rows, ensure_ascii=False)
        for secret in ("Deneme.Parola1", "Yeni.Parola22", "Gizli.Parola33", "yanlis"):
            self.assertNotIn(secret, dump)  # parolalar hiçbir alana yazılmaz
        # Arayüze/API'ye açık değil.
        self.assertEqual((await adm.get("/zs/api/denetim")).status, 404)
        with open(os.path.join(os.path.dirname(zs.__file__), "webapp", "zs", "zs.js"), encoding="utf-8") as f:
            self.assertNotIn("audit", f.read())

    async def test_bildirim_kurallari(self):
        n = zs.C.notifier
        n.test_sink = []
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        ayse = await self.login("ayse")
        uid_adm = [u["id"] for u in self.store.list_users() if u["username"] == "yonetici"][0]

        async def got():
            await n.drain()
            out, n.test_sink[:] = list(n.test_sink), []
            self._raw = out
            return [(u, m["title"], m["body"].split("\n")[0]) for u, m in out]

        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Dava dilekçesi", "assignee_id": self.ali["id"],
                                                                "approvers": [self.ayse["id"]]}, headers=H)))["task"]
        self.assertEqual(sorted(await got()), sorted([(self.ali["id"], "Size yeni bir iş atandı", "Dava dilekçesi"),
                                                      (self.ayse["id"], "Bir işe onay yetkilisi olarak eklendiniz", "Dava dilekçesi")]))
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "gonder"}, headers=H))
        self.assertEqual(await got(), [(self.ayse["id"], "Onayınızı bekleyen bir iş var", "Dava dilekçesi")])  # işe özel yetkili
        await self.j(await ayse.post(f"/zs/api/tasks/{t['id']}/durum", json={"action": "reddet", "reason": "Eksik ek"}, headers=H))
        r = await got()
        self.assertEqual([x[:2] for x in r], [(self.ali["id"], "İşiniz reddedildi")])
        self.assertIn("Gerekçe: Eksik ek", self._raw[0][1]["body"])
        # Yetkili yoksa onay bildirimi yöneticilere; kişi kendi yaptığı işlem için bildirim almaz.
        t2 = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Haciz", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        await got()
        await self.j(await ali.post(f"/zs/api/tasks/{t2['id']}/durum", json={"action": "gonder"}, headers=H))
        self.assertEqual(await got(), [(uid_adm, "Onayınızı bekleyen bir iş var", "Haciz")])
        # Özel iş bildirim üretmez.
        p = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Gizli", "private": True}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{p['id']}/durum", json={"action": "tamamla"}, headers=H))
        self.assertEqual(await got(), [])
        # Reddedilen istek bildirim üretmez.
        await self.j(await ayse.post(f"/zs/api/tasks/{t2['id']}/durum", json={"action": "onayla"}, headers=H), 400)
        self.assertEqual(await got(), [])

    async def test_bildirim_kanallari(self):
        import zs_push
        ali = await self.login("ali")
        st = await self.j(await ali.get("/zs/api/bildirim"))
        self.assertTrue(st["push"]["enabled"])
        self.assertEqual(len(zs_push.unb64u(st["push"]["key"])), 65)
        self.assertFalse(st["telegram"]["enabled"])  # jeton yok
        ua = zs_push.private_from_raw(zs_push.new_private_raw())
        sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": zs_push.b64u(zs_push._pub_bytes(ua)), "auth": "BTBZMqHH6r4Tts7J_aSIgg"}}
        await self.j(await ali.post("/zs/api/bildirim/push", json={"endpoint": "http://x", "keys": {}}, headers=H), 400)
        self.assertEqual((await self.j(await ali.post("/zs/api/bildirim/push", json=sub, headers=H)))["devices"], 1)
        self.assertEqual((await self.j(await ali.post("/zs/api/bildirim/push", json=sub, headers=H)))["devices"], 1)  # tekrar: çoğalmaz
        self.assertEqual((await self.j(await ali.post("/zs/api/bildirim/push/sil", json={"endpoint": sub["endpoint"]}, headers=H)))["devices"], 0)
        # Telegram bağlama kodu: tek kullanımlık, süreli.
        code = self.store.tg_code_new(self.ali["id"])
        self.assertEqual(self.store.tg_link(code, 555, "ali_tg"), self.ali["id"])
        self.assertIsNone(self.store.tg_link(code, 555))
        self.assertEqual(self.store.tg_get([self.ali["id"]])[0]["chat_id"], 555)
        expired = self.store.tg_code_new(self.ayse["id"], ttl=-1)
        self.assertIsNone(self.store.tg_link(expired, 777))
        # Webhook gizli başlık olmadan kapalı.
        self.assertEqual((await ali.post("/zs/telegram/hook", json={})).status, 404)
        # Uygulama dosyaları.
        for path in ("/zs/manifest.webmanifest", "/zs/sw.js", "/zs/static/icon-192.png", "/zs/static/apple-touch-icon.png"):
            self.assertEqual((await ali.get(path)).status, 200, path)
        self.assertEqual((await ali.get("/zs/sw.js")).headers.get("Service-Worker-Allowed"), "/zs")

    async def test_hatirlatici(self):
        import time as _t
        n = zs.C.notifier
        n.test_sink = []
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Bilirkişi raporu", "assignee_id": self.ali["id"],
                                                                "due_at": int(_t.time()) + 3 * 86400}, headers=H)))["task"]
        soon = int(_t.time()) + 120
        # Ali kendine kurar; başkasına kuramaz; geçmişe kuramaz.
        r1 = (await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": soon, "note": "Müvekkili ara"}, headers=H)))["reminder"]
        self.assertEqual(r1["user_id"], self.ali["id"])
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": soon, "user_id": self.ayse["id"]}, headers=H), 400)
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": int(_t.time()) - 3600}, headers=H), 400)
        # Yönetici, işi gören Ali'ye kurabilir; işi görmeyen Ayşe'ye kuramaz.
        r2 = (await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": soon + 60, "user_id": self.ali["id"]}, headers=H)))["reminder"]
        await self.j(await adm.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": soon, "user_id": self.ayse["id"]}, headers=H), 400)
        # Ayşe işi göremez → hatırlatıcı listesi/kurma yok, başkasınınkini silemez.
        ayse = await self.login("ayse")
        await self.j(await ayse.get(f"/zs/api/tasks/{t['id']}/hatirlatici"), 400)
        await self.j(await ayse.delete(f"/zs/api/hatirlatici/{r1['id']}", headers=H), 400)
        # Listede en yakın bekleyen hatırlatma görünür.
        lst = (await self.j(await ali.get("/zs/api/tasks")))["tasks"]
        self.assertEqual(lst[0]["reminder_at"], soon)
        self.assertEqual(len((await self.j(await ali.get(f"/zs/api/tasks/{t['id']}/hatirlatici")))["reminders"]), 2)
        # Zamanı gelmeden gitmez; gelince bir kez gider (ikinci tur boş).
        await n.drain(); n.test_sink[:] = []
        self.assertEqual(await n.remind_tick(soon - 10), 0)
        self.assertEqual(await n.remind_tick(soon + 61), 2)
        self.assertEqual(await n.remind_tick(soon + 120), 0)
        msgs = {m["body"].split("\n")[1]: (u, m) for u, m in n.test_sink}
        self.assertEqual(msgs["Müvekkili ara"][0], self.ali["id"])
        self.assertEqual(msgs["Müvekkili ara"][1]["title"], "Hatırlatma")
        self.assertIn("Hatırlatan: Yönetici", [m for _, (_, m) in msgs.items() if "Hatırlatan" in m["body"]][0]["body"])
        lst = (await self.j(await ali.get("/zs/api/tasks")))["tasks"]
        self.assertIsNone(lst[0]["reminder_at"])
        # Alıcı silebilir; iş silinince hatırlatıcılar da gider.
        await self.j(await ali.delete(f"/zs/api/hatirlatici/{r2['id']}", headers=H))
        await self.j(await ali.post(f"/zs/api/tasks/{t['id']}/hatirlatici", json={"remind_at": soon + 3600}, headers=H))
        await self.j(await adm.delete(f"/zs/api/tasks/{t['id']}", headers=H))
        self.assertEqual(await n.remind_tick(soon + 7200), 0)

    async def test_hatirlatici_ozel_kendi_ve_bagimsiz(self):
        import time as _t
        n = zs.C.notifier
        n.test_sink = []
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        ayse = await self.login("ayse")
        soon = int(_t.time()) + 120
        # Ali'nin kendi açtığı iş ve özel işi: ikisine de kendine hatırlatıcı kurabilir.
        kendi = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Kendi işim"}, headers=H)))["task"]
        ozel = (await self.j(await ali.post("/zs/api/tasks", json={"title": "Özel işim", "private": True}, headers=H)))["task"]
        await self.j(await ali.post(f"/zs/api/tasks/{kendi['id']}/hatirlatici", json={"remind_at": soon}, headers=H))
        await self.j(await ali.post(f"/zs/api/tasks/{ozel['id']}/hatirlatici", json={"remind_at": soon}, headers=H))
        # Yönetici Ali'nin özel işini göremez → hatırlatıcı da kuramaz/göremez.
        await self.j(await adm.get(f"/zs/api/tasks/{ozel['id']}/hatirlatici"), 400)
        # İşten bağımsız: başlık zorunlu; kullanıcı yalnız kendine; yönetici başkasına kurabilir.
        await self.j(await ali.post("/zs/api/hatirlatici", json={"remind_at": soon}, headers=H), 400)
        await self.j(await ali.post("/zs/api/hatirlatici", json={"title": "x", "remind_at": soon, "user_id": self.ayse["id"]}, headers=H), 400)
        b1 = (await self.j(await ali.post("/zs/api/hatirlatici", json={"title": "Bilirkişiyi ara", "remind_at": soon,
                                                                       "note": "Dosya 2024/11435"}, headers=H)))["reminder"]
        self.assertIsNone(b1["task_id"])
        b2 = (await self.j(await adm.post("/zs/api/hatirlatici", json={"title": "Toplantı notlarını gönder", "remind_at": soon + 30,
                                                                       "user_id": self.ayse["id"]}, headers=H)))["reminder"]
        # Kişisel: Ali kendininkini görür; Ayşe kendisine kurulanı; yönetici yalnız kurduğunu (Ali'ninkini değil).
        ids = lambda d: {r["id"] for r in d["reminders"]}
        self.assertEqual(ids(await self.j(await ali.get("/zs/api/hatirlatici"))), {b1["id"]})
        self.assertEqual(ids(await self.j(await ayse.get("/zs/api/hatirlatici"))), {b2["id"]})
        self.assertEqual(ids(await self.j(await adm.get("/zs/api/hatirlatici"))), {b2["id"]})
        await self.j(await adm.delete(f"/zs/api/hatirlatici/{b1['id']}", headers=H), 400)
        await self.j(await ayse.delete(f"/zs/api/hatirlatici/{b1['id']}", headers=H), 400)
        # Zamanı gelince hepsi (2 işe bağlı + 2 bağımsız) bir kez gider; bağımsızda başlık kendi başlığıdır.
        await n.drain(); n.test_sink[:] = []
        self.assertEqual(await n.remind_tick(soon + 60), 4)
        self.assertEqual(await n.remind_tick(soon + 120), 0)
        first = {(u, m["body"].split("\n")[0]) for u, m in n.test_sink}
        self.assertEqual(first, {(self.ali["id"], "Kendi işim"), (self.ali["id"], "Özel işim"),
                                 (self.ali["id"], "Bilirkişiyi ara"), (self.ayse["id"], "Toplantı notlarını gönder")})
        free = [m for u, m in n.test_sink if m["body"].startswith("Bilirkişiyi ara")][0]
        self.assertIsNone(free["task_id"])
        self.assertIn("Dosya 2024/11435", free["body"])
        # Gönderilen bağımsız hatırlatıcı listede "gönderildi" olarak kalır; alıcı silebilir.
        lst = (await self.j(await ali.get("/zs/api/hatirlatici")))["reminders"]
        self.assertTrue(lst[0]["sent_at"])
        await self.j(await ali.delete(f"/zs/api/hatirlatici/{b1['id']}", headers=H))

    async def test_hatirlatici_alt_is_ve_liste(self):
        import time as _t
        n = zs.C.notifier
        n.test_sink = []
        adm = await self.login("yonetici")
        ali = await self.login("ali")
        ayse = await self.login("ayse")
        soon = int(_t.time()) + 120
        t = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Keşif", "assignee_id": self.ali["id"]}, headers=H)))["task"]
        sub = (await self.j(await adm.post("/zs/api/tasks", json={"title": "Fotoğraf çek", "parent_id": t["id"],
                                                                  "assignee_id": self.ayse["id"]}, headers=H)))["task"]
        # Alt işte herkes, işi gören herkese kurar: Ali → Ayşe, Ayşe → Ali (genel uç, task_id = alt iş).
        r1 = (await self.j(await ali.post("/zs/api/hatirlatici", json={"task_id": sub["id"], "remind_at": soon,
                                                                       "user_id": self.ayse["id"]}, headers=H)))["reminder"]
        self.assertEqual((r1["task_id"], r1["sub_id"]), (t["id"], sub["id"]))
        await self.j(await ayse.post("/zs/api/hatirlatici", json={"task_id": sub["id"], "remind_at": soon, "user_id": self.ali["id"]}, headers=H))
        # Ana işte kullanıcı başkasına kuramaz; yönetici atadığı kişiye kurar.
        await self.j(await ali.post("/zs/api/hatirlatici", json={"task_id": t["id"], "remind_at": soon, "user_id": self.ayse["id"]}, headers=H), 400)
        await self.j(await adm.post("/zs/api/hatirlatici", json={"task_id": t["id"], "remind_at": soon + 5, "user_id": self.ali["id"]}, headers=H))
        # Hatırlatıcılar ekranı: Ali'ye kurulan 2 + Ali'nin kurduğu 1; başlıklar dolu.
        lst = (await self.j(await ali.get("/zs/api/hatirlaticilar")))["reminders"]
        self.assertEqual(len(lst), 3)
        r = [x for x in lst if x["id"] == r1["id"]][0]
        self.assertEqual((r["task_title"], r["sub_title"]), ("Keşif", "Fotoğraf çek"))
        # Bildirim: alt işte "İş › Alt iş".
        await n.drain(); n.test_sink[:] = []
        self.assertEqual(await n.remind_tick(soon + 60), 3)
        heads = sorted((u, m["body"].split("\n")[0]) for u, m in n.test_sink)
        self.assertEqual(heads, sorted([(self.ayse["id"], "Keşif › Fotoğraf çek"), (self.ali["id"], "Keşif › Fotoğraf çek"),
                                        (self.ali["id"], "Keşif")]))
        # Alt iş silinince hatırlatıcıları da gider.
        await self.j(await adm.post("/zs/api/hatirlatici", json={"task_id": sub["id"], "remind_at": soon + 3600}, headers=H))
        await self.j(await adm.delete(f"/zs/api/tasks/{sub['id']}", headers=H))
        self.assertEqual(await n.remind_tick(soon + 7200), 0)

    # ── Telegram grubu ──
    def _tg_setup(self):
        """Sahte Telegram: gönderilen yanıtlar self.tg_out'a, indirilecek dosyalar self.tg_files'tan."""
        n = zs.C.notifier
        n.test_sink = []
        n.tg_bot = "zs_bot"
        self.tg_out, self.tg_files, self._mid = [], {}, [9000]

        async def reply(chat_id, text, reply_to=None):
            self._mid[0] += 1
            self.tg_out.append((chat_id, text, reply_to, self._mid[0]))
            return self._mid[0]

        async def download(file_id, limit):
            data = self.tg_files[file_id]
            if len(data) > limit:
                raise ValueError("büyük")
            return data
        n.tg_reply, n.tg_download = reply, download
        old = os.environ.get("ZS_TELEGRAM_GROUPS")
        os.environ["ZS_TELEGRAM_GROUPS"] = "-100500, -100600"
        self.addCleanup(lambda: os.environ.__setitem__("ZS_TELEGRAM_GROUPS", old) if old is not None
                        else os.environ.pop("ZS_TELEGRAM_GROUPS", None))
        adm = [u for u in self.store.list_users() if u["username"] == "yonetici"][0]
        for uid, tg_id, uname in ((adm["id"], 1001, "Patron"), (self.ali["id"], 1002, "ali_tg"),
                                  (self.ayse["id"], 1003, None)):
            self.assertEqual(self.store.tg_link(self.store.tg_code_new(uid), tg_id, "x", uname), uid)
        return n

    _tg_seq = [100]

    def tgmsg(self, text, frm, chat=-100500, reply=None, **extra):
        import re as _re
        self._tg_seq[0] += 1
        ents = []
        m = _re.match(r"/\w+(@\w+)?", text or "")
        if m:
            ents.append({"type": "bot_command", "offset": 0, "length": m.end()})
        ents += [{"type": "mention", "offset": x.start(), "length": x.end() - x.start()}
                 for x in _re.finditer(r"(?<![/\w])@\w+", text or "")]
        msg = {"message_id": self._tg_seq[0], "date": 1760000000, "chat": {"id": chat, "type": "supergroup"},
               "from": {"id": frm[0], "username": frm[1], "first_name": "Ad"}, **extra}
        if text is not None:
            if extra.get("photo") or extra.get("document"):
                msg["caption"], msg["caption_entities"] = text, ents
            else:
                msg["text"], msg["entities"] = text, ents
        if reply:
            msg["reply_to_message"] = reply
        return msg

    async def tg(self, msg):
        await zs.C.notifier.tg_update({"message": msg})
        return self.tg_out[-1][1] if self.tg_out else None

    def test_tg_komut_ayristirma(self):
        import zs_tg_grup as g
        # Emoji UTF-16'da iki birimdir; konumlar buna göre verilir.
        text = "/is@zs_bot 🙂 @ayse_tg Dilekçe yaz\nYarın 10'da"
        m = {"text": text, "entities": [{"type": "bot_command", "offset": 0, "length": 10},
                                        {"type": "mention", "offset": 14, "length": 8}]}
        r = g.parse_command(m, "zs_bot")
        self.assertEqual(r["mentions"], [("username", "ayse_tg")])
        self.assertEqual((r["title"], r["description"]), ("🙂 Dilekçe yaz", "Yarın 10'da"))
        m2 = {"text": "/is Ali'ye", "entities": [{"type": "bot_command", "offset": 0, "length": 3},
                                                  {"type": "text_mention", "offset": 4, "length": 3,
                                                   "user": {"id": 77, "first_name": "Ali"}}]}
        r2 = g.parse_command(m2, "zs_bot")
        self.assertEqual((r2["mentions"], r2["title"]), ([("id", 77, "Ali")], "'ye"))
        for t, ln in (("/is@baska_bot x", 13), ("/isim x", 5)):
            self.assertIsNone(g.parse_command({"text": t, "entities": [{"type": "bot_command", "offset": 0, "length": ln}]}, "zs_bot"))
        self.assertIsNone(g.parse_command({"text": "selam"}, "zs_bot"))
        foto = g.attachment({"date": 1760000000, "photo": [{"file_id": "k", "file_size": 10}, {"file_id": "b", "file_size": 99}]})
        self.assertEqual(foto["file_id"], "b")
        self.assertTrue(foto["filename"].startswith("telegram_foto_") and foto["filename"].endswith(".jpg"))
        self.assertEqual(g.attachment({"voice": {"file_id": "v"}}), {"unsupported": "voice"})

    async def test_tg_grupta_is_ac_ve_belge_ekle(self):
        self._tg_setup()
        ADM, ALI, AYSE, YABANCI = (1001, "Patron"), (1002, "ali_tg"), (1003, None), (2000, "kimse")
        JPG = b"\xff\xd8\xff\xe0" + b"j" * 500
        self.tg_files.update({"foto1": JPG, "pdf1": PDF, "exe1": b"MZ\x90\x00"})
        n_tasks = lambda: len(self.store._run(lambda cur: cur.execute("SELECT id FROM zs_tasks").fetchall()))

        # Yetkisiz grupta grup kimliği söylenir; sıradan sohbet yok sayılır; bağlı olmayan kişi reddedilir.
        self.assertIn("-100999", await self.tg(self.tgmsg("/is @ali_tg x", ADM, chat=-100999)))
        k = len(self.tg_out)
        await self.tg(self.tgmsg("merhaba @ali_tg", ADM))
        self.assertEqual(len(self.tg_out), k)
        self.assertIn("tanımıyorum", await self.tg(self.tgmsg("/is x", YABANCI)))
        self.assertEqual(n_tasks(), 0)

        # Yönetici bir fotoğrafa yanıt vererek Ali'ye iş açar → fotoğraf belge olarak eklenir.
        foto = self.tgmsg(None, AYSE, photo=[{"file_id": "foto1", "file_size": len(JPG)}])
        out = await self.tg(self.tgmsg("/is @ali_tg Tebligatı işle\nAcele", ADM, reply=foto))
        self.assertIn("İş açıldı", out)
        bot_mid = self.tg_out[-1][3]
        t = self.store._run(lambda cur: self.store._one(self.store._q(cur, "SELECT * FROM zs_tasks")))
        self.assertEqual((t["title"], t["description"], t["assignee_id"]), ("Tebligatı işle", "Acele", self.ali["id"]))
        ali = await self.login("ali")
        d = (await self.j(await ali.get(f"/zs/api/tasks/{t['id']}")))["task"]
        self.assertEqual(len(d["files"]), 1)
        r = await ali.get(f"/zs/api/files/{d['files'][0]['id']}")
        self.assertEqual(await r.read(), JPG)
        await zs.C.notifier.drain()
        self.assertIn((self.ali["id"], "Size yeni bir iş atandı"), [(u, m["title"]) for u, m in zs.C.notifier.test_sink])

        # Botun mesajına yanıt: PDF + açıklama → aynı işe belge + not. Ali de ekleyebilir.
        bot_msg = {"message_id": bot_mid, "from": {"id": 1, "is_bot": True}, "chat": {"id": -100500}}
        out = await self.tg(self.tgmsg("Ek evrak", ALI, reply=bot_msg,
                                       document={"file_id": "pdf1", "file_name": "ek.pdf", "file_size": len(PDF)}))
        self.assertIn("Belge eklendi: ek.pdf", out)
        d = (await self.j(await ali.get(f"/zs/api/tasks/{t['id']}")))["task"]
        self.assertEqual(sorted(f["filename"] for f in d["files"])[0], "ek.pdf")
        self.assertEqual(d["notes"][0]["body"], "Ek evrak")
        # Ayşe işi göremez → ekleyemez.
        self.assertIn("İş bulunamadı", await self.tg(self.tgmsg("not", AYSE, reply=bot_msg)))

        # Kullanıcı başkasına iş açamaz; etiketsiz kendine açar.
        self.assertIn("yalnız yönetici", await self.tg(self.tgmsg("/is @ali_tg x", AYSE)))
        await self.tg(self.tgmsg("/is Kendi işim", AYSE))
        own = self.store._run(lambda cur: self.store._one(self.store._q(cur, "SELECT * FROM zs_tasks WHERE title = 'Kendi işim'")))
        self.assertEqual(own["assignee_id"], self.ayse["id"])

        # Bağlı olmayan etiket ve kabul edilmeyen dosya → iş hiç açılmaz.
        k = n_tasks()
        self.assertIn("@kimse", await self.tg(self.tgmsg("/is @kimse x", ADM)))
        exe = self.tgmsg(None, ADM, document={"file_id": "exe1", "file_name": "a.exe", "file_size": 4})
        self.assertIn("kabul edilmiyor", await self.tg(self.tgmsg("/is @ali_tg x", ADM, reply=exe)))
        self.assertEqual(n_tasks(), k)

        # Birden çok etiket: ilki atanan, diğerleri ek atanan. Ayşe'nin kullanıcı adı yoktu; grupta
        # yazınca öğrenilir.
        self.store.tg_touch(1003, "Ayse_TG")
        await self.tg(self.tgmsg("/is @ali_tg @ayse_tg Ortak iş", ADM))
        t2 = self.store._run(lambda cur: self.store._one(self.store._q(cur, "SELECT * FROM zs_tasks WHERE title = 'Ortak iş'")))
        self.assertEqual(t2["assignee_id"], self.ali["id"])
        self.assertEqual(set(self.store.task_roles(t2["id"])["assignees"]), {self.ali["id"], self.ayse["id"]})
        # Denetim kaydı.
        acts = [r["action"] for r in self.store.audit_rows(limit=50)]
        self.assertIn("tg_is_ac", acts)
        self.assertIn("tg_ekle", acts)

    def test_rapor_donem_dilimleri(self):
        import zs_rapor
        s, e = zs_rapor.parse_range("2026-01-15", "2026-03-10")
        ay = zs_rapor._buckets(s, e, "ay")
        self.assertEqual([b[0] for b in ay], ["Oca 2026", "Şub 2026", "Mar 2026"])
        self.assertEqual((ay[0][1], ay[-1][2]), (s, e))  # uçlar aralığa kırpılır
        hafta = zs_rapor._buckets(*zs_rapor.parse_range("2026-10-01", "2026-10-11"), "hafta")
        self.assertEqual([b[0] for b in hafta], ["28 Eyl – 4 Eki", "5 Eki – 11 Eki"])

    def test_sigv4_aws_test_vektoru(self):
        h = zs_r2.sigv4_headers("GET", "example.amazonaws.com", "/", None, "AKIDEXAMPLE",
                                "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", region="us-east-1",
                                service="service", amz_date="20150830T123600Z")
        self.assertTrue(h["Authorization"].endswith(
            "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"))


class IsListesiTest(unittest.IsolatedAsyncioTestCase):
    """/is-listesi: ana hesaplarla giriş, büro yalıtımı, UYAP dosyası bağı, masaüstü jetonu,
    tek kullanımlık web geçişi ve eski kullanıcı eşleştirme. Eski /zs aynı arka uçta yedek kalır."""

    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "accounts.json")
        self.acc = accounts.AccountStore(path, backend=accounts._FileBackend(path))
        a = self.acc.create_office("Kemal Hukuk", "ahmet", PW, slug="kemal")
        self.acc.create_user(a["office_id"], "mehmet", PW, label="Mehmet Yılmaz")
        b = self.acc.create_office("Deniz Hukuk", "zeynep", PW, slug="deniz")
        self.oa, self.ob = a["office_id"], b["office_id"]
        store = ZsStore(dsn="", sqlite_path=os.path.join(self.tmp, "zs.sqlite3"))
        store.create_user("yonetici", "Yönetici", PW, "admin")       # eski /zs havuzu
        self.ali = store.create_user("ali", "Ali", PW, "user")
        app = web.Application()
        zs.register(app, session_secret="test-secret", guard=_Guard(), client_ip=lambda r: "1.2.3.4", store=store,
                    accounts_store=self.acc, owner_user=lambda r: "utku" if r.headers.get("X-Test-Owner") else None)
        zs.C.storage = zs_r2.LocalStorage(os.path.join(self.tmp, "files"))
        zs.C.file_key = os.urandom(32)
        self.store = store
        self.clients = []

    async def asyncTearDown(self):
        for c in self.clients:
            await c.close()
        self.store._conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    async def client(self):
        app = web.Application()
        zs_app_routes(app)
        c = TestClient(TestServer(app))
        await c.start_server()
        self.clients.append(c)
        return c

    async def login(self, office, username, status=303):
        c = await self.client()
        r = await c.post("/is-listesi/login", data={"office_code": office, "username": username, "password": PW},
                         allow_redirects=False)
        self.assertEqual(r.status, status)
        return c

    async def legacy(self, username):
        c = await self.client()
        r = await c.post("/zs/login", data={"username": username, "password": PW}, allow_redirects=False)
        self.assertEqual(r.status, 303)
        return c

    async def j(self, resp, status=200):
        data = await resp.json()
        self.assertEqual(resp.status, status, data)
        return data

    async def people(self, c):
        return {p["display_name"]: p["id"] for p in (await self.j(await c.get("/is-listesi/api/me")))["people"]}

    DOSYA = {"dosya_id": "987654", "birim_id": "1001", "birim_adi": "İstanbul 3. İcra Dairesi",
             "dosya_no": "2026/1234", "dosya_tur": "İcra"}

    async def test_giris_buro_yalitimi_ve_dosya_bagi(self):
        ahmet = await self.login("kemal", "ahmet")
        me = await self.j(await ahmet.get("/is-listesi/api/me"))
        self.assertEqual(me["me"]["role"], "admin")                  # büro sahibi → yönetici
        self.assertEqual(me["office"]["code"], "kemal")
        ppl = await self.people(ahmet)
        self.assertIn("Mehmet Yılmaz", ppl)                          # hiç girmemiş üyeye de atanabilir
        self.assertNotIn("Ali", ppl)                                 # eski /zs havuzu görünmez
        t = (await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={
            "title": "Haciz talebi", "assignee_id": ppl["Mehmet Yılmaz"], "dosya": self.DOSYA})))["task"]
        self.assertEqual((t["dosya_id"], t["dosya_no"], t["office_id"]), ("987654", "2026/1234", self.oa))
        await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={"title": "Dosyasız iş"}))
        lst = await self.j(await ahmet.get("/is-listesi/api/tasks?filtre=tumu&dosya_id=987654"))
        self.assertEqual([x["id"] for x in lst["tasks"]], [t["id"]])
        self.assertIsNone(t["evrak_id"])
        # UYAP dosyaId'si oturumla değişir: birim + dosya no ile eşleşme kimlik farklı olsa da bulur.
        lst = await self.j(await ahmet.get("/is-listesi/api/tasks?filtre=tumu&dosya_id=yeni-oturum-kimligi"
                                           "&birim_id=1001&dosya_no=2026/1234"))
        self.assertEqual([x["id"] for x in lst["tasks"]], [t["id"]])
        lst = await self.j(await ahmet.get("/is-listesi/api/tasks?filtre=tumu&birim_id=1001&dosya_no=2026/9999"))
        self.assertEqual(lst["tasks"], [])
        # Belgeye bağlı iş: evrak_id + adı saklanır (evrak ağacında mor görünür); evrak_id'siz ad atılır.
        b = (await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={
            "title": "Bilirkişi raporuna itiraz", "dosya": dict(self.DOSYA, evrak_id="55501", evrak_adi="Bilirkişi Raporu")})))["task"]
        self.assertEqual((b["dosya_id"], b["evrak_id"], b["evrak_adi"]), ("987654", "55501", "Bilirkişi Raporu"))
        b2 = (await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={
            "title": "y", "dosya": dict(self.DOSYA, evrak_adi="sahipsiz ad")})))["task"]
        self.assertEqual((b2["evrak_id"], b2["evrak_adi"]), (None, None))
        await self.j(await ahmet.delete(f"/is-listesi/api/tasks/{b['id']}", headers=H))
        await self.j(await ahmet.delete(f"/is-listesi/api/tasks/{b2['id']}", headers=H))
        # Başka büro: görmez, kendi kişisi olmayana atayamaz.
        zeynep = await self.login("deniz", "zeynep")
        self.assertEqual((await self.j(await zeynep.get("/is-listesi/api/tasks?filtre=tumu")))["tasks"], [])
        await self.j(await zeynep.get(f"/is-listesi/api/tasks/{t['id']}"), 400)
        await self.j(await zeynep.post("/is-listesi/api/tasks", headers=H, json={
            "title": "x", "assignee_id": ppl["Mehmet Yılmaz"]}), 400)
        # Eski /zs havuzu da görmez.
        eski = await self.legacy("yonetici")
        self.assertEqual((await self.j(await eski.get("/zs/api/tasks?filtre=tumu")))["tasks"], [])
        # Üye yalnız kendine iş açar; işi görür.
        mehmet = await self.login("kemal", "mehmet")
        kendi = (await self.j(await mehmet.post("/is-listesi/api/tasks", headers=H, json={
            "title": "y", "assignee_id": ppl["Ahmet"] if "Ahmet" in ppl else me["me"]["id"]})))["task"]
        self.assertEqual(kendi["assignee_id"], ppl["Mehmet Yılmaz"])
        ids = {x["id"] for x in (await self.j(await mehmet.get("/is-listesi/api/tasks")))["tasks"]}
        self.assertIn(t["id"], ids)

    async def test_uyap_kart_verisi_saklanir(self):
        """'UYAP'ta aç' için kartın UYAP verisi türleriyle saklanır; tanınmayan/iç içe alanlar atılır."""
        ahmet = await self.login("kemal", "ahmet")
        uyap = {"dosyaId": "987654", "dosyaNo": "2026/1234", "birimId": "1001", "birimAdi": "İstanbul 3. İcra Dairesi",
                "dosyaTurKod": 35, "dosyaTurKodAciklama": "İcra", "dosyaDurumKod": 0, "birimTuru3": 1101,
                "zararli": "<script>", "dosyaDurum": {"ic": "nesne"}}
        t = (await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={
            "title": "Kart", "dosya": dict(self.DOSYA, uyap=uyap)})))["task"]
        kart = json.loads(t["dosya_uyap"])
        self.assertEqual(kart["dosyaTurKod"], 35)                     # sayı sayı kalır
        self.assertNotIn("zararli", kart)
        self.assertNotIn("dosyaDurum", kart)                          # nesne değer alınmaz
        # Kart verisi olmayan (eski) işte alan boş; düzenlemede dosya bağı kaldırılabilir.
        t2 = (await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={"title": "Eski", "dosya": self.DOSYA})))["task"]
        self.assertIsNone(t2["dosya_uyap"])
        t3 = (await self.j(await ahmet.patch(f"/is-listesi/api/tasks/{t['id']}", headers=H, json={"dosya": None})))["task"]
        self.assertIsNone(t3["dosya_id"]); self.assertIsNone(t3["dosya_uyap"])

    async def test_eski_kullanici_ve_owner_yeni_arayuze_giremez(self):
        c = await self.client()
        r = await c.post("/is-listesi/login", data={"office_code": "", "username": "ali", "password": PW},
                         allow_redirects=False)
        self.assertEqual(r.status, 401)
        await self.login("kemal", "ahmet", status=303)
        await self.login("kemal", "ahmet-yok", status=401)
        # Eski /zs oturum çerezi yeni arayüzde geçmez (ayrı çerez + bağlı hesap şartı).
        eski = await self.legacy("ali")
        self.assertEqual((await eski.get("/is-listesi/api/me")).status, 401)

    async def test_ana_hesap_degisince_oturum_duser(self):
        ahmet = await self.login("kemal", "ahmet")
        await self.j(await ahmet.get("/is-listesi/api/me"))
        self.acc.bump_sess_epoch("ahmet@kemal")                      # ana hesapta çıkış / parola değişimi
        self.assertEqual((await ahmet.get("/is-listesi/api/me")).status, 401)
        mehmet = await self.login("kemal", "mehmet")
        self.acc.set_user_active("mehmet@kemal", False)
        self.assertEqual((await mehmet.get("/is-listesi/api/me")).status, 401)
        await self.login("kemal", "mehmet", status=401)
        # Pasif hesap büro listesinde kapalı görünür, ona iş atanamaz.
        ahmet = await self.login("kemal", "ahmet")
        u = await self.j(await ahmet.get("/is-listesi/api/users"))
        m = next(x for x in u["users"] if x["account_username"] == "mehmet@kemal")
        self.assertFalse(m["active"])
        await self.j(await ahmet.post("/is-listesi/api/tasks", headers=H, json={"title": "x", "assignee_id": m["id"]}), 400)

    async def test_hesapli_kisiler_ofis_panelinden_yonetilir(self):
        ahmet = await self.login("kemal", "ahmet")
        ppl = await self.people(ahmet)
        mid = ppl["Mehmet Yılmaz"]
        await self.j(await ahmet.post("/is-listesi/api/users", headers=H, json={"username": "x", "display_name": "X"}), 400)
        await self.j(await ahmet.post(f"/is-listesi/api/users/{mid}/sifre", headers=H, json={}), 400)
        await self.j(await ahmet.post(f"/is-listesi/api/users/{mid}/aktif", headers=H, json={"active": False}), 400)
        await self.j(await ahmet.post("/is-listesi/api/me/password", headers=H, json={"old": PW, "new": PW + "x"}), 400)
        # Rol değişir: üye yönetici yapılabilir ve sonraki girişte de öyle kalır.
        await self.j(await ahmet.post(f"/is-listesi/api/users/{mid}", headers=H, json={"role": "admin"}))
        mehmet = await self.login("kemal", "mehmet")
        self.assertEqual((await self.j(await mehmet.get("/is-listesi/api/me")))["me"]["role"], "admin")
        # Büro sahibinin rolü düşürülemez; başka büronun kişisi düzenlenemez.
        aid = (await self.j(await ahmet.get("/is-listesi/api/me")))["me"]["id"]
        await self.j(await mehmet.post(f"/is-listesi/api/users/{aid}", headers=H, json={"role": "user"}), 400)
        zeynep = await self.login("deniz", "zeynep")
        await self.j(await zeynep.post(f"/is-listesi/api/users/{mid}", headers=H, json={"role": "user"}), 400)

    async def test_masaustu_jetonu_ve_web_gecisi(self):
        c = await self.client()
        r = await self.j(await c.post("/is-listesi/api/token", json={"office_code": "kemal", "username": "ahmet",
                                                                      "password": "yanlis"}), 401)
        r = await self.j(await c.post("/is-listesi/api/token", json={"office_code": "kemal", "username": "ahmet",
                                                                      "password": PW}))
        auth = {"Authorization": f"Bearer {r['token']}"}
        # Jetonla X-ZS başlığı gerekmez; dosya kartından iş açılır ve dosyanın işleri listelenir.
        t = (await self.j(await c.post("/is-listesi/api/tasks", headers=auth,
                                       json={"title": "Kart işi", "dosya": self.DOSYA})))["task"]
        lst = await self.j(await c.get("/is-listesi/api/tasks?filtre=tumu&dosya_id=987654", headers=auth))
        self.assertEqual([x["id"] for x in lst["tasks"]], [t["id"]])
        # Jeton çerez yerine geçmez; bozuk jeton reddedilir.
        self.assertEqual((await c.get("/is-listesi/api/me", headers={"Authorization": "Bearer xx"})).status, 401)
        # Tek kullanımlık web geçişi: kod bir kez çalışır, oturum çerezi kurulur.
        k = await self.j(await c.post("/is-listesi/api/sso-kod", headers=auth, json={}))
        web_c = await self.client()
        r1 = await web_c.get(k["url"], allow_redirects=False)
        self.assertEqual(r1.status, 200)
        self.assertIn("il_sess", r1.cookies)
        me = await self.j(await web_c.get("/is-listesi/api/me"))
        self.assertEqual(me["me"]["account_username"], "ahmet@kemal")
        other = await self.client()
        self.assertEqual((await other.get(k["url"], allow_redirects=False)).status, 401)
        # ?is=<id> ile geçiş doğrudan o işe gider; sayı olmayan değer yok sayılır.
        for ek, hedef in ((f"&is={t['id']}", f"/is-listesi#is-{t['id']}"), ("&is=x'><b>", "url=/is-listesi'")):
            k2 = await self.j(await c.post("/is-listesi/api/sso-kod", headers=auth, json={}))
            self.assertIn(hedef, await (await (await self.client()).get(k2["url"] + ek, allow_redirects=False)).text())
        # Ana hesap epoch'u değişince jeton da düşer.
        self.acc.bump_sess_epoch("ahmet@kemal")
        self.assertEqual((await c.get("/is-listesi/api/me", headers=auth)).status, 401)

    async def test_eslestirme(self):
        # Eski havuzda yönetici Ali'ye iş açmış; Mehmet'in hesabı İş Listesi'ne önceden girmiş (boş satır).
        eski = await self.legacy("yonetici")
        t = (await self.j(await eski.post("/zs/api/tasks", headers=H, json={"title": "Eski iş", "assignee_id": self.ali["id"]})))["task"]
        await self.j(await eski.post("/zs/api/tasks", headers=H, json={"title": "alt", "parent_id": t["id"]}))
        await self.login("kemal", "mehmet")
        c = await self.client()
        self.assertEqual((await c.get("/is-listesi/yonetim/eslestir")).status, 403)
        own = {"X-Test-Owner": "1"}
        page = await c.get("/is-listesi/yonetim/eslestir", headers=own)
        self.assertIn("mehmet@kemal", await page.text())
        r = await c.post("/is-listesi/yonetim/eslestir", headers=own, allow_redirects=False,
                         data={"csrf": "sahte", "zs_user_id": str(self.ali["id"]), "account_username": "mehmet@kemal"})
        self.assertEqual(r.status, 403)
        r = await c.post("/is-listesi/yonetim/eslestir", headers=own, allow_redirects=False,
                         data={"csrf": zs._map_csrf("utku"), "zs_user_id": str(self.ali["id"]),
                               "account_username": "mehmet@kemal"})
        self.assertEqual(r.status, 303)
        self.assertNotIn("hata=1", r.headers["Location"])
        u = self.store.get_user(self.ali["id"])
        self.assertEqual((u["account_username"], u["office_id"]), ("mehmet@kemal", self.oa))
        self.assertEqual(self.store.get_task(t["id"])["office_id"], self.oa)
        self.assertTrue(all(s["office_id"] == self.oa for s in self.store.task_detail(t["id"])["subtasks"]))
        # Mehmet yeni arayüzde eski işini görür; eski havuzun yöneticisi artık görmez.
        mehmet = await self.login("kemal", "mehmet")
        me = await self.j(await mehmet.get("/is-listesi/api/me"))
        self.assertEqual(me["me"]["id"], self.ali["id"])
        self.assertEqual([x["id"] for x in (await self.j(await mehmet.get("/is-listesi/api/tasks")))["tasks"]], [t["id"]])
        self.assertEqual((await self.j(await eski.get("/zs/api/tasks?filtre=tumu")))["tasks"], [])
        # Aynı hesap ikinci kez eşlenemez.
        ayse = self.store.create_user("ayse", "Ayşe", PW, "user")
        r = await c.post("/is-listesi/yonetim/eslestir", headers=own, allow_redirects=False,
                         data={"csrf": zs._map_csrf("utku"), "zs_user_id": str(ayse["id"]),
                               "account_username": "mehmet@kemal"})
        self.assertIn("hata=1", r.headers["Location"])

    async def test_masaustu_koprusu(self):
        """uyap_core/is_listesi: dosya kartındaki 'İş Ata' yerel köprüsü gerçek sunucuya karşı."""
        from uyap_core import is_listesi as k
        c = await self.client()
        base = str(c.make_url("")).rstrip("/")
        try:
            k.clear()
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/me", "", b"")
            self.assertEqual(st, 503)
            k.configure(base, "kemal", "ahmet", "yanlis")
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/me", "", b"")
            self.assertEqual(st, 401)
            k.configure(base, "kemal", "ahmet", PW)
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/me", "", b"")
            ppl = {p["display_name"]: p["id"] for p in json.loads(out)["people"]}
            self.assertEqual(st, 200)
            body = json.dumps({"title": "Kart işi", "assignee_id": ppl["Mehmet Yılmaz"], "dosya": self.DOSYA,
                               "status": "onaylandi"}).encode()      # izinsiz alan iletilmez
            st, _, out = await k.handle("POST", "/__uyap_kisi__/is-listesi/tasks", "", body)
            t = json.loads(out)["task"]
            self.assertEqual((st, t["status"], t["dosya_id"]), (200, "acik", "987654"))
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/tasks", "dosya_id=987654", b"")
            self.assertEqual([x["id"] for x in json.loads(out)["tasks"]], [t["id"]])
            # Sağdaki iş paneli: dosyasız liste (varsayılan "benim" — iş Mehmet'te, Ahmet'in değil;
            # yönetici "tümü"nde görür); bilinmeyen süzgeç reddedilir.
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/tasks", "", b"")
            self.assertEqual((st, json.loads(out)["tasks"]), (200, []))
            st, _, out = await k.handle("GET", "/__uyap_kisi__/is-listesi/tasks", "filtre=tumu", b"")
            self.assertEqual((st, [x["id"] for x in json.loads(out)["tasks"]]), (200, [t["id"]]))
            st, _, _ = await k.handle("GET", "/__uyap_kisi__/is-listesi/tasks", "filtre=hepsi", b"")
            self.assertEqual(st, 400)
            st, hdr, _ = await k.handle("GET", "/__uyap_kisi__/is-listesi/ac", "", b"")
            self.assertEqual(st, 303)
            self.assertTrue(hdr["Location"].startswith(base + "/is-listesi/sso?kod="))
            st, hdr, _ = await k.handle("GET", "/__uyap_kisi__/is-listesi/ac", f"is={t['id']}", b"")
            self.assertTrue(hdr["Location"].endswith(f"&is={t['id']}"))
            st, hdr, _ = await k.handle("GET", "/__uyap_kisi__/is-listesi/ac", "is=1%26x%3D2", b"")
            self.assertNotIn("&is", hdr["Location"])
            st, _, _ = await k.handle("DELETE", "/__uyap_kisi__/is-listesi/tasks", "", b"")
            self.assertEqual(st, 404)
            # Jeton sunucuda geçersiz kalırsa (ör. ana hesapta çıkış) köprü yeniden alır.
            self.acc.bump_sess_epoch("ahmet@kemal")
            st, _, _ = await k.handle("GET", "/__uyap_kisi__/is-listesi/me", "", b"")
            self.assertEqual(st, 200)
            self.assertTrue(k.is_path("__uyap_kisi__/is-listesi/me") and not k.is_path("/__uyap_agent__/jobs"))
        finally:
            k.clear()

    async def test_eski_arayuz_yedek_olarak_durur(self):
        c = await self.client()
        self.assertIn("İş Takip", await (await c.get("/zs")).text())
        self.assertIn("Ofis kodu", await (await c.get("/is-listesi")).text())
        for p in ("/zs", "/is-listesi"):
            r = await c.get(f"{p}/static/zs.js")
            self.assertEqual(r.status, 200)
            self.assertIn(f"{p}/api/me", await r.text())
            sw = await c.get(f"{p}/sw.js")
            self.assertEqual(sw.headers.get("Service-Worker-Allowed"), p)
            # Sayfanın istediği her dosya gerçekten sunuluyor (yanlış ad → stilsiz/boş sayfa).
            with open(os.path.join(zs.WEB_DIRS[p], "index.html"), encoding="utf-8") as f:
                refs = __import__("re").findall(r'(?:href|src)="(/[^"]+)"', f.read())
            self.assertTrue(any(r.endswith(".css") for r in refs) and any(r.endswith(".js") for r in refs))
            for ref in refs:
                self.assertTrue(ref.startswith(p + "/"), ref)
                self.assertEqual((await c.get(ref)).status, 200, ref)


def zs_app_routes(app):
    """register()'ın rota kısmını mevcut C durumunu bozmadan yeni bir uygulamaya bağlar."""
    saved = (zs.C.store, zs.C.storage, zs.C.file_key, zs.C.notifier)
    zs.register(app, session_secret="test-secret", guard=zs.C.guard, client_ip=zs.C.client_ip, store=saved[0],
                accounts_store=zs.C.accounts, owner_user=zs.C.owner_user)
    zs.C.store, zs.C.storage, zs.C.file_key, zs.C.notifier = saved


if __name__ == "__main__":
    unittest.main()
