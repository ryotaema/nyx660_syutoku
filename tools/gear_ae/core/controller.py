"""ギア方式の Color 露光制御（判断ロジック本体。SDK・ROS・YOLO 非依存）。

入力（今フレームの明るさ指標・conf の合計）を受け取り、次の段を返すだけ。
カメラへの設定は呼び出し側（backends）が行う。

判断の優先順位（上ほど優先）:
  1. 段を変えた直後 settle_frames は何もしない（新しい露光の反映待ち）
  2. 「戻す」判定: 段を変える前と後の conf の合計を eval_frames ずつ比べ、
     revert_drop 以上下がっていたら元の段に戻し、その方向を ban_frames の間禁止する。
     → 検出できない範囲へ移動したままにならない。conf を理由に段を「進める」ことはしない。
     変更前の conf の合計が min_eval_score 未満（対象なし）のときは判定しない。
  3. 判定待ちの間は段を動かさない（白飛びによる下げだけは例外）
  4. freeze 中（点群の統合中など）は段を動かさない
  5. 計測不能（空ばかり・全体が白飛び）が invalid_reset_frames 続いたら既定の段に戻す
  6. 白飛び率が clip_down 以上なら待たずに1段下げる
  7. 明るさが target_lum より暗い状態が patience_up 続いたら1段上げ、
     明るい状態が patience_down 続いたら1段下げる（白飛び側を速く）

安全のための性質:
  - 段はギア表の範囲外に出ない。起動時・リセット時は既定の段から始まる。
  - 6 と 5 による変更（白飛び・計測不能）は「戻す」判定の対象にしない（安全側を優先）。
"""

from collections import deque
from dataclasses import dataclass
from typing import Optional

UP, DOWN = 1, -1


@dataclass(frozen=True)
class Decision:
    gear: int
    changed: bool
    reason: str


@dataclass
class _Pending:
    from_gear: int
    direction: int
    score_before: Optional[float]   # None なら「戻す」判定をしない


class GearController:
    def __init__(self, table, start_gear=None):
        self.table = table
        self.reset(start_gear)

    def reset(self, start_gear=None):
        g = self.table.default_gear if start_gear is None else start_gear
        if not 0 <= g <= self.table.last:
            raise ValueError(f'start_gear={g} が範囲外（0..{self.table.last}）')
        self.gear = g
        self.frame = 0
        self._since_change = 0
        self._low = self._high = self._invalid = 0
        self._ban_until = {UP: 0, DOWN: 0}
        self._pending = None
        self._scores = deque(maxlen=self.table.params.eval_frames)

    # ------------------------------------------------------------------
    def step(self, metrics, score=0.0, freeze=False):
        p = self.table.params
        self.frame += 1
        self._since_change += 1

        if self._since_change <= p.settle_frames:
            return self._hold('settling')
        self._scores.append(score)

        if self._pending is not None:
            decision = self._evaluate_pending(metrics)
            if decision is not None:
                return decision
            return self._hold('evaluating')

        if freeze:
            return self._hold('frozen')

        if not metrics.valid:
            self._invalid += 1
            self._low = self._high = 0
            if self._invalid >= p.invalid_reset_frames and self.gear != self.table.default_gear:
                return self._change(self.table.default_gear, 'reset: no valid region', evaluate=False)
            return self._hold('invalid')
        self._invalid = 0

        if metrics.clip_frac >= p.clip_down and self.gear > 0:
            return self._change(self.gear - 1, f'clip {metrics.clip_frac:.3f}', evaluate=False)

        lo, hi = p.target_lum
        if metrics.lum < lo:
            self._low, self._high = self._low + 1, 0
        elif metrics.lum > hi:
            self._low, self._high = 0, self._high + 1
        else:
            self._low = self._high = 0
            return self._hold('in band')

        if self._low >= p.patience_up:
            if self.gear >= self.table.last:
                return self._hold('dark: at top gear')
            if self._banned(UP):
                return self._hold('dark: up banned')
            return self._change(self.gear + 1, f'dark lum {metrics.lum:.0f}')
        if self._high >= p.patience_down:
            if self.gear <= 0:
                return self._hold('bright: at bottom gear')
            if self._banned(DOWN):
                return self._hold('bright: down banned')
            return self._change(self.gear - 1, f'bright lum {metrics.lum:.0f}')
        return self._hold('waiting')

    # ------------------------------------------------------------------
    def _evaluate_pending(self, metrics):
        p = self.table.params
        pend = self._pending
        # 判定待ちの間でも、白飛びだけは待たずに下げる（判定は打ち切る）
        if metrics.valid and metrics.clip_frac >= p.clip_down and self.gear > 0:
            self._pending = None
            return self._change(self.gear - 1, f'clip {metrics.clip_frac:.3f} (during eval)', evaluate=False)
        if len(self._scores) < p.eval_frames:
            return None
        self._pending = None
        after = sum(self._scores) / len(self._scores)
        before = pend.score_before
        if before is None or before < p.min_eval_score:
            return self._hold(f'eval skipped (before={before})')
        if after < before * (1.0 - p.revert_drop):
            self._ban_until[pend.direction] = self.frame + p.ban_frames
            return self._change(pend.from_gear, f'revert: score {before:.2f} -> {after:.2f}', evaluate=False)
        return self._hold(f'eval ok: score {before:.2f} -> {after:.2f}')

    def _banned(self, direction):
        return self.frame < self._ban_until[direction]

    def _change(self, gear, reason, evaluate=True):
        gear = max(0, min(self.table.last, gear))
        if gear == self.gear:
            return self._hold(reason + ' (no-op)')
        direction = UP if gear > self.gear else DOWN
        before = (sum(self._scores) / len(self._scores)) if (evaluate and self._scores) else None
        self._pending = _Pending(from_gear=self.gear, direction=direction, score_before=before) if evaluate else None
        self.gear = gear
        self._since_change = 0
        self._low = self._high = self._invalid = 0
        self._scores.clear()
        return Decision(gear=gear, changed=True, reason=reason)

    def _hold(self, reason):
        return Decision(gear=self.gear, changed=False, reason=reason)
