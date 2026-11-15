//! Sıra kapısı arayüzü (uyap_app.rs'den kullanılır) — müşteri komut satırı görmez.
//!
//!   • SiraKapisiSekmesi: ofis bilgisayarı, gösterge panelindeki "Sıra Kapısı" sekmesi.
//!   • BotKurulumPenceresi: bot bilgisayarı, giriş ekranındaki "Bot bilgisayarı kurulumu".
//!
//! `sira_kapisi_arayuz.py` dosyasının 1:1 Rust/egui karşılığı.

use base64::Engine;
use eframe::egui;
use std::sync::mpsc::{self, Receiver};
use std::sync::Arc;

use crate::sira_eslestirme as se;
use crate::sira_kapisi as sk;
use crate::uyap_theme::{ButtonKind, C, Fonts, RoundButton};

// ── Windows MessageBox yardımcıları ───────────────────────────────────────────────────────

#[cfg(windows)]
pub fn msg_info(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONINFORMATION, MB_OK};
    let t: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let m: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(std::ptr::null_mut(), m.as_ptr(), t.as_ptr(), MB_OK | MB_ICONINFORMATION);
    }
}

#[cfg(not(windows))]
pub fn msg_info(title: &str, text: &str) {
    println!("[{title}] {text}");
}

#[cfg(windows)]
pub fn msg_error(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};
    let t: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let m: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(std::ptr::null_mut(), m.as_ptr(), t.as_ptr(), MB_OK | MB_ICONERROR);
    }
}

#[cfg(not(windows))]
pub fn msg_error(title: &str, text: &str) {
    eprintln!("[{title}] {text}");
}

#[cfg(windows)]
pub fn msg_ask_yes_no(title: &str, text: &str) -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, IDYES, MB_ICONQUESTION, MB_YESNO};
    let t: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let m: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            m.as_ptr(),
            t.as_ptr(),
            MB_YESNO | MB_ICONQUESTION,
        ) == IDYES
    }
}

#[cfg(not(windows))]
pub fn msg_ask_yes_no(_title: &str, _text: &str) -> bool {
    true
}

#[cfg(windows)]
pub fn msg_ask_ok_cancel(title: &str, text: &str) -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, IDOK, MB_ICONQUESTION, MB_OKCANCEL,
    };
    let t: Vec<u16> = title.encode_utf16().chain(std::iter::once(0)).collect();
    let m: Vec<u16> = text.encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            m.as_ptr(),
            t.as_ptr(),
            MB_OKCANCEL | MB_ICONQUESTION,
        ) == IDOK
    }
}

#[cfg(not(windows))]
pub fn msg_ask_ok_cancel(_title: &str, _text: &str) -> bool {
    true
}

#[cfg(windows)]
pub fn open_file_shell(path: &std::path::Path) {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;
    let op: Vec<u16> = "open\0".encode_utf16().collect();
    let file: Vec<u16> = path.as_os_str().encode_wide().chain(std::iter::once(0)).collect();
    unsafe {
        ShellExecuteW(
            std::ptr::null_mut(),
            op.as_ptr(),
            file.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            SW_SHOWNORMAL,
        );
    }
}

#[cfg(not(windows))]
pub fn open_file_shell(path: &std::path::Path) {
    let _ = std::process::Command::new("xdg-open").arg(path).spawn();
}

fn yonetici_hata_metni(hata: &se::EslestirmeHatasi) -> String {
    match hata {
        se::EslestirmeHatasi::YetkiReddedildi => {
            "Windows yönetici onayı verilmedi; işlem yapılmadı.".to_string()
        }
        other => format!("İşlem yapılamadı: {other}"),
    }
}

fn kod_etiketi(ui: &mut egui::Ui, kod: &str) {
    egui::Frame::none()
        .fill(C::SAGE_TINT())
        .inner_margin(egui::Margin::symmetric(18.0, 8.0))
        .show(ui, |ui| {
            ui.label(
                egui::RichText::new(kod)
                    .font(Fonts::code_big())
                    .color(C::SAGE_DK()),
            );
        });
}

// ═══════════════════════════════ OFİS TARAFI ═════════════════════════════════════════════

pub struct SiraKapisiSekmesi {
    pub botlar: Vec<sk::BotKaydi>,
    pub secili_idx: Option<usize>,
    pub etkin: bool,
    pub kapi_ip: String,
    pub calisiyor: bool,
    pub sertifika_bitis_str: Option<String>,
    pub yenile_zamani: Option<std::time::Instant>,
    pub firewall_rx: Option<Receiver<Result<i32, se::EslestirmeHatasi>>>,
    pub eslestirme: Option<EslestirmePenceresi>,
}

impl Default for SiraKapisiSekmesi {
    fn default() -> Self {
        let mut s = Self {
            botlar: Vec::new(),
            secili_idx: None,
            etkin: false,
            kapi_ip: String::new(),
            calisiyor: false,
            sertifika_bitis_str: None,
            yenile_zamani: None,
            firewall_rx: None,
            eslestirme: None,
        };
        s.yenile();
        s
    }
}

