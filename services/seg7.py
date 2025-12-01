# seg7.py
# Fresh 7-seg reader with two interchangeable methods:
#   Method 1 (ratio):  read_lcd_roi(...)
#   Method 2 (ssocr):  ssocr_read_digits(...)
#
# No external binaries required. Keeps the same public signatures you requested.

from __future__ import annotations
from typing import List, Tuple, Optional, Dict
import numpy as np
import cv2

try:
    # Flask optional (for logging)
    from flask import current_app
except Exception:  # pragma: no cover
    current_app = None  # type: ignore

# ----------------------------
# Tunables / module constants
# ----------------------------

# Segment decision weights for scoring a candidate digit pattern
W_TP: float = 1.0   # reward for segment ON when it should be ON
W_FP: float = 0.6   # penalty for segment ON when it should be OFF
W_FN: float = 0.9   # penalty for segment OFF when it should be ON

# Default segment "on" threshold (raised to suppress glow)
DEFAULT_SEG_THR: float = 0.62

# Minimum lit fraction inside a digit tile to consider it "not blank"
TILE_MIN_LIT: float = 0.06  # >>> was 0.06 in your paste; keep

# If ROI height is small, upscale for stability
UPSCALE_MIN_H: int = 40
UPSCALE_FX: float = 3.0
UPSCALE_FY: float = 3.0

# Edge trim to remove bezel/glow (fraction of width on each side)
EDGE_TRIM_FRAC: float = 0.02  # >>> a bit less trimming than 0.04

# Red color gate HSV bands (handles wrap-around of hue near 0/180)
# Assumes BGR input
HSV_RED1 = ((0, 80, 60), (10, 255, 255))
HSV_RED2 = ((170, 80, 60), (180, 255, 255))

# "Weak-8" suppression: how many segments ON before we believe a digit
WEAK8_MIN_ON: int = 3

# --------------------------------
# Small logging helper
# --------------------------------
def _log(fmt: str, *args) -> None:
    try:
        if current_app:
            current_app.logger.info(fmt, *args)
    except Exception:
        pass

# --------------------------------
# Basic utilities
# --------------------------------
def _upscale_if_needed(img: np.ndarray) -> np.ndarray:
    h = img.shape[0]
    if h < UPSCALE_MIN_H:
        img = cv2.resize(img, (0, 0), fx=UPSCALE_FX, fy=UPSCALE_FY, interpolation=cv2.INTER_CUBIC)
    return img

def _trim_edges(bw: np.ndarray, frac: float) -> np.ndarray:
    h, w = bw.shape[:2]
    pad = int(round(frac * w))
    if pad <= 0 or pad * 2 >= w:
        return bw
    return bw[:, pad:w - pad]

def _to_gray(img_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

def _normalize_u8(g: np.ndarray) -> np.ndarray:
    g = cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX)
    return g.astype(np.uint8, copy=False)

def _otsu_or_adapt(g: np.ndarray, invert: bool = False) -> np.ndarray:
    # Try Otsu; if OTSU flag is missing, fallback to median threshold
    flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    try:
        _ = cv2.OTSU  # type: ignore
        _, bw = cv2.threshold(g, 0, 255, flag | cv2.THRESH_OTSU)
        return bw
    except Exception:
        thr = int(np.median(g))
        _, bw = cv2.threshold(g, thr, 255, flag)
        return bw

def _percentile_thresh(g: np.ndarray, p: float, invert: bool = False) -> np.ndarray:
    thr = int(np.percentile(g, p))
    typ = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
    _, bw = cv2.threshold(g, thr, 255, typ)
    return bw

# --------------------------------
# Color gating (red)
# --------------------------------
def _red_mask(bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    lower1 = np.array(HSV_RED1[0], dtype=np.uint8)
    upper1 = np.array(HSV_RED1[1], dtype=np.uint8)
    lower2 = np.array(HSV_RED2[0], dtype=np.uint8)
    upper2 = np.array(HSV_RED2[1], dtype=np.uint8)
    m1 = cv2.inRange(hsv, lower1, upper1)
    m2 = cv2.inRange(hsv, lower2, upper2)
    mask = cv2.bitwise_or(m1, m2)
    # light open to reduce salt; then dilate a touch to bridge splits
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8), iterations=1)
    mask = cv2.dilate(mask, np.ones((2, 2), np.uint8), iterations=1)
    return mask  # 0/255

