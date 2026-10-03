"""ギア表（機種 × 環境ごとの、Color 露光・ゲインの段の一覧）の読み込みと検証。

ファイルは gears/<model>_<env>.yaml。段は暗い → 明るい の順に並べる。
判断の基準値（params）もギア表に持たせ、環境ごとに調整できるようにする。
params で省略した項目は DEFAULT_PARAMS の値を使う。
"""

from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Tuple

import yaml


@dataclass(frozen=True)
class Gear:
    exposure: int   # Color 露光時間（us）
    gain: float     # Color ゲイン


@dataclass(frozen=True)
class ControllerParams:
    target_lum: Tuple[float, float] = (115.0, 150.0)  # この帯に入っていれば段を動かさない
    clip_down: float = 0.06        # 白飛び率がこれ以上なら待たずに1段下げる
    patience_up: int = 5           # 暗い状態がこのフレーム数続いたら1段上げる
    patience_down: int = 3         # 明るい状態がこのフレーム数続いたら1段下げる（白飛び側を速く）
    settle_frames: int = 2         # 段を変えた直後、新しい露光が反映されるまで判断しないフレーム数
    eval_frames: int = 3           # 段を変えた前後で conf の合計を比べるフレーム数
    revert_drop: float = 0.3       # conf の合計がこの割合以上下がったら元の段に戻す
    min_eval_score: float = 1.0    # 変更前の conf の合計がこれ未満なら「戻す」判定をしない（対象なし扱い）
    ban_frames: int = 60           # 戻した後、同じ方向へ動かさないフレーム数
    invalid_reset_frames: int = 10 # 計測不能がこのフレーム数続いたら既定の段に戻す
    min_valid_frac: float = 0.05   # 空以外の画素がこの割合未満なら計測不能


DEFAULT_PARAMS = ControllerParams()


@dataclass(frozen=True)
class GearTable:
    model: str
    env: str
    gears: Tuple[Gear, ...]
    default_gear: int
    params: ControllerParams
    source: str = ''

    @property
    def last(self):
        return len(self.gears) - 1


def load_gear_table(path):
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)
    gears = tuple(Gear(exposure=int(g['exposure']), gain=float(g['gain'])) for g in raw['gears'])
    known = {f.name for f in fields(ControllerParams)}
    overrides = raw.get('params') or {}
    unknown = set(overrides) - known
    if unknown:
        raise ValueError(f'{path}: 不明な params: {sorted(unknown)}')
    if 'target_lum' in overrides:
        overrides['target_lum'] = tuple(float(v) for v in overrides['target_lum'])
    table = GearTable(model=str(raw['model']), env=str(raw['env']), gears=gears,
                      default_gear=int(raw['default_gear']),
                      params=replace(DEFAULT_PARAMS, **overrides), source=str(path))
    validate(table)
    return table


def validate(table):
    where = table.source or f'{table.model}_{table.env}'
    if not table.gears:
        raise ValueError(f'{where}: gears が空')
    if not 0 <= table.default_gear < len(table.gears):
        raise ValueError(f'{where}: default_gear={table.default_gear} が範囲外（0..{table.last}）')
    for i, g in enumerate(table.gears):
        if g.exposure <= 0 or g.gain <= 0:
            raise ValueError(f'{where}: gears[{i}] の exposure/gain は正の値にすること: {g}')
    # 暗い → 明るい の順を最低限確認する（露光・ゲインとも下がる段は並び違い）
    for i in range(1, len(table.gears)):
        a, b = table.gears[i - 1], table.gears[i]
        if b.exposure < a.exposure and b.gain <= a.gain or b.exposure <= a.exposure and b.gain < a.gain:
            raise ValueError(f'{where}: gears[{i - 1}]→[{i}] が暗くなる並び: {a} → {b}')
    lo, hi = table.params.target_lum
    if not lo < hi:
        raise ValueError(f'{where}: target_lum は [下限, 上限] で下限 < 上限: {table.params.target_lum}')


def find_gear_table(gears_dir, model, env):
    path = Path(gears_dir) / f'{model}_{env}.yaml'
    if not path.exists():
        have = sorted(p.stem for p in Path(gears_dir).glob('*.yaml'))
        raise FileNotFoundError(f'ギア表がありません: {path}（あるもの: {have}）')
    return load_gear_table(path)
