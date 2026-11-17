# Solarmaa: Update-Paket für die Pis freigeben (signieren). Installiert nichts.
#
#   1. Auf GitHub angemeldet den freigegebenen Stand als .tar.gz herunterladen:
#      https://github.com/SolarmaaTisch/solarmaa-mail-api/archive/<COMMIT>.tar.gz
#      (den Commit vorher mit dem geprüften Stand vergleichen)
#   2. powershell -ExecutionPolicy Bypass -File .\paket-signieren.ps1
#      Ohne Angaben nimmt das Skript das neueste solarmaa-mail-api-*.tar.gz im Ordner «Downloads» und die
#      einzige Datei .ssh\solarmaa-update-* und fragt nach dem Kanal. Sonst: -Archiv <Datei> -Kanal test
#      -Schluessel <Datei>. Ergebnis: Ordner «freigabe-<kanal>-<version>» auf dem Desktop.
#   3. Im Supabase-Dashboard (Storage, Bucket pi-pakete) in den Ordner des Kanals hochladen:
#      ZUERST solarmaa-dash-<version>.tar.gz, DANACH freigabe.txt (ersetzt die alte).
#
# Das Skript liest Commit und Version aus dem Archiv, zeigt alles an, fragt nach «ja», schreibt das
# Manifest, lässt es mit dem USB-Sicherheitsschlüssel signieren (PIN, berühren), prüft die Signatur mit
# den Schlüsseln aus dem Archiv selbst und legt die Dateien im Ordner «freigabe-<kanal>-<version>» ab.
# Anleitung: docs/15-update-pakete.md
param(
  [string]$Archiv,
  [ValidateSet("test", "stabil")][string]$Kanal,
  [string]$Schluessel
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

# ---------------------------------------------------------------- Werkzeuge und Dateien
OpenSSH-Pruefen
if (-not $Archiv) {
  $downloads = Join-Path $HOME "Downloads"
  $neueste = Get-ChildItem -Path $downloads -Filter "solarmaa-mail-api-*.tar.gz" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $neueste) { Abbruch "Im Ordner $downloads liegt kein solarmaa-mail-api-*.tar.gz. Archiv von GitHub laden oder -Archiv angeben." }
  $Archiv = $neueste.FullName
  if ((Read-Host "Archiv: $Archiv (geladen $($neueste.LastWriteTime)). Dieses nehmen? «ja»") -ne "ja") { Abbruch "Nichts signiert." }
}
if (-not $Schluessel) {
  $kandidaten = @(Get-ChildItem -Path (Join-Path $HOME ".ssh") -Filter "solarmaa-update-*" -ErrorAction SilentlyContinue |
                  Where-Object { $_.Extension -ne ".pub" })
  if ($kandidaten.Count -ne 1) { Abbruch "Schlüsseldatei nicht eindeutig gefunden (.ssh\solarmaa-update-<Name>). Mit -Schluessel angeben." }
  $Schluessel = $kandidaten[0].FullName
}
Write-Host "Schlüssel: $Schluessel"
if (-not (Test-Path $Archiv)) { Abbruch "Archiv $Archiv nicht gefunden." }
if (-not (Test-Path $Schluessel)) { Abbruch "Schlüsseldatei $Schluessel nicht gefunden (erzeugt mit schluessel-erzeugen.ps1)." }
$Archiv = (Resolve-Path $Archiv).Path

