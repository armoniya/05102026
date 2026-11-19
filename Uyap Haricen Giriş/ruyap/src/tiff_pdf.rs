//! TIFF → PDF (dış kütüphanesiz, kayıpsız). UYAP'ın taranmış evrakı çoğunlukla CCITT G4 (faks) sıkıştırmalı,
//! çok sayfalı TIFF'tir. PDF bu sıkıştırmaları doğrudan tanıdığı için görüntü verisi çözülmeden PDF'e konur
//! (CCITT G3/G4 → CCITTFaxDecode, LZW → LZWDecode, Deflate → FlateDecode, JPEG → DCTDecode). Sıkıştırmasız
//! ve PackBits veri Deflate ile sıkıştırılır. Her şerit (strip) ayrı görüntü olarak alt alta yerleşir.

use std::io::Write;

struct Okur<'a> {
    b: &'a [u8],
    le: bool,
}

impl<'a> Okur<'a> {
    fn u16(&self, o: usize) -> Option<u16> {
        let s = self.b.get(o..o + 2)?;
        Some(if self.le { u16::from_le_bytes([s[0], s[1]]) } else { u16::from_be_bytes([s[0], s[1]]) })
    }
    fn u32(&self, o: usize) -> Option<u32> {
        let s = self.b.get(o..o + 4)?;
        let a = [s[0], s[1], s[2], s[3]];
        Some(if self.le { u32::from_le_bytes(a) } else { u32::from_be_bytes(a) })
    }
}

#[derive(Default, Clone)]
struct Etiket {
    tur: u16,
    say: u32,
    deger_ofs: usize, // değerlerin başladığı yer
}

struct Ifd {
    etiketler: std::collections::HashMap<u16, Etiket>,
}

impl Ifd {
    fn degerler(&self, r: &Okur, tag: u16) -> Vec<u64> {
        let Some(e) = self.etiketler.get(&tag) else { return Vec::new() };
        let mut v = Vec::new();
        for i in 0..e.say.min(1_000_000) as usize {
            let x = match e.tur {
                1 | 2 | 6 | 7 => r.b.get(e.deger_ofs + i).map(|b| *b as u64),
                3 | 8 => r.u16(e.deger_ofs + i * 2).map(|x| x as u64),
                4 | 9 | 13 => r.u32(e.deger_ofs + i * 4).map(|x| x as u64),
                _ => None,
            };
            match x {
                Some(x) => v.push(x),
                None => break,
            }
        }
        v
    }
    fn tek(&self, r: &Okur, tag: u16, vars: u64) -> u64 {
        self.degerler(r, tag).first().copied().unwrap_or(vars)
    }
    fn oran(&self, r: &Okur, tag: u16) -> Option<f64> {
        let e = self.etiketler.get(&tag)?;
        if e.tur != 5 {
            return self.degerler(r, tag).first().map(|v| *v as f64);
        }
        let p = r.u32(e.deger_ofs)? as f64;
        let q = r.u32(e.deger_ofs + 4)? as f64;
        if q == 0.0 { None } else { Some(p / q) }
    }
    fn ham(&self, r: &'_ Okur, tag: u16) -> Option<Vec<u8>> {
        let e = self.etiketler.get(&tag)?;
        r.b.get(e.deger_ofs..e.deger_ofs + e.say as usize).map(|s| s.to_vec())
    }
}

fn ifdler(r: &Okur) -> Result<Vec<Ifd>, String> {
    let mut out = Vec::new();
    let mut ofs = r.u32(4).ok_or("TIFF başlığı kısa")? as usize;
    let mut gorulen = std::collections::HashSet::new();
    while ofs != 0 && out.len() < 5000 {
        if !gorulen.insert(ofs) {
            break;
        }
        let n = r.u16(ofs).ok_or("TIFF dizini okunamadı")? as usize;
        let mut etiketler = std::collections::HashMap::new();
        for i in 0..n {
            let g = ofs + 2 + i * 12;
            let (Some(tag), Some(tur), Some(say)) = (r.u16(g), r.u16(g + 2), r.u32(g + 4)) else { break };
            let boy = match tur {
                1 | 2 | 6 | 7 => 1,
                3 | 8 => 2,
                4 | 9 | 11 | 13 => 4,
                5 | 10 | 12 => 8,
                _ => 0,
            } * say as usize;
            let deger_ofs = if boy <= 4 { g + 8 } else { r.u32(g + 8).unwrap_or(0) as usize };
            etiketler.insert(tag, Etiket { tur, say, deger_ofs });
        }
        out.push(Ifd { etiketler });
        ofs = r.u32(ofs + 2 + n * 12).unwrap_or(0) as usize;
    }
    Ok(out)
}

