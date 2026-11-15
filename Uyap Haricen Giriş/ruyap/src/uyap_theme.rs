//! Çoklu UYAP masaüstü — ortak tema (uyap_theme.py'nin 1:1 Rust/egui karşılığı)
//! ============================================================================
//! Palet: ılık kâğıt zemin + adaçayı vurgu.

use egui::{Color32, FontData, FontDefinitions, FontFamily, FontId, Pos2, Rect, Response, Rounding, Sense, Stroke, Ui, Vec2};

pub struct C;

impl C {
    pub const BG_HEX: &'static str = "#F1EEE8";
    pub const SIDEBAR_HEX: &'static str = "#EAE5DC";
    pub const HEADER_HEX: &'static str = "#EFEBE3";
    pub const CARD_HEX: &'static str = "#FCFBF8";
    pub const CARD_EDGE_HEX: &'static str = "#E8E2D7";
    pub const SHADOW_HEX: &'static str = "#E0D9CD";

    pub const INK_HEX: &'static str = "#43423D";
    pub const INK_SOFT_HEX: &'static str = "#8C867B";
    pub const INK_FAINT_HEX: &'static str = "#B6AFA2";
    pub const LINE_HEX: &'static str = "#E3DDD2";

    pub const SAGE_HEX: &'static str = "#7C9A7E";
    pub const SAGE_DK_HEX: &'static str = "#5E7D63";
    pub const SAGE_TINT_HEX: &'static str = "#E4EBE0";

    pub const CLAY_HEX: &'static str = "#C18A66";
    pub const BLUE_HEX: &'static str = "#7C93AB";
    pub const GOLD_HEX: &'static str = "#C8A66A";

    pub const OK_HEX: &'static str = "#7C9A7E";
    pub const WAIT_HEX: &'static str = "#C8A66A";
    pub const OFF_HEX: &'static str = "#B6AFA2";

    // ── Renkler o anki temadan gelir (2026-11-14: masaüstüne web ile ortak temalar). Adlar eski sabitlerle
    // aynı kaldı (C::INK() …) ki çağıran kod değişmesin. Armonia = masaüstünün önceki tonları.
    #[allow(non_snake_case)] pub fn BG() -> Color32 { palet().zemin }
    #[allow(non_snake_case)] pub fn SIDEBAR() -> Color32 { palet().sol }
    #[allow(non_snake_case)] pub fn HEADER() -> Color32 { palet().serit }
    #[allow(non_snake_case)] pub fn CARD() -> Color32 { palet().yuzey }
    #[allow(non_snake_case)] pub fn CARD_EDGE() -> Color32 { palet().kenar }
    #[allow(non_snake_case)] pub fn SHADOW() -> Color32 { karistir(palet().kenar, palet().yazi, 0.10) }
    #[allow(non_snake_case)] pub fn INK() -> Color32 { palet().yazi }
    #[allow(non_snake_case)] pub fn INK_SOFT() -> Color32 { karistir(palet().yazi, palet().yuzey, 0.42) }
    #[allow(non_snake_case)] pub fn INK_FAINT() -> Color32 { karistir(palet().yazi, palet().yuzey, 0.62) }
    #[allow(non_snake_case)] pub fn LINE() -> Color32 { palet().kenar }
    #[allow(non_snake_case)] pub fn SAGE() -> Color32 { palet().vurgu }
    #[allow(non_snake_case)] pub fn SAGE_DK() -> Color32 { palet().vurgu_koyu }
    #[allow(non_snake_case)] pub fn SAGE_TINT() -> Color32 { palet().vurgu_zemin }
    /// Vurgu renkli zemin (birincil düğme) üstündeki yazı — Güneş'te koyu.
    #[allow(non_snake_case)] pub fn VURGU_UZERI() -> Color32 { palet().vurgu_uzeri }
    /// Metin kutusu zemini (açık temalarda kart yüzeyi, koyuda koyu yüzey).
    #[allow(non_snake_case)] pub fn ENTRY() -> Color32 { palet().yuzey }
    // Anlam taşıyan sabit renkler (hata, uyarı) temadan bağımsız.
    #[allow(non_snake_case)] pub fn CLAY() -> Color32 { Color32::from_rgb(0xC1, 0x8A, 0x66) }
    #[allow(non_snake_case)] pub fn CLAY_DK() -> Color32 { Color32::from_rgb(0xA9, 0x74, 0x4F) }
    #[allow(non_snake_case)] pub fn BLUE() -> Color32 { Color32::from_rgb(0x7C, 0x93, 0xAB) }
    #[allow(non_snake_case)] pub fn GOLD() -> Color32 { Color32::from_rgb(0xC8, 0xA6, 0x6A) }
    #[allow(non_snake_case)] pub fn OK() -> Color32 { Self::SAGE() }
    #[allow(non_snake_case)] pub fn WAIT() -> Color32 { Self::GOLD() }
    #[allow(non_snake_case)] pub fn OFF() -> Color32 { Self::INK_FAINT() }
}

