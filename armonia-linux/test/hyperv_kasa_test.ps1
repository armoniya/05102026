# Faz 2 kasa testi: ikinci sanal diskle acar; 1. acilista test servisi diski LUKS2 kasa yapip yeniden baslatir;
# 2. acilista Plymouth parola ekraninin goruntusu alinir, test parolasi klavyeden yazilir, olcum blogu beklenir.
# Kullanim: powershell -File hyperv_kasa_test.ps1 [-Iso C:\dev\armonia_test_kasa.iso]
# Ciktilar: C:\dev\armonia_com1.txt, C:\dev\kasa_parola_ekrani.png, C:\dev\vm_ekran.png. Claude, 2026-11-14.
# Test parolasi kasa parola kuralina uyar (buyuk+kucuk+rakam+sembol, Turkce karakter yok); '-' TR-Q yerinden yazilir.
param([string]$Iso = 'C:\dev\armonia_test_kasa.iso', [int]$RamMB = 1536, [string]$Parola = 'Kasa-test2026')
$ErrorActionPreference = 'Stop'
$ad = 'ArmoniaTest'; $dizin = Split-Path -Parent $MyInvocation.MyCommand.Path
$cikti = 'C:\dev\armonia_com1.txt'; $vhd = 'C:\dev\armonia_kasa_test.vhdx'
$eski = Get-VM -Name $ad -ErrorAction SilentlyContinue
if ($eski) { Stop-VM -Name $ad -TurnOff -Force -ErrorAction SilentlyContinue; Remove-VM -Name $ad -Force }
if (Test-Path $vhd) { Remove-Item $vhd -Force }
New-VHD -Path $vhd -SizeBytes 2GB -Dynamic | Out-Null
New-VM -Name $ad -Generation 1 -MemoryStartupBytes ([int64]$RamMB * 1MB) -NoVHD | Out-Null
Set-VM -Name $ad -ProcessorCount 2 -StaticMemory -AutomaticCheckpointsEnabled $false
Set-VMDvdDrive -VMName $ad -ControllerNumber 1 -ControllerLocation 0 -Path $Iso
Add-VMHardDiskDrive -VMName $ad -ControllerType IDE -ControllerNumber 0 -ControllerLocation 0 -Path $vhd
$boru = 'armk' + (Get-Date -Format 'MMddHHmmss')
Set-VMComPort -VMName $ad -Number 1 -Path ('\\.\pipe\' + $boru)
$sw = Get-VMSwitch -Name 'Default Switch' -ErrorAction SilentlyContinue
if ($sw) { Connect-VMNetworkAdapter -VMName $ad -SwitchName $sw.Name }
# Not: Gen1'de DVD'den acilis icin acilis sirasi CD once olmali
Set-VMBios -VMName $ad -StartupOrder @('CD', 'IDE', 'LegacyNetworkAdapter', 'Floppy')
Remove-Item $cikti -ErrorAction SilentlyContinue
Start-VM -Name $ad
$t0 = Get-Date
Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$dizin\com_oku.ps1",'-Boru',$boru,'-Cikti',$cikti,'-ZamanAsimiSn','600' | Out-Null

function Bekle([string]$desen, [int]$sn) {
  $son = (Get-Date).AddSeconds($sn)
  while ((Get-Date) -lt $son) {
    try {
      # Okuyucu sureci dosyaya yazarken kilit cakismasin: paylasimli okuma
      $fs = [IO.File]::Open($cikti, 'Open', 'Read', 'ReadWrite'); $sr = New-Object IO.StreamReader($fs)
      $metin = $sr.ReadToEnd(); $sr.Close()
      if ($metin -match $desen) { return $true }
    } catch {}
    Start-Sleep -Milliseconds 500
  }
  return $false
}
if (-not (Bekle 'KASA-TEST-KUR bitti' 150)) { "HATA: kasa bicimlendirme bitmedi"; if (Test-Path $cikti) { Get-Content $cikti }; exit 1 }
"Kasa bicimlendirildi: +$([int]((Get-Date)-$t0).TotalSeconds) sn; yeniden acilis bekleniyor"
$t1 = Get-Date
# 2. acilis: parola ekrani gelene kadar bekle (cekirdek + Plymouth ~8-12 sn)
Start-Sleep -Seconds 25
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\kasa_parola_ekrani.png' -W 640 -H 480 | Out-Null; "Parola ekrani goruntusu alindi" } catch { "Goruntu alinamadi: $_" }
# Test parolasini yaz (Msvm_Keyboard.TypeText) + Enter
$cs = Get-CimInstance -Namespace root\virtualization\v2 -ClassName Msvm_ComputerSystem -Filter "ElementName='$ad'"
$kb = Get-CimAssociatedInstance -InputObject $cs -ResultClassName Msvm_Keyboard | Select-Object -First 1
if (-not $kb) { "HATA: Msvm_Keyboard bulunamadi"; Stop-VM -Name $ad -TurnOff -Force; exit 1 }
# TypeText KULLANILMAZ: Turkce Windows'ta misafire yanlis tus kodlari gonderiyor (7. deneme evtest: "kasatest2026" ->
# KEY_RO, M, B, M, RIGHTSHIFT). Bunun yerine PC set-1 tarama kodlari dogrudan (basma + birakma = kod|0x80).
$sc = @{ 'a'=0x1E; 'b'=0x30; 'c'=0x2E; 'd'=0x20; 'e'=0x12; 'f'=0x21; 'g'=0x22; 'h'=0x23; 'j'=0x24; 'k'=0x25; 'l'=0x26;
         'm'=0x32; 'n'=0x31; 'o'=0x18; 'p'=0x19; 'q'=0x10; 'r'=0x13; 's'=0x1F; 't'=0x14; 'u'=0x16; 'v'=0x2F; 'w'=0x11;
         'x'=0x2D; 'y'=0x15; 'z'=0x2C; '1'=0x02; '2'=0x03; '3'=0x04; '4'=0x05; '5'=0x06; '6'=0x07; '7'=0x08; '8'=0x09;
         '9'=0x0A; '0'=0x0B }
# Konsol duzeni TR-Q: semboller ABD tus yerlerinde DEGIL. '-' TR-Q'da ABD '=' tusunda (0x0D), '*' ABD '-' tusunda (0x0C).
$sembol = @{ '-'=0x0D; '*'=0x0C }
function Tara([int]$kod, [switch]$Shift) {
  $dizi = if ($Shift) { @(0x2A, $kod, ($kod -bor 0x80), 0xAA) } else { @($kod, ($kod -bor 0x80)) }
  Invoke-CimMethod -InputObject $kb -MethodName TypeScancodes -Arguments @{ Scancodes = [byte[]]$dizi } | Out-Null
  Start-Sleep -Milliseconds 120
}
function Yaz([string]$metin) {
  foreach ($c in $metin.ToCharArray()) {
    $k = [string]$c; $kucuk = $k.ToLowerInvariant()
    if ($sembol.ContainsKey($k)) { Tara $sembol[$k] }
    elseif ($sc.ContainsKey($kucuk) -and $k -cne $kucuk) { Tara $sc[$kucuk] -Shift }   # buyuk harf
    elseif ($sc.ContainsKey($k)) { Tara $sc[$k] }
    else { "HATA: '$k' icin tarama kodu yok (TR-Q'da 'i' = ABD i tusu degildir)"; Stop-VM -Name $ad -TurnOff -Force; exit 1 }
  }
}
# Once YANLIS parola: Plymouth ipucu + yeniden sorma denenir
Yaz 'Kasa-test2025'; Tara 0x1C
Start-Sleep -Seconds 6
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\kasa_yanlis_parola.png' -W 640 -H 480 | Out-Null; "Yanlis parola goruntusu alindi" } catch {}
Yaz $Parola
Start-Sleep -Seconds 2
# Enter'dan once: nokta sayisi = ulasan karakter sayisi
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\kasa_parola_yazilmis.png' -W 640 -H 480 | Out-Null } catch {}
Tara 0x1C   # Enter
$t2 = Get-Date
"Parola yazildi: 2. acilistan +$([int]($t2-$t1).TotalSeconds) sn"
Start-Sleep -Seconds 20
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\kasa_parola_sonrasi.png' -W 640 -H 480 | Out-Null } catch {}
if (-not (Bekle 'ARMONIA-OLCUM-SONU' 240)) { "UYARI: olcum blogu gelmedi" }
"Kasa acildiktan sonra olcum: +$([int]((Get-Date)-$t2).TotalSeconds) sn"
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\vm_ekran.png' -W 640 -H 480 | Out-Null } catch {}
Stop-VM -Name $ad -TurnOff -Force; "Makine kapatildi."
Get-Content $cikti
