# -*- mode: python ; coding: utf-8 -*-
# Çoklu UYAP — masaüstü ofis uygulaması (uyap_app.py) PyInstaller tanımı.
# Müşteriye dağıtılan program BUDUR (uyapmasa.py tarzı arayüz: giriş → otomatik paylaşım).
# Derleme ("Uyap Haricen Giriş" içinde):
#   .\.venv\Scripts\python.exe -m PyInstaller cokluuyap_app.spec --noconfirm
# Çıktı: dist/CokluUyap/ — tek başına dağıtılmaz; CokluUyapKur.exe (kurucu) içine gömülür.
import os
from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.abspath(SPECPATH)

a = Analysis(
    ["uyap_app.py"],
    pathex=[HERE],
    binaries=[],
    datas=[
        # UYAP statik önbelleği (office_agent/home_client CACHE_DIR)
        (os.path.join(HERE, "uyap_core", "static_cache"), "uyap_core/static_cache"),
        # E-imza giriş modülü: adı Türkçe karakter içerdiğinden uyap_proxy bunu dosya
        # yolundan (importlib) yükler → PyInstaller çözümlemesi göremez, elle taşınır.
        (os.path.join(HERE, "uyap_core", "uyap_giris_dışarıdan.py"), "uyap_core"),
    ],
    hiddenimports=(
        collect_submodules("uvicorn")
        + collect_submodules("websockets")
        # uyap_giris_dışarıdan.py dinamik yüklendiği için bağımlılıkları da elle:
        + ["requests", "urllib3"]
        # Sıra kapısı arayüzü/eşleştirme: uyap_app içinde yöntem içinden içe aktarılır.
        + ["sira_kapisi_arayuz", "uyap_core.sira_eslestirme", "uyap_core.sira_kapisi"]
        # Tüm Evrakı İndir: jobs içinden tembel içe aktarılır; dönüştürme PyMuPDF ile.
        + ["uyap_core.evrak_toplu", "uyap_core.udf_pdf", "fitz", "pymupdf", "pypdf"]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Çekirdek paylaşım için gereksiz ağır bağımlılıklar dışlanır (mts modülleri salt
    # stdlib kullanır, doğrulandı). Paket ~140 MB açık / ~60 MB sıkışık kalır;
    # gömülü PostgreSQL / İcra araçları BU PAKETE ASLA GİRMEZ (2 GB şişme yasağı).
    # PyMuPDF (fitz) "Tüm Evrakı İndir" dönüştürmesi için pakette (~+25 MB).
    excludes=["playwright", "matplotlib", "PIL", "numpy", "pandas",
              "django", "openpyxl", "lxml",
              # Toplu iş işleyicileri light'ta kapalı (uyap_app → UYAP_TOPLU_IS=0); pakete girmez.
              "uyap_core.job_handlers", "uyap_core.mts", "uyap_core.teblig_212",
              "uyap_core.ipotek", "uyap_core.xml_takip", "uyap_core.esas_no",
              "uyap_core.dosya_kaydet"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CokluUyap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # masaüstü arayüz; log zaten pencere içindeki günlükte
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CokluUyap",
)
