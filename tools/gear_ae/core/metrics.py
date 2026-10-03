"""Color画像から露光判断用の明るさ指標を計算する（SDK・YOLO非依存）。

明るさは「空を除いた部分の輝度の中央値」（lum）で測る。空は「明るくて色の薄い画素」
（HSVで S < SKY_SAT_MAX かつ V > SKY_VAL_MIN）とする。

2026_0930_DS_compare の Color スイープで確認した性質（../log_nyx/2026-10-03.md）:
  - lum は露光を上げるほど単調に増え、白飛びした植物が空として除外されても逆向きに
    動かない（最も白飛びした条件でも lum≈237）。
  - lum は植物部分（緑色の画素）の明るさとほぼ完全に連動する（相関 1.00）。
    果実が写っていなくても使えるため、検出結果に頼らず判断できる。
  - 空以外の画素のうち CLIP_LEVEL 以上の割合（clip_frac）は、lum 160 で約1%、
    lum 200 で約4%、lum 220 で7〜12% と、白飛びに合わせて増える。

空以外の画素が min_valid_frac 未満のとき（空ばかり写っている、全体が白飛びしている等）は
lum を None にして「計測不能」とする。どちらの状況かは画像だけでは区別できないため、
判断側（controller）で既定の段に戻す等の扱いをする。
"""

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

SKY_SAT_MAX = 40
SKY_VAL_MIN = 170
CLIP_LEVEL = 245
WORK_WIDTH = 400  # 計算を軽くするため、この幅に縮小してから計測する


@dataclass(frozen=True)
class FrameMetrics:
    lum: Optional[float]   # 空以外の輝度の中央値。計測不能なら None
    clip_frac: float       # 空以外の画素のうち白飛び（CLIP_LEVEL以上）の割合
    valid_frac: float      # 画像全体のうち空以外の画素の割合
    sky_frac: float        # 画像全体のうち空と判定した画素の割合

    @property
    def valid(self):
        return self.lum is not None


def measure(image_bgr, min_valid_frac=0.05):
    """BGR画像（uint8）から FrameMetrics を計算する。"""
    h, w = image_bgr.shape[:2]
    if w > WORK_WIDTH:
        image_bgr = cv2.resize(image_bgr, (WORK_WIDTH, round(h * WORK_WIDTH / w)),
                               interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    sky = (hsv[..., 1] < SKY_SAT_MAX) & (hsv[..., 2] > SKY_VAL_MIN)
    sky_frac = float(sky.mean())
    valid_frac = 1.0 - sky_frac
    if valid_frac < min_valid_frac:
        return FrameMetrics(lum=None, clip_frac=float((gray >= CLIP_LEVEL).mean()),
                            valid_frac=valid_frac, sky_frac=sky_frac)
    rest = gray[~sky]
    return FrameMetrics(lum=float(np.median(rest)), clip_frac=float((rest >= CLIP_LEVEL).mean()),
                        valid_frac=valid_frac, sky_frac=sky_frac)