# ---------------------------------------------------------------- Archiv lesen (tar.gz, ohne Hilfsprogramme)
function Lies-Archiv($Pfad) {
  $ein = [System.IO.File]::OpenRead($Pfad)
  $gz = New-Object System.IO.Compression.GZipStream($ein, [System.IO.Compression.CompressionMode]::Decompress)
  $ms = New-Object System.IO.MemoryStream
  $gz.CopyTo($ms); $gz.Close(); $ein.Close()
  $b = $ms.ToArray()
  $ascii = [System.Text.Encoding]::ASCII
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  $ergebnis = @{ commit = $null; dateien = @{} }
  $pos = 0; $paxPfad = $null
  while ($pos + 512 -le $b.Length) {
    if ($b[$pos] -eq 0) { break }
    $name = $ascii.GetString($b, $pos, 100).Split([char]0)[0]
    $groesse = [Convert]::ToInt64(($ascii.GetString($b, $pos + 124, 12).Trim([char]0, ' ')), 8)
    $typ = [char]$b[$pos + 156]
    $vorsilbe = $ascii.GetString($b, $pos + 345, 155).Split([char]0)[0]
    if ($vorsilbe) { $name = "$vorsilbe/$name" }
    $daten = $pos + 512
    if ($typ -eq 'g' -or $typ -eq 'x') {
      $text = $utf8.GetString($b, $daten, $groesse)
      if ($typ -eq 'g' -and $text -match '\d+ comment=([0-9a-f]{40})\n') { $ergebnis.commit = $Matches[1] }
      if ($typ -eq 'x' -and $text -match '\d+ path=([^\n]+)\n') { $paxPfad = $Matches[1] }
    } else {
      if ($paxPfad) { $name = $paxPfad; $paxPfad = $null }
      if ($typ -eq '0' -or $typ -eq [char]0) { $ergebnis.dateien[$name] = @($daten, $groesse) }
    }
    $pos = $daten + [int64]([math]::Ceiling($groesse / 512.0) * 512)
  }
  $ergebnis.bytes = $b
  return $ergebnis
}
function Datei-Text($A, $Endung) {
  $treffer = @($A.dateien.Keys | Where-Object { $_ -match "^[^/]+/$([regex]::Escape($Endung))$" })
  if ($treffer.Count -ne 1) { return $null }
  $p = $A.dateien[$treffer[0]]
  return (New-Object System.Text.UTF8Encoding($false)).GetString($A.bytes, $p[0], $p[1])
}

Write-Host "Archiv lesen ..."
$a = Lies-Archiv $Archiv
if (-not $a.commit) { Abbruch "Kein Commit im Archivkopf. Bitte das .tar.gz direkt von GitHub verwenden (nicht entpacken und neu packen)." }
$init = Datei-Text $a "pi/solarmaa_dash/__init__.py"
if (-not $init -or $init -notmatch '(?m)^__version__\s*=\s*"(\d{1,4}\.\d{1,4}\.\d{1,4})"') { Abbruch "Version im Archiv nicht gefunden." }
$ver = $Matches[1]
$erlaubt = Datei-Text $a "pi/update-schluessel/allowed_signers"
$schnellstart = Datei-Text $a "pi/schnellstart.sh"
if (-not $erlaubt -or $erlaubt -notmatch '(?m)^update@solarmaa ') { Abbruch "Im Archiv sind keine Update-Schlüssel eingetragen. So ein Paket würde kein Pi installieren." }
# Fingerabdrücke der Schlüssel, die dieser Stand auf die Pis bringt (mit den notierten vergleichen)
$abdruecke = @()
foreach ($zeile in ($erlaubt -split "`n")) {
  $t = $zeile.Trim().Split(" ")
  if ($t.Count -ge 4 -and $t[0] -eq "update@solarmaa") {
    $tmpPub = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmpPub, "$($t[2]) $($t[3])`n")
    $kommentar = if ($t.Count -gt 4) { $t[4..($t.Count - 1)] -join " " } else { "" }
    $abdruecke += "$((& ssh-keygen -l -f $tmpPub | Out-String).Trim().Split(' ')[1])  $kommentar"
    Remove-Item $tmpPub
  }
}

if (-not $Kanal) {
  $Kanal = Read-Host "Kanal (test = nur Test-Pi, stabil = alle Kunden)"
  if ($Kanal -ne "test" -and $Kanal -ne "stabil") { Abbruch "Kanal muss test oder stabil sein." }
}
$groesse = (Get-Item $Archiv).Length
$sha = (Get-FileHash -Algorithm SHA256 $Archiv).Hash.ToLower()
$dateiname = "solarmaa-dash-$ver.tar.gz"

Write-Host ""
Write-Host "Version:  $ver"
Write-Host "Commit:   $($a.commit)   <- mit dem freigegebenen Stand auf GitHub vergleichen"
Write-Host "Kanal:    $Kanal" -ForegroundColor $(if ($Kanal -eq "stabil") { "Yellow" } else { "Cyan" })
if ($Kanal -eq "stabil") { Write-Host "          stabil = ALLE Kundenanlagen installieren diesen Stand." -ForegroundColor Yellow }
Write-Host "Datei:    $dateiname ($groesse Bytes)"
Write-Host "SHA-256:  $sha"
Write-Host "Schlüssel in diesem Stand (mit den notierten Fingerabdrücken vergleichen):"
foreach ($a2 in $abdruecke) { Write-Host "          $a2" }
Write-Host ""
if ((Read-Host "Stimmt alles? Zum Signieren «ja» eingeben") -ne "ja") { Abbruch "Nichts signiert." }

