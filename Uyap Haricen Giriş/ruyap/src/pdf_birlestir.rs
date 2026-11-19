//! PDF birleştirici (dış kütüphanesiz). Tüm belge indirmede (evrak_toplu) her belgenin sayfalarını sırayla tek
//! PDF'te toplar. Her girdi PDF ayrıştırılır, sayfalardan erişilebilen nesneler yeni numaralarla kopyalanır,
//! sayfa ağacından miras kalan özellikler (Resources, MediaBox, CropBox, Rotate) sayfaya yazılır.
//! Okunamayan / şifreli belgenin yerine açıklama sayfası konur; birleştirme yarıda kalmaz.

use std::collections::{HashMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug)]
pub enum Obj {
    Null,
    Bool(bool),
    Num(Vec<u8>),
    Name(Vec<u8>),
    Str(Vec<u8>), // ham: parantezler dahil
    Hex(Vec<u8>), // ham: < > dahil
    Arr(Vec<Obj>),
    Dict(Vec<(Vec<u8>, Obj)>),
    Ref(u32, u16),
    Stream(Vec<(Vec<u8>, Obj)>, Vec<u8>),
}

impl Obj {
    fn dict(&self) -> Option<&Vec<(Vec<u8>, Obj)>> {
        match self {
            Obj::Dict(d) | Obj::Stream(d, _) => Some(d),
            _ => None,
        }
    }
    fn get(&self, k: &[u8]) -> Option<&Obj> {
        self.dict()?.iter().find(|(a, _)| a == k).map(|(_, v)| v)
    }
    fn name_is(&self, k: &[u8], v: &[u8]) -> bool {
        matches!(self.get(k), Some(Obj::Name(n)) if n == v)
    }
    fn as_int(&self) -> Option<i64> {
        match self {
            Obj::Num(n) => std::str::from_utf8(n).ok()?.split('.').next()?.parse().ok(),
            _ => None,
        }
    }
}

fn bosluk(c: u8) -> bool {
    matches!(c, b' ' | b'\t' | b'\r' | b'\n' | b'\x0c' | b'\0')
}
fn ayirac(c: u8) -> bool {
    bosluk(c) || matches!(c, b'(' | b')' | b'<' | b'>' | b'[' | b']' | b'{' | b'}' | b'/' | b'%')
}

struct Lex<'a> {
    b: &'a [u8],
    p: usize,
}

