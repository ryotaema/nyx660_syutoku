"""実機用の設定先: ScepterSDK（Python）でカメラを直接操作し、Color の露光・ゲインを段に合わせて設定する。

カメラのオープンとパラメータJSONの適用は utils.open_camera() に任せる（他の撮影スクリプトと同じ初期状態）。
その上で Color 露光を手動にし、set_gear() で露光・ゲインを設定する。close() で開始前の
Color 露光モード・露光・ゲインに戻してからカメラを閉じる。

露光時間は現在の FPS での上限（scGetMaxExposureTime）に丸めてから設定する。上限を超える値を
設定すると -105（SC_CMD_SYNC_TIME_OUT）になり実機の値は変わらないため
（tools/param_tune/param_sweep.py に記録済みの実機挙動）。

replay.py と同じく set_gear() / read_frame() / detect_model() / check_gears() を持つ。
read_frame() の直後に last_frame（frameIndex・hardwaretimestamp）、set_gear() の直後に
last_applied（要求値・丸め後の値・読み戻し値）を参照できる。
"""

import sys
import time
from ctypes import c_float, c_int32, c_uint16
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / 'nyx660_script'))

from utils import close_camera, extract_color, open_camera  # noqa: E402


class SdkDirectBackend:
    def __init__(self, cfg, frame_timeout_ms=1200, max_wait_s=5.0):
        self.cfg = cfg
        self.frame_timeout_ms = frame_timeout_ms
        self.max_wait_s = max_wait_s
        self.cam = None
        self.max_exposure = None
        self.last_frame = {}
        self.last_applied = {}
        self._restore = None
        self._current = None   # 実機に設定済みの (exposure, gain)

    # ------------------------------------------------------------------
    def open(self):
        self.cam = open_camera(self.cfg)
        from API.ScepterDS_enums import ScExposureControlMode, ScSensorType
        self._enums = (ScSensorType, ScExposureControlMode)
        cam = self.cam
        color = ScSensorType.SC_COLOR_SENSOR
        self._restore = dict(mode=cam.scGetExposureControlMode(color)[1],
                             exposure=cam.scGetExposureTime(color)[1],
                             gain=cam.scGetColorGain()[1])
        self._check('scSetExposureControlMode(Color->Manual)',
                    cam.scSetExposureControlMode(color, ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL))
        ret, self.max_exposure = cam.scGetMaxExposureTime(color)
        if ret != 0:
            print(f'警告: scGetMaxExposureTime(Color) failed: {ret}（露光の丸めをしません）')
            self.max_exposure = None
        return self

    def close(self):
        if self.cam is None:
            return
        ScSensorType, ScExposureControlMode = self._enums
        color = ScSensorType.SC_COLOR_SENSOR
        try:
            if self._restore:
                # scGetExposureControlMode は整数を返すが、設定側は列挙型（.value を参照）を要求する
                self._check('scSetExposureControlMode(Color, restore)',
                            self.cam.scSetExposureControlMode(color, ScExposureControlMode(self._restore['mode'])))
                if self._restore['mode'] == ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL.value:
                    self.cam.scSetExposureTime(color, c_int32(self._restore['exposure']))
                    self.cam.scSetColorGain(c_float(self._restore['gain']))
        finally:
            close_camera(self.cam)
            self.cam = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------
    def device(self):
        info = getattr(self.cam, 'device_info', None)
        if info is None:
            return {}
        return dict(product=info.productName.decode(errors='replace'),
                    serial=info.serialNumber.decode(errors='replace'),
                    ip=info.ip.decode(errors='replace'))

    def detect_model(self):
        product = self.device().get('product')
        if not product:
            raise RuntimeError('機種名（productName）を取得できません。--model で指定してください')
        return product

    def check_gears(self, table):
        """露光が現在の上限を超える段を返す（設定時は上限に丸められる）。"""
        if self.max_exposure is None:
            return []
        return [(i, g) for i, g in enumerate(table.gears) if g.exposure > self.max_exposure]

    def set_gear(self, gear):
        ScSensorType, _ = self._enums
        color = ScSensorType.SC_COLOR_SENSOR
        exposure = gear.exposure
        if self.max_exposure is not None and exposure > self.max_exposure:
            exposure = self.max_exposure
        t0 = time.monotonic()
        if self._current is None or self._current[0] != exposure:
            self._check('scSetExposureTime(Color)', self.cam.scSetExposureTime(color, c_int32(exposure)))
        if self._current is None or self._current[1] != gear.gain:
            self._check('scSetColorGain', self.cam.scSetColorGain(c_float(gear.gain)))
        self._current = (exposure, gear.gain)
        self.last_applied = dict(req_exposure=gear.exposure, req_gain=gear.gain, set_exposure=exposure,
                                 rb_exposure=self.cam.scGetExposureTime(color)[1],
                                 rb_gain=round(float(self.cam.scGetColorGain()[1]), 3),
                                 set_ms=round((time.monotonic() - t0) * 1000, 1),
                                 after_frame=self.last_frame.get('frame_index'))
        return self.last_applied

    def read_frame(self):
        """次の Color フレームを (BGR画像, キー) で返す。キーは 'live_<frameIndex>'。"""
        from API.ScepterDS_enums import ScFrameType
        deadline = time.monotonic() + self.max_wait_s
        while time.monotonic() < deadline:
            ret, ready = self.cam.scGetFrameReady(c_uint16(self.frame_timeout_ms))
            if ret != 0 or not ready.color:
                continue
            ret, frame = self.cam.scGetFrame(ScFrameType.SC_COLOR_FRAME)
            if ret != 0:
                continue
            self.last_frame = dict(frame_index=int(frame.frameIndex), hw_ts=int(frame.hardwaretimestamp),
                                   wall=time.time())
            return extract_color(frame), f"live_{frame.frameIndex}"
        raise TimeoutError(f'{self.max_wait_s}s 以内に Color フレームを取得できませんでした')

    @staticmethod
    def _check(name, ret):
        if ret == 0:
            return
        hint = '（-105: 確認応答タイムアウト。露光が現在のFPSでの上限を超えている可能性）' if ret == -105 else ''
        print(f'警告: {name} failed: {ret}{hint}')
