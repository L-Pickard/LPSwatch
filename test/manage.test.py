"""Exercise manage.py as a separate process, in an isolated HOME: no real theme,
background or trash is ever touched."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "manage.py"


class ManageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.themes = self.home / ".config/omarchy/themes"
        self.bgs = self.home / ".config/omarchy/backgrounds"
        self.stock = self.home / "omarchy/themes"
        self.current = self.home / ".local/state/omarchy/current"
        self.trash = self.home / ".local/share/Trash"
        self.state = self.home / ".local/state/omarchy/lpswatch/collection.json"
        for path in (self.themes, self.bgs, self.stock, self.current):
            path.mkdir(parents=True)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
        self.env.update(HOME=str(self.home), OMARCHY_PATH=str(self.home / "omarchy"))
        self.theme("alpha", ["a.jpg", "b.png"])
        self.theme("beta", ["one.jpg"])
        self.theme("tokyo", ["t1.jpg", "t2.jpg"], root=self.stock)
        (self.current / "theme.name").write_text("beta\n")

    def theme(self, name, backgrounds, root=None):
        folder = (root or self.themes) / name / "backgrounds"
        folder.mkdir(parents=True)
        (folder.parent / "colors.toml").write_text("accent = '#ffffff'\n")
        for background in backgrounds:
            (folder / background).write_bytes(b"x" * 5000)

    def run_helper(self, *args, crash=False):
        result = subprocess.run(["python3", str(SCRIPT), *args], env=self.env, capture_output=True, timeout=20)
        self.assertEqual(result.returncode != 0, crash, result.stderr)
        return None if crash else json.loads(result.stdout)

    def refused(self, code, *args):
        result = self.run_helper(*args)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], code, result)
        return result

    def tree(self):
        return sorted(str(p.relative_to(self.home)) for p in self.home.rglob("*")
                      if ".local/state/omarchy/lpswatch" not in str(p))

    def test_theme_round_trip(self):
        (self.bgs / "alpha").mkdir()
        (self.bgs / "alpha/mine.jpg").write_bytes(b"y" * 9000)
        before = self.tree()
        result = self.run_helper("trash-theme", "alpha")
        self.assertTrue(result["ok"])
        self.assertFalse(result["stockRemains"])
        self.assertFalse((self.themes / "alpha").exists())
        self.assertFalse((self.bgs / "alpha").exists())
        self.assertEqual(result["trashCount"], 1)
        self.assertGreater(result["trashBytes"], 0)
        # A real freedesktop trash entry, so the file manager can see and restore it.
        info = (self.trash / "info/alpha.trashinfo").read_text()
        self.assertIn("Path=" + str(self.themes / "alpha"), info)
        self.assertTrue((self.trash / "files/alpha/backgrounds/a.jpg").exists())
        restored = self.run_helper("restore", result["id"])
        self.assertEqual((restored["ok"], restored["kind"], restored["theme"]), (True, "theme", "alpha"))
        self.assertEqual(restored["trashCount"], 0)
        self.assertEqual([p for p in self.tree() if not p.startswith(".local/share")], before)
        self.assertTrue((self.themes / "alpha/backgrounds/a.jpg").exists())
        self.assertTrue((self.bgs / "alpha/mine.jpg").exists())
        self.assertEqual(list((self.trash / "files").iterdir()), [])
        self.assertEqual(list((self.trash / "info").iterdir()), [])

    def test_theme_refusals_change_nothing(self):
        before = self.tree()
        self.refused("active", "trash-theme", "beta")
        self.refused("hide", "trash-theme", "tokyo")
        self.refused("missing", "trash-theme", "nope")
        for bad in ("../alpha", "..", ".hidden", "a/b", "", "alpha/"):
            self.refused("name", "trash-theme", bad)
        outside = self.home / "elsewhere"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep")
        before = self.tree()
        os.symlink(outside, self.themes / "linked")
        self.refused("hide", "trash-theme", "linked")
        os.unlink(self.themes / "linked")
        self.assertEqual(self.tree(), before)
        self.assertTrue((outside / "keep.txt").exists())

    def test_user_theme_shadowing_stock_keeps_added_backgrounds(self):
        self.theme("tokyo", ["u.jpg"])
        (self.bgs / "tokyo").mkdir()
        (self.bgs / "tokyo/mine.jpg").write_bytes(b"y")
        result = self.run_helper("trash-theme", "tokyo")
        self.assertTrue(result["stockRemains"])
        self.assertTrue((self.bgs / "tokyo/mine.jpg").exists())
        self.assertTrue((self.stock / "tokyo/backgrounds/t1.jpg").exists())

    def test_background_round_trip(self):
        (self.bgs / "alpha").mkdir()
        (self.bgs / "alpha/mine.jpg").write_bytes(b"y" * 9000)
        shipped = self.run_helper("trash-bg", "alpha", "theme", "a.jpg")
        added = self.run_helper("trash-bg", "alpha", "extra", "mine.jpg")
        self.assertEqual(shipped["path"], str(self.themes / "alpha/backgrounds/a.jpg"))
        self.assertEqual(added["trashCount"], 2)
        self.assertFalse((self.themes / "alpha/backgrounds/a.jpg").exists())
        self.assertFalse((self.bgs / "alpha/mine.jpg").exists())
        self.refused("last", "trash-bg", "alpha", "theme", "b.png")
        self.run_helper("restore", added["id"])
        self.run_helper("restore", shipped["id"])
        self.assertTrue((self.themes / "alpha/backgrounds/a.jpg").exists())
        self.assertEqual((self.bgs / "alpha/mine.jpg").read_bytes(), b"y" * 9000)

    def test_background_refusals_change_nothing(self):
        (self.bgs / "tokyo").mkdir()
        (self.bgs / "tokyo/mine.jpg").write_bytes(b"y")
        secret = self.home / "secret.jpg"
        secret.write_text("secret")
        os.symlink(secret, self.themes / "alpha/backgrounds/link.jpg")
        os.symlink(self.themes / "alpha/backgrounds/a.jpg", self.current / "background")
        before = self.tree()
        self.refused("hide", "trash-bg", "tokyo", "theme", "t1.jpg")       # stock
        self.refused("hide", "trash-bg", "alpha", "theme", "link.jpg")     # a link
        self.refused("active", "trash-bg", "alpha", "theme", "a.jpg")      # on the desktop
        self.refused("last", "trash-bg", "beta", "theme", "one.jpg")
        self.refused("missing", "trash-bg", "alpha", "theme", "gone.jpg")
        self.refused("missing", "trash-bg", "alpha", "extra", "a.jpg")
        for bad in ("../colors.toml", "../../beta/backgrounds/one.jpg", "/etc/passwd", "..", "colors.toml", "a.jpg/"):
            self.refused("name", "trash-bg", "alpha", "theme", bad)
        self.refused("name", "trash-bg", "alpha", "other", "a.jpg")
        self.refused("name", "trash-bg", "../alpha", "theme", "a.jpg")
        self.assertEqual(self.tree(), before)
        self.assertEqual(secret.read_text(), "secret")

    def test_names_do_not_collide_in_the_trash(self):
        (self.trash / "files").mkdir(parents=True)
        (self.trash / "info").mkdir()
        (self.trash / "files/a.jpg").write_text("someone else's")
        (self.trash / "info/a.jpg.trashinfo").write_text("[Trash Info]\nPath=/somewhere/a.jpg\nDeletionDate=2026-01-01T00:00:00\n")
        result = self.run_helper("trash-bg", "alpha", "theme", "a.jpg")
        self.assertEqual((self.trash / "files/a.jpg").read_text(), "someone else's")
        self.assertEqual((self.trash / "files/a.jpg.2").read_bytes(), b"x" * 5000)
        self.run_helper("restore", result["id"])
        self.assertEqual((self.themes / "alpha/backgrounds/a.jpg").read_bytes(), b"x" * 5000)

    def test_restore_refuses_to_overwrite(self):
        result = self.run_helper("trash-bg", "alpha", "theme", "a.jpg")
        (self.themes / "alpha/backgrounds/a.jpg").write_text("new")
        self.refused("exists", "restore", result["id"])
        self.assertEqual((self.themes / "alpha/backgrounds/a.jpg").read_text(), "new")
        self.refused("missing", "restore", "0" * 16)

    def test_empty_takes_only_our_own(self):
        (self.trash / "files").mkdir(parents=True)
        (self.trash / "info").mkdir()
        (self.trash / "files/thesis.txt").write_text("not ours")
        (self.trash / "info/thesis.txt.trashinfo").write_text("[Trash Info]\nPath=/home/x/thesis.txt\nDeletionDate=2026-01-01T00:00:00\n")
        self.run_helper("trash-theme", "alpha")
        result = self.run_helper("empty")
        self.assertTrue(result["ok"])
        self.assertGreater(result["freed"], 0)
        self.assertEqual((result["trashCount"], result["trashBytes"]), (0, 0))
        self.assertEqual(sorted(p.name for p in (self.trash / "files").iterdir()), ["thesis.txt"])
        self.assertEqual(sorted(p.name for p in (self.trash / "info").iterdir()), ["thesis.txt.trashinfo"])
        self.assertEqual((self.trash / "files/thesis.txt").read_text(), "not ours")

    def test_empty_skips_an_item_that_is_no_longer_ours(self):
        result = self.run_helper("trash-bg", "alpha", "theme", "a.jpg")
        # Restored by hand, then something unrelated trashed under the same name.
        os.rename(self.trash / "files/a.jpg", self.themes / "alpha/backgrounds/a.jpg")
        (self.trash / "files/a.jpg").write_text("unrelated")
        (self.trash / "info/a.jpg.trashinfo").write_text("[Trash Info]\nPath=/home/x/a.jpg\nDeletionDate=2026-01-01T00:00:00\n")
        self.assertEqual(self.run_helper("status")["trashCount"], 0)
        self.run_helper("empty")
        self.assertEqual((self.trash / "files/a.jpg").read_text(), "unrelated")
        self.refused("missing", "restore", result["id"])

    def test_hide_theme(self):
        self.assertEqual(self.run_helper("status")["hiddenThemes"], [])
        self.assertEqual(self.run_helper("hide-theme", "tokyo", "true")["hiddenThemes"], ["tokyo"])
        self.assertEqual(self.run_helper("hide-theme", "tokyo", "true")["hiddenThemes"], ["tokyo"])
        self.refused("active", "hide-theme", "beta", "true")
        self.refused("name", "hide-theme", "../x", "true")
        self.assertEqual(self.run_helper("hide-theme", "tokyo", "false")["hiddenThemes"], [])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_remove_theme_trashes_ours_and_hides_stock(self):
        ours = self.run_helper("remove-theme", "alpha")
        self.assertEqual((ours["ok"], ours["action"], ours["trashCount"]), (True, "trashed", 1))
        self.assertFalse((self.themes / "alpha").exists())
        stock = self.run_helper("remove-theme", "tokyo")
        self.assertEqual((stock["ok"], stock["action"], stock["hiddenThemes"]), (True, "hidden", ["tokyo"]))
        self.assertTrue((self.stock / "tokyo/backgrounds/t1.jpg").exists())
        self.refused("active", "remove-theme", "beta")
        self.refused("missing", "remove-theme", "nope")
        self.refused("name", "remove-theme", "../alpha")

    def test_corrupt_state_is_refused_and_left_alone(self):
        self.state.parent.mkdir(parents=True)
        for content in ("not json", json.dumps({"version": 1, "hiddenThemes": [], "trash": [
                {"id": "0" * 16, "kind": "theme", "theme": "x", "bytes": 1,
                 "parts": [{"origin": "/etc", "name": "etc"}]}]})):
            self.state.write_text(content)
            before = self.tree()
            self.run_helper("trash-theme", "alpha", crash=True)
            self.run_helper("empty", crash=True)
            self.assertEqual(self.state.read_text(), content)
            self.assertEqual(self.tree(), before)

    def test_unknown_actions_fail(self):
        self.run_helper("delete-everything", crash=True)
        self.run_helper("trash-theme", crash=True)
        self.run_helper("trash-theme", "alpha", "extra", crash=True)


if __name__ == "__main__":
    unittest.main()
