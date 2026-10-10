# LPSwatch: manage themes and backgrounds from the picker

Status: APPROVED by Leo on 2026-10-10.

Progress: slice 1 built (B3, B4, B5, B8, B9, B10, B11 for deletes and hides, B14, and the B13 buttons for those). Automated tests pass; on-screen checks and Leo's sign-off on the look are still to do. Not started: B1, B2, B6, B7, B12.
Quick spec: Obsidian `Specs/Swatch plugin fork.md`. Upstream: `jmckible/omarchy-swatch` at `52bfa98`.

## Goal

Swatch is already the theme picker Leo likes. LPSwatch is his fork of it, which lets him curate his collection without leaving it: add and delete themes, and add and remove backgrounds per theme, from the same full-screen view.

## Behaviours

Every action has both an on-screen button and a keyboard shortcut (see B13). Keys are proposals and apply in navigation mode only (not while typing in search).

### Themes

**B1. Add a theme by URL.** Pressing `a` opens a small input box. Pasting a git repo URL and pressing Enter installs the theme. While it installs, the box shows progress; Swatch stays usable. When done, the new theme appears in the strip, selected for preview, and is **not applied**. A bad URL, a failed clone, or a repo that isn't a theme shows a one-line error and changes nothing.

**B2. Browse for themes.** Pressing `e` closes Swatch and opens the Extra Themes plugin. Themes installed there appear in Swatch the next time it opens (this already works).

**B3. Delete a theme.** Pressing `Shift+Delete` on a user-installed theme moves its folder to the system trash, and moves that theme's user background folder with it. The theme leaves the strip and selection moves to its neighbour. No confirmation prompt; undo is the safety net.

**B4. Stock themes can't be deleted.** The 22 stock themes are system-owned. `Shift+Delete` on one hides it from Swatch instead (same idea as upstream's hidden backgrounds), and a short message says so. Hidden themes can be revealed and restored.

**B5. The active theme is protected.** `Shift+Delete` on the theme currently applied to the desktop is refused with a message: apply another theme first.

### Backgrounds

**B6. Add backgrounds with a file chooser.** Pressing `b` opens the standard file dialog. Choosing one or more images copies them into the selected theme's user background folder (`~/.config/omarchy/backgrounds/<theme>/`). They appear at the end of that theme's background strip, with the first new one selected. Works for stock themes too.

**B7. Add backgrounds by browsing Pictures.** Pressing `p` switches Swatch into a Pictures mode: a full-screen strip of the images under `~/Pictures` (subfolders included, screenshots skipped, and narrowable to a single subfolder such as `Artvee`), each shown full-screen with the selected theme's colours on the chrome, so Leo sees the picture wearing the theme. Enter adds the picture to the theme (a copy) and stays in Pictures mode so several can be added; Esc returns to the theme with the new backgrounds in its strip.

**B8. Remove a background.** Pressing `Delete` on a background Leo owns (anything in a user background folder, or shipped inside a user-installed theme) moves the file to the system trash. This replaces upstream's hide for those files.

**B9. Stock backgrounds are hidden, not deleted.** `Delete` on a background shipped by a stock theme hides it in Swatch, exactly as upstream does today.

**B10. A theme keeps at least one background.** Removing or hiding the last visible background of a theme is refused (upstream rule, kept).

### Undo

**B11. Undo.** `Ctrl+Z` reverses the most recent delete, hide or add, and can be pressed repeatedly to step back through everything done since Swatch was opened. Undoing a delete restores the files from the trash to where they were; undoing an add removes the copies that were added. Undo history is forgotten when Swatch closes; trashed files stay in the system trash until Leo empties it.

### General

**B12. Added files are copies.** Adding never moves or changes the original picture. If a file with the same name already exists in the theme, the new one gets a numeric suffix; nothing is overwritten.

**B13. Buttons for everything.** Every action in this spec (add theme, browse themes, delete theme, add backgrounds, browse Pictures, remove background, undo, empty trash) has an on-screen button as well as its shortcut, so LPSwatch is fully usable with the mouse. Buttons are styled as part of Swatch's chrome: painted from the previewed theme's palette, same shapes, spacing and motion as the existing strip and search field, icon plus short label, with the shortcut shown on hover. Theme actions sit with the theme strip and background actions with the background strip. Buttons that don't apply are visibly disabled (for example delete on the active theme) and say why on hover. They must not cover the wallpaper preview more than the existing chrome does.

**B14. Empty the trash.** An "Empty trash" button shows how much space LPSwatch's trashed themes and backgrounds are using. Pressing it asks for confirmation, then permanently deletes only the items LPSwatch itself trashed, leaving anything else in the system trash alone. This cannot be undone, and it clears the undo history for those items.

## Out of scope

- A theme catalogue built into Swatch (Extra Themes covers it).
- Adding backgrounds from a pasted web URL, or copying a background from another theme.
- Artwork info (title, artist, resolution) on backgrounds. Candidate for a later spec.
- Renaming or reordering backgrounds, editing a theme's colours.
- Anything about OmaSettings vs per-theme look settings.
- Deleting or modifying anything under `/usr/share/omarchy`.

## Constraints

- **What the fork may write or remove**, and nothing else:
  - `~/.config/omarchy/themes/<theme>/` (install by clone; move to trash)
  - `~/.config/omarchy/backgrounds/<theme>/` (copy images in; move to trash)
  - Swatch's own cache and preferences, as upstream.