# ---------------------------------------------------------------- Manifest, Signatur, Freigabe
$ziel = Join-Path ([Environment]::GetFolderPath("Desktop")) "freigabe-$Kanal-$ver"
if (Test-Path $ziel) { Abbruch "Ordner $ziel gibt es schon (alte Freigabe?). Bitte wegräumen und neu starten." }
New-Item -ItemType Directory -Path $ziel | Out-Null
$utf8 = New-Object System.Text.UTF8Encoding($false)
$erstellt = Get-Date -Format "dd.MM.yyyy HH:mm"
$manifest = "{`n  `"format`": 1,`n  `"paket`": `"solarmaa-dash`",`n  `"kanal`": `"$Kanal`",`n  `"version`": `"$ver`",`n" +
            "  `"commit`": `"$($a.commit)`",`n  `"erstellt`": `"$erstellt`",`n  `"datei`": `"$dateiname`",`n" +
            "  `"groesse`": $groesse,`n  `"sha256`": `"$sha`"`n}`n"
$mPfad = Join-Path $ziel "manifest.json"
[System.IO.File]::WriteAllBytes($mPfad, $utf8.GetBytes($manifest))

Write-Host ""
Write-Host "Sicherheitsschlüssel einstecken; nach der PIN fragen lassen und berühren, wenn er blinkt." -ForegroundColor Cyan
& ssh-keygen -Y sign -f $Schluessel -n solarmaa-update $mPfad
if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$mPfad.sig")) { Abbruch "Signieren fehlgeschlagen." }

# Gegenprobe mit den Schlüsseln aus dem Archiv (so prüfen es auch die Pis)
$ePfad = Join-Path $ziel "allowed_signers"
[System.IO.File]::WriteAllBytes($ePfad, $utf8.GetBytes(($erlaubt -replace "`r", "")))
& cmd /c "ssh-keygen -Y verify -f `"$ePfad`" -I update@solarmaa -n solarmaa-update -s `"$mPfad.sig`" < `"$mPfad`""
if ($LASTEXITCODE -ne 0) { Abbruch "Die Signatur passt nicht zu den Schlüsseln im Archiv. Richtiger Sicherheitsschlüssel?" }

$sig = [System.IO.File]::ReadAllBytes("$mPfad.sig")
$sigText = ($utf8.GetString($sig)) -replace "`r", ""
[System.IO.File]::WriteAllBytes((Join-Path $ziel "freigabe.txt"), $utf8.GetBytes($manifest + $sigText))
Copy-Item $Archiv (Join-Path $ziel $dateiname)
Remove-Item $ePfad, $mPfad, "$mPfad.sig"
if ($schnellstart) {
  $sPfad = Join-Path $ziel "schnellstart.sh"
  [System.IO.File]::WriteAllBytes($sPfad, $utf8.GetBytes(($schnellstart -replace "`r", "")))
  $sSha = (Get-FileHash -Algorithm SHA256 $sPfad).Hash.ToLower()
}

Write-Host ""
Write-Host "Fertig: $ziel" -ForegroundColor Green
Write-Host "Im Supabase-Dashboard, Storage, Bucket pi-pakete, Ordner «$Kanal» hochladen:"
Write-Host "  1. $dateiname"
Write-Host "  2. danach freigabe.txt: die alte freigabe.txt zuerst löschen, dann die neue hochladen"
if ($schnellstart) {
  Write-Host ""
  Write-Host "SHA-256 schnellstart.sh: $sSha"
  Write-Host "Mit der Prüfsumme in der Einbauanleitung (docs/03-einbauanleitung.md, Schritt A3) vergleichen. Nur wenn"
  Write-Host "sie abweicht (schnellstart.sh wurde geändert) und Kanal stabil: schnellstart.sh in den Hauptordner des"
  Write-Host "Buckets laden und die neue Prüfsumme dem Entwicklungsteam für die Anleitung geben."
}