impl<'a> Lex<'a> {
    fn ws(&mut self) {
        while self.p < self.b.len() {
            let c = self.b[self.p];
            if bosluk(c) {
                self.p += 1;
            } else if c == b'%' {
                while self.p < self.b.len() && self.b[self.p] != b'\n' && self.b[self.p] != b'\r' {
                    self.p += 1;
                }
            } else {
                break;
            }
        }
    }
    fn kelime(&mut self) -> &'a [u8] {
        let s = self.p;
        while self.p < self.b.len() && !ayirac(self.b[self.p]) {
            self.p += 1;
        }
        &self.b[s..self.p]
    }
    fn tamsayi_mi(t: &[u8]) -> bool {
        !t.is_empty() && t.iter().all(|c| c.is_ascii_digit())
    }
    fn nesne(&mut self, derinlik: u32) -> Result<Obj, String> {
        if derinlik > 200 {
            return Err("iç içe nesne çok derin".into());
        }
        self.ws();
        let b = self.b;
        if self.p >= b.len() {
            return Err("beklenmedik dosya sonu".into());
        }
        match b[self.p] {
            b'<' if b.get(self.p + 1) == Some(&b'<') => {
                self.p += 2;
                let mut d = Vec::new();
                loop {
                    self.ws();
                    if self.p >= b.len() {
                        return Err("sözlük kapanmadı".into());
                    }
                    if b[self.p] == b'>' && b.get(self.p + 1) == Some(&b'>') {
                        self.p += 2;
                        break;
                    }
                    if b[self.p] != b'/' {
                        // bozuk anahtar: bir belirteç atla
                        let _ = self.nesne(derinlik + 1)?;
                        continue;
                    }
                    self.p += 1;
                    let k = self.kelime().to_vec();
                    let v = self.nesne(derinlik + 1)?;
                    d.push((k, v));
                }
                Ok(Obj::Dict(d))
            }
            b'<' => {
                let s = self.p;
                while self.p < b.len() && b[self.p] != b'>' {
                    self.p += 1;
                }
                self.p = (self.p + 1).min(b.len());
                Ok(Obj::Hex(b[s..self.p].to_vec()))
            }
            b'(' => {
                let s = self.p;
                let mut d = 0i32;
                while self.p < b.len() {
                    match b[self.p] {
                        b'\\' => self.p += 1,
                        b'(' => d += 1,
                        b')' => {
                            d -= 1;
                            if d == 0 {
                                self.p += 1;
                                break;
                            }
                        }
                        _ => {}
                    }
                    self.p += 1;
                }
                Ok(Obj::Str(b[s..self.p.min(b.len())].to_vec()))
            }
            b'/' => {
                self.p += 1;
                Ok(Obj::Name(self.kelime().to_vec()))
            }
            b'[' => {
                self.p += 1;
                let mut a = Vec::new();
                loop {
                    self.ws();
                    if self.p >= b.len() {
                        return Err("dizi kapanmadı".into());
                    }
                    if b[self.p] == b']' {
                        self.p += 1;
                        break;
                    }
                    a.push(self.nesne(derinlik + 1)?);
                }
                Ok(Obj::Arr(a))
            }
            b')' | b'>' | b']' | b'{' | b'}' => {
                self.p += 1;
                Ok(Obj::Null)
            }
            _ => {
                let t = self.kelime();
                if t.is_empty() {
                    self.p += 1;
                    return Ok(Obj::Null);
                }
                match t {
                    b"true" => return Ok(Obj::Bool(true)),
                    b"false" => return Ok(Obj::Bool(false)),
                    b"null" => return Ok(Obj::Null),
                    _ => {}
                }
                if Self::tamsayi_mi(t) {
                    // "n g R" başvurusu mu?
                    let geri = self.p;
                    self.ws();
                    let t2 = self.kelime();
                    if Self::tamsayi_mi(t2) {
                        self.ws();
                        if self.b.get(self.p) == Some(&b'R')
                            && self.b.get(self.p + 1).map_or(true, |c| ayirac(*c))
                        {
                            self.p += 1;
                            let n = std::str::from_utf8(t).unwrap().parse::<u32>().unwrap_or(0);
                            let g = std::str::from_utf8(t2).unwrap().parse::<u16>().unwrap_or(0);
                            return Ok(Obj::Ref(n, g));
                        }
                    }
                    self.p = geri;
                }
                Ok(Obj::Num(t.to_vec()))
            }
        }
    }
}

fn ac(veri: &[u8]) -> Option<Vec<u8>> {
    let mut o = Vec::new();
    if flate2::read::ZlibDecoder::new(veri).read_to_end(&mut o).is_ok() {
        return Some(o);
    }
    o.clear();
    // Bazı dosyalarda zlib sonu bozuk: okunabilen kısım yeterli
    let mut d = flate2::read::ZlibDecoder::new(veri);
    let mut buf = [0u8; 8192];
    loop {
        match d.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => o.extend_from_slice(&buf[..n]),
            Err(_) => break,
        }
    }
    if o.is_empty() { None } else { Some(o) }
}

struct Belge {
    nesneler: HashMap<u32, Obj>,
    kok: Option<u32>,
}

