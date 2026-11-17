#!/usr/bin/env bash
# Verbindet diesen Pi mit dem Solarmaa-Portal: holt das neueste signierte Paket (solarmaa-update),
# trägt den Portal-Block in die Konfiguration ein (samt fernwartung = true; ein vorhandenes false bleibt)
# und startet den Dienst neu. Wird von install.sh als /usr/local/sbin/solarmaa-portal-verbinden installiert:
#
#   sudo solarmaa-portal-verbinden TOKEN
set -euo pipefail
TOKEN="${1:-}"
URL="${SOLARMAA_PORTAL_URL:-https://uydqpixvoaznaalhygaw.supabase.co}"
KEY="${SOLARMAA_PORTAL_KEY:-sb_publishable_VDqT93u-o0W7fiRTgQ5DEA_-Kgq9aMf}"
CONF=/etc/solarmaa-dash/config.toml

[[ $EUID -eq 0 ]] || { echo "Bitte mit sudo ausführen."; exit 1; }
[[ "$TOKEN" =~ ^[0-9a-f]{40,64}$ ]] || { echo "Token fehlt oder hat ein falsches Format."; exit 1; }
[[ -f "$CONF" ]] || { echo "Programm ist noch nicht installiert (zuerst schnellstart.sh)."; exit 1; }

echo "==> Neueste Programmversion holen (nur signierte Pakete)"
Z=/var/lib/solarmaa-dash/abregelung-zustand.json
if [[ -f "$Z" ]]; then echo "    Hinweis: Zustandsdatei vorhanden: $(cat "$Z")"; fi
if ! grep -q "abregelung.wechselrichter" "$CONF"; then
  rm -f "$Z"       # ohne konfigurierte Wechselrichter kann nichts begrenzt sein
fi
# solarmaa-update verschiebt selbst, solange abgeregelt wird oder eine Freigabe aussteht.
if /usr/local/sbin/solarmaa-update; then R=ok; else R=fehler; fi
tail -n 2 /var/log/solarmaa-dash/update.log 2>/dev/null | sed 's/^/    /' || true
[[ $R == ok ]] || echo "    Update nicht möglich; der installierte Stand bleibt (Protokoll: /var/log/solarmaa-dash/update.log)."

echo "==> Portal in die Konfiguration eintragen"
cp -p "$CONF" "$CONF.vor-portal"
python3 - "$CONF" "$URL" "$KEY" "$TOKEN" <<'PY'
import re, sys
pfad, url, key, token = sys.argv[1:5]
text = open(pfad, encoding="utf-8").read()
# Ein bewusst gesetztes fernwartung = false im bisherigen [portal]-Abschnitt bleibt erhalten;
# fehlt die Zeile, wird die Fernwartung eingeschaltet (ENTSCHEIDE E7).
bisher = re.search(r"(?ms)^\[portal\][^\n]*\n(.*?)(?=^\[|\Z)", text)
aus = bool(bisher and re.search(r"(?m)^\s*fernwartung\s*=\s*false\b", bisher.group(1)))
# vorhandenen [portal]-Abschnitt entfernen (bis zur nächsten Tabelle oder zum Dateiende)
text = re.sub(r"(?ms)^\[portal\][^\n]*\n.*?(?=^\[|\Z)", "", text).rstrip("\n")
text += (f'\n\n[portal]\nurl = "{url}"\nschluessel = "{key}"\ntoken = "{token}"\n'
         '# Fernwartung durch Solarmaa (auf false setzen, wenn nicht vereinbart)\n'
         f'fernwartung = {"false" if aus else "true"}\n')
open(pfad, "w", encoding="utf-8").write(text)
PY
chown root:solarmaa-dash "$CONF"; chmod 660 "$CONF"

echo "==> Dienst neu starten"
systemctl restart solarmaa-dash
sleep 8
if journalctl -u solarmaa-dash -n 30 --no-pager | grep -q "Portal-Verbindung aktiv"; then
  echo "Fertig. Der Pi meldet sich jetzt beim Portal. In etwa einer Minute erscheint er dort als «online»."
else
  echo "Dienst läuft, aber die Portal-Verbindung wurde nicht bestätigt. Letzte Meldungen:"
  journalctl -u solarmaa-dash -n 12 --no-pager
fi
