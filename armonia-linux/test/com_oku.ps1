# ArmoniaTest COM1 borusunu okur, olcum blogu bitince cikar. Claude, 2026-11-13.
# 14 Kasim: misafir yeniden baslarken boru kapaniyor; zaman asimina kadar yeniden baglanip okumaya devam eder.
param([string]$Boru = 'armoniacom', [string]$Cikti = 'C:\dev\armonia_com1.txt', [int]$ZamanAsimiSn = 240)
Remove-Item $Cikti -ErrorAction SilentlyContinue
$t = [Diagnostics.Stopwatch]::StartNew()
$bitti = $false
while (-not $bitti -and $t.Elapsed.TotalSeconds -lt $ZamanAsimiSn) {
  $p = New-Object System.IO.Pipes.NamedPipeClientStream('.', $Boru, [System.IO.Pipes.PipeDirection]::In)
  try { $p.Connect(5000) } catch { $p.Dispose(); Start-Sleep -Milliseconds 300; continue }
  $sr = New-Object System.IO.StreamReader($p)
  $task = $sr.ReadLineAsync()
  while ($t.Elapsed.TotalSeconds -lt $ZamanAsimiSn) {
    $hazir = $false
    try { $hazir = $task.Wait(1000) } catch { break }
    if (-not $hazir) { continue }
    $l = $null
    try { $l = $task.Result } catch { break }
    if ($null -eq $l) { break }
    Add-Content -Path $Cikti -Value $l -Encoding UTF8
    if ($l -match 'ARMONIA-OLCUM-SONU') { $bitti = $true; break }
    $task = $sr.ReadLineAsync()
  }
  try { $sr.Dispose() } catch {}
  try { $p.Dispose() } catch {}
  if (-not $bitti) { Add-Content -Path $Cikti -Value '=====COM-BORU-YENIDEN-BAGLANDI=====' -Encoding UTF8 }
}
"Okuma bitti: $([int]$t.Elapsed.TotalSeconds) sn"
