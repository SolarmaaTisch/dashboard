#!/usr/bin/env bash
# Solarmaa Energie-Dashboard: Installation auf Raspberry Pi OS Lite (Bookworm oder neuer)
#
#   sudo ./pi/install.sh                    installieren oder aktualisieren
#   sudo ./pi/install.sh --hostname solarmaa-muster
#   sudo ./pi/install.sh --auto-update         zusätzlich nächtliche Update-Prüfung
#   sudo ./pi/install.sh --kanal test          Update-Kanal setzen (test oder stabil)
#
# Updates über die Weboberfläche (Wartung), das Portal oder «sudo solarmaa-update» installieren nur
# von Solarmaa signierte Pakete (docs/15-update-pakete.md). Ohne eingetragene Update-Schlüssel und ohne
# funktionierendes ssh-keygen bricht dieses Skript ab, bevor es etwas verändert: Ein Pi soll nie in
# einem Zustand landen, in dem er keine Updates mehr erhält. (Ein Aufruf aus dem alten git-Update
# geht dann auf den vorherigen Stand zurück.)
#
# Die Konfiguration /etc/solarmaa-dash/config.toml wird nur beim ersten Mal angelegt
# und bei Updates nie überschrieben.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET=/opt/solarmaa-dash
CONF_DIR=/etc/solarmaa-dash
USER_NAME=solarmaa-dash
NEW_HOSTNAME=""
AUTO_UPDATE=0
IS_UPDATE=0
KANAL=""
OHNE_NEUSTART=0     # nur für die Wiederherstellung beim Systemstart: Dateien zurück, kein systemctl
INTER_URL="https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hostname) NEW_HOSTNAME="$2"; shift 2 ;;
    --auto-update) AUTO_UPDATE=1; shift ;;
    --update) IS_UPDATE=1; shift ;;
    --kanal) KANAL="${2:-}"; shift 2 ;;
    --ohne-neustart) OHNE_NEUSTART=1; shift ;;
    *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }

PYV=$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')
[[ "$PYV" == "True" ]] || { echo "Python 3.11 oder neuer nötig (Raspberry Pi OS Bookworm)." >&2; exit 1; }
[[ -z "$KANAL" || "$KANAL" == "test" || "$KANAL" == "stabil" ]] || { echo "--kanal muss test oder stabil sein." >&2; exit 2; }

if [[ $IS_UPDATE -eq 0 ]]; then
  echo "==> Pakete"
  apt-get update -qq
  apt-get install -y -qq avahi-daemon unzip curl openssh-client >/dev/null
fi

# Vor jeder Änderung: Kann dieser Pi signierte Updates prüfen, und sind Schlüssel eingetragen?
echo "==> Signaturprüfung für Updates"
PRUEFER="$REPO_DIR/pi/solarmaa_update.py"
abbruch() { echo "$1" >&2; echo "Installation abgebrochen, nichts verändert." >&2; exit 1; }
[[ -x /usr/bin/ssh-keygen ]] || abbruch "ssh-keygen fehlt (Paket openssh-client: sudo apt-get install openssh-client)."
python3 -I "$PRUEFER" --selbsttest || abbruch "ssh-keygen kann hier keine Signaturen prüfen (OpenSSH zu alt?)."
SCHLUESSEL_INFO=$(python3 -I "$PRUEFER" --schluessel-pruefen "$REPO_DIR/pi/update-schluessel/allowed_signers") \
  || abbruch "$SCHLUESSEL_INFO"
[[ -s "$REPO_DIR/pi/update-quelle" ]] || abbruch "pi/update-quelle fehlt."

echo "==> Benutzer und Verzeichnisse"
id -u "$USER_NAME" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "$USER_NAME"
install -d -m 755 "$TARGET" "$CONF_DIR"
install -d -m 750 -o "$USER_NAME" -g "$USER_NAME" /var/lib/solarmaa-dash
install -d -m 755 -o root -g root /var/log/solarmaa-dash