fn packbits_coz(v: &[u8]) -> Vec<u8> {
    let mut o = Vec::with_capacity(v.len() * 2);
    let mut i = 0;
    while i < v.len() {
        let n = v[i] as i8;
        i += 1;
        if n >= 0 {
            let k = n as usize + 1;
            o.extend_from_slice(&v[i..(i + k).min(v.len())]);
            i += k;
        } else if n != -128 {
            if i < v.len() {
                o.extend(std::iter::repeat(v[i]).take((1 - n as i32) as usize));
            }
            i += 1;
        }
    }
    o
}

/// TIFF LZW (MSB önce, 9–12 bit, erken genişleme).
fn lzw_coz(v: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len() * 3);
    let mut sozluk: Vec<Vec<u8>> = (0..=255u8).map(|b| vec![b]).collect();
    sozluk.push(Vec::new());
    sozluk.push(Vec::new());
    let mut bit = 0usize;
    let mut gen = 9usize;
    let mut onceki: Option<Vec<u8>> = None;
    loop {
        if bit + gen > v.len() * 8 {
            break;
        }
        let mut kod = 0usize;
        for k in 0..gen {
            let b = bit + k;
            kod = (kod << 1) | ((v[b / 8] >> (7 - b % 8)) & 1) as usize;
        }
        bit += gen;
        if kod == 256 {
            sozluk.truncate(258);
            gen = 9;
            onceki = None;
            continue;
        }
        if kod == 257 {
            break;
        }
        let giris = if kod < sozluk.len() && kod != 256 && kod != 257 {
            sozluk[kod].clone()
        } else if let Some(p) = &onceki {
            let mut e = p.clone();
            e.push(p[0]);
            e
        } else {
            break;
        };
        out.extend_from_slice(&giris);
        if let Some(mut p) = onceki.take() {
            if sozluk.len() < 4096 {
                p.push(giris[0]);
                sozluk.push(p);
            }
        }
        onceki = Some(giris);
        if sozluk.len() + 1 >= (1 << gen) && gen < 12 {
            gen += 1;
        }
    }
    out
}

/// Sıkıştırmayı çözer (yalnız saydamlık katmanını atmak gerektiğinde kullanılır).
fn coz(sik: u64, ham: &[u8]) -> Result<Vec<u8>, String> {
    match sik {
        1 => Ok(ham.to_vec()),
        32773 => Ok(packbits_coz(ham)),
        5 => Ok(lzw_coz(ham)),
        8 | 32946 => {
            use std::io::Read;
            let mut o = Vec::new();
            flate2::read::ZlibDecoder::new(ham).read_to_end(&mut o).map_err(|e| format!("Deflate çözülemedi: {e}"))?;
            Ok(o)
        }
        x => Err(format!("saydam TIFF'te sıkıştırma desteklenmiyor ({})", x)),
    }
}

fn sikistir(v: &[u8]) -> Vec<u8> {
    let mut e = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
    let _ = e.write_all(v);
    e.finish().unwrap_or_default()
}

struct Goruntu {
    gen: u32,
    yuk: u32,
    sozluk: String, // /Width … dışındaki anahtarlar
    veri: Vec<u8>,
}

struct Sayfa {
    gen_pt: f64,
    yuk_pt: f64,
    seritler: Vec<Goruntu>,
}

