# -*- coding: utf-8 -*-
"""UyapMasa (tam program) için Windows kurulum paketi üretir → dist/UyapMasaKur.msi
====================================================================================
"Uyap Haricen Giriş/msi_hazirla.py" (light sürüm, PyInstaller) ile AYNI MSI düzeni
(perUser → yönetici izni istemez, MajorUpgrade, kısayollar). PyInstaller dondurması
YOK: UyapMasa dinamik sys.path/importlib yüklemeleriyle çalıştığı için klasör düzeni
geliştirme makinesindekiyle birebir korunur; ama her kendi modülümüz Nuitka ile
makine koduna (.pyd) derlenir ve .py kaynağı pakete GİRMEZ (yalnız manage.py,
migrations ve __init__.py açık kalır). Kısayol python/UyapMasa.exe'yi açar
(baslatici.c — gömülü Python'la uyapmasa.main()).

Kurulum düzeni (%LOCALAPPDATA%\\UyapMasa):
    python\\   taşınabilir CPython (bu makinedeki Python314'ün kopyası) + site-packages
    app\\      git'te izlenen kaynak (sır/veri/belge YOK — yalnız `git ls-files`)
    pgsql\\    PostgreSQL bin/lib/share — ilk açılışta db_baslat bunu
              %LOCALAPPDATA%\\UyapIcra\\pgsql'e kopyalar

Gereken: kurulum/araclar/nuitka-venv (aynı Python314'ten venv; pip install nuitka ziglang).
Çalıştırma (kök klasörde, UyapMasa'nın kullandığı Python ile; derleme ~30 dk):
    python kurulum\\uyapmasa_paketle.py
"""
import concurrent.futures
import datetime
import os
import queue
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
KOK = os.path.dirname(HERE)
BUILD = os.path.join(HERE, "build")
STAGE = os.path.join(BUILD, "stage")
DIST = os.path.join(HERE, "dist")
MSI = os.path.join(DIST, "UyapMasaKur.msi")
WIX = os.path.join(HERE, "araclar", "wix311")
WIX_URL = ("https://github.com/wixtoolset/wix3/releases/download/"
           "wix3112rtm/wix311-binaries.zip")

NUITKA_PY = os.path.join(HERE, "araclar", "nuitka-venv", "Scripts", "python.exe")
PY_KAYNAK = os.path.dirname(sys.executable)
PG_KAYNAK = os.path.join(os.environ["LOCALAPPDATA"], "UyapIcra", "pgsql")
SYS32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
VC_DLL = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")

APP_ADI = "UyapMasa"
# SABİT kalmalı (light sürümün kodundan FARKLI: ikisi yan yana kurulabilir).
UPGRADE_CODE = "{4D7A2C91-3E58-4B0F-A6D2-8C1F5E9B7A30}"

# Derleme/geliştirme araçları pakete girmez; "crypto" (Naked/shellescape) kullanılmıyor.
PAKET_DISI = {"altgraph", "pefile", "pyflakes", "pyinstaller", "pyinstaller-hooks-contrib",
              "crypto", "naked", "shellescape"}

# git ls-files içinden pakete GİRMEYENLER: sunucu tarafı (cokluuyap.com), derleme
# betikleri, testler, belgeler/notlar.
HARIC_ON_EK = ("docs/", "kurulum/", "Panel/", "Uyap Haricen Giriş/tests/",
               "Uyap Haricen Giriş/webapp/",
               "Dosya Açılış/İcra Takip Açılış - XML/")  # hiçbir yerden kullanılmıyor
HARIC_DOSYA = {
    "Uyap Haricen Giriş/" + ad for ad in (
        "vendor_server.py", "accounts.py", "signaling_server.py", "turn_dogrula.py",
        "zs.py", "zs_store.py", "zs_r2.py", "zs_rapor.py", "zs_push.py", "zs_bildirim.py",
        "zs_denetim.py", "kurucu.py", "msi_hazirla.py", "paket_hazirla.py",
        "cokluuyap_app.spec", "cokluuyap_kur.spec", ".dockerignore", ".gitignore")
} | {"sifirla_veritabani.py", "temizle_artefaktlar.py", ".gitignore"}
HARIC_UZANTI = (".md", ".txt", ".bat", ".spec")
HARIC_ICINDE = ("/test_", "/tests/")


