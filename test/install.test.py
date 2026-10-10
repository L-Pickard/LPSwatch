"""Exercise install.py as a separate process, in an isolated HOME and with no
network: git is told to fetch https://themes.test/ from a folder of local repos."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent.parent / "install.py"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.repos = Path(self.temp.name) / "repos"
        self.themes = self.home / ".config/omarchy/themes"
        self.themes.mkdir(parents=True)
        self.repos.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "GIT_"))}
        self.env.update(HOME=str(self.home), GIT_CONFIG_COUNT="1",
                        GIT_CONFIG_KEY_0="url.file://%s/.insteadOf" % self.repos,
                        GIT_CONFIG_VALUE_0="https://themes.test/",
                        GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
        self.repo("omarchy-Sunset-theme", {"colors.toml": "accent = '#ff8800'\n", "backgrounds/1.jpg": "x"})
        self.repo("legacy", {"alacritty.toml": "[colors]\n"})
        self.repo("not-a-theme", {"README.md": "hello\n"})

    def repo(self, name, files):
        work = self.repos / (name + ".work")
        for path, content in files.items():
            (work / path).parent.mkdir(parents=True, exist_ok=True)
            (work / path).write_text(content)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main"]
        quiet = dict(cwd=work, check=True, capture_output=True, env=dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null"))
        subprocess.run(git + ["init", "-q"], **quiet)
        subprocess.run(git + ["add", "-A"], **quiet)
        subprocess.run(git + ["commit", "-q", "-m", "theme"], **quiet)
        subprocess.run(git + ["clone", "-q", "--bare", str(work), str(self.repos / (name + ".git"))], **quiet)

    def run_helper(self, *args, crash=False):
        result = subprocess.run(["python3", str(SCRIPT), *args], env=self.env, capture_output=True, timeout=60)
        self.assertEqual(result.returncode != 0, crash, result.stderr)
        return None if crash else json.loads(result.stdout)

    def refused(self, code, url):
        before = sorted(p.name for p in self.themes.iterdir())
        result = self.run_helper("install", url)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["code"], code, result)
        self.assertEqual(sorted(p.name for p in self.themes.iterdir()), before)
        return result

    def test_installs_without_applying(self):
        result = self.run_helper("install", "https://themes.test/omarchy-Sunset-theme.git")
        self.assertEqual(result, {"ok": True, "theme": "sunset"})
        self.assertEqual((self.themes / "sunset/colors.toml").read_text(), "accent = '#ff8800'\n")
        self.assertTrue((self.themes / "sunset/.git").is_dir())                   # still a clone: `omarchy theme update` works
        self.assertFalse((self.home / ".local/state/omarchy/current").exists())   # nothing was applied
        self.assertEqual(list((self.home / ".cache/omarchy/lpswatch/installing").iterdir()), [])
        self.assertEqual(self.run_helper("install", "https://themes.test/legacy")["theme"], "legacy")

    def test_never_replaces_an_installed_theme(self):
        (self.themes / "sunset").mkdir()
        (self.themes / "sunset/mine.txt").write_text("mine")
        self.refused("exists", "https://themes.test/omarchy-Sunset-theme.git")
        self.assertEqual((self.themes / "sunset/mine.txt").read_text(), "mine")

    def test_refuses_what_is_not_a_theme_or_not_there(self):
        self.refused("not-theme", "https://themes.test/not-a-theme.git")
        self.refused("clone", "https://themes.test/absent.git")

    def test_refuses_urls_that_are_not_remote_repositories(self):
        for url in ("ext::sh -c 'touch /tmp/pwned'", "ext::sh%20-c%20id", "--upload-pack=touch /tmp/x",
                    "-oProxyCommand=id", "file://%s/legacy.git" % self.repos, str(self.repos / "legacy.git"),
                    "../repos/legacy.git", "fd::17", "foo://themes.test/legacy.git", "", "https://themes.test/a b.git",
                    "https://themes.test/x\n.git", "x" * 600):
            self.refused("url", url)
        for url in ("https://themes.test/..", "https://themes.test/.git", "https://themes.test/-rf.git",
                    "https://themes.test/a;id.git", "git@themes.test:..",
                    "https://themes.test/" + "n" * 80 + ".git"):
            self.refused("name", url)

    def test_browser_and_unknown_actions(self):
        self.assertFalse(self.run_helper("browser")["ok"])
        plugin = self.home / ".config/omarchy/plugins/io.github.simakwm.extra-themes"
        plugin.mkdir(parents=True)
        (plugin / "manifest.json").write_text("{}")
        self.assertTrue(self.run_helper("browser")["ok"])
        self.run_helper("install", crash=True)
        self.run_helper("remove", "x", crash=True)


if __name__ == "__main__":
    unittest.main()
