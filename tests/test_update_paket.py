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


_SCHLUESSEL: dict = {}


def schluessel(name: str) -> "Schluessel":
    """Wegwerf-Schlüssel, einmal pro Testlauf erzeugt (spart Zeit auf dem Pi) und am Ende gelöscht."""
    if name not in _SCHLUESSEL:
        if not _SCHLUESSEL:
            ordner = Path(tempfile.mkdtemp(prefix="nur-test-schluessel."))
            unittest.addModuleCleanup(shutil.rmtree, ordner, True)
            _SCHLUESSEL["_ordner"] = ordner
        _SCHLUESSEL[name] = Schluessel(_SCHLUESSEL["_ordner"], name)
    return _SCHLUESSEL[name]


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
        self.haupt, self.ersatz, self.fremd = (schluessel(n) for n in ("haupt", "ersatz", "fremd"))
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
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
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


@unittest.skipIf(SSH_KEYGEN is None, "ssh-keygen fehlt: Ablauftests übersprungen (Paket openssh-client)")
class AblaufTests(unittest.TestCase):
    """Der ganze Lauf mit Attrappen: Pfade im Temp-Ordner, Tests/konfig-pruefen/install.sh/Dienst ersetzt."""

    def setUp(self):
        import contextlib
        self.d = Path(tempfile.mkdtemp(prefix="nur-test-ablauf."))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.haupt, self.fremd = schluessel("haupt"), schluessel("fremd")
        etc, ziel = self.d / "etc", self.d / "opt"
        for o in (etc, ziel / "pi/solarmaa_dash", ziel / "app", self.d / "dienst"):
            o.mkdir(parents=True)
        (etc / "update-schluessel").write_text(self.haupt.zeile())
        (etc / "update-kanal").write_text("stabil\n")
        (etc / "config.toml").write_text('[server]\nport = 1\n')
        (ziel / "pi/solarmaa_dash/__init__.py").write_text('__version__ = "1.7.0"\n')
        (ziel / "pi/install.sh").write_text("#!/bin/sh\n")
        (ziel / "app/index.html").write_text("alt\n")
        (self.d / "dienst/update-anfrage").write_text("admin\n")
        self.p = su.Pfade(etc=str(etc), zustand=str(self.d / "zustand"), ziel=str(ziel),
                          dienst_daten=str(self.d / "dienst"), ssh_keygen=SSH_KEYGEN)
        os.makedirs(self.p.zustand, 0o700)
        os.makedirs(self.p.releases)
        self.dateien = {}
        self.srv, url = datei_server(self, self.dateien)
        (etc / "update-quelle").write_text(url + "/pi-pakete\n")
        self.u = self._updater()
        self.ausgabe = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.ausgabe))

    def _updater(self):
        test = self

        class U(su.Updater):
            nur_https = False
            tests_ok, konfig_fehler, install_ok, dienst_ok = True, None, [True], True
            gruende = []
            aufrufe = []

            def abregelung_grund(self):
                return self.gruende.pop(0) if self.gruende else None

            def tests_laufen(self, release):
                self.aufrufe.append(("tests", os.path.basename(release)))
                return self.tests_ok

            def konfig_pruefen(self, release):
                self.aufrufe.append(("konfig", os.path.basename(release)))
                return self.konfig_fehler

            def installieren(self, release, ohne_neustart=False):
                self.aufrufe.append(("install", os.path.basename(release), ohne_neustart))
                test.assertTrue(os.path.exists(self.p.marke), "Marke muss vor install.sh stehen")
                ok = self.install_ok.pop(0) if len(self.install_ok) > 1 else self.install_ok[0]
                if ok:   # wie install.sh: Programm nach /opt kopieren
                    for teil in ("pi", "app"):
                        shutil.rmtree(os.path.join(self.p.ziel, teil), True)
                        shutil.copytree(os.path.join(release, teil), os.path.join(self.p.ziel, teil))
                return ok

            def dienst_antwortet(self):
                return self.dienst_ok

        return U(self.p)

    def veroeffentlichen(self, version="1.8.0", kanal="stabil", schluessel=None, paket=None, **anders):
        paket = paket if paket is not None else paket_bauen(version)
        m = manifest(version, kanal, groesse=len(paket), sha256=hashlib.sha256(paket).hexdigest())
        m.update(anders)
        mb = manifest_bytes(m)
        self.dateien[f"/pi-pakete/{kanal}/freigabe.txt"] = mb + (schluessel or self.haupt).signieren(mb)
        self.dateien[f"/pi-pakete/{kanal}/solarmaa-dash-{version}.tar.gz"] = paket
        return m

    def lauf(self, erzwingen=False) -> int:
        self.srv.abrufe.clear()
        return self.u.aktualisieren(erzwingen)

    def installiert(self):
        return su._json_lesen(self.p.installiert)

    def releases(self):
        return sorted(os.listdir(self.p.releases))

    def ergebnis(self):
        from solarmaa_dash import config, wartung
        from unittest import mock
        log = self.d / "update.log"
        log.write_text(self.ausgabe.getvalue(), encoding="utf-8")
        with mock.patch.object(wartung, "UPDATE_LOG", log):
            return wartung.Wartung(config.load(None), log.parent).update_ergebnis(0)

    def test_erfolg_und_zweites_update(self):
        self.veroeffentlichen("1.8.0")
        self.assertEqual(self.lauf(), 0, self.ausgabe.getvalue())
        self.assertFalse(os.path.exists(self.p.anfrage))
        self.assertEqual(self.u.aufrufe, [("tests", "1.8.0"), ("konfig", "1.8.0"), ("install", "1.8.0", False)])
        inst = self.installiert()
        self.assertEqual((inst["version"], inst["release"], inst["vorher"], inst["kanal"], inst["commit"]),
                         ("1.8.0", "1.8.0", "vorher-1.7.0", "stabil", COMMIT))
        self.assertEqual(os.stat(self.p.installiert).st_mode & 0o777, 0o600)
        anzeige = json.loads(Path(self.p.paket_json).read_text())
        self.assertEqual((anzeige["version"], anzeige["kanal"], anzeige["commit"]), ("1.8.0", "stabil", COMMIT))
        self.assertNotIn("sha256", anzeige)
        self.assertEqual(os.stat(self.p.paket_json).st_mode & 0o777, 0o644)
        self.assertFalse(os.path.exists(self.p.marke))
        self.assertEqual(self.releases(), ["1.8.0", "vorher-1.7.0"])
        self.assertEqual(Path(self.p.releases, "vorher-1.7.0/app/index.html").read_text(), "alt\n")
        self.assertEqual(self.ergebnis(), (True, f"OK: 1.7.0 → 1.8.0 installiert (Kanal stabil, Commit {COMMIT[:7]})."))
        self.assertFalse(os.path.exists(os.path.join(self.p.zustand, "paket.tmp")))
        # nochmals: bereits aktuell, nur die Freigabe wird geladen
        self.assertEqual(self.lauf(), 0)
        self.assertEqual(self.srv.abrufe, ["/pi-pakete/stabil/freigabe.txt"])
        self.assertTrue(self.ausgabe.getvalue().rstrip().endswith("Bereits aktuell (1.8.0)."))
        # nächste Version: höchstens zwei Stände bleiben
        self.u.aufrufe.clear()
        self.veroeffentlichen("1.9.0")
        self.assertEqual(self.lauf(), 0, self.ausgabe.getvalue())
        self.assertEqual(self.releases(), ["1.8.0", "1.9.0"])
        self.assertEqual((self.installiert()["version"], self.installiert()["vorher"]), ("1.9.0", "1.8.0"))

    def test_aeltere_freigabe_wird_nicht_installiert(self):
        self.veroeffentlichen("1.6.9")
        self.assertEqual(self.lauf(), 0)
        self.assertEqual(self.srv.abrufe, ["/pi-pakete/stabil/freigabe.txt"])
        self.assertIn("freigegeben ist die ältere 1.6.9, sie wird nicht installiert", self.ausgabe.getvalue())
        self.assertIsNone(self.installiert())
        self.assertEqual(self.u.aufrufe, [])
        self.veroeffentlichen("1.7.0")
        self.assertEqual(self.lauf(), 0)
        self.assertEqual(self.ergebnis(), (True, "Bereits aktuell (1.7.0)."))

    def test_ungueltige_freigabe_laedt_kein_paket(self):
        for schluessel, kanal in ((self.fremd, "stabil"), (self.haupt, "test")):
            self.dateien.clear()
            self.veroeffentlichen("1.8.0", kanal=kanal, schluessel=schluessel)
            if kanal == "test":    # Freigabe für «test» unter dem Pfad von «stabil» abgelegt
                self.dateien["/pi-pakete/stabil/freigabe.txt"] = self.dateien.pop("/pi-pakete/test/freigabe.txt")
            with self.subTest(kanal=kanal):
                self.assertEqual(self.lauf(), 1)
                self.assertEqual(self.srv.abrufe, ["/pi-pakete/stabil/freigabe.txt"])
                self.assertEqual(self.releases(), [])
                self.assertEqual(self.u.aufrufe, [])
                self.assertFalse(self.ergebnis()[0])
        self.assertIn("FEHLER: Signatur der Freigabe ungültig", self.ausgabe.getvalue())
        self.assertIn("FEHLER: Freigabe gilt für Kanal test, dieser Pi ist auf Kanal stabil", self.ausgabe.getvalue())

    def test_paket_passt_nicht(self):
        echt = paket_bauen("1.8.0")
        for name, paket in (("kürzer", echt[:-10]), ("länger", echt + b"x"), ("anders", echt[:-1] + bytes([echt[-1] ^ 1]))):
            with self.subTest(name):
                m = self.veroeffentlichen("1.8.0")
                self.dateien["/pi-pakete/stabil/solarmaa-dash-1.8.0.tar.gz"] = paket
                self.assertEqual(self.lauf(), 1)
                self.assertRegex(self.ausgabe.getvalue(), "FEHLER: (Paket passt nicht zur Freigabe|Datei grösser als erwartet)")
                self.assertEqual(self.releases(), [])
                self.assertEqual(self.u.aufrufe, [])
                self.assertEqual(m["groesse"], len(echt))
        self.dateien["/pi-pakete/stabil/solarmaa-dash-1.8.0.tar.gz"] = (echt[:100], len(echt))   # Download bricht ab
        self.assertEqual(self.lauf(), 1)
        self.assertIn("abgebrochen", self.ausgabe.getvalue().splitlines()[-1])
        self.assertEqual(self.releases(), [])

    def test_freigabe_zu_gross(self):
        self.veroeffentlichen("1.8.0")
        f = self.dateien["/pi-pakete/stabil/freigabe.txt"]
        self.dateien["/pi-pakete/stabil/freigabe.txt"] = b" " * (su.MAX_FREIGABE - len(f) + 1) + f
        self.assertEqual(self.lauf(), 1)
        self.assertIn("grösser als erwartet", self.ausgabe.getvalue())

    def test_boeses_paket_wird_nicht_entpackt(self):
        def link(tar, praefix):
            ti = tarfile.TarInfo(f"{praefix}/pi/x")
            ti.type, ti.linkname = tarfile.SYMTYPE, "/etc"
            tar.addfile(ti)
        self.veroeffentlichen("1.8.0", paket=paket_bauen("1.8.0", extra=[link]))
        self.assertEqual(self.lauf(), 1)
        self.assertIn("Link oder eine Sonderdatei", self.ausgabe.getvalue())
        self.assertEqual(self.releases(), [])
        self.assertEqual(self.u.aufrufe, [])

    def _unveraendert(self):
        self.assertIsNone(self.installiert())
        self.assertFalse(os.path.exists(self.p.marke))
        self.assertEqual(Path(self.p.ziel, "pi/solarmaa_dash/__init__.py").read_text(), '__version__ = "1.7.0"\n')
        self.assertFalse(self.ergebnis()[0])

    def test_tests_oder_konfiguration_scheitern(self):
        self.veroeffentlichen("1.8.0")
        self.u.tests_ok = False
        self.assertEqual(self.lauf(), 1)
        self.assertEqual(self.u.aufrufe, [("tests", "1.8.0")])
        self.assertIn("FEHLER: Tests von 1.8.0 fehlgeschlagen", self.ausgabe.getvalue())
        self.assertEqual(self.releases(), [])
        self._unveraendert()
        self.u.tests_ok, self.u.konfig_fehler = True, "smartme.basis_url muss mit https:// beginnen"
        self.u.aufrufe.clear()
        self.assertEqual(self.lauf(), 1)
        self.assertEqual([a[0] for a in self.u.aufrufe], ["tests", "konfig"])
        self.assertIn("FEHLER: Konfiguration passt nicht zur neuen Version: smartme.basis_url", self.ausgabe.getvalue())
        self._unveraendert()

    def test_installation_oder_dienst_scheitert_zurueck(self):
        self.veroeffentlichen("1.8.0")
        for install_ok, dienst_ok in (([False, True], True), ([True], False)):
            with self.subTest(install=install_ok, dienst=dienst_ok):
                self.u.aufrufe.clear()
                self.u.install_ok, self.u.dienst_ok = install_ok, dienst_ok
                self.assertEqual(self.lauf(), 1)
                self.assertEqual([a for a in self.u.aufrufe if a[0] == "install"],
                                 [("install", "1.8.0", False), ("install", "vorher-1.7.0", False)])
                self.assertIn("zurück auf 1.7.0", self.ausgabe.getvalue())
                self.assertIn("Alte Version vorher-1.7.0 wiederhergestellt.", self.ausgabe.getvalue())
                self.assertEqual(self.releases(), ["vorher-1.7.0"])
                self._unveraendert()

    def test_stromausfall_beim_umschalten_und_wiederherstellung(self):
        self.veroeffentlichen("1.8.0")
        self.u.install_ok = [False, False]           # neu scheitert, Rückweg scheitert auch
        self.assertEqual(self.lauf(), 1)
        self.assertTrue(os.path.exists(self.p.marke))
        self.assertIn("FEHLER: Wiederherstellung von vorher-1.7.0 fehlgeschlagen", self.ausgabe.getvalue())
        # Systemstart: solarmaa-update --wiederherstellen (ohne Neustart des Dienstes)
        self.u.install_ok, self.u.aufrufe = [True], []
        self.assertEqual(self.u.wiederherstellen(ohne_neustart=True), 0)
        self.assertEqual(self.u.aufrufe, [("install", "vorher-1.7.0", True)])
        self.assertFalse(os.path.exists(self.p.marke))
        # Marke steht noch beim nächsten Lauf: zuerst zurück, dann normal weiter
        su._schreiben(self.p.marke, json.dumps({"ziel": "1.8.0", "zurueck": "vorher-1.7.0"}).encode(), 0o600)
        self.u.aufrufe = []
        self.assertEqual(self.lauf(), 0, self.ausgabe.getvalue())
        self.assertEqual([a[1] for a in self.u.aufrufe if a[0] == "install"], ["vorher-1.7.0", "1.8.0"])
        self.assertEqual(self.installiert()["version"], "1.8.0")
        # Marke mit Pfad nach draussen: nichts ausführen
        su._schreiben(self.p.marke, json.dumps({"zurueck": "../../tmp/x"}).encode(), 0o600)
        self.u.aufrufe = []
        self.assertEqual(self.u.wiederherstellen(True), 1)
        self.assertEqual(self.u.aufrufe, [])

    def test_wiederherstellen_beim_start(self):
        from unittest import mock
        with mock.patch.object(su, "Pfade", return_value=self.p), mock.patch.object(su, "Updater", lambda p: self.u):
            self.assertEqual(su.main(["--wiederherstellen"]), 0)      # keine Marke: nichts
            self.assertEqual(self.ausgabe.getvalue(), "")
            os.makedirs(os.path.join(self.p.releases, "vorher-1.7.0", "pi"))
            os.makedirs(os.path.join(self.p.releases, "vorher-1.7.0", "app"))
            Path(self.p.releases, "vorher-1.7.0", "pi", "install.sh").write_text("")
            su._schreiben(self.p.marke, json.dumps({"ziel": "1.8.0", "zurueck": "vorher-1.7.0"}).encode(), 0o600)
            self.assertEqual(su.main(["--wiederherstellen"]), 0)
        self.assertEqual(self.u.aufrufe, [("install", "vorher-1.7.0", True)])
        self.assertFalse(os.path.exists(self.p.marke))
        # das unterbrochene Update gilt als fehlgeschlagen
        self.ausgabe.seek(0)
        text = self.ausgabe.getvalue()
        self.ausgabe.truncate(0)
        self.ausgabe.write("=== Update 01.01.2030 00:00:00, Kanal stabil ===\n" + text)
        ok, zeile = self.ergebnis()
        self.assertFalse(ok)
        self.assertIn("beim Umschalten unterbrochen", zeile)

    def test_abregelung_verschiebt(self):
        self.veroeffentlichen("1.8.0")
        for gruende, geladen in ((["Anlage wird gerade abgeregelt"], False),
                                 ([None, "Freigabe eines Wechselrichters steht noch aus"], True)):
            with self.subTest(gruende=gruende):
                self.u.gruende, self.u.aufrufe = list(gruende), []
                self.assertEqual(self.lauf(), 0)
                self.assertEqual(self.ausgabe.getvalue().splitlines()[-1],
                                 f"Verschoben: {gruende[-1]}. Später erneut starten (oder: sudo solarmaa-update --erzwingen).")
                self.assertEqual(bool(self.srv.abrufe), geladen)
                self.assertNotIn("install", [a[0] for a in self.u.aufrufe])
                self.assertEqual(self.releases(), [])
                self.assertFalse(os.path.exists(self.p.marke))
                ok, zeile = self.ergebnis()
                self.assertFalse(ok)
                self.assertTrue(zeile.startswith("Verschoben"))
        self.u.gruende = ["Anlage wird gerade abgeregelt"] * 2
        self.assertEqual(self.lauf(erzwingen=True), 0)
        self.assertEqual(self.u.gruende, ["Anlage wird gerade abgeregelt"] * 2)      # nicht gefragt
        self.assertEqual(self.installiert()["version"], "1.8.0")

    def test_kanal_und_quelle_nur_aus_den_root_dateien(self):
        Path(self.p.config).write_text('[server]\nport = 1\n[update]\nkanal = "test"\nquelle = "http://boese.example"\n')
        self.veroeffentlichen("1.8.0", kanal="test")
        self.assertEqual(self.lauf(), 1)                       # Pi bleibt auf «stabil»: dort liegt nichts
        self.assertEqual(self.srv.abrufe, ["/pi-pakete/stabil/freigabe.txt"])
        os.unlink(self.p.kanal)
        self.assertEqual(self.lauf(), 1)
        self.assertIn("FEHLER: Update-Kanal fehlt oder ist ungültig", self.ausgabe.getvalue())
        Path(self.p.kanal).write_text("stabil\n")
        os.unlink(self.p.quelle)
        self.assertEqual(self.lauf(), 1)
        self.assertIn("FEHLER: Update-Quelle fehlt oder ist ungültig", self.ausgabe.getvalue())
        Path(self.p.quelle).write_text("http://127.0.0.1:1/x\n")
        self.u.nur_https = True
        self.assertEqual(self.lauf(), 1)
        self.assertEqual(self.ausgabe.getvalue().count("FEHLER: Update-Quelle fehlt oder ist ungültig"), 2)

    def test_pruefung_nur_mit_installiertem_schluessel(self):
        """Nie eine Datei aus dem neuen Paket für die Prüfung: Das Paket bringt eigene Schlüssel und ein eigenes
        solarmaa_update.py mit; geprüft wird trotzdem nur mit der installierten Schlüsseldatei, und vor der
        Prüfung liegt nichts vom Paket auf der Platte."""
        from unittest import mock

        def eigene(tar, praefix):
            for name, inhalt in (("pi/update-schluessel/allowed_signers", self.fremd.zeile().encode()),
                                 ("pi/solarmaa_update.py.boese", b"raise SystemExit('nie')\n")):
                ti = tarfile.TarInfo(f"{praefix}/{name}")
                ti.size = len(inhalt)
                tar.addfile(ti, io.BytesIO(inhalt))
        self.veroeffentlichen("1.8.0", paket=paket_bauen("1.8.0", extra=[eigene]), schluessel=self.fremd)
        echt = subprocess.run
        aufrufe = []

        def spion(args, *a, **k):
            aufrufe.append(list(args))
            return echt(args, *a, **k)
        with mock.patch.object(su.subprocess, "run", spion):
            self.assertEqual(self.lauf(), 1)
        self.assertEqual(len(aufrufe), 1)
        self.assertEqual(aufrufe[0][:2], [SSH_KEYGEN, "-Y"])
        self.assertEqual(aufrufe[0][aufrufe[0].index("-f") + 1], self.p.schluessel)
        self.assertEqual(self.releases(), [])
        self.assertEqual(self.srv.abrufe, ["/pi-pakete/stabil/freigabe.txt"])

    def test_erstinstallation(self):
        from unittest import mock
        m = self.veroeffentlichen("1.8.0")
        f, p = self.d / "freigabe.txt", self.d / "paket.tar.gz"
        f.write_bytes(self.dateien["/pi-pakete/stabil/freigabe.txt"])
        p.write_bytes(self.dateien["/pi-pakete/stabil/solarmaa-dash-1.8.0.tar.gz"])
        aufrufe, echt = [], subprocess.run

        def install_attrappe(args, **k):
            if args[0] == SSH_KEYGEN:
                return echt(args, **k)
            aufrufe.append(args)
            return subprocess.CompletedProcess(args, 0)
        with mock.patch.object(su.subprocess, "run", install_attrappe):
            self.assertEqual(self.u.erstinstallation(str(f), str(p), self.p.schluessel, "test", []), 1)   # falscher Kanal
            self.assertEqual(aufrufe, [])
            self.assertEqual(self.u.erstinstallation(str(f), str(p), self.p.schluessel, "stabil", ["--hostname", "x"]), 0)
        self.assertEqual(aufrufe, [[os.path.join(self.p.releases, "1.8.0", "pi", "install.sh"), "--kanal", "stabil", "--hostname", "x"]])
        self.assertEqual(self.installiert()["sha256"], m["sha256"])
        p.write_bytes(p.read_bytes() + b"x")
        self.assertEqual(self.u.erstinstallation(str(f), str(p), self.p.schluessel, "stabil", []), 1)
        self.assertIn("FEHLER: Paket passt nicht zur Freigabe", self.ausgabe.getvalue())


