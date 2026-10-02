# -*- coding: utf-8 -*-
"""
İcra veri modeli — kapak künyesi + taraf zinciri (rol bazlı, tekil taraf)
=========================================================================
Zincir (kullanıcının istediği sorgular):
  • Bir alacaklının TÜM borçluları  → alacaklı tarafın bulunduğu dosyalardaki
    borçlu taraflar.
  • Bir borçlunun TÜM dosyaları       → borçlu tarafın dosya bağları.
Bu, ayrı bir 'Alacaklı→Borçlu→Dosya' FK zinciri yerine TEK 'Taraf' tablosu
(gerçek/tüzel, TCKN ya da MERSIS/Vergi ile tekil) + 'DosyaTaraf' bağ tablosu
(rol = alacaklı/borçlu, vekil) ile sağlanır; aynı kişi tekrar yazılmaz.

Şimdilik UYAP 'search_phrase_detayli' yanıtı yalnızca KAPAK KÜNYESİNİ verir
(Dosya + Birim). Taraf/Vekil verisi dosya detay sorgusundan gelince doldurulur;
modeller şimdiden hazır.
"""
from django.db import models
from django.utils import timezone


class YargiTuru(models.IntegerChoices):
    """UYAP'ın sabit Yargı Türü enum'u (`yargiBirimleriSorgula_brd.ajx`
    dropdown'ından canlı doğrulandı — bkz.
    docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.1; büyümez, ön yüzde 8 sabit
    seçenek). `Birim.yargi_turu` ve `Dosya.yargi_turu` AYNI bu enum'u
    kullanır — iki alan da aynı kod kümesini paylaşır, farklı anlam
    taşımaz."""
    CEZA = 0, "Ceza"
    HUKUK = 1, "Hukuk"
    ICRA = 2, "İcra"
    CBS = 3, "Cbs"
    IDARI_YARGI = 6, "İdari Yargı"
    SATIS_MEMURLUGU = 11, "Satış Memurluğu"
    ARABULUCULUK = 25, "Arabuluculuk"
    TAZMINAT_KOMISYONU = 26, "Tazminat Komisyonu Başkanlığı"


class Birim(models.Model):
    """İcra dairesi / birim / mahkeme — kapak künyesinin birim kısmı.
    turu1/turu2/turu3, UYAP yanıt kaydının KENDİ 'birimTuru1/2/3' alanlarıdır
    (örn. icra için "11"/"1101"/"1199") — bunlar arama sorgusunda gönderilen
    'yargiTuru' parametresiyle AYNI ANLAMA GELMEZ (bkz.
    docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.6, ingest.py okunarak
    doğrulandı). Yargı türü (Ceza/Hukuk/İcra/... — search_phrase_detayli.ajx
    payload'ındaki 'birimTuru3') bu yüzden AYRI bir alanla tutulur."""
    birim_id = models.CharField("UYAP Birim No", max_length=32, unique=True)
    ad = models.CharField("Birim Adı", max_length=255)
    turu1 = models.CharField("birimTuru1", max_length=8, blank=True)
    turu2 = models.CharField("birimTuru2", max_length=8, blank=True)   # 1101 = İcra Dairesi
    turu3 = models.CharField("birimTuru3", max_length=8, blank=True)
    yargi_turu = models.PositiveSmallIntegerField(
        "Yargı Türü Kodu", choices=YargiTuru.choices, null=True, blank=True, db_index=True)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Birim"
        verbose_name_plural = "Birimler"
        ordering = ["ad"]

    def __str__(self):
        return self.ad


class YargiBirimi(models.Model):
    """Yargı Birimi (mahkeme türü) referans listesi — yargiBirimleriSorgula_brd.ajx
    yanıtından (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.2). 'kod' alanı
    UYAP'ın 'tablo' alanıdır (arama sorgusunda birimTuru2 olarak gönderilir),
    'ad' ise UYAP'ın (ters adlandırılmış) 'kod' alanıdır (görünen isim)."""
    yargi_turu = models.PositiveSmallIntegerField("Yargı Türü Kodu", db_index=True)
    kod = models.CharField("Birim Türü Kodu (tablo)", max_length=8)
    ad = models.CharField("Birim Türü Adı", max_length=120)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Yargı Birimi"
        verbose_name_plural = "Yargı Birimleri"
        ordering = ["yargi_turu", "ad"]
        constraints = [
            models.UniqueConstraint(fields=["yargi_turu", "kod"], name="uq_yargi_birimi"),
        ]

    def __str__(self):
        return f"{self.ad} ({self.kod})"


class SenkronKapsami(models.Model):
    """Kullanıcının senkronize edilmesini istediği (yargı türü, yargı birimi
    türü) kombinasyonu. Hiç kayıt yoksa hiçbir şey otomatik senkron edilmez —
    kullanıcı en az bir kombinasyon eklemeli. yargi_birimi_kod boşsa o yargı
    türünün TAMAMI kapsam dahilindedir."""
    yargi_turu = models.PositiveSmallIntegerField("Yargı Türü Kodu")
    yargi_birimi_kod = models.CharField("Yargı Birimi Kodu (tablo)", max_length=8, blank=True)
    aktif = models.BooleanField(default=True)
    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Senkron Kapsamı"
        verbose_name_plural = "Senkron Kapsamları"
        constraints = [
            models.UniqueConstraint(fields=["yargi_turu", "yargi_birimi_kod"],
                                    name="uq_senkron_kapsami"),
        ]

    def __str__(self):
        return f"Yargı Türü {self.yargi_turu} · {self.yargi_birimi_kod or 'tümü'}"


class Taraf(models.Model):
    """Alacaklı veya borçlu olabilen kişi/kurum. Kimlikle TEKİLdir; aynı kişi
    farklı dosyalarda tekrar yazılmaz (dosyalara DosyaTaraf ile bağlanır)."""

    class Tur(models.TextChoices):
        GERCEK = "gercek", "Gerçek Kişi"
        TUZEL = "tuzel", "Tüzel Kişi"

    tur = models.CharField(max_length=8, choices=Tur.choices)

    # — gerçek kişi —
    ad = models.CharField(max_length=120, blank=True)
    soyad = models.CharField(max_length=120, blank=True)
    tckn = models.CharField("TCKN", max_length=11, blank=True, null=True, unique=True)

    # — tüzel kişi —
    unvan = models.CharField("Unvan / Şirket Adı", max_length=400, blank=True)
    mersis_no = models.CharField("MERSIS No", max_length=20, blank=True, null=True, unique=True)
    vergi_no = models.CharField("Vergi No", max_length=15, blank=True, db_index=True)
    e_tebligat_adresi = models.CharField("E-Tebligat Adresi", max_length=255, blank=True)
    kep_adresi = models.CharField("KEP Adresi", max_length=255, blank=True)

    # — ortak —
    adres = models.TextField(blank=True)
    iban = models.CharField(max_length=34, blank=True)

    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Taraf"
        verbose_name_plural = "Taraflar"

    def __str__(self):
        if self.tur == self.Tur.TUZEL:
            return self.unvan or self.mersis_no or self.vergi_no or f"Taraf #{self.pk}"
        return (f"{self.ad} {self.soyad}".strip()
                or self.tckn or f"Taraf #{self.pk}")


class Vekil(models.Model):
    """Bir tarafın vekili (avukat). Tarafa DosyaTaraf üzerinden bağlanır."""
    ad = models.CharField(max_length=120)
    soyad = models.CharField(max_length=120, blank=True)
    tckn = models.CharField("TCKN", max_length=11, blank=True, null=True, unique=True)
    baro_sicil = models.CharField("Baro Sicil No", max_length=40, blank=True)
    adres = models.TextField(blank=True)
    iban = models.CharField(max_length=34, blank=True)
    olusturulma = models.DateTimeField(auto_now_add=True)
    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Vekil"
        verbose_name_plural = "Vekiller"

    def __str__(self):
        return f"{self.ad} {self.soyad}".strip() or f"Vekil #{self.pk}"