// ── Temalar ──────────────────────────────────────────────────────────────────────────────────────────
// Web (uyap_core/enjekte/tema.css, tema.js) ile aynı adlar ve renkler; seçim iki tarafta ortak
// (tema_dosyasi). "koyu": masaüstünün kendi koyu paleti (UYAP'ın koyu görünümü değil).
#[derive(Debug, Clone, Copy)]
pub struct Palet {
    pub zemin: Color32,
    pub sol: Color32,
    pub serit: Color32,
    pub yuzey: Color32,
    pub kenar: Color32,
    pub yazi: Color32,
    pub vurgu: Color32,
    pub vurgu_koyu: Color32,
    pub vurgu_zemin: Color32,
    pub vurgu_uzeri: Color32,
}

const fn h(x: u32) -> Color32 {
    Color32::from_rgb((x >> 16) as u8, (x >> 8) as u8, x as u8)
}

const fn pal(zemin: u32, sol: u32, serit: u32, yuzey: u32, kenar: u32, yazi: u32, vurgu: u32, vurgu_koyu: u32,
             vurgu_zemin: u32, vurgu_uzeri: u32) -> Palet {
    Palet {
        zemin: h(zemin), sol: h(sol), serit: h(serit), yuzey: h(yuzey), kenar: h(kenar), yazi: h(yazi),
        vurgu: h(vurgu), vurgu_koyu: h(vurgu_koyu), vurgu_zemin: h(vurgu_zemin), vurgu_uzeri: h(vurgu_uzeri),
    }
}

/// (kimlik, görünen ad, palet) — sıra menüdeki sıradır.
pub const TEMALAR: &[(&str, &str, Palet)] = &[
    ("armonia", "Armonia", pal(0xF1EEE8, 0xEAE5DC, 0xEFEBE3, 0xFCFBF8, 0xE8E2D7, 0x43423D, 0x7C9A7E, 0x5E7D63, 0xE4EBE0, 0xFFFFFF)),
    ("gul", "Gül", pal(0xFFF3F7, 0xFDE6EF, 0xFDEEF4, 0xFFFAFC, 0xF6CFDD, 0x4A2335, 0xD6447E, 0xB8326A, 0xFDE7F0, 0xFFFFFF)),
    ("okyanus", "Okyanus", pal(0xEEF6F7, 0xE1EFF1, 0xEAF4F5, 0xFBFEFF, 0xCFE5E8, 0x1F3B40, 0x0F7C8C, 0x0B6370, 0xDFF3F5, 0xFFFFFF)),
    ("lavanta", "Lavanta", pal(0xF6F3FA, 0xECE6F5, 0xF2EDF8, 0xFDFBFF, 0xDDD3EC, 0x3B2E4F, 0x7B5EA7, 0x654A8E, 0xEFE9F8, 0xFFFFFF)),
    ("gunbatimi", "Gün Batımı", pal(0xFBF4EE, 0xF5E8DC, 0xF8EEE5, 0xFFFBF7, 0xEDD9C7, 0x4A3020, 0xC2602F, 0xA34E24, 0xFBEBDF, 0xFFFFFF)),
    ("gunes", "Güneş", pal(0xFFFBEA, 0xFFF3C4, 0xFDF6D8, 0xFFFDF3, 0xF1E2A6, 0x3D3418, 0xF2B51D, 0xB98A0A, 0xFFF4CF, 0x3B2F0B)),
    ("grafit", "Grafit", pal(0xF3F4F6, 0xE5E7EB, 0xF1F2F4, 0xFFFFFF, 0xD6D9DE, 0x1F2937, 0x475569, 0x334155, 0xE9EDF2, 0xFFFFFF)),
    // UYAP'ın kendi iki görünümü (kullanıcı kararı 2026-11-14: seçenek olarak kalsın; varsayılan Armonia).
    ("uyap", "UYAP", pal(0xF1F1F1, 0xFFFFFF, 0xF5F6FA, 0xFFFFFF, 0xDEE2E6, 0x212529, 0x24377F, 0x1B295F, 0xE8ECF8, 0xFFFFFF)),
    ("koyu", "Koyu (UYAP)", pal(0x1D2130, 0x232838, 0x262B3B, 0x2A2F40, 0x3A4055, 0xDEDEDE, 0x3A55B5, 0x8EA2E6, 0x2C3554, 0xFFFFFF)),
];

