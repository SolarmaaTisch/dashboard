"""Signierte Update-Pakete (pi/solarmaa_update.py, Entscheid E9).

Die Schlüssel hier sind NUR TEST: Sie werden pro Lauf in einem Temp-Ordner erzeugt (normales ed25519, kein
USB-Sicherheitsschlüssel) und danach gelöscht. Ohne ssh-keygen werden die Signaturtests übersprungen.
Root, systemd, /opt, /etc und die echten Geräte kommen nicht vor: Alle Pfade zeigen in Temp-Ordner,
Tests, Konfigurationsprüfung, install.sh und die Dienstabfragen sind Attrappen.
"""
import base64
import hashlib
import http.server
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))

import solarmaa_update as su  # noqa: E402

SSH_KEYGEN = shutil.which("ssh-keygen")
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def ssh(*args, eingabe=None, check=True):
    return subprocess.run([SSH_KEYGEN, *args], input=eingabe, capture_output=True, check=check,
                          env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/bin:/bin")})


class Schluessel:
    """Ein Wegwerf-Schlüsselpaar, nur für Tests."""

    def __init__(self, ordner: Path, name: str):
        self.pfad = ordner / name
        ssh("-q", "-t", "ed25519", "-N", "", "-C", f"nur-test-{name}", "-f", str(self.pfad))
        self.pub = (ordner / f"{name}.pub").read_text().split()

    def zeile(self, namensraum="solarmaa-update", identitaet="update@solarmaa") -> str:
        return f'{identitaet} namespaces="{namensraum}" {self.pub[0]} {self.pub[1]} nur-test\n'

    def signieren(self, daten: bytes, namensraum="solarmaa-update") -> bytes:
        d = Path(tempfile.mkdtemp(dir=self.pfad.parent))
        (d / "m").write_bytes(daten)
        ssh("-Y", "sign", "-f", str(self.pfad), "-n", namensraum, str(d / "m"))
        return (d / "m.sig").read_bytes()


def manifest(version="1.8.0", kanal="stabil", **anders) -> dict:
    m = {"format": 1, "paket": "solarmaa-dash", "kanal": kanal, "version": version, "commit": COMMIT,
         "erstellt": "17.11.2026 10:00", "datei": f"solarmaa-dash-{version}.tar.gz", "groesse": 1, "sha256": "0" * 64}
    m.update(anders)
    return m


def manifest_bytes(m: dict) -> bytes:
    return (json.dumps(m, ensure_ascii=False, indent=2) + "\n").encode()


def paket_bauen(version="1.8.0", commit=COMMIT, extra=(), praefix="solarmaa-mail-api-x", ohne=()) -> bytes:
    """Ein tar.gz wie das GitHub-Archiv: ein Hauptordner, Commit im pax-Kopf, darunter pi/, app/, tests/, docs/."""
    puffer = io.BytesIO()
    with tarfile.open(fileobj=puffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": commit}) as tar:
        def datei(name, inhalt: bytes, modus=0o644):
            if name in ohne:
                return
            ti = tarfile.TarInfo(f"{praefix}/{name}")
            ti.size, ti.mode = len(inhalt), modus
            tar.addfile(ti, io.BytesIO(inhalt))

        def ordner(name):
            ti = tarfile.TarInfo(f"{praefix}/{name}")
            ti.type, ti.mode = tarfile.DIRTYPE, 0o775
            tar.addfile(ti)

        ordner("")
        for o in ("pi", "pi/solarmaa_dash", "app", "tests", "docs"):
            ordner(o)
        datei("pi/install.sh", b"#!/bin/sh\necho installiert\n", 0o775)
        datei("pi/solarmaa_update.py", b"# neu\n")
        datei("pi/solarmaa_dash/__init__.py", f'"""x"""\n__version__ = "{version}"\n'.encode())
        datei("app/index.html", b"<html></html>\n", 0o664)
        datei("tests/test_x.py", b"\n")
        datei("docs/a.md", b"doku\n")
        for f in extra:
            f(tar, praefix)
    return puffer.getvalue()


@unittest.skipIf(SSH_KEYGEN is None, "ssh-keygen fehlt: Signaturtests übersprungen (Paket openssh-client)")
class FreigabeTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="nur-test-schluessel."))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.haupt, self.ersatz, self.fremd = (Schluessel(self.d, n) for n in ("haupt", "ersatz", "fremd"))
        self.erlaubt = self.d / "update-schluessel"
        self.erlaubt.write_text("# nur Test\n" + self.haupt.zeile() + self.ersatz.zeile())

    def freigabe(self, m=None, schluessel=None, namensraum="solarmaa-update") -> bytes:
        mb = manifest_bytes(m or manifest())
        return mb + (schluessel or self.haupt).signieren(mb, namensraum)

    def pruefen(self, daten: bytes, kanal="stabil") -> dict:
        return su.freigabe_pruefen(daten, str(self.erlaubt), SSH_KEYGEN, kanal)

    def test_gueltig_haupt_und_ersatz(self):
        self.assertEqual(self.pruefen(self.freigabe())["version"], "1.8.0")
        self.assertEqual(self.pruefen(self.freigabe(schluessel=self.ersatz))["version"], "1.8.0")

    def test_manifest_veraendert(self):
        f = self.freigabe()
        for alt, neu in ((b'"1.8.0"', b'"1.9.0"'), (b'"stabil"', b'"test"  '), (b"\n}", b"\n }")):
            with self.subTest(neu=neu), self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
                self.pruefen(f.replace(alt, neu, 1))

    def test_fremder_schluessel_und_falscher_namensraum(self):
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            self.pruefen(self.freigabe(schluessel=self.fremd))
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            self.pruefen(self.freigabe(namensraum="file"))
        # Schlüssel nur für einen anderen Namensraum eingetragen: gilt nicht
        self.erlaubt.write_text(self.haupt.zeile(namensraum="git"))
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            self.pruefen(self.freigabe())
        # andere Identität
        self.erlaubt.write_text(self.haupt.zeile(identitaet="jemand@anders"))
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            self.pruefen(self.freigabe())

    def test_schluesseldatei_fehlt_oder_leer(self):
        f = self.freigabe()
        self.erlaubt.write_text("# noch keine Schlüssel\n")
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            self.pruefen(f)
        self.erlaubt.unlink()
        with self.assertRaisesRegex(su.UpdateFehler, "Schlüsseldatei .* fehlt"):
            self.pruefen(f)

    def test_signatur_fehlt_oder_abgeschnitten(self):
        f = self.freigabe()
        mb = manifest_bytes(manifest())
        with self.assertRaisesRegex(su.UpdateFehler, "ohne"):
            self.pruefen(mb)
        with self.assertRaisesRegex(su.UpdateFehler, "unvollständig"):
            self.pruefen(f[:-40])
        with self.assertRaisesRegex(su.UpdateFehler, "Signatur der Freigabe ungültig"):
            sig = f[len(mb):]
            mitte = sig.index(b"\n", 40)
            self.pruefen(mb + sig[:mitte] + sig[mitte + 1 + 70:])
        with self.assertRaisesRegex(su.UpdateFehler, "mehreren"):
            self.pruefen(f + f[len(mb):])
        with self.assertRaisesRegex(su.UpdateFehler, "Anhang"):
            self.pruefen(f + b"\nnoch etwas\n")
        with self.assertRaisesRegex(su.UpdateFehler, "Zeilenanfang"):
            self.pruefen(mb.rstrip(b"\n") + f[len(mb):])

    def test_falscher_kanal(self):
        with self.assertRaisesRegex(su.UpdateFehler, "Kanal test, dieser Pi ist auf Kanal stabil"):
            self.pruefen(self.freigabe(manifest(kanal="test")))
        self.assertEqual(self.pruefen(self.freigabe(manifest(kanal="test")), kanal="test")["kanal"], "test")

    def test_signierter_aber_ungueltiger_inhalt(self):
        faelle = {
            "Version hat ein falsches Format": [manifest(version="1.8"), manifest(version="1.8.0-rc1"),
                                                manifest(version="١.8.0", datei="solarmaa-dash-١.8.0.tar.gz")],
            "Dateiname passt nicht": [manifest(datei="../solarmaa-dash-1.8.0.tar.gz"), manifest(datei="/etc/passwd"),
                                      manifest(datei="solarmaa-dash-1.9.0.tar.gz")],
            "falsches Paket": [manifest(paket="anderes")],
            "unbekanntes Format": [manifest(format=2), manifest(format=True)],
            "unbekannter Kanal": [manifest(kanal="main")],
            "Grösse ungültig": [manifest(groesse=0), manifest(groesse=su.MAX_PAKET + 1), manifest(groesse="1")],
            "SHA-256": [manifest(sha256="A" * 64)],
            "Commit": [manifest(commit="abc")],
            "Felder": [{k: v for k, v in manifest().items() if k != "sha256"}, {**manifest(), "url": "x"}],
        }
        for meldung, liste in faelle.items():
            for m in liste:
                with self.subTest(m=m), self.assertRaisesRegex(su.UpdateFehler, meldung):
                    self.pruefen(self.freigabe(m))
        doppelt = manifest_bytes(manifest()).replace(b'"format": 1,', b'"format": 1,\n  "version": "9.9.9",')
        with self.assertRaisesRegex(su.UpdateFehler, "doppelt"):
            self.pruefen(doppelt + self.haupt.signieren(doppelt))
        for kaputt in (b"[1, 2]\n", b"{\"a\": NaN}\n", b"\xff\xfe\n"):
            with self.subTest(kaputt=kaputt), self.assertRaises(su.UpdateFehler):
                self.pruefen(kaputt + self.haupt.signieren(kaputt))

    def test_versionen(self):
        self.assertGreater(su.version_tupel("1.10.0"), su.version_tupel("1.9.9"))
        for v in ("1.7", "v1.7.0", "1.7.0 ", "", None):
            with self.assertRaises(su.UpdateFehler):
                su.version_tupel(v)

    def test_selbsttest_und_schluessel_pruefen(self):
        su.selbsttest(SSH_KEYGEN)
        # Ein syntaktisch gültiger sk-ssh-ed25519-Schlüssel ohne Hardware (zufällige Bytes, NUR TEST)
        def sk_zeile(kommentar="nur-test"):
            def s(b):
                return struct.pack(">I", len(b)) + b
            blob = s(b"sk-ssh-ed25519@openssh.com") + s(os.urandom(32)) + s(b"ssh:")
            return f'update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com {base64.b64encode(blob).decode()} {kommentar}\n'
        datei = self.d / "allowed"
        datei.write_text("# Kommentar\n\n" + sk_zeile() + sk_zeile("Ersatz"))
        abdruecke = su.schluessel_pruefen(str(datei), SSH_KEYGEN)
        self.assertEqual(len(abdruecke), 2)
        self.assertTrue(all(a.startswith("SHA256:") for a in abdruecke))
        datei.write_text("# noch keine Schlüssel\n")
        with self.assertRaisesRegex(su.UpdateFehler, "Noch keine Update-Schlüssel"):
            su.schluessel_pruefen(str(datei), SSH_KEYGEN)
        datei.write_text(self.haupt.zeile())          # normaler Schlüssel: nicht erlaubt
        with self.assertRaisesRegex(su.UpdateFehler, "nur sk-ssh-ed25519"):
            su.schluessel_pruefen(str(datei), SSH_KEYGEN)
        for falsch in ('update@solarmaa cert-authority,namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com AAAA\n',
                       'update@solarmaa sk-ssh-ed25519@openssh.com AAAA\n', '* namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com AAAA\n'):
            datei.write_text(falsch)
            with self.assertRaisesRegex(su.UpdateFehler, "falsches Format"):
                su.schluessel_pruefen(str(datei), SSH_KEYGEN)
        datei.write_text('update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com AAAAB3NzaC1yc2E= x\n')
        with self.assertRaisesRegex(su.UpdateFehler, "unlesbaren"):
            su.schluessel_pruefen(str(datei), SSH_KEYGEN)


class EntpackenTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def entpacken(self, daten: bytes, version="1.8.0", commit=COMMIT) -> Path:
        p = self.d / "p.tar.gz"
        p.write_bytes(daten)
        ziel = self.d / "rel"
        shutil.rmtree(ziel, True)
        su.entpacken(str(p), str(ziel), version, commit)
        return ziel

    def test_gueltig_nur_drei_ordner_und_eigene_rechte(self):
        z = self.entpacken(paket_bauen())
        self.assertEqual(sorted(p.name for p in z.iterdir()), ["app", "pi", "tests"])
        self.assertEqual((z / "pi/install.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual((z / "app/index.html").stat().st_mode & 0o777, 0o644)
        self.assertEqual((z / "pi").stat().st_mode & 0o777, 0o755)
        self.assertEqual(su.version_aus_quelle(str(z)), "1.8.0")

    def _boese(self, art):
        def f(tar, praefix):
            ti = tarfile.TarInfo(f"{praefix}/pi/boese")
            if art == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/shadow"
            elif art == "hardlink":
                ti.type, ti.linkname = tarfile.LNKTYPE, f"{praefix}/pi/install.sh"
            elif art == "geraet":
                ti.type, ti.devmajor, ti.devminor = tarfile.CHRTYPE, 1, 3
            elif art == "fifo":
                ti.type = tarfile.FIFOTYPE
            tar.addfile(ti)
        return f

    def _name(self, name, inhalt=b"x"):
        def f(tar, praefix):
            ti = tarfile.TarInfo(name.replace("P", praefix))
            ti.size = len(inhalt)
            tar.addfile(ti, io.BytesIO(inhalt))
        return f

    def test_links_und_sonderdateien_abgelehnt(self):
        for art in ("symlink", "hardlink", "geraet", "fifo"):
            with self.subTest(art=art), self.assertRaisesRegex(su.UpdateFehler, "Link oder eine Sonderdatei"):
                self.entpacken(paket_bauen(extra=[self._boese(art)]))

    def test_link_auch_ausserhalb_der_drei_ordner_abgelehnt(self):
        def f(tar, praefix):
            ti = tarfile.TarInfo(f"{praefix}/docs/link")
            ti.type, ti.linkname = tarfile.SYMTYPE, "/"
            tar.addfile(ti)
        with self.assertRaisesRegex(su.UpdateFehler, "Link"):
            self.entpacken(paket_bauen(extra=[f]))

    def test_pfade_abgelehnt(self):
        for name in ("/etc/x", "P/../x", "P/pi/../../x", "P/pi/./x", "P//pi/x", "anderer/pi/x", "P/pi/a\\b",
                     "P/pi/a\nb", "P/pi/ä"):
            with self.subTest(name=name), self.assertRaisesRegex(su.UpdateFehler, "ungültigen Pfad|mehr als einen"):
                self.entpacken(paket_bauen(extra=[self._name(name)]))
        self.assertFalse((self.d / "x").exists())

    def test_doppelt_zu_gross_zu_viele(self):
        with self.assertRaisesRegex(su.UpdateFehler, "doppelt"):
            self.entpacken(paket_bauen(extra=[self._name("P/pi/install.sh")]))
        alt = su.MAX_ENTPACKT
        su.MAX_ENTPACKT = 30
        try:
            with self.assertRaisesRegex(su.UpdateFehler, "zu gross"):
                self.entpacken(paket_bauen(extra=[self._name("P/pi/gross", b"x" * 40)]))
        finally:
            su.MAX_ENTPACKT = alt
        alt = su.MAX_EINTRAEGE
        su.MAX_EINTRAEGE = 5
        try:
            with self.assertRaisesRegex(su.UpdateFehler, "zu viele"):
                self.entpacken(paket_bauen())
        finally:
            su.MAX_EINTRAEGE = alt

    def test_commit_version_und_vollstaendigkeit(self):
        with self.assertRaisesRegex(su.UpdateFehler, "nicht vom freigegebenen Commit"):
            self.entpacken(paket_bauen(commit="f" * 40))
        with self.assertRaisesRegex(su.UpdateFehler, "Version im Paket passt nicht"):
            self.entpacken(paket_bauen(version="1.7.9"))
        with self.assertRaisesRegex(su.UpdateFehler, "pi/install.sh fehlt"):
            self.entpacken(paket_bauen(ohne=("pi/install.sh",)))
        with self.assertRaisesRegex(su.UpdateFehler, "lässt sich nicht"):
            self.entpacken(paket_bauen()[:200])
        with self.assertRaisesRegex(su.UpdateFehler, "lässt sich nicht"):
            self.entpacken(b"kein tar")


class _Datei(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):  # noqa: N802
        self.server.abrufe.append(self.path)
        eintrag = self.server.dateien.get(self.path.split("?")[0])
        if eintrag is None:
            self.send_response(404); self.end_headers(); return
        if isinstance(eintrag, tuple):                       # (Daten, angegebene Länge): Abbruch simulieren
            daten, laenge = eintrag
        else:
            daten, laenge = eintrag, len(eintrag)
        self.send_response(200)
        self.send_header("Content-Length", str(laenge))
        self.end_headers()
        self.wfile.write(daten)


def datei_server(test: unittest.TestCase, dateien: dict):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Datei)
    srv.daemon_threads = True
    srv.dateien, srv.abrufe = dateien, []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    test.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


class LadenTests(unittest.TestCase):
    def test_grenzen_und_abbruch(self):
        srv, url = datei_server(self, {"/a": b"x" * 100, "/kurz": (b"x" * 10, 100)})
        daten, n, h = su.laden(url + "/a", 100, nur_https=False)
        self.assertEqual((daten, n, h), (b"x" * 100, 100, hashlib.sha256(b"x" * 100).hexdigest()))
        with self.assertRaisesRegex(su.UpdateFehler, "grösser als erwartet"):
            su.laden(url + "/a", 99, nur_https=False)
        with self.assertRaisesRegex(su.UpdateFehler, "HTTP 404"):
            su.laden(url + "/fehlt", 99, nur_https=False)
        with self.assertRaisesRegex(su.UpdateFehler, "abgebrochen"):
            su.laden(url + "/kurz", 1000, nur_https=False, timeout=5)
        with self.assertRaisesRegex(su.UpdateFehler, "https://"):
            su.laden(url + "/a", 100)
        with self.assertRaisesRegex(su.UpdateFehler, "https://"):
            su.laden("file:///etc/passwd", 100, nur_https=False)


if __name__ == "__main__":
    unittest.main()
