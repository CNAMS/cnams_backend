"""WHO LMS z-score engine — Python port of cnams_app/lib/core/zscore/.

Mirrors Dart 1:1 for Gate G2 diffing:
  lms.py <-> lmsZScore/lmsValueAtZ (+ WHO |z|>3 rescale)
  reference_tables.py <-> Lms/LmsPoint/LmsTable.at (binary search + lerp, null OOR)
  anthropometry.py <-> 731d boundary + 7mm correction
  classifier.py <-> oedema>whz>muac precedence
  who_lms_engine.py <-> units + flags + MUAC 182..1826 window
  reference_loader.dart <-> parse_reference_tables()

With empty tables every LMS lookup misses -> indeterminate (fail-safe),
except MUAC-mm / oedema classification which needs no table.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field

VERSION = "1.0.0-lms"

_L_EPS = 1e-7
BOUNDARY_DAYS = 731
POSITION_CORRECTION_MM = 7
MUAC_MIN_DAYS = 182
MUAC_MAX_DAYS = 1826
MUAC_SAM_MM = 115
MUAC_MAM_MM = 125


# ---------- LMS math ----------
def lms_value_at_z(z: float, l: float, m: float, s: float) -> float:
    if abs(l) < _L_EPS:
        return m * math.exp(s * z)
    return m * math.pow(1 + l * s * z, 1 / l)


def lms_zscore(y: float, l: float, m: float, s: float) -> float:
    if abs(l) < _L_EPS:
        z = math.log(y / m) / s
    else:
        z = (math.pow(y / m, l) - 1) / (l * s)
    if z > 3:
        sd3 = lms_value_at_z(3, l, m, s)
        sd2 = lms_value_at_z(2, l, m, s)
        return 3 + (y - sd3) / (sd3 - sd2)
    if z < -3:
        sd3 = lms_value_at_z(-3, l, m, s)
        sd2 = lms_value_at_z(-2, l, m, s)
        return -3 + (y - sd3) / (sd2 - sd3)
    return z


# ---------- tables ----------
@dataclass(frozen=True)
class Lms:
    l: float
    m: float
    s: float


@dataclass(frozen=True)
class LmsPoint:
    x: float
    lms: Lms


class LmsTable:
    def __init__(self, indicator: str, sex: str, points: list[LmsPoint]):
        self.indicator = indicator
        self.sex = sex
        self.points = sorted(points, key=lambda p: p.x)

    def at(self, x: float) -> Lms | None:
        pts = self.points
        if not pts or x < pts[0].x or x > pts[-1].x:
            return None
        lo, hi = 0, len(pts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if pts[mid].x <= x:
                lo = mid
            else:
                hi = mid - 1
        left = pts[lo]
        if left.x == x or lo == len(pts) - 1:
            return left.lms
        right = pts[lo + 1]
        t = (x - left.x) / (right.x - left.x)
        return Lms(
            left.lms.l + (right.lms.l - left.lms.l) * t,
            left.lms.m + (right.lms.m - left.lms.m) * t,
            left.lms.s + (right.lms.s - left.lms.s) * t,
        )


class ReferenceTables:
    def __init__(self, tables: list[LmsTable]):
        self._by: dict[tuple[str, str], LmsTable] = {(t.indicator, t.sex): t for t in tables}

    def table(self, indicator: str, sex: str) -> LmsTable | None:
        return self._by.get((indicator, sex))

    @property
    def is_empty(self) -> bool:
        return not self._by


def parse_reference_tables(json_str: str) -> ReferenceTables:
    data = json.loads(json_str)
    raw = data.get("tables") or []
    tables: list[LmsTable] = []
    for t in raw:
        pts = [LmsPoint(float(p["x"]), Lms(float(p["l"]), float(p["m"]), float(p["s"])))
               for p in t["points"]]
        if not pts:
            continue
        tables.append(LmsTable(str(t["indicator"]), str(t["sex"]), pts))
    return ReferenceTables(tables)


# ---------- anthropometry ----------
def is_recumbent_expected(age_days: int) -> bool:
    return age_days < BOUNDARY_DAYS


def corrected_stature_mm(age_days: int, measured_mm: int, measured_recumbent: bool) -> int:
    expect = is_recumbent_expected(age_days)
    if expect and not measured_recumbent:
        return measured_mm + POSITION_CORRECTION_MM
    if not expect and measured_recumbent:
        return measured_mm - POSITION_CORRECTION_MM
    return measured_mm


def weight_for_stature_indicator(age_days: int) -> str:
    return "weightForLength" if is_recumbent_expected(age_days) else "weightForHeight"


# ---------- classifier ----------
def classify(whz: float | None, muac_mm: int | None, oedema: bool) -> str:
    if oedema:
        return "sam"
    saw = sam = mam = overweight = False
    if whz is not None:
        saw = True
        if whz < -3:
            sam = True
        elif whz < -2:
            mam = True
        elif whz > 2:
            overweight = True
    if muac_mm is not None:
        saw = True
        if muac_mm < MUAC_SAM_MM:
            sam = True
        elif muac_mm < MUAC_MAM_MM:
            mam = True
    if not saw:
        return "indeterminate"
    if sam:
        return "sam"
    if mam:
        return "mam"
    if overweight:
        return "overweight"
    return "normal"


# ---------- engine ----------
@dataclass
class ZScoreInput:
    sex: str  # male|female (also accepts M|F)
    age_days: int
    weight_g: int | None = None
    length_mm: int | None = None
    muac_mm: int | None = None
    recumbent: bool | None = None
    oedema: bool = False

    def norm_sex(self) -> str:
        s = self.sex.lower()
        if s in ("m", "male"):
            return "male"
        return "female"


@dataclass
class ZScoreResult:
    waz: float | None
    haz: float | None
    whz: float | None
    maz: float | None
    classification: str
    engine_version: str = VERSION
    flags: list[str] = field(default_factory=list)


class WhoLmsEngine:
    version = VERSION

    def __init__(self, tables: ReferenceTables):
        self.tables = tables

    def compute(self, inp: ZScoreInput) -> ZScoreResult:
        flags: list[str] = []
        sex = inp.norm_sex()
        waz = self._wfa(inp, sex, flags)
        stature = self._stature(inp, flags)
        haz = self._lfa(inp, sex, stature, flags)
        whz = self._wfs(inp, sex, stature, flags)
        maz = self._mfa(inp, sex, flags)
        muac_cls = inp.muac_mm if (inp.muac_mm is not None and MUAC_MIN_DAYS <= inp.age_days < MUAC_MAX_DAYS) else None
        cls = classify(whz, muac_cls, inp.oedema)
        return ZScoreResult(waz, haz, whz, maz, cls, VERSION, flags)

    def _wfa(self, inp: ZScoreInput, sex: str, flags: list[str]) -> float | None:
        if inp.weight_g is None:
            return None
        if inp.weight_g < 500 or inp.weight_g > 40000:
            flags.append("weight_implausible")
        t = self.tables.table("weightForAge", sex)
        lms = t.at(float(inp.age_days)) if t else None
        if lms is None:
            flags.append("waz_out_of_range")
            return None
        return lms_zscore(inp.weight_g / 1000, lms.l, lms.m, lms.s)

    def _stature(self, inp: ZScoreInput, flags: list[str]) -> int | None:
        if inp.length_mm is None:
            return None
        if inp.length_mm < 300 or inp.length_mm > 1300:
            flags.append("length_implausible")
        expect = is_recumbent_expected(inp.age_days)
        measured = inp.recumbent if inp.recumbent is not None else expect
        if inp.recumbent is not None and inp.recumbent != expect:
            flags.append("position_corrected")
        return corrected_stature_mm(inp.age_days, inp.length_mm, measured)

    def _lfa(self, inp: ZScoreInput, sex: str, stature: int | None, flags: list[str]) -> float | None:
        if stature is None:
            return None
        t = self.tables.table("lengthOrHeightForAge", sex)
        lms = t.at(float(inp.age_days)) if t else None
        if lms is None:
            flags.append("haz_out_of_range")
            return None
        return lms_zscore(stature / 10, lms.l, lms.m, lms.s)

    def _wfs(self, inp: ZScoreInput, sex: str, stature: int | None, flags: list[str]) -> float | None:
        if inp.weight_g is None or stature is None:
            return None
        t = self.tables.table(weight_for_stature_indicator(inp.age_days), sex)
        lms = t.at(stature / 10) if t else None
        if lms is None:
            flags.append("whz_out_of_range")
            return None
        return lms_zscore(inp.weight_g / 1000, lms.l, lms.m, lms.s)

    def _mfa(self, inp: ZScoreInput, sex: str, flags: list[str]) -> float | None:
        if inp.muac_mm is None:
            return None
        if inp.muac_mm < 50 or inp.muac_mm > 300:
            flags.append("muac_implausible")
        t = self.tables.table("muacForAge", sex)
        lms = t.at(float(inp.age_days)) if t else None
        if lms is None:
            flags.append("maz_out_of_range")
            return None
        return lms_zscore(inp.muac_mm / 10, lms.l, lms.m, lms.s)


_EMPTY = ReferenceTables([])
_ENGINE_EMPTY = WhoLmsEngine(_EMPTY)


def recompute(payload: dict, child_sex: str = "F") -> dict:
    """Server recompute for sync path. payload carries raw + client_* + position.

    position: recumbent|standing|None. sex from child row (M|F).
    Returns waz/haz/whz/maz/classification/engine_mismatch/engine_version/flags.
    """
    pos = payload.get("position")
    rec = None if pos is None else (str(pos).lower() == "recumbent")
    # Try bundled tables.json if present, else empty (fail-safe indeterminate)
    tables = _load_bundled_tables()
    eng = WhoLmsEngine(tables)
    res = eng.compute(ZScoreInput(
        sex=str(child_sex or "F"), age_days=int(payload.get("age_days", 0)),
        weight_g=payload.get("weight_g"), length_mm=payload.get("length_mm"),
        muac_mm=payload.get("muac_mm"), recumbent=rec, oedema=bool(payload.get("oedema", False)),
    ))
    mismatch = False
    for k, ck in (("waz", "client_waz"), ("haz", "client_haz"), ("whz", "client_whz")):
        sv, cv = getattr(res, k), payload.get(ck)
        if sv is not None and cv is not None and abs(sv - cv) > 0.01:
            mismatch = True
    return {
        "waz": res.waz, "haz": res.haz, "whz": res.whz, "maz": res.maz,
        "classification": res.classification,
        "engine_mismatch": mismatch,
        "engine_version": VERSION,
        "flags": res.flags,
    }


def _load_bundled_tables() -> ReferenceTables:
    import pathlib
    for cand in (
        pathlib.Path(__file__).with_name("reference_tables.json"),
        pathlib.Path("/home/danish1075/Documents/cnams/cnams_app/assets/who_reference/tables.json"),
    ):
        try:
            if cand.exists():
                return parse_reference_tables(cand.read_text())
        except Exception:
            pass
    return _EMPTY