fn ayristir(b: &[u8]) -> Result<Belge, String> {
    if !b.starts_with(b"%PDF") && !b[..b.len().min(1024)].windows(4).any(|w| w == b"%PDF") {
        return Err("PDF değil".into());
    }
    let re = regex::bytes::Regex::new(r"(\d{1,10})[ \t\r\n\x0c\x00]+(\d{1,5})[ \t\r\n\x0c\x00]+obj\b").unwrap();
    // 1) nesne konumları (aynı numara birden çoksa sonuncusu: artımlı güncelleme)
    let mut konumlar: Vec<(u32, usize, usize)> = Vec::new(); // (no, eşleşme başı, gövde başı)
    for m in re.captures_iter(b) {
        let tum = m.get(0).unwrap();
        if tum.start() > 0 && !ayirac(b[tum.start() - 1]) {
            continue;
        }
        let no: u32 = std::str::from_utf8(&m[1]).unwrap().parse().unwrap_or(0);
        konumlar.push((no, tum.start(), tum.end()));
    }
    let konum_haritasi: HashMap<u32, usize> = konumlar.iter().map(|(n, _, g)| (*n, *g)).collect();

    let dogrudan_tamsayi = |no: u32| -> Option<usize> {
        let g = *konum_haritasi.get(&no)?;
        let mut l = Lex { b, p: g };
        l.nesne(0).ok()?.as_int().map(|v| v.max(0) as usize)
    };

    let mut nesneler: HashMap<u32, Obj> = HashMap::new();
    let mut nesne_akislari: Vec<u32> = Vec::new();
    let mut xref_kokleri: Vec<u32> = Vec::new();
    let mut son_bitis = 0usize;
    for (no, bas, govde) in &konumlar {
        if *bas < son_bitis {
            continue; // bir önceki akışın verisi içinde kalan sahte eşleşme
        }
        let mut l = Lex { b, p: *govde };
        let Ok(o) = l.nesne(0) else { continue };
        let mut l2 = Lex { b, p: l.p };
        l2.ws();
        let mut o = o;
        if b[l2.p..].starts_with(b"stream") {
            let mut s = l2.p + 6;
            if b.get(s) == Some(&b'\r') {
                s += 1;
            }
            if b.get(s) == Some(&b'\n') {
                s += 1;
            }
            let uzunluk = match o.get(b"Length") {
                Some(Obj::Ref(n, _)) => dogrudan_tamsayi(*n),
                Some(v) => v.as_int().map(|v| v.max(0) as usize),
                None => None,
            };
            let mut bitis = None;
            if let Some(u) = uzunluk {
                if s + u <= b.len() {
                    let mut k = s + u;
                    while k < b.len() && bosluk(b[k]) {
                        k += 1;
                    }
                    if b[k..].starts_with(b"endstream") {
                        bitis = Some((s + u, k + 9));
                    }
                }
            }
            if bitis.is_none() {
                if let Some(i) = b[s..].windows(9).position(|w| w == b"endstream") {
                    let mut e = s + i;
                    if e > s && b[e - 1] == b'\n' {
                        e -= 1;
                    }
                    if e > s && b[e - 1] == b'\r' {
                        e -= 1;
                    }
                    bitis = Some((e, s + i + 9));
                }
            }
            let Some((veri_son, akis_son)) = bitis else { continue };
            let d = match o {
                Obj::Dict(d) => d,
                _ => Vec::new(),
            };
            o = Obj::Stream(d, b[s..veri_son].to_vec());
            son_bitis = akis_son;
        } else {
            son_bitis = l.p;
        }
        if o.name_is(b"Type", b"ObjStm") {
            nesne_akislari.push(*no);
        }
        if o.name_is(b"Type", b"XRef") {
            if let Some(Obj::Ref(r, _)) = o.get(b"Root") {
                xref_kokleri.push(*r);
            }
            if o.get(b"Encrypt").is_some() {
                return Err("şifreli PDF".into());
            }
        }
        nesneler.insert(*no, o);
    }

    // 2) nesne akışları (sıkıştırılmış nesneler); düz tanımı olan nesne öncelikli
    for no in nesne_akislari {
        let Some(Obj::Stream(d, veri)) = nesneler.get(&no).cloned() else { continue };
        let st = Obj::Stream(d.clone(), Vec::new());
        let filtre_flate = matches!(st.get(b"Filter"), Some(Obj::Name(n)) if n == b"FlateDecode")
            || matches!(st.get(b"Filter"), Some(Obj::Arr(a)) if a.len() == 1 && matches!(&a[0], Obj::Name(n) if n == b"FlateDecode"));
        let acik = if st.get(b"Filter").is_none() { Some(veri) } else if filtre_flate { ac(&veri) } else { None };
        let Some(acik) = acik else { continue };
        let n = st.get(b"N").and_then(|v| v.as_int()).unwrap_or(0).max(0) as usize;
        let ilk = st.get(b"First").and_then(|v| v.as_int()).unwrap_or(0).max(0) as usize;
        let mut l = Lex { b: &acik, p: 0 };
        let mut cift = Vec::new();
        for _ in 0..n.min(100_000) {
            let (Ok(a), Ok(o)) = (l.nesne(0), l.nesne(0)) else { break };
            if let (Some(a), Some(o)) = (a.as_int(), o.as_int()) {
                cift.push((a as u32, o.max(0) as usize));
            }
        }
        for (onum, ofs) in cift {
            if konum_haritasi.contains_key(&onum) && nesneler.contains_key(&onum) {
                continue;
            }
            if ilk + ofs >= acik.len() {
                continue;
            }
            let mut l = Lex { b: &acik, p: ilk + ofs };
            if let Ok(o) = l.nesne(0) {
                nesneler.insert(onum, o);
            }
        }
    }

    // 3) kök: son "trailer" sözlüğü ya da XRef akışı
    let mut kok = None;
    let mut ara = b.len();
    while let Some(i) = b[..ara].windows(7).rposition(|w| w == b"trailer") {
        let mut l = Lex { b, p: i + 7 };
        if let Ok(t) = l.nesne(0) {
            if t.get(b"Encrypt").is_some() {
                return Err("şifreli PDF".into());
            }
            if let Some(Obj::Ref(r, _)) = t.get(b"Root") {
                kok = Some(*r);
                break;
            }
        }
        ara = i;
    }
    if kok.is_none() {
        kok = xref_kokleri.last().copied();
    }
    if kok.map_or(true, |k| !nesneler.contains_key(&k)) {
        // son çare: /Type /Catalog nesnesi
        kok = nesneler
            .iter()
            .filter(|(_, o)| o.name_is(b"Type", b"Catalog"))
            .map(|(n, _)| *n)
            .max();
    }
    Ok(Belge { nesneler, kok })
}