fn sayfa_coz(r: &Okur, f: &Ifd) -> Result<Sayfa, String> {
    let gen = f.tek(r, 256, 0) as u32;
    let yuk = f.tek(r, 257, 0) as u32;
    if gen == 0 || yuk == 0 {
        return Err("TIFF boyutu yok".into());
    }
    if f.etiketler.contains_key(&322) {
        return Err("döşemeli (tiled) TIFF desteklenmiyor".into());
    }
    let sik = f.tek(r, 259, 1);
    let foto = f.tek(r, 262, 0);
    let bps = f.tek(r, 258, 1);
    let spp = f.tek(r, 277, 1);
    let dolgu = f.tek(r, 266, 1);
    let planar = f.tek(r, 284, 1);
    let tahmin = f.tek(r, 317, 1);
    let satir_serit = f.tek(r, 278, yuk as u64).max(1).min(yuk as u64) as u32;
    let ofsler = f.degerler(r, 273);
    let boylar = f.degerler(r, 279);
    if ofsler.is_empty() {
        return Err("TIFF veri konumu yok".into());
    }
    if planar == 2 && spp > 1 {
        return Err("ayrık düzlemli TIFF desteklenmiyor".into());
    }
    // çözünürlük → sayfa boyutu
    let birim = f.tek(r, 296, 2);
    let carpan = if birim == 3 { 2.54 } else { 1.0 };
    let xr = f.oran(r, 282).map(|v| v * carpan).filter(|v| *v >= 20.0 && *v <= 4800.0).unwrap_or(200.0);
    let yr = f.oran(r, 283).map(|v| v * carpan).filter(|v| *v >= 20.0 && *v <= 4800.0).unwrap_or(xr);

    let renk = match (spp, foto) {
        (1, 3) => {
            let cm = f.degerler(r, 320);
            let n = 1usize << bps.min(8);
            if cm.len() < 3 * n {
                return Err("TIFF renk tablosu eksik".into());
            }
            let mut hex = String::with_capacity(n * 6);
            for i in 0..n {
                for k in 0..3 {
                    hex.push_str(&format!("{:02x}", cm[k * n + i] >> 8));
                }
            }
            format!("[/Indexed /DeviceRGB {} <{}>]", n - 1, hex)
        }
        (1, _) | (2, 0) | (2, 1) => "/DeviceGray".to_string(),
        (3, 2) | (3, 6) | (4, 2) => "/DeviceRGB".to_string(),
        (4, 5) => "/DeviceCMYK".to_string(),
        _ => return Err(format!("TIFF renk düzeni desteklenmiyor ({} kanal, tür {})", spp, foto)),
    };
    // RGBA / gri+alfa: saydamlık katmanı atılır (veri çözülür, ilk 3 / 1 kanal Deflate ile yazılır)
    let alfa = (spp == 4 && foto == 2) || spp == 2;
    if alfa && bps != 8 {
        return Err(format!("saydam TIFF'te {} bit desteklenmiyor", bps));
    }
    // Eski tip JPEG (6): çoğunlukla dosyada tam bir JPEG akışı vardır (etiket 513/514)
    if sik == 6 {
        let jo = f.tek(r, 513, 0) as usize;
        let jl = f.tek(r, 514, 0) as usize;
        let tam = if jo > 0 && jl > 0 { r.b.get(jo..jo + jl) } else { None }
            .or_else(|| {
                let o = *ofsler.first()? as usize;
                let toplam: u64 = boylar.iter().sum();
                r.b.get(o..o + toplam as usize).filter(|v| v.starts_with(&[0xFF, 0xD8]))
            });
        let Some(j) = tam else { return Err("eski tip JPEG'li TIFF okunamadı".into()) };
        let cs = if spp == 1 { "/DeviceGray" } else { "/DeviceRGB" };
        return Ok(Sayfa {
            gen_pt: gen as f64 * 72.0 / xr,
            yuk_pt: yuk as f64 * 72.0 / yr,
            seritler: vec![Goruntu { gen, yuk, sozluk: format!("/ColorSpace {} /BitsPerComponent 8 /Filter /DCTDecode", cs), veri: j.to_vec() }],
        });
    }
    let ccitt = matches!(sik, 2 | 3 | 4);
    // WhiteIsZero (0): CCITT kodları zaten beyaz/siyah anlamlı → çevirme yok; diğer sıkıştırmalarda ters
    let ters = if ccitt { foto == 1 } else { foto == 0 && spp == 1 };
    let decode = if ters { if bps == 1 { " /Decode [1 0]" } else { " /Decode [1 0]" } } else { "" };
    let jpeg_tablo = f.ham(r, 347);

    let mut seritler = Vec::new();
    let mut kalan = yuk;
    for (i, o) in ofsler.iter().enumerate() {
        if kalan == 0 {
            break;
        }
        let satir = satir_serit.min(kalan);
        kalan -= satir;
        let boy = boylar.get(i).copied().unwrap_or((r.b.len() as u64).saturating_sub(*o)) as usize;
        let o = *o as usize;
        let Some(ham) = r.b.get(o..(o + boy).min(r.b.len())) else { return Err("TIFF verisi kesik".into()) };
        let mut ham = ham.to_vec();
        if dolgu == 2 && matches!(sik, 1 | 2 | 3 | 4) {
            for b in ham.iter_mut() {
                *b = b.reverse_bits();
            }
        }
        let tahmin_parms = |bpc: u64| {
            if tahmin == 2 {
                format!(" /DecodeParms << /Predictor 2 /Colors {} /BitsPerComponent {} /Columns {} >>", spp, bpc, gen)
            } else {
                String::new()
            }
        };
        let (filtre, veri, bpc): (String, Vec<u8>, u64) = if alfa {
            let mut v = coz(sik, &ham)?;
            let sb = gen as usize * spp as usize;
            if tahmin == 2 {
                for satir in v.chunks_mut(sb) {
                    for i in spp as usize..satir.len() {
                        satir[i] = satir[i].wrapping_add(satir[i - spp as usize]);
                    }
                }
            }
            let tut = spp as usize - 1;
            let mut o = Vec::with_capacity(v.len());
            for px in v.chunks(spp as usize) {
                if px.len() == spp as usize {
                    o.extend_from_slice(&px[..tut]);
                }
            }
            o.resize(gen as usize * satir as usize * tut, 255);
            (" /Filter /FlateDecode".into(), sikistir(&o), 8)
        } else { match sik {
            1 => (" /Filter /FlateDecode".into(), sikistir(&ham), bps),
            32773 => (" /Filter /FlateDecode".into(), sikistir(&packbits_coz(&ham)), bps),
            2 => (format!(" /Filter /CCITTFaxDecode /DecodeParms << /K 0 /EncodedByteAlign true /Columns {} /Rows {} >>", gen, satir), ham, 1),
            3 => {
                let t4 = f.tek(r, 292, 0);
                let k = if t4 & 1 != 0 { 1 } else { 0 };
                let hiza = if t4 & 4 != 0 { " /EncodedByteAlign true" } else { "" };
                (format!(" /Filter /CCITTFaxDecode /DecodeParms << /K {} /Columns {} /Rows {}{} >>", k, gen, satir, hiza), ham, 1)
            }
            4 => (format!(" /Filter /CCITTFaxDecode /DecodeParms << /K -1 /Columns {} /Rows {} >>", gen, satir), ham, 1),
            5 => (format!(" /Filter /LZWDecode{}", tahmin_parms(bps)), ham, bps),
            8 | 32946 => (format!(" /Filter /FlateDecode{}", tahmin_parms(bps)), ham, bps),
            7 => {
                let veri = match &jpeg_tablo {
                    Some(t) if t.len() > 4 && ham.len() > 2 => {
                        let mut v = t[..t.len() - 2].to_vec(); // tablolar (EOI hariç)
                        v.extend_from_slice(&ham[2..]); // şerit (SOI hariç)
                        v
                    }
                    _ => ham,
                };
                let renk_donusum = if foto == 2 { " /DecodeParms << /ColorTransform 0 >>" } else { "" };
                (format!(" /Filter /DCTDecode{}", renk_donusum), veri, 8)
            }
            x => return Err(format!("TIFF sıkıştırması desteklenmiyor ({})", x)),
        } };
        seritler.push(Goruntu {
            gen,
            yuk: satir,
            sozluk: format!("/ColorSpace {} /BitsPerComponent {}{}{}", renk, bpc, decode, filtre),
            veri,
        });
    }
    Ok(Sayfa { gen_pt: gen as f64 * 72.0 / xr, yuk_pt: yuk as f64 * 72.0 / yr, seritler })
}