echo "==> Programm kopieren nach $TARGET"
# Erst vollständig daneben kopieren, dann per Umbenennen tauschen (auch wenn REPO_DIR selbst unter $TARGET liegt)
rm -rf "$TARGET/pi.new" "$TARGET/pi.alt" "$TARGET/app.new" "$TARGET/app.alt"
cp -r "$REPO_DIR/pi" "$TARGET/pi.new"
cp -r "$REPO_DIR/app" "$TARGET/app.new"
if [[ -f "$TARGET/app/fonts/InterVariable.woff2" ]]; then
  mkdir -p "$TARGET/app.new/fonts"
  cp "$TARGET/app/fonts/InterVariable.woff2" "$TARGET/app.new/fonts/"
fi
for t in pi app; do
  if [[ -d "$TARGET/$t" ]]; then mv "$TARGET/$t" "$TARGET/$t.alt"; fi
  mv "$TARGET/$t.new" "$TARGET/$t"
  rm -rf "$TARGET/$t.alt"
done
find "$TARGET" -name '__pycache__' -prune -exec rm -rf {} +

if [[ $OHNE_NEUSTART -eq 0 && ! -f "$TARGET/app/fonts/InterVariable.woff2" ]]; then
  echo "==> Schrift Inter laden (optional)"
  TMP=$(mktemp -d)
  if curl -fsSL "$INTER_URL" -o "$TMP/inter.zip" && unzip -q -j "$TMP/inter.zip" 'web/InterVariable.woff2' -d "$TMP"; then
    mkdir -p "$TARGET/app/fonts"; cp "$TMP/InterVariable.woff2" "$TARGET/app/fonts/"
  else
    echo "    Kein Internet oder Datei nicht gefunden: App nutzt die Systemschrift."
  fi
  rm -rf "$TMP"
fi

if [[ ! -f "$CONF_DIR/config.toml" ]]; then
  echo "==> Beispiel-Konfiguration anlegen ($CONF_DIR/config.toml)"
  install -m 640 -g "$USER_NAME" "$REPO_DIR/pi/config.example.toml" "$CONF_DIR/config.toml"
fi
# Konfiguration kann Passwörter/API-Keys enthalten: nur root und der Dienst dürfen lesen
# Schreibbar für den Dienst, damit die Konfiguration im Browser (Wartung) bearbeitet werden kann.
chown root:"$USER_NAME" "$CONF_DIR/config.toml"; chmod 660 "$CONF_DIR/config.toml"