class DosyaQuerySet(models.QuerySet):
    """Programın her yerinden aynı kümeye erişim: `Dosya.objects.icra()`,
    `Dosya.objects.icra().acik()` (2026-09-25 kullanıcı isteği — iş listesi,
    toplu aksiyonlar, dosya kartı hep bu tanımı kullanır)."""

    def yargi(self, tur):
        return self.filter(yargi_turu=int(tur))

    def icra(self):
        return self.filter(yargi_turu=YargiTuru.ICRA)

    def acik(self):
        return self.filter(durum_kod=0)

    def kapali(self):
        return self.filter(durum_kod=1)


class Dosya(models.Model):
    """İcra dosyası kapak künyesi: İcra Dairesi + Yıl + Dosya No, durum, tür,
    açılış tarihi. KALICI tekil anahtar = (birim, yıl, sıra_no, tür_kod)
    [uq_dosya_kunye]. UYAP'ın 'dosyaId' değeri OTURUMLUKtur (her sorguda değişebilir),
    kimlik DEĞİLDİR; yalnız o oturumda taraf/detay çekmek için saklanır."""

    class Durum(models.IntegerChoices):
        """UYAP'ın kendi 'dosyaDurumKod' değerleri — OLDUĞU GİBİ saklanır
        (bkz. ingest.py, choices yalnız görüntüleme içindir). Kod anlamı
        yargı türüne göre değişebilir (bkz.
        docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §5) — burada yalnız canlı
        doğrulanan değerler var, tam liste değildir."""
        ACIK = 0, "Açık"
        KAPALI = 1, "Kapalı"
        YARGITAYDA = 3, "Yargıtayda"
        KARARA_CIKMIS = 7, "Karara Çıkmış"
        YARGITAYDAN_DONDU = 16, "Yargıtaydan Döndü"
        ISTINAFTA = 29, "İstinafta"
        # 3/16/29/31 YALNIZ Ceza (Ağır Ceza Mahkemesi) dosyalarında canlı
        # görüldü (2026-09-22, geniş tarama) — diğer yargı türlerinde AYNI
        # koda farklı anlam gelebileceği bkz. sınıf docstring'i UYARISI
        # hâlâ geçerli, buraya eklenmeleri "her yerde bu anlama gelir"
        # DEMEK DEĞİL.
        ISTINAFTAN_KISMI_DONDU = 31, "İstinaftan Kısmi Döndü"

    class Tur(models.IntegerChoices):
        """UYAP'ın kendi 'dosyaTurKod' değerleri — OLDUĞU GİBİ saklanır (bkz.
        ingest.py). Yalnız canlı doğrulanan değerler; tam liste değildir
        (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §5)."""
        ESAS = 0, "Esas"
        TALIMAT = 1, "Talimat"
        CEZA_DAVA = 3, "Ceza Dava Dosyası"  # 2026-09-22 geniş taramada canlı bulundu
        HUKUK_DEGISIK_IS = 14, "Hukuk Değişik İş Dosyası"
        HUKUK_DAVA = 15, "Hukuk Dava Dosyası"
        CBS_SORUSTURMA = 16, "CBS Soruşturma Dosyası"
        ICRA_DOSYASI = 35, "İcra Dosyası"  # 2026-09-22'de canlı bulundu — İcra'nın ANA dosya türü kodu, şimdiye dek hiç eklenmemişti

    dosya_id = models.CharField("UYAP Dosya Kimliği (oturumluk)", max_length=255,
                                blank=True, db_index=True)
    birim = models.ForeignKey(Birim, on_delete=models.PROTECT, related_name="dosyalar")

    # DOĞRUDAN Dosya satırında (yalnız `birim.yargi_turu` üzerinden DOLAYLI
    # değil) — kullanıcı isteği, 2026-09-22: yargı türleri arasında (Ceza/
    # Hukuk/İcra/İdari Yargı/Satış Memurluğu/Arabuluculuk/Cbs/Tazminat
    # Komisyonu) veri ÇARPIŞMASI olmasın diye her dosyanın hangi türe ait
    # olduğu kendi satırında da sabitlenir. `ingest.dosya_kunyesi_kaydet`'e
    # zaten geçirilen (aramanın hangi kapsamdan geldiğini bilen) `yargi_turu`
    # parametresinden yazılır — UYDURULMAZ. `birim.yargi_turu`'na EK/yedek
    # bir kopyadır, onun yerine geçmez (bir birim her zaman tek bir yargı
    # türüne ait olduğundan ikisi ÇAKIŞMAZ); asıl amacı sorgu/filtrelemeyi
    # join'e muhtaç bırakmadan basit ve yanlışa kapalı hale getirmek.
    # 2026-09-25: ZORUNLU (null yok). 44.649 dosyanın hepsinde doluydu; eski
    # "boşsa birimden / birimTuru2=1101'den tanı" yedek kuralları kaldırıldı —
    # İcra dosyası = `Dosya.objects.icra()` (yargi_turu=İcra), tek tanım.
    yargi_turu = models.PositiveSmallIntegerField(
        "Yargı Türü Kodu", choices=YargiTuru.choices, db_index=True)

    objects = DosyaQuerySet.as_manager()

    yil = models.PositiveIntegerField("Dosya Yılı")
    sira_no = models.PositiveIntegerField("Dosya No")
    # max_length=20: yalnız İcra'nın "2025/237" biçimiyle canlı doğrulandı.
    # "Yenile" artık her yargı türünü tarayabildiği (tum_turler) için AYNI hata
    # sınıfı (bkz. durum/tur/DosyaTaraf.rol yorumları) burada da çıktı — henüz
    # canlı doğrulanmamış türlerin dosyaNo biçimi 20 karakteri aşabiliyor.
    # Aynı gerekçeyle Birim.ad ile aynı bol paya (255) çıkarıldı.
    dosya_no = models.CharField("Dosya No (ham)", max_length=255)

    durum_kod = models.IntegerField(choices=Durum.choices, default=Durum.ACIK)
    # 'dosyaDurum' UYAP'ın kendi serbest metin etiketidir (choices DEĞİL,
    # durum_kod'un görünen adı) — sabit bir kelime kümesi değil, yargı türüne
    # göre değişen açıklamalar da olabilir. Daha önce AYNI hata sınıfı
    # DosyaTaraf.rol'de yaşandı (bkz. migration 0005/0006, "Üçüncü Şahıs" gibi
    # 10 karakteri aşan roller PostgreSQL DataError'a sebep olmuştu) — durum/tur
    # da aynı riski taşıdığı için Birim.ad ile aynı bol payla (255) tutulur.
    durum = models.CharField(max_length=255, blank=True)
    tur_kod = models.IntegerField(choices=Tur.choices, default=Tur.ESAS)
    tur = models.CharField(max_length=255, blank=True)

    acilis_tarihi = models.DateTimeField("Açılış Tarihi", null=True, blank=True)
    is_dava_dosyasi_acilmis = models.BooleanField(default=False)

    taraflar = models.ManyToManyField(Taraf, through="DosyaTaraf", related_name="dosyalar")

    # Büronun KENDİ iş işareti (2026-10-02 kullanıcı isteği) — UYAP'tan GELMEZ,
    # senkron `dosya_defaults`'a hiç yazmaz. Tek otomatik kural: dosya UYAP'ta
    # Açık→Kapalı'ya geçtiği anda Pasif'e düşer (ingest.dosya_kunyesi_kaydet);
    # sonrasında elle Aktif'e alınırsa her senkronda yeniden ezilmez. Toplu
    # turlar bu alana göre ŞİMDİLİK filtrelemez (kullanıcı kararı: yalnız model).
    class IslemDurumu(models.TextChoices):
        AKTIF = "aktif", "Aktif - işlem yapılabilir"
        PASIF = "pasif", "Pasif - işlem yapma"
        BEKLEMEDE = "beklemede", "Beklemede"

    islem_durumu = models.CharField("İşlem Durumu", max_length=12, choices=IslemDurumu.choices,
                                    default=IslemDurumu.AKTIF, db_index=True)
    islem_durumu_notu = models.TextField("İşlem Durumu Notu / Gerekçe", blank=True, default="")
    islem_durumu_tarihi = models.DateTimeField("İşlem Durumu Değişiklik Tarihi", null=True, blank=True)
    # Yalnız Beklemede'de anlamlı; başka duruma geçince temizlenir.
    bekleme_bitis_tarihi = models.DateField("Bekleme Bitiş Tarihi", null=True, blank=True)

    son_senkron = models.DateTimeField(auto_now=True)
    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dosya"
        verbose_name_plural = "Dosyalar"
        ordering = ["-acilis_tarihi"]
        constraints = [
            models.UniqueConstraint(fields=["birim", "yil", "sira_no", "tur_kod"],
                                    name="uq_dosya_kunye"),
        ]

    def __str__(self):
        return f"{self.birim.ad} {self.yil}/{self.sira_no} ({self.get_tur_kod_display()})"

    def islem_durumu_ata(self, durum, notu="", bekleme_bitis=None, kaydet=True):
        """İşlem durumunu değiştirir; tarih şimdi olur, bekleme bitişi yalnız
        Beklemede'de tutulur. kaydet=True ise yalnız bu dört alan yazılır
        (künye alanlarına dokunulmaz)."""
        self.islem_durumu =self.IslemDurumu(durum)
        self.islem_durumu_notu = notu or ""
        self.islem_durumu_tarihi = timezone.now()
        self.bekleme_bitis_tarihi = (bekleme_bitis if self.islem_durumu == self.IslemDurumu.BEKLEMEDE
                                     else None)
        if kaydet:
            self.save(update_fields=["islem_durumu", "islem_durumu_notu",
                                     "islem_durumu_tarihi", "bekleme_bitis_tarihi"])