impl SiraKapisiSekmesi {
    pub fn yenile(&mut self) {
        let ayar = sk::ayar_oku();
        self.botlar = sk::botlar(Some(&ayar));
        if let Some(idx) = self.secili_idx {
            if idx >= self.botlar.len() {
                self.secili_idx = None;
            }
        }
        self.etkin = ayar.etkin;
        self.kapi_ip = ayar.kapi_ip.clone().unwrap_or_default();
        self.calisiyor = sk::calisiyor_mu();
        self.sertifika_bitis_str = sk::sertifika_bitis()
            .map(|dt| dt.format("%d.%m.%Y").to_string());
    }

    pub fn show(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        if let Some(t) = self.yenile_zamani {
            if std::time::Instant::now() >= t {
                self.yenile_zamani = None;
                self.yenile();
            } else {
                ctx.request_repaint_after(std::time::Duration::from_millis(200));
            }
        }

        if let Some(rx) = &self.firewall_rx {
            if let Ok(res) = rx.try_recv() {
                self.firewall_rx = None;
                match res {
                    Err(hata) => {
                        msg_error("Sıra Kapısı", &yonetici_hata_metni(&hata));
                    }
                    Ok(kod) if kod != 0 => {
                        msg_error("Sıra Kapısı", se::cikis_metni(kod));
                    }
                    Ok(_) => {
                        self.eslestirme = Some(EslestirmePenceresi::new());
                    }
                }
            } else {
                ctx.request_repaint_after(std::time::Duration::from_millis(150));
            }
        }

        // Eşleştirme penceresi açıksa alt pencere (viewport) olarak göster
        if let Some(mut ep) = self.eslestirme.take() {
            let mut kapandi = false;
            let mut bitti_yenile = false;
            ctx.show_viewport_immediate(
                egui::ViewportId::from_hash_of("sira_eslestirme_penceresi"),
                egui::ViewportBuilder::default()
                    .with_title("Bot Bilgisayarı Ekle")
                    .with_inner_size([470.0, 400.0])
                    .with_resizable(false),
                |ctx_child, _class| {
                    if ctx_child.input(|i| i.viewport().close_requested()) {
                        ep.durdur();
                        kapandi = true;
                        return;
                    }
                    if ep.show(ctx_child) {
                        bitti_yenile = true;
                    }
                    if ep.kapandi {
                        kapandi = true;
                    }
                },
            );
            if bitti_yenile || kapandi {
                self.yenile();
            }
            if !kapandi {
                self.eslestirme = Some(ep);
            }
        }

        egui::Frame::none()
            .fill(C::BG())
            .inner_margin(egui::Margin::same(14.0))
            .show(ui, |ui| {
                egui::Frame::none()
                    .fill(C::CARD())
                    .stroke(egui::Stroke::new(1.0, C::CARD_EDGE()))
                    .inner_margin(egui::Margin::symmetric(20.0, 16.0))
                    .show(ui, |ui| {
                        ui.set_min_size(ui.available_size());

                        ui.label(
                            egui::RichText::new("Sıra Kapısı")
                                .font(Fonts::card_t())
                                .color(C::INK()),
                        );
                        ui.add_space(4.0);
                        ui.label(
                            egui::RichText::new(
                                "Ofisteki başka bir bilgisayarda çalışan UYAP otomasyonunun (bot) isteklerini \
                                 sıraya alır. Sizin işlemleriniz önce gelir, iki istek UYAP'a aynı anda gitmez. \
                                 Kapı yalnız paylaşım açıkken çalışır.",
                            )
                            .font(Fonts::small())
                            .color(C::INK_SOFT()),
                        );
                        ui.add_space(10.0);

                        let (metin, renk) = if self.botlar.is_empty() {
                            ("● Kapı kurulmadı".to_string(), C::INK_FAINT())
                        } else if !self.etkin {
                            ("● Kapı kapalı (botlar UYAP'a ulaşamaz)".to_string(), C::CLAY())
                        } else if self.calisiyor {
                            (format!("● Kapı açık — {}", self.kapi_ip), C::SAGE_DK())
                        } else {
                            ("● Kapı hazır — paylaşım başlayınca açılır".to_string(), C::GOLD())
                        };
                        ui.label(egui::RichText::new(metin).font(Fonts::body()).color(renk));
                        ui.add_space(8.0);

                        ui.label(
                            egui::RichText::new("Bağlı bot bilgisayarları")
                                .font(Fonts::small())
                                .color(C::INK_SOFT()),
                        );
                        ui.add_space(4.0);

                        egui::Frame::none()
                            .fill(C::CARD())
                            .stroke(egui::Stroke::new(1.0, C::LINE()))
                            .inner_margin(egui::Margin::same(4.0))
                            .show(ui, |ui| {
                                ui.set_width(ui.available_width());
                                ui.set_min_height(140.0);
                                egui::ScrollArea::vertical()
                                    .id_salt("sira_bot_liste")
                                    .max_height(140.0)
                                    .show(ui, |ui| {
                                        if self.botlar.is_empty() {
                                            ui.label(
                                                egui::RichText::new("Henüz bot bilgisayarı eklenmedi.")
                                                    .font(Fonts::body())
                                                    .color(C::INK_SOFT()),
                                            );
                                        } else {
                                            for (i, b) in self.botlar.iter().enumerate() {
                                                let ad = if b.ad.is_empty() {
                                                    String::new()
                                                } else {
                                                    format!(" — {}", b.ad)
                                                };
                                                let tarih = if b.tarih.is_empty() {
                                                    String::new()
                                                } else {
                                                    format!("  ({})", b.tarih)
                                                };
                                                let line = format!("{}{}{}", b.ip, ad, tarih);
                                                let selected = self.secili_idx == Some(i);
                                                let bg = if selected { C::SAGE_TINT() } else { C::CARD() };
                                                let resp = egui::Frame::none()
                                                    .fill(bg)
                                                    .inner_margin(egui::Margin::symmetric(6.0, 3.0))
                                                    .show(ui, |ui| {
                                                        ui.set_width(ui.available_width());
                                                        ui.label(
                                                            egui::RichText::new(line)
                                                                .font(Fonts::body())
                                                                .color(C::INK()),
                                                        );
                                                    })
                                                    .response
                                                    .interact(egui::Sense::click());
                                                if resp.clicked() {
                                                    self.secili_idx = Some(i);
                                                }
                                            }
                                        }
                                    });
                            });

                        ui.add_space(10.0);

                        ui.horizontal(|ui| {
                            if RoundButton::new("Bot Bilgisayarı Ekle")
                                .kind(ButtonKind::Primary)
                                .font(Fonts::nav_b())
                                .height(36.0)
                                .show(ui)
                                .clicked()
                            {
                                self.on_ekle();
                            }
                            ui.add_space(8.0);
                            if RoundButton::new("Seçili Botu Kaldır")
                                .kind(ButtonKind::Ghost)
                                .font(Fonts::nav_b())
                                .height(36.0)
                                .show(ui)
                                .clicked()
                            {
                                self.on_sil();
                            }
                            ui.add_space(8.0);
                            let ac_kapat_text = if !self.etkin && !self.botlar.is_empty() {
                                "Kapıyı Aç"
                            } else {
                                "Kapıyı Kapat"
                            };
                            if RoundButton::new(ac_kapat_text)
                                .kind(ButtonKind::Ghost)
                                .font(Fonts::nav_b())
                                .height(36.0)
                                .show(ui)
                                .clicked()
                            {
                                self.on_ac_kapat();
                            }
                            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                                if RoundButton::new("Günlüğü Aç")
                                    .kind(ButtonKind::Ghost)
                                    .font(Fonts::nav_b())
                                    .height(36.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    self.on_gunluk();
                                }
                            });
                        });

                        ui.add_space(12.0);
                        let alt_prefix = match (&self.sertifika_bitis_str, self.botlar.is_empty()) {
                            (Some(bitis), false) => format!(
                                "Sertifika geçerlilik sonu: {bitis}. Bu tarihten önce botları yeniden ekleyin.  "
                            ),
                            _ => String::new(),
                        };
                        let alt_text = format!(
                            "{alt_prefix}Bot kaldırıldığında bot bilgisayarında da 'Bot bilgisayarı kurulumu > Kurulumu Kaldır' yapın."
                        );
                        ui.label(
                            egui::RichText::new(alt_text)
                                .font(Fonts::small())
                                .color(C::INK_SOFT()),
                        );
                    });
            });
    }

    fn on_gunluk(&self) {
        let yol = sk::_yol("kapi.log");
        if yol.exists() {
            open_file_shell(&yol);
        } else {
            msg_info("Sıra Kapısı", "Henüz günlük yok.");
        }
    }

    fn on_ac_kapat(&mut self) {
        let ayar = sk::ayar_oku();
        sk::etkin_ayarla(!ayar.etkin);
        self.yenile();
        self.yenile_zamani = Some(std::time::Instant::now() + std::time::Duration::from_millis(1500));
    }

    fn on_sil(&mut self) {
        let Some(idx) = self.secili_idx else {
            return;
        };
        if idx >= self.botlar.len() {
            return;
        }
        let ip = self.botlar[idx].ip.clone();
        if msg_ask_yes_no(
            "Sıra Kapısı",
            &format!(
                "{ip} bot listesinden kaldırılsın mı?\n\n\
                 Bot bilgisayarında da kurulumu kaldırmazsanız bot UYAP'a ulaşamaz."
            ),
        ) {
            sk::bot_sil(&ip);
            self.yenile();
        }
    }

    fn on_ekle(&mut self) {
        if cfg!(windows) && !se::guvenlik_duvari_var() {
            if !msg_ask_ok_cancel(
                "Sıra Kapısı",
                "Bot bilgisayarının bu bilgisayara bağlanabilmesi için Windows güvenlik duvarına \
                 bir izin eklenecek (yalnız Çoklu UYAP ve yerel ağ için).\n\n\
                 Tamam'a bastıktan sonra Windows bir onay penceresi gösterecek; 'Evet' deyin.",
            ) {
                return;
            }
            let (tx, rx) = mpsc::channel();
            self.firewall_rx = Some(rx);
            std::thread::spawn(move || {
                let res = se::yonetici_olarak_calistir(&["guvenlik-duvari"]);
                let _ = tx.send(res);
            });
            return;
        }
        self.eslestirme = Some(EslestirmePenceresi::new());
    }
}

