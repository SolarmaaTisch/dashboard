#!/usr/bin/env bash
# Solarmaa Dashboard: Update aus einem von Solarmaa signierten Paket (wird von systemd als root gestartet).
# Manuell: sudo /usr/local/sbin/solarmaa-update            (--erzwingen: auch während einer Abregelung)
# Die ganze Prüfung macht das installierte solarmaa_update.py (nur Standardbibliothek, nie Code aus dem
# neuen Paket). Kein Rückfall auf git oder eine ungeprüfte Installation. Siehe docs/15-update-pakete.md.
set -uo pipefail
LOGDIR=/var/log/solarmaa-dash   # gehört root: der Webdienst kann hier nichts unterschieben
LOG=$LOGDIR/update.log
install -d -m 755 -o root -g root "$LOGDIR"
{
  python3 -I /usr/local/lib/solarmaa-update/solarmaa_update.py "$@"
} >> "$LOG" 2>&1
RC=$?
# 75: ein anderer Lauf ist im Gang. Dann das Protokoll nicht ersetzen (er schreibt noch hinein).
if [[ $RC -eq 75 ]]; then exit 0; fi
TMP=$(mktemp "$LOGDIR/update.XXXXXX") && tail -c 200000 "$LOG" > "$TMP" && chmod 644 "$TMP" && mv "$TMP" "$LOG"
exit $RC
