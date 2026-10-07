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


def zs_app_routes(app):
    """register()'ın rota kısmını mevcut C durumunu bozmadan yeni bir uygulamaya bağlar."""
    saved = (zs.C.store, zs.C.storage, zs.C.file_key, zs.C.notifier)
    zs.register(app, session_secret="test-secret", guard=zs.C.guard, client_ip=zs.C.client_ip, store=saved[0])
    zs.C.store, zs.C.storage, zs.C.file_key, zs.C.notifier = saved


if __name__ == "__main__":
    unittest.main()