class DosyaTaraf(models.Model):
    """Dosya ↔ Taraf bağı: rol (alacaklı/borçlu) ve (varsa) vekil. Zincirin
    kalbi burasıdır."""

    class Rol(models.TextChoices):
        """Kod değerleri tr_lower()'ın canlı yakalanan UYAP 'rol' string'lerine
        uyguladığı çıktıdır (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §8) —
        UYDURULMADI, her biri gerçek bir dosyada canlı doğrulandı: İcra/Hukuk/
        İdari Yargı/Satış Memurluğu/Arabuluculuk. Ceza/İdari Yargı için ayrı
        canlı doğrulama (2026-07-11). KATILAN/VASI/HACIZ_ALACAKLISI Chrome ağ
        yakalamasıyla EK canlı doğrulama (2026-09-22, dosya_taraf_bilgileri_
        brd.ajx — Ceza Dava Dosyası ve Satış Memurluğu örnekleri, Dosya.
        yargi_turu çalışmasının devamı)."""
        ALACAKLI = "alacakli", "Alacaklı"
        BORCLU = "borclu", "Borçlu"
        DAVACI = "davaci", "Davacı"
        DAVALI = "davali", "Davalı"
        SANIK = "sanik", "Sanık"
        MUSTEKI = "musteki", "Müşteki"
        UCUNCU_SAHIS = "ucuncu sahis", "Üçüncü Şahıs"
        HISSEDAR = "hissedar", "Hissedar"
        TASFIYE_MEMURU = "tasfiye memuru", "Tasfiye Memuru"
        BASVURUCU = "basvurucu", "Başvurucu"
        DIGER_TARAF = "diger taraf", "Diğer Taraf"
        KATILAN = "katilan", "Katılan"                      # Ceza — mağdur/suçtan zarar gören taraf
        VASI = "vasi", "Vasi"                                # Satış Memurluğu — kısıtlının yasal temsilcisi
        HACIZ_ALACAKLISI = "haciz alacaklisi", "Haciz Alacaklısı"  # Satış Memurluğu — 'Alacaklı'dan AYRI rol

    class KesinlesmeDurumu(models.TextChoices):
        """Borçlu bazlı kesinleşme durumu — aynı dosyada birden fazla borçlu
        varsa her biri kendi DosyaTaraf satırında AYRI değer taşır (kullanıcı
        isteği, 2026-07-13). Şimdilik yalnız alan tanımı: girişi UYAP'tan
        OTOMATİK ÇEKİLMİYOR, ileride yazılacak bir kesinleşme-girişi
        fonksiyonuyla elle/hesapla doldurulacak — bu yüzden varsayılan boş.
        Seçenek kümesi kullanıcı isteğiyle 2026-08-03'te güncellendi; bu
        değerlere ilişkin kurallar (hangi durumda hangisinin seçileceği)
        henüz belirlenmedi — bkz. alan tanımı üstündeki not."""
        KESINLESTI = "kesinlesti", "Kesinleşti"
        KESINLESME_BEKLIYOR = "kesinlesme_bekliyor", "Kesinleşme Bekliyor"
        # 2026-09-30: dosya durumu "Açık (Durdurulmuş : Takibe İtiraz)" ise
        # dosyanın tüm borçluları (bkz. dosya_core.DURDURMA_NEDENLERI).
        ITIRAZ = "itiraz", "İtiraz"
        # Elle girilir (hesap ezmez).
        KONKORDATO = "konkordato", "Konkordato"
        VEFAT = "vefat", "Vefat"
        VASI = "vasi", "Vasi"
        # 2026-09-30 kullanıcı isteği: İcra dosya durumu "Açık (Durdurulmuş : X)"
        # ise dosyanın TÜM borçluları X olur (iş tespiti + filtre). Metinler
        # canlı DB'deki UYAP değerleridir (bkz. dosya_core.DURDURMA_NEDENLERI);
        # listede olmayan yeni bir neden DURDURULMUS_TANIMSIZ olur ve loglanır.
        DURDURULMUS_ICRA_MAH_ITIRAZ = "durd_icra_mah_itiraz", "Durdurulmuş: İcra Mahkemesine İtiraz"
        DURDURULMUS_GENEL_MAH_DAVA = "durd_genel_mah_dava", "Durdurulmuş: Genel Mahkemeye Dava Açılması"
        DURDURULMUS_TEHIRI_ICRA = "durd_tehiri_icra", "Durdurulmuş: Tehir-i İcra Kararı"
        DURDURULMUS_IHTIYATI_TEDBIR = "durd_ihtiyati_tedbir", "Durdurulmuş: İhtiyati Tedbir"
        DURDURULMUS_IFLAS = "durd_iflas", "Durdurulmuş: İflas"
        DURDURULMUS_BORCLUNUN_OLUMU = "durd_borclu_olumu", "Durdurulmuş: Borçlunun Ölümü"
        DURDURULMUS_DIGER = "durd_diger", "Durdurulmuş: Diğer"
        DURDURULMUS_TANIMSIZ = "durd_tanimsiz", "Durdurulmuş: Tanımsız Neden"

    class TebligatDurumu(models.TextChoices):
        """Borçlu bazlı tebliğ durumu (kullanıcı isteği, 2026-08-03) —
        KesinlesmeDurumu ile AYNI desen: aynı dosyada birden fazla borçlu
        varsa her biri kendi DosyaTaraf satırında AYRI değer taşır. Kaynak
        veri barkod_sorgu.py'nin PTT sorgusundan (TebligatBarkod.ptt_durumu,
        serbest metin) geliyor ama bu alan OTOMATİK türetilmiyor — hangi PTT
        durumunun hangi seçeneğe eşleneceğine ve "yeniden tebliğ talep
        edilecek/edildi" gibi elle işlenen iş akışı durumlarının nasıl
        tetikleneceğine ilişkin kurallar henüz belirlenmedi (kullanıcı,
        2026-08-03: "Bu veri türlerine ilişkin kuralları sonra
        belirleyeceğiz"). Şimdilik yalnız alan tanımı, varsayılan boş."""
        TEBLIG_EDILDI = "teblig_edildi", "Tebliğ Edildi"
        IADE = "iade", "İade"
        DAGITIMDA = "dagitimda", "Dağıtımda"
        MERNIS_YOK = "mernis_yok", "Mernis Yok"
        YENIDEN_TEBLIG_TALEP_EDILECEK = "yeniden_teblig_talep_edilecek", "Yeniden Tebliğ Talep Edilecek"
        YENIDEN_TEBLIG_TALEP_EDILDI = "yeniden_teblig_talep_edildi", "Yeniden Tebliğ Talep Edildi"

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="taraf_baglari")
    taraf = models.ForeignKey(Taraf, on_delete=models.PROTECT, related_name="dosya_baglari")
    # max_length=20 AYNI hata sınıfını (bkz. migration 0005/0006) 'tüm türler'
    # taramasında TEKRAR verdi (kullanıcı bulgusu, 2026-07-11): "Dosyalarım
    # (Tümü)" artık her yargı türünü tarıyor, henüz canlı doğrulanmamış
    # türlerin rol metinleri 20 karakteri aşabiliyor. durum/tur ile AYNI
    # gerekçeyle (bkz. Dosya.durum yorumu) Birim.ad'la aynı bol paya (255)
    # çıkarıldı — choices yalnız görüntüleme/bilinen değerler içindir, DB
    # düzeyinde zorlanmaz.
    rol = models.CharField(max_length=255, choices=Rol.choices)
    # ManyToMany (TEKİL ForeignKey DEĞİL): kullanıcı bulgusu (2026-07-11) —
    # UYAP'ın taraf_bilgileri yanıtında bir tarafın BİRDEN FAZLA vekili olabilir
    # ("[AD1, AD2]" — bkz. dosya_core._vekiller_kaydet). Eskiden tekil FK olduğu
    # için yalnız ilk vekil kaydedilip diğerleri sessizce atılıyordu.
    vekiller = models.ManyToManyField(Vekil, blank=True, related_name="temsil_baglari")
    sira = models.PositiveIntegerField(default=0)

    # Yalnız rol=borclu için anlamlıdır (alacaklı/diğer rollerde boş kalır).
    # Şimdilik hep boş yazılır — UYAP'tan uydurulmaz, ileride kesinleşme-girişi
    # fonksiyonu ile doldurulacak (kullanıcı isteği, 2026-07-13).
    kesinlesme_durumu = models.CharField("Kesinleşme Durumu", max_length=30,
                                         choices=KesinlesmeDurumu.choices, blank=True)
    # 2026-09-30 kullanıcı isteği: kesinleşme günü (itiraz süresinin bittiği
    # günden sonraki ilk iş günü, bkz. kesinlesme_core). Kesinleşti'de gerçekleşen,
    # Kesinleşme Bekliyor'da tebliğ tarihi biliniyorsa beklenen gün; itirazda boş.
    kesinlesme_tarihi = models.DateField("Kesinleşme Tarihi", null=True, blank=True)
    # Yalnız rol=borclu için anlamlıdır (alacaklı/diğer rollerde boş kalır).
    # kesinlesme_durumu ile AYNI gerekçeyle şimdilik hep boş — bkz.
    # TebligatDurumu tanımı üstündeki not (kullanıcı isteği, 2026-08-03).
    # 2026-09-25 kullanıcı kararı: "Tebliğ durumu borçluya tebliğ ve kesinleşme
    # için önem verdiğimiz değer — her borçlu için ayrı, ÖDEME EMRİ için."
    # Artık `dosya_core.kesinlesme_durumlarini_guncelle` borçluya bağlı ödeme
    # emri barkodlarından yazar (bkz. `_borclu_tebligat_hesapla`); tarih
    # tebliğ/iade tarihidir (mazbatadan gelip tarihi bilinmeyende boş).
    tebligat_durumu = models.CharField("Tebliğ Durumu", max_length=30,
                                        choices=TebligatDurumu.choices, blank=True)
    tebligat_durum_tarihi = models.DateField("Tebliğ Durumu Tarihi", null=True, blank=True)
    # Tebliğ durumunu veren tebligatın teslim şekli / iade nedeni (TebligatBarkod.teslim_sekli).
    tebligat_sekli = models.CharField("Teslim Şekli / İade Nedeni", max_length=150, blank=True, default="")

    # Kesinleşme hesabının (bkz. kesinlesme_core.py) girdisi — UYAP'ta itiraz
    # tespiti için canlı doğrulanmış bir evrak-türü eşlemesi YOK (kullanıcı
    # kararı, 2026-08-03: elle işaretleme), bu yüzden burada da UYAP'tan
    # otomatik doldurulmaz, yalnız kullanıcı elle işaretler.
    itiraz_edildi_mi = models.BooleanField("İtiraz Edildi mi", default=False)
    itiraz_tarihi = models.DateField("İtiraz Tarihi", null=True, blank=True)

    class Meta:
        verbose_name = "Dosya Tarafı"
        verbose_name_plural = "Dosya Tarafları"
        ordering = ["dosya", "rol", "sira"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "taraf", "rol"],
                                    name="uq_dosya_taraf_rol"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.get_rol_display()}: {self.taraf}"