def calistir(*komut, **kw):
    r = subprocess.run(list(komut), capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.stderr.write((r.stdout or "")[-4000:] + (r.stderr or "")[-4000:])
        raise SystemExit(f"Komut başarısız ({os.path.basename(komut[0])}, kod {r.returncode})")
    return r.stdout


def surum():
    t = datetime.date.today()
    return f"{t.year % 100}.{t.month}.{t.day}"


def python_kopyala():
    """Taban CPython'u (site-packages HARİÇ) kopyalar, paketleri pip --target ile kurar."""
    hedef = os.path.join(STAGE, "python")
    print(f"[1/5] Python kopyalanıyor ({PY_KAYNAK}) …")
    _mkdir(hedef)
    for ad in os.listdir(PY_KAYNAK):
        kaynak = os.path.join(PY_KAYNAK, ad)
        if os.path.isfile(kaynak) and ad.lower().endswith((".exe", ".dll", ".txt")):
            shutil.copy2(kaynak, hedef)
    for alt in ("DLLs", "tcl"):
        shutil.copytree(os.path.join(PY_KAYNAK, alt), os.path.join(hedef, alt),
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(os.path.join(PY_KAYNAK, "Lib"), os.path.join(hedef, "Lib"),
                    ignore=lambda d, n: [x for x in n if x in (
                        "site-packages", "__pycache__", "test", "idlelib", "turtledemo")])
    for dll in VC_DLL:
        if not os.path.exists(os.path.join(hedef, dll)):
            shutil.copy2(os.path.join(SYS32, dll), hedef)

    print("[2/5] Paketler kuruluyor (pip) …")
    freeze = calistir(sys.executable, "-m", "pip", "freeze").splitlines()
    gerek = [s for s in freeze
             if "==" in s and s.split("==")[0].strip().lower() not in PAKET_DISI]
    req = os.path.join(BUILD, "requirements-uyapmasa.txt")
    with open(req, "w", encoding="utf-8") as f:
        f.write("\n".join(gerek) + "\n")
    sp = os.path.join(hedef, "Lib", "site-packages")
    calistir(sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
             "--no-warn-script-location", "--only-binary", ":all:",
             "--target", sp, "-r", req)
    # pip --target "bin/Scripts" klasörü bırakır; çalışma için gereksiz.
    shutil.rmtree(os.path.join(sp, "bin"), ignore_errors=True)
    for kok, dirs, _ in os.walk(sp):
        for d in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(os.path.join(kok, d), ignore_errors=True)


def _mkdir(yol):
    os.makedirs(yol, exist_ok=True)
    return yol


def kaynak_kopyala():
    print("[3/5] Kaynak kopyalanıyor (git ls-files) …")
    dosyalar = calistir("git", "-C", KOK, "-c", "core.quotepath=off", "ls-files",
                        encoding="utf-8").splitlines()
    n = 0
    for yol in dosyalar:
        if (yol.startswith(HARIC_ON_EK) or yol in HARIC_DOSYA
                or yol.lower().endswith(HARIC_UZANTI)
                or any(p in "/" + yol for p in HARIC_ICINDE)):
            continue
        hedef = os.path.join(STAGE, "app", *yol.split("/"))
        _mkdir(os.path.dirname(hedef))
        shutil.copy2(os.path.join(KOK, *yol.split("/")), hedef)
        n += 1
    print(f"      {n} dosya")
    # Türkçe karakterli modül adı derlenmiş .pyd'de sorun çıkarır; ASCII ada çevrilir
    # (uyap_proxy, .py yoksa uyap_giris_disaridan'ı import eder).
    core = os.path.join(STAGE, "app", "Uyap Haricen Giriş", "uyap_core")
    os.replace(os.path.join(core, "uyap_giris_dışarıdan.py"),
               os.path.join(core, "uyap_giris_disaridan.py"))


def _acik_kalir(yol):
    ad = os.path.basename(yol)
    return (ad in ("manage.py", "__init__.py")
            or os.sep + "migrations" + os.sep in yol)


_ISCI_YUVALARI = queue.Queue()


def _derle_tek(is_):
    i, yol = is_
    cikti = os.path.join(BUILD, "nuitka", str(i))
    shutil.rmtree(cikti, ignore_errors=True)
    # Zig'in YEREL derleme önbelleği modüller arasında paylaşılırsa bozuk .pyd çıkıyor
    # (import edilince access violation — 2026-10-08 yeniden üretildi: önbellekteki ilk
    # modül sağlam, sonrakiler bozuk). Yerel önbellek her modüle ayrı (cikti altında,
    # onunla silinir); pahalı GLOBAL önbellek (libc/compiler-rt) işçi başına paylaşılır.
    yuva = _ISCI_YUVALARI.get()
    try:
        onbellek = os.path.join(BUILD, "onbellek", str(yuva))
        env = dict(os.environ, NUITKA_CACHE_DIR=os.path.join(onbellek, "nuitka"),
                   ZIG_GLOBAL_CACHE_DIR=os.path.join(onbellek, "zig"),
                   ZIG_LOCAL_CACHE_DIR=os.path.join(cikti, "zig-yerel"))
        r = subprocess.run([NUITKA_PY, "-m", "nuitka", "--module", "--zig", "--nofollow-imports",
                            "--python-flag=no_docstrings", "--assume-yes-for-downloads",
                            "--quiet", "--remove-output", f"--output-dir={cikti}", yol],
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env)
    finally:
        _ISCI_YUVALARI.put(yuva)
    pyd = [f for f in os.listdir(cikti) if f.endswith(".pyd")] if os.path.isdir(cikti) else []
    if r.returncode != 0 or len(pyd) != 1:
        return yol, (r.stdout or "")[-3000:] + (r.stderr or "")[-3000:]
    shutil.move(os.path.join(cikti, pyd[0]), os.path.join(os.path.dirname(yol), pyd[0]))
    os.remove(yol)
    shutil.rmtree(cikti, ignore_errors=True)
    return yol, None


def kaynak_derle():
    """Her kendi .py modülümüzü Nuitka ile .pyd'ye derler, .py'yi siler."""
    if not os.path.exists(NUITKA_PY):
        raise SystemExit(f"Nuitka ortamı yok: {NUITKA_PY}")
    app = os.path.join(STAGE, "app")
    isler = []
    for kok, _d, dosyalar in os.walk(app):
        for ad in dosyalar:
            yol = os.path.join(kok, ad)
            if ad.endswith(".py") and not _acik_kalir(yol):
                isler.append(yol)
    isler.sort(key=os.path.getsize, reverse=True)   # büyükler önce → paralellik dengeli
    print(f"[3b] {len(isler)} modül derleniyor (Nuitka + Zig) …", flush=True)
    hatalar = []
    isci = 2   # bellek: 8 GB makinede 3-4 işçi derlemeyi bellek darlığına soktu
    for n in range(isci):
        _ISCI_YUVALARI.put(n)
    with concurrent.futures.ThreadPoolExecutor(max_workers=isci) as ex:
        for n, (yol, hata) in enumerate(ex.map(_derle_tek, enumerate(isler)), 1):
            if hata:
                hatalar.append((yol, hata))
            if n % 10 == 0:
                print(f"      {n}/{len(isler)}", flush=True)
    if hatalar:
        for yol, hata in hatalar:
            sys.stderr.write(f"\n=== {yol}\n{hata}\n")
        raise SystemExit(f"{len(hatalar)} modül derlenemedi.")
    acik = []
    for kok, _d, dosyalar in os.walk(app):
        for ad in dosyalar:
            yol = os.path.join(kok, ad)
            if ad.endswith(".pyi"):
                os.remove(yol)
            elif ad.endswith(".py"):
                if not _acik_kalir(yol):
                    raise SystemExit(f"Derlenmemiş kaynak kaldı: {yol}")
                acik.append(yol)
    print(f"      açık kalan .py: {len(acik)} (manage.py / migrations / __init__.py)")
    # Son güvence: her .pyd ayrı süreçte import edilir; çöken (bozuk derlenmiş) varsa dur.
    sys.path.insert(0, HERE)
    import pyd_kontrol
    bozuk = pyd_kontrol.bozuklar(app, os.path.join(STAGE, "python", "python.exe"))
    if bozuk:
        raise SystemExit("Import edilince ÇÖKEN modüller:\n  " + "\n  ".join(bozuk))
    print("      çökme denetimi: temiz", flush=True)


def baslatici_derle():
    print("[3c] UyapMasa.exe başlatıcısı derleniyor …", flush=True)
    calistir(NUITKA_PY, "-m", "ziglang", "cc", "-O2", "-target", "x86_64-windows-gnu",
             "-municode", "-Wl,--subsystem,windows",
             "-I", os.path.join(PY_KAYNAK, "include"), "-L", os.path.join(PY_KAYNAK, "libs"),
             os.path.join(HERE, "baslatici.c"), "-lpython314", "-lshell32", "-luser32",
             "-o", os.path.join(STAGE, "python", "UyapMasa.exe"))


def pgsql_kopyala():
    print(f"[4/5] PostgreSQL kopyalanıyor ({PG_KAYNAK}) …")
    hedef = os.path.join(STAGE, "pgsql")
    for alt in ("bin", "lib", "share"):
        shutil.copytree(os.path.join(PG_KAYNAK, alt), os.path.join(hedef, alt),
                        ignore=shutil.ignore_patterns("*.pdb", "pgAdmin*", "stackbuilder*"))
    for ad in os.listdir(PG_KAYNAK):
        if ad.endswith("license.txt"):
            shutil.copy2(os.path.join(PG_KAYNAK, ad), hedef)
    for dll in VC_DLL:
        if not os.path.exists(os.path.join(hedef, "bin", dll)):
            shutil.copy2(os.path.join(SYS32, dll), os.path.join(hedef, "bin"))


def wix_hazirla():
    if os.path.exists(os.path.join(WIX, "candle.exe")):
        return
    _mkdir(WIX)
    zpath = os.path.join(WIX, "wix311-binaries.zip")
    print("WiX araçları indiriliyor (bir kereye mahsus) …")
    urllib.request.urlretrieve(WIX_URL, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(WIX)
    os.remove(zpath)


URUN_WXS = """<?xml version="1.0" encoding="utf-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi"
     xmlns:util="http://schemas.microsoft.com/wix/UtilExtension">
  <Product Id="*" Name="%(ad)s" Language="1055" Codepage="1254" Version="%(surum)s"
           Manufacturer="Çoklu UYAP" UpgradeCode="%(upgrade)s">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perUser"
             InstallPrivileges="limited" Description="%(ad)s Kurulumu" />
    <MajorUpgrade AllowSameVersionUpgrades="yes"
                  DowngradeErrorMessage="Daha yeni bir sürüm zaten kurulu." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" MaximumUncompressedMediaSize="1024" />
    <Property Id="MSIFASTINSTALL" Value="7" />
    <Property Id="ARPNOMODIFY" Value="1" />

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="LocalAppDataFolder">
        <Directory Id="INSTALLDIR" Name="UyapMasa" />
      </Directory>
      <Directory Id="DesktopFolder" />
      <Directory Id="ProgramMenuFolder" />
    </Directory>

    <DirectoryRef Id="DesktopFolder">
      <Component Id="MasaustuKisayol" Guid="{8E3B6F12-5A47-4C9D-B0E1-2F6A9D4C7B58}">
        <Shortcut Id="MasaustuLnk" Name="%(ad)s" Target="[INSTALLDIR]python\\UyapMasa.exe"
                  WorkingDirectory="INSTALLDIR" />
        <RegistryValue Root="HKCU" Key="Software\\UyapMasa" Name="masaustu_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>
    <DirectoryRef Id="ProgramMenuFolder">
      <Component Id="BaslatKisayol" Guid="{1C9D4A73-B826-4E05-9F3A-6D2E8B1C5F07}">
        <Shortcut Id="BaslatLnk" Name="%(ad)s" Target="[INSTALLDIR]python\\UyapMasa.exe"
                  WorkingDirectory="INSTALLDIR" />
        <RegistryValue Root="HKCU" Key="Software\\UyapMasa" Name="baslat_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>

    <util:CloseApplication Id="CalisaniKapat" Target="UyapMasa.exe" CloseMessage="yes"
                           RebootPrompt="no" />

    <CustomAction Id="UygulamayiBaslat" Directory="INSTALLDIR"
                  ExeCommand="&quot;[INSTALLDIR]python\\UyapMasa.exe&quot;"
                  Impersonate="yes" Return="asyncNoWait" />
    <InstallExecuteSequence>
      <Custom Action="UygulamayiBaslat" After="InstallFinalize">NOT Installed OR REINSTALL OR UPGRADINGPRODUCTCODE</Custom>
    </InstallExecuteSequence>

    <Feature Id="Ana" Title="%(ad)s" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="MasaustuKisayol" />
      <ComponentRef Id="BaslatKisayol" />
    </Feature>
  </Product>
</Wix>
"""


def msi_uret():
    print("[5/5] MSI üretiliyor …")
    wix_hazirla()
    v = surum()
    dosyalar_wxs = os.path.join(BUILD, "msi_dosyalar.wxs")
    calistir(os.path.join(WIX, "heat.exe"), "dir", STAGE,
             "-cg", "AppFiles", "-dr", "INSTALLDIR",
             "-srd", "-sfrag", "-ag", "-sreg", "-scom",
             "-var", "var.SourceDir", "-nologo", "-out", dosyalar_wxs)
    urun = URUN_WXS
    urun_wxs = os.path.join(BUILD, "msi_urun.wxs")
    with open(urun_wxs, "w", encoding="utf-8") as f:
        f.write(urun % {"ad": APP_ADI, "surum": v, "upgrade": UPGRADE_CODE})
    calistir(os.path.join(WIX, "candle.exe"), "-nologo", "-ext", "WixUtilExtension",
             f"-dSourceDir={STAGE}", "-out", BUILD + os.sep, urun_wxs, dosyalar_wxs)
    _mkdir(DIST)
    calistir(os.path.join(WIX, "light.exe"), "-nologo", "-sval", "-spdb",
             "-ext", "WixUtilExtension", "-out", MSI,
             os.path.join(BUILD, "msi_urun.wixobj"), os.path.join(BUILD, "msi_dosyalar.wixobj"))
    print(f"MSI hazır: {MSI} ({os.path.getsize(MSI) / 1e6:.1f} MB, sürüm {v})")


def main():
    if "--devam" in sys.argv:
        # Yarıda kalan derlemeyi sürdür: başarıyla derlenenlerin .py'si zaten silinmiş,
        # kaynak_derle yalnız kalan .py'leri derler.
        kaynak_derle()
        baslatici_derle()
        if not os.path.isdir(os.path.join(STAGE, "pgsql")):
            pgsql_kopyala()
    elif "--yalniz-msi" not in sys.argv:
        shutil.rmtree(STAGE, ignore_errors=True)
        _mkdir(STAGE)
        python_kopyala()
        kaynak_kopyala()
        kaynak_derle()
        baslatici_derle()
        pgsql_kopyala()
    msi_uret()


if __name__ == "__main__":
    main()
