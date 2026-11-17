# Signierte Update-Pakete

Seit Version 1.7.0 installieren die Pis Updates nur noch als von Solarmaa signierte Pakete (Entscheid E9). Wer GitHub, den Speicherplatz im Internet oder die Leitung dazwischen kontrolliert, kann damit keinen Code mehr auf die Pis bringen. Unterschreiben kann nur, wer einen der beiden USB-Sicherheitsschlüssel von Solarmaa hat und dessen PIN kennt.

Diese Anleitung ist für Tin und die zweite Vertrauensperson. Alles läuft unter Windows in PowerShell mit den Skripten im Ordner `werkzeuge/`; installiert wird nichts.

**Die Skripte holen:** Auf GitHub angemeldet das Repository öffnen → grüner Knopf «Code» → «Download ZIP». Die ZIP-Datei entpacken; darin liegt der Ordner `werkzeuge` mit `schluessel-erzeugen.ps1` und `paket-signieren.ps1`. Das geht auch, wenn das Repository privat ist. In PowerShell in diesen Ordner wechseln (`cd $HOME\Downloads\solarmaa-mail-api-<branch>\werkzeuge`).

## Begriffe

| Begriff | Bedeutung |
|---|---|
| Sicherheitsschlüssel | USB-Schlüssel nach FIDO-Standard (z. B. YubiKey). Der geheime Teil verlässt ihn nie. |
| Hauptschlüssel / Ersatzschlüssel | Tins Sicherheitsschlüssel und der einer zweiten Vertrauensperson. Beide sind auf den Pis eingetragen; jeder allein genügt zum Freigeben. |
| Freigabe (`freigabe.txt`) | Kurze Textdatei: welche Version, für welchen Kanal, welche Datei mit welcher Prüfsumme (SHA-256), dazu die Unterschrift. |
| Paket (`solarmaa-dash-<version>.tar.gz`) | Das Archiv des freigegebenen Stands, unverändert so, wie GitHub es liefert. |
| Kanal | `test` (zuerst nur der Test-Pi) oder `stabil` (alle Kundenanlagen). Jeder Pi kennt seinen Kanal (`/etc/solarmaa-dash/update-kanal`). |
| Ablage | Öffentlicher Bucket `pi-pakete` im Supabase-Projekt, Ordner `test/` und `stabil/`. Hochladen nur über das Supabase-Dashboard. |

## Einmalig: Schlüssel erzeugen

Jede der beiden Personen an ihrem eigenen Computer:

