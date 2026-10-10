// Pure functions over the index. No QML here so `node test/model.test.js` can
// cover them.
.pragma library

function norm(s) { return String(s || "").toLowerCase() }

// Subsequence match: the typed characters appear in order, gaps allowed, so
// "catl" finds "catppuccin-latte". A substring hit is the gapless case.
function fuzzy(haystack, needle) {
  var i = 0
  for (var j = 0; j < haystack.length && i < needle.length; j++)
    if (haystack[j] === needle[i]) i++
  return i === needle.length
}

function matches(theme, text, mode) {
  if (mode === "dark" && theme.mode !== "dark") return false
  if (mode === "light" && theme.mode !== "light") return false
  if (mode === "installed" && theme.source !== "user") return false
  if (mode === "stock" && theme.source !== "stock") return false
  if (!text) return true
  var needle = norm(text)
  return fuzzy(norm(theme.name), needle) || fuzzy(norm(theme.label), needle)
}

function filter(themes, text, mode) {
  var out = []
  for (var i = 0; i < themes.length; i++) if (matches(themes[i], text, mode)) out.push(themes[i])
  return out
}

// Search spans the collection; saved browsing filters resume when it clears.
function browse(themes, text, mode, favoritesOnly) {
  if (text) return filter(themes, text, "all")
  var collection = favoritesOnly ? themes.filter(function(theme) { return theme.favorite }) : themes
  return filter(collection, "", mode)
}

// Personal choices project the index without changing its cached records. All
// background arrays are projected together so video/still pairing survives.
function backgroundId(path) {
  return (String(path).indexOf("/omarchy/backgrounds/") !== -1 ? "extra:" : "theme:") + baseName(path)
}

function isHidden(preferences, name, path) {
  var map = preferences.hidden || {}
  var hidden = Object.prototype.hasOwnProperty.call(map, name) ? map[name] : []
  return hidden.indexOf(backgroundId(path)) !== -1
}

function curate(themes, preferences, favoritesOnly, showHidden) {
  var favorites = preferences.favorites || []
  var result = []
  for (var i = 0; i < themes.length; i++) {
    var theme = themes[i]
    if (favoritesOnly && favorites.indexOf(theme.name) === -1) continue
    var row = Object.assign({}, theme)
    row.favorite = favorites.indexOf(theme.name) !== -1
    var hiddenMap = preferences.hidden || {}
    var hidden = Object.prototype.hasOwnProperty.call(hiddenMap, theme.name) ? hiddenMap[theme.name] : []
    if (!hidden.length) { result.push(row); continue }
    var indices = []
    var backgrounds = theme.backgrounds || []
    for (var j = 0; j < backgrounds.length; j++)
      if (showHidden || !isHidden(preferences, theme.name, backgrounds[j])) indices.push(j)
    // Upstream can remove the only unhidden wallpaper; keep a usable fallback.
    if (!indices.length && backgrounds.length) indices.push(0)
    ;["backgrounds", "bgKeys", "bgVideos", "bgVideoKeys"].forEach(function(field) {
      row[field] = indices.map(function(index) { return (theme[field] || [])[index] || "" })
    })
    if (!showHidden && isHidden(preferences, theme.name, theme.preview)) {
      row.preview = row.backgrounds[0] || ""
      row.previewKey = row.bgKeys[0] || ""
    }
    row.hiddenCount = backgrounds.filter(function(path) { return isHidden(preferences, theme.name, path) }).length
    result.push(row)
  }
  return result
}

var MODES = ["all", "dark", "light", "installed", "stock"]

function nextMode(mode) {
  var i = MODES.indexOf(mode)
  return MODES[(i + 1) % MODES.length]
}

function indexOf(rows, name) {
  for (var i = 0; i < rows.length; i++) if (rows[i].name === name) return i
  return -1
}

function findByName(themes, name) {
  var i = indexOf(themes, name)
  return i === -1 ? null : themes[i]
}

function clamp(i, n) {
  if (n <= 0) return -1
  return Math.max(0, Math.min(n - 1, i))
}

function wrap(i, n) {
  if (n <= 0) return -1
  return ((i % n) + n) % n
}

// Background path for a theme at bgIndex; falls back to the preview image.
// This is the theme's file — used to match the current background and to
// hand to omarchy-theme-bg-set, never to load pixels from.
function backgroundAt(theme, bgIndex) {
  if (!theme) return ""
  var bgs = theme.backgrounds || []
  if (bgs.length === 0) return theme.preview || ""
  return bgs[clamp(bgIndex, bgs.length)]
}

// Where the desktop's current background sits in a theme's list. The current
// background link points into the staged copy omarchy-theme-set makes
// (~/.local/state/omarchy/current/theme/backgrounds/x.jpg), not at the source
// file this index lists, so an exact path only matches a background the user
// set themselves through omarchy-theme-bg-set. The copy keeps the file name, so
// that is the fallback -- without it the picker opens on the theme's first
// wallpaper while the desktop is showing its fourth.
function backgroundIndexOf(theme, path) {
  var bgs = theme && theme.backgrounds ? theme.backgrounds : []
  if (!path || bgs.length === 0) return -1
  var exact = bgs.indexOf(path)
  if (exact !== -1) return exact
  // Stock background controls can put the animated alternative on the desktop.
  // Restore its paired still selection so applying it returns to a static image.
  var videos = theme.bgVideos || []
  exact = videos.indexOf(path)
  if (exact !== -1 && exact < bgs.length) return exact
  var base = baseName(path)
  for (var i = 0; i < bgs.length; i++) if (baseName(bgs[i]) === base) return i
  for (var j = 0; j < bgs.length; j++) if (videos[j] && baseName(videos[j]) === base) return j
  return -1
}

