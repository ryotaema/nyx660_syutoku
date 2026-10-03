"""数フレーム続けて同じ位置に出た検出だけを数える簡易トラッカー（SDK・YOLO非依存）。

1フレームだけ出た誤検出に、露光の判断（conf の合計による「戻す」判定）が引っ張られないようにする。
対応付けは IoU の貪欲法。ロボットの頭部カメラは撮影中ほぼ静止している前提で、
動きの予測はしない。
"""

from dataclasses import dataclass, field
from typing import List, Tuple


@dataclass(frozen=True)
class Detection:
    box: Tuple[float, float, float, float]  # x1, y1, x2, y2（画素）
    conf: float


def iou(a, b):
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


@dataclass
class _Track:
    box: Tuple[float, float, float, float]
    hits: int = 1
    misses: int = 0


@dataclass
class PersistenceTracker:
    iou_thr: float = 0.3   # 同じ対象とみなす枠の重なり
    min_hits: int = 3      # この回数以上連続して（途切れは max_misses まで許容）出たら確定
    max_misses: int = 2    # 見失ってもこのフレーム数までは追跡を残す
    _tracks: List[_Track] = field(default_factory=list)

    def reset(self):
        self._tracks = []

    def update(self, detections):
        """今フレームの検出を受け取り、確定した追跡に対応する検出だけを返す。"""
        unmatched = list(range(len(self._tracks)))
        confirmed = []
        for det in sorted(detections, key=lambda d: d.conf, reverse=True):
            best, best_iou = None, self.iou_thr
            for i in unmatched:
                v = iou(self._tracks[i].box, det.box)
                if v >= best_iou:
                    best, best_iou = i, v
            if best is None:
                self._tracks.append(_Track(box=det.box))
                continue
            unmatched.remove(best)
            t = self._tracks[best]
            t.box, t.hits, t.misses = det.box, t.hits + 1, 0
            if t.hits >= self.min_hits:
                confirmed.append(det)
        for i in unmatched:
            self._tracks[i].misses += 1
        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]
        return confirmed


def score(confirmed):
    """露光の良し悪しの判定に使う値: 確定した検出の conf の合計（≒ 検出数 × 平均conf）。

    平均 conf は使わない。極端に暗い/白飛びした画像では最も目立つ果実1個だけが残り、
    平均が高く出るため（2026_0930 のスイープで確認）。
    """
    return float(sum(d.conf for d in confirmed))