class IcraTakipDetay(models.Model):
    """İcra/takip dosyasına özgü ayrıntı — dosyaAyrintiBilgileri_brd.ajx
    yanıtından (bkz. docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.5/§2.5). Hukuk
    dava dosyalarında bu alanlar hiç YOK — bu yüzden ayrı bir model (ortak
    tek tabloda hep NULL kalacak alanlar yerine).

    2026-09-22 geniş İcra taramasında (İcra Dairesi, hem "İlamsız Takip" hem
    "İlamlı Takip" örnekleriyle) CANLI doğrulandı: `takibin_turu` alanı
    (UYAP'ın kendi 'takibinTuru' kodu, "0"/"1"/...) İlamsız/İlamlı Takip gibi
    ANA takip türünü ayırt eder; `takibin_yolu`/`takibin_sekli` ise o ana tür
    İÇİNDEKİ alt kategoriyi (ör. İlamsız'da "Genel Haciz Yoluyla Takip",
    İlamlı'da "İlamların İcrası, Para ve Teminat Verilmesi Hakkındaki
    İlamların İcrası"). Kullanıcı uyarısı (2026-09-22): İcra'da bundan başka
    (Kambiyo Senedi, Rehin, Tahliye, İflas gibi) çok daha fazla takip türü/
    yolu/şekli kombinasyonu var — bunlar HENÜZ CANLI GÖRÜLMEDİ, kullanıcı
    kendisi ayrı ayrı anlatacak; buradaki alan kümesi DEĞİŞMEZ (UYAP'ın
    şekli aynı JSON zarfını kullanıyor), yalnız `takibin_*_aciklama`
    metinlerinin değerleri çeşitlenecek — TAHMİN edip yeni sabit
    liste/choices EKLENMEZ."""
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="icra_detay")

    takibin_turu = models.CharField(max_length=8, blank=True)
    takibin_turu_aciklama = models.CharField(max_length=120, blank=True)
    takibin_sekli = models.CharField(max_length=8, blank=True)
    takibin_sekli_aciklama = models.CharField(max_length=255, blank=True)
    takibin_yolu = models.CharField(max_length=8, blank=True)
    takibin_yolu_aciklama = models.CharField(max_length=120, blank=True)

    alacak_kalemi_toplam = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    alacak_kalemi_faiz = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    takip_sonrasi_masraf = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    vekalet_ucreti = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tahsil_harci = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    yapilmis_tahsilat = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # 2026-09-22'de "İlamlı Takip" örneğinde CANLI görüldü (İlamsız Takip
    # örneğinde bu 4 anahtar hiç YOKTU — UYAP yanıtı takip türüne göre
    # değişken alan kümesi döndürüyor olabilir; ingest.py bu alanları
    # `rec.get(...)` ile OPSİYONEL okumalı, eksikse 0 varsayılmalı).
    bsmv_ucreti = models.DecimalField("BSMV Ücreti", max_digits=14, decimal_places=2, default=0)
    kdv = models.DecimalField("KDV", max_digits=14, decimal_places=2, default=0)
    kkdf_ucreti = models.DecimalField("KKDF Ücreti", max_digits=14, decimal_places=2, default=0)
    ozel_iletisim_vergisi = models.DecimalField("Özel İletişim Vergisi", max_digits=14, decimal_places=2, default=0)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "İcra Takip Detayı"
        verbose_name_plural = "İcra Takip Detayları"

    def __str__(self):
        return f"{self.dosya} · Takip Detayı"