fn yaz_pdf(sayfalar: &[Sayfa]) -> Vec<u8> {
    let mut pdf: Vec<u8> = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n".to_vec();
    let mut ofs: Vec<usize> = vec![0, 0, 0]; // 1 katalog, 2 sayfalar (sonra)
    let mut sonraki = 3u32;
    let mut sayfa_nolari = Vec::new();
    for s in sayfalar {
        let mut xobj = String::new();
        let mut icerik = String::new();
        let olcek_y = s.yuk_pt / s.seritler.iter().map(|g| g.yuk as f64).sum::<f64>().max(1.0);
        let mut y = s.yuk_pt;
        for (i, g) in s.seritler.iter().enumerate() {
            let no = sonraki;
            sonraki += 1;
            ofs.push(pdf.len());
            pdf.extend_from_slice(
                format!("{} 0 obj\n<< /Type /XObject /Subtype /Image /Width {} /Height {} {} /Length {} >>\nstream\n", no, g.gen, g.yuk, g.sozluk, g.veri.len()).as_bytes(),
            );
            pdf.extend_from_slice(&g.veri);
            pdf.extend_from_slice(b"\nendstream\nendobj\n");
            let h = g.yuk as f64 * olcek_y;
            y -= h;
            xobj.push_str(&format!("/I{} {} 0 R ", i, no));
            icerik.push_str(&format!("q {:.4} 0 0 {:.4} 0 {:.4} cm /I{} Do Q\n", s.gen_pt, h, y.max(0.0), i));
        }
        let ic_no = sonraki;
        sonraki += 1;
        ofs.push(pdf.len());
        pdf.extend_from_slice(format!("{} 0 obj\n<< /Length {} >>\nstream\n{}\nendstream\nendobj\n", ic_no, icerik.len(), icerik).as_bytes());
        let s_no = sonraki;
        sonraki += 1;
        ofs.push(pdf.len());
        pdf.extend_from_slice(
            format!(
                "{} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {:.2} {:.2}] /Resources << /XObject << {}>> >> /Contents {} 0 R >>\nendobj\n",
                s_no, s.gen_pt, s.yuk_pt, xobj, ic_no
            )
            .as_bytes(),
        );
        sayfa_nolari.push(s_no);
    }
    ofs[1] = pdf.len();
    pdf.extend_from_slice(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n");
    ofs[2] = pdf.len();
    let kids: Vec<String> = sayfa_nolari.iter().map(|n| format!("{} 0 R", n)).collect();
    pdf.extend_from_slice(format!("2 0 obj\n<< /Type /Pages /Kids [{}] /Count {} >>\nendobj\n", kids.join(" "), sayfa_nolari.len()).as_bytes());
    let xref = pdf.len();
    pdf.extend_from_slice(format!("xref\n0 {}\n0000000000 65535 f \n", sonraki).as_bytes());
    for o in &ofs[1..] {
        pdf.extend_from_slice(format!("{:010} 00000 n \n", o).as_bytes());
    }
    pdf.extend_from_slice(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n", sonraki, xref).as_bytes());
    pdf
}

/// TIFF (çok sayfalı olabilir) → PDF.
pub fn tiff_pdf(b: &[u8]) -> Result<Vec<u8>, String> {
    let le = match b.get(0..4) {
        Some(b"II*\0") => true,
        Some(b"MM\0*") => false,
        _ => return Err("TIFF değil (ya da BigTIFF)".into()),
    };
    let r = Okur { b, le };
    let mut sayfalar = Vec::new();
    let mut hata = None;
    for f in ifdler(&r)? {
        // küçük resim (thumbnail) dizinlerini atla
        if f.tek(&r, 254, 0) & 1 != 0 {
            continue;
        }
        match sayfa_coz(&r, &f) {
            Ok(s) => sayfalar.push(s),
            Err(e) => hata = Some(e),
        }
    }
    if sayfalar.is_empty() {
        return Err(hata.unwrap_or_else(|| "TIFF'te sayfa yok".into()));
    }
    Ok(yaz_pdf(&sayfalar))
}

/// Ham RGB (8 bit) → tek sayfalık PDF (PNG gibi çözülmüş görüntüler için). 200 dpi varsayılır, A4'e sığdırılır.
pub fn rgb_pdf(gen: u32, yuk: u32, rgb: &[u8]) -> Vec<u8> {
    let olcek = (595.0 / gen as f64).min(842.0 / yuk as f64).min(72.0 / 96.0 * 1.0f64.max(1.0));
    let s = Sayfa {
        gen_pt: gen as f64 * olcek,
        yuk_pt: yuk as f64 * olcek,
        seritler: vec![Goruntu { gen, yuk, sozluk: "/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode".into(), veri: sikistir(rgb) }],
    };
    yaz_pdf(&[s])
}