- **Never delete directly.** All removals go into the system trash (the same one the file manager shows, written to the freedesktop trash format by `manage.py`). The one exception is B14, which permanently deletes only items LPSwatch trashed, after a confirm.
- **Keep upstream's security posture.** Theme names, URLs and filenames are untrusted: validated against the same name rules upstream uses, passed as arguments (never built into a shell command line), no following of symlinks out of the allowed folders. Added images go through the same checks as existing backgrounds (real image, size and pixel ceilings). URL checking reuses Omarchy's own `omarchy-git-url-check`.
- **Network:** only the `git clone` in B1. Nothing else contacts the internet.
- **No new dependencies** beyond what Omarchy ships.
- **Stays fast.** Nothing added to the open path that scales with the number of themes or pictures; Pictures mode indexes and thumbnails only when entered.
- **Stays mergeable.** New logic goes in new files where practical, so upstream Swatch updates can still be pulled in. Upstream's existing tests stay green.
- **Plugin identity.** Display name `LPSwatch`, repo `L-Pickard/LPSwatch` (a GitHub fork of upstream, renamed), plugin id `lpickard.lpswatch`, so it can't be confused with, or overwritten by, upstream Swatch. Existing favourites and hidden backgrounds carry over.
- **Per-theme look hook** (`~/.config/omarchy/hooks/theme-set.d/theme-overrides`) keeps working unchanged; the fork still applies themes through Omarchy's own commands.

## Done means

| # | Check |
|---|---|
| B1 | Paste a known theme repo URL: it appears selected, the desktop theme has not changed, Enter applies it. Paste garbage: error shown, theme count unchanged. |
| B2 | `e` opens Extra Themes; a theme installed there is in Swatch on next open. |
| B3 | `Shift+Delete` on a user theme: it's gone from Swatch and `omarchy theme list`, and both folders are in the trash. |
| B4 | `Shift+Delete` on a stock theme: hidden in Swatch, files untouched, still in `omarchy theme list`. |
| B5 | `Shift+Delete` on the active theme: refused, nothing changes. |
| B6 | Pick three images in the dialog: three new backgrounds in the strip, originals still in place. |
| B7 | `p`, add two pictures, Esc: both are in the theme's strip and apply with Enter. |
| B8 | `Delete` on an owned background: file is in the trash and Omarchy's own background cycling no longer shows it. |
| B9 | `Delete` on a stock background: hidden in Swatch, file untouched. |
| B10 | With one background left, `Delete` is refused. |
| B11 | Delete a theme, add a background, delete a background, then `Ctrl+Z` three times: everything is back exactly as it was. |
| B12 | Add the same picture twice: two files, neither overwritten. |
| B13 | Every check above can be done with the mouse alone. Buttons retint with each previewed theme and stay readable on a light theme, a dark theme and a low-contrast one. Leo signs off on the look from a screenshot before it is called done. |
| B14 | Trash a theme and a background, note the size shown, empty: the size drops to zero, those items are gone from the system trash, and an unrelated file trashed beforehand is still there. |
| All | Upstream tests pass; new automated tests cover the scripts, including hostile inputs (bad URLs, path tricks in names, symlinks, fake images) run in an isolated home folder so no real theme is ever touched. |

## Priorities

- **Must:** B3, B4, B5, B6, B8, B9, B10, B11, B12, B13, B14
- **Should:** B1, B2
- **Biggest single piece, built last:** B7 (Pictures mode)

Build order: delete, undo and empty trash first (they make the rest safe to test), then file-chooser add, then theme add, then Pictures mode. Each slice ships with its buttons.

## Open questions

None.

## Decisions

| Date | Decision | Why |
|---|---|---|
| 2026-10-10 | Add theme by pasted URL and via Extra Themes, not a built-in catalogue | Leo wants both routes; a catalogue would duplicate Extra Themes |
| 2026-10-10 | Deletes go to the trash with undo, no confirm prompt | Leo's choice; recoverable without slowing down bulk clear-outs |
| 2026-10-10 | Backgrounds are added by file chooser and by a Pictures browse | Leo's choice; URL paste and copy-between-themes dropped |
| 2026-10-10 | Public fork on Leo's GitHub | Leo's choice; easiest way to keep pulling upstream updates |
| 2026-10-10 | Named LPSwatch | Leo's choice |
| 2026-10-10 | Every action has a styled on-screen button as well as a shortcut | Leo's requirement |
| 2026-10-10 | Empty-trash action limited to LPSwatch's own trashed items | Leo wants the space back; the system trash may hold unrelated files |
| 2026-10-10 | The trash is written directly in the freedesktop format instead of calling `gio trash` | Gives the exact trash entry back, which undo and "empty only ours" both need, and lets the tests run in a throwaway home folder |
| 2026-10-10 | LPSwatch keeps its own cache and saved-choices folders (`omarchy/lpswatch`) | So it never fights upstream Swatch if both are installed. There were no saved favourites to carry over |
| 2026-10-10 | Empty trash shortcut is `Ctrl+Shift+Delete`, and asks twice (second press or Enter within 6 seconds) | The spec named no key; it is the one permanent action |
| 2026-10-10 | A background that is on the desktop right now can't be removed | Same reasoning as B5 for themes |
| 2026-10-10 | A newly installed theme is previewed, not applied | Keeps Enter as the only thing that changes the desktop |
| 2026-10-10 | Keys: `a` add theme, `e` Extra Themes, `b` add backgrounds, `p` Pictures, `Delete` remove background, `Shift+Delete` delete theme, `Ctrl+Z` undo | Approved as proposed |
| 2026-10-10 | Backgrounds shipped inside a user-installed theme are trashed like any other | Otherwise most backgrounds could only be hidden. Accepted cost: that theme's `git pull` fails if its author later changes the same file |
| 2026-10-10 | Pictures mode shows all of `~/Pictures` except screenshots, with a way to narrow to one folder | Approved as proposed |
