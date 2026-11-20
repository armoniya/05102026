//! Sunucu üzerinden aktarmada (WS-relay) uçtan uca şifreleme (20.11.2026).
//!
//! Doğrudan bağlantıda (WebRTC) veri DTLS ile uçtan uca şifreli. P2P kurulamayınca istemci aynı signaling
//! WebSocket'ini veri kanalı olarak kullanır; çerçeveler eskiden yalnız base64'lenip sunucudan geçiyordu (sunucu
//! TLS'i açtıktan sonra içeriği okuyabiliyordu). Artık:
//!
//! 1. İstemci `relay_hello` ile tek kullanımlık P-256 açık anahtarını (`pub`, ham 65 bayt, base64) gönderir.
//! 2. Ofis kendi tek kullanımlık anahtarını üretir, `relay_ready` ile `pub` döner. İki taraf ECDH ile aynı sırrı bulur;
//!    sunucu yalnız açık anahtarları görür.
//! 3. Sırdan HKDF-SHA256 ile (tuz = istemci_pub ‖ ofis_pub, bilgi = `cokluuyap-relay-v1`) 64 bayt türetilir:
//!    ilk 32 bayt istemci→ofis, son 32 bayt ofis→istemci AES-256-GCM anahtarı.
//! 4. Her çerçeve `{"type":"relay","e": base64(şifreli ‖ etiket)}`. Nonce = 4 sıfır bayt ‖ 8 bayt (büyük uçlu) sayaç;
//!    her yönün sayacı 0'dan başlar ve her çerçevede bir artar. Değiştirilen, tekrar gönderilen, atlanan ya da sırası
//!    bozulan çerçeve çözülemez → bağlantı kapatılır.
//!
//! Güvenlik seviyesi WebRTC ile aynı: sunucu trafiği pasif olarak okuyamaz; anahtarlar bağlantı başınadır, diske
//! yazılmaz (ileriye dönük gizlilik). Kötü niyetli (etkin) bir sunucu araya girebilir — WebRTC'de de parmak izleri
//! sunucudan geçer. Tarayıcı karşılığı: `webapp/js/relay_sifre.js` (aynı test vektörü iki tarafta da sınanır).

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use hmac::{Hmac, Mac};
use p256::ecdh::EphemeralSecret;
use p256::{EncodedPoint, PublicKey};
use sha2::Sha256;

pub const BILGI: &[u8] = b"cokluuyap-relay-v1";

type HmacSha256 = Hmac<Sha256>;

/// HKDF-SHA256 (RFC 5869): 64 bayt.
fn hkdf64(sir: &[u8], tuz: &[u8], bilgi: &[u8]) -> [u8; 64] {
    let mut m = <HmacSha256 as Mac>::new_from_slice(tuz).expect("HMAC her anahtar uzunluğunu kabul eder");
    m.update(sir);
    let prk = m.finalize().into_bytes();
    let mut out = [0u8; 64];
    let mut onceki: Vec<u8> = Vec::new();
    for (i, parca) in out.chunks_mut(32).enumerate() {
        let mut m = <HmacSha256 as Mac>::new_from_slice(&prk).expect("HMAC");
        m.update(&onceki);
        m.update(bilgi);
        m.update(&[(i + 1) as u8]);
        let t = m.finalize().into_bytes();
        parca.copy_from_slice(&t);
        onceki = t.to_vec();
    }
    out
}

fn nonce(sayac: u64) -> [u8; 12] {
    let mut n = [0u8; 12];
    n[4..].copy_from_slice(&sayac.to_be_bytes());
    n
}

/// Bir yönün şifreleyicisi (gönderen ya da alan). Sayaç her çerçevede bir artar.
pub struct Yon {
    sifre: Aes256Gcm,
    sayac: u64,
}

impl Yon {
    fn yeni(anahtar: &[u8]) -> Yon {
        Yon { sifre: Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(anahtar)), sayac: 0 }
    }
    pub fn sifrele(&mut self, acik: &[u8]) -> Vec<u8> {
        let n = nonce(self.sayac);
        self.sayac += 1;
        self.sifre.encrypt(Nonce::from_slice(&n), acik).expect("AES-GCM şifreleme bellek dışında hata vermez")
    }
    /// Çözülemezse (değiştirilmiş, tekrar edilmiş, sırası bozulmuş) hata; sayaç ilerlemez.
    pub fn coz(&mut self, sifreli: &[u8]) -> Result<Vec<u8>, String> {
        let n = nonce(self.sayac);
        let acik = self.sifre.decrypt(Nonce::from_slice(&n), sifreli).map_err(|_| "aktarma çerçevesi doğrulanamadı".to_string())?;
        self.sayac += 1;
        Ok(acik)
    }
}

/// Paylaşılan sırdan iki yönün anahtarları: (istemci→ofis, ofis→istemci).
pub fn anahtarlar(sir: &[u8], istemci_pub: &[u8], ofis_pub: &[u8]) -> ([u8; 32], [u8; 32]) {
    let tuz = [istemci_pub, ofis_pub].concat();
    let k = hkdf64(sir, &tuz, BILGI);
    let (mut a, mut b) = ([0u8; 32], [0u8; 32]);
    a.copy_from_slice(&k[..32]);
    b.copy_from_slice(&k[32..]);
    (a, b)
}

