"""Exercise pictures.py as a separate process, in an isolated HOME."""
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parent.parent / "pictures.py"


def png(width=64, height=36):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    raw = b"".join(b"\x00" + b"\x40\x80\xc0" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class PicturesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.pictures = self.home / "Pictures"
        self.cache = self.home / ".cache/omarchy/lpswatch/pictures"
        (self.pictures / "Art").mkdir(parents=True)
        (self.pictures / "Screenshots").mkdir()
        (self.pictures / ".hidden").mkdir()
        self.outside = self.home / "outside"
        self.outside.mkdir()
        (self.outside / "secret.png").write_bytes(png())
        for name in ("Art/b.png", "Art/A.png", "loose.png", "screenshot-2026-10-09.png", "Screenshots/x.png", ".hidden/y.png"):
            (self.pictures / name).write_bytes(png())
        (self.pictures / "notes.txt").write_text("not a picture")
        (self.pictures / "fake.png").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
        os.symlink(self.outside / "secret.png", self.pictures / "link.png")
        os.symlink(self.outside, self.pictures / "linked-folder")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        self.env["HOME"] = str(self.home)

    def run_helper(self, *args, crash=False):
        result = subprocess.run(["python3", str(SCRIPT), *args], env=self.env, capture_output=True, timeout=120)
        self.assertEqual(result.returncode != 0, crash, result.stderr)
        return result.stdout

    def source_files(self):
        return sorted((str(p), p.stat().st_mtime_ns) for p in self.pictures.rglob("*") if p.is_file())

    def test_inventory_skips_screenshots_links_and_hidden(self):
        listing = json.loads(self.run_helper("list"))
        self.assertEqual([p["rel"] for p in listing["pictures"]], ["Art/A.png", "Art/b.png", "fake.png", "loose.png"])
        self.assertEqual(listing["folders"], ["Art"])
        self.assertEqual([p["folder"] for p in listing["pictures"]], ["Art", "Art", "", ""])
        self.assertTrue(all(len(p["key"]) == 16 for p in listing["pictures"]))
        self.assertFalse(self.cache.exists())                     # listing writes nothing

    def test_thumbs_and_stage_write_only_to_the_cache(self):
        before = self.source_files()
        listing = json.loads(self.run_helper("list"))
        keys = {p["rel"]: p["key"] for p in listing["pictures"]}
        self.run_helper("thumbs")
        made = sorted(p.name for p in self.cache.iterdir())
        self.assertEqual(made, sorted(["bg-%s.jpg" % keys[r] for r in ("Art/A.png", "Art/b.png", "loose.png")]
                                      + ["reject-" + keys["fake.png"]]))
        self.assertEqual((self.cache / ("bg-%s.jpg" % keys["loose.png"])).read_bytes()[:3], b"\xff\xd8\xff")
        self.run_helper("stage", "1920", "1080", str(self.pictures / "loose.png"))
        self.assertTrue((self.cache / ("stage-%s-1920x1080.jpg" % keys["loose.png"])).exists())
        self.assertEqual(self.source_files(), before)
        # A picture that has gone takes its copies with it on the next run.
        (self.pictures / "loose.png").unlink()
        self.run_helper("thumbs")
        self.assertFalse(any(keys["loose.png"] in p.name for p in self.cache.iterdir()))

    def test_stage_refuses_anything_outside_pictures(self):
        self.run_helper("stage", "1920", "1080", str(self.outside / "secret.png"), str(self.pictures / "link.png"),
                        str(self.pictures / "linked-folder/secret.png"), str(self.pictures / "../outside/secret.png"),
                        "/etc/passwd", str(self.pictures / "absent.png"))
        self.assertEqual(list(self.cache.iterdir()), [])
        self.run_helper("stage", "1920", "1080", crash=True)
        self.run_helper("stage", "99999", "1080", str(self.pictures / "loose.png"), crash=True)
        self.run_helper("stage", "x", "y", str(self.pictures / "loose.png"), crash=True)
        self.run_helper("delete", crash=True)


if __name__ == "__main__":
    unittest.main()
