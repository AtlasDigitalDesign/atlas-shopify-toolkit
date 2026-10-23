"""Compose Pinterest pins for every plan in the catalogue.

    python3 pin_compose.py                  # every plan that has assets
    python3 pin_compose.py --only the-holly
    python3 pin_compose.py --contact-sheet  # also write a 236px review strip

Reads the folders `fetch_plan_assets.py` produces, writes one file per plan per
variant into `Pinterest/composed/` named `<handle>__<variant>.jpg`, which is
exactly what `campaign.generate()` looks for.

The four variants and why each exists
-------------------------------------
elevation  Render at 67% of the canvas, name band, no floor plan. Pinterest's
           own guidance is to let the imagery take centre stage, and at 236px
           this is the strongest of the four.
plan       Render on top, then ONE ROOM BLOCK of the floor plan enlarged. A
           whole floor plan cannot be read at feed size - roughly fifteen room
           labels across 200 pixels is 4px type - so a fragment at scale beats
           a complete sheet reduced. Tested.
planhero   The floor plan at full canvas width, portrait. Floor plans ARE
           portrait; the old layout put one in a landscape band, which is most
           of why it landed small. This runs it about 2.5x larger.
detail     Cropped into the architecture with a dark overlay. The variant that
           works with no legible text at all.

Brand values are sampled from the designer's existing cards, not invented:
    green band #99C5A2   navy ink #04082E
The wordmark is lifted from an existing pin as pixels so it is exactly the
store's mark rather than a re-set approximation.

Two things an earlier version got wrong, fixed here
---------------------------------------------------
* Plan names were drawn at a fixed size. Nineteen of the sixty-six names
  (the longest plan names in the catalog) were wider
  than the canvas and clipped at both ends. Names are now measured and shrunk
  to fit, and wrapped onto two lines when shrinking alone would make them
  smaller than the spec line beneath.
* Every floor-plan pin was captioned "FIRST FLOOR · MAIN LIVING" regardless
  of which sheet was actually used. The caption now comes from the file the
  plan slot was loaded from, and no longer claims to know which rooms are in
  the crop.
"""
from __future__ import annotations
import argparse
import csv
import datetime as _dt
import json
import os
import re
import sys
import traceback
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
PLANS = os.path.abspath(os.path.join(HERE, "..", "..", "Plans"))
PIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
OUT = os.path.join(PIN_ROOT, "composed")
WORDMARK_SRC = os.path.join(PIN_ROOT, "wordmark_source_pin.jpg")
ASSIGNMENTS = os.path.join(HERE, "board_assignments.csv")

W, H = 1000, 1500
GREEN = (153, 197, 162)
NAVY = (4, 8, 46)
WHITE = (255, 255, 255)
MUTED = (90, 96, 110)
MARGIN = 44                      # text never comes closer than this to an edge
TEXT_MAX_W = W - 2 * MARGIN

# Type. The site sets headings in Archivo Narrow 500, uppercase, tracked
# .085em, and body in Barlow (verified from the live theme's computed
# styles). The pins use the same faces from rebuild/fonts/ so a pin and the
# product page it lands on read as one brand. Arial / DejaVu are the fallback
# only, and the run says so loudly, because a pin set in Arial is the kind of
# thing a designer spots from across the room.
FONTS = os.path.join(HERE, "fonts")


def _first_font(*cands):
    for c in cands:
        if os.path.exists(c):
            return c
    return None