class HukukDavaDetay(models.Model):
    """Hukuk dava/değişik iş dosyasına özgü ayrıntı —
    dosyaAyrintiBilgileri_brd.ajx yanıtından (bkz.
    docs/CIOK_YARGI_TURU_SENKRON_PLANI.md §1.5/§2.5). İcra dosyalarında bu
    alanlar hiç YOK — bu yüzden ayrı bir model."""
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="hukuk_detay")

    dava_acilis_turu = models.CharField(max_length=120, blank=True)
    dava_turleri = models.CharField(max_length=500, blank=True)
    ilgili_dosya_listesi = models.CharField(max_length=500, blank=True)
    ilgili_dava_listesi = models.CharField(max_length=500, blank=True)
    ilgili_seri_dava_listesi = models.CharField(max_length=500, blank=True)
    birlesen_dosya_listesi = models.CharField(max_length=500, blank=True)
    durusma_tarihi = models.DateTimeField(null=True, blank=True)
    basvuruya_birakilma_tarihi = models.DateTimeField(null=True, blank=True)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Hukuk Dava Detayı"
        verbose_name_plural = "Hukuk Dava Detayları"

    def __str__(self):
        return f"{self.dosya} · Dava Detayı"


class IdariYargiDetay(models.Model):
    """İdari Yargı dava dosyasına özgü ayrıntı — dosyaAyrıntıBilgileri_brd.ajx
    yanıtından. CANLI DOĞRULANDI (Chrome ağ yakalama, 2026-09-22, gerçek bir
    İdare Dava Dosyası'nda "Dosya Görüntüle"): {"davaKonusu":"Disiplin
    İşlemleri-Cezaları/İlişik Kesme Sonucunu Doğurmayan","heyetli":"Evet",
    "adliMuzaheret":"Hayır","delilTespiti":"Hayır","durusma":"Hayır",
    "yd":"Hayır","iliskiliDosyaIliskiTuru":"","iliskiliDosyaEsasNo":"/",
    "karar":"İptal","kararNo":"2023/360","kararTarihi":"09/03/2023 12:33", ...}
    — İcra/Hukuk detaylarındaki hiçbir alanla ÖRTÜŞMÜYOR (Heyetli/YD/Duruşma/
    Karar gibi kavramlar o iki ailede yok), bu yüzden AYRI model (aynı karar,
    §2.5 CIOK_YARGI_TURU_SENKRON_PLANI.md). Evet/Hayır alanları BooleanField'a
    çevrilir (tek anlamlı ikili, TAHMİN değil); karar_tarihi ise HAM STRİNG
    olarak saklanır — biçimi ("GG/AA/YYYY SS:DD") İcra/Hukuk'un {date,time}
    nesnesinden FARKLI ve yalnız TEK örnekte görüldü, DateTimeField'a
    zorlanıp yanlış ayrıştırma riski alınmaz. `ingest`e yazan taraf henüz
    YOK — dosya_core.py bu aileyi henüz sorgulamıyor (yalnız model hazır;
    "Yenile" akışı ayrı bir adımda eklenecek)."""
    dosya = models.OneToOneField(Dosya, on_delete=models.CASCADE, related_name="idari_yargi_detay")

    dava_konusu = models.CharField("Davanın Konusu", max_length=500, blank=True)
    heyetli = models.BooleanField(null=True, blank=True)
    adli_muzaheret = models.BooleanField("Adli Yardımlı", null=True, blank=True)
    delil_tespiti = models.BooleanField("Delil Tespiti İstemli", null=True, blank=True)
    durusma = models.BooleanField("Duruşmalı", null=True, blank=True)
    yd = models.BooleanField("Yürütmeyi Durdurma (YD)", null=True, blank=True)
    iliskili_dosya_iliski_turu = models.CharField(max_length=120, blank=True)
    iliskili_dosya_esas_no = models.CharField(max_length=40, blank=True)
    karar = models.CharField(max_length=120, blank=True)
    karar_no = models.CharField(max_length=40, blank=True)
    karar_tarihi = models.CharField("Karar Tarihi (ham)", max_length=40, blank=True)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "İdari Yargı Detayı"
        verbose_name_plural = "İdari Yargı Detayları"

    def __str__(self):
        return f"{self.dosya} · İdari Yargı Detayı"


class DosyaSafahat(models.Model):
    """Dosyanın işlem geçmişi/kronolojisi — `dosya_safahat_bilgileri_brd.ajx`
    yanıtından. CANLI DOĞRULANDI (Chrome ağ yakalama), HER BİRİ AYRI AYRI
    (bkz. sınıf altındaki "ÖNEMLİ" notu — birini görüp diğerini varsaymak
    YASAK):
      - **Ceza** — İcra Ceza Hakimliği, "Ceza Dava Dosyası" (2026-09-22,
        dosya 2026/78): {"safahatlar":[{"islemYapanBirim":"İzmir 12. İcra
        Ceza Mahkemesi","safahatTarihi":"Apr 9, 2026 11:58:19 AM",
        "safahatTarihiSTR":"09.04.2026","safahatTuruKod":9,
        "safahatTuruAciklama":"Vekaletname Olmaksızın Vekil Kaydedildi",
        "aciklama":"...","safahatStatuKod":2,
        "safahatStatuKodAciklama":"Evraksız", ...}, ...]}.
      - **Hukuk** — Asliye Hukuk Mahkemesi, "Hukuk Dava Dosyası" (2026-09-22,
        dosya 2026/117, Çeşme 2. Asliye Hukuk Mahkemesi): AYNI alan kümesi,
        farklı örnek satırlar (ör. safahatTuruKod:146 "Harç Masraf Reddiyatı
        İçin Gerekli Hesaplama Yapıldı", 3045 "Cevap Dilekçesi Kaydedildi.").
      - **İcra** — İcra Dairesi, "İcra Dosyası" (2026-09-22, dosya
        2026/21475): AYNI alan kümesi (Gelir İdaresi Başkanlığı/TAKBİS
        sorgu kayıtları, "Dosya Kesinleşme Bilgi Girişi", "Harç Masraf
        Tahsilatı" gibi satırlarla). **ÖNEMLİ/ÜCRETLİ UYARI:** İcra'da bu
        sekme her açıldığında UYAP'ın kendi "Sorgu Bakiyeniz" / "Kalan
        Ücretsiz Sorgu Limiti" sayacını AZALTIYOR (canlı örnekte 5/5 →
        4/5, "Toplam Sorgu Sayısı" 0 → 1) — otomatik/toplu senkron kodu
        yazılırken bu sekme SINIRSIZ/rastgele çağrılmamalı, ileride bir
        önbellekleme/oran sınırlama stratejisi GEREKİR (Ceza/Hukuk'ta
        böyle bir sayaç/ücret GÖRÜLMEDİ — yalnız İcra'ya özgü olabilir,
        ama bu da varsayım, kanıtlanmadı).
    **YOK olduğu doğrulanan bağlamlar** (aynı geniş taramada, 2026-09-22):
      - Ceza / Bölge Adliye Mah. Ceza Dairesi (istinaf) — "Ceza Dava
        Dosyası" olsa bile Safahat sekmesi HİÇ YOK.
      - Ceza / Asliye-Ağır Ceza Mahkemesi "Talimat Dosyası" ve Satış
        Memurluğu — "Dosya Bilgileri" sekmesiyle BİRLİKTE hiçbir ek sekme
        yok (yalnız kapak künyesi).
      - Hukuk / Aile Mahkemesi "Hukuk Değişik İş Dosyası" (tur_kod=14,
        "Karara Çıkmış") — bu dosya türünde HİÇBİR ek sekme yok (ne Taraf
        Bilgileri, ne Evrak, ne Safahat, ne Tah./Red. — yalnız Evrak
        Gönderme/Ödeme/Kişisel Notlar; `dosya_islem_turleri_sorgula_brd.
        ajx` bunu `{"14":"evrak_gonderme,kisisel_not_alani,odeme"}` olarak
        döner).
      - Cbs Soruşturma Dosyası (bkz. yukarıdaki not).
    Genel kural: `dosya_islem_turleri_sorgula_brd.ajx`'in cevabında
    "safahat_bilgileri" anahtarı VARSA bu sekme o dosyada mevcuttur — bu,
    HER (yargı türü, dosya türü) çifti için AYRI AYRI kontrol edilmesi
    gereken bir sorudur, tahmin edilmez. ÖNEMLİ (kullanıcı uyarısı,
    2026-09-22): bu modelin başka bağlamlarda da AYNI ŞEKİLDE çalıştığı asla
    VARSAYILMAZ — yukarıda listelenmeyen her (yargı türü, dosya türü) çifti
    DOĞRULANMAMIŞ sayılır. `safahat_tarihi` ham string olarak saklanır
    (biçimi "Mon D, YYYY H:MM:SS AM/PM" — İcra/Hukuk detaylarındaki
    {date,time} nesnesinden FARKLI, ayrıştırma riske girilmez)."""
    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="safahatlar")

    islem_yapan_birim = models.CharField(max_length=255, blank=True)
    safahat_tarihi = models.CharField("Safahat Tarihi (ham)", max_length=40, blank=True)
    safahat_turu_kod = models.IntegerField(null=True, blank=True)
    safahat_turu_aciklama = models.CharField(max_length=255, blank=True)
    aciklama = models.TextField(blank=True)
    safahat_statu_kod = models.IntegerField(null=True, blank=True)
    safahat_statu_aciklama = models.CharField(max_length=120, blank=True)
    personel = models.CharField(max_length=120, blank=True)

    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dosya Safahatı"
        verbose_name_plural = "Dosya Safahatları"
        ordering = ["dosya", "-id"]

    def __str__(self):
        return f"{self.dosya} · {self.safahat_turu_aciklama or self.pk}"