def sk_zeile(kommentar="nur-test") -> str:
    """Syntaktisch gültiger sk-ssh-ed25519-Schlüssel aus Zufallsbytes, NUR TEST (gehört zu keiner Hardware)."""
    def s(b):
        return struct.pack(">I", len(b)) + b
    blob = s(b"sk-ssh-ed25519@openssh.com") + s(os.urandom(32)) + s(b"ssh:")
    return f'update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com {base64.b64encode(blob).decode()} {kommentar}\n'


@unittest.skipIf(SSH_KEYGEN != "/usr/bin/ssh-keygen" or shutil.which("bash") is None,
                 "braucht /usr/bin/ssh-keygen und bash")
class InstallShTests(unittest.TestCase):
    """install.sh ohne root: Kopie, in der /etc, /var, /opt und /usr/local in einen Temp-Ordner zeigen;
    useradd, chown, systemctl, install -o usw. sind Attrappen, die nur mitschreiben. Was root, systemd und
    die echten Pfade tun, ist damit nur simuliert."""

    ATTRAPPEN = ("apt-get", "useradd", "chown", "systemctl", "hostnamectl", "hostname", "sleep", "curl", "id")

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="install-sh."))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = self.d / "repo"
        shutil.copytree(ROOT / "pi", self.repo / "pi", ignore=shutil.ignore_patterns("__pycache__", "solarmaa_dash"))
        shutil.copytree(ROOT / "app", self.repo / "app")
        (self.repo / "pi/solarmaa_dash").mkdir()
        self.wurzel = self.d / "wurzel"
        for o in ("etc/solarmaa-dash", "var", "opt", "usr/local/sbin", "usr/local/lib", "etc/systemd/system"):
            (self.wurzel / o).mkdir(parents=True)
        skript = (ROOT / "pi/install.sh").read_text()
        w = str(self.wurzel)
        for pfad in ("/etc/", "/var/", "/opt/", "/usr/local/"):
            skript = skript.replace(pfad, w + pfad)
        skript = skript.replace("[[ $EUID -eq 0 ]] ||", "true ||")
        rest = "\n".join(z for z in skript.replace(w, "WURZEL").splitlines() if not z.lstrip().startswith("#"))
        for pfad in ("/etc/", "/var/", "/opt/", "/usr/local/", "/root", "/home"):      # Schutz: nichts ausserhalb
            self.assertNotRegex(rest, "(?<!WURZEL)" + pfad)
        self.skript = self.repo / "pi/install.sh"
        self.skript.write_text(skript)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.log = self.d / "aufrufe.log"
        for name in self.ATTRAPPEN:
            (self.bin / name).write_text(f'#!/bin/bash\necho "{name} $*" >> "{self.log}"\n')
        (self.bin / "install").write_text(  # install -d / install -m … quelle ziel: ohne Besitzerwechsel
            f'#!/bin/bash\necho "install $*" >> "{self.log}"\nd=0; a=()\n'
            'while [[ $# -gt 0 ]]; do case $1 in -d) d=1; shift;; -m|-o|-g) shift 2;; *) a+=("$1"); shift;; esac; done\n'
            'if [[ $d -eq 1 ]]; then mkdir -p "${a[@]}"; else cp "${a[0]}" "${a[1]}"; fi\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def lauf(self, *args, schluessel: str | None = None):
        datei = self.repo / "pi/update-schluessel/allowed_signers"
        if schluessel is not None:
            datei.write_text(datei.read_text() + schluessel)
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
        return subprocess.run(["bash", str(self.skript), *args], capture_output=True, text=True, env=env, timeout=120)

    def dateien(self):
        return sorted(str(p.relative_to(self.wurzel)) for p in self.wurzel.rglob("*") if p.is_file())

    def test_ohne_schluessel_bricht_vor_jeder_aenderung_ab(self):
        """So wie heute: In pi/update-schluessel/allowed_signers ist noch kein Schlüssel eingetragen."""
        (self.repo / "pi/update-schluessel/allowed_signers").write_text("# NOCH KEINE SCHLÜSSEL EINGETRAGEN\n\n")
        vorher = self.dateien()
        for args in (["--update"], []):
            r = self.lauf(*args)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("Noch keine Update-Schlüssel eingetragen", r.stdout + r.stderr)
            self.assertIn("Installation abgebrochen, nichts verändert.", r.stderr)
            self.assertEqual(self.dateien(), vorher)
            aufrufe = self.log.read_text() if self.log.exists() else ""
            self.assertNotIn("useradd", aufrufe)
            self.assertNotRegex(aufrufe, "(?m)^install ")
            self.assertNotIn("systemctl", aufrufe)

    def test_ohne_ssh_keygen_oder_mit_testschluessel_abbruch(self):
        self.skript.write_text(self.skript.read_text().replace("[[ -x /usr/bin/ssh-keygen ]]", "[[ -x /gibt/es/nicht ]]"))
        r = self.lauf("--update", schluessel=sk_zeile())
        self.assertEqual(r.returncode, 1)
        self.assertIn("ssh-keygen fehlt", r.stderr)
        self.assertEqual(self.dateien(), [])
        shutil.rmtree(self.d)
        self.setUp()
        r = self.lauf("--update", schluessel=schluessel("haupt").zeile())        # normaler Schlüssel statt USB
        self.assertEqual(r.returncode, 1)
        self.assertIn("nur sk-ssh-ed25519", r.stdout + r.stderr)
        self.assertEqual(self.dateien(), [])

    def test_umstieg_vom_git_update(self):
        (self.wurzel / "etc/solarmaa-dash/update-branch").write_text("claude/entwicklung\n")
        (self.wurzel / "opt/solarmaa-dash/pi").mkdir(parents=True)
        (self.wurzel / "opt/solarmaa-dash/pi/alt.txt").write_text("alt")
        zeile = sk_zeile("Haupt")
        r = self.lauf("--update", schluessel=zeile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        w = self.wurzel
        self.assertFalse((w / "opt/solarmaa-dash/pi/alt.txt").exists())
        self.assertTrue((w / "opt/solarmaa-dash/pi/install.sh").exists())
        self.assertEqual(sorted(p.name for p in (w / "opt/solarmaa-dash").iterdir()), ["app", "pi", "releases"])
        self.assertEqual((w / "usr/local/sbin/solarmaa-update").read_text(), (ROOT / "pi/update.sh").read_text())
        self.assertEqual((w / "usr/local/lib/solarmaa-update/solarmaa_update.py").read_text(),
                         (ROOT / "pi/solarmaa_update.py").read_text())
        self.assertTrue((w / "usr/local/sbin/solarmaa-portal-verbinden").exists())
        self.assertTrue((w / "etc/solarmaa-dash/update-schluessel").read_text().endswith(zeile))
        self.assertEqual((w / "etc/solarmaa-dash/update-kanal").read_text(), "test\n")          # Test-Pi
        self.assertEqual((w / "etc/solarmaa-dash/update-quelle").read_text(), (ROOT / "pi/update-quelle").read_text())
        self.assertTrue((w / "etc/systemd/system/solarmaa-update-wiederherstellen.service").exists())
        self.assertRegex(r.stdout, r"Update-Schlüssel: SHA256:\S+ \(sk-ssh-ed25519@openssh.com\)")
        self.assertIn("Selbsttest Signaturprüfung: ok", r.stdout)
        aufrufe = self.log.read_text()
        self.assertIn("systemctl enable solarmaa-update-wiederherstellen.service", aufrufe)
        self.assertNotIn("apt-get", aufrufe)
        # vorhandener Kanal bleibt, --kanal setzt ihn
        self.assertEqual(self.lauf("--update").returncode, 0)
        self.assertEqual((w / "etc/solarmaa-dash/update-kanal").read_text(), "test\n")
        self.assertEqual(self.lauf("--update", "--kanal", "stabil").returncode, 0)
        self.assertEqual((w / "etc/solarmaa-dash/update-kanal").read_text(), "stabil\n")
        self.assertEqual(self.lauf("--update", "--kanal", "main").returncode, 2)

    def test_kanal_kundenanlage_und_wiederherstellung_ohne_systemctl(self):
        (self.wurzel / "etc/solarmaa-dash/update-branch").write_text("stabil\n")
        r = self.lauf("--update", "--ohne-neustart", schluessel=sk_zeile())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.wurzel / "etc/solarmaa-dash/update-kanal").read_text(), "stabil\n")
        self.assertNotIn("systemctl", self.log.read_text())
        self.assertNotIn("curl", self.log.read_text())
        self.assertTrue((self.wurzel / "usr/local/sbin/solarmaa-update").exists())