pub enum EslestirmeDurum {
    Bekleniyor,
    Baslatilamadi,
    Istek { ip: String, ad: String, kod: String },
    OnayBekleniyor,
    Bitti { basarili: bool, mesaj: String },
}

pub struct EslestirmePenceresi {
    pub sunucu: Option<Arc<se::EslestirmeSunucusu>>,
    pub bilgi: String,
    pub bilgi_renk: egui::Color32,
    pub sure: String,
    pub durum: EslestirmeDurum,
    pub kapandi: bool,
}

impl EslestirmePenceresi {
    pub fn new() -> Self {
        let sunucu = Arc::new(se::EslestirmeSunucusu::new());
        match sunucu.baslat() {
            Ok(()) => Self {
                sunucu: Some(sunucu),
                bilgi: "Bot bilgisayarında Çoklu UYAP'ı açın ve giriş ekranındaki\n\
                        “Bot bilgisayarı kurulumu” bağlantısına tıklayın.\n\n\
                        Bot bu bilgisayarı kendiliğinden bulacaktır. Bekleniyor…"
                    .to_string(),
                bilgi_renk: C::INK(),
                sure: String::new(),
                durum: EslestirmeDurum::Bekleniyor,
                kapandi: false,
            },
            Err(e) => Self {
                sunucu: None,
                bilgi: format!(
                    "Eşleştirme başlatılamadı: {e}\n(Program ikinci kez mi açık?)"
                ),
                bilgi_renk: C::CLAY(),
                sure: String::new(),
                durum: EslestirmeDurum::Baslatilamadi,
                kapandi: false,
            },
        }
    }