class DosyaTahsilatHarc(models.Model):
    """Dosyaya yapılan tahsilat VEYA harç ödemesi — `dosya_tahsilat_reddiyat_
    bilgileri_brd.ajx` yanıtının `tahsilatList`/`harcList` dizilerinden.
    CANLI DOĞRULANDI, HER BİRİ AYRI AYRI (2026-09-22):
      - **Ceza** — İcra Ceza Hakimliği, dosya 2026/78:
        {"tahsilatList":[{"makbuzNo":"62972","dosyaNo":"2026/89096",
        "dosyaTurAciklama":"Ceza Tevzii Dosyası","birimAdi":"...",
        "odeyenKisi":"...","tahsilatTuru":"Gider Avansı","tahsilatTarihi":
        "Mar 13, 2026 11:44:45 AM","yatirilanMiktar":2650,
        "hesaplamaYapilanTutar":2581.3,"odenebilirMiktar":2581.3,
        "kayitId":1492277292}], "harcList":[{... AYNI ALAN KÜMESİ ...}],
        "toplamTahsilat":2650,"toplamreddiyat":68.7,"haricen":312,
        "toplamTeminat":0,"toplamTahsilHarci":0,"toplamKalan":2581.3,
        "isIcraMi":false}.
      - **Hukuk** — Asliye Hukuk Mahkemesi, dosya 2026/117 (Çeşme 2. Asliye
        Hukuk Mahkemesi): AYNI alan kümesi, "dosyaTurAciklama":"Hukuk Tevzi
        Dosyası"/"Hukuk Dava Dosyası" örnekleriyle bağımsız doğrulandı.
    **YOK olduğu doğrulanan bağlam:** Hukuk / Aile Mahkemesi "Hukuk Değişik
    İş Dosyası" (bkz. `DosyaSafahat` docstring'indeki aynı not — bu dosya
    türünde hiçbir ek sekme/veri yok).
    `tahsilatList` ve `harcList` BİREBİR AYNI alan kümesini taşıdığı için
    `tur` alanıyla TEK modelde tutulur (`kayitId` UYAP'ın kendi kalıcı
    kaydı — `reddiyatList[].kapattigiTahsilatIDler` ondan REFERANS VERİYOR,
    bu yüzden oturumluk `dosyaId`'nin aksine GÜVENLE tekil anahtar sayılır).
    Kullanıcı uyarısı (2026-09-22): yukarıda listelenmeyen hiçbir bağlamda
    "var" SAYILMAZ, her biri kendi başına doğrulanmalı."""

    class Tur(models.TextChoices):
        TAHSILAT = "tahsilat", "Tahsilat"
        HARC = "harc", "Harç"

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="tahsilat_harclar")
    tur = models.CharField(max_length=10, choices=Tur.choices)
    kayit_id = models.BigIntegerField("UYAP Kayıt ID (kalıcı)")

    makbuz_no = models.CharField("Makbuz No", max_length=40, blank=True)
    birim_adi = models.CharField(max_length=255, blank=True)
    odeyen_kisi = models.CharField(max_length=255, blank=True)
    tahsilat_turu = models.CharField(max_length=120, blank=True)
    tahsilat_tarihi = models.CharField("Tahsilat Tarihi (ham)", max_length=40, blank=True)
    yatirilan_miktar = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    hesaplama_yapilan_tutar = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    odenebilir_miktar = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dosya Tahsilat/Harç Kaydı"
        verbose_name_plural = "Dosya Tahsilat/Harç Kayıtları"
        ordering = ["dosya", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "kayit_id"], name="uq_dosya_tahsilat_harc_kayit"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.get_tur_display()} · {self.makbuz_no}"


class DosyaReddiyat(models.Model):
    """Dosyadan yapılan reddiyat (iade) — `dosya_tahsilat_reddiyat_bilgileri_
    brd.ajx` yanıtının `reddiyatList` dizisinden. CANLI DOĞRULANDI, HER
    BİRİ AYRI AYRI (2026-09-22):
      - **Ceza** — İcra Ceza Hakimliği, dosya 2026/78: {"makbuzNo":"69203",
        "dosyaNo":"2026/78","dosyaTurAciklama":"Ceza Dava Dosyası",
        "durumAciklama":"Ödemesi Yapıldı","miktar":15,"birimAdi":"...",
        "odeyenKisi":"Posta Masrafı (314403676458)","reddiyatNedeni":"Posta
        Masrafı (Elektronik Tebligat)","reddiyatTarihi":"Mar 18, 2026
        9:27:44 AM","gelirVergisi":0,"damgaVergisi":0,"odenecekMiktar":15,
        "kapattigiTahsilatIDler":"1492277292"}.
      - **Hukuk** — Asliye Hukuk Mahkemesi, dosya 2026/117: AYNI alan
        kümesi, "reddiyatNedeni":"KEP Reddiyatı"/"Posta Masrafı" örnekleriyle
        bağımsız doğrulandı.
    `DosyaTahsilatHarc`'tan AYRI model — alan kümesi farklı (vergi alanları,
    kapattığı tahsilat referansı) ve UYAP'ın kendisi de reddiyatı
    tahsilat/harçtan AYRI bir liste olarak döndürüyor. Kalıcı bir `kayitId`
    YOK (yalnız `makbuzNo` — canlı örnekte AYNI makbuzNo+tutar birkaç kez,
    saniyeler arayla tekrar etti; bu YİNELEME UYDURULMADI, olduğu gibi
    gözlemlendi, hem Ceza hem Hukuk örneklerinde) — bu yüzden BİREBİR
    (dosya, kayit_id) gibi sert bir tekillik ZORLANMAZ, satırlar olduğu gibi
    (ek kayıt olarak) tutulur. Kullanıcı uyarısı (2026-09-22): yukarıda
    listelenmeyen hiçbir bağlamda "var" SAYILMAZ."""
    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="reddiyatlar")

    makbuz_no = models.CharField("Makbuz No", max_length=40, blank=True)
    durum_aciklama = models.CharField(max_length=120, blank=True)
    miktar = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    birim_adi = models.CharField(max_length=255, blank=True)
    odeyen_kisi = models.CharField(max_length=255, blank=True)
    reddiyat_nedeni = models.CharField(max_length=255, blank=True)
    reddiyat_tarihi = models.CharField("Reddiyat Tarihi (ham)", max_length=40, blank=True)
    gelir_vergisi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    damga_vergisi = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    odenecek_miktar = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    kapattigi_tahsilat_idler = models.CharField(max_length=255, blank=True)

    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Dosya Reddiyatı"
        verbose_name_plural = "Dosya Reddiyatları"
        ordering = ["dosya", "-id"]

    def __str__(self):
        return f"{self.dosya} · {self.reddiyat_nedeni or self.makbuz_no}"