BOLD = _first_font(os.path.join(FONTS, "ArchivoNarrow-Medium.ttf"),
                   "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                   "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REG = _first_font(os.path.join(FONTS, "Barlow-Regular.ttf"),
                  "/System/Library/Fonts/Supplemental/Arial.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
CAPTION = _first_font(os.path.join(FONTS, "Barlow-Medium.ttf"),
                      os.path.join(FONTS, "Barlow-Regular.ttf"), BOLD or "")
BRAND_TYPE = bool(BOLD and "ArchivoNarrow" in BOLD)
# The site's heading tracking, as a fraction of the font size.
TRACK_HEAD = 0.085
TRACK_CAPTION = 0.12

# Which downloaded sheet feeds the plan slot, in preference order, and what
# the pin may truthfully call it. The ground sheet is deliberately absent:
# on an elevated coastal plan it is parking and storage under the house, and
# a pin of a garage layout sells nothing. A plan with only a ground sheet
# gets the elevation and detail pins.
PLAN_SOURCES = (("plan_first", "FIRST FLOOR"),
                ("plan_other", "FLOOR PLAN"),
                ("plan_second", "SECOND FLOOR"))


def font(path, size):
    return ImageFont.truetype(path, size)


def text_width(draw, text, f, track):
    return sum(draw.textlength(c, font=f) for c in text) + track * max(0, len(text) - 1)


def tracked(draw, y, text, f, fill, track=0, cx=W / 2):
    """Letter-spaced text. PIL has no tracking and the band type is heavily
    tracked, so it is drawn per character."""
    widths = [draw.textlength(c, font=f) for c in text]
    x = cx - (sum(widths) + track * (len(text) - 1)) / 2
    for c, w in zip(text, widths):
        draw.text((x, y), c, font=f, fill=fill)
        x += w + track


def split_two(text):
    """Break at the space nearest the middle. Returns [text] if there is no
    space to break at."""
    idx = [i for i, ch in enumerate(text) if ch == " "]
    if not idx:
        return [text]
    mid = len(text) / 2
    cut = min(idx, key=lambda i: abs(i - mid))
    return [text[:cut].strip(), text[cut + 1:].strip()]


def fit_lines(draw, text, path, size, track_ratio, max_w=TEXT_MAX_W,
              min_ratio=0.75):
    """Return [(line, font, track)] so every line fits inside max_w.

    First shrink the single line down to min_ratio of the asked size. If it is
    still too wide, wrap onto two lines and shrink those from the asked size
    instead. A name that cannot fit even then is shrunk until it does, which
    is the only acceptable last resort - clipping a plan's name is worse than
    setting it small."""
    def attempt(lines, px):
        f = font(path, px)
        tr = px * track_ratio
        return f, tr, max(text_width(draw, ln, f, tr) for ln in lines)

    px = size
    floor = max(12, int(size * min_ratio))
    while px >= floor:
        f, tr, w = attempt([text], px)
        if w <= max_w:
            return [(text, f, tr)]
        px -= 2

    lines = split_two(text)
    if len(lines) == 1:            # one unbreakable word: keep shrinking
        px = floor
        while px > 12:
            f, tr, w = attempt(lines, px)
            if w <= max_w:
                break
            px -= 2
        return [(text, f, tr)]

    px = int(size * 0.86)
    while px > 12:
        f, tr, w = attempt(lines, px)
        if w <= max_w:
            break
        px -= 2
    return [(ln, f, tr) for ln in lines]


def cover(img, bw, bh, focus_y=0.5):
    s = max(bw / img.width, bh / img.height)
    im = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    left = (im.width - bw) // 2
    top = int((im.height - bh) * focus_y)
    return im.crop((left, top, left + bw, top + bh))


def fit(img, bw, bh):
    s = min(bw / img.width, bh / img.height)
    return img.resize((max(1, round(img.width * s)),
                       max(1, round(img.height * s))), Image.LANCZOS)


def trim(img, thresh=246):
    """Crop the paper margin off a floor-plan sheet. Sampled every 3px - exact
    bounds do not matter, and a per-pixel scan of a 2000px sheet is slow across
    66 plans."""
    g = img.convert("L")
    px, w, h = g.load(), g.width, g.height
    x0, y0, x1, y1 = w, h, 0, 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            if px[x, y] < thresh:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 <= x0 or y1 <= y0:
        return img
    p = 8
    return img.crop((max(0, x0 - p), max(0, y0 - p),
                     min(w, x1 + p), min(h, y1 + p)))


def best_fragment(p, frac_w=0.58, frac_h=0.48, steps=6):
    """Which block of the sheet to enlarge.

    The default window (4-62% across, 30-78% down) was tuned on real sheets
    and usually lands on the living and kitchen block, which is the part
    that sells. It is kept whenever it holds a reasonable share of the
    sheet's ink (35% of the densest window; measured across all 57 sheets,
    the normal ones sit at 0.4 to 1.0 and the sparse ones - Blue Isle,
    two of the catalog's plans, at 0.15 to 0.2). When it lands on a blank
    corner the window with the most ink is used instead. Scored on a small
    grayscale copy for speed."""
    g = p.convert("L")
    g.thumbnail((240, 240))
    px, gw, gh = g.load(), g.width, g.height

    def ink(x0, y0, x1, y1):
        n = 0
        for y in range(max(0, y0), min(gh, y1), 2):
            for x in range(max(0, x0), min(gw, x1), 2):
                if px[x, y] < 128:
                    n += 1
        return n

    default = (int(gw * .04), int(gh * .30), int(gw * .62), int(gh * .78))
    d_ink = ink(*default)
    ww, wh = max(1, int(gw * frac_w)), max(1, int(gh * frac_h))
    best, best_box = -1, default
    xs = [int(i * (gw - ww) / max(1, steps - 1)) for i in range(steps)] if gw > ww else [0]
    ys = [int(i * (gh - wh) / max(1, steps - 1)) for i in range(steps)] if gh > wh else [0]
    for y0 in ys:
        for x0 in xs:
            n = ink(x0, y0, x0 + ww, y0 + wh)
            if n > best:
                best, best_box = n, (x0, y0, x0 + ww, y0 + wh)
    box = default if d_ink >= 0.35 * best else best_box
    sx, sy = p.width / gw, p.height / gh
    x0, y0, x1, y1 = box
    return p.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))


