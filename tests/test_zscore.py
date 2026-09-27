"""C3 — mirrors cnams_app/test/zscore/*.dart. Hand-computed values, not WHO data."""
import math

from app.zscore import (
    Lms,
    LmsPoint,
    LmsTable,
    ReferenceTables,
    WhoLmsEngine,
    ZScoreInput,
    classify,
    corrected_stature_mm,
    is_recumbent_expected,
    lms_value_at_z,
    lms_zscore,
    parse_reference_tables,
    weight_for_stature_indicator,
)


def _close(a, b, tol=1e-9):
    assert abs(a - b) <= tol, f"{a} != {b}"


def test_lms_median_is_zero():
    _close(lms_zscore(10, 1, 10, 0.1), 0)
    _close(lms_zscore(7.5, -0.3, 7.5, 0.12), 0)
    _close(lms_zscore(50, 0, 50, 0.05), 0)


def test_lms_linear():
    _close(lms_zscore(11, 1, 10, 0.1), 1)
    _close(lms_zscore(9, 1, 10, 0.1), -1)
    _close(lms_zscore(12, 1, 10, 0.1), 2)
    _close(lms_zscore(13, 1, 10, 0.1), 3)


def test_lms_log_branch():
    y = 10 * math.exp(0.1)
    _close(lms_zscore(y, 0, 10, 0.1), 1)


def test_lms_extreme_high():
    # SD3=14.28571 SD2=12.5 -> 3+(20-14.28571)/1.78571=6.2
    _close(lms_zscore(20, -1, 10, 0.1), 6.2, 1e-3)


def test_lms_extreme_low():
    _close(lms_zscore(6, -1, 10, 0.1), -5.64, 1e-2)


def test_lms_linear_tail_noop():
    _close(lms_zscore(14, 1, 10, 0.1), 4)
    _close(lms_zscore(6, 1, 10, 0.1), -4)


def test_lms_roundtrip():
    for z in (-2.0, -1.0, 0.0, 1.5, 2.0):
        v = lms_value_at_z(z, -0.3, 8.0, 0.11)
        _close(lms_zscore(v, -0.3, 8.0, 0.11), z)


def test_tables_exact_interp_oor():
    t = LmsTable("weightForAge", "male", [LmsPoint(0, Lms(0.3, 3.3, 0.14)), LmsPoint(30, Lms(0.2, 4.5, 0.13)), LmsPoint(60, Lms(0.1, 5.6, 0.12))])
    assert t.at(30).m == 4.5
    m = t.at(15)
    _close(m.l, 0.25)
    _close(m.m, 3.9)
    _close(m.s, 0.135)
    assert t.at(0).m == 3.3 and t.at(60).m == 5.6
    assert t.at(-1) is None and t.at(61) is None
    rt = ReferenceTables([t])
    assert rt.table("weightForAge", "male") is not None
    assert rt.table("weightForAge", "female") is None


def test_anthropometry():
    assert is_recumbent_expected(730) and not is_recumbent_expected(731)
    assert corrected_stature_mm(365, 743, False) == 750
    assert corrected_stature_mm(800, 750, True) == 743
    assert weight_for_stature_indicator(100) == "weightForLength"
    assert weight_for_stature_indicator(800) == "weightForHeight"


def test_classifier():
    assert classify(0.5, 140, True) == "sam"
    assert classify(-3.1, None, False) == "sam"
    assert classify(-2.5, None, False) == "mam"
    assert classify(0, None, False) == "normal"
    assert classify(2.5, None, False) == "overweight"
    assert classify(None, 110, False) == "sam"
    assert classify(None, 120, False) == "mam"
    assert classify(None, 130, False) == "normal"
    assert classify(0.2, 110, False) == "sam"
    assert classify(2.5, 120, False) == "mam"
    assert classify(None, None, False) == "indeterminate"


