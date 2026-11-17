# Solarmaa: Update-Schlüssel auf einem USB-Sicherheitsschlüssel (FIDO, z. B. YubiKey) erzeugen.
#
# Einmal pro Sicherheitsschlüssel ausführen: Tin (Hauptschlüssel) und die zweite Vertrauensperson
# (Ersatzschlüssel), jede Person an ihrem eigenen Computer. Installiert nichts.
#
#   powershell -ExecutionPolicy Bypass -File .\schluessel-erzeugen.ps1 -Name Tin
#
# Danach die ausgegebene Zeile «update@solarmaa namespaces=…» an Solarmaa geben (sie ist öffentlich).
# Der geheime Teil bleibt auf dem Sicherheitsschlüssel; die Datei im Ordner .ssh ist nur ein Verweis
# darauf und nützt ohne den Sicherheitsschlüssel (und die PIN) nichts. Anleitung: docs/15-update-pakete.md
param(
  [Parameter(Mandatory = $true)][string]$Name
)
$ErrorActionPreference = "Stop"

function Abbruch($Text) { Write-Host ""; Write-Host "ABBRUCH: $Text" -ForegroundColor Red; exit 1 }

# Version von OpenSSH lesen. «ssh -V» schreibt auf den Fehlerkanal; Windows PowerShell 5.1 würde das bei
# $ErrorActionPreference = "Stop" als Fehler werten, darum hier lokal «Continue».
function OpenSSH-Version {
  $alt = $ErrorActionPreference; $ErrorActionPreference = "Continue"
  try { return ((& ssh -V 2>&1 | ForEach-Object { "$_" }) -join " ").Trim() } catch { return "" }
  finally { $ErrorActionPreference = $alt }
}
function OpenSSH-Pruefen {
  if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
    Abbruch "ssh-keygen nicht gefunden. Windows: Einstellungen > System > Optionale Features > «OpenSSH-Client» hinzufügen."
  }
  $v = OpenSSH-Version
  Write-Host "OpenSSH: $v"
  if ($v -match 'OpenSSH_(?:for_Windows_)?(\d+)\.(\d+)') {
    $haupt = [int]$Matches[1]; $neben = [int]$Matches[2]
    if ($haupt -lt 8 -or ($haupt -eq 8 -and $neben -lt 9)) {
      Abbruch "OpenSSH $haupt.$neben ist zu alt für USB-Sicherheitsschlüssel. Nötig ist 8.9 oder neuer (Windows-Updates installieren oder Solarmaa fragen)."
    }
  } else {
    # Unbekannte Schreibweise der Version: weiter versuchen. Kann ssh-keygen keine Sicherheitsschlüssel,
    # meldet es beim Erzeugen bzw. Signieren selbst einen Fehler und es entsteht nichts.
    Write-Host "Version nicht erkannt; es wird trotzdem versucht (ohne Unterstützung bricht ssh-keygen selbst ab)." -ForegroundColor Yellow
  }
}

if ($Name -notmatch '^[A-Za-z0-9-]{2,30}$') { Abbruch "Name nur mit Buchstaben, Ziffern und Bindestrich (2 bis 30 Zeichen), z. B. Tin." }

# OpenSSH mit Unterstützung für Sicherheitsschlüssel? (Windows: ab Version 8.9 erwartet; nicht an jedem
# Windows geprüft. Ältere Fassungen kennen «ed25519-sk» nicht.)
OpenSSH-Pruefen

$ordner = Join-Path $HOME ".ssh"
if (-not (Test-Path $ordner)) { New-Item -ItemType Directory -Path $ordner | Out-Null }
$datei = Join-Path $ordner "solarmaa-update-$Name"
if (Test-Path $datei) { Abbruch "$datei gibt es schon. Bestehenden Schlüssel nicht überschreiben (sonst sind Freigaben mit ihm nicht mehr möglich)." }

Write-Host ""
Write-Host "Sicherheitsschlüssel jetzt einstecken." -ForegroundColor Cyan
Write-Host "Gleich wird nach der PIN des Sicherheitsschlüssels gefragt und er blinkt: dann kurz berühren."
Write-Host "Danach fragt ssh-keygen nach einer Passphrase für die Verweisdatei: Enter genügt (leer)."
Write-Host ""
# verify-required: Jede Unterschrift verlangt die PIN des Sicherheitsschlüssels (nicht nur Berühren).
# Die Pis können das nicht selbst nachprüfen (ssh-keygen kennt dafür keine Angabe in der Schlüsseldatei);
# darum wird es hier beim Erzeugen fest eingestellt.
$optionen = @("-O", "verify-required", "-t", "ed25519-sk", "-C", "solarmaa-update-$Name", "-f", $datei)
& ssh-keygen @optionen
if ($LASTEXITCODE -ne 0) {
  Abbruch "Schlüssel wurde nicht erzeugt. Sicherheitsschlüssel eingesteckt und berührt? Hat er eine PIN (siehe docs/15, Schritt 2)?"
}

$teile = (Get-Content "$datei.pub" -Raw).Trim().Split(" ")
if ($teile[0] -ne "sk-ssh-ed25519@openssh.com") { Abbruch "Unerwarteter Schlüsseltyp $($teile[0])." }
$abdruck = (& ssh-keygen -l -f "$datei.pub" | Out-String).Trim()

Write-Host ""
Write-Host "Fertig." -ForegroundColor Green
Write-Host "Fingerabdruck (notieren und mit Solarmaa am Telefon vergleichen):"
Write-Host "  $abdruck"
Write-Host ""
Write-Host "Diese eine Zeile an Solarmaa geben (öffentlich, kein Geheimnis; per E-Mail oder Chat an das Entwicklungsteam):"
Write-Host "update@solarmaa namespaces=`"solarmaa-update`" $($teile[0]) $($teile[1]) $Name"
Write-Host ""
Write-Host "Verweisdatei (für paket-signieren.ps1): $datei"