1. Windows aktuell halten. Nötig ist der OpenSSH-Client ab Version 8.9 mit Unterstützung für Sicherheitsschlüssel (welche Windows-Fassung das mitbringt, ist nicht geprüft; das Skript meldet eine zu alte Version).
2. Den Sicherheitsschlüssel mit einer PIN versehen (YubiKey: «Yubico Authenticator», FIDO2, PIN setzen), falls noch nicht geschehen. Ohne PIN bricht das Skript ab: Jede Unterschrift verlangt die PIN (fest eingestellt, `verify-required`). Die Pis können die PIN-Pflicht nicht selbst nachprüfen (ssh-keygen kennt dafür keine Angabe in der Schlüsseldatei; mit OpenSSH 9.6 geprüft: die Angabe wird abgelehnt); sie wird darum beim Erzeugen festgelegt.
3. Den Ordner `werkzeuge` aus dem Repository auf den Computer kopieren und in PowerShell ausführen:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\schluessel-erzeugen.ps1 -Name Tin
   ```
   (die zweite Person mit ihrem eigenen Namen). Sicherheitsschlüssel einstecken, PIN eingeben, berühren, wenn er blinkt. Die Frage nach einer Passphrase mit Enter beantworten.
4. Das Skript zeigt einen **Fingerabdruck** (`SHA256:…`) und **eine Zeile**, die mit `update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com` beginnt. Den Fingerabdruck **auf Papier** notieren (mit Name und Datum).
5. **Schlüssel eintragen:** Tin schickt die zwei öffentlichen Zeilen (Haupt- und Ersatzschlüssel) an das Entwicklungsteam (Claude). Das Team trägt sie in `pi/update-schluessel/allowed_signers` und in den Block in `pi/schnellstart.sh` ein (beide gleich), prüft das mit den Tests und legt Tin die Fingerabdrücke vor. Ausgeliefert wird erst mit Tins OK.
6. Die Datei `%USERPROFILE%\.ssh\solarmaa-update-<Name>` ist nur ein Verweis auf den Sicherheitsschlüssel; ohne ihn und die PIN nützt sie nichts. Trotzdem nicht weitergeben. Geht sie verloren, braucht es einen neuen Schlüssel (siehe Notfall).

Solange in `allowed_signers` kein Schlüssel steht, bricht `install.sh` ab, bevor es etwas verändert. Ein Pi bleibt dann auf seinem bisherigen Update-Weg und erhält weiter Updates.

**Fingerabdrücke vergleichen (wer, wann, womit):**

| Wann | Wer | Womit vergleichen |
|---|---|---|
| Beim Eintragen | Tin | Die Fingerabdrücke, die das Team vorlegt, mit den beiden notierten Papierzetteln. Erst dann OK geben. |
| Bei jedem Signieren | wer signiert | `paket-signieren.ps1` zeigt «Schlüssel in diesem Stand» mit Fingerabdrücken: müssen genau den Zetteln entsprechen. Sonst nicht signieren. |
| Nach dem Umstieg des Test-Pi | Tin | Im Update-Protokoll (Wartung → Update-Protokoll) die Zeilen «Update-Schlüssel: SHA256:…» mit den Zetteln. |

Verglichen wird Zeichen für Zeichen, mindestens die ersten und letzten zehn Zeichen nach `SHA256:`.

**Ersatzschlüssel:** Die zweite Person bewahrt ihren Sicherheitsschlüssel an einem anderen Ort auf als Tin (z. B. zu Hause im abschliessbaren Fach), die PIN nicht beim Schlüssel. Empfohlen: Nach dem Eintragen einmal mit dem Ersatzschlüssel eine Freigabe für den Kanal `test` signieren und auf dem Test-Pi installieren lassen; so ist sicher, dass er im Ernstfall funktioniert.

## Ein Update freigeben

1. **Stand festlegen.** Auf GitHub den geprüften Commit heraussuchen (bei Kundenanlagen: der geprüfte Stand auf `stabil`) und die Commit-Nummer mit dem vergleichen, was geprüft wurde.
2. **Archiv laden.** Auf GitHub angemeldet `https://github.com/SolarmaaTisch/solarmaa-mail-api/archive/<COMMIT>.tar.gz` herunterladen. Nicht entpacken.
3. **Signieren.**
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\paket-signieren.ps1
   ```
   Ohne Angaben nimmt das Skript das neueste `solarmaa-mail-api-*.tar.gz` im Ordner «Downloads» (es fragt nach) und die Schlüsseldatei `.ssh\solarmaa-update-<Name>`, und es fragt nach dem Kanal. Es zeigt Version, Commit, Kanal, Grösse, Prüfsumme und die Fingerabdrücke der Schlüssel in diesem Stand. Commit und Fingerabdrücke vergleichen und mit `ja` bestätigen, dann PIN und berühren. Es prüft die Unterschrift gleich selbst gegen die Schlüssel im Archiv. **Ergebnis: Ordner `freigabe-<kanal>-<version>` auf dem Desktop** mit `solarmaa-dash-<version>.tar.gz`, `freigabe.txt` und `schnellstart.sh`.
4. **Hochladen.** Supabase-Dashboard → Storage → Bucket `pi-pakete` → Ordner des Kanals (`test` bzw. `stabil`):
   1. **zuerst** `solarmaa-dash-<version>.tar.gz` hochladen,
   2. dann die alte `freigabe.txt` löschen (Rechtsklick → Delete) und die neue `freigabe.txt` hochladen.

   Ob das Dashboard eine vorhandene Datei beim Hochladen ersetzt, ist nicht geprüft; darum zuerst löschen. Zwischen Löschen und Hochladen gibt es kurz keine Freigabe: Ein Pi, der genau dann fragt, meldet einen Fehler und versucht es später wieder. Schaden entsteht keiner, ebenso wenn die Reihenfolge einmal vertauscht wird.
5. **Test-Pi.** Im Portal oder in der Wartung des Test-Pi «Update» auslösen. Im Update-Protokoll muss stehen: `OK: <alt> → <neu> installiert (Kanal test, Commit …)`. Die Wartungsseite zeigt Version, Kanal und «Installiert aus: signiertes Paket».
6. **Kundenanlagen.** Erst wenn der Test-Pi läuft: denselben Commit nochmals mit `-Kanal stabil` signieren und in den Ordner `stabil/` hochladen. Die Pis holen ihn beim nächsten Update (Knopf, Portal oder nächtliche Prüfung).

Die öffentliche Ablage kann eine alte `freigabe.txt` bis etwa eine Stunde zwischenspeichern (Supabase-Standard; nicht geprüft). Meldet ein Pi «Bereits aktuell», später nochmals versuchen.

**Erstes signiertes Paket:** Ob ein Pi die Unterschrift eines USB-Sicherheitsschlüssels prüfen kann, liess sich ohne Sicherheitsschlüssel nicht testen (nur mit gewöhnlichen Testschlüsseln). Darum geht das erste Paket immer nur auf den Kanal `test`. Erst wenn der Test-Pi «OK: … installiert» meldet, ist die Prüfung auf Raspberry Pi OS bestätigt; meldet er «Signatur der Freigabe ungültig», nicht auf `stabil` freigeben, sondern dem Entwicklungsteam melden.

**schnellstart.sh:** Das Skript zeigt jedes Mal die Prüfsumme von `schnellstart.sh` aus diesem Stand. Sie mit dem Wert in der [Einbauanleitung](03-einbauanleitung.md) (Schritt A3) vergleichen. Weicht sie ab, wurde `schnellstart.sh` geändert: bei Kanal `stabil` die Datei `schnellstart.sh` aus dem Ausgabeordner in den Hauptordner des Buckets laden (alte zuerst löschen) und die neue Prüfsumme dem Entwicklungsteam geben, das die Einbauanleitung anpasst.

## Was der Pi prüft

`/usr/local/sbin/solarmaa-update` ruft das bereits installierte `solarmaa_update.py` auf (nur Python-Standardbibliothek, nie Code aus dem neuen Paket für die Prüfung):

1. Kanal und Quelle aus `/etc/solarmaa-dash/update-kanal` und `update-quelle` (gehören root, der Dienst kann sie nicht ändern; nicht in `config.toml`).
2. Wird gerade abgeregelt oder steht die Freigabe eines Wechselrichters aus: «Verschoben». Antwortet der Dienst nicht, entscheidet die Zustandsdatei der Abregelung (`/var/lib/solarmaa-dash/abregelung-zustand.json`): steht dort «begrenzt» oder ist sie unlesbar, wird verschoben; fehlt sie oder steht «nicht begrenzt», läuft das Update (sonst liesse sich ein abgestürzter Dienst nie per Update reparieren). Im Modus `simulation` steht keine Freigabe aus (es wird nichts geschrieben).
3. `freigabe.txt` laden (höchstens 16 KB), Unterschrift mit `ssh-keygen -Y verify` gegen `/etc/solarmaa-dash/update-schluessel` prüfen, dann Inhalt: Paket, Kanal, Version **strikt neuer** als installiert (gleich: «Bereits aktuell»; älter wird nie installiert).
4. Paket laden (höchstens die angegebene Grösse), Grösse und SHA-256 vergleichen, Commit im Archivkopf und Version im Programm vergleichen.
5. Sicher entpacken nach `/opt/solarmaa-dash/releases/<version>`: nur normale Dateien und Ordner unter `pi/`, `app/`, `tests/`; keine Links, keine absoluten Pfade, kein `..`; Rechte setzt der Pi selbst.
6. Tests und `konfig-pruefen` des neuen Stands als Dienstbenutzer, nochmals Abregelung abfragen, Marke setzen, `install.sh --update` aus dem neuen Stand, Dienst muss antworten.
7. Erfolg: `/var/lib/solarmaa-update/installiert.json` und `/opt/solarmaa-dash/paket.json` schreiben, Marke löschen; die zwei letzten Stände bleiben. Fehler: vorherigen Stand neu installieren. Bleibt die Marke nach einem Stromausfall stehen, nimmt `solarmaa-update-wiederherstellen.service` den Umstieg beim nächsten Start zurück, bevor der Dienst startet.

Es gibt keinen Rückfall auf git oder auf eine ungeprüfte Installation. `sudo solarmaa-update --erzwingen` überspringt nur die Abfrage der Abregelung, nie eine Prüfung.

## Umstieg der bestehenden Pis

Pis bis Version 1.6.x holen Updates noch mit git. Version 1.7.0 kommt einmal auf diesem alten Weg (vom Branch in `/etc/solarmaa-dash/update-branch`): Das alte Skript testet sie, ihr `install.sh` prüft zuerst `ssh-keygen` (Selbsttest mit einem Wegwerf-Schlüssel) und die eingetragenen Schlüssel und richtet dann den Paketweg ein. Fehlt etwas, bricht es ohne Änderung ab und der Pi bleibt auf dem alten Stand. Kanal: Branch `stabil` → `stabil`, jeder andere Branch → `test`.

Gegenprobe nach dem Umstieg: Im Update-Protokoll stehen die Fingerabdrücke («Update-Schlüssel: SHA256:…»). Sie müssen mit den notierten übereinstimmen.

Reihenfolge der Auslieferung: Bucket einspielen (schema.sql) → Schlüssel erzeugen und eintragen → 1.7.0 auf `stabil` (Test-Pi zuerst über seinen Branch) → Fingerabdrücke prüfen → erstes signiertes Paket auf Kanal `test`, dann `stabil` → erst danach das Repository auf privat stellen.

## Notfall

| Fall | Vorgehen |
|---|---|
| Ein Sicherheitsschlüssel verloren oder gestohlen | Mit dem anderen Schlüssel eine Version freigeben, deren `allowed_signers` den verlorenen nicht mehr enthält, zuerst `test`, dann `stabil`. Danach einen neuen Ersatzschlüssel erzeugen und mit der nächsten Version eintragen. Bis alle Pis aktualisiert sind, könnte der Dieb mit Schlüssel **und** PIN noch freigeben. |
| PIN vergessen | Nach mehreren Fehlversuchen sperrt der Sicherheitsschlüssel. Wie verloren behandeln. |
| Beide Schlüssel verloren | Die Pis laufen normal weiter, nehmen aber kein Update mehr an. Vorgehen: zwei neue Sicherheitsschlüssel einrichten (wie oben), das Entwicklungsteam trägt sie in einen neuen Stand ein. Dann muss **jeder Pi einzeln** von Solarmaa vor Ort oder über Tailscale/SSH umgestellt werden: den neuen Stand dort mit `schnellstart.sh` installieren (Konfiguration und Messwerte bleiben). Das dauert pro Anlage etwa eine halbe Stunde. Darum den Ersatzschlüssel getrennt aufbewahren. |
| Falsche Version auf `stabil` freigegeben | Eine ältere Freigabe installieren die Pis nicht. Fehler beheben, neue (höhere) Version freigeben. |
| Portal zeigt «verschoben» | Kein Fehler: Abregelung lief oder eine Freigabe stand aus. Später erneut senden. |
| Pi meldet «Signatur der Freigabe ungültig» | Falsche Datei hochgeladen, Datei unterwegs verändert, oder jemand versucht es. Freigabe neu erzeugen und hochladen; bleibt es, Solarmaa-intern prüfen. |

## Restrisiken

- Wer einen Sicherheitsschlüssel samt PIN hat, kann eine Version freigeben, die die Schlüsseldatei austauscht.
- Wer GitHub kontrolliert, kann Code in einen Commit schmuggeln, den Tin dann signiert. Darum vor dem Signieren den Commit vergleichen.
- Der erste Schlüssel kommt beim Umstieg einmal über den alten git-Weg auf die Pis (Gegenprobe Fingerabdruck).
- Nur simuliert geprüft: root, systemd, `/opt` und `/etc` auf einem echten Pi, Prüfung von Signaturen echter Sicherheitsschlüssel auf dem Pi, die Windows-Skripte.
