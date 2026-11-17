# Wartung, Updates und Fernzugriff

## Wartung im Browser

In der Hausansicht (`/haus.html`) sieht ein Benutzer mit Rolle `admin` den Bereich **Wartung**:

- installierte Version, Stand (Commit), Kanal (`test` oder `stabil`) und woher er installiert ist (signiertes Paket oder, bis zum ersten Paket-Update, Git)
- **Update installieren** mit Bestätigung, danach das Update-Protokoll
- Status jedes Anlagen-Geräts und jedes Hauszählers (grün = liefert Daten)
- **Geräte im Netz suchen**: automatische Erkennung, mit Konfigurationsvorschlag zum Kopieren

Für Anlagen ohne Hausansicht reicht dafür `[haus] aktiv = false`: Die Wartung braucht nur einen `admin`-Benutzer.

## Alles im Browser, ohne Kommandozeile

Nach der Installation braucht es keine PowerShell und kein SSH mehr:

1. **Ersteinrichtung:** `http://<pi>/haus.html` öffnen → «Ersteinrichtung: Admin-Konto anlegen». Der Einrichtungs-Code steht am Ende der Installation (einmalig gültig).
2. **Konfiguration:** Wartung → «Konfiguration» → Laden, bearbeiten, «Prüfen und speichern». Passwörter und Tokens erscheinen als `********` und bleiben erhalten, wenn man sie nicht ändert. Ungültige Eingaben werden mit Begründung abgelehnt, die alte Fassung wird gesichert (letzte 10 unter `/var/lib/solarmaa-dash/config-sicherung`), danach startet der Dienst neu.
3. **Benutzer:** Wartung → «Benutzer»: anlegen, Passwort neu setzen, löschen.
4. **Updates, Geräteerkennung, Alarmtest:** ebenfalls in der Wartung.

Während einer Abregelung sind Konfigurationsänderung und Update gesperrt, das Update auch, solange die Freigabe eines Wechselrichters aussteht.

**Von fern:** Die Wartungsseite des Pi ist nur im Netz des Kunden erreichbar. Update und Konfiguration gehen zusätzlich über das Solarmaa-Portal, wenn die Fernwartung auf dem Pi eingeschaltet ist (nächster Abschnitt). Für alles andere (Benutzer, Geräteerkennung, Alarmtest, SSH) den Pi mit Tailscale verbinden (siehe unten).

## Fernwartung über das Portal

Ab Version 1.6.0, ausführlich in [13-portal.md](13-portal.md), Kapitel «Fernwartung».

- Einschalten nur vor Ort: in der Konfiguration `[portal] fernwartung = true`. Standard ist aus.
- Im Portal können Solarmaa-Konten mit Zwei-Faktor-Anmeldung dann **Update auslösen** und die **Konfiguration laden, ändern und senden**. Der Pi holt den Befehl bei seiner nächsten Meldung ab (etwa eine Minute), prüft ihn selbst und meldet das Ergebnis.
- Aus der Ferne nicht änderbar: `[portal]`, `[server]`, `[daten]` und der Demo-Modus.
- Während einer Abregelung lehnt der Pi Fernbefehle ab, gleich wie die lokale Wartung.
- Ein Update aus der Ferne läuft über denselben Weg wie der Knopf «Update installieren» (nächster Abschnitt); installiert wird die neueste signierte Freigabe des Kanals in `/etc/solarmaa-dash/update-kanal`.
- Der Pi merkt sich laufende Befehle und noch nicht übermittelte Quittungen in `/var/lib/solarmaa-dash/portal-befehle.json`. Jeder Fernbefehl steht im Protokoll des Dienstes (`journalctl -u solarmaa-dash`, Zeilen «Fernwartung: …»); die Wartungsseite zeigt ihn noch nicht an.

## Wie das Update funktioniert

Ab Version 1.7.0 installiert der Pi nur noch von Solarmaa signierte Pakete (Entscheid E9). Freigeben, Schlüssel und Notfall: [15-update-pakete.md](15-update-pakete.md).

1. Ein Klick auf «Update installieren» (oder ein Fernbefehl) legt die Datei `/var/lib/solarmaa-dash/update-anfrage` an. Der Webdienst selbst hat keine Root-Rechte.
2. systemd bemerkt die Datei (`solarmaa-dash-update.path`) und startet `/usr/local/sbin/solarmaa-update` als root. Das ruft das installierte `/usr/local/lib/solarmaa-update/solarmaa_update.py` auf.
3. Wird gerade abgeregelt oder steht die Freigabe eines Wechselrichters aus, steht im Protokoll «Verschoben» und es passiert nichts (im Portal: «verschoben», kein Fehler). Antwortet der Dienst nicht, entscheidet die Zustandsdatei der Abregelung (Details in [15-update-pakete.md](15-update-pakete.md)).
4. Der Pi lädt die Freigabe seines Kanals aus dem öffentlichen Bucket `pi-pakete` (Adresse in `/etc/solarmaa-dash/update-quelle`), prüft die Unterschrift mit den Schlüsseln in `/etc/solarmaa-dash/update-schluessel` und installiert nur eine **neuere** Version. Dann lädt er das Paket, prüft Grösse und SHA-256 und entpackt es nach `/opt/solarmaa-dash/releases/<version>`.
5. Er **führt die Tests aus** (ohne Root-Rechte) und lädt die vorhandene Konfiguration mit dem neuen Programmstand (`konfig-pruefen`, ohne Geräte anzusprechen); passt sie nicht, bleibt die alte Version und im Protokoll steht «FEHLER: Konfiguration passt nicht zur neuen Version: …».
6. Direkt vor dem Umschalten fragt er nochmals nach der Abregelung, installiert dann und wartet etwa 40 Sekunden (höchstens gut eine Minute), ob der Dienst antwortet. Sonst installiert er den vorherigen Stand wieder. Wird er beim Umschalten unterbrochen (Stromausfall), stellt `solarmaa-update-wiederherstellen.service` beim nächsten Start den vorherigen Stand her, bevor der Dienst startet.