    pub fn durdur(&mut self) {
        if let Some(s) = &self.sunucu {
            s.durdur();
        }
        self.kapandi = true;
    }

    /// Returns true if state transitioned to `Bitti` and caller should refresh.
    pub fn show(&mut self, ctx: &egui::Context) -> bool {
        let mut should_refresh = false;
        if let Some(s) = &self.sunucu {
            while let Some(olay) = s.olay_al() {
                match olay {
                    se::SunucuOlay::Uyari { mesaj } => {
                        self.sure = format!("Uyarı: {mesaj}");
                    }
                    se::SunucuOlay::Istek { ip, ad, kod } => {
                        let ad_ek = if ad.is_empty() {
                            String::new()
                        } else {
                            format!("{ad} ")
                        };
                        self.bilgi = format!(
                            "Bot bilgisayarı bağlanmak istiyor: {ad_ek}({ip})\n\n\
                             Bot ekranındaki kod aşağıdakiyle AYNIYSA Onayla'ya basın:"
                        );
                        self.bilgi_renk = C::INK();
                        self.durum = EslestirmeDurum::Istek { ip, ad, kod };
                    }
                    se::SunucuOlay::Bitti { basarili, mesaj } => {
                        let ek = if basarili {
                            "\n\nPaylaşım açıksa kapı hemen çalışmaya başlar."
                        } else {
                            ""
                        };
                        self.bilgi = format!("{mesaj}{ek}");
                        self.bilgi_renk = if basarili { C::SAGE_DK() } else { C::CLAY() };
                        self.sure.clear();
                        self.durum = EslestirmeDurum::Bitti { basarili, mesaj };
                        should_refresh = true;
                    }
                }
            }
            if !s.durdu_mu() {
                let k = s.kalan_sn();
                self.sure = format!("Eşleştirme penceresi {}:{:02} sonra kapanır.", k / 60, k % 60);
                ctx.request_repaint_after(std::time::Duration::from_millis(200));
            }
        }

        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(C::CARD())
                    .inner_margin(egui::Margin::symmetric(22.0, 18.0)),
            )
            .show(ctx, |ui| {
                ui.label(
                    egui::RichText::new("Bot Bilgisayarı Ekle")
                        .font(Fonts::card_t())
                        .color(C::INK()),
                );
                ui.add_space(10.0);
                ui.label(
                    egui::RichText::new(&self.bilgi)
                        .font(Fonts::body())
                        .color(self.bilgi_renk),
                );

                if let EslestirmeDurum::Istek { kod, .. } = &self.durum {
                    ui.add_space(8.0);
                    ui.vertical_centered(|ui| {
                        kod_etiketi(ui, kod);
                    });
                }

                ui.with_layout(egui::Layout::bottom_up(egui::Align::LEFT), |ui| {
                    if !self.sure.is_empty() {
                        ui.label(
                            egui::RichText::new(&self.sure)
                                .font(Fonts::small())
                                .color(C::INK_SOFT()),
                        );
                        ui.add_space(8.0);
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        match &self.durum {
                            EslestirmeDurum::Istek { .. } => {
                                if RoundButton::new("Onayla")
                                    .kind(ButtonKind::Primary)
                                    .font(Fonts::nav_b())
                                    .height(34.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    self.karar(true);
                                }
                                ui.add_space(8.0);
                                if RoundButton::new("Kodlar farklı — Reddet")
                                    .kind(ButtonKind::Ghost)
                                    .font(Fonts::nav_b())
                                    .height(34.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    self.karar(false);
                                }
                            }
                            EslestirmeDurum::Baslatilamadi | EslestirmeDurum::Bitti { .. } => {
                                if RoundButton::new("Kapat")
                                    .kind(ButtonKind::Ghost)
                                    .font(Fonts::nav_b())
                                    .height(34.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    self.durdur();
                                }
                            }
                            _ => {
                                if RoundButton::new("Vazgeç")
                                    .kind(ButtonKind::Ghost)
                                    .font(Fonts::nav_b())
                                    .height(34.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    self.durdur();
                                }
                            }
                        }
                    });
                });
            });

        should_refresh
    }

    fn karar(&mut self, onay: bool) {
        if let Some(s) = &self.sunucu {
            s.karar_ver(onay);
        }
        if onay {
            self.bilgi = "Bot bilgisayarındaki onay bekleniyor…".to_string();
            self.durum = EslestirmeDurum::OnayBekleniyor;
        } else {
            self.bilgi = "Reddedildi. Başka bir bot bekleniyor…".to_string();
            self.durum = EslestirmeDurum::Bekleniyor;
        }
    }
}

