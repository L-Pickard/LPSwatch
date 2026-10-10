#!/usr/bin/env python3
"""Collection changes for LPSwatch: the only code that removes or restores a
theme or a background. argv in, one JSON object out, nothing composed into a
shell string.

  manage.py status
  manage.py remove-theme <theme>          trash it if it is ours, else hide it
  manage.py trash-theme <theme>
  manage.py trash-bg <theme> <extra|theme> <filename>
  manage.py hide-theme <theme> <true|false>
  manage.py add-bg <theme> <image>...       copy images in; never overwrites
  manage.py discard-bg <theme> <filename>...  undo of add-bg: trash the copies
  manage.py restore <id>
  manage.py empty

Nothing here deletes directly. A removal is a rename into the user's own
freedesktop trash (the one the file manager shows), recorded in our journal so
it can be put back, and so that `empty` -- the one permanent action -- can take
out exactly what this plugin put in and leave the rest of the trash alone.

Every path that is written or removed is built here from $HOME and a validated
name; none is taken from argv. The one path argv does supply is the picture the
user chose to add, which is only ever read: once, through one descriptor, into
a copy that is checked as an image before it is given a name. Only two places
are ever touched: ~/.config/omarchy/themes/<theme> and
~/.config/omarchy/backgrounds/<theme>. A refusal is an answer, not a failure:
exit 0 with {"ok": false, "code": ..., "error": ...}. Exit 1 means the state on
disk could not be trusted and nothing was changed.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import time
from urllib.parse import quote, unquote

CAP = 262144
MAX_TRASHED = 2000
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
IMAGES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
# The same ceilings lib.sh holds a theme's own backgrounds to.
MAX_IMAGE_BYTES = 67108864
MAX_PIXELS = 50000000
MAX_BACKGROUNDS = 200
MAX_ADDED = 50
LOADERS = {"jpegload": ".jpg", "pngload": ".png", "webpload": ".webp", "gifload": ".gif"}

HOME = Path.home()
THEMES = HOME / ".config/omarchy/themes"
BACKGROUNDS = HOME / ".config/omarchy/backgrounds"
STOCK = Path(os.environ.get("OMARCHY_PATH") or HOME / ".local/share/omarchy") / "themes"
CURRENT = HOME / ".local/state/omarchy/current"
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state") / "omarchy/lpswatch"
TRASH = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share") / "Trash"


class Refused(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def valid_name(value):
    return isinstance(value, str) and bool(NAME.fullmatch(value)) and ".." not in value


def valid_filename(value):
    return (isinstance(value, str) and 0 < len(value.encode()) <= 255
            and "/" not in value and "\x00" not in value and value not in (".", ".."))


def owned(path):
    """A journal origin must sit inside one of the two folders we manage."""
    text = str(path)
    return any(text.startswith(str(root) + "/") for root in (THEMES, BACKGROUNDS)) and "/../" not in text + "/"


def real_dir(path):
    try:
        return stat.S_ISDIR(os.lstat(path).st_mode)
    except OSError:
        return False


def tree_bytes(path):
    """Disk space a file or folder occupies, never following a link."""
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    total = info.st_blocks * 512
    if stat.S_ISDIR(info.st_mode):
        for root, dirs, files in os.walk(path, followlinks=False):
            for entry in dirs + files:
                try:
                    total += os.lstat(os.path.join(root, entry)).st_blocks * 512
                except OSError:
                    pass
    return total


def current_theme():
    try:
        return (CURRENT / "theme.name").read_text(errors="replace")[:256].strip()
    except OSError:
        return ""


# ------------------------------------------------------------------ journal

def validate(data):
    if not isinstance(data, dict) or set(data) != {"version", "hiddenThemes", "trash"} or data["version"] != 1:
        raise ValueError("Invalid collection state")
    hidden, trash = data["hiddenThemes"], data["trash"]
    if not isinstance(hidden, list) or len(hidden) > 512 or not all(valid_name(x) for x in hidden):
        raise ValueError("Invalid hidden themes")
    if not isinstance(trash, list) or len(trash) > MAX_TRASHED:
        raise ValueError("Invalid trash journal")
    for entry in trash:
        if (not isinstance(entry, dict) or set(entry) != {"id", "kind", "theme", "bytes", "parts"}
                or not isinstance(entry["id"], str) or not re.fullmatch(r"[0-9a-f]{16}", entry["id"])
                or entry["kind"] not in ("theme", "bg") or not valid_name(entry["theme"])
                or not isinstance(entry["bytes"], int) or entry["bytes"] < 0
                or not isinstance(entry["parts"], list) or not 1 <= len(entry["parts"]) <= 2):
            raise ValueError("Invalid trash entry")
        for part in entry["parts"]:
            if (not isinstance(part, dict) or set(part) != {"origin", "name"}
                    or not isinstance(part["origin"], str) or not owned(part["origin"])
                    or not valid_filename(part["name"])):
                raise ValueError("Invalid trash entry")
    return data


def read_state(directory):
    try:
        fd = os.open("collection.json", os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    except FileNotFoundError:
        return {"version": 1, "hiddenThemes": [], "trash": []}
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > CAP:
            raise ValueError("Refused collection state file")
        raw = source.read(CAP + 1)
    if len(raw) > CAP:
        raise ValueError("Collection state too large")
    return validate(json.loads(raw))


def write_state(directory, data):
    raw = json.dumps(validate(data), ensure_ascii=True).encode()
    if len(raw) > CAP:
        raise ValueError("Collection state too large")
    temporary = ".collection-" + secrets.token_hex(16)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, "collection.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


# ------------------------------------------------------------------ trash

def trash_info(name):
    """The original path a trashed item's .trashinfo records, or None."""
    try:
        fd = os.open(TRASH / "info" / (name + ".trashinfo"), os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            return None
        text = source.read(8192).decode(errors="replace")
    for line in text.splitlines():
        if line.startswith("Path="):
            return unquote(line[5:])
    return None


def in_trash(part):
    """Still in the trash, and still the thing we put there."""
    return os.path.lexists(TRASH / "files" / part["name"]) and trash_info(part["name"]) == part["origin"]


def trash_move(source):
    files, info = TRASH / "files", TRASH / "info"
    files.mkdir(parents=True, exist_ok=True, mode=0o700)
    info.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A rename is the whole point: instant, and undone by another rename.
    if os.stat(files).st_dev != os.lstat(source).st_dev:
        raise Refused("device", "The trash is on a different disk; nothing was removed")
    record = "[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(str(source)), time.strftime("%Y-%m-%dT%H:%M:%S"))
    for attempt in range(1000):
        name = source.name if attempt == 0 else "%s.%d" % (source.name, attempt + 1)
        if os.path.lexists(files / name):
            continue
        # The .trashinfo is created exclusively first: that is what claims the name.
        try:
            fd = os.open(info / (name + ".trashinfo"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(fd, "w") as target:
                target.write(record)
            os.rename(source, files / name)
        except OSError:
            os.unlink(info / (name + ".trashinfo"))
            raise
        return name
    raise Refused("full", "Could not find a free name in the trash")


def trash_restore(part):
    origin = Path(part["origin"])
    origin.parent.mkdir(parents=True, exist_ok=True)
    os.rename(TRASH / "files" / part["name"], origin)
    os.unlink(TRASH / "info" / (part["name"] + ".trashinfo"))


def trash_purge(part):
    target = TRASH / "files" / part["name"]
    if real_dir(target):
        shutil.rmtree(target)
    else:
        os.unlink(target)
    os.unlink(TRASH / "info" / (part["name"] + ".trashinfo"))


def prune(data):
    """Forget anything the user has since restored or emptied themselves."""
    kept = []
    for entry in data["trash"]:
        parts = [part for part in entry["parts"] if in_trash(part)]
        if parts:
            kept.append(dict(entry, parts=parts))
    changed = kept != data["trash"]
    data["trash"] = kept
    return changed


# ------------------------------------------------------------------ actions

def images_in(directory):
    if not real_dir(directory):
        return 0
    count = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name.lower().endswith(IMAGES) and entry.is_file(follow_symlinks=False):
                count += 1
    return count


def trash_theme(data, name):
    if not valid_name(name):
        raise Refused("name", "Not a theme name")
    path = THEMES / name
    stock = real_dir(STOCK / name)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        if stock:
            raise Refused("hide", "Stock themes belong to the system and can only be hidden")
        raise Refused("missing", "Theme not found")
    # A linked theme is someone's working folder elsewhere; not ours to move.
    if not stat.S_ISDIR(info.st_mode):
        raise Refused("hide", "This theme is a link to a folder elsewhere and can only be hidden")
    if current_theme() == name:
        raise Refused("active", "That theme is on your desktop. Apply another one first")
    if len(data["trash"]) >= MAX_TRASHED:
        raise Refused("full", "Empty the trash first")
    sources = [path]
    # Its added backgrounds go with it, unless a stock theme of the same name
    # is about to reappear and still wants them.
    if not stock and real_dir(BACKGROUNDS / name):
        sources.append(BACKGROUNDS / name)
    size = sum(tree_bytes(source) for source in sources)
    parts = []
    try:
        for source in sources:
            parts.append({"origin": str(source), "name": trash_move(source)})
    except (OSError, Refused):
        for part in parts:
            trash_restore(part)
        raise
    entry = {"id": secrets.token_hex(8), "kind": "theme", "theme": name, "bytes": size, "parts": parts}
    data["trash"].append(entry)
    return {"id": entry["id"], "theme": name, "stockRemains": stock}


def trash_background(data, name, kind, filename, keep_one=True):
    if not valid_name(name):
        raise Refused("name", "Not a theme name")
    if kind not in ("extra", "theme") or not valid_filename(filename) or not filename.lower().endswith(IMAGES):
        raise Refused("name", "Not a background")
    theme_dir = THEMES / name
    if kind == "theme":
        # Shipped by the theme. Ours to remove only when the theme is a real
        # folder in the user's themes; stock and linked themes get hidden.
        if not real_dir(theme_dir):
            raise Refused("hide", "This background belongs to a stock theme and can only be hidden")
        folder = theme_dir / "backgrounds"
    else:
        folder = BACKGROUNDS / name
    if not real_dir(folder):
        raise Refused("missing", "Background not found")
    path = folder / filename
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise Refused("missing", "Background not found")
    if not stat.S_ISREG(info.st_mode):
        raise Refused("hide", "This background is a link and can only be hidden")
    if os.path.realpath(CURRENT / "background") == str(path):
        raise Refused("active", "That background is on your desktop. Apply another one first")
    shipped = theme_dir / "backgrounds" if real_dir(theme_dir) else STOCK / name / "backgrounds"
    if keep_one and images_in(BACKGROUNDS / name) + images_in(shipped) <= 1:
        raise Refused("last", "Keep one background for this theme")
    if len(data["trash"]) >= MAX_TRASHED:
        raise Refused("full", "Empty the trash first")
    part = {"origin": str(path), "name": trash_move(path)}
    entry = {"id": secrets.token_hex(8), "kind": "bg", "theme": name, "bytes": info.st_blocks * 512, "parts": [part]}
    data["trash"].append(entry)
    return {"id": entry["id"], "theme": name, "path": str(path)}


def sniff(head):
    """The extension the bytes themselves claim, whatever the file is called."""
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def vips_field(path, field):
    result = subprocess.run(["vipsheader", "-f", field, str(path)], capture_output=True, timeout=10)
    return result.stdout.decode(errors="replace").strip() if result.returncode == 0 else None


def check_image(path, extension):
    """Decodable as the type it claims, within the pixel ceiling. vips is the
    decoder thumbs.sh will hand it to, so this is the same judgement, early."""
    try:
        loader = vips_field(path, "vips-loader")
        width, height = vips_field(path, "width"), vips_field(path, "height")
    except FileNotFoundError:
        return      # no vips: thumbs.sh could not show it either; the sniff stands
    except subprocess.TimeoutExpired:
        raise Refused("image", "That picture took too long to read")
    if loader is None or LOADERS.get(loader) != extension or not (width or "").isdigit() or not (height or "").isdigit():
        raise Refused("image", "That file is not a readable JPEG, PNG, WebP or GIF")
    if int(width) * int(height) > MAX_PIXELS:
        raise Refused("image", "That picture is too large (over 50 megapixels)")


def clean_stem(source):
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(source).stem).strip("-._")[:80]
    return stem or "background"


def copy_in(folder, directory, source):
    if not isinstance(source, str) or not source.startswith("/") or "\x00" in source:
        raise Refused("image", "Not a file")
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        raise Refused("image", "Could not open that file")
    temporary = ".adding-" + secrets.token_hex(16)
    try:
        with os.fdopen(fd, "rb") as reader:
            info = os.fstat(reader.fileno())
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_IMAGE_BYTES:
                raise Refused("image", "That is not a picture under 64 MB")
            head = reader.read(16)
            extension = sniff(head)
            if extension is None:
                raise Refused("image", "That file is not a JPEG, PNG, WebP or GIF")
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=directory)
            with os.fdopen(out, "wb") as writer:
                copied, chunk = 0, head
                while chunk:
                    copied += len(chunk)
                    # A file that grows mid-copy is refused, not kept as a prefix.
                    if copied > MAX_IMAGE_BYTES:
                        raise Refused("image", "That is not a picture under 64 MB")
                    writer.write(chunk)
                    chunk = reader.read(1 << 20)
        check_image(folder / temporary, extension)
        stem = clean_stem(source)
        for attempt in range(1, 1000):
            name = stem + ("" if attempt == 1 else "-%d" % attempt) + extension
            # A hard link fails if the name is taken: nothing is ever overwritten.
            try:
                os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            except FileExistsError:
                continue
            return str(folder / name)
        raise Refused("full", "Too many backgrounds share that name")
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def add_backgrounds(data, name, *sources):
    if not valid_name(name):
        raise Refused("name", "Not a theme name")
    if not os.path.isdir(THEMES / name) and not real_dir(STOCK / name):
        raise Refused("missing", "Theme not found")
    if len(sources) > MAX_ADDED:
        raise Refused("full", "Add at most %d pictures at a time" % MAX_ADDED)
    folder = BACKGROUNDS / name
    BACKGROUNDS.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(folder) and not real_dir(folder):
        raise Refused("linked", "This theme's background folder is a link; add pictures to it yourself")
    folder.mkdir(exist_ok=True)
    directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    added, skipped = [], []
    try:
        room = MAX_BACKGROUNDS - images_in(folder)
        for source in sources:
            try:
                if len(added) >= room:
                    raise Refused("full", "This theme already has %d backgrounds" % MAX_BACKGROUNDS)
                added.append(copy_in(folder, directory, source))
            except Refused as refusal:
                skipped.append({"name": os.path.basename(str(source))[:120], "error": str(refusal)})
    finally:
        os.close(directory)
    if not added:
        raise Refused("image", skipped[0]["error"] if skipped else "Nothing to add")
    return {"theme": name, "added": added, "skipped": skipped}


def discard_backgrounds(data, name, *filenames):
    """Undo of an add: the copies go to the trash like any other removal. The
    keep-one rule does not apply — these are being taken back, not chosen."""
    removed, skipped = [], []
    for filename in filenames[:MAX_ADDED]:
        try:
            removed.append(trash_background(data, name, "extra", filename, keep_one=False)["path"])
        except Refused as refusal:
            skipped.append({"name": str(filename)[:120], "error": str(refusal)})
    if not removed:
        raise Refused("missing", skipped[0]["error"] if skipped else "Nothing to take back")
    return {"theme": name, "removed": removed, "skipped": skipped}


def hide_theme(data, name, value):
    if not valid_name(name) or value not in ("true", "false"):
        raise Refused("name", "Not a theme name")
    hidden = data["hiddenThemes"]
    if value == "true":
        if current_theme() == name:
            raise Refused("active", "That theme is on your desktop. Apply another one first")
        if name not in hidden:
            if len(hidden) >= 512:
                raise Refused("full", "Too many hidden themes")
            hidden.append(name)
    elif name in hidden:
        hidden.remove(name)
    return {"theme": name, "hidden": value == "true"}


def remove_theme(data, name):
    """What the delete key means: gone if it can be, hidden if it cannot."""
    try:
        return dict(trash_theme(data, name), action="trashed")
    except Refused as refusal:
        if refusal.code != "hide":
            raise
    return dict(hide_theme(data, name, "true"), action="hidden")


def restore(data, identifier):
    entry = next((e for e in data["trash"] if e["id"] == identifier), None)
    if entry is None:
        raise Refused("missing", "That is no longer in the trash")
    for part in entry["parts"]:
        if not in_trash(part):
            raise Refused("missing", "That is no longer in the trash")
        if os.path.lexists(part["origin"]):
            raise Refused("exists", "Something else is already where that used to be")
    for part in entry["parts"]:
        trash_restore(part)
    data["trash"].remove(entry)
    return {"kind": entry["kind"], "theme": entry["theme"], "path": entry["parts"][0]["origin"]}


def empty(data):
    freed = 0
    for entry in data["trash"]:
        # in_trash is checked per part, immediately before the delete: only an
        # item whose own .trashinfo still names the path we recorded is ours.
        for part in entry["parts"]:
            if in_trash(part):
                trash_purge(part)
        freed += entry["bytes"]
    data["trash"] = []
    return {"freed": freed}


def summary(data):
    return {"hiddenThemes": data["hiddenThemes"], "trashCount": len(data["trash"]),
            "trashBytes": sum(entry["bytes"] for entry in data["trash"])}


# Argument counts; a negative number is a minimum.
ACTIONS = {"status": 0, "remove-theme": 1, "trash-theme": 1, "trash-bg": 3, "hide-theme": 2, "restore": 1,
           "empty": 0, "add-bg": -2, "discard-bg": -2}


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    args = sys.argv[2:]
    if action not in ACTIONS or (len(args) != ACTIONS[action] if ACTIONS[action] >= 0 else len(args) < -ACTIONS[action]):
        raise ValueError("Unknown action")
    signal.alarm(600 if action in ("empty", "add-bg") else 30)
    # A read-only script descriptor serializes changes without a writable lock file.
    with open(__file__, "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            data = read_state(directory)
            before = json.dumps(data, sort_keys=True)
            prune(data)
            result = {"ok": True}
            try:
                if action == "remove-theme":
                    result.update(remove_theme(data, *args))
                elif action == "trash-theme":
                    result.update(trash_theme(data, *args))
                elif action == "trash-bg":
                    result.update(trash_background(data, *args))
                elif action == "add-bg":
                    result.update(add_backgrounds(data, *args))
                elif action == "discard-bg":
                    result.update(discard_backgrounds(data, *args))
                elif action == "hide-theme":
                    result.update(hide_theme(data, *args))
                elif action == "restore":
                    result.update(restore(data, *args))
                elif action == "empty":
                    result.update(empty(data))
            except Refused as refusal:
                result = {"ok": False, "code": refusal.code, "error": str(refusal)}
            finally:
                # Written even when an action failed partway, so the journal
                # never forgets something that did reach the trash.
                if json.dumps(data, sort_keys=True) != before:
                    write_state(directory, data)
            result.update(summary(data))
            print(json.dumps(result, ensure_ascii=True))
        finally:
            os.close(directory)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        print("lpswatch manage: " + str(error), file=sys.stderr)
        sys.exit(1)