# Einmal-Code für die Ersteinrichtung im Browser (erster Admin). Wird nach Gebrauch gelöscht.
CODE_DATEI=/var/lib/solarmaa-dash/einrichtung-code
if [[ $IS_UPDATE -eq 0 && ! -f "$CODE_DATEI" ]]; then
  # tr endet mit SIGPIPE, sobald head genug hat; mit pipefail wäre das sonst ein Abbruch
  CODE=$(LC_ALL=C tr -dc 'A-HJ-NP-Z2-9' < /dev/urandom | head -c 8 || true)
  [[ ${#CODE} -eq 8 ]] || { echo "Einrichtungs-Code liess sich nicht erzeugen." >&2; exit 1; }
  echo "${CODE:0:4}-${CODE:4:4}" > "$CODE_DATEI"
  chown "$USER_NAME":"$USER_NAME" "$CODE_DATEI"; chmod 600 "$CODE_DATEI"
fi

if [[ -n "$NEW_HOSTNAME" ]]; then
  echo "==> Hostname: $NEW_HOSTNAME (erreichbar als http://$NEW_HOSTNAME.local)"
  hostnamectl set-hostname "$NEW_HOSTNAME"
  sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$NEW_HOSTNAME/" /etc/hosts
fi

echo "==> Robustheit: Hardware-Watchdog und Log-Grösse begrenzen"
mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15s\n' > /etc/systemd/system.conf.d/solarmaa-watchdog.conf
printf '[Journal]\nSystemMaxUse=50M\n' > /etc/systemd/journald.conf.d/solarmaa.conf

echo "==> Update-Weg einrichten (nur signierte Pakete)"
# Alles root-eigen und für den Dienst nicht schreibbar (anders als config.toml). Ersetzt wird atomar
# (Temp-Datei + Umbenennen): Ein gerade laufendes solarmaa-update liest seine Datei unverändert weiter.
neu() {     # neu <quelle> <ziel> <modus>
  local tmp; tmp=$(mktemp "$(dirname "$2")/.neu.XXXXXX")
  install -m "$3" -o root -g root "$1" "$tmp" && mv -f "$tmp" "$2"
}
install -d -m 700 -o root -g root /var/lib/solarmaa-update
install -d -m 755 -o root -g root "$TARGET/releases" /usr/local/lib/solarmaa-update
neu "$REPO_DIR/pi/solarmaa_update.py" /usr/local/lib/solarmaa-update/solarmaa_update.py 644
neu "$REPO_DIR/pi/update-schluessel/allowed_signers" "$CONF_DIR/update-schluessel" 644
neu "$REPO_DIR/pi/update-quelle" "$CONF_DIR/update-quelle" 644
# Kanal: --kanal gewinnt; sonst bleibt ein vorhandener. Beim Umstieg von git: Branch «stabil» -> stabil,
# jeder andere Branch (Test-Pi auf dem Entwicklungs-Branch) -> test; ohne Angabe stabil (Kundenanlage).
if [[ -z "$KANAL" && ! -f "$CONF_DIR/update-kanal" ]]; then
  BR=$(cat "$CONF_DIR/update-branch" 2>/dev/null || echo stabil)
  if [[ "$BR" == "stabil" ]]; then KANAL=stabil; else KANAL=test; fi
fi
if [[ -n "$KANAL" ]]; then
  K_TMP=$(mktemp); echo "$KANAL" > "$K_TMP"; neu "$K_TMP" "$CONF_DIR/update-kanal" 644; rm -f "$K_TMP"
fi
echo "    Kanal: $(cat "$CONF_DIR/update-kanal"), Quelle: $(cat "$CONF_DIR/update-quelle")"
echo "$SCHLUESSEL_INFO"

echo "==> Dienst und Web-Update einrichten"
install -m 644 "$REPO_DIR/pi/systemd/solarmaa-dash.service" /etc/systemd/system/solarmaa-dash.service
for u in solarmaa-dash-update.path solarmaa-dash-update.service solarmaa-dash-update.timer \
         solarmaa-update-wiederherstellen.service; do
  install -m 644 "$REPO_DIR/pi/systemd/$u" "/etc/systemd/system/$u"
done
neu "$REPO_DIR/pi/portal-verbinden.sh" /usr/local/sbin/solarmaa-portal-verbinden 755
neu "$REPO_DIR/pi/update.sh" /usr/local/sbin/solarmaa-update 755     # zuletzt: ab hier gilt der Paketweg
if [[ $OHNE_NEUSTART -eq 1 ]]; then
  # Wiederherstellung beim Systemstart (vor solarmaa-dash.service): kein systemctl, sonst wartet
  # der Neustart auf diese Einheit und umgekehrt. Geänderte Einheiten gelten ab dem nächsten Laden.
  echo "Dateien wiederhergestellt; der Dienst startet anschliessend normal."
  exit 0
fi
systemctl daemon-reload
systemctl enable solarmaa-update-wiederherstellen.service >/dev/null
systemctl enable --now solarmaa-dash-update.path >/dev/null
[[ $AUTO_UPDATE -eq 1 ]] && systemctl enable --now solarmaa-dash-update.timer >/dev/null
systemctl enable solarmaa-dash >/dev/null
systemctl restart solarmaa-dash
sleep 3
systemctl --no-pager --lines=5 status solarmaa-dash || true
if [[ $IS_UPDATE -eq 0 ]]; then
  # Eine Installation von Hand ersetzt einen unterbrochenen Paket-Umstieg: dessen Marke würde sonst beim
  # nächsten Start den vorherigen Stand zurückholen. (Mit --update ruft solarmaa-update auf; es löscht selbst.)
  sync; rm -f /var/lib/solarmaa-update/umschalten-laeuft
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "Fertig. Dashboard: http://$(hostname).local  oder  http://$IP"
echo "Einrichtung im Browser:  http://$(hostname).local/haus.html"
if [[ -f /var/lib/solarmaa-dash/einrichtung-code ]]; then
  echo "Einrichtungs-Code (für das erste Admin-Konto):  $(cat /var/lib/solarmaa-dash/einrichtung-code)"
fi