def _apply_gate(gray: np.ndarray, mask255: np.ndarray) -> np.ndarray:
    # Keep gray only where red mask is present; elsewhere dark
    gated = cv2.bitwise_and(gray, mask255)
    return gated

# --------------------------------
# NEW: robust ROI → binary preprocessor (used by both methods)
# --------------------------------
def _preprocess_roi_to_bw(
    roi_bgr: np.ndarray,
    use_red: bool,
    invert: bool = False,
) -> np.ndarray:
    """
    ROI -> upscale ×3 -> CLAHE (LAB L-channel) -> (optional) red gate ->
    Otsu (fallback: adaptive) -> morphology close→open -> return white-on-black (unless invert=True)
    """
    if roi_bgr is None or roi_bgr.size == 0:
        return np.zeros((0, 0), np.uint8)

    # always upscale small ROIs to make segments chunkier/stable
    h = roi_bgr.shape[0]
    if h < 3 * UPSCALE_MIN_H:
        roi_bgr = cv2.resize(roi_bgr, (0, 0), fx=UPSCALE_FX, fy=UPSCALE_FY, interpolation=cv2.INTER_CUBIC)

    # CLAHE on L
    lab = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2LAB)
    L, A, B = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    L = clahe.apply(L)
    roi_eq = cv2.cvtColor(cv2.merge((L, A, B)), cv2.COLOR_LAB2BGR)

    gray = _to_gray(roi_eq)
    if use_red:
        mask = _red_mask(roi_eq)
        gray = _apply_gate(gray, mask)

    # primary Otsu, fallback adaptive when too dark
    bw = _otsu_or_adapt(gray, invert=False)
    lit = float((bw == 255).mean())
    if lit < 0.01:
        bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 11, 2)

    if invert:
        bw = cv2.bitwise_not(bw)

    # stabilize segments: close then gentle open
    if min(bw.shape[:2]) >= 8:
        k = np.ones((2, 2), np.uint8)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN,  k, iterations=1)

    return bw