def band(c, top, height, name, spec, name_px=64, spec_px=27):
    """Green band with the plan name over a spec line, both fitted to width
    and the whole block centred vertically in the band."""
    d = ImageDraw.Draw(c)
    d.rectangle([0, top, W, top + height], fill=GREEN)
    name_lines = fit_lines(d, name, BOLD, name_px, TRACK_HEAD)
    npx = name_lines[0][1].size
    spec_lines = fit_lines(d, spec, BOLD, spec_px, TRACK_HEAD, min_ratio=0.75) if spec else []
    spx = spec_lines[0][1].size if spec_lines else 0

    line_h = npx * 1.12
    block = len(name_lines) * line_h + (spx * (0.35 + 1.2 * len(spec_lines)) if spec_lines else 0)
    y = top + (height - block) / 2 - npx * 0.10
    for ln, f, tr in name_lines:
        tracked(d, y, ln, f, NAVY, tr)
        y += line_h
    if spec_lines:
        y += spx * 0.35
        for ln, f, tr in spec_lines:
            tracked(d, y, ln, f, NAVY, tr)
            y += spx * 1.2


def caption(c, y, text):
    d = ImageDraw.Draw(c)
    lines = fit_lines(d, text, CAPTION, 21, TRACK_CAPTION, min_ratio=0.8)
    for ln, f, tr in lines:
        tracked(d, y, ln, f, MUTED, tr)
        y += f.size * 1.25


def floorspec(a, spec):
    label = a.get("plan_floor") or "FLOOR PLAN"
    return ("%s  ·  %s" % (label, spec["SPEC"])) if spec["SPEC"] else label


# --------------------------------------------------------------- variants
def v_elevation(a, wm, spec):
    c = Image.new("RGB", (W, H), WHITE)
    c.paste(wm, (0, 0))
    c.paste(cover(a["hero"], W, 1010, 0.52), (0, 166))
    band(c, 1176, 250, spec["NAME"], spec["SPEC"], 78, 30)
    tracked(ImageDraw.Draw(c), 1450, spec["DOMAIN"], font(REG, 25), NAVY, 0.6)
    return c


