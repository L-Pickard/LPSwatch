#!/usr/bin/env python3
"""Ask for pictures with the desktop's own file dialog and print the chosen
absolute paths as a JSON list — empty when cancelled. Chooses, never reads:
manage.py is what opens a picture, and decides whether it is one.

  choose.py [start-folder] [title]
"""
import json
import os
import sys

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

LIMIT = 50


def choose(start="", title="Add backgrounds", cancellable=None):
    Gtk.init()
    loop = GLib.MainLoop()
    chosen = []
    images = Gtk.FileFilter()
    images.set_name("Pictures (JPEG, PNG, WebP, GIF)")
    for mime in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        images.add_mime_type(mime)
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(images)
    dialog = Gtk.FileDialog(title=title, modal=True, accept_label="Add")
    dialog.set_filters(filters)
    dialog.set_default_filter(images)
    if start and os.path.isdir(start):
        dialog.set_initial_folder(Gio.File.new_for_path(start))

    def finished(source, result):
        try:
            files = source.open_multiple_finish(result)
            chosen.extend(path for path in (item.get_path() for item in files) if path)
        except GLib.Error:
            pass        # dismissed
        loop.quit()

    dialog.open_multiple(None, cancellable, finished)
    loop.run()
    return chosen[:LIMIT]


if __name__ == "__main__":
    print(json.dumps(choose(*sys.argv[1:3])))