class IcraDosyaHesabi(models.Model):
    """İcra dosyasının GÜNCEL (o anki, canlı hesaplanan) bakiye dökümü —
    "Dosya Hesabı" sekmesi, `dosya_hesap_bilgileri.ajx` yanıtından. CANLI
    DOĞRULANDI (2026-09-22) YALNIZ **İcra** (İcra Dairesi, "İcra Dosyası",
    dosya 2026/21475) için: [{"textAlan":"Takipte Kesinlesen Miktar",
    "degerAlan":3762.9,"grupId":1}, {"textAlan":"Basvurma Harci","grupId":1},
    {"textAlan":"Toplam Faiz Miktari","degerAlan":73.76,"grupId":1}, ...,
    {"textAlan":"Yatan Para","degerAlan":0,"grupId":2},
    {"textAlan":"Bakiye Borç Miktari","degerAlan":8923.97,"grupId":3}].
    UYAP'ın kendisi bunu SABİT KOLONLAR olarak DEĞİL, esnek bir (etiket,
    değer, grup) satır listesi olarak döner — canlı örnekte bazı kalemlerin
    (Başvurma Harcı, BSMV, KKDF, KDV, Özel İletişim Vergisi) `degerAlan`
    alanı SIFIR bile değil, HİÇ YOK (alan tümüyle eksik) — bu yüzden sabit
    `DecimalField` kolonlarına ZORLANMAZ (TAHMİN ile "hep bu N kalem var"
    denemez), UYAP'ın kendi esnek satır yapısı AYNEN korunur.
    `IcraTakipDetay`'daki statik ayrıntı alanlarının AKSİNE bu değerler
    ZAMANLA DEĞİŞİR (ödeme yapıldıkça bakiye düşer) — bu yüzden OneToOne
    değil, dosya başına satır kümesi tutan ayrı bir model; her yeniden
    sorguda (dosya, metin_alan, grup_id) üzerinden upsert edilir."""
    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="hesap_kalemleri")

    grup_id = models.PositiveSmallIntegerField("Grup ID")
    metin_alan = models.CharField("Kalem Adı", max_length=120)
    deger_alan = models.DecimalField("Tutar", max_digits=14, decimal_places=2, null=True, blank=True)

    guncelleme = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "İcra Dosya Hesap Kalemi"
        verbose_name_plural = "İcra Dosya Hesap Kalemleri"
        ordering = ["dosya", "grup_id", "id"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "metin_alan", "grup_id"],
                                    name="uq_icra_dosya_hesap_kalemi"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.metin_alan}: {self.deger_alan}"


class BorcluSorgu(models.Model):
    """İcra dosyasının "Borçlu Bilgileri" sekmesinden bir borçlu için
    çalıştırılan malvarlığı/adres araştırma sorgusu —
    `borclu_bilgileri_goruntule_<tur>.ajx` uç nokta ailesinden. UYAP bu
    sekmede 9 sorgu türü listeliyor: MERSİS, GİB, SGK (+ alt sorgu "Kamu
    Çalışanı" gibi), EGM-TNB, Posta Çeki, TAKBİS, SGK Haciz, İcra Dosyası,
    Banka — CANLI DOĞRULANDI (2026-09-22, İcra Dairesi, dosya 2026/21475,
    TÜZEL KİŞİ borçlu) yalnız 3'ü: **MERSİS** (sorguSonucDVO DÜZ METİN bir
    adres), **GİB** (sorguSonucDVO YAPILANDIRILMIŞ bir adres nesnesi:
    {"caddeSokak":...,"daireNo":...,"ilKodu":...,"ilceAdi":...,"kapiNo":...,
    "mahalleSemt":...}), **SGK Kamu Çalışanı** (tüzel kişide anlamsız —
    sorguSonucDVO bir HATA AÇIKLAMA metni, "sorguSonucu":false). Kalan 6 tür
    (EGM-TNB, Posta Çeki, TAKBİS, SGK Haciz, İcra Dosyası, Banka) ile GERÇEK
    KİŞİ bir borçlu üzerinde HENÜZ TEST EDİLMEDİ — kullanıcı bunları ayrı
    ayrı anlatacak.

    Yanıt ZARFI (borcluBilgileri, sorgulayanBilgileri, dosyaOzetDVO,
    sorguTarihi, sorgulamaTuru, sorguSonucu, hasError, hasData) tüm görülen
    türlerde AYNI ama `sorguSonucDVO` (asıl sonuç) TÜRE GÖRE BAMBAŞKA
    şekiller alıyor (düz metin / yapılandırılmış nesne / hata metni) — bu
    yüzden ham JSON olarak saklanır, sabit kolonlara ZORLANMAZ."""
    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="borclu_sorgulari")
    borclu = models.ForeignKey(
        DosyaTaraf, on_delete=models.CASCADE, related_name="malvarligi_sorgulari",
        limit_choices_to={"rol": DosyaTaraf.Rol.BORCLU})

    sorgu_turu = models.CharField("Sorgu Türü", max_length=60)  # UYAP'ın kendi "sorgulamaTuru" metni (ör. "Mersis", "GİB", "Kamu Çalışanı")
    sorgu_tarihi = models.CharField("Sorgu Tarihi (ham)", max_length=40, blank=True)
    sorgu_sonucu = models.BooleanField("Sorgu Sonucu", null=True, blank=True)
    has_error = models.BooleanField("Hata Var mı", default=False)
    has_data = models.BooleanField("Veri Var mı", default=False)
    sonuc = models.JSONField("Sorgu Sonucu (ham, sorguSonucDVO)", null=True, blank=True)

    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Borçlu Malvarlığı Sorgusu"
        verbose_name_plural = "Borçlu Malvarlığı Sorguları"
        ordering = ["dosya", "borclu", "-id"]

    def __str__(self):
        return f"{self.dosya} · {self.borclu} · {self.sorgu_turu}"