def v_plan(a, wm, spec):
    if "plan" not in a:
        return None
    c = Image.new("RGB", (W, H), WHITE)
    c.paste(wm, (0, 0))
    c.paste(cover(a["hero"], W, 620, 0.55), (0, 166))
    band(c, 786, 208, spec["NAME"], spec["SPEC"], 66, 26)
    p = trim(a["plan"])
    frag = best_fragment(p)
    pl = fit(frag, 940, 400)
    c.paste(pl, ((W - pl.width) // 2, 1012 + (400 - pl.height) // 2))
    # The crop is a fixed window into the sheet; it does not know which rooms
    # it caught, so the caption says what it is - a detail of this floor - and
    # not "MAIN LIVING".
    caption(c, 1452, "%s  ·  PLAN DETAIL" % (a.get("plan_floor") or "FLOOR PLAN"))
    return c


def v_planhero(a, wm, spec):
    if "plan" not in a:
        return None
    c = Image.new("RGB", (W, H), WHITE)
    c.paste(wm, (0, 0))
    pl = fit(trim(a["plan"]), 1000, 1112)
    c.paste(pl, ((W - pl.width) // 2, 178))
    band(c, 1318, 182, spec["NAME"], floorspec(a, spec), 58, 24)
    return c


def v_detail(a, wm, spec):
    c = Image.new("RGB", (W, H), WHITE)
    # Prefer a real interior; otherwise crop into the FRONT render. Never
    # hero2 by default - it is often the aerial or rear shot, and an aerial
    # scaled to a 236px thumbnail reads as a sheet of roofing.
    src = a.get("detail") or a["hero"]
    fw, fh = src.size
    crop = src.crop((int(fw * .18), int(fh * .16), int(fw * .82), int(fh * .96)))
    c.paste(cover(crop, W, 1290, 0.5), (0, 0))
    ov = Image.new("RGBA", (W, 300), (4, 8, 46, 205))
    c.paste(Image.alpha_composite(
        c.crop((0, 990, W, 1290)).convert("RGBA"), ov).convert("RGB"), (0, 990))
    d = ImageDraw.Draw(c)
    name_lines = fit_lines(d, spec["NAME"], BOLD, 74, TRACK_HEAD)
    npx = name_lines[0][1].size
    line_h = npx * 1.1
    sub_lines = fit_lines(d, spec["FOUND"], BOLD, 26, TRACK_HEAD, min_ratio=0.75)
    block = len(name_lines) * line_h + 26 * (0.5 + 1.3 * len(sub_lines))
    y = 990 + (300 - block) / 2 - npx * 0.08
    for ln, f, tr in name_lines:
        tracked(d, y, ln, f, WHITE, tr)
        y += line_h
    y += 26 * 0.5
    for ln, f, tr in sub_lines:
        tracked(d, y, ln, f, GREEN, tr)
        y += 26 * 1.3
    c.paste(wm.resize((640, 106), Image.LANCZOS), (180, 1316))
    return c


VARIANTS = {"elevation": v_elevation, "plan": v_plan,
            "planhero": v_planhero, "detail": v_detail}


# ------------------------------------------------------------------ load
def open_rgb(path):
    """Open on a WHITE ground. A transparent PNG floor plan converted straight
    to RGB turns its transparent pixels black - a black sheet with black
    walls, and trim() finds nothing."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    return im.convert("RGB")


def load_assets(folder):
    """Map the downloaded files onto the slots the variants need. A plan with
    no hero is skipped entirely rather than composed from a floor plan.

    `plan_floor` records which sheet filled the plan slot so the pin can be
    captioned truthfully; `detail_src` says whether the detail crop is an
    interior or the exterior, for the alt text."""
    def first(*names):
        for n in names:
            # Newest of .jpg / .png wins, so a re-fetch that changed format
            # is not shadowed by the stale file of the other extension.
            cands = [os.path.join(folder, n + ext) for ext in (".jpg", ".png")]
            cands = [c for c in cands if os.path.exists(c)]
            if cands:
                return n, open_rgb(max(cands, key=os.path.getmtime))
        return None, None

    overrides = {}
    opath = os.path.join(folder, "_overrides.json")
    if os.path.exists(opath):
        try:
            with open(opath, encoding="utf-8") as f:
                overrides = json.load(f)
        except (OSError, ValueError):
            overrides = {}

    a = {}
    _, hero = first("hero")
    if hero is None:
        return None
    # Per-plan hand override. {"hero_crop": [left, top, right, bottom]} as
    # fractions trims a badge or watermark off the product's render - the
    # two plan variants carry a "360 Tour" label in the top corner.
    hc = overrides.get("hero_crop")
    if isinstance(hc, list) and len(hc) == 4:
        try:
            l, t, r, b = [float(v) for v in hc]
            ok = 0 <= l < r <= 1 and 0 <= t < b <= 1 and (r - l) >= 0.3 and (b - t) >= 0.3
        except (TypeError, ValueError):
            ok = False
        if ok:
            hero = hero.crop((int(hero.width * l), int(hero.height * t),
                              int(hero.width * r), int(hero.height * b)))
        else:
            print("   WARNING: ignoring hero_crop %r in %s - use fractions, "
                  "0 <= left < right <= 1, 0 <= top < bottom <= 1" % (hc, opath))
    a["hero"] = hero
    _, img = first("hero2")
    if img is not None:
        a["hero2"] = img
    _, img = first("detail_1", "detail")
    if img is not None:
        a["detail"] = img
    a["detail_src"] = "interior" if img is not None else "exterior"
    which, img = first(*[n for n, _ in PLAN_SOURCES])
    if img is not None and min(img.size) >= 300:
        a["plan"] = img
        a["plan_floor"] = dict(PLAN_SOURCES)[which]
    elif img is not None:
        print("   plan sheet %s is only %dx%d - too small to use" % (which, *img.size))
    return a


TITLE_RE = re.compile(
    r"^(?P<name>[^|]+?)\s*\|\s*(?P<cat>[^|]+?)\s*\|\s*"
    r"(?P<sqft>[\d,]+)\s*Sq\s*Ft\s*\|\s*(?:(?P<beds>[\d.\-]+)\s*BR|(?P<studio>Studio))\s*"
    r"(?P<baths>[\d.\-]+)\s*BA", re.I)


def parse_title(title):
    """Pull the specs out of the product title.

    Every title follows the same shape, which the designer has kept consistent:
        The Holly | Elevated Narrow-Lot Coastal House Plan | 1,495 Sq Ft | 3BR 2.5BA

    This replaces an earlier version that looked for the foundation type in
    image ALT text. Shopify's alt text does not contain it, so that lookup
    silently failed on every plan and fell back to a generic subtitle. The
    title is authoritative, always present, and the category it carries is a
    better subtitle than the foundation anyway.
    """
    m = TITLE_RE.match(title or "")
    if not m:
        return None
    g = m.groupdict()
    return {"name": g["name"].strip(), "category": g["cat"].strip(),
            "sqft": g["sqft"], "beds": g["beds"] or "Studio", "baths": g["baths"]}


def specs_for(handle, row, manifest):
    parsed = parse_title(manifest.get("title") or "")
    if parsed:
        name, cat = parsed["name"], parsed["category"]
        sqft, beds = parsed["sqft"], parsed["beds"]
    else:
        # No manifest yet, or a title that does not follow the pattern. Fall
        # back to the assignments file and say nothing that is not known.
        name = row.get("plan") or handle.replace("-", " ").title()
        cat = "Coastal House Plan"
        sqft = (row.get("sq_ft") or "").strip()
        if sqft.isdigit():
            sqft = "{:,}".format(int(sqft))
        beds = (row.get("beds") or "").strip()

    bits = []
    if sqft:
        bits.append("%s SQ FT" % sqft)
    if beds in ("0", "Studio", "studio"):
        bits.append("STUDIO")
    elif beds:
        bits.append("%s BED" % beds)
    spec = "  ·  ".join(bits)
    return {"NAME": name.upper(),
            "SPEC": spec,
            "FOUND": cat.upper(),
            "DOMAIN": os.environ.get("PIN_DOMAIN", "example-plans.com")}


def remove_stale(handle, out_dir, keep=()):
    """Delete this plan's composed files that this run did not produce. A
    plan that lost its floor-plan sheet, or now errors, must not leave last
    month's __plan.jpg in the folder for generate() to queue."""
    removed = []
    for v in VARIANTS:
        p = os.path.join(out_dir, "%s__%s.jpg" % (handle, v))
        if p not in keep and os.path.exists(p):
            os.remove(p)
            removed.append(p)
    return removed


def subject_for(vname, assets):
    """What the finished image shows, for the alt text generate() writes."""
    floor = (assets.get("plan_floor") or "floor plan").lower()
    if vname == "elevation":
        return "front exterior rendering"
    if vname == "plan":
        return "front exterior rendering above an enlarged %s detail" % (
            floor if floor == "floor plan" else floor + " plan")
    if vname == "planhero":
        return "%s plan" % floor if floor != "floor plan" else "floor plan"
    if vname == "detail":
        return ("interior rendering" if assets.get("detail_src") == "interior"
                else "architectural detail of the exterior rendering")
    return "rendering"


def compose_one(handle, folder, row, wm, out_dir):
    """Compose every variant for one plan. Returns (paths, meta, reason)
    where reason is set when nothing could be composed."""
    if not os.path.isdir(folder):
        return [], {}, "no asset folder"
    assets = load_assets(folder)
    if assets is None:
        return [], {}, "no hero render"
    mpath = os.path.join(folder, "_manifest.json")
    manifest = {}
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
    spec = specs_for(handle, row, manifest)

    paths, meta = [], {}
    for vname, fn in VARIANTS.items():
        img = fn(assets, wm, spec)
        if img is None:
            continue
        path = os.path.join(out_dir, "%s__%s.jpg" % (handle, vname))
        tmp = path + ".part"
        img.save(tmp, "JPEG", quality=88, optimize=True)
        os.replace(tmp, path)
        paths.append(path)
        meta["%s__%s" % (handle, vname)] = {
            "subject": subject_for(vname, assets),
            "floor": assets.get("plan_floor", ""),
            "composed": _dt.datetime.now().isoformat(timespec="seconds")}
    remove_stale(handle, out_dir, keep=paths)
    return paths, meta, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--contact-sheet", action="store_true")
    ap.add_argument("--plans-dir", default=PLANS)
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()

    if not os.path.exists(WORDMARK_SRC):
        sys.exit("Wordmark source missing: %s" % WORDMARK_SRC)
    if BRAND_TYPE:
        print("type: Archivo Narrow Medium + Barlow (brand faces from fonts/)")
    else:
        print("WARNING: brand fonts not found in %s - falling back to %s. The pins "
              "will not match the site's type. Put ArchivoNarrow-Medium.ttf and "
              "Barlow-Regular.ttf / Barlow-Medium.ttf in that folder." % (FONTS, os.path.basename(BOLD or "?")))
    wm = Image.open(WORDMARK_SRC).crop((0, 0, 1000, 166))

    rows = {}
    if os.path.exists(ASSIGNMENTS):
        with open(ASSIGNMENTS, newline="", encoding="utf-8") as f:
            rows = {r["handle"]: r for r in csv.DictReader(f)}

    os.makedirs(a.out, exist_ok=True)
    meta_path = os.path.join(a.out, "_composed.json")
    all_meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                all_meta = json.load(f)
        except (OSError, ValueError):
            all_meta = {}
    skipped, produced = [], []
    handles = [a.only] if a.only else sorted(rows or os.listdir(a.plans_dir))

    for handle in handles:
        folder = os.path.join(a.plans_dir, handle)
        # One corrupt download must not abort the other sixty-five plans.
        try:
            paths, meta, why = compose_one(handle, folder, rows.get(handle, {}), wm, a.out)
        except Exception as e:                     # noqa: BLE001
            skipped.append((handle, "ERROR %s: %s" % (type(e).__name__, e)))
            traceback.print_exc(limit=1)
            # Nothing from this plan can be vouched for now.
            for p in remove_stale(handle, a.out):
                print("   removed stale %s" % os.path.basename(p))
            all_meta = {k: v for k, v in all_meta.items() if not k.startswith(handle + "__")}
            continue
        if why:
            skipped.append((handle, why))
            for p in remove_stale(handle, a.out):
                print("   removed stale %s" % os.path.basename(p))
            all_meta = {k: v for k, v in all_meta.items() if not k.startswith(handle + "__")}
            continue
        all_meta = {k: v for k, v in all_meta.items() if not k.startswith(handle + "__")}
        all_meta.update(meta)
        produced += paths
        # Count what THIS run produced, not what happens to be on disk from
        # an earlier run - a plan whose floor plan was removed would otherwise
        # still report four variants.
        print("%-38s %d variants" % (handle, len(paths)))

    tmp = meta_path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(all_meta, f, indent=1, sort_keys=True)
    os.replace(tmp, meta_path)

    print("\n%d images written to %s" % (len(produced), a.out))
    if skipped:
        print("%d plans skipped:" % len(skipped))
        for h, why in skipped:
            print("   %-34s %s" % (h, why))

    if a.contact_sheet and produced:
        tw, th, per = 236, 354, 6
        rows_n = (len(produced) + per - 1) // per
        sheet = Image.new("RGB", (per * (tw + 14) + 14,
                                  rows_n * (th + 34) + 14), (250, 250, 251))
        d = ImageDraw.Draw(sheet)
        f = font(BOLD, 12)
        for i, p in enumerate(produced):
            x = 14 + (i % per) * (tw + 14)
            y = 14 + (i // per) * (th + 34)
            sheet.paste(Image.open(p).resize((tw, th), Image.LANCZOS), (x, y))
            d.text((x, y + th + 6), os.path.basename(p)[:34], font=f,
                   fill=(21, 23, 27))
        sp = os.path.join(a.out, "_contact_sheet.png")
        sheet.save(sp)
        print("Contact sheet: %s" % sp)
    return 0 if not any(w.startswith("ERROR") for _, w in skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