static ETKIN_TEMA: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);

pub fn palet() -> &'static Palet {
    let i = ETKIN_TEMA.load(std::sync::atomic::Ordering::Relaxed);
    &TEMALAR[i.min(TEMALAR.len() - 1)].2
}

pub fn etkin_tema() -> &'static str {
    TEMALAR[ETKIN_TEMA.load(std::sync::atomic::Ordering::Relaxed).min(TEMALAR.len() - 1)].0
}

/// Temayı kimliğiyle seçer; bilinmeyen ad (eski "uyap" dahil) → Armonia. Değiştiyse true.
pub fn tema_sec(ad: &str) -> bool {
    let i = TEMALAR.iter().position(|t| t.0 == ad.trim()).unwrap_or(0);
    ETKIN_TEMA.swap(i, std::sync::atomic::Ordering::Relaxed) != i
}

/// Seçimin web ile ortak tutulduğu dosya: %LOCALAPPDATA%/CokluUyap/tema.txt (tek satır, tema kimliği).
pub fn tema_dosyasi() -> std::path::PathBuf {
    let base = std::env::var("LOCALAPPDATA")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| std::env::var("HOME").ok())
        .unwrap_or_else(|| ".".to_string());
    std::path::PathBuf::from(base).join("CokluUyap").join("tema.txt")
}

pub fn tema_dosyadan_oku() -> Option<String> {
    let s = std::fs::read_to_string(tema_dosyasi()).ok()?;
    let s = s.trim().to_string();
    TEMALAR.iter().any(|t| t.0 == s).then_some(s)
}

pub fn tema_dosyaya_yaz(ad: &str) {
    if TEMALAR.iter().any(|t| t.0 == ad) {
        let yol = tema_dosyasi();
        if let Some(d) = yol.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(yol, ad);
    }
}

/// egui'nin genel görünümünü (açık/koyu, seçim, imleç, pencere zemini) etkin temaya uyarlar.
/// Tema değişince çağrılır.
pub fn gorunum_uygula(ctx: &egui::Context) {
    let p = palet();
    let koyu = etkin_tema() == "koyu";
    let mut v = if koyu { egui::Visuals::dark() } else { egui::Visuals::light() };
    v.panel_fill = p.zemin;
    v.window_fill = p.yuzey;
    v.extreme_bg_color = p.yuzey;
    v.faint_bg_color = p.serit;
    v.window_stroke = Stroke::new(1.0, p.kenar);
    v.selection.bg_fill = karistir(p.vurgu_zemin, p.vurgu, 0.35);
    v.selection.stroke = Stroke::new(1.0, p.vurgu_koyu);
    v.hyperlink_color = p.vurgu_koyu;
    v.text_cursor.stroke = Stroke::new(2.0, p.yazi);
    v.widgets.noninteractive.fg_stroke = Stroke::new(1.0, p.yazi);
    v.widgets.inactive.bg_fill = p.yuzey;
    v.widgets.inactive.weak_bg_fill = p.yuzey;
    v.widgets.hovered.weak_bg_fill = p.vurgu_zemin;
    v.widgets.active.weak_bg_fill = p.vurgu_zemin;
    ctx.set_visuals(v);
}