const MIRAS: [&[u8]; 4] = [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

impl Belge {
    fn coz<'a>(&'a self, o: &'a Obj) -> &'a Obj {
        let mut o = o;
        for _ in 0..8 {
            match o {
                Obj::Ref(n, _) => match self.nesneler.get(n) {
                    Some(x) => o = x,
                    None => return &Obj::Null,
                },
                _ => return o,
            }
        }
        &Obj::Null
    }
    /// Sayfa nesneleri (numara) ve her birine miras kalan özellikler.
    fn sayfalar(&self) -> Vec<(u32, Vec<(Vec<u8>, Obj)>)> {
        let mut out = Vec::new();
        let Some(kok) = self.kok.and_then(|k| self.nesneler.get(&k)) else { return out };
        let Some(Obj::Ref(p, _)) = kok.get(b"Pages") else { return out };
        let mut gorulen = HashSet::new();
        self.gez(*p, Vec::new(), &mut out, &mut gorulen, 0);
        out
    }
    fn gez(&self, no: u32, miras: Vec<(Vec<u8>, Obj)>, out: &mut Vec<(u32, Vec<(Vec<u8>, Obj)>)>, gorulen: &mut HashSet<u32>, d: u32) {
        if d > 64 || !gorulen.insert(no) {
            return;
        }
        let Some(o) = self.nesneler.get(&no) else { return };
        let mut m = miras;
        for k in MIRAS {
            if let Some(v) = o.get(k) {
                m.retain(|(a, _)| a != k);
                m.push((k.to_vec(), v.clone()));
            }
        }
        let kids = o.get(b"Kids").map(|k| self.coz(k));
        if let Some(Obj::Arr(a)) = kids {
            if !o.name_is(b"Type", b"Page") {
                for c in a {
                    if let Obj::Ref(n, _) = c {
                        self.gez(*n, m.clone(), out, gorulen, d + 1);
                    }
                }
                return;
            }
        }
        out.push((no, m));
    }
}

struct Yazici<W: Write> {
    w: W,
    konum: usize,
    ofsetler: HashMap<u32, usize>,
    sonraki: u32,
}

impl<W: Write> Yazici<W> {
    fn yaz(&mut self, b: &[u8]) -> std::io::Result<()> {
        self.w.write_all(b)?;
        self.konum += b.len();
        Ok(())
    }
    fn nesne_yaz(&mut self, no: u32, o: &Obj) -> std::io::Result<()> {
        self.ofsetler.insert(no, self.konum);
        let mut s = format!("{} 0 obj\n", no).into_bytes();
        seri(o, &mut s);
        if let Obj::Stream(_, veri) = o {
            s.extend_from_slice(b"\nstream\n");
            self.yaz(&s)?;
            self.yaz(veri)?;
            self.yaz(b"\nendstream\nendobj\n")
        } else {
            s.extend_from_slice(b"\nendobj\n");
            self.yaz(&s)
        }
    }
}

fn seri_sozluk(d: &[(Vec<u8>, Obj)], s: &mut Vec<u8>) {
    s.extend_from_slice(b"<<");
    for (k, v) in d {
        s.push(b'/');
        s.extend_from_slice(k);
        s.push(b' ');
        seri(v, s);
        s.push(b' ');
    }
    s.extend_from_slice(b">>");
}

fn seri(o: &Obj, s: &mut Vec<u8>) {
    match o {
        Obj::Null => s.extend_from_slice(b"null"),
        Obj::Bool(b) => s.extend_from_slice(if *b { b"true" } else { b"false" }),
        Obj::Num(n) => s.extend_from_slice(n),
        Obj::Name(n) => {
            s.push(b'/');
            s.extend_from_slice(n);
        }
        Obj::Str(v) | Obj::Hex(v) => s.extend_from_slice(v),
        Obj::Arr(a) => {
            s.push(b'[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    s.push(b' ');
                }
                seri(x, s);
            }
            s.push(b']');
        }
        Obj::Dict(d) => seri_sozluk(d, s),
        Obj::Ref(n, g) => s.extend_from_slice(format!("{} {} R", n, g).as_bytes()),
        Obj::Stream(d, veri) => {
            let mut d2: Vec<(Vec<u8>, Obj)> = d.iter().filter(|(k, _)| k != b"Length").cloned().collect();
            d2.push((b"Length".to_vec(), Obj::Num(veri.len().to_string().into_bytes())));
            seri_sozluk(&d2, s);
        }
    }
}

