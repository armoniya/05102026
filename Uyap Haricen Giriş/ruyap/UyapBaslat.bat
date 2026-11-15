@echo off
rem DIKKAT: Bu dosyada yalniz ASCII karakter kullanin. Asagidaki "chcp 65001" sonrasinda cmd kalan
rem satirlari UTF-8 olarak yeniden okur; Turkce harfler bayt kaymasina yol acip komutlari bozar
rem (14.11.2026: bu yuzden uyap_app.exe hic baslamiyordu).
chcp 65001 >nul
title UYAP Ag Gecidi
cd /d "%~dp0"

rem Once en son derleme (uyap_app.exe), yoksa kurulu CokluUyap.exe. Program konsolsuz calisir ve
rem bildirim alanina (tepsi) yerlesir; bu pencere hemen kapanir.
if exist "uyap_app.exe" (
    start "" "uyap_app.exe"
    exit /b 0
)
if exist "CokluUyap.exe" (
    start "" "CokluUyap.exe"
    exit /b 0
)

echo UYAP Ag Gecidi kaynaktan baslatiliyor...
cargo run --release --bin uyap_app
if %errorlevel% neq 0 (
    echo.
    echo [HATA] Uygulama beklenmedik sekilde kapandi. Hata kodu: %errorlevel%
    pause
)