// ═══════════════════════════════ BOT TARAFI ══════════════════════════════════════════════

pub enum BotEkran {
    Baslangic,
    Araniyor(Receiver<Vec<(String, String)>>),
    Bulundu {
        bulunan: Vec<(String, String)>,
        secili: usize,
        elle_ip: String,
    },
    Baglaniyor {
        ip: String,
        rx: Receiver<Result<String, String>>,
    },
    KodGeldi {
        kod: String,
    },
    OnayBekleniyor(Receiver<Result<String, String>>),
    Kuruldu {
        sina: String,
    },
    Hata(String),
}

pub struct BotKurulumPenceresi {
    pub istemci: Option<Arc<std::sync::Mutex<se::EslestirmeIstemcisi>>>,
    pub durum_metin: String,
    pub durum_renk: egui::Color32,
    pub bagli: bool,
    pub ekran: BotEkran,
    pub kaldir_rx: Option<Receiver<Result<i32, se::EslestirmeHatasi>>>,
    pub sina_rx: Option<Receiver<String>>,
    pub kapandi: bool,
}

impl BotKurulumPenceresi {
    pub fn new() -> Self {
        let mut p = Self {
            istemci: None,
            durum_metin: String::new(),
            durum_renk: C::INK_SOFT(),
            bagli: false,
            ekran: BotEkran::Baslangic,
            kaldir_rx: None,
            sina_rx: None,
            kapandi: false,
        };
        p.durum_goster();
        p
    }

    fn durum_goster(&mut self) {
        let ip = se::hosts_kapi_ip(None);
        match ip {
            Some(ref addr) if se::kok_kurulu_mu() => {
                self.durum_metin = format!("● Bu bilgisayar sıra kapısına bağlı (ofis: {addr}).");
                self.durum_renk = C::SAGE_DK();
                self.bagli = true;
            }
            Some(ref addr) => {
                self.durum_metin =
                    format!("● Kurulum eksik (ofis: {addr}). Yeniden kurun ya da kaldırın.");
                self.durum_renk = C::CLAY();
                self.bagli = true;
            }
            None => {
                self.durum_metin = "● Bu bilgisayar sıra kapısına bağlı değil.".to_string();
                self.durum_renk = C::INK_SOFT();
                self.bagli = false;
            }
        }
    }

    pub fn istemci_kapat(&mut self) {
        if let Some(ic) = self.istemci.take() {
            if let Ok(mut guard) = ic.lock() {
                guard.kapat();
            }
        }
    }