class TebligatBarkod(models.Model):
    """'Barkod Sorgu' modülünün (UyapMasa/modules/barkod_sorgu.py) her çalıştırmada
    upsert ettiği sonuç kaydı: bir 'Kapalı Tebligat' evrakının PTT barkodu +
    PTT'deki güncel tebliğ durumu + o dosyada tebliğ mazbatasının (fiziki
    dönüş belgesi) dosyaya dönüp dönmediği. KALICI anahtar = (dosya,
    birim_evrak_no) [uq_dosya_tebligat_barkod] — Evrak modeliyle AYNI doğal
    anahtar (bkz. Evrak.birim_evrak_no yorumu); aynı evrak tekrar sorgulanınca
    çoğaltılmaz, güncellenir."""

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="tebligat_barkodlari")
    birim_evrak_no = models.PositiveIntegerField("Birim Evrak No", db_index=True)

    # Bu tebligatın HANGİ borçluya ait olduğu (kullanıcı sorusu, 2026-08-04:
    # kesinleşme hesabı borçlu bazlı ama TebligatBarkod yalnız Dosya'ya
    # bağlıydı — çok borçlulu dosyada ayrım yoktu). barkod_sorgu.py ingest
    # sırasında yalnız dosyada TEK borçlu varsa otomatik atar (bkz.
    # _borclu_coz_tekil) — çok borçlulu dosyada hangi evrakın hangi borçluya
    # gittiğini ayırt eden CANLI doğrulanmış bir alan/desen henüz yok, bu
    # yüzden orada UYDURULMAZ, boş kalır.
    borclu = models.ForeignKey(
        DosyaTaraf, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tebligat_barkodlari",
        limit_choices_to={"rol": DosyaTaraf.Rol.BORCLU})

    evrak_aciklama = models.CharField("Evrak Açıklaması", max_length=500, blank=True)
    evrak_tarihi = models.CharField("Evrak Tarihi", max_length=40, blank=True)

    barkod = models.CharField("PTT Barkod No", max_length=32, blank=True, db_index=True)
    elektronik_tebligat = models.BooleanField(
        "Elektronik Tebligat (PTT'de takip yok)", default=False)

    # Kullanıcı bulgusu (2026-08-03, beşinci tur): evrak açıklaması/PDF gövde
    # metnindeki "... ihtiva eder" ibaresi ve elektronik tebligatlardaki
    # "üçüncü taraf" parantezinden çıkarılan sınıflandırma (Ödeme Emri /
    # Banka (Haciz İhbarnamesi) / Maaş (Haciz İhbarnamesi) / 103 Davetiyesi /
    # Kıymet Takdiri Raporu / Bilirkişi Raporu / ...) — bkz.
    # barkod_sorgu._tebligat_turunu_belirle. CANLI toplu veriyle
    # doğrulanmadı, anahtar kelimeler kullanıcı örneklerinden çıkarıldı.
    tebligat_turu = models.CharField("Tebligat Türü", max_length=60, blank=True)

    ptt_durumu = models.CharField("PTT Durumu", max_length=255, blank=True)
    son_islem_tarihi = models.CharField("Son İşlem Tarihi", max_length=40, blank=True)
    ham_yanit = models.JSONField("PTT Ham Yanıt", null=True, blank=True)
    # PTT hareketlerindeki "işlem detayı"ndan (bkz. barkod_sorgu.ptt_teslim_sekli):
    # teslimde kime/nasıl (muhtara 21. md., bizzat, aynı konutta yakına, kapıya
    # yapıştırma 35. md. …), iadede iade nedeni (Tanınmıyor, adres değişmiş …).
    teslim_sekli = models.CharField("Teslim Şekli / İade Nedeni", max_length=150, blank=True, default="")

    # 'Mazbata' = tebliğ mazbatası (fiziki dönüş belgesi) dosyaya dönmüş mü —
    # bkz. barkod_sorgu.py TEBLIG_MAZBATASI_TUR/KAPALI_TEBLIG_MAZBATASI_TUR;
    # PTT durumundan BAĞIMSIZ bir bilgidir (PTT "teslim edildi" dese de
    # mazbata dosyaya henüz dönmemiş olabilir).
    tebligat_mazbatasi_var = models.BooleanField("Tebliğ Mazbatası Dosyaya Dönmüş", default=False)
    tebligat_mazbatasi_aciklama = models.CharField(
        "Tebliğ Mazbatası Açıklaması", max_length=500, blank=True)
    kapali_tebligat_mazbatasi_var = models.BooleanField(
        "Kapalı Tebliğ Mazbatası Var", default=False)

    # Avukat portalından bu evrak/borçlu için (21/2 dahil) bir yeniden tebliğ
    # talebi ATILMIŞ MI — dosyanın evrak listesinde "Avukat Portal Tebligat
    # Talebi" türünde bir kayıt var mı diye barkod_sorgu.py her taramada
    # bakar (kullanıcı isteği, 2026-08-13). Dosya açılışındaki otomatik
    # tebligat bu türde SAYILMAZ, yalnız avukatın portaldan attığı talepler.
    yeniden_teblig_talep_edildi = models.BooleanField(
        "Yeniden Tebliğ Talebi Gönderildi mi", default=False)

    # T.K.21/2 UYGUNLUK TARAMASI sonucu (bkz. UyapMasa/modules/teblig_21_2_core.py
    # iade_tarama) — kullanıcı bulgusu (2026-08-14): "son 21/2 raporu silindi,
    # bu bilgiler veritabanına eklenmiyor mu?" — eskiden tarama sonucu YALNIZ
    # UyapMasa'nın belleğinde tutuluyordu, program kapanınca/yeniden başlayınca
    # kayboluyordu. Artık her tarama bu alanlara YAZILIR; "Son 21/2 Raporu"
    # buradan (belleğe değil) okunur. t212_tarandi_zamani NULL = bu kayıt
    # hiç 21/2 taramasından geçmedi (rapora girmez).
    t212_kategori = models.CharField("21/2 Tarama Sonucu", max_length=255, blank=True)
    t212_teblig_adresi = models.CharField("21/2 Tebligat Adresi", max_length=500, blank=True)
    t212_mernis_adresi = models.CharField("21/2 Mernis Adresi", max_length=500, blank=True)
    t212_tarandi_zamani = models.DateTimeField("21/2 Son Tarama Zamanı", null=True, blank=True)

    sorgu_zamani = models.DateTimeField(auto_now=True)
    olusturulma = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tebligat Barkod Sorgusu"
        verbose_name_plural = "Tebligat Barkod Sorguları"
        ordering = ["-sorgu_zamani"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "birim_evrak_no"],
                                    name="uq_dosya_tebligat_barkod"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.barkod or '(barkodsuz)'}"


class Evrak(models.Model):
    """Bir dosyaya ait evrak (karar, tebligat, dilekçe, dayanak vb.) önbellek
    kaydı. KALICI anahtar = (dosya, birim_evrak_no) [uq_dosya_evrak].
    UYAP'ın döndüğü evrakId/dosyaId/ggEvrakId OTURUMLUKTUR (canlı testte
    doğrulandı, bkz. docs/BELGE_ONBELLEK_PLANI.md §0.2) — yalnız o oturumda
    içerik indirmek için geçici kullanılır, burada SAKLANMAZ."""

    dosya = models.ForeignKey(Dosya, on_delete=models.CASCADE, related_name="evraklar")
    ust_evrak = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True,
                                  related_name="ekler")

    birim_evrak_no = models.PositiveIntegerField("Birim Evrak No", db_index=True)

    evrak_turu = models.CharField("Evrak Türü", max_length=64, blank=True)
    evrak_tip = models.CharField("Evrak Tipi", max_length=16, blank=True)
    aciklama = models.CharField("Açıklama", max_length=500, blank=True)
    evrak_tarihi = models.DateTimeField("Evrak Tarihi", null=True, blank=True)

    mime_turu = models.CharField("MIME Türü", max_length=64, blank=True)
    boyut = models.PositiveIntegerField("Boyut (bayt)", default=0)
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)

    dosya_yolu = models.CharField("Diskteki Dosya Yolu", max_length=500)
    degisebilir = models.BooleanField("Değişebilir Evrak", default=False)

    indirilme_zamani = models.DateTimeField(auto_now_add=True)
    son_erisim_zamani = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Evrak"
        verbose_name_plural = "Evraklar"
        ordering = ["dosya", "birim_evrak_no"]
        constraints = [
            models.UniqueConstraint(fields=["dosya", "birim_evrak_no"], name="uq_dosya_evrak"),
        ]

    def __str__(self):
        return f"{self.dosya} · {self.birim_evrak_no}"


class ResmiTatil(models.Model):
    """Resmi/dini tatil takvimi — kesinleşme süre hesaplarında (bkz.
    kesinlesme_core.py) 7. günün hafta sonu/tatile denk gelmesi durumunda
    ilk iş gününe kaydırılması için kullanılır. Kullanıcı kararıyla elle
    yönetilir (2026-08-03): dini bayramlar (Ramazan/Kurban) yıldan yıla
    kayar ve Diyanet'in resmi ilanı esas alınmalı — bu tablo TEK otorite
    kaynağıdır. Python `holidays` kütüphanesi yalnız Ayarlar ekranındaki
    (ileride yazılacak) 'öneri doldur' özelliği için kullanılacak, asla
    doğrudan bu tabloya yazmaz."""
    tarih = models.DateField("Tarih", unique=True)
    aciklama = models.CharField("Açıklama", max_length=120, blank=True)

    class Meta:
        verbose_name = "Resmi Tatil"
        verbose_name_plural = "Resmi Tatiller"
        ordering = ["tarih"]

    def __str__(self):
        return f"{self.tarih:%d.%m.%Y}" + (f" — {self.aciklama}" if self.aciklama else "")