class SchluesseldateiTests(unittest.TestCase):
    """pi/update-schluessel/allowed_signers: nur USB-Sicherheitsschlüssel im Namensraum solarmaa-update,
    kein Testschlüssel. Solange keiner eingetragen ist, bricht install.sh ab (InstallShTests)."""

    def test_format_namensraum_kein_testschluessel(self):
        text = (ROOT / "pi/update-schluessel/allowed_signers").read_text(encoding="utf-8")
        zeilen = su.schluessel_zeilen(text)            # Fehler bei jeder anderen Zeile oder anderem Typ
        for z in text.splitlines():
            if z.strip() and not z.lstrip().startswith("#"):
                self.assertTrue(z.startswith('update@solarmaa namespaces="solarmaa-update" sk-ssh-ed25519@openssh.com '), z)
                self.assertNotRegex(z.lower(), "test|beispiel|selbsttest")
        self.assertEqual(len({b for _t, b in zeilen}), len(zeilen))       # kein Schlüssel doppelt
        for name in _SCHLUESSEL:
            if name != "_ordner":
                self.assertNotIn(_SCHLUESSEL[name].pub[1], text)


class WartungAnzeigeTests(unittest.TestCase):
    """Wartungsseite: Version, Kanal, Stand aus paket.json; vorher (git) aus /opt/solarmaa-dash/src."""

    def test_paket_kanal_und_rueckfall_git(self):
        from unittest import mock
        from solarmaa_dash import config, wartung
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        paket, kanal = d / "paket.json", d / "update-kanal"
        w = wartung.Wartung(config.load(None), d)
        with mock.patch.object(wartung, "PAKET_JSON", paket), mock.patch.object(wartung, "KANAL_DATEI", kanal), \
                mock.patch.object(wartung, "SRC", d / "gibt-es-nicht"):
            st = w.status()
            self.assertEqual((st["commit"], st["stand"], st["kanal"], st["weg"]), (None, None, None, None))
            paket.write_text(json.dumps({"version": "1.8.0", "kanal": "stabil", "commit": COMMIT,
                                         "erstellt": "17.11.2026 10:00", "installiert": "17.11.2026 11:00"}))
            kanal.write_text("test\n")
            st = w.status()
            self.assertEqual((st["commit"], st["stand"], st["kanal"], st["weg"]), (COMMIT[:7], "17.11.2026 10:00", "test", "paket"))
            paket.write_text('{"commit": "<script>"}')
            kanal.write_text("main\n")
            st = w.status()
            self.assertEqual((st["commit"], st["kanal"], st["weg"]), (None, None, None))
            with mock.patch.object(wartung.Wartung, "_git", lambda self, *a: "abc1234" if "rev-parse" in a else "01.11.2026 09:00"):
                st = w.status()
            self.assertEqual((st["commit"], st["stand"], st["weg"]), ("abc1234", "01.11.2026 09:00", "git"))


