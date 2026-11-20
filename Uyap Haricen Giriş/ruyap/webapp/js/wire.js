// p2p_wire.py'nin tarayıcı (JS) karşılığı — ofis ajanıyla BİREBİR aynı tel protokolü.
//
// Her çerçeve = [4 bayt başlık uzunluğu (big-endian)] + [JSON başlık (utf-8)] + [ham parça].
// Başlık: {id, kind, seq, total, meta?(yalnızca seq==0)}; meta._comp sıkıştırma bayrağı.
//
// Tarayıcı -> ofis isteklerini SIKIŞTIRMAYIZ (genelde küçük/gövdesiz); meta._comp=false
// gönderir, ofis Reassembler'ı bunu olduğu gibi açar. Ofis -> tarayıcı cevapları zlib ile
// sıkıştırılmış gelir; onları DecompressionStream('deflate') ile açarız (RFC 1950 = zlib).

export const CHUNK = 48 * 1024;

const _enc = new TextEncoder();
const _dec = new TextDecoder();

export function* encodeFrames(msgId, kind, meta, body) {
  body = body || new Uint8Array(0);
  const total = Math.max(1, Math.ceil(body.length / CHUNK));
  for (let seq = 0; seq < total; seq++) {
    const chunk = body.subarray(seq * CHUNK, (seq + 1) * CHUNK);
    const header = { id: msgId, kind, seq, total };
    if (seq === 0) {
      const m = meta ? Object.assign({}, meta) : {};
      m._comp = false;               // tarayıcı tarafı sıkıştırma yapmaz
      header.meta = m;
    }
    const headerBytes = _enc.encode(JSON.stringify(header));
    const frame = new Uint8Array(4 + headerBytes.length + chunk.length);
    new DataView(frame.buffer).setUint32(0, headerBytes.length, false); // big-endian
    frame.set(headerBytes, 4);
    frame.set(chunk, 4 + headerBytes.length);
    yield frame.buffer;
  }
}

export class Reassembler {
  constructor() {
    this._buf = new Map(); // id -> {chunks: Map<seq,Uint8Array>, total, meta, kind}
  }

  // data: ArrayBuffer (channel.binaryType = 'arraybuffer'). Mesaj tamamlandıysa kayıt,
  // değilse null döndürür. Kayıt: {id, kind, meta, body(Uint8Array), comp}.
  feed(data) {
    const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
    const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const hlen = dv.getUint32(0, false);
    const header = JSON.parse(_dec.decode(bytes.subarray(4, 4 + hlen)));
    // Geçici tampon yeniden kullanılabileceği için parçayı kopyalayarak sakla.
    const chunk = Uint8Array.from(bytes.subarray(4 + hlen));
    const mid = header.id;
    let entry = this._buf.get(mid);
    if (!entry) {
      entry = { chunks: new Map(), total: header.total, meta: null, kind: header.kind };
      this._buf.set(mid, entry);
    }
    entry.chunks.set(header.seq, chunk);
    if (header.seq === 0) entry.meta = header.meta || null;
    if (entry.chunks.size >= entry.total) {
      this._buf.delete(mid);
      let totalLen = 0;
      for (let i = 0; i < entry.total; i++) totalLen += entry.chunks.get(i).length;
      const body = new Uint8Array(totalLen);
      let off = 0;
      for (let i = 0; i < entry.total; i++) {
        const c = entry.chunks.get(i);
        body.set(c, off);
        off += c.length;
      }
      return {
        id: mid, kind: entry.kind, meta: entry.meta, body,
        comp: !!(entry.meta && entry.meta._comp),
      };
    }
    return null;
  }
}

export async function inflate(bytes) {
  if (typeof DecompressionStream === "undefined") {
    throw new Error("Tarayıcı DecompressionStream desteklemiyor (zlib açılamıyor).");
  }
  const ds = new DecompressionStream("deflate"); // zlib (RFC 1950) = Python zlib.compress
  const stream = new Blob([bytes]).stream().pipeThrough(ds);
  const buf = await new Response(stream).arrayBuffer();
  return new Uint8Array(buf);
}