/// İki rengi karıştırır (t=0 → a, t=1 → b).
pub fn karistir(a: Color32, b: Color32, t: f32) -> Color32 {
    let k = |x: u8, y: u8| (x as f32 + (y as f32 - x as f32) * t).round() as u8;
    Color32::from_rgb(k(a.r(), b.r()), k(a.g(), b.g()), k(a.b(), b.b()))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ButtonKind {
    Primary,
    Ghost,
    Stop,
}

/// Buton tipleri: (zemin, hover_zemin, yazı, kenar | None)
pub fn btn_palette(kind: ButtonKind, enabled: bool) -> (Color32, Color32, Color32, Option<Color32>) {
    if !enabled {
        return (C::LINE(), C::LINE(), C::INK_FAINT(), Some(C::LINE()));
    }
    match kind {
        ButtonKind::Primary => (C::SAGE(), C::SAGE_DK(), C::VURGU_UZERI(), None),
        ButtonKind::Ghost => (C::CARD(), C::SAGE_TINT(), C::INK_SOFT(), Some(C::LINE())),
        ButtonKind::Stop => (C::CLAY(), C::CLAY_DK(), Color32::WHITE, None),
    }
}

/// Tuval üzerinde yuvarlak köşeli dikdörtgen.
pub fn round_rect(
    painter: &egui::Painter,
    rect: Rect,
    radius: f32,
    fill: Color32,
    outline: Option<Color32>,
) {
    let rounding = Rounding::same(radius);
    let stroke = match outline {
        Some(col) => Stroke::new(1.0, col),
        None => Stroke::NONE,
    };
    painter.rect(rect, rounding, fill, stroke);
}

/// Yumuşak köşeli, hafif gölgeli buton (uyap_theme.RoundButton 1:1).
pub struct RoundButton<'a> {
    pub text: &'a str,
    pub kind: ButtonKind,
    pub font: FontId,
    pub height: f32,
    pub radius: f32,
    pub pad: f32,
    pub enabled: bool,
    pub fill_width: bool,
}

impl<'a> RoundButton<'a> {
    pub fn new(text: &'a str) -> Self {
        Self {
            text,
            kind: ButtonKind::Primary,
            font: Fonts::nav_b(),
            height: 36.0,
            radius: 11.0,
            pad: 18.0,
            enabled: true,
            fill_width: false,
        }
    }

    pub fn kind(mut self, kind: ButtonKind) -> Self {
        self.kind = kind;
        self
    }

    pub fn font(mut self, font: FontId) -> Self {
        self.font = font;
        self
    }

    pub fn height(mut self, height: f32) -> Self {
        self.height = height;
        self
    }

    pub fn radius(mut self, radius: f32) -> Self {
        self.radius = radius;
        self
    }

    pub fn pad(mut self, pad: f32) -> Self {
        self.pad = pad;
        self
    }

    pub fn enabled(mut self, enabled: bool) -> Self {
        self.enabled = enabled;
        self
    }

    pub fn fill_width(mut self, fill: bool) -> Self {
        self.fill_width = fill;
        self
    }

    pub fn show(self, ui: &mut Ui) -> Response {
        let galley = ui.painter().layout_no_wrap(
            self.text.to_string(),
            self.font.clone(),
            Color32::WHITE,
        );
        let desired_w = if self.fill_width {
            ui.available_width().max(galley.size().x + self.pad * 2.0)
        } else {
            galley.size().x + self.pad * 2.0
        };
        let desired = Vec2::new(desired_w, self.height);
        let sense = if self.enabled { Sense::click() } else { Sense::hover() };
        let (rect, resp) = ui.allocate_exact_size(desired, sense);

        if resp.hovered() && self.enabled {
            ui.ctx().set_cursor_icon(egui::CursorIcon::PointingHand);
        }

        if ui.is_rect_visible(rect) {
            let (fill, hover, mut fg, border) = btn_palette(self.kind, self.enabled);
            let is_hover = resp.hovered() && self.enabled;
            let bg = if is_hover && self.kind != ButtonKind::Ghost {
                hover
            } else {
                fill
            };

            let painter = ui.painter();
            // hafif gölge (alt-sağ, yumuşak): (2, 4, w-1, h-1)
            let shadow_rect = Rect::from_min_max(
                Pos2::new(rect.min.x + 2.0, rect.min.y + 4.0),
                Pos2::new(rect.max.x - 1.0, rect.max.y - 1.0),
            );
            round_rect(painter, shadow_rect, self.radius, C::SHADOW(), None);

            // gövde: (1, 1, w-2, h-3)
            let body_rect = Rect::from_min_max(
                Pos2::new(rect.min.x + 1.0, rect.min.y + 1.0),
                Pos2::new(rect.max.x - 2.0, rect.max.y - 3.0),
            );
            round_rect(painter, body_rect, self.radius, bg, Some(border.unwrap_or(bg)));

            // ghost hover: zemini hafif tint, yazıyı koyulaştır
            if self.kind == ButtonKind::Ghost && is_hover {
                round_rect(painter, body_rect, self.radius, C::SAGE_TINT(), Some(C::SAGE()));
                fg = C::INK();
            }

            let text_pos = Pos2::new(
                (rect.min.x + rect.max.x - 1.0) / 2.0,
                (rect.min.y + rect.max.y - 1.0) / 2.0,
            );
            painter.text(
                text_pos,
                egui::Align2::CENTER_CENTER,
                self.text,
                self.font,
                fg,
            );
        }

        resp
    }
}