@unittest.skipIf(SSH_KEYGEN != "/usr/bin/ssh-keygen" or shutil.which("bash") is None or shutil.which("sha256sum") is None,
                 "braucht /usr/bin/ssh-keygen, bash und sha256sum")
class SchnellstartTests(unittest.TestCase):
    """pi/schnellstart.sh ohne root und ohne Netz: apt-get und curl sind Attrappen (curl kopiert aus einem
    Temp-Ordner), der Testschlüssel ist ein normaler ed25519 (nur Test, darum die Typprüfung entschärft) und
    der letzte Schritt (solarmaa_update.py --erstinstallation) wird nur mitgeschrieben."""

    def test_eingebettete_schluessel_wie_allowed_signers(self):
        skript = (ROOT / "pi/schnellstart.sh").read_text()
        block = skript.split("SCHLUESSEL=$(cat <<'SCHLUESSEL'\n", 1)[1].split("\nSCHLUESSEL\n)", 1)[0] if \
            "SCHLUESSEL=$(cat <<'SCHLUESSEL'\nSCHLUESSEL\n)" not in skript else ""
        datei = (ROOT / "pi/update-schluessel/allowed_signers").read_text()
        self.assertEqual([z for z in block.splitlines() if z.strip()],
                         [z for z in datei.splitlines() if z.strip() and not z.lstrip().startswith("#")])

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="schnellstart."))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.ablage = self.d / "ablage"
        (self.ablage / "stabil").mkdir(parents=True)
        self.bin = self.d / "bin"
        self.bin.mkdir()
        self.log = self.d / "aufrufe.log"
        (self.bin / "apt-get").write_text(f'#!/bin/bash\necho "apt-get $*" >> "{self.log}"\n')
        (self.bin / "curl").write_text(  # curl … -o ZIEL URL  ->  Datei aus der Ablage
            f'#!/bin/bash\necho "curl $*" >> "{self.log}"\nwhile [[ $# -gt 0 ]]; do case $1 in -o) o=$2; shift 2;; '
            '--max-filesize) m=$2; shift 2;; -*) shift;; *) u=$1; shift;; esac; done\n'
            f'q="{self.ablage}/${{u#*pi-pakete/}}"; [[ -f "$q" ]] || exit 22\n'
            '[[ $(stat -c %s "$q") -le $m ]] || exit 63\ncp "$q" "$o"\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def lauf(self, schluessel_zeile: str, *args):
        skript = (ROOT / "pi/schnellstart.sh").read_text()
        skript = skript.replace("[[ $EUID -eq 0 ]] ||", "true ||")
        skript = skript.replace("SCHLUESSEL=$(cat <<'SCHLUESSEL'\n", "SCHLUESSEL=$(cat <<'SCHLUESSEL'\n" + schluessel_zeile)
        skript = skript.replace('sk-ssh-ed25519@openssh.com \' <<< "$SCHLUESSEL"', '\' <<< "$SCHLUESSEL"')
        letzte = 'python3 -I "$T/solarmaa_update.py" --erstinstallation'
        self.assertEqual(skript.count(letzte), 1)
        skript = skript.replace(letzte, f'cp "$T/solarmaa_update.py" "{self.d}/geladen.py"; echo ERSTINSTALLATION')
        pfad = self.d / "schnellstart.sh"
        pfad.write_text(skript)
        if self.log.exists():
            self.log.unlink()
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}"}
        return subprocess.run(["bash", str(pfad), *args], capture_output=True, text=True, env=env, timeout=60)

    def veroeffentlichen(self, schluessel_obj, paket=None):
        paket = paket or paket_bauen("1.8.0")
        m = manifest("1.8.0", groesse=len(paket), sha256=hashlib.sha256(paket).hexdigest())
        mb = manifest_bytes(m)
        (self.ablage / "stabil/freigabe.txt").write_bytes(mb + schluessel_obj.signieren(mb))
        (self.ablage / "stabil/solarmaa-dash-1.8.0.tar.gz").write_bytes(paket)

    def test_ohne_schluessel_nichts(self):
        r = self.lauf("")
        self.assertEqual(r.returncode, 1)
        self.assertIn("noch keine Update-Schlüssel", r.stdout)
        self.assertFalse(self.log.exists())

    def test_gueltig_und_ungueltig(self):
        zeile = schluessel("haupt").zeile()
        self.veroeffentlichen(schluessel("haupt"))
        r = self.lauf(zeile, "--hostname", "x")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ERSTINSTALLATION", r.stdout)
        self.assertEqual((self.d / "geladen.py").read_text(), "# neu\n")         # aus dem geprüften Paket
        (self.d / "geladen.py").unlink()
        self.veroeffentlichen(schluessel("fremd"))                                 # fremde Signatur
        r = self.lauf(zeile)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Signatur der Freigabe ungültig", r.stdout)
        self.assertNotIn("solarmaa-dash-1.8.0.tar.gz", self.log.read_text())
        self.assertFalse((self.d / "geladen.py").exists())
        self.veroeffentlichen(schluessel("haupt"))                                 # Paket ausgetauscht
        p = self.ablage / "stabil/solarmaa-dash-1.8.0.tar.gz"
        p.write_bytes(paket_bauen("1.8.0", praefix="anders")[:p.stat().st_size].ljust(p.stat().st_size, b"\0"))
        r = self.lauf(zeile)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("ERSTINSTALLATION", r.stdout)
        self.assertFalse((self.d / "geladen.py").exists())

    def test_erstinstallation_argumente(self):
        from unittest import mock
        aufrufe = []

        class U:
            def __init__(self, p):
                pass

            def erstinstallation(self, *a):
                aufrufe.append(a)
                return 0
        with mock.patch.object(su, "Updater", U):
            self.assertEqual(su.main(["--erstinstallation", "f", "p", "k", "stabil", "--", "--hostname", "x"]), 0)
            self.assertEqual(su.main(["--erstinstallation", "f", "p", "k", "test", "--"]), 0)
        self.assertEqual(aufrufe, [("f", "p", "k", "stabil", ["--hostname", "x"]), ("f", "p", "k", "test", [])])