Alles steht im Update-Protokoll (`/var/log/solarmaa-dash/update.log`). Manuell per SSH: `sudo solarmaa-update` (`--erzwingen` überspringt nur die Abfrage der Abregelung, keine Prüfung).

Nächtliche automatische Prüfung (optional) einschalten: `sudo /opt/solarmaa-dash/releases/<version>/pi/install.sh --update --auto-update`, wobei `<version>` die installierte Version ist (Wartungsseite oder `cat /opt/solarmaa-dash/paket.json`). Vor dem ersten Paket-Update (Umstieg von git) stattdessen `sudo /opt/solarmaa-dash/src/pi/install.sh --update --auto-update`. Die Prüfung startet um 03:17 Uhr plus eine zufällige Verzögerung bis 45 Minuten, damit nicht alle Anlagen gleichzeitig laden.

### Zugang

Die Pakete liegen öffentlich (sie enthalten keine Geheimnisse); der Pi braucht keinen GitHub-Zugang und keinen Deploy-Key. Pis bis 1.6.x holen 1.7.0 noch einmal über git; danach ist git auf dem Pi nicht mehr nötig. Das Repository kann nach dem ersten erfolgreichen Paket-Update auf privat gestellt werden.

## Fernzugriff ohne Portfreigabe

Damit Solarmaa vom Büro aus alle Anlagen erreicht (Wartungsseite, SSH), ohne beim Kunden Ports im Router zu öffnen, eignet sich ein Mesh-VPN wie **Tailscale**:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname solarmaa-muster
```

Danach ist der Pi im Solarmaa-Tailnet unter `http://solarmaa-muster` erreichbar. Wichtig:

- Den Kunden informieren und in den Vertrag aufnehmen (Fernwartungszugang).
- Im Tailscale-Adminbereich die Geräte mit Tags versehen und den Zugriff auf Solarmaa-Konten beschränken.
- Lizenz- und Kostenbedingungen von Tailscale für die Anzahl Geräte prüfen. Alternativen: WireGuard mit eigenem Server, Netbird, ZeroTier.

## Checkliste bei Problemen

| Problem | Prüfen |
|---|---|
| Update-Protokoll: «Konfiguration passt nicht zur neuen Version» | Grund steht dabei; Konfiguration vor Ort anpassen, dann Update erneut starten. Die alte Version läuft weiter |
| Dienst startet nicht, im Journal «Konfiguration ungültig, Dienst startet nicht: …» | Konfiguration vor Ort korrigieren (`sudo nano /etc/solarmaa-dash/config.toml`), dann `sudo systemctl restart solarmaa-dash` |
| «Web-Update nicht eingerichtet» | `install.sh` mit neuer Version erneut ausführen |
| Update-Protokoll: «Quelle nicht erreichbar» oder «HTTP …» | Internet beim Kunden; Freigabe und Paket im Bucket `pi-pakete` im richtigen Kanal-Ordner? |
| Update-Protokoll: «Signatur der Freigabe ungültig» | Nicht installiert. Freigabe neu erzeugen und hochladen ([15-update-pakete.md](15-update-pakete.md)); bleibt es, Solarmaa-intern prüfen |
| Update-Protokoll: «Bereits aktuell» obwohl eine neue Version freigegeben ist | Zwischenspeicher der Ablage (bis etwa 1 Stunde), Kanal des Pi (`/etc/solarmaa-dash/update-kanal`) |
| Update-Protokoll: «Verschoben» | Abregelung aktiv oder Freigabe eines Wechselrichters steht aus. Später erneut starten |
| `install.sh`: «Noch keine Update-Schlüssel eingetragen» oder «ssh-keygen fehlt» | Nichts verändert. Schlüssel in `pi/update-schluessel/allowed_signers` eintragen bzw. `sudo apt-get install openssh-client` |
| Update-Protokoll: «Tests fehlgeschlagen» | Alte Version läuft weiter. Fehler im Protokoll an die Entwicklung |
| Portal: Befehl bleibt auf «wartet auf den Pi» | Pi offline, oder Fernwartung auf dem Pi aus (dann nimmt das Portal gar keine Befehle an). Nach einer Stunde verfällt der Befehl |
| Portal: Befehl bleibt auf «abgeholt, läuft» bzw. «keine Quittung» | Pi hat den Befehl, konnte aber noch nicht antworten (Update läuft, Internet weg, Strom weg). Die Quittung wird nachgeliefert, sobald der Pi wieder meldet. Vor Ort: Update-Protokoll und `portal-befehle.json` ansehen |
| Portal: Befehl «fehler: Abgelehnt …» | Begründung steht dabei: Abregelung aktiv oder Freigabe eines Wechselrichters steht noch aus (später erneut senden; bleibt es dabei, vor Ort die Abregelung prüfen), gesperrter Abschnitt, Konfiguration inzwischen geändert (im Portal neu laden), zu viele Befehle in einer Stunde |
| Portal: «Geheimnis neu eingeben» | Im geänderten Abschnitt steht ein verdecktes Passwort. Den echten Wert statt `********` eintragen |
| Portal zeigt keine Konfiguration | Fernwartung auf dem Pi aus, oder ein geheimer Wert steht in einer Form in der Datei, die nicht verdeckt werden kann (Warnung in `journalctl -u solarmaa-dash`) |
| Gerät rot in der Wartung | Meldung daneben; `python3 -m solarmaa_dash geraet-test` per SSH |
