# Armonia ISO'sunu Hyper-V'de acar, COM1'den olcum blogunu toplar, ekran goruntusu alir, makineyi kapatir.
# Kullanim: powershell -File hyperv_test.ps1 [-Iso C:\dev\armonia.iso] [-RamMB 1536] [-Birak]
# Cikti: C:\dev\armonia_com1.txt (olcum), C:\dev\vm_ekran.png (ekran). Claude, 2026-11-14.
param([string]$Iso = 'C:\dev\armonia.iso', [int]$RamMB = 1536, [int]$ZamanAsimiSn = 180, [switch]$Birak)
$ErrorActionPreference = 'Stop'
$ad = 'ArmoniaTest'; $dizin = Split-Path -Parent $MyInvocation.MyCommand.Path
$cikti = 'C:\dev\armonia_com1.txt'
$eski = Get-VM -Name $ad -ErrorAction SilentlyContinue
if ($eski) { Stop-VM -Name $ad -TurnOff -Force -ErrorAction SilentlyContinue; Remove-VM -Name $ad -Force }
New-VM -Name $ad -Generation 1 -MemoryStartupBytes ([int64]$RamMB * 1MB) -NoVHD | Out-Null
Set-VM -Name $ad -ProcessorCount 2 -StaticMemory -AutomaticCheckpointsEnabled $false
Set-VMDvdDrive -VMName $ad -ControllerNumber 1 -ControllerLocation 0 -Path $Iso
# Ayni boru adi ikinci kez "Parametre hatali" veriyor: her calistirmada benzersiz ad
$boru = 'arm' + (Get-Date -Format 'MMddHHmmss')
Set-VMComPort -VMName $ad -Number 1 -Path ('\\.\pipe\' + $boru)
$sw = Get-VMSwitch -Name 'Default Switch' -ErrorAction SilentlyContinue
if ($sw) { Connect-VMNetworkAdapter -VMName $ad -SwitchName $sw.Name }
Remove-Item $cikti -ErrorAction SilentlyContinue
# Bilinen Hyper-V tuhafligi: COM borulu yeni makinenin ilk Start-VM'i 'Parametre hatali (0x80070057)' verebiliyor;
# COM yeniden ayarlaninca aciliyor. 3 deneme.
for ($d = 1; $d -le 3; $d++) {
  try { Start-VM -Name $ad -ErrorAction Stop; break }
  catch {
    if ($d -eq 3) { throw }
    "Start-VM denemesi $d basarisiz, COM yeniden ayarlaniyor..."
    $boru = 'arm' + (Get-Date -Format 'MMddHHmmss') + $d
    Set-VMComPort -VMName $ad -Number 1 -Path ('\\.\pipe\' + $boru)
  }
}
$t0 = Get-Date
$okuyucu = Start-Process powershell -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',"$dizin\com_oku.ps1",'-Boru',$boru,'-Cikti',$cikti,'-ZamanAsimiSn',$ZamanAsimiSn
$okuyucu.WaitForExit(($ZamanAsimiSn + 35) * 1000) | Out-Null
"Makine acildi: $($t0.ToString('HH:mm:ss')), olcum suresi: $([int]((Get-Date) - $t0).TotalSeconds) sn"
try { & "$dizin\vm_ekran.ps1" -Ad $ad -Cikti 'C:\dev\vm_ekran.png' -W 640 -H 480 } catch { "Ekran goruntusu alinamadi: $_" }
if (-not $Birak) { Stop-VM -Name $ad -TurnOff -Force; "Makine kapatildi." }
if (Test-Path $cikti) { Get-Content $cikti } else { "OLCUM GELMEDI (seri port bos)" }