class OhneGitTests(unittest.TestCase):
    def test_kein_git_mehr_auf_dem_pi(self):
        """Updates und Erstinstallation kommen nur noch aus signierten Paketen (kein git clone/fetch)."""
        for name in ("install.sh", "update.sh", "schnellstart.sh", "portal-verbinden.sh"):
            befehle = "\n".join(z for z in (ROOT / "pi" / name).read_text().splitlines() if not z.lstrip().startswith("#"))
            self.assertNotRegex(befehle, r"\bgit\b", name)
            self.assertNotIn("raw.githubusercontent.com", befehle, name)


@unittest.skipUnless((ROOT / "werkzeuge").is_dir(), "werkzeuge/ ist nicht im Paket für die Pis")
class WerkzeugeTests(unittest.TestCase):
    """werkzeuge/*.ps1 laufen nur unter Windows (hier nicht ausführbar). Geprüft wird nur, dass das Manifest,
    das paket-signieren.ps1 schreibt, genau dem entspricht, was die Pis annehmen."""

    def test_manifest_aus_paket_signieren(self):
        import re
        text = (ROOT / "werkzeuge/paket-signieren.ps1").read_text(encoding="utf-8-sig")
        teil = text.split("$manifest = ", 1)[1].split("\n$mPfad", 1)[0]
        stuecke = re.findall(r'"((?:[^"`]|`.)*)"', teil)
        roh = "".join(stuecke).replace("`n", "\n").replace('`"', '"')
        werte = {"$Kanal": "test", "$ver": "1.8.0", "$($a.commit)": COMMIT, "$erstellt": "17.11.2026 10:00",
                 "$dateiname": "solarmaa-dash-1.8.0.tar.gz", "$groesse": "1234", "$sha": "a" * 64}
        for k, v in werte.items():
            roh = roh.replace(k, v)
        self.assertNotIn("$", roh)
        self.assertTrue(roh.endswith("}\n"))
        m = su.manifest_lesen(roh.encode())
        self.assertEqual((m["kanal"], m["version"], m["groesse"]), ("test", "1.8.0", 1234))
        for name in ("paket-signieren.ps1", "schluessel-erzeugen.ps1"):      # Windows PowerShell 5.1 braucht das BOM für Umlaute
            self.assertTrue((ROOT / "werkzeuge" / name).read_bytes().startswith(b"\xef\xbb\xbf"), name)
        self.assertIn('-n solarmaa-update', text)
        self.assertIn('"-t", "ed25519-sk"', (ROOT / "werkzeuge/schluessel-erzeugen.ps1").read_text(encoding="utf-8-sig"))
