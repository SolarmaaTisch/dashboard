#!/usr/bin/env python3
"""Solarmaa Dashboard: Updates nur aus von Solarmaa signierten Paketen (Entscheid E9).

Läuft als root, gestartet von /usr/local/sbin/solarmaa-update (systemd oder von Hand) mit «python3 -I»
aus /usr/local/lib/solarmaa-update/. Nur Standardbibliothek; importiert nichts aus solarmaa_dash und führt
für die Prüfung nie Code aus einem neuen Paket aus.

Ablauf eines Updates
  1. Kanal (/etc/solarmaa-dash/update-kanal) und Quelle (/etc/solarmaa-dash/update-quelle) lesen. Beide
     Dateien und die Schlüsseldatei gehören root; der Dienst kann sie nicht ändern (anders als config.toml).
  2. Läuft eine Abregelung oder steht die Freigabe eines Wechselrichters aus: «Verschoben».
  3. <quelle>/<kanal>/freigabe.txt laden (höchstens 16 KB): Manifest (JSON) + SSH-Signatur.
  4. Signatur mit «ssh-keygen -Y verify» gegen /etc/solarmaa-dash/update-schluessel prüfen (nur der
     Exit-Code zählt), danach das Manifest lesen: Paket, Kanal, Version strikt grösser als installiert.
  5. Paket laden (höchstens so gross wie angegeben), Grösse und SHA-256 prüfen, sicher entpacken nach
     /opt/solarmaa-dash/releases/<version> (nur normale Dateien und Ordner unter pi/, app/, tests/).
  6. Tests und Konfigurationsprüfung als Dienstbenutzer im neuen Stand, nochmals Abregelung abfragen,
     Marke «umschalten-laeuft» setzen, install.sh --update aus dem neuen Stand, Dienst muss antworten.
  7. Erfolg: installiert.json schreiben, Marke löschen. Fehler: vorherigen Stand neu installieren.
     Bleibt die Marke stehen (Stromausfall), nimmt solarmaa-update-wiederherstellen.service den
     Umstieg beim nächsten Start zurück, bevor der Dienst startet.

Protokollzeilen (wartung.update_ergebnis liest sie): «=== Update …», «OK:», «FEHLER:», «Bereits aktuell»,
«Verschoben».
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

PAKET = "solarmaa-dash"
NAMENSRAUM = "solarmaa-update"
IDENTITAET = "update@solarmaa"
KANAELE = ("test", "stabil")
FORMAT = 1
MAX_FREIGABE = 16 * 1024
MAX_PAKET = 20 * 1024 * 1024
MAX_ENTPACKT = 200 * 1024 * 1024
MAX_EINTRAEGE = 5000
ORDNER = ("pi", "app", "tests")
SIG_ANFANG = b"-----BEGIN SSH SIGNATURE-----\n"
SIG_ENDE = b"-----END SSH SIGNATURE-----"
SCHLUESSELTYP = "sk-ssh-ed25519@openssh.com"
VERSION_RE = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
UMGEBUNG = {"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}


class UpdateFehler(Exception):
    """Fehler mit einer Meldung für das Protokoll (ohne «FEHLER:»)."""


class Pfade:
    """Alle Orte an einer Stelle (Tests setzen einen Temp-Ordner ein)."""

    def __init__(self, etc="/etc/solarmaa-dash", zustand="/var/lib/solarmaa-update", ziel="/opt/solarmaa-dash",
                 dienst_daten="/var/lib/solarmaa-dash", ssh_keygen="/usr/bin/ssh-keygen"):
        self.schluessel = os.path.join(etc, "update-schluessel")
        self.kanal = os.path.join(etc, "update-kanal")
        self.quelle = os.path.join(etc, "update-quelle")
        self.config = os.path.join(etc, "config.toml")
        self.zustand = zustand
        self.installiert = os.path.join(zustand, "installiert.json")
        self.marke = os.path.join(zustand, "umschalten-laeuft")
        self.sperre = os.path.join(zustand, "sperre")
        self.ziel = ziel
        self.releases = os.path.join(ziel, "releases")
        self.paket_json = os.path.join(ziel, "paket.json")
        self.anfrage = os.path.join(dienst_daten, "update-anfrage")
        self.ssh_keygen = ssh_keygen


# ---------------------------------------------------------------- Freigabe: zerlegen, Signatur, Manifest
def zerlegen(daten: bytes) -> tuple[bytes, bytes]:
    """freigabe.txt = Manifest (genau diese Bytes sind signiert) + SSH-Signaturblock."""
    if daten.count(SIG_ANFANG) != 1:
        raise UpdateFehler("Freigabe ohne (oder mit mehreren) Signaturblöcken")
    i = daten.index(SIG_ANFANG)
    if i == 0 or daten[i - 1:i] != b"\n":
        raise UpdateFehler("Freigabe: Signaturblock nicht am Zeilenanfang")
    manifest, signatur = daten[:i], daten[i:]
    rest = signatur.split(SIG_ENDE, 1)
    if len(rest) != 2 or rest[1].strip(b"\r\n") != b"":
        raise UpdateFehler("Freigabe: Signaturblock unvollständig oder mit Anhang")
    return manifest, signatur


def signatur_pruefen(manifest: bytes, signatur: bytes, schluessel: str, ssh_keygen: str) -> None:
    """Gültig nur, wenn ssh-keygen mit Exit-Code 0 endet (Ausgabe wird nicht ausgewertet)."""
    if not os.path.isfile(schluessel):
        raise UpdateFehler(f"Schlüsseldatei {schluessel} fehlt")
    with tempfile.TemporaryDirectory(prefix="solarmaa-sig.") as d:
        sig = os.path.join(d, "freigabe.sig")
        with open(sig, "wb") as f:
            f.write(signatur)
        try:
            r = subprocess.run([ssh_keygen, "-Y", "verify", "-f", schluessel, "-I", IDENTITAET, "-n", NAMENSRAUM,
                                "-s", sig], input=manifest, capture_output=True, env=UMGEBUNG, timeout=60)
        except (OSError, subprocess.SubprocessError) as e:
            raise UpdateFehler(f"Signaturprüfung nicht möglich ({type(e).__name__})") from e
    if r.returncode != 0:
        raise UpdateFehler("Signatur der Freigabe ungültig (nicht von Solarmaa unterschrieben oder verändert)")


def _ohne_doppelte(paare):
    d = {}
    for k, v in paare:
        if k in d:
            raise UpdateFehler(f"Manifest: Feld {k} doppelt")
        d[k] = v
    return d


def _konstante(name):
    raise UpdateFehler(f"Manifest: ungültiger Wert {name}")


def manifest_lesen(manifest: bytes) -> dict:
    """Liest das (bereits geprüfte) Manifest streng: genau diese Felder, genau diese Formen."""
    try:
        m = json.loads(manifest.decode("utf-8"), object_pairs_hook=_ohne_doppelte, parse_constant=_konstante)
    except (UnicodeDecodeError, ValueError) as e:
        raise UpdateFehler(f"Manifest ist kein gültiges JSON ({type(e).__name__})") from e
    felder = {"format", "paket", "kanal", "version", "commit", "erstellt", "datei", "groesse", "sha256"}
    if not isinstance(m, dict) or set(m) != felder:
        raise UpdateFehler("Manifest: Felder fehlen oder unbekannte Felder")

    def ganz(x):
        return isinstance(x, int) and not isinstance(x, bool)

    def text(x):
        return isinstance(x, str)

    if not (ganz(m["format"]) and m["format"] == FORMAT):
        raise UpdateFehler("Manifest: unbekanntes Format")
    if m["paket"] != PAKET:
        raise UpdateFehler("Manifest: falsches Paket")
    if not (text(m["kanal"]) and m["kanal"] in KANAELE):
        raise UpdateFehler("Manifest: unbekannter Kanal")
    if not (text(m["version"]) and VERSION_RE.fullmatch(m["version"])):
        raise UpdateFehler("Manifest: Version hat ein falsches Format")
    if not (text(m["commit"]) and re.fullmatch(r"[0-9a-f]{40}", m["commit"])):
        raise UpdateFehler("Manifest: Commit hat ein falsches Format")
    if not (text(m["erstellt"]) and len(m["erstellt"]) <= 40 and m["erstellt"].isprintable()):
        raise UpdateFehler("Manifest: Feld erstellt ungültig")
    if m["datei"] != f"{PAKET}-{m['version']}.tar.gz":
        raise UpdateFehler("Manifest: Dateiname passt nicht zur Version")
    if not (ganz(m["groesse"]) and 0 < m["groesse"] <= MAX_PAKET):
        raise UpdateFehler("Manifest: Grösse ungültig")
    if not (text(m["sha256"]) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])):
        raise UpdateFehler("Manifest: SHA-256 hat ein falsches Format")
    return m


def version_tupel(v: str) -> tuple[int, int, int]:
    if not isinstance(v, str) or not VERSION_RE.fullmatch(v):
        raise UpdateFehler(f"Version {v!r} hat ein falsches Format")
    a, b, c = v.split(".")
    return int(a), int(b), int(c)


def freigabe_pruefen(daten: bytes, schluessel: str, ssh_keygen: str, kanal: str) -> dict:
    """Erst Signatur, dann Inhalt. Liefert das Manifest."""
    manifest, signatur = zerlegen(daten)
    signatur_pruefen(manifest, signatur, schluessel, ssh_keygen)
    m = manifest_lesen(manifest)
    if m["kanal"] != kanal:
        raise UpdateFehler(f"Freigabe gilt für Kanal {m['kanal']}, dieser Pi ist auf Kanal {kanal}")
    return m


# ---------------------------------------------------------------- Laden
class _NurHttps(urllib.request.HTTPRedirectHandler):
    erlaubt = ("https://",)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith(self.erlaubt):
            raise urllib.error.HTTPError(newurl, code, "Umleitung auf unsichere Adresse", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def laden(url: str, max_bytes: int, ziel=None, nur_https: bool = True, timeout: float = 30) -> tuple[bytes | None, int, str]:
    """Lädt höchstens max_bytes. Mit ziel (offene Datei) wird gestreamt. Liefert (Daten|None, Grösse, SHA-256)."""
    erlaubt = ("https://",) if nur_https else ("https://", "http://")
    if not url.startswith(erlaubt):
        raise UpdateFehler("Quelle muss mit https:// beginnen")
    handler = type("Umleitung", (_NurHttps,), {"erlaubt": erlaubt})
    oeffner = urllib.request.build_opener(handler)
    req = urllib.request.Request(url, headers={"User-Agent": "solarmaa-update", "Cache-Control": "no-cache"})
    h, n, teile = hashlib.sha256(), 0, []
    try:
        with oeffner.open(req, timeout=timeout) as r:
            while True:
                stueck = r.read(65536)
                if not stueck:
                    break
                n += len(stueck)
                if n > max_bytes:
                    raise UpdateFehler(f"Datei grösser als erwartet (über {max_bytes} Bytes): {url.rsplit('/', 1)[-1]}")
                h.update(stueck)
                if ziel is None:
                    teile.append(stueck)
                else:
                    ziel.write(stueck)
            angegeben = r.headers.get("Content-Length")
            if angegeben is not None and angegeben.strip().isdigit() and int(angegeben) != n:
                raise UpdateFehler(f"Download abgebrochen ({n} von {angegeben} Bytes)")
    except urllib.error.HTTPError as e:
        raise UpdateFehler(f"{url.rsplit('/', 1)[-1]} nicht abrufbar (HTTP {e.code})") from e
    except (OSError, ValueError) as e:      # URLError, Zeitüberschreitung, abgebrochene Verbindung
        raise UpdateFehler(f"Quelle nicht erreichbar oder Download abgebrochen ({type(e).__name__})") from e
    return (b"".join(teile) if ziel is None else None), n, h.hexdigest()


# ---------------------------------------------------------------- Entpacken
_NAME_RE = re.compile(r"[A-Za-z0-9._+@=,~-]+")


def entpacken(paket: str, ziel: str, version: str, commit: str) -> None:
    """Entpackt nur normale Dateien und Ordner unter <präfix>/pi, /app, /tests nach ziel (darf nicht bestehen).
    Abgelehnt werden: Links, Gerätedateien, absolute Pfade, «..», fremde Präfixe, zu viele oder zu grosse
    Einträge, doppelte Dateien. Rechte werden selbst gesetzt (Ordner 755, Dateien 644, ausführbare 755)."""
    os.mkdir(ziel, 0o755)
    eintraege = summe = 0
    praefix = None
    ordner = [ziel]
    try:
        tar = tarfile.open(paket, "r:gz")
    except (tarfile.TarError, OSError) as e:
        raise UpdateFehler(f"Paket lässt sich nicht öffnen ({type(e).__name__})") from e
    with tar:
        try:
            if tar.pax_headers.get("comment") != commit:
                raise UpdateFehler("Paket stammt nicht vom freigegebenen Commit")
            for m in tar:
                eintraege += 1
                if eintraege > MAX_EINTRAEGE:
                    raise UpdateFehler("Paket hat zu viele Einträge")
                name = m.name
                if name.startswith("/") or "\\" in name or not name.isprintable():
                    raise UpdateFehler(f"Paket enthält einen ungültigen Pfad: {name[:80]!r}")
                teile = name.split("/")
                if any(t in ("", ".", "..") or not _NAME_RE.fullmatch(t) for t in teile):
                    raise UpdateFehler(f"Paket enthält einen ungültigen Pfad: {name[:80]!r}")
                if praefix is None:
                    praefix = teile[0]
                if teile[0] != praefix:
                    raise UpdateFehler("Paket enthält mehr als einen Hauptordner")
                if not (m.isreg() or m.isdir()):
                    raise UpdateFehler(f"Paket enthält einen Link oder eine Sonderdatei: {name[:80]!r}")
                rest = teile[1:]
                if not rest or rest[0] not in ORDNER:
                    continue                                   # docs/, portal/ usw. werden nicht gebraucht
                pfad = os.path.join(ziel, *rest)
                if m.isdir():
                    if not os.path.isdir(pfad):
                        os.makedirs(pfad, 0o755)
                    ordner.append(pfad)
                    continue
                summe += m.size
                if summe > MAX_ENTPACKT:
                    raise UpdateFehler("Paket ist entpackt zu gross")
                eltern = os.path.dirname(pfad)
                if not os.path.isdir(eltern):
                    os.makedirs(eltern, 0o755)
                    ordner.append(eltern)
                fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as aus, tar.extractfile(m) as ein:
                    rest_n = m.size
                    while rest_n > 0:
                        stueck = ein.read(min(65536, rest_n))
                        if not stueck:
                            raise UpdateFehler("Paket ist beschädigt (Datei zu kurz)")
                        aus.write(stueck)
                        rest_n -= len(stueck)
                os.chmod(pfad, 0o755 if m.mode & 0o111 else 0o644)
        except FileExistsError as e:
            raise UpdateFehler("Paket enthält einen Eintrag doppelt") from e
        except (tarfile.TarError, EOFError, OSError) as e:
            raise UpdateFehler(f"Paket lässt sich nicht entpacken ({type(e).__name__})") from e
    for o in ordner:
        os.chmod(o, 0o755)
    for teil in ("pi/install.sh", "pi/solarmaa_update.py", "pi/solarmaa_dash/__init__.py", "tests", "app"):
        if not os.path.exists(os.path.join(ziel, teil)):
            raise UpdateFehler(f"Paket unvollständig: {teil} fehlt")
    if version_aus_quelle(ziel) != version:
        raise UpdateFehler("Version im Paket passt nicht zur Freigabe")


def version_aus_quelle(wurzel: str) -> str | None:
    """__version__ aus <wurzel>/pi/solarmaa_dash/__init__.py, ohne das Modul zu laden."""
    try:
        with open(os.path.join(wurzel, "pi", "solarmaa_dash", "__init__.py"), encoding="utf-8") as f:
            t = f.read(10000)
    except OSError:
        return None
    m = re.search(r'^__version__\s*=\s*"([0-9.]+)"\s*$', t, re.M)
    return m.group(1) if m and VERSION_RE.fullmatch(m.group(1)) else None


# ---------------------------------------------------------------- Schlüsseldatei und Selbsttest (für install.sh)
_SCHLUESSEL_RE = re.compile(r'update@solarmaa namespaces="solarmaa-update" (\S+) ([A-Za-z0-9+/]+={0,2})(?: [ -~]*)?')


def schluessel_zeilen(text: str) -> list[tuple[str, str]]:
    """Gültige Zeilen der Schlüsseldatei (Typ, Base64). Kommentare und Leerzeilen zählen nicht;
    jede andere Zeile ist ein Fehler. Nur USB-Sicherheitsschlüssel (sk-ssh-ed25519) sind erlaubt."""
    zeilen = []
    for nr, z in enumerate(text.splitlines(), 1):
        z = z.strip()
        if not z or z.startswith("#"):
            continue
        m = _SCHLUESSEL_RE.fullmatch(z)
        if not m:
            raise UpdateFehler(f"Schlüsseldatei Zeile {nr}: falsches Format")
        if m.group(1) != SCHLUESSELTYP:
            raise UpdateFehler(f"Schlüsseldatei Zeile {nr}: nur {SCHLUESSELTYP} (USB-Sicherheitsschlüssel) erlaubt")
        zeilen.append((m.group(1), m.group(2)))
    return zeilen


def schluessel_pruefen(datei: str, ssh_keygen: str) -> list[str]:
    """Liefert die Fingerabdrücke. Fehler, wenn kein Schlüssel eingetragen ist oder einer unlesbar ist."""
    try:
        with open(datei, encoding="utf-8") as f:
            zeilen = schluessel_zeilen(f.read(100000))
    except (OSError, UnicodeDecodeError) as e:
        raise UpdateFehler(f"Schlüsseldatei nicht lesbar ({type(e).__name__})") from e
    if not zeilen:
        raise UpdateFehler("Noch keine Update-Schlüssel eingetragen (pi/update-schluessel/allowed_signers)")
    abdruecke = []
    for typ, b64 in zeilen:
        r = subprocess.run([ssh_keygen, "-l", "-f", "/dev/stdin"], input=f"{typ} {b64}\n", capture_output=True,
                           text=True, env=UMGEBUNG, timeout=30)
        teile = r.stdout.split()
        if r.returncode != 0 or len(teile) < 2 or not teile[1].startswith("SHA256:"):
            raise UpdateFehler("Schlüsseldatei enthält einen unlesbaren Schlüssel")
        abdruecke.append(f"{teile[1]} ({typ})")
    return abdruecke


def selbsttest(ssh_keygen: str) -> None:
    """Prüft mit einem Wegwerf-Schlüssel, dass ssh-keygen hier signieren und prüfen kann:
    gültig = Exit 0, verändert und falscher Namensraum = nicht 0."""
    with tempfile.TemporaryDirectory(prefix="solarmaa-selbsttest.") as d:
        def lauf(*args, eingabe=None):
            return subprocess.run([ssh_keygen, *args], input=eingabe, capture_output=True, env=UMGEBUNG, timeout=60).returncode

        k, daten, erlaubt = os.path.join(d, "k"), os.path.join(d, "daten"), os.path.join(d, "erlaubt")
        if lauf("-q", "-t", "ed25519", "-N", "", "-C", "selbsttest", "-f", k) != 0:
            raise UpdateFehler("Selbsttest: ssh-keygen kann keinen Schlüssel erzeugen")
        with open(k + ".pub", encoding="ascii") as f:
            pub = f.read().split()
        with open(erlaubt, "w", encoding="ascii") as f:
            f.write(f'selbsttest@solarmaa namespaces="{NAMENSRAUM}" {pub[0]} {pub[1]}\n')
        with open(daten, "wb") as f:
            f.write(b'{"selbsttest": 1}\n')
        if lauf("-Y", "sign", "-f", k, "-n", NAMENSRAUM, daten) != 0:
            raise UpdateFehler("Selbsttest: ssh-keygen kann nicht signieren (-Y sign)")
        pruefe = ("-Y", "verify", "-f", erlaubt, "-I", "selbsttest@solarmaa", "-s", daten + ".sig")
        if lauf(*pruefe, "-n", NAMENSRAUM, eingabe=b'{"selbsttest": 1}\n') != 0:
            raise UpdateFehler("Selbsttest: gültige Signatur wird nicht erkannt (ssh-keygen -Y verify)")
        if lauf(*pruefe, "-n", NAMENSRAUM, eingabe=b'{"selbsttest": 2}\n') == 0:
            raise UpdateFehler("Selbsttest: veränderte Daten werden als gültig erkannt")
        if lauf(*pruefe, "-n", "anderer-zweck", eingabe=b'{"selbsttest": 1}\n') == 0:
            raise UpdateFehler("Selbsttest: falscher Namensraum wird als gültig erkannt")
