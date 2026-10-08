# -*- coding: utf-8 -*-
"""UyapMasa (tam program) için Windows kurulum paketi üretir → dist/UyapMasaKur.msi
====================================================================================
"Uyap Haricen Giriş/msi_hazirla.py" (light sürüm, PyInstaller) ile AYNI MSI düzeni
(perUser → yönetici izni istemez, MajorUpgrade, kısayollar), ama dondurma YOK:
UyapMasa dinamik sys.path/importlib yüklemeleriyle çalıştığı için kaynak, geliştirme
makinesindeki klasör düzeniyle birebir taşınır ve gömülü Python ile koşar.

Kurulum düzeni (%LOCALAPPDATA%\\UyapMasa):
    python\\   taşınabilir CPython (bu makinedeki Python314'ün kopyası) + site-packages
    app\\      git'te izlenen kaynak (sır/veri/belge YOK — yalnız `git ls-files`)
    pgsql\\    PostgreSQL bin/lib/share — ilk açılışta db_baslat bunu
              %LOCALAPPDATA%\\UyapIcra\\pgsql'e kopyalar

Çalıştırma (kök klasörde, UyapMasa'nın kullandığı Python ile):
    python kurulum\\uyapmasa_paketle.py
"""
import datetime
import os
import re
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
               "Uyap Haricen Giriş/webapp/")
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
        <Shortcut Id="MasaustuLnk" Name="%(ad)s" Target="[INSTALLDIR]python\\pythonw.exe"
                  Arguments="&quot;[INSTALLDIR]app\\UyapMasa\\uyapmasa.py&quot;"
                  WorkingDirectory="UYAPMASADIR" />
        <RegistryValue Root="HKCU" Key="Software\\UyapMasa" Name="masaustu_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>
    <DirectoryRef Id="ProgramMenuFolder">
      <Component Id="BaslatKisayol" Guid="{1C9D4A73-B826-4E05-9F3A-6D2E8B1C5F07}">
        <Shortcut Id="BaslatLnk" Name="%(ad)s" Target="[INSTALLDIR]python\\pythonw.exe"
                  Arguments="&quot;[INSTALLDIR]app\\UyapMasa\\uyapmasa.py&quot;"
                  WorkingDirectory="UYAPMASADIR" />
        <RegistryValue Root="HKCU" Key="Software\\UyapMasa" Name="baslat_kisayol"
                       Type="integer" Value="1" KeyPath="yes" />
      </Component>
    </DirectoryRef>

    <util:CloseApplication Id="CalisaniKapat" Target="pythonw.exe" CloseMessage="yes"
                           RebootPrompt="no" />

    <CustomAction Id="UygulamayiBaslat" Directory="INSTALLDIR"
                  ExeCommand="&quot;[INSTALLDIR]python\\pythonw.exe&quot; &quot;[INSTALLDIR]app\\UyapMasa\\uyapmasa.py&quot;"
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
    # Kısayolların çalışma klasörü (app\UyapMasa) için heat'in ürettiği dizin kimliği.
    with open(dosyalar_wxs, encoding="utf-8") as f:
        wxs = f.read()
    m = re.findall(r'<Directory Id="(dir[0-9A-F]+)" Name="UyapMasa">', wxs)
    if len(m) != 1:
        raise SystemExit("heat çıktısında app\\UyapMasa dizini tek olarak bulunamadı.")
    urun = URUN_WXS.replace("UYAPMASADIR", m[0])
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
    if "--yalniz-msi" not in sys.argv:
        shutil.rmtree(STAGE, ignore_errors=True)
        _mkdir(STAGE)
        python_kopyala()
        kaynak_kopyala()
        pgsql_kopyala()
    msi_uret()


if __name__ == "__main__":
    main()
