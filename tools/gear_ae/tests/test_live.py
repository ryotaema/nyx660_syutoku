"""run_live.py の制御ループと反映遅れ解析のテスト。実機の代わりに仮想カメラを使う（実機・YOLO 不要）。

仮想カメラ: 段の露光に比例して明るくなる緑一色の画像を返し、露光の変更は DELAY 回読んだ後に反映される。
"""

import sys
from argparse import Namespace
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import run_live  # noqa: E402
from core.gear_table import find_gear_table  # noqa: E402
from core.metrics import measure  # noqa: E402

DELAY = 2


class FakeBackend:
    def __init__(self, lum_per_us=0.055):
        self.lum_per_us = lum_per_us
        self.max_exposure = 10000
        self.last_frame = {}
        self._queue = []       # 反映待ちの露光（読み取り回数, 露光）
        self._exposure = 1000
        self._reads = 0

    def set_gear(self, gear):
        self._queue.append((self._reads + DELAY, gear.exposure * gear.gain ** 0.1))
        return dict(req_exposure=gear.exposure, req_gain=gear.gain, set_exposure=gear.exposure,
                    rb_exposure=gear.exposure, rb_gain=gear.gain, set_ms=0.0,
                    after_frame=self.last_frame.get('frame_index'))

    def read_frame(self):
        while self._queue and self._queue[0][0] <= self._reads:
            self._exposure = self._queue.pop(0)[1]
        self._reads += 1
        self.last_frame = dict(frame_index=1000 + self._reads, hw_ts=self._reads * 33, wall=0.0)
        v = int(min(255, self._exposure * self.lum_per_us))
        img = np.zeros((120, 160, 3), np.uint8)
        img[..., 1] = v                          # 緑（空とは判定されない）
        img[..., 0] = img[..., 2] = v // 3
        return img, f'live_{self._reads}'


def args(**kw):
    base = dict(no_yolo=True, yolo_model=None, conf=None, start_gear=0, frames=80, detect_every=1,
                save_every=0, latency_gears=None, latency_period=15, latency_cycles=3)
    base.update(kw)
    return Namespace(**base)


def test_control_loop_converges_with_delay(tmp_path):
    table = find_gear_table(HERE / 'gears', 'NYX660', 'outdoor_day')
    rows, events, _ = run_live.run_control(args(), {}, FakeBackend(), table, tmp_path)
    assert len(rows) == 80
    lo, hi = table.params.target_lum
    final = [r['lum'] for r in rows[-10:]]
    assert all(lo <= v <= hi for v in final), final
    # settle_frames >= DELAY なので、反映前の古い画像を見て行き過ぎない（上げ続けるだけで下げない）
    assert all(e['to_gear'] > (e['from_gear'] or -1) for e in events)


def test_latency_test_measures_delay(tmp_path):
    table = find_gear_table(HERE / 'gears', 'NYX660', 'outdoor_day')
    rows, events, extra = run_live.run_latency(args(latency_gears='1,4'), {}, FakeBackend(), table, tmp_path)
    lat = extra['latency']
    assert lat['delay_reads_max'] == DELAY and lat['delay_reads_median'] == DELAY
    assert len(lat['switches']) == 3 * 2 - 1


def test_fake_image_is_measurable():
    img, _ = FakeBackend().read_frame()
    assert measure(img).valid