/// Ofis tarafı el sıkışma: istemcinin ham P-256 açık anahtarı → (ofisin ham açık anahtarı, gönderen = ofis→istemci,
/// alan = istemci→ofis).
pub fn ofis_el_sikis(istemci_pub: &[u8]) -> Result<(Vec<u8>, Yon, Yon), String> {
    let karsi = PublicKey::from_sec1_bytes(istemci_pub).map_err(|_| "istemcinin açık anahtarı geçersiz".to_string())?;
    let gizli = EphemeralSecret::random(&mut rand::rngs::OsRng);
    let ofis_pub = EncodedPoint::from(gizli.public_key()).as_bytes().to_vec();
    let sir = gizli.diffie_hellman(&karsi);
    let istemci_ham = EncodedPoint::from(karsi).as_bytes().to_vec();
    let (c2o, o2c) = anahtarlar(sir.raw_secret_bytes(), &istemci_ham, &ofis_pub);
    Ok((ofis_pub, Yon::yeni(&o2c), Yon::yeni(&c2o)))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// İstemci tarafının Rust benzeri (yalnız test): (ham açık anahtar, ofis cevabını işleyen kapanış).
    fn istemci() -> (Vec<u8>, EphemeralSecret) {
        let g = EphemeralSecret::random(&mut rand::rngs::OsRng);
        (EncodedPoint::from(g.public_key()).as_bytes().to_vec(), g)
    }

    #[test]
    fn uctan_uca_gidip_gelir() {
        let (ipub, igizli) = istemci();
        let (opub, mut ofis_gonder, mut ofis_al) = ofis_el_sikis(&ipub).unwrap();
        let sir = igizli.diffie_hellman(&PublicKey::from_sec1_bytes(&opub).unwrap());
        let (c2o, o2c) = anahtarlar(sir.raw_secret_bytes(), &ipub, &opub);
        let (mut ist_gonder, mut ist_al) = (Yon::yeni(&c2o), Yon::yeni(&o2c));
        for i in 0..3u8 {
            let c = ist_gonder.sifrele(&[i; 100]);
            assert_ne!(&c[..100], &[i; 100][..], "açık metin görünmez");
            assert_eq!(ofis_al.coz(&c).unwrap(), vec![i; 100]);
            let c = ofis_gonder.sifrele(b"cevap");
            assert_eq!(ist_al.coz(&c).unwrap(), b"cevap");
        }
    }

    #[test]
    fn bozuk_tekrar_ve_sira_reddedilir() {
        let (ipub, igizli) = istemci();
        let (opub, _, mut ofis_al) = ofis_el_sikis(&ipub).unwrap();
        let sir = igizli.diffie_hellman(&PublicKey::from_sec1_bytes(&opub).unwrap());
        let (c2o, _) = anahtarlar(sir.raw_secret_bytes(), &ipub, &opub);
        let mut g = Yon::yeni(&c2o);
        let (a, b) = (g.sifrele(b"bir"), g.sifrele(b"iki"));
        assert!(ofis_al.coz(&b).is_err(), "sırası bozulan (atlanan) çerçeve");
        let mut bozuk = a.clone();
        bozuk[0] ^= 1;
        assert!(ofis_al.coz(&bozuk).is_err(), "değiştirilen çerçeve");
        assert_eq!(ofis_al.coz(&a).unwrap(), b"bir", "hata sayaç ilerletmez");
        assert!(ofis_al.coz(&a).is_err(), "tekrar gönderilen çerçeve");
        assert_eq!(ofis_al.coz(&b).unwrap(), b"iki");
        assert!(ofis_el_sikis(&[4u8; 65]).is_err(), "geçersiz açık anahtar");
    }

    /// Tarayıcı (WebCrypto) ile aynı sonuç: `araclar/relay_sifre_vektor.mjs` ile üretilmiş vektör.
    #[test]
    fn webcrypto_test_vektoru() {
        let h = |s: &str| hex::decode(s).unwrap();
        let v: serde_json::Value = serde_json::from_str(include_str!("../araclar/relay_sifre_vektor.json")).unwrap();
        let (c2o, o2c) = anahtarlar(&h(v["sir"].as_str().unwrap()), &h(v["istemci_pub"].as_str().unwrap()), &h(v["ofis_pub"].as_str().unwrap()));
        assert_eq!(hex::encode(c2o), v["c2o"].as_str().unwrap());
        assert_eq!(hex::encode(o2c), v["o2c"].as_str().unwrap());
        let mut al = Yon::yeni(&c2o);
        for c in v["istemci_cerceveleri"].as_array().unwrap() {
            assert_eq!(al.coz(&h(c["sifreli"].as_str().unwrap())).unwrap(), c["acik"].as_str().unwrap().as_bytes());
        }
        let mut gonder = Yon::yeni(&o2c);
        for c in v["ofis_cerceveleri"].as_array().unwrap() {
            assert_eq!(hex::encode(gonder.sifrele(c["acik"].as_str().unwrap().as_bytes())), c["sifreli"].as_str().unwrap());
        }
        // ECDH: tarayıcının iki anahtarıyla bulduğu sır, Rust'ın aynı gizli anahtarlarla bulduğuyla aynı
        let gizli = p256::SecretKey::from_slice(&h(v["istemci_gizli"].as_str().unwrap())).unwrap();
        let karsi = PublicKey::from_sec1_bytes(&h(v["ofis_pub"].as_str().unwrap())).unwrap();
        let sir = p256::ecdh::diffie_hellman(gizli.to_nonzero_scalar(), karsi.as_affine());
        assert_eq!(hex::encode(sir.raw_secret_bytes()), v["sir"].as_str().unwrap());
    }
}