def _synthetic():
    return ReferenceTables([
        LmsTable("weightForAge", "male", [LmsPoint(0, Lms(-0.2, 3.3, 0.14)), LmsPoint(365, Lms(-0.2, 10.0, 0.10)), LmsPoint(1826, Lms(-0.2, 18.0, 0.12))]),
        LmsTable("lengthOrHeightForAge", "male", [LmsPoint(0, Lms(1, 50.0, 0.04)), LmsPoint(365, Lms(1, 75.0, 0.04)), LmsPoint(1826, Lms(1, 110.0, 0.04))]),
        LmsTable("weightForLength", "male", [LmsPoint(45.0, Lms(-0.5, 2.5, 0.08)), LmsPoint(75.0, Lms(-0.5, 9.5, 0.08)), LmsPoint(110.0, Lms(-0.5, 18.0, 0.08))]),
        LmsTable("muacForAge", "male", [LmsPoint(182, Lms(1, 14.0, 0.08)), LmsPoint(365, Lms(1, 15.0, 0.08)), LmsPoint(1826, Lms(1, 16.0, 0.08))]),
    ])


def test_engine_median_normal():
    r = WhoLmsEngine(_synthetic()).compute(ZScoreInput(sex="male", age_days=365, weight_g=10000, length_mm=750, muac_mm=150, recumbent=True))
    _close(r.waz, 0)
    _close(r.haz, 0)
    _close(r.maz, 0)
    _close(r.whz, 0.633, 1e-3)
    assert r.classification == "normal" and r.flags == [] and r.engine_version == "1.0.0-lms"


def test_engine_sam_tail():
    r = WhoLmsEngine(_synthetic()).compute(ZScoreInput(sex="male", age_days=365, weight_g=7000, length_mm=750, muac_mm=150, recumbent=True))
    _close(r.whz, -4.003, 1e-2)
    assert r.classification == "sam"


def test_engine_muac_oedema_position_indeterminate_oor_implausible():
    e = WhoLmsEngine(_synthetic())
    assert e.compute(ZScoreInput(sex="male", age_days=365, weight_g=10000, length_mm=750, muac_mm=110, recumbent=True)).classification == "sam"
    assert e.compute(ZScoreInput(sex="male", age_days=365, weight_g=10000, length_mm=750, oedema=True)).classification == "sam"
    r = e.compute(ZScoreInput(sex="male", age_days=365, weight_g=10000, length_mm=743, recumbent=False))
    assert "position_corrected" in r.flags
    _close(r.haz, 0)
    r = e.compute(ZScoreInput(sex="male", age_days=365))
    assert r.waz is None and r.classification == "indeterminate"
    r = e.compute(ZScoreInput(sex="male", age_days=5000, weight_g=12000))
    assert r.waz is None and "waz_out_of_range" in r.flags
    r = e.compute(ZScoreInput(sex="male", age_days=365, weight_g=100))
    assert "weight_implausible" in r.flags and r.waz is not None


def test_empty_tables_failsafe():
    r = WhoLmsEngine(ReferenceTables([])).compute(ZScoreInput(sex="F", age_days=400, weight_g=9000, length_mm=720))
    assert r.waz is None and r.haz is None and r.whz is None
    assert r.classification == "indeterminate"
    assert "waz_out_of_range" in r.flags


def test_loader_sorts_and_parses():
    rt = parse_reference_tables('{"tables": [{"indicator": "weightForAge", "sex": "male", "points": [{"x": 30, "l": 0.2, "m": 4.5, "s": 0.13}, {"x": 0, "l": 0.3, "m": 3.3, "s": 0.14}]}]}')
    assert rt.table("weightForAge", "male").at(15).m == 3.9
    assert parse_reference_tables('{"source":"x","tables":[]}').is_empty


def test_mismatch_threshold():
    from app.zscore import recompute
    # empty tables -> server None, never mismatch even with client values
    z = recompute({"age_days": 400, "weight_g": 9000, "client_waz": 5.0}, "F")
    assert z["engine_mismatch"] is False
