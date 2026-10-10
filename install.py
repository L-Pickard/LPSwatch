#!/usr/bin/env python3
"""Install a theme from a git URL without applying it. argv in, one JSON object
out, nothing composed into a shell string.

  install.py install <git-url>
  install.py browser              is the Extra Themes plugin there to open?

`omarchy theme install` does the same clone and then switches the desktop to
the theme. A picker wants the theme to appear and be tried on first, so this is
that command minus the apply: the same URL check (omarchy-git-url-check), the
same name derived from the URL, a clone into our own cache, and — only once it
has turned out to be a theme — one rename into ~/.config/omarchy/themes. An
existing theme of that name is refused, never replaced.

This is the plugin's only use of the network. It keeps no journal and takes no
lock; taking an install back is manage.py's trash-theme. A refusal is an
answer: exit 0 with {"ok": false, "code": ..., "error": ...}.
"""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
SCHEMES = ("https", "http", "ssh", "git", "git+ssh", "ssh+git")
MAX_URL_BYTES = 512
MAX_THEMES = 512
MAX_THEME_BYTES = 2 << 30
CLONE_SECONDS = 150
BROWSER = "io.github.simakwm.extra-themes"

HOME = Path.home()
THEMES = HOME / ".config/omarchy/themes"
PLUGINS = HOME / ".config/omarchy/plugins"
SCRATCH = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "omarchy/lpswatch/installing"


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def theme_name(url):
    """The name `omarchy theme install` would give it, held to the stricter
    set of characters the picker indexes."""
    path = url
    # scp-style ssh (git@host:org/repo.git): the name is in the part after the colon.
    if "://" not in path and ":" in path and "/" not in path.split(":", 1)[0]:
        path = path.split(":", 1)[1]
    name = os.path.basename(path.rstrip("/"))
    name = name[:-4] if name.endswith(".git") else name
    name = re.sub(r"-theme$", "", re.sub(r"^omarchy-", "", name)).lower()
    if not NAME.fullmatch(name) or ".." in name:
        raise Refused("name", "That URL does not give a usable theme name")
    return name


def check_url(url):
    if not 0 < len(url.encode()) <= MAX_URL_BYTES or any(ord(c) < 33 or ord(c) == 127 for c in url):
        raise Refused("url", "That is not a git URL")
    # Omarchy's own check: no git options, no transport helpers. A missing
    # checker refuses the URL rather than cloning it, as omarchy-theme-install does.
    try:
        allowed = subprocess.run(["omarchy-git-url-check", url], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        allowed = False
    if not allowed:
        raise Refused("url", "That is not a git URL Omarchy will clone from")
    # Narrower than Omarchy's: a theme comes from a remote repository, so only
    # network transports — a named one, or scp-style ssh. Never a local path.
    scheme = re.match(r"([A-Za-z0-9][A-Za-z0-9+.-]*)://", url)
    if scheme:
        if scheme.group(1).lower() not in SCHEMES:
            raise Refused("url", "Paste an https or ssh git URL")
    elif not re.match(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^/]", url):
        raise Refused("url", "Paste an https or ssh git URL")


def tree_bytes(path):
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for entry in dirs + files:
            try:
                total += os.lstat(os.path.join(root, entry)).st_blocks * 512
            except OSError:
                pass
    return total


def is_file(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def install(url):
    check_url(url)
    name = theme_name(url)
    final = THEMES / name
    if os.path.lexists(final):
        raise Refused("exists", "A theme called %s is already installed" % name)
    THEMES.mkdir(parents=True, exist_ok=True)
    with os.scandir(THEMES) as entries:
        if sum(1 for _ in entries) >= MAX_THEMES:
            raise Refused("full", "Too many themes are installed")
    # Our own scratch, in our own cache: anything a crashed run left is ours to clear.
    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, mode=0o700)
    work = Path(tempfile.mkdtemp(dir=SCRATCH))
    try:
        clone = work / name
        environment = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true",
                           GIT_SSH_COMMAND="ssh -oBatchMode=yes -oStrictHostKeyChecking=accept-new")
        try:
            result = subprocess.run(
                ["git", "-c", "protocol.ext.allow=never", "clone", "--quiet", "--depth", "1", "--no-tags",
                 "--single-branch", "--no-recurse-submodules", "--", url, str(clone)],
                env=environment, stdin=subprocess.DEVNULL, capture_output=True, timeout=CLONE_SECONDS)
        except subprocess.TimeoutExpired:
            raise Refused("clone", "Downloading that repository took too long")
        except OSError:
            raise Refused("clone", "git is not available")
        if result.returncode != 0:
            raise Refused("clone", "Could not download that repository. Check the URL, and that it is public")
        # What makes it a theme is what omarchy-theme-set needs from one.
        if not is_file(clone / "colors.toml") and not is_file(clone / "alacritty.toml"):
            raise Refused("not-theme", "That repository is not an Omarchy theme (it has no colors.toml)")
        if tree_bytes(clone) > MAX_THEME_BYTES:
            raise Refused("full", "That repository is over 2 GB; not installing it")
        if os.path.lexists(final):
            raise Refused("exists", "A theme called %s is already installed" % name)
        try:
            os.rename(clone, final)
        except OSError:
            shutil.move(str(clone), str(final))     # the cache is on another disk
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"theme": name}


def main():
    signal.alarm(CLONE_SECONDS + 40)
    action, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    try:
        if action == "install" and len(args) == 1:
            result = dict(install(args[0]), ok=True)
        elif action == "browser" and not args:
            result = {"ok": os.path.isfile(PLUGINS / BROWSER / "manifest.json"), "plugin": BROWSER,
                      "error": "The Extra Themes plugin is not installed"}
        else:
            raise ValueError("Unknown action")
    except Refused as refusal:
        result = {"ok": False, "code": refusal.code, "error": str(refusal)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("lpswatch install: " + str(error), file=sys.stderr)
        sys.exit(1)
