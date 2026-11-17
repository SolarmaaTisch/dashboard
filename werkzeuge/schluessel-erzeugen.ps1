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
  [Parameter(Mandatory = $true)][string]$Name,
  [switch]$OhnePinPflicht
)
$ErrorActionPreference = "Stop"

function Abbruch($Text) { Write-Host ""; Write-Host "ABBRUCH: $Text" -ForegroundColor Red; exit 1 }

if ($Name -notmatch '^[A-Za-z0-9-]{2,30}$') { Abbruch "Name nur mit Buchstaben, Ziffern und Bindestrich (2 bis 30 Zeichen), z. B. Tin." }

# OpenSSH mit Unterstützung für Sicherheitsschlüssel? (Windows: ab Version 8.9 erwartet; nicht an jedem
# Windows geprüft. Ältere Fassungen kennen «ed25519-sk» nicht.)
if (-not (Get-Command ssh-keygen -ErrorAction SilentlyContinue)) {
  Abbruch "ssh-keygen nicht gefunden. Windows: Einstellungen > System > Optionale Features > «OpenSSH-Client» hinzufügen."
}
$version = (& ssh -V 2>&1 | Out-String).Trim()
Write-Host "OpenSSH: $version"
if ($version -match 'OpenSSH_(?:for_Windows_)?(\d+)\.(\d+)') {
  $haupt = [int]$Matches[1]; $neben = [int]$Matches[2]
  if ($haupt -lt 8 -or ($haupt -eq 8 -and $neben -lt 9)) {
    Abbruch "OpenSSH $haupt.$neben ist zu alt für USB-Sicherheitsschlüssel. Nötig ist 8.9 oder neuer (Windows 11 aktuell halten oder Solarmaa fragen)."
  }
} else {
  Write-Host "Version nicht erkannt, es wird trotzdem versucht." -ForegroundColor Yellow
}

$ordner = Join-Path $HOME ".ssh"
if (-not (Test-Path $ordner)) { New-Item -ItemType Directory -Path $ordner | Out-Null }
$datei = Join-Path $ordner "solarmaa-update-$Name"
if (Test-Path $datei) { Abbruch "$datei gibt es schon. Bestehenden Schlüssel nicht überschreiben (sonst sind Freigaben mit ihm nicht mehr möglich)." }

Write-Host ""
Write-Host "Sicherheitsschlüssel jetzt einstecken." -ForegroundColor Cyan
Write-Host "Gleich wird nach der PIN des Sicherheitsschlüssels gefragt und er blinkt: dann kurz berühren."
Write-Host "Danach fragt ssh-keygen nach einer Passphrase für die Verweisdatei: Enter genügt (leer)."
Write-Host ""
$optionen = @("-t", "ed25519-sk", "-C", "solarmaa-update-$Name", "-f", $datei)
if (-not $OhnePinPflicht) { $optionen = @("-O", "verify-required") + $optionen }
& ssh-keygen @optionen
if ($LASTEXITCODE -ne 0) {
  Abbruch "Schlüssel wurde nicht erzeugt. Sicherheitsschlüssel eingesteckt und berührt? Hat er eine PIN? (Ohne PIN: -OhnePinPflicht, nicht empfohlen.)"
}

$teile = (Get-Content "$datei.pub" -Raw).Trim().Split(" ")
if ($teile[0] -ne "sk-ssh-ed25519@openssh.com") { Abbruch "Unerwarteter Schlüsseltyp $($teile[0])." }
$abdruck = (& ssh-keygen -l -f "$datei.pub" | Out-String).Trim()

Write-Host ""
Write-Host "Fertig." -ForegroundColor Green
Write-Host "Fingerabdruck (notieren und mit Solarmaa am Telefon vergleichen):"
Write-Host "  $abdruck"
Write-Host ""
Write-Host "Diese eine Zeile an Solarmaa geben (öffentlich, kein Geheimnis):"
Write-Host "update@solarmaa namespaces=`"solarmaa-update`" $($teile[0]) $($teile[1]) $Name"
Write-Host ""
Write-Host "Verweisdatei (für paket-signieren.ps1): $datei"
