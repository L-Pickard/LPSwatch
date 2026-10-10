#!/usr/bin/env python3
"""Pictures mode: inventory ~/Pictures and make the preview copies the overlay
shows. Never writes to ~/Pictures; adding a picture to a theme is manage.py's.

  pictures.py list                      JSON inventory on stdout
  pictures.py thumbs                    filmstrip thumbs for everything listed
  pictures.py stage <W> <H> <path>...   screen-size copies for a few pictures

Run only when Pictures mode is entered, never on the picker's open path. Thumbs
are small and made for the whole inventory; screen-size copies are about 1 MB
each, so they are made only for the pictures actually looked at and dropped
after 30 days unused.

The posture is thumbs.sh's, because the shell must not decode a file it did not
make: each picture is read once through one no-follow descriptor that has to
resolve under ~/Pictures, into a private snapshot; vips sees only the snapshot,
single-threaded, under a deadline and a memory ceiling; and the shell is shown
only the JPEG vips wrote.
"""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import tempfile
import time

IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
LOADERS = ("jpegload", "pngload", "webpload", "gifload")
MAX_IMAGE_BYTES = 67108864
MAX_PIXELS = 50000000
MAX_PICTURES = 2000
MAX_ENTRIES = 20000         # directory entries examined, whatever they turn out to be
MAX_DEPTH = 4
MAX_PATH_BYTES = 512
MAX_STAGED = 8
KEY = re.compile(r"[0-9a-f]{16}\Z")

HOME = Path.home()
PICTURES = HOME / "Pictures"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache") / "omarchy/lpswatch/pictures"


def image_key(path, info):
    """index.sh's image_key: a replaced file gets new copies."""
    return hashlib.md5(("%s\t%d:%d" % (path, info.st_size, int(info.st_mtime))).encode()).hexdigest()[:16]


def is_screenshot(name):
    lowered = name.lower()
    return lowered.startswith(("screenshot", "screen shot", "screen_shot", "screencast"))