/// Bir belgenin sayfalarını yazar; sayfa nesnelerinin yeni numaralarını döner.
fn belge_kopyala<W: Write>(bel: &Belge, y: &mut Yazici<W>, sayfa_koku: u32) -> std::io::Result<Vec<u32>> {
    let sayfalar = bel.sayfalar();
    let mut harita: HashMap<u32, u32> = HashMap::new();
    let mut kuyruk: Vec<u32> = Vec::new();
    let sayfa_kumesi: HashSet<u32> = sayfalar.iter().map(|(n, _)| *n).collect();

    // yeniden numaralama: erişilen her nesneye yeni numara; katalog / sayfa ağacı düğümleri null olur
    fn cevir(o: &Obj, bel: &Belge, harita: &mut HashMap<u32, u32>, kuyruk: &mut Vec<u32>, sonraki: &mut u32, sayfalar: &HashSet<u32>) -> Obj {
        match o {
            Obj::Ref(n, _) => {
                if let Some(y) = harita.get(n) {
                    return Obj::Ref(*y, 0);
                }
                let Some(h) = bel.nesneler.get(n) else { return Obj::Null };
                if !sayfalar.contains(n) && (h.name_is(b"Type", b"Pages") || h.name_is(b"Type", b"Catalog") || h.name_is(b"Type", b"Page")) {
                    return Obj::Null; // başka sayfa ağacına sızma yok
                }
                let y = *sonraki;
                *sonraki += 1;
                harita.insert(*n, y);
                kuyruk.push(*n);
                Obj::Ref(y, 0)
            }
            Obj::Arr(a) => Obj::Arr(a.iter().map(|x| cevir(x, bel, harita, kuyruk, sonraki, sayfalar)).collect()),
            Obj::Dict(d) => Obj::Dict(d.iter().map(|(k, v)| (k.clone(), cevir(v, bel, harita, kuyruk, sonraki, sayfalar))).collect()),
            Obj::Stream(d, veri) => Obj::Stream(
                d.iter().map(|(k, v)| (k.clone(), cevir(v, bel, harita, kuyruk, sonraki, sayfalar))).collect(),
                veri.clone(),
            ),
            x => x.clone(),
        }
    }

    let mut yeni_sayfalar = Vec::new();
    for (no, _) in &sayfalar {
        let y2 = y.sonraki;
        y.sonraki += 1;
        harita.insert(*no, y2);
        yeni_sayfalar.push(y2);
    }
    for (i, (no, miras)) in sayfalar.iter().enumerate() {
        let Some(Obj::Dict(d)) = bel.nesneler.get(no) else {
            // sayfa sözlük değilse boş A4 sayfa
            let bos = Obj::Dict(vec![
                (b"Type".to_vec(), Obj::Name(b"Page".to_vec())),
                (b"Parent".to_vec(), Obj::Ref(sayfa_koku, 0)),
                (b"MediaBox".to_vec(), Obj::Arr(["0", "0", "595", "842"].iter().map(|v| Obj::Num(v.as_bytes().to_vec())).collect())),
            ]);
            y.nesne_yaz(yeni_sayfalar[i], &bos)?;
            continue;
        };
        let mut d2: Vec<(Vec<u8>, Obj)> = d.iter().filter(|(k, _)| k != b"Parent").cloned().collect();
        for (k, v) in miras {
            if !d2.iter().any(|(a, _)| a == k) {
                d2.push((k.clone(), v.clone()));
            }
        }
        if !d2.iter().any(|(a, _)| a == b"MediaBox") {
            d2.push((b"MediaBox".to_vec(), Obj::Arr(["0", "0", "595", "842"].iter().map(|v| Obj::Num(v.as_bytes().to_vec())).collect())));
        }
        let mut sonraki = y.sonraki;
        let mut cevrilmis = match cevir(&Obj::Dict(d2), bel, &mut harita, &mut kuyruk, &mut sonraki, &sayfa_kumesi) {
            Obj::Dict(d) => d,
            _ => Vec::new(),
        };
        y.sonraki = sonraki;
        cevrilmis.push((b"Parent".to_vec(), Obj::Ref(sayfa_koku, 0)));
        y.nesne_yaz(yeni_sayfalar[i], &Obj::Dict(cevrilmis))?;
        // bu sayfanın eriştiği nesneleri hemen yaz (bellek az kalsın)
        while let Some(eski) = kuyruk.pop() {
            let Some(o) = bel.nesneler.get(&eski) else { continue };
            let mut sonraki = y.sonraki;
            let c = cevir(o, bel, &mut harita, &mut kuyruk, &mut sonraki, &sayfa_kumesi);
            y.sonraki = sonraki;
            let yn = harita[&eski];
            y.nesne_yaz(yn, &c)?;
        }
    }
    Ok(yeni_sayfalar)
}

