"""core（metrics / tracker / gear_table / controller）のテスト。実機・YOLO 不要。

実行: python3 -m pytest tools/gear_ae/tests
"""

import random
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from core.controller import GearController  # noqa: E402
from core.gear_table import DEFAULT_PARAMS, Gear, GearTable, find_gear_table, validate  # noqa: E402
from core.metrics import FrameMetrics, measure  # noqa: E402
from core.tracker import Detection, PersistenceTracker, score  # noqa: E402


def make_table(n=5, default=2, **params):
    gears = tuple(Gear(exposure=500 * (i + 1), gain=1.0) for i in range(n))
    return GearTable(model='TEST', env='test', gears=gears, default_gear=default,
                     params=replace(DEFAULT_PARAMS, **params))


def lum_of(gear):
    """段ごとに 30 ずつ明るくなる仮想シーン（段2 = 135 だけが目標の帯 115〜150 の中）。"""
    return 75.0 + 30.0 * gear


def metrics(lum, clip=0.0):
    return FrameMetrics(lum=lum, clip_frac=clip, valid_frac=0.8, sky_frac=0.2)


INVALID = FrameMetrics(lum=None, clip_frac=1.0, valid_frac=0.0, sky_frac=1.0)


def run(ctl, n, metric_fn, score_fn=lambda g: 0.0):
    history = []
    for _ in range(n):
        g = ctl.gear
        ctl.step(metric_fn(g), score_fn(g))
        history.append(ctl.gear)
    return history


# ---------------------------------------------------------------- metrics
def test_measure_excludes_sky():
    img = np.zeros((100, 100, 3), np.uint8)
    img[:50] = (230, 228, 226)          # 上半分: 空（明るく色が薄い）
    img[50:] = (40, 140, 60)            # 下半分: 植物（緑）
    m = measure(img)
    assert m.valid and abs(m.sky_frac - 0.5) < 0.02
    assert 80 < m.lum < 110             # 空の明るさに引っ張られない


def test_measure_invalid_when_only_sky():
    img = np.full((100, 100, 3), (235, 230, 225), np.uint8)
    assert not measure(img).valid


# ---------------------------------------------------------------- tracker
def test_tracker_ignores_one_frame_false_positive():
    t = PersistenceTracker(min_hits=3)
    real = Detection(box=(10, 10, 50, 50), conf=0.8)
    confirmed = []
    for i in range(5):
        dets = [real] + ([Detection(box=(200 + 40 * i, 200, 240 + 40 * i, 240), conf=0.7)])
        confirmed = t.update(dets)
    assert score(confirmed) == pytest.approx(0.8)


# ---------------------------------------------------------------- gear_table
def test_validate_rejects_wrong_order():
    t = make_table()
    bad = replace(t, gears=(Gear(1000, 1.0), Gear(500, 1.0)), default_gear=0)
    with pytest.raises(ValueError):
        validate(bad)


@pytest.mark.parametrize('model', ['NYX660', 'DS86'])
def test_shipped_gear_tables_load(model):
    t = find_gear_table(HERE / 'gears', model, 'outdoor_day')
    assert t.model == model and 0 <= t.default_gear <= t.last


# ---------------------------------------------------------------- controller
@pytest.mark.parametrize('start', range(5))
def test_converges_into_band_from_any_start(start):
    ctl = GearController(make_table(), start_gear=start)
    hist = run(ctl, 80, lambda g: metrics(lum_of(g)))
    assert hist[-1] == 2 and len(set(hist[-20:])) == 1


def test_never_leaves_table_range():
    ctl = GearController(make_table())
    rng = random.Random(0)
    hist = run(ctl, 500, lambda g: metrics(rng.choice([0.0, 255.0]), clip=rng.choice([0.0, 0.5])),
               lambda g: rng.uniform(0, 5))
    assert min(hist) >= 0 and max(hist) <= 4


def test_no_target_at_start_holds_then_resets_to_default():
    ctl = GearController(make_table(invalid_reset_frames=10), start_gear=4)
    hist = run(ctl, 30, lambda g: INVALID)
    assert hist[-1] == 2                 # 計測不能が続いたら既定の段へ
    assert all(h in (4, 2) for h in hist)  # それ以外には動かない


def test_clip_steps_down_immediately():
    ctl = GearController(make_table(), start_gear=4)
    for _ in range(ctl.table.params.settle_frames):   # 起動直後は反映待ちで判断しない
        assert not ctl.step(metrics(140)).changed
    d = ctl.step(metrics(140, clip=0.2))
    assert d.changed and d.gear == 3


def test_reverts_when_detection_drops_and_bans_direction():
    # 段2: lum 110（少し暗い）で検出あり、段3: 目標の帯の中だが検出が消える
    table = make_table(target_lum=(115.0, 150.0), ban_frames=50)
    ctl = GearController(table, start_gear=2)
    lum = {2: 110.0, 3: 135.0}
    sc = {2: 3.0, 3: 0.5}
    hist = run(ctl, 40, lambda g: metrics(lum.get(g, 135.0)), lambda g: sc.get(g, 0.5))
    assert 3 in hist                     # 一度は上げる
    assert hist[-1] == 2                 # 検出が落ちたので戻す
    assert hist[hist.index(3):].count(3) <= table.params.settle_frames + table.params.eval_frames + 1


def test_false_positives_do_not_push_gear():
    # conf の合計は「戻す」判定にしか使わないので、誤検出が多くても段は明るさだけで決まる
    ctl = GearController(make_table(), start_gear=2)
    rng = random.Random(1)
    hist = run(ctl, 100, lambda g: metrics(lum_of(g)), lambda g: rng.uniform(0, 10))
    assert set(hist) == {2}
