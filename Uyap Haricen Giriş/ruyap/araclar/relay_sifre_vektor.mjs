// Sunucu üzerinden aktarma şifrelemesi: tarayıcı kodu (webapp/js/wire.js) ile Rust (src/relay_sifre.rs) aynı sonucu
// veriyor mu? Bu betik tarayıcıdaki WebCrypto koduyla bir test vektörü üretir ve önce kendi içinde sınar;
// Rust testi (`relay_sifre::tests::webcrypto_test_vektoru`) aynı vektörü çözer ve aynı şifreli metni üretir.
// Çalıştırma: node araclar/relay_sifre_vektor.mjs  → araclar/relay_sifre_vektor.json
import { writeFileSync } from "node:fs";
import { relayAnahtarlari, RelayYon, relayIstemciAnahtari } from "../webapp/js/wire.js";

const subtle = globalThis.crypto.subtle;
const hex = (b) => Buffer.from(b).toString("hex");
const ecdh = { name: "ECDH", namedCurve: "P-256" };

// Vektör için dışa aktarılabilir anahtarlar (gerçek istemcide gizli anahtar dışa aktarılamaz).
const istemci = await subtle.generateKey(ecdh, true, ["deriveBits"]);
const ofis = await subtle.generateKey(ecdh, true, ["deriveBits"]);
const istemciPub = new Uint8Array(await subtle.exportKey("raw", istemci.publicKey));
const ofisPub = new Uint8Array(await subtle.exportKey("raw", ofis.publicKey));
const sir = new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: ofis.publicKey }, istemci.privateKey, 256));
const sir2 = new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: istemci.publicKey }, ofis.privateKey, 256));
if (hex(sir) !== hex(sir2)) throw new Error("ECDH iki tarafta farklı");
const istemciGizli = Buffer.from((await subtle.exportKey("jwk", istemci.privateKey)).d, "base64url");

const [c2o, o2c] = await relayAnahtarlari(sir, istemciPub, ofisPub);
const ig = await RelayYon.yeni(c2o), og = await RelayYon.yeni(o2c);
const enc = new TextEncoder();
const istemciCerceveleri = [], ofisCerceveleri = [];
for (const acik of ["istek 1: GET /", "istek 2: büro verisi (Türkçe ğüşıöç)"]) istemciCerceveleri.push({ acik, sifreli: hex(await ig.sifrele(enc.encode(acik))) });
for (const acik of ["cevap 1", "cevap 2: ödeme emri"]) ofisCerceveleri.push({ acik, sifreli: hex(await og.sifrele(enc.encode(acik))) });

// Kendi içinde: çözülür, bozulan çerçeve reddedilir.
const al = await RelayYon.yeni(c2o);
for (const c of istemciCerceveleri) {
  const a = new TextDecoder().decode(await al.coz(Buffer.from(c.sifreli, "hex")));
  if (a !== c.acik) throw new Error("çözülemedi");
}
const bozuk = Buffer.from(istemciCerceveleri[0].sifreli, "hex"); bozuk[0] ^= 1;
let reddedildi = false;
try { await (await RelayYon.yeni(c2o)).coz(bozuk); } catch (_) { reddedildi = true; }
if (!reddedildi) throw new Error("bozuk çerçeve kabul edildi");
// Gerçek istemci akışı (dışa aktarılamayan anahtar) da çalışıyor.
const a = await relayIstemciAnahtari();
if (a.pub.length !== 65) throw new Error("açık anahtar 65 bayt olmalı");

writeFileSync(new URL("./relay_sifre_vektor.json", import.meta.url), JSON.stringify({
  sir: hex(sir), istemci_pub: hex(istemciPub), ofis_pub: hex(ofisPub), istemci_gizli: hex(istemciGizli),
  c2o: hex(c2o), o2c: hex(o2c), istemci_cerceveleri: istemciCerceveleri, ofis_cerceveleri: ofisCerceveleri,
}, null, 1));
console.log("TAMAM: vektör yazıldı (", istemciCerceveleri.length + ofisCerceveleri.length, "çerçeve )");