def inventory():
    pictures, examined, truncated = [], 0, False
    pending = [(PICTURES, 0)]
    while pending:
        folder, depth = pending.pop()
        try:
            entries = sorted(os.scandir(folder), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            examined += 1
            if examined > MAX_ENTRIES or len(pictures) >= MAX_PICTURES:
                truncated = True
                break
            name = entry.name
            if name.startswith(".") or is_screenshot(name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth < MAX_DEPTH and name.lower() != "screenshots":
                        pending.append((Path(entry.path), depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False) or not name.lower().endswith(IMAGES):
                    continue
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not 0 < info.st_size <= MAX_IMAGE_BYTES or len(entry.path.encode()) > MAX_PATH_BYTES:
                continue
            rel = os.path.relpath(entry.path, PICTURES)
            pictures.append({"path": entry.path, "rel": rel, "folder": rel.split("/", 1)[0] if "/" in rel else "",
                             "key": image_key(entry.path, info)})
    pictures.sort(key=lambda picture: picture["rel"].lower())
    folders = sorted({picture["folder"] for picture in pictures if picture["folder"]}, key=str.lower)
    return {"dir": str(CACHE), "root": str(PICTURES), "folders": folders, "pictures": pictures, "truncated": truncated}


def snapshot(path, scratch):
    """One open, checked on the descriptor, copied out. None when refused."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        real = os.readlink("/proc/self/fd/%d" % source.fileno())
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES
                or not real.startswith(str(PICTURES) + "/")):
            return None
        out, name = tempfile.mkstemp(dir=scratch)
        copied = 0
        with os.fdopen(out, "wb") as target:
            while True:
                chunk = source.read(1 << 20)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > MAX_IMAGE_BYTES:      # grew mid-read: refused, not truncated
                    os.unlink(name)
                    return None
                target.write(chunk)
    return name, image_key(real, info)


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))


def header(path):
    try:
        line = subprocess.run(["vipsheader", "--", path], capture_output=True, timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.search(r": (\d+)x(\d+) [^,]+, \d+ bands?, [^,]+, ([a-z0-9]+)\s*$", line)
    return (int(match.group(1)), int(match.group(2)), match.group(3)) if match else None


def render(source, size, out, quality):
    """True when made, False when refused, None when the deadline hit (retry later)."""
    fd, temporary = tempfile.mkstemp(dir=CACHE, prefix=".tmp.", suffix=".jpg")
    os.close(fd)
    try:
        try:
            subprocess.run(["vipsthumbnail", source, "--size", size, "--smartcrop=centre", "--no-rotate",
                            "--path", "%s[Q=%d,strip]" % (temporary, quality)],
                           env=dict(os.environ, VIPS_CONCURRENCY="1"), preexec_fn=limits,
                           stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return False
        # vipsthumbnail exits 0 on unreadable input, so the output is what is checked.
        if os.path.getsize(temporary) == 0:
            return False
        os.replace(temporary, out)
        return True
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def generate(path, scratch, stage_size=None):
    taken = snapshot(path, scratch)
    if taken is None:
        return
    snap, key = taken
    try:
        reject = CACHE / ("reject-" + key)
        thumb = CACHE / ("bg-%s.jpg" % key)
        stage = CACHE / ("stage-%s-%s.jpg" % (key, stage_size)) if stage_size else None
        if reject.exists() or (thumb.exists() and (stage is None or stage.exists())):
            if stage is not None and stage.exists():
                os.utime(stage)         # still in use: keep it out of the 30-day sweep
            return
        seen = header(snap)
        results = []
        if seen and seen[2] in LOADERS and 0 < seen[0] * seen[1] <= MAX_PIXELS:
            if stage is not None and not stage.exists():
                results.append(render(snap, stage_size + ">", stage, 85))     # never upscaled
            if not thumb.exists():
                results.append(render(snap, "640x360", thumb, 80))
        else:
            results.append(False)
        # A refusal is remembered by key; a timeout is not, and is retried.
        if False in results:
            try:
                os.close(os.open(reject, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
            except OSError:
                pass
    finally:
        os.unlink(snap)


def sweep(live):
    now = time.time()
    with os.scandir(CACHE) as entries:
        for count, entry in enumerate(entries):
            if count > 40000 or not entry.is_file(follow_symlinks=False):
                continue
            name = entry.name
            if name.startswith("bg-") and name.endswith(".jpg"):
                key = name[3:-4]
            elif name.startswith("stage-") and name.endswith(".jpg"):
                key = name[6:].split("-", 1)[0]
                if key in live and now - entry.stat(follow_symlinks=False).st_mtime > 30 * 86400:
                    key = ""            # nobody has looked at it in a month
            elif name.startswith("reject-"):
                key = name[7:]
            else:
                key = ""                # a stale temp file, or not ours to keep
            if key not in live:
                try:
                    os.unlink(entry.path)
                except OSError:
                    pass


def main():
    action, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if action == "list" and not args:
        signal.alarm(20)
        print(json.dumps(inventory(), ensure_ascii=True))
        return
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    scratch = tempfile.mkdtemp(prefix="lpswatch-pictures.", dir=os.environ.get("XDG_RUNTIME_DIR") or str(CACHE.parent))
    try:
        if action == "thumbs" and not args:
            signal.alarm(900)
            # One run at a time; a second one has nothing to add.
            with open(__file__, "rb") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    return
                pictures = inventory()["pictures"]
                todo = [p["path"] for p in pictures
                        if not (CACHE / ("bg-%s.jpg" % p["key"])).exists() and not (CACHE / ("reject-" + p["key"])).exists()]
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(lambda path: generate(path, scratch), todo))
                sweep({p["key"] for p in pictures})
        elif action == "stage" and 3 <= len(args) <= 2 + MAX_STAGED and args[0].isdigit() and args[1].isdigit():
            signal.alarm(120)
            width, height = int(args[0]), int(args[1])
            if not (320 <= width <= 7680 and 200 <= height <= 4320):
                raise ValueError("Unreasonable stage size")
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda path: generate(path, scratch, "%dx%d" % (width, height)), args[2:]))
        else:
            raise ValueError("Unknown action")
    finally:
        for name in os.listdir(scratch):
            os.unlink(os.path.join(scratch, name))
        os.rmdir(scratch)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("lpswatch pictures: " + str(error), file=sys.stderr)
        sys.exit(1)