/// Kabuğun kullandığı yazı tipleri (UyapMasa / uyap_theme.make_fonts ile aynı ölçek).
/// Not: Tkinter'daki pt boyutları Windows 96 DPI'da ~1.333 px karşılığıdır; burada
/// okunaklı 1:1 masaüstü ölçeği için doğrudan pt * 1.333 kullanılır.
pub struct Fonts;

impl Fonts {
    pub fn semibold_family() -> FontFamily {
        FontFamily::Name("SegoeUISemibold".into())
    }

    pub fn regular_family() -> FontFamily {
        FontFamily::Proportional
    }

    pub fn mono_family() -> FontFamily {
        FontFamily::Monospace
    }

    #[inline]
    fn pt(pt: f32) -> f32 {
        pt * 1.333
    }

    pub fn h1() -> FontId { FontId::new(Self::pt(20.0), Self::semibold_family()) }
    pub fn sub() -> FontId { FontId::new(Self::pt(11.0), Self::regular_family()) }
    pub fn grp() -> FontId { FontId::new(Self::pt(8.0), Self::semibold_family()) }
    pub fn nav() -> FontId { FontId::new(Self::pt(11.0), Self::regular_family()) }
    pub fn nav_b() -> FontId { FontId::new(Self::pt(9.0), Self::semibold_family()) }
    pub fn nav_sel() -> FontId { FontId::new(Self::pt(11.0), Self::semibold_family()) }
    pub fn card_t() -> FontId { FontId::new(Self::pt(13.0), Self::semibold_family()) }
    pub fn card_d() -> FontId { FontId::new(Self::pt(10.0), Self::regular_family()) }
    pub fn body() -> FontId { FontId::new(Self::pt(11.0), Self::regular_family()) }
    pub fn small() -> FontId { FontId::new(Self::pt(9.0), Self::regular_family()) }
    pub fn mono() -> FontId { FontId::new(Self::pt(9.0), Self::mono_family()) }
    pub fn mono_10() -> FontId { FontId::new(Self::pt(10.0), Self::mono_family()) }
    pub fn mono_11() -> FontId { FontId::new(Self::pt(11.0), Self::mono_family()) }
    pub fn code_big() -> FontId { FontId::new(Self::pt(28.0), Self::semibold_family()) }
}

/// Windows sistem yazı tiplerini (`Segoe UI`, `Segoe UI Semibold`, `Consolas`) yükler.
pub fn configure_egui_fonts(ctx: &egui::Context) {
    let mut fonts = FontDefinitions::default();

    let win_fonts = std::env::var("WINDIR")
        .map(|w| std::path::PathBuf::from(w).join("Fonts"))
        .unwrap_or_else(|_| std::path::PathBuf::from(r"C:\Windows\Fonts"));

    if let Ok(data) = std::fs::read(win_fonts.join("segoeui.ttf")) {
        fonts.font_data.insert("SegoeUI".to_owned(), FontData::from_owned(data).into());
        fonts
            .families
            .entry(FontFamily::Proportional)
            .or_default()
            .insert(0, "SegoeUI".to_owned());
    }

    if let Ok(data) = std::fs::read(win_fonts.join("seguisb.ttf")) {
        fonts.font_data.insert("SegoeUISemibold".to_owned(), FontData::from_owned(data).into());
        fonts
            .families
            .insert(Fonts::semibold_family(), vec!["SegoeUISemibold".to_owned(), "SegoeUI".to_owned()]);
    } else {
        fonts
            .families
            .insert(Fonts::semibold_family(), fonts.families.get(&FontFamily::Proportional).cloned().unwrap_or_default());
    }

    if let Ok(data) = std::fs::read(win_fonts.join("consola.ttf")) {
        fonts.font_data.insert("Consolas".to_owned(), FontData::from_owned(data).into());
        fonts
            .families
            .entry(FontFamily::Monospace)
            .or_default()
            .insert(0, "Consolas".to_owned());
    }

    ctx.set_fonts(fonts);
}