function baseName(path) {
  var s = String(path || "")
  return s.slice(s.lastIndexOf("/") + 1)
}

// Cache key of that same image. Every pixel the overlay shows comes from a
// derivative thumbs.sh produced under this key.
function keyAt(theme, bgIndex) {
  if (!theme) return ""
  var keys = theme.bgKeys || []
  if (keys.length === 0) return theme.previewKey || ""
  return keys[clamp(bgIndex, keys.length)] || ""
}

function thumbPath(dir, key) { return dir && key ? dir + "/bg-" + key + ".jpg" : "" }
function stagePath(dir, key, w, h) { return dir && key && w > 0 && h > 0 ? dir + "/stage-" + key + "-" + w + "x" + h + ".jpg" : "" }

// The animated alternative of the background at bgIndex, or "" when that
// background does not move. bgVideoKeys is parallel to backgrounds, with a
// blank at every still-only position -- so a miss is a blank entry, not a
// short array, and the index is never shifted by earlier misses.
function videoKeyAt(theme, bgIndex) {
  if (!theme) return ""
  var keys = theme.bgVideoKeys || []
  if (keys.length === 0) return ""
  var i = clamp(bgIndex, keys.length)
  return i === -1 ? "" : (keys[i] || "")
}

function videoPath(dir, key) { return dir && key ? dir + "/vid-" + key + ".mp4" : "" }

// Whether a background has an animated alternative at all. The QML asks this
// before arming the dwell timer, so a still-only background costs nothing.
function hasVideo(theme, bgIndex) { return videoKeyAt(theme, bgIndex) !== "" }

function ansi(theme) {
  var c = theme && theme.colors ? theme.colors : {}
  return [c.red, c.yellow, c.green, c.cyan, c.blue, c.magenta].map(function(x) { return x || "#808080" })
}

// ---- collection management (LPSwatch)

// Hidden themes leave the strip unless they are being revealed. The theme on
// the desktop is always shown: the picker has to be able to land on it.
function visibleThemes(themes, hiddenNames, showHidden, currentTheme) {
  if (showHidden || !hiddenNames || !hiddenNames.length) return themes
  return themes.filter(function(theme) {
    return theme.name === currentTheme || hiddenNames.indexOf(theme.name) === -1
  })
}

// A removal is shown at once and confirmed by the index refresh that follows.
// Both return new arrays and new rows; cached index records are never edited.
function withoutTheme(themes, name) {
  return themes.filter(function(theme) { return theme.name !== name })
}

function withoutBackground(themes, name, path) {
  return themes.map(function(theme) {
    if (theme.name !== name) return theme
    var at = (theme.backgrounds || []).indexOf(path)
    if (at === -1) return theme
    var row = Object.assign({}, theme)
    // All four together, or every later clip slides onto the wrong wallpaper.
    ;["backgrounds", "bgKeys", "bgVideos", "bgVideoKeys"].forEach(function(field) {
      var list = (theme[field] || []).slice()
      if (at < list.length) list.splice(at, 1)
      row[field] = list
    })
    if (theme.preview === path) {
      row.preview = row.backgrounds[0] || ""
      row.previewKey = row.bgKeys[0] || ""
    }
    return row
  })
}

// Whether removing this background means moving a file (ours) or only hiding
// it (a stock theme's). manage.py takes the real decision; this picks the label.
function ownsBackground(theme, path) {
  if (!theme || !path) return false
  return backgroundId(path).indexOf("extra:") === 0 || theme.source === "user"
}

// Pictures mode borrows the whole picker: each picture is a row wearing the
// target theme's palette, with itself as its one background, so the strip, the
// stage, the wipe and the search all work on pictures without knowing it.
function pictureRows(pictures, theme, folder, text) {
  var needle = norm(text), out = []
  if (!theme) return out
  for (var i = 0; i < pictures.length; i++) {
    var p = pictures[i]
    if (folder && p.folder !== folder) continue
    if (needle && !fuzzy(norm(p.rel), needle)) continue
    out.push({
      name: p.rel, caption: baseName(p.rel), label: p.rel, source: "picture", picture: p.path,
      mode: theme.mode, colors: theme.colors || {}, colorsToml: theme.colorsToml, shellToml: theme.shellToml,
      preview: p.path, previewKey: p.key, backgrounds: [p.path], bgKeys: [p.key],
      bgVideos: [""], bgVideoKeys: [""], hiddenCount: 0, favorite: false
    })
  }
  return out
}

// "" is every folder; then each top-level folder in turn, and back.
function nextFolder(folders, current) {
  var all = [""].concat(folders || [])
  return all[(all.indexOf(current) + 1) % all.length]
}

function formatBytes(bytes) {
  var n = Number(bytes) || 0
  if (n < 1024) return n + " B"
  var units = ["KB", "MB", "GB", "TB"], u = -1
  do { n /= 1024; u++ } while (n >= 1024 && u < units.length - 1)
  return (n >= 100 ? Math.round(n) : n.toFixed(1)) + " " + units[u]
}

if (typeof module !== "undefined") {
  module.exports = { pictureRows: pictureRows, nextFolder: nextFolder, visibleThemes: visibleThemes, withoutTheme: withoutTheme, withoutBackground: withoutBackground, ownsBackground: ownsBackground, formatBytes: formatBytes, browse: browse, curate: curate, backgroundId: backgroundId, isHidden: isHidden, matches: matches, filter: filter, nextMode: nextMode, indexOf: indexOf, findByName: findByName, clamp: clamp, wrap: wrap, backgroundAt: backgroundAt, backgroundIndexOf: backgroundIndexOf, keyAt: keyAt, thumbPath: thumbPath, stagePath: stagePath, videoKeyAt: videoKeyAt, videoPath: videoPath, hasVideo: hasVideo, ansi: ansi, MODES: MODES }
}
