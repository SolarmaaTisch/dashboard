#!/usr/bin/env bash
# Solarmaa Energie-Dashboard: Erstinstallation aus einem von Solarmaa signierten Paket.
#
# Liegt im öffentlichen Bucket «pi-pakete». Nur mit geprüfter Prüfsumme ausführen (Wert in der
# Einbauanleitung, docs/03-einbauanleitung.md, Schritt A3, bzw. von Solarmaa):
#
#   curl -fsSLo schnellstart.sh https://uydqpixvoaznaalhygaw.supabase.co/storage/v1/object/public/pi-pakete/schnellstart.sh
#   echo "PRÜFSUMME  schnellstart.sh" | sha256sum -c && sudo bash schnellstart.sh [--kanal test] [--hostname NAME]
#
# Ablauf: Freigabe des Kanals laden, Signatur mit den unten eingebetteten öffentlichen Schlüsseln prüfen,
# Paket laden und SHA-256 prüfen. Erst danach läuft Code aus dem Paket (solarmaa_update.py prüft alles
# nochmals, entpackt sicher und startet install.sh). Kein git, kein ungeprüfter Rückfall.
set -euo pipefail
QUELLE="https://uydqpixvoaznaalhygaw.supabase.co/storage/v1/object/public/pi-pakete"
KANAL=stabil
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanal) KANAL="${2:-}"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[[ "$KANAL" == "stabil" || "$KANAL" == "test" ]] || { echo "--kanal muss test oder stabil sein."; exit 2; }
[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }

# Öffentliche Update-Schlüssel von Solarmaa (gleich wie pi/update-schluessel/allowed_signers)
SCHLUESSEL=$(cat <<'SCHLUESSEL'
SCHLUESSEL
)
if ! grep -q '^update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com ' <<< "$SCHLUESSEL"; then
  echo "In diesem Skript sind noch keine Update-Schlüssel eingetragen. Nichts installiert."; exit 1
fi

echo "==> Werkzeuge (openssh-client für die Signaturprüfung)"
apt-get update -qq && apt-get install -y -qq openssh-client curl python3 >/dev/null
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
printf '%s\n' "$SCHLUESSEL" > "$T/schluessel"

echo "==> Freigabe für Kanal $KANAL laden und Signatur prüfen"
curl -fsS --max-filesize 16384 -o "$T/freigabe.txt" "$QUELLE/$KANAL/freigabe.txt"
sed '/^-----BEGIN SSH SIGNATURE-----$/,$d' "$T/freigabe.txt" > "$T/manifest.json"
sed -n '/^-----BEGIN SSH SIGNATURE-----$/,$p' "$T/freigabe.txt" > "$T/freigabe.sig"
if ! LC_ALL=C /usr/bin/ssh-keygen -Y verify -f "$T/schluessel" -I update@solarmaa -n solarmaa-update \
     -s "$T/freigabe.sig" < "$T/manifest.json" >/dev/null 2>&1; then
  echo "FEHLER: Signatur der Freigabe ungültig. Nichts installiert."; exit 1
fi
read -r DATEI GROESSE SHA < <(python3 -I -c 'import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
print(m["datei"], int(m["groesse"]), m["sha256"])' "$T/manifest.json")
[[ "$DATEI" =~ ^solarmaa-dash-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz$ && "$SHA" =~ ^[0-9a-f]{64}$ ]] \
  || { echo "FEHLER: Freigabe hat ein unerwartetes Format. Nichts installiert."; exit 1; }

echo "==> Paket $DATEI laden und prüfen"
curl -fsS --max-filesize "$GROESSE" -o "$T/paket.tar.gz" "$QUELLE/$KANAL/$DATEI"
echo "$SHA  $T/paket.tar.gz" | sha256sum -c --quiet - || { echo "FEHLER: Paket passt nicht zur Freigabe."; exit 1; }
NAME=$(tar -tzf "$T/paket.tar.gz" | grep -E '^[^/]+/pi/solarmaa_update\.py$' || true)
[[ $(printf '%s\n' "$NAME" | grep -c .) -eq 1 ]] || { echo "FEHLER: Paket unvollständig."; exit 1; }
tar -xzOf "$T/paket.tar.gz" "$NAME" > "$T/solarmaa_update.py"

echo "==> Installieren"
python3 -I "$T/solarmaa_update.py" --erstinstallation "$T/freigabe.txt" "$T/paket.tar.gz" "$T/schluessel" "$KANAL" \
  -- "${ARGS[@]}"
