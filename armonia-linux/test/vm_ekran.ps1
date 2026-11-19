# Hyper-V sanal makinesinin ekran goruntusunu PNG olarak kaydeder (WMI GetVirtualSystemThumbnailImage). Claude, 2026-11-13.
param([string]$Ad = 'ArmoniaTest', [string]$Cikti = 'C:\dev\vm_ekran.png', [int]$W = 1024, [int]$H = 768)
$ErrorActionPreference = 'Stop'
$ns = 'root\virtualization\v2'
$vm = Get-CimInstance -Namespace $ns -ClassName Msvm_ComputerSystem -Filter "ElementName='$Ad'"
$vsms = Get-CimInstance -Namespace $ns -ClassName Msvm_VirtualSystemManagementService
$vssd = Get-CimAssociatedInstance -InputObject $vm -ResultClassName Msvm_VirtualSystemSettingData |
        Where-Object { $_.VirtualSystemType -eq 'Microsoft:Hyper-V:System:Realized' }
$r = Invoke-CimMethod -InputObject $vsms -MethodName GetVirtualSystemThumbnailImage -Arguments @{
        TargetSystem = $vssd; WidthPixels = [uint16]$W; HeightPixels = [uint16]$H }
if ($r.ReturnValue -ne 0 -or -not $r.ImageData) { throw "Goruntu alinamadi (kod $($r.ReturnValue))" }
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap($W, $H, [System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$rect = New-Object System.Drawing.Rectangle(0, 0, $W, $H)
$bd = $bmp.LockBits($rect, [System.Drawing.Imaging.ImageLockMode]::WriteOnly, $bmp.PixelFormat)
$n = [Math]::Min($r.ImageData.Length, $bd.Stride * $H)
[System.Runtime.InteropServices.Marshal]::Copy([byte[]]$r.ImageData, 0, $bd.Scan0, $n)
$bmp.UnlockBits($bd)
$bmp.Save($Cikti, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
"Kaydedildi: $Cikti"