# --------------------------------
# Tile split and blank checks
# --------------------------------
def _split_tiles(img: np.ndarray, digits: int) -> List[np.ndarray]:
    h, w = img.shape[:2]
    if digits <= 1:
        return [img]
    # Gentle trim of edges to avoid bezel/glow
    img = _trim_edges(img, EDGE_TRIM_FRAC)
    h, w = img.shape[:2]
    tile_w = max(1, w // digits)
    tiles = []
    for i in range(digits):
        x1 = i * tile_w
        x2 = w if i == digits - 1 else (i + 1) * tile_w
        tiles.append(img[:, x1:x2])
    return tiles

def _lit_fraction(bw255: np.ndarray) -> float:
    if bw255.size == 0:
        return 0.0
    return float((bw255 == 255).mean())

def _weak8_suppression(on: List[int]) -> bool:
    # Return True if the pattern is "too weak" to be trusted as a digit (treat as blank)
    return sum(on) < WEAK8_MIN_ON

# --------------------------------
# Digit segment model
# --------------------------------
# Segment ordering: [a, b, c, d, e, f, g]
# Boxes are expressed as fractions of the tile width/height
# Layout: a (top), b (top-right), c (bottom-right), d (bottom),
#         e (bottom-left), f (top-left), g (middle)

_SEG_BOXES = [
    #   x     y     w     h
    (0.18, 0.04, 0.64, 0.18),  # a
    (0.74, 0.18, 0.18, 0.36),  # b
    (0.74, 0.54, 0.18, 0.36),  # c
    (0.18, 0.78, 0.64, 0.18),  # d
    (0.08, 0.54, 0.18, 0.36),  # e
    (0.08, 0.18, 0.18, 0.36),  # f
    (0.18, 0.46, 0.64, 0.18),  # g
]

# Canonical on/off patterns for digits 0-9 in [a,b,c,d,e,f,g] order
_DIGIT_PATTERNS: Dict[int, Tuple[int, ...]] = {
    0: (1, 1, 1, 1, 1, 1, 0),
    1: (0, 1, 1, 0, 0, 0, 0),
    2: (1, 1, 0, 1, 1, 0, 1),
    3: (1, 1, 1, 1, 0, 0, 1),
    4: (0, 1, 1, 0, 0, 1, 1),
    5: (1, 0, 1, 1, 0, 1, 1),
    6: (1, 0, 1, 1, 1, 1, 1),
    7: (1, 1, 1, 0, 0, 0, 0),
    8: (1, 1, 1, 1, 1, 1, 1),
    9: (1, 1, 1, 1, 0, 1, 1),
}

# --------------------------------
# Segment ratio extraction + decoding
# --------------------------------
def _segment_ratios(tile_bw: np.ndarray) -> List[float]:
    # tile_bw is 0/255 (white=ON)
    h, w = tile_bw.shape[:2]
    ratios: List[float] = []
    for rx, ry, rw, rh in _SEG_BOXES:
        x1 = int(round(rx * w))
        y1 = int(round(ry * h))
        x2 = int(round((rx + rw) * w))
        y2 = int(round((ry + rh) * h))
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(x1 + 1, min(w, x2))
        y2 = max(y1 + 1, min(h, y2))
        roi = tile_bw[y1:y2, x1:x2]
        if roi.size == 0:
            ratios.append(0.0)
        else:
            ratios.append(float((roi == 255).mean()))
    return ratios

def _score_pattern(on: List[int], pat: Tuple[int, ...]) -> float:
    # Higher is better
    score = 0.0
    for o, p in zip(on, pat):
        if o == 1 and p == 1:
            score += W_TP
        elif o == 1 and p == 0:
            score -= W_FP
        elif o == 0 and p == 1:
            score -= W_FN
        else:
            # o=0, p=0 -> small reward for matching blank
            score += 0.1
    return score

def _pick_digit(ratios: List[float], thr: float) -> Tuple[int, float, List[int]]:
    on = [1 if r >= thr else 0 for r in ratios]
    # Weak-8 suppression: if the pattern is too weak, treat as blank
    if _weak8_suppression(on):
        return -1, 0.5, on  # blank

    best_d = 8
    best_s = -1e9
    for d, pat in _DIGIT_PATTERNS.items():
        s = _score_pattern(on, pat)
        if s > best_s:
            best_s = s
            best_d = d

    # Convert score to [0..1]-ish confidence by normalizing by ideal (7*W_TP)
    max_ideal = 7.0 * W_TP
    conf = max(0.0, min(1.0, (best_s + max_ideal) / (2.0 * max_ideal)))
    return best_d, conf, on

# --------------------------------
# Public Method 1: Ratio decoder
# --------------------------------
def read_lcd_roi(
    roi_bgr: np.ndarray,
    digits: int,
    hint: Optional[str],
    seg_thr: Optional[float] = None
) -> Tuple[str, List[float]]:
    """
    Method 1 (ratio):
      1) Strong preprocessing (upscale×3, CLAHE, red-gate if hinted, Otsu/Adaptive, close→open)
      2) Split tiles, blank via lit-fraction + weak-8
      3) Segment ratios + weighted pattern score
    Returns (text, per_digit_conf)
    """
    if roi_bgr is None or roi_bgr.size == 0:
        return "", []

    use_red = (hint or "red").lower() == "red"  # default red for your panel
    bw = _preprocess_roi_to_bw(roi_bgr, use_red=use_red, invert=False)  # >>> unified path
    tiles = _split_tiles(bw, max(1, int(digits)))

    out: List[str] = []
    confs: List[float] = []
    thr = float(seg_thr) if seg_thr is not None else DEFAULT_SEG_THR

    for ti, tbw in enumerate(tiles, start=1):
        lit = _lit_fraction(tbw)
        if lit < TILE_MIN_LIT:
            out.append(" ")
            confs.append(0.5)
            _log("[seg7] tile%d blank (lit=%.3f<TILE_MIN_LIT)", ti, lit)
            continue

        ratios = _segment_ratios(tbw)
        d, c, on = _pick_digit(ratios, thr)
        if d < 0:
            out.append(" ")
            confs.append(0.5)
            _log("[seg7] tile%d weak-8 (on=%d) -> blank", ti, sum(on))
            continue

        out.append(str(d))
        confs.append(c)
        _log("[seg7] tile%d lit=%.3f ratios=%s on=%s thr=%.2f -> %d(%.3f)",
             ti,
             lit,
             [round(r, 3) for r in ratios],
             on, thr, d, c)

    text = "".join(out)
    _log("[seg7] ratio result -> '%s'", text)
    return text, confs

# --------------------------------
# Public Method 2: ssocr-style (pure Python)
# --------------------------------
def ssocr_read_digits(
    roi_bgr: np.ndarray,
    digits: int,
    invert: bool = False,
    threshold: str | int = "otsu",
    whitelist: Optional[str] = None
) -> Tuple[str, Dict]:
    """
    Method 2 (ssocr-style):
      - Build robust binary variants via the same preprocessor (strong=red-gated, soft=gray)
      - Try both polarities
      - Decode and SCORE all candidates; pick the best (penalize 'mostly zeros')
    Returns (text, meta) where meta includes per-digit confs and the chosen variant.
    """
    meta = {"ok": False, "pass": 0, "variant": "", "invert": bool(invert), "thr": threshold, "confs": []}

    if roi_bgr is None or roi_bgr.size == 0:
        return "", meta

    # Build binary variants consistently with Method 1
    strong_bw = _preprocess_roi_to_bw(roi_bgr, use_red=True,  invert=False)
    soft_bw   = _preprocess_roi_to_bw(roi_bgr, use_red=False, invert=False)

    variants = [
        ("soft",   False, soft_bw),
        ("strong", False, strong_bw),
        ("soft",   True,  soft_bw),
        ("strong", True,  strong_bw),
    ]

    candidates = []  # (score_tuple, idx, vname, inv, text, confs)

    for idx, (vname, inv_used, bw0) in enumerate(variants, start=1):
        bw = cv2.bitwise_not(bw0) if inv_used else bw0
        bw = _trim_edges(bw, EDGE_TRIM_FRAC)

        tiles = _split_tiles(bw, max(1, int(digits)))
        text_chars: List[str] = []
        confs: List[float] = []

        _log("[seg7] ssocr pass%d %s inv=%s thr=preproc", idx, vname, inv_used)

        for ti, tbw in enumerate(tiles, start=1):
            lit = _lit_fraction(tbw)
            if lit < TILE_MIN_LIT:
                text_chars.append(" ")
                confs.append(0.5)
                _log("[seg7] tile%d all-off -> blank", ti)
                continue

            ratios = _segment_ratios(tbw)
            # Use a slightly more permissive threshold for ssocr path internally
            d, c, on = _pick_digit(ratios, thr=0.60)  # >>> softer than 0.62
            if d < 0 or (whitelist and str(d) not in whitelist):
                text_chars.append(" ")
                confs.append(0.5)
            else:
                text_chars.append(str(d))
                confs.append(c)

        text = "".join(text_chars)

        # quality metrics
        conf_np = np.array(confs, dtype=np.float32) if confs else np.zeros((0,), np.float32)
        num_solid = int((conf_np >= 0.6).sum())
        avg_conf  = float(conf_np.mean()) if confs else 0.0
        num_digits = sum(1 for ch in text if ch.isdigit())

        # penalize zero/eight-heavy reads unless they're very confident
        num_zero8 = text.count('0') + text.count('8')
        penalty = 0.0
        if num_zero8 >= 2 and avg_conf < 0.82:
            penalty += 0.5 * (num_zero8 - 1)

        #mostly_zeros = (text.count('0') >= max(3, digits - 1))
        #penalty = 1 if (mostly_zeros and avg_conf < 0.75) else 0

        score = (num_solid - penalty, avg_conf, num_digits)  # lexicographic: higher is better

        _log("[seg7] ssocr %s result -> '%s' confs=%s score=%s",
             vname, text, [round(x, 3) for x in confs],
             tuple(round(x,3) if isinstance(x,float) else x for x in score))

        candidates.append((score, idx, vname, inv_used, text, confs))

    if candidates:
        candidates.sort(reverse=True, key=lambda t: t[0])
        (_, idx, vname, inv_used, text, confs) = candidates[0]
        meta.update({
            "ok": True,
            "pass": idx,
            "variant": vname,
            "invert": inv_used,
            "thr": "preproc",
            "confs": confs,
        })
        _log("[seg7] ssocr picked pass%d %s inv=%s -> '%s'", idx, vname, inv_used, text)
        return text, meta

    meta["reason"] = "no_hit"
    return " " * max(1, int(digits)), meta