    pub fn show(&mut self, ctx: &egui::Context) {
        // Arka plan işlemlerini yokla
        if let Some(rx) = &self.kaldir_rx {
            if let Ok(res) = rx.try_recv() {
                self.kaldir_rx = None;
                self.durum_goster();
                match res {
                    Err(hata) => self.ekran = BotEkran::Hata(yonetici_hata_metni(&hata)),
                    Ok(kod) if kod != 0 => {
                        self.ekran = BotEkran::Hata(se::cikis_metni(kod).to_string());
                    }
                    Ok(_) => {
                        msg_info(
                            "Bot kurulumu",
                            "Kaldırıldı. Ofis bilgisayarında da bu botu Sıra Kapısı listesinden kaldırın.",
                        );
                        self.ekran = BotEkran::Baslangic;
                    }
                }
            } else {
                ctx.request_repaint_after(std::time::Duration::from_millis(150));
            }
        }

        if let Some(rx) = &self.sina_rx {
            if let Ok(sonuc) = rx.try_recv() {
                self.sina_rx = None;
                let metin = match sonuc.as_str() {
                    "tamam" => "Kapı çalışıyor.",
                    "kapali" => {
                        "Kapıya ulaşılamıyor: ofis bilgisayarı kapalı ya da paylaşım durdurulmuş."
                    }
                    "sertifika" => {
                        "Kapıyla güvenli bağlantı kurulamadı; kurulumu yenileyin."
                    }
                    other => other,
                };
                msg_info("Bot kurulumu", metin);
            } else {
                ctx.request_repaint_after(std::time::Duration::from_millis(150));
            }
        }

        let mut next_screen: Option<BotEkran> = None;
        match &self.ekran {
            BotEkran::Araniyor(rx) => {
                if let Ok(sonuc) = rx.try_recv() {
                    next_screen = Some(BotEkran::Bulundu {
                        bulunan: sonuc,
                        secili: 0,
                        elle_ip: String::new(),
                    });
                } else {
                    ctx.request_repaint_after(std::time::Duration::from_millis(150));
                }
            }
            BotEkran::Baglaniyor { rx, .. } => {
                if let Ok(res) = rx.try_recv() {
                    next_screen = Some(match res {
                        Ok(kod) => BotEkran::KodGeldi { kod },
                        Err(e) => BotEkran::Hata(e),
                    });
                } else {
                    ctx.request_repaint_after(std::time::Duration::from_millis(150));
                }
            }
            BotEkran::OnayBekleniyor(rx) => {
                if let Ok(res) = rx.try_recv() {
                    self.istemci_kapat();
                    self.durum_goster();
                    next_screen = Some(match res {
                        Ok(sina) => BotEkran::Kuruldu { sina },
                        Err(e) => BotEkran::Hata(e),
                    });
                } else {
                    ctx.request_repaint_after(std::time::Duration::from_millis(150));
                }
            }
            _ => {}
        }
        if let Some(s) = next_screen {
            self.ekran = s;
        }

        egui::CentralPanel::default()
            .frame(
                egui::Frame::none()
                    .fill(C::CARD())
                    .inner_margin(egui::Margin::symmetric(22.0, 18.0)),
            )
            .show(ctx, |ui| {
                ui.label(
                    egui::RichText::new("Bot bilgisayarı kurulumu")
                        .font(Fonts::card_t())
                        .color(C::INK()),
                );
                ui.add_space(4.0);
                ui.label(
                    egui::RichText::new(
                        "Bu bilgisayarda UYAP'a istek gönderen bir otomasyon programı çalışıyorsa, \
                         isteklerini ofisteki Çoklu UYAP bilgisayarı üzerinden sıraya alır.",
                    )
                    .font(Fonts::small())
                    .color(C::INK_SOFT()),
                );
                ui.add_space(8.0);
                ui.label(
                    egui::RichText::new(&self.durum_metin)
                        .font(Fonts::body())
                        .color(self.durum_renk),
                );
                ui.add_space(8.0);

                enum Aksiyon {
                    None,
                    Bul,
                    Kaldir,
                    Sina,
                    Baslangic,
                    Baglan(String),
                    Onay(bool),
                    Kapat,
                }
                let mut aksiyon = Aksiyon::None;

                match &mut self.ekran {
                    BotEkran::Baslangic => {
                        ui.label(
                            egui::RichText::new(
                                "1. Ofis bilgisayarında Çoklu UYAP > Sıra Kapısı > “Bot Bilgisayarı Ekle”ye basın.\n\
                                 2. Sonra burada “Ofis Bilgisayarını Bul”a basın.",
                            )
                            .font(Fonts::body())
                            .color(C::INK()),
                        );
                    }
                    BotEkran::Araniyor(_) => {
                        ui.label(
                            egui::RichText::new("Ofis bilgisayarı aranıyor…")
                                .font(Fonts::body())
                                .color(C::INK()),
                        );
                    }
                    BotEkran::Bulundu {
                        bulunan,
                        secili,
                        elle_ip,
                    } => {
                        if !bulunan.is_empty() {
                            ui.label(
                                egui::RichText::new("Bulunan ofis bilgisayarı:")
                                    .font(Fonts::body())
                                    .color(C::INK()),
                            );
                            ui.add_space(4.0);
                            egui::Frame::none()
                                .fill(C::CARD())
                                .stroke(egui::Stroke::new(1.0, C::LINE()))
                                .inner_margin(egui::Margin::same(4.0))
                                .show(ui, |ui| {
                                    ui.set_width(ui.available_width());
                                    for (i, (ip, ad)) in bulunan.iter().enumerate() {
                                        let label = if ad.is_empty() {
                                            ip.clone()
                                        } else {
                                            format!("{ad} ({ip})")
                                        };
                                        let bg = if *secili == i {
                                            C::SAGE_TINT()
                                        } else {
                                            C::CARD()
                                        };
                                        let r = egui::Frame::none()
                                            .fill(bg)
                                            .inner_margin(egui::Margin::symmetric(6.0, 3.0))
                                            .show(ui, |ui| {
                                                ui.set_width(ui.available_width());
                                                ui.label(
                                                    egui::RichText::new(label)
                                                        .font(Fonts::body())
                                                        .color(C::INK()),
                                                );
                                            })
                                            .response
                                            .interact(egui::Sense::click());
                                        if r.clicked() {
                                            *secili = i;
                                        }
                                    }
                                });
                        } else {
                            ui.label(
                                egui::RichText::new(
                                    "Ofis bilgisayarı bulunamadı. Orada “Bot Bilgisayarı Ekle” penceresinin \
                                     açık olduğundan emin olup tekrar deneyin.\n\nAğınız aramaya izin \
                                     vermiyorsa ofis bilgisayarının IP adresini yazabilirsiniz:",
                                )
                                .font(Fonts::small())
                                .color(C::INK_SOFT()),
                            );
                            ui.add_space(6.0);
                            egui::Frame::none()
                                .fill(C::CARD())
                                .stroke(egui::Stroke::new(1.0, C::LINE()))
                                .inner_margin(egui::Margin::symmetric(6.0, 5.0))
                                .show(ui, |ui| {
                                    ui.add(
                                        egui::TextEdit::singleline(elle_ip)
                                            .frame(false)
                                            .desired_width(f32::INFINITY)
                                            .font(Fonts::body()),
                                    );
                                });
                        }
                    }
                    BotEkran::Baglaniyor { ip, .. } => {
                        ui.label(
                            egui::RichText::new(format!("{ip} adresine bağlanılıyor…"))
                                .font(Fonts::body())
                                .color(C::INK()),
                        );
                    }
                    BotEkran::KodGeldi { kod } => {
                        ui.label(
                            egui::RichText::new(
                                "Ofis bilgisayarının ekranında da bir kod göründü.\n\
                                 Aşağıdaki kod onunla AYNI mı?",
                            )
                            .font(Fonts::body())
                            .color(C::INK()),
                        );
                        ui.add_space(12.0);
                        ui.vertical_centered(|ui| {
                            kod_etiketi(ui, kod);
                        });
                        ui.add_space(8.0);
                        ui.label(
                            egui::RichText::new(
                                "Evet dedikten sonra Windows bir onay penceresi gösterecek; 'Evet' deyin.",
                            )
                            .font(Fonts::small())
                            .color(C::INK_SOFT()),
                        );
                    }
                    BotEkran::OnayBekleniyor(_) => {
                        ui.label(
                            egui::RichText::new("Ofis bilgisayarındaki onay bekleniyor…")
                                .font(Fonts::body())
                                .color(C::INK()),
                        );
                    }
                    BotEkran::Kuruldu { sina } => {
                        let ek = match sina.as_str() {
                            "tamam" => "Kapıya bağlantı denendi: çalışıyor.",
                            "kapali" => {
                                "Ofis bilgisayarında paylaşım şu an kapalı; açıldığında bot oradan geçecek."
                            }
                            "sertifika" => {
                                "Uyarı: kapıyla güvenli bağlantı kurulamadı. Ofis bilgisayarını kontrol edin."
                            }
                            _ => "",
                        };
                        ui.label(
                            egui::RichText::new(format!(
                                "Kurulum tamamlandı. Bot programı artık UYAP'a ofis bilgisayarı \
                                 üzerinden gidecek.\n\n{ek}"
                            ))
                            .font(Fonts::body())
                            .color(C::SAGE_DK()),
                        );
                        ui.add_space(10.0);
                        ui.label(
                            egui::RichText::new(
                                "Not: Ofis bilgisayarı kapalıyken ya da paylaşım durdurulduğunda bot UYAP'a \
                                 ulaşamaz. Bu bilgisayardan tarayıcıyla UYAP'a girenler de ofis bilgisayarı \
                                 üzerinden gider.",
                            )
                            .font(Fonts::small())
                            .color(C::INK_SOFT()),
                        );
                    }
                    BotEkran::Hata(metin) => {
                        ui.label(
                            egui::RichText::new(metin.as_str())
                                .font(Fonts::body())
                                .color(C::CLAY()),
                        );
                    }
                }

                // Alt düğme çubuğu
                ui.with_layout(egui::Layout::bottom_up(egui::Align::LEFT), |ui| {
                    ui.horizontal(|ui| {
                        match &self.ekran {
                            BotEkran::Baslangic => {
                                if self.bagli {
                                    if RoundButton::new("Kurulumu Kaldır")
                                        .kind(ButtonKind::Ghost)
                                        .font(Fonts::nav_b())
                                        .height(34.0)
                                        .show(ui)
                                        .clicked()
                                    {
                                        aksiyon = Aksiyon::Kaldir;
                                    }
                                    ui.add_space(8.0);
                                    if RoundButton::new("Bağlantıyı Sına")
                                        .kind(ButtonKind::Ghost)
                                        .font(Fonts::nav_b())
                                        .height(34.0)
                                        .show(ui)
                                        .clicked()
                                    {
                                        aksiyon = Aksiyon::Sina;
                                    }
                                }
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        if RoundButton::new("Ofis Bilgisayarını Bul")
                                            .kind(ButtonKind::Primary)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Bul;
                                        }
                                    },
                                );
                            }
                            BotEkran::Bulundu {
                                bulunan,
                                secili,
                                elle_ip,
                            } => {
                                if RoundButton::new("Geri")
                                    .kind(ButtonKind::Ghost)
                                    .font(Fonts::nav_b())
                                    .height(34.0)
                                    .show(ui)
                                    .clicked()
                                {
                                    aksiyon = Aksiyon::Baslangic;
                                }
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        if RoundButton::new("Bağlan")
                                            .kind(ButtonKind::Primary)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            let mut ip = elle_ip.trim().to_string();
                                            if ip.is_empty() && !bulunan.is_empty() {
                                                let idx = (*secili).min(bulunan.len() - 1);
                                                ip = bulunan[idx].0.clone();
                                            }
                                            if !ip.is_empty() {
                                                aksiyon = Aksiyon::Baglan(ip);
                                            }
                                        }
                                        ui.add_space(8.0);
                                        if RoundButton::new("Tekrar Ara")
                                            .kind(ButtonKind::Ghost)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Bul;
                                        }
                                    },
                                );
                            }
                            BotEkran::KodGeldi { .. } => {
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        if RoundButton::new("Evet, aynı")
                                            .kind(ButtonKind::Primary)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Onay(true);
                                        }
                                        ui.add_space(8.0);
                                        if RoundButton::new("Hayır, farklı")
                                            .kind(ButtonKind::Ghost)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Onay(false);
                                        }
                                    },
                                );
                            }
                            BotEkran::Kuruldu { .. } => {
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        if RoundButton::new("Kapat")
                                            .kind(ButtonKind::Primary)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Kapat;
                                        }
                                    },
                                );
                            }
                            BotEkran::Hata(_) => {
                                ui.with_layout(
                                    egui::Layout::right_to_left(egui::Align::Center),
                                    |ui| {
                                        if RoundButton::new("Baştan Başla")
                                            .kind(ButtonKind::Primary)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Baslangic;
                                        }
                                        ui.add_space(8.0);
                                        if RoundButton::new("Kapat")
                                            .kind(ButtonKind::Ghost)
                                            .font(Fonts::nav_b())
                                            .height(34.0)
                                            .show(ui)
                                            .clicked()
                                        {
                                            aksiyon = Aksiyon::Kapat;
                                        }
                                    },
                                );
                            }
                            _ => {}
                        }
                    });
                });

                match aksiyon {
                    Aksiyon::None => {}
                    Aksiyon::Baslangic => {
                        self.ekran = BotEkran::Baslangic;
                    }
                    Aksiyon::Kapat => {
                        self.istemci_kapat();
                        self.kapandi = true;
                    }
                    Aksiyon::Bul => {
                        let (tx, rx) = mpsc::channel();
                        std::thread::spawn(move || {
                            let _ = tx.send(se::ofisleri_bul(2.0));
                        });
                        self.ekran = BotEkran::Araniyor(rx);
                    }
                    Aksiyon::Baglan(ip) => match se::EslestirmeIstemcisi::new(&ip) {
                        Ok(ic) => {
                            let arc_ic = Arc::new(std::sync::Mutex::new(ic));
                            self.istemci = Some(Arc::clone(&arc_ic));
                            let (tx, rx) = mpsc::channel();
                            std::thread::spawn(move || {
                                let res = arc_ic
                                    .lock()
                                    .unwrap()
                                    .baglan()
                                    .map_err(|e| e.to_string());
                                let _ = tx.send(res);
                            });
                            self.ekran = BotEkran::Baglaniyor { ip, rx };
                        }
                        Err(e) => {
                            msg_error("Bot kurulumu", &e.to_string());
                        }
                    },
                    Aksiyon::Onay(evet) => {
                        if !evet {
                            if let Some(ic) = &self.istemci {
                                if let Ok(mut guard) = ic.lock() {
                                    let _ = guard.karar_bildir_ve_bekle(false);
                                }
                            }
                            self.istemci_kapat();
                            self.ekran = BotEkran::Hata(
                                "Kodlar farklıysa ağda başka biri araya girmeye çalışıyor olabilir. \
                                 Kurulum yapılmadı; ofis yöneticinize haber verin."
                                    .to_string(),
                            );
                        } else if let Some(ic) = self.istemci.clone() {
                            let (tx, rx) = mpsc::channel();
                            std::thread::spawn(move || {
                                let res = (|| -> Result<String, String> {
                                    let (ok, kapi_ip, cer) = {
                                        let mut guard = ic.lock().unwrap();
                                        let ok = guard
                                            .karar_bildir_ve_bekle(true)
                                            .map_err(|e| e.to_string())?;
                                        (ok, guard.kapi_ip.clone(), guard.cer.clone().unwrap_or_default())
                                    };
                                    if !ok {
                                        return Err("Ofis bilgisayarında onaylanmadı.".to_string());
                                    }
                                    let cer_b64 = base64::engine::general_purpose::STANDARD.encode(&cer);
                                    let kod = se::yonetici_olarak_calistir(&[
                                        "bot-kur",
                                        &kapi_ip,
                                        &cer_b64,
                                    ])
                                    .map_err(|e| yonetici_hata_metni(&e))?;
                                    if kod != 0 {
                                        return Err(se::cikis_metni(kod).to_string());
                                    }
                                    Ok(se::kapi_sina(&kapi_ip, Some(&cer), 5.0))
                                })();
                                let _ = tx.send(res);
                            });
                            self.ekran = BotEkran::OnayBekleniyor(rx);
                        }
                    }
                    Aksiyon::Kaldir => {
                        if msg_ask_yes_no(
                            "Bot kurulumu",
                            "Sıra kapısı bu bilgisayardan kaldırılsın mı?\n\
                             Bot yeniden doğrudan UYAP'a bağlanır.\n\n\
                             Windows bir onay penceresi gösterecek; 'Evet' deyin.",
                        ) {
                            let (tx, rx) = mpsc::channel();
                            self.kaldir_rx = Some(rx);
                            std::thread::spawn(move || {
                                let _ = tx.send(se::yonetici_olarak_calistir(&["bot-kaldir"]));
                            });
                        }
                    }
                    Aksiyon::Sina => {
                        if let Some(ip) = se::hosts_kapi_ip(None) {
                            let (tx, rx) = mpsc::channel();
                            self.sina_rx = Some(rx);
                            std::thread::spawn(move || {
                                let _ = tx.send(se::kapi_sina(&ip, None, 5.0));
                            });
                        }
                    }
                }
            });
    }
}