/// PDF'leri sırayla birleştirir. `aciklama(yol, hata)` okunamayan belge yerine konacak tek sayfalık PDF'i üretir.
/// Dönen değer: (toplam sayfa, okunamayan belge sayısı).
pub fn birlestir(yollar: &[PathBuf], hedef: &Path, aciklama: &dyn Fn(&Path, &str) -> Vec<u8>) -> Result<(usize, usize), String> {
    let gecici = hedef.with_extension("pdf.yaziliyor");
    let f = std::fs::File::create(&gecici).map_err(|e| format!("Birleşik PDF açılamadı: {e}"))?;
    let mut y = Yazici { w: std::io::BufWriter::new(f), konum: 0, ofsetler: HashMap::new(), sonraki: 3 };
    let sonuc = (|| -> std::io::Result<(usize, usize)> {
        y.yaz(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")?;
        let mut tum_sayfalar = Vec::new();
        let mut okunamayan = 0usize;
        for yol in yollar {
            let bel = std::fs::read(yol)
                .map_err(|e| e.to_string())
                .and_then(|b| ayristir(&b))
                .and_then(|bel| if bel.sayfalar().is_empty() { Err("sayfa bulunamadı".to_string()) } else { Ok(bel) });
            let bel = match bel {
                Ok(b) => b,
                Err(e) => {
                    okunamayan += 1;
                    match ayristir(&aciklama(yol, &e)) {
                        Ok(b) => b,
                        Err(_) => continue,
                    }
                }
            };
            tum_sayfalar.extend(belge_kopyala(&bel, &mut y, 2)?);
        }
        let kids = Obj::Arr(tum_sayfalar.iter().map(|n| Obj::Ref(*n, 0)).collect());
        y.nesne_yaz(2, &Obj::Dict(vec![
            (b"Type".to_vec(), Obj::Name(b"Pages".to_vec())),
            (b"Kids".to_vec(), kids),
            (b"Count".to_vec(), Obj::Num(tum_sayfalar.len().to_string().into_bytes())),
        ]))?;
        y.nesne_yaz(1, &Obj::Dict(vec![
            (b"Type".to_vec(), Obj::Name(b"Catalog".to_vec())),
            (b"Pages".to_vec(), Obj::Ref(2, 0)),
        ]))?;
        let xref = y.konum;
        let boyut = y.sonraki;
        let mut s = format!("xref\n0 {}\n0000000000 65535 f \n", boyut).into_bytes();
        for n in 1..boyut {
            match y.ofsetler.get(&n) {
                Some(o) => s.extend_from_slice(format!("{:010} 00000 n \n", o).as_bytes()),
                None => s.extend_from_slice(b"0000000000 65535 f \n"),
            }
        }
        s.extend_from_slice(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n", boyut, xref).as_bytes());
        y.yaz(&s)?;
        y.w.flush()?;
        Ok((tum_sayfalar.len(), okunamayan))
    })();
    drop(y);
    match sonuc {
        Ok(r) => {
            let _ = std::fs::remove_file(hedef);
            std::fs::rename(&gecici, hedef).map_err(|e| format!("Birleşik PDF yazılamadı: {e}"))?;
            Ok(r)
        }
        Err(e) => {
            let _ = std::fs::remove_file(&gecici);
            Err(format!("Birleşik PDF yazılamadı: {e}"))
        }
    }
}

/// PDF'in sayfa sayısı (ayrıştırılamazsa None).
pub fn sayfa_sayisi(b: &[u8]) -> Option<usize> {
    ayristir(b).ok().map(|d| d.sayfalar().len())
}
