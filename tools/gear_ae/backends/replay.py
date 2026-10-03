"""オフライン再現用の設定先: param_sweep.py の Color スイープ（color_exposure × color_gain）から、
指定された段（露光・ゲイン）で撮った画像を返す。

段の露光・ゲインと完全に一致する組合せがスイープに無い場合はエラーにする（近い画像で
ごまかすと再現結果が実機とずれるため）。ギア表はスイープに含まれる組合せで組むこと。
"""

import json
from pathlib import Path

import cv2

# 2026_0930 のメタデータは機種名が 'NYX660' 固定で記録されていたため、ToF 内部パラメータの fx で判別する
# （../log_nyx/2026-09-30.md）
_FX_TO_MODEL = ((479.0, 'DS86'), (489.1, 'NYX660'))


def _gain_key(g):
    return round(float(g), 3)


class ReplayBackend:
    def __init__(self, session_dir):
        self.session_dir = Path(session_dir)
        meta = json.loads((self.session_dir / 'metadata.json').read_text())
        params = [w['param'] for w in meta['sweeps']]
        if set(params) != {'color_exposure', 'color_gain'}:
            raise ValueError(f'Color スイープ（color_exposure × color_gain）ではありません: {params}')
        self.meta = meta
        self._paths = {}
        for r in meta['results']:
            if not r.get('saved'):
                continue
            c = r['combo']
            path = self.session_dir / r['dirname'] / f"{r['dirname']}_color.png"
            if path.exists():
                self._paths[(int(c['color_exposure']), _gain_key(c['color_gain']))] = path
        self._images = {}
        self._current = None

    @property
    def combos(self):
        return sorted(self._paths)

    def detect_model(self):
        fx = json.loads((self.session_dir / 'intrinsics.json').read_text())['tof']['fx']
        for ref, model in _FX_TO_MODEL:
            if abs(fx - ref) < 1.0:
                return model
        raise ValueError(f'ToF fx={fx:.1f} から機種を判別できません。--model で指定してください')

    def check_gears(self, table):
        missing = [g for g in table.gears if (g.exposure, _gain_key(g.gain)) not in self._paths]
        if missing:
            raise ValueError(f'ギア表の段がスイープにありません: {missing}')

    def set_gear(self, gear):
        key = (gear.exposure, _gain_key(gear.gain))
        if key not in self._paths:
            raise KeyError(f'スイープに無い組合せ: exposure={gear.exposure} gain={gear.gain}')
        self._current = key

    def read_frame(self):
        """(BGR画像, キャッシュ用のキー) を返す。"""
        key = self._current
        if key not in self._images:
            self._images[key] = cv2.imread(str(self._paths[key]))
        return self._images[key], self._paths[key].name