// Tamamlanmış kaydı, gerekiyorsa açarak gövdesini hazır hale getirir.
export async function finalize(rec) {
  if (rec.comp) rec.body = await inflate(rec.body);
  return rec;
}

// ─────────────────────────────────────────────────────────────────────────────────────────
// Sunucu üzerinden aktarmada (WS-relay) uçtan uca şifreleme (20.11.2026) — src/relay_sifre.rs'in karşılığı.
// İstemci tek kullanımlık P-256 anahtarını relay_hello ile gönderir, ofis relay_ready ile kendininkini döner;
// ECDH sırrından HKDF-SHA256 (tuz = istemci_pub ‖ ofis_pub, bilgi "cokluuyap-relay-v1") ile iki AES-256-GCM
// anahtarı (istemci→ofis, ofis→istemci). Nonce = 4 sıfır bayt ‖ 8 bayt sayaç; her yönde 0'dan başlar.
// Sunucu yalnız açık anahtarları görür (WebRTC ile aynı seviye). Test vektörü: araclar/relay_sifre_vektor.mjs.
// ─────────────────────────────────────────────────────────────────────────────────────────
const RELAY_BILGI = new TextEncoder().encode("cokluuyap-relay-v1");

function relayNonce(sayac) {
  const n = new Uint8Array(12);
  new DataView(n.buffer).setBigUint64(4, BigInt(sayac));
  return n;
}

// Paylaşılan sırdan [istemci→ofis, ofis→istemci] ham anahtarları.
export async function relayAnahtarlari(sir, istemciPub, ofisPub) {
  const subtle = globalThis.crypto.subtle;
  const tuz = new Uint8Array(istemciPub.length + ofisPub.length);
  tuz.set(istemciPub);
  tuz.set(ofisPub, istemciPub.length);
  const ham = await subtle.importKey("raw", sir, "HKDF", false, ["deriveBits"]);
  const bit = new Uint8Array(await subtle.deriveBits({ name: "HKDF", hash: "SHA-256", salt: tuz, info: RELAY_BILGI }, ham, 512));
  return [bit.slice(0, 32), bit.slice(32)];
}

// Bir yönün şifreleyicisi. Sayaç çağrı anında (eşzamanlı) ilerler: sifrele() sonuçları ÇAĞRI SIRASIYLA gönderilmeli,
// coz() çağrıları sırayla (bir öncekini bekleyerek) yapılmalı.
export class RelayYon {
  static async yeni(hamAnahtar) {
    const y = new RelayYon();
    y.anahtar = await globalThis.crypto.subtle.importKey("raw", hamAnahtar, "AES-GCM", false, ["encrypt", "decrypt"]);
    y.sayac = 0;
    return y;
  }
  async sifrele(acik) {
    const iv = relayNonce(this.sayac++);
    return new Uint8Array(await globalThis.crypto.subtle.encrypt({ name: "AES-GCM", iv }, this.anahtar, acik));
  }
  async coz(sifreli) {
    const iv = relayNonce(this.sayac);
    const acik = new Uint8Array(await globalThis.crypto.subtle.decrypt({ name: "AES-GCM", iv }, this.anahtar, sifreli));
    this.sayac++;
    return acik;
  }
}

// İstemci el sıkışması: { pub (ham 65 bayt), tamamla(ofisPub) → { gonder, al } }. Gizli anahtar dışa aktarılamaz.
export async function relayIstemciAnahtari() {
  const subtle = globalThis.crypto.subtle;
  const cift = await subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, false, ["deriveBits"]);
  const pub = new Uint8Array(await subtle.exportKey("raw", cift.publicKey));
  return {
    pub,
    async tamamla(ofisPub) {
      const karsi = await subtle.importKey("raw", ofisPub, { name: "ECDH", namedCurve: "P-256" }, false, []);
      const sir = new Uint8Array(await subtle.deriveBits({ name: "ECDH", public: karsi }, cift.privateKey, 256));
      const [c2o, o2c] = await relayAnahtarlari(sir, pub, ofisPub);
      return { gonder: await RelayYon.yeni(c2o), al: await RelayYon.yeni(o2c) };
    },
  };
}
