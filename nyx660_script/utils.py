import yaml
import argparse
import json
import sys
import os
import struct

# sudo実行時にDISPLAYが消える問題を自動修正（cv2.imshow前に適用が必要）
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ':0'
    sudo_user = os.environ.get('SUDO_USER')
    if sudo_user:
        xauth = f'/home/{sudo_user}/.Xauthority'
        if os.path.exists(xauth):
            os.environ['XAUTHORITY'] = xauth
import numpy as np
import cv2
from datetime import datetime
from pathlib import Path
from ctypes import c_uint8, c_int32, c_uint16, c_bool

_CFG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    with open(_CFG_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg_dir = _CFG_PATH.parent
    # sdk_path: 相対パスならconfig.yaml基準で解決
    p = cfg['sdk_path']
    cfg['sdk_path'] = str((cfg_dir / p).resolve()) if not os.path.isabs(p) else p
    # camera.params_json: 相対パスならconfig.yaml基準で解決
    p = cfg.get('camera', {}).get('params_json')
    if p:
        params_path = str((cfg_dir / p).resolve()) if not os.path.isabs(p) else p
        cfg['camera']['params_json'] = params_path
        # fps/color_width/color_height が config.yaml に明示されていなければ
        # params_json（唯一の情報源）から読み取ってデフォルトにする
        _fill_camera_defaults_from_params_json(cfg['camera'], params_path)
    # output paths: ~展開 → 相対パスならconfig.yaml基準で解決
    for key in ('images_dir', 'pointcloud_dir', 'timelapse_dir', 'mp4_dir'):
        if key not in cfg['output']:
            continue
        p = os.path.expanduser(cfg['output'][key])
        cfg['output'][key] = str((cfg_dir / p).resolve()) if not os.path.isabs(p) else p
    return cfg


def _fill_camera_defaults_from_params_json(camera_cfg, params_path):
    """params_json の Control.FrameRate / ColorResolution を
    fps / color_width / color_height のデフォルト値として camera_cfg に補完する。
    config.yaml 側に明示指定があればそちらを優先し、上書きしない。
    """
    try:
        with open(params_path) as f:
            params = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"警告: params_json の読み込みに失敗しました（{params_path}）: {e}")
        return

    control = params.get('Control', {})
    if 'fps' not in camera_cfg and 'FrameRate' in control:
        camera_cfg['fps'] = int(control['FrameRate'])

    if 'color_width' not in camera_cfg and 'color_height' not in camera_cfg:
        res = control.get('ColorResolution')  # 例: "640*480"
        if res and '*' in res:
            w, h = res.split('*')
            camera_cfg['color_width'] = int(w)
            camera_cfg['color_height'] = int(h)


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument('--fps', type=int, default=None, metavar='N',
                   help='FPS（config.yaml の値を上書き）')
    p.add_argument('--color-size', type=str, default=None, metavar='WxH',
                   help='color解像度 例: 800x600（config.yaml の値を上書き）')
    p.add_argument('--tag', type=str, default=None, metavar='NAME',
                   help='セッションディレクトリ名に付ける任意タグ 例: greenhouse'
                        '（ファイル名には付かない）')
    return p


def apply_args(cfg, args):
    if getattr(args, 'fps', None) is not None:
        cfg['camera']['fps'] = args.fps
    if getattr(args, 'color_size', None) is not None:
        try:
            w, h = map(int, args.color_size.lower().split('x'))
            cfg['camera']['color_width']  = w
            cfg['camera']['color_height'] = h
        except ValueError:
            print(f"警告: --color-size の形式が不正です（例: 800x600）。デフォルト値を使用します。")
    return cfg


# ============================================================================
# データ命名規則  （real_syutoku / nyx660_syutoku 共通。両者で同一の実装を置く）
#
#   {cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}
#   例) nyx_260707_101741_00042_c.jpg
#
#   cam    : カメラコード（nyx / d435 / d405）。リポジトリを跨いでも衝突しない
#   YYMMDD : 取得日
#   HHMMSS : セッション開始時刻。1プロセス＝1セッションで固定値
#   NNNNN  : セッション内のショット連番（撮影時刻ではない）。
#            同一ショットの color/depth/ir は全て同じ番号を共有するため、
#            末尾トークンの差し替えだけで対応するファイルを引ける
#   mod    : モダリティコード（下表 MOD_CODES）
#
# ファイル名だけで「どのカメラの・いつの・何枚目の・何の画像か」が復元できるので、
# 学習用に flatten しても labelImg にまとめて放り込んでも識別性が壊れない。
# 撮影条件などファイル名に載せない情報は metadata.json に記録する。
# ============================================================================

CAM = 'nyx'  # このリポジトリはNYX660固定

# サブディレクトリ名 → モダリティコード
# （キーがそのままセッション配下のサブディレクトリ名になる）
MOD_CODES = {
    'color':          'c',
    'depth':          'd',      # 16bit raw（png）
    'depth_colormap': 'dc',
    'ir':             'i1',     # 単眼IR（NYX660）
    'ir_left':        'i1',
    'ir_right':       'i2',
    'ir_left_color':  'i1c',
    'ir_right_color': 'i2c',
    'pointcloud':     'pc',
    'points':         'pt',     # クリック座標などのテキスト
    'detected':       'det',    # YOLO描画済み
    'annotated':      'det',
    'depth_crop':      'dcrop',  # BBoxで切り出したdepth（crop/fruit_crop_extract.py）
    'pointcloud_crop': 'pccrop', # BBoxで切り出した点群（crop/fruit_crop_extract.py）
}


def mod_code(modality):
    """モダリティ名 → コード。未登録の名前はそのまま使う。"""
    return MOD_CODES.get(modality, modality)


def make_prefix(cam=CAM, started_at=None):
    """セッションprefix（{cam}_{YYMMDD}_{HHMMSS}）を返す。mp4 など
    セッションディレクトリを作らない出力でも同じ規則を使うためのヘルパー。"""
    now = started_at or datetime.now()
    return f"{cam}_{now.strftime('%y%m%d')}_{now.strftime('%H%M%S')}"


class Session:
    """1回の取得セッション。保存先ディレクトリとファイル名の生成を一元管理する。

    <base_dir>/<YYMMDD>/<prefix>[_<tag>]/<modality>/<prefix>_<NNNNN>_<mod>.<ext>

    tag はセッションディレクトリ名にだけ付く（ファイル名の桁を汚さないため）。
    ディレクトリ名はファイル名の prefix に前方一致するので対応関係は保たれる。
    """

    def __init__(self, base_dir, cam=CAM, tag=None, subdirs=(), started_at=None):
        self.started_at = started_at or datetime.now()
        self.cam    = cam
        self.tag    = tag
        self.date   = self.started_at.strftime('%y%m%d')
        self.time   = self.started_at.strftime('%H%M%S')
        self.prefix = make_prefix(cam, self.started_at)

        dir_name = f"{self.prefix}_{tag}" if tag else self.prefix
        self.dir = Path(os.path.expanduser(str(base_dir))) / self.date / dir_name
        self.dir.mkdir(parents=True, exist_ok=True)
        for sub in subdirs:
            (self.dir / sub).mkdir(exist_ok=True)

    def name(self, idx, modality, ext='jpg'):
        """ファイル名のみを返す。"""
        return f"{self.prefix}_{idx:05d}_{mod_code(modality)}.{ext}"

    def path(self, idx, modality, ext='jpg', sub=True):
        """保存先のフルパスを返す（ディレクトリは自動作成）。

        sub=True  : <dir>/<modality>/ に置く（既定）
        sub=False : <dir>/ 直下に置く
        sub='xxx' : <dir>/xxx/ に置く
        """
        d = self.dir if sub is False else self.dir / (modality if sub is True else sub)
        d.mkdir(parents=True, exist_ok=True)
        return str(d / self.name(idx, modality, ext))

    def file(self, suffix, ext):
        """連番を持たないセッション単位のファイル（動画など）のフルパスを返す。"""
        return str(self.dir / f"{self.prefix}_{suffix}.{ext}")

    def write_metadata(self, **info):
        """metadata.json を書き出す。撮影条件など、ファイル名に載せない情報はここに。"""
        meta = {
            'session_id': self.prefix,
            'camera_code': self.cam,
            'started_at': self.started_at.isoformat(timespec='seconds'),
            'tag': self.tag,
            'naming': '{cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}',
        }
        meta.update(info)
        with open(self.dir / 'metadata.json', 'w') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)


def init_sdk(cfg):
    """SDKのPythonパスをsys.path[0]に挿入する。SDK型のimport前に必ず呼ぶこと。"""
    python_path = os.path.join(cfg['sdk_path'], "MultilanguageSDK/Python")
    if python_path not in sys.path:
        sys.path.insert(0, python_path)


def _load_params_json(params_path):
    try:
        with open(params_path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"警告: params_json の読み込みに失敗しました（{params_path}）: {e}")
        return None


def _apply_tof_resolution(cam, control):
    """ToF解像度をJSONのControlから適用する（StartStream前必須）。"""
    res = control.get('ToFResolution')
    if res and '*' in res:
        w, h = res.split('*')
        ret = cam.scSetToFResolution(c_int32(int(w)), c_int32(int(h)))
        if ret != 0:
            print(f"警告: scSetToFResolution failed: {ret}")


def _apply_exposure_and_filter_params(cam, params):
    """露光・フィルタ・IRGm補正をJSONの値で個別APIを使い明示的に設定する（StartStream後必須）。

    scSetParamsByJson（JSON一括適用）は実機でScepterGUIToolの表示と一致しない事象が
    確認されたため使用せず、公式サンプル ToFExposureTimeSetGet / ToFFiltersSetGet と
    同じ個別APIをJSONの値で呼ぶ方式にしている。
    """
    from API.ScepterDS_enums import ScSensorType, ScExposureControlMode
    from API.ScepterDS_types import (
        ScIRGMMCorrectionParams, ScTimeFilterParams,
        ScConfidenceFilterParams, ScFlyingPixelFilterParams,
    )

    def _check(name, ret):
        if ret != 0:
            print(f"警告: {name} failed: {ret}")

    exposure = params.get('ExposureTime', {})

    def _set_exposure(label, sensor_type, mode_str, time_us):
        if not mode_str:
            return
        mode = (ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL if mode_str == 'Manual'
                else ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_AUTO)
        _check(f"scSetExposureControlMode({label})", cam.scSetExposureControlMode(sensor_type, mode))
        if mode == ScExposureControlMode.SC_EXPOSURE_CONTROL_MODE_MANUAL and time_us is not None:
            _check(f"scSetExposureTime({label})", cam.scSetExposureTime(sensor_type, c_int32(int(time_us))))

    _set_exposure('ToF', ScSensorType.SC_TOF_SENSOR, exposure.get('ToF_ExposureMode'), exposure.get('ToF_ExposureTime'))
    _set_exposure('Color', ScSensorType.SC_COLOR_SENSOR, exposure.get('Color_ExposureMode'), exposure.get('Color_ExposureTime'))

    roi = exposure.get('ColorAECROI')
    if roi and len(roi) == 4:
        x, y, w, h = roi
        _check("scSetColorAECROI", cam.scSetColorAECROI(c_uint16(x), c_uint16(y), c_uint16(w), c_uint16(h)))

    if 'HDR_Mode' in exposure:
        _check("scSetHDRModeEnabled", cam.scSetHDRModeEnabled(c_bool(bool(exposure['HDR_Mode']))))
    if 'WDR_Mode' in exposure:
        _check("scSetWDRModeEnabled", cam.scSetWDRModeEnabled(c_bool(bool(exposure['WDR_Mode']))))

    control = params.get('Control', {})
    if 'IRGmmGain' in control:
        _check("scSetIRGMMGain", cam.scSetIRGMMGain(c_uint8(int(control['IRGmmGain']))))
    if 'IRGmmCorrectionEnabled' in control or 'IRGmmCorrectionThreshold' in control:
        gmm = ScIRGMMCorrectionParams()
        gmm.enable = bool(control.get('IRGmmCorrectionEnabled', False))
        gmm.threshold = int(control.get('IRGmmCorrectionThreshold', 0))
        _check("scSetIRGMMCorrection", cam.scSetIRGMMCorrection(gmm))

    filt = params.get('Filter', {})
    if 'TimeFilter' in filt or 'TimeFilter_Threshold' in filt:
        p = ScTimeFilterParams()
        p.enable = bool(filt.get('TimeFilter', False))
        p.threshold = int(filt.get('TimeFilter_Threshold', 0))
        _check("scSetTimeFilterParams", cam.scSetTimeFilterParams(p))
    if 'ConfidenceFilter' in filt or 'ConfidenceFilter_Threshold' in filt:
        p = ScConfidenceFilterParams()
        p.enable = bool(filt.get('ConfidenceFilter', False))
        p.threshold = int(filt.get('ConfidenceFilter_Threshold', 0))
        _check("scSetConfidenceFilterParams", cam.scSetConfidenceFilterParams(p))
    if 'FlyingPixelFilter' in filt or 'FlyingPixelFilter_Threshold' in filt:
        p = ScFlyingPixelFilterParams()
        p.enable = bool(filt.get('FlyingPixelFilter', False))
        p.threshold = int(filt.get('FlyingPixelFilter_Threshold', 0))
        _check("scSetFlyingPixelFilterParams", cam.scSetFlyingPixelFilterParams(p))
    if 'Fillhole' in filt:
        _check("scSetFillHoleFilterEnabled", cam.scSetFillHoleFilterEnabled(c_bool(bool(filt['Fillhole']))))
    if 'SpatialFilter' in filt:
        _check("scSetSpatialFilterEnabled", cam.scSetSpatialFilterEnabled(c_bool(bool(filt['SpatialFilter']))))


def _print_applied_params(cam, params, color_w=None, color_h=None):
    """JSONの期待値と実機の読み戻し値を並べて表示する（ズレを一目で確認するため）。"""
    from API.ScepterDS_enums import ScSensorType, ScExposureControlMode, ScWorkMode

    def _mode_name(v):
        try:
            return ScExposureControlMode(v).name
        except ValueError:
            return str(v)

    def _workmode_name(v):
        try:
            return ScWorkMode(v).name
        except ValueError:
            return str(v)

    control  = (params or {}).get('Control', {})
    exposure = (params or {}).get('ExposureTime', {})
    filt     = (params or {}).get('Filter', {})

    _, tw, th       = cam.scGetToFResolution()
    _, cw, ch       = cam.scGetColorResolution()
    _, fps          = cam.scGetFrameRate()
    _, work_mode    = cam.scGetWorkMode()
    _, tof_mode     = cam.scGetExposureControlMode(ScSensorType.SC_TOF_SENSOR)
    _, tof_exp      = cam.scGetExposureTime(ScSensorType.SC_TOF_SENSOR)
    _, color_mode   = cam.scGetExposureControlMode(ScSensorType.SC_COLOR_SENSOR)
    _, color_exp    = cam.scGetExposureTime(ScSensorType.SC_COLOR_SENSOR)
    _, rx, ry, rw, rh = cam.scGetColorAECROI()
    _, hdr          = cam.scGetHDRModeEnabled()
    _, wdr          = cam.scGetWDRModeEnabled()
    _, gmm_gain     = cam.scGetIRGMMGain()
    _, gmm_corr     = cam.scGetIRGMMCorrection()
    _, time_f       = cam.scGetTimeFilterParams()
    _, conf_f       = cam.scGetConfidenceFilterParams()
    _, fly_f        = cam.scGetFlyingPixelFilterParams()
    _, fillhole     = cam.scGetFillHoleFilterEnabled()
    _, spatial      = cam.scGetSpatialFilterEnabled()
    _, xform_d2c    = cam.scGetTransformDepthImgToColorSensorEnabled()
    _, xform_c2d    = cam.scGetTransformColorImgToDepthSensorEnabled()

    # Color解像度はconfig.yamlで意図的にJSONの値から上書きしているため、
    # JSONの値ではなくconfig.yamlの要求値と比較する（他はJSON準拠のOK/!!判定）
    color_expected = f"{color_w}*{color_h}" if color_w and color_h else control.get('ColorResolution')

    rows = [
        ("ToF解像度",              control.get('ToFResolution'),          f"{tw}*{th}"),
        ("Color解像度(config.yaml基準)", color_expected,                  f"{cw}*{ch}"),
        ("FPS",                    control.get('FrameRate'),              fps),
        ("WorkMode",               control.get('WorkMode'),               _workmode_name(work_mode)),
        ("ToF露光mode",            exposure.get('ToF_ExposureMode'),      _mode_name(tof_mode)),
        ("ToF露光time(us)",        exposure.get('ToF_ExposureTime'),      tof_exp),
        ("Color露光mode",          exposure.get('Color_ExposureMode'),    _mode_name(color_mode)),
        ("Color露光time(us)",      exposure.get('Color_ExposureTime'),    color_exp),
        ("ColorAECROI",            exposure.get('ColorAECROI'),           [rx, ry, rw, rh]),
        ("HDR_Mode",               exposure.get('HDR_Mode'),              hdr),
        ("WDR_Mode",               exposure.get('WDR_Mode'),              wdr),
        ("IRGmmGain",              control.get('IRGmmGain'),              gmm_gain),
        ("IRGmmCorrection enable", control.get('IRGmmCorrectionEnabled'), gmm_corr.enable),
        ("IRGmmCorrection thresh", control.get('IRGmmCorrectionThreshold'), gmm_corr.threshold),
        ("TimeFilter enable",      filt.get('TimeFilter'),                time_f.enable),
        ("TimeFilter thresh",      filt.get('TimeFilter_Threshold'),      time_f.threshold),
        ("ConfidenceFilter enable",   filt.get('ConfidenceFilter'),       conf_f.enable),
        ("ConfidenceFilter thresh",   filt.get('ConfidenceFilter_Threshold'), conf_f.threshold),
        ("FlyingPixelFilter enable",  filt.get('FlyingPixelFilter'),      fly_f.enable),
        ("FlyingPixelFilter thresh",  filt.get('FlyingPixelFilter_Threshold'), fly_f.threshold),
        ("Fillhole",               filt.get('Fillhole'),                  fillhole),
        ("SpatialFilter",          filt.get('SpatialFilter'),             spatial),
        ("Transform Depth→Color(常時False)", False,                      xform_d2c),
        ("Transform Color→Depth(常時False)", False,                      xform_c2d),
    ]

    print("=== 実機に適用されているパラメータ（JSON期待値 vs 実機の読み戻し値） ===")
    for label, json_val, actual_val in rows:
        json_str = "-" if json_val is None else str(json_val)
        match = (json_val is None) or (str(json_val) == str(actual_val))
        mark = "OK " if match else "!! "
        print(f"  {mark}{label:<28} JSON={json_str:<14} 実機={actual_val}")
    print("=" * 60)


def open_camera(cfg):
    """デバイスを検出・オープンし、ストリームを開始して返す。"""
    init_sdk(cfg)
    from API.ScepterDS_api import ScepterTofCam
    from API.ScepterDS_enums import ScWorkMode

    cam = ScepterTofCam()
    count = cam.scGetDeviceCount(3000)
    if count <= 0:
        raise RuntimeError("NYX660が見つかりません（ネットワーク接続とIPを確認してください）")

    ret, infolist = cam.scGetDeviceInfoList(count)
    if ret != 0:
        raise RuntimeError(f"scGetDeviceInfoList failed: {ret}")
    info = infolist[0]
    print(f"デバイス: {info.serialNumber.decode()}  IP: {info.ip.decode()}")

    ret = cam.scOpenDeviceBySN(info.serialNumber)
    if ret != 0:
        raise RuntimeError(f"scOpenDeviceBySN failed: {ret}")

    # params_json が設定されているのに読み込めない場合は、意図しないパラメータのまま
    # データ取得が進んでしまう事故を防ぐため、警告で済ませず即座に停止する
    # （2026-07-10: JSON構文エラーが警告のみで見過ごされ、パラメータ未適用のまま
    #   撮影を続けてしまった事例があったため）。
    params_path = cfg['camera'].get('params_json')
    params = None
    if params_path:
        params = _load_params_json(params_path)
        if params is None:
            cam.scCloseDevice()
            raise RuntimeError(
                f"params_json の読み込みに失敗しました（{params_path}）。"
                "JSONの構文を確認してください（例: //コメントはJSON非対応）。"
            )
    else:
        print("警告: camera.params_json が未設定のため、パラメータJSONは適用されません")

    # --- StartStream 前: ToF解像度・FPS・Color解像度・ワークモード ---
    # （ToF解像度はJSONの値をそのまま適用。FPS/Color解像度はconfig.yaml/CLI引数での
    #   明示上書き。デフォルトはparams_jsonのControlと同値）
    if params:
        _apply_tof_resolution(cam, params.get('Control', {}))

    fps = int(cfg['camera'].get('fps', 15))
    cam.scSetFrameRate(c_uint8(fps))

    color_w = int(cfg['camera'].get('color_width', 640))
    color_h = int(cfg['camera'].get('color_height', 480))
    ret = cam.scSetColorResolution(c_int32(color_w), c_int32(color_h))
    if ret != 0:
        print(f"警告: scSetColorResolution({color_w}x{color_h}) failed: {ret}"
              "（このカメラ未対応の解像度の可能性。実機は別の値のまま動作しています）")
    print(f"color解像度（要求値）: {color_w}×{color_h}  FPS: {fps}")

    # アクティブモードを明示設定（ScepterGUIToolがトリガーモードで終了した場合の対処）
    ret = cam.scSetWorkMode(ScWorkMode.SC_ACTIVE_MODE)
    if ret != 0:
        print(f"警告: scSetWorkMode failed: {ret}")

    ret = cam.scStartStream()
    if ret != 0:
        cam.scCloseDevice()
        raise RuntimeError(f"scStartStream failed: {ret}")

    # --- StartStream 後: カメラ状態をリセット ---
    # GUITool が有効にしたまま終了した設定を解除する（params_json の対象外の項目）
    cam.scSetTransformDepthImgToColorSensorEnabled(False)
    cam.scSetTransformColorImgToDepthSensorEnabled(False)

    # --- StartStream 後: 露光・フィルタ・IRGm補正をJSONの値で個別API適用 ---
    # ToF_ExposureTime等は深度性能に直結するため確実に反映させる必要がある。
    if params:
        _apply_exposure_and_filter_params(cam, params)
        print(f"カメラパラメータ適用（個別API・{params_path}）")

    _print_applied_params(cam, params, color_w=color_w, color_h=color_h)

    return cam


def close_camera(cam):
    cam.scStopStream()
    cam.scCloseDevice()


def save_intrinsics(cam, save_dir):
    """ToF/Colorセンサーの内部・外部パラメータを <save_dir>/intrinsics.json として保存する。"""
    from API.ScepterDS_enums import ScSensorType

    _, tof_intr = cam.scGetSensorIntrinsicParameters(ScSensorType.SC_TOF_SENSOR)
    _, col_intr = cam.scGetSensorIntrinsicParameters(ScSensorType.SC_COLOR_SENSOR)
    _, extr     = cam.scGetSensorExtrinsicParameters()

    intrinsics_data = {
        'tof': {
            'fx': tof_intr.fx, 'fy': tof_intr.fy,
            'cx': tof_intr.cx, 'cy': tof_intr.cy,
            'k1': tof_intr.k1, 'k2': tof_intr.k2,
            'p1': tof_intr.p1, 'p2': tof_intr.p2,
            'k3': tof_intr.k3, 'k4': tof_intr.k4,
            'k5': tof_intr.k5, 'k6': tof_intr.k6,
        },
        'color': {
            'fx': col_intr.fx, 'fy': col_intr.fy,
            'cx': col_intr.cx, 'cy': col_intr.cy,
            'k1': col_intr.k1, 'k2': col_intr.k2,
            'p1': col_intr.p1, 'p2': col_intr.p2,
            'k3': col_intr.k3,
        },
        'extrinsics': {
            'rotation':    list(extr.rotation),
            'translation': list(extr.translation),
        },
    }
    with open(os.path.join(save_dir, 'intrinsics.json'), 'w') as f:
        json.dump(intrinsics_data, f, indent=2)


def extract_depth(frame):
    """ScFrame → uint16 numpy 配列 (mm単位)"""
    raw = np.ctypeslib.as_array(frame.pFrameData, (frame.width * frame.height * 2,))
    return raw.view(np.uint16).reshape(frame.height, frame.width).copy()


def extract_color(frame):
    """ScFrame → uint8 BGR numpy 配列"""
    raw = np.ctypeslib.as_array(frame.pFrameData, (frame.width * frame.height * 3,))
    return raw.view(np.uint8).reshape(frame.height, frame.width, 3).copy()


def extract_ir(frame):
    """ScFrame → uint8 grayscale numpy 配列"""
    raw = np.ctypeslib.as_array(frame.pFrameData, (frame.dataLen,))
    return raw.view(np.uint8).reshape(frame.height, frame.width).copy()


def make_depth_colormap(depth_image, alpha=0.4):
    if alpha is None:
        valid = depth_image[depth_image > 0]
        if valid.size == 0:
            return np.zeros((*depth_image.shape, 3), dtype=np.uint8)
        normed = np.clip(
            (depth_image.astype(np.float32) - valid.min()) / (valid.max() - valid.min() + 1e-6) * 255,
            0, 255,
        ).astype(np.uint8)
        return cv2.applyColorMap(normed, cv2.COLORMAP_JET)
    return cv2.applyColorMap(cv2.convertScaleAbs(depth_image, alpha=alpha), cv2.COLORMAP_JET)


def get_bbox_depth(depth, x1, y1, x2, y2):
    """BBox 内の有効深度（>0）の中央値を mm 単位で返す。有効点なしなら None。
    depth は 640×480、bbox 座標も同じ座標系（inference_size=640×480 で推論した結果）を渡すこと。
    """
    roi = depth[max(0, y1):min(depth.shape[0], y2),
                max(0, x1):min(depth.shape[1], x2)]
    valid = roi[roi > 0]
    return float(np.median(valid)) if len(valid) > 0 else None


def save_ply(path, pointlist, count):
    """ScVector3f のリストをバイナリ PLY として保存（z==0 / z==65535 は除外）。"""
    valid = [(p.x, p.y, p.z) for p in pointlist[:count]
             if p.z != 0 and p.z != 65535]
    with open(path, 'wb') as f:
        header = (
            "ply\n"
            "format binary_little_endian 1.0\n"
            f"element vertex {len(valid)}\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
        )
        f.write(header.encode('ascii'))
        for x, y, z in valid:
            f.write(struct.pack('<fff', x, y, z))


def write_ply_points(path, points_xyz):
    """(N,3) float配列をバイナリPLYとして保存する（NaN行は除外）。"""
    pts = np.asarray(points_xyz, dtype=np.float64).reshape(-1, 3)
    pts = pts[~np.isnan(pts).any(axis=1)]
    with open(path, 'wb') as f:
        header = (
            "ply\n"
            "format binary_little_endian 1.0\n"
            f"element vertex {len(pts)}\n"
            "property float x\n"
            "property float y\n"
            "property float z\n"
            "end_header\n"
        )
        f.write(header.encode('ascii'))
        f.write(pts.astype('<f4').tobytes())


# ============================================================================
# BBoxクロップ（crop/bbox_annotate.py, crop/fruit_crop_extract.py で使用）
#
# dataset_point_collect.py の出力は、color(既定1600x1200・raw) と
# depth/pointcloud(640x480) が別解像度・別座標系のまま保存される
# （CLAUDE.md記載の通りアラインは撮影時にかけない方針がデフォルト）。
# そのため colorに描いたBBoxからdepth/pointcloudを切り出すには2通りある:
#
#  1) 撮影時アライン（dataset_point_collect.py --align）:
#     scSetTransformColorImgToDepthSensorEnabled で color を depth と同じ
#     640x480ピクセルグリッドに変換してから保存するため、BBoxはそのまま
#     depth画素座標として使える（bbox_mask_direct）。
#  2) 撮影後クロップ（未アラインセッション、fruit_crop_extract.py既定）:
#     intrinsics.json の内部・外部パラメータで depth点群をcolor画像へ射影し、
#     BBox内に収まるdepth画素を逆算する（bbox_mask_via_projection）。
#     scGetSensorExtrinsicParameters の rotation/translation が
#     「ToF→Color」なのか「Color→ToF」なのかはPython公式サンプルに
#     手動射影の例がなく未検証。fruit_crop_extract.py --debug-overlay で
#     射影結果をcolor画像に重ねて可視化し、目視でズレを確認すること。
#     ズレていた場合は --extrinsics-mode color2tof を試す。
# ============================================================================

def backproject_depth(depth, tof_intr):
    """depth(mm, HxW uint16/float) -> (H,W,3) 3D点（ToFセンサー座標系, mm）。

    無効画素（depth<=0）は NaN。歪み補正はしていない簡易ピンホールモデル
    （クロップ用途の位置精度としては十分だが、精密な点群生成には
    scConvertDepthFrameToPointCloudVector の結果を使うこと）。
    """
    h, w = depth.shape
    fx, fy, cx, cy = tof_intr['fx'], tof_intr['fy'], tof_intr['cx'], tof_intr['cy']
    ys, xs = np.mgrid[0:h, 0:w]
    z = depth.astype(np.float64)
    x = (xs - cx) * z / fx
    y = (ys - cy) * z / fy
    pts = np.stack([x, y, z], axis=-1)
    pts[depth <= 0] = np.nan
    return pts


def project_to_color(points, extrinsics, color_intr, mode='tof2color'):
    """ToF座標系の3D点(...,3) をColorセンサー画像へ射影し(...,2)の(u,v)を返す。

    mode='tof2color': rotation/translationを「ToF→Color」として使用（既定）。
    mode='color2tof' : 向きが逆だった場合用に R^T, -R^T@T へ変換して使用。
    """
    shape = points.shape[:-1]
    pts = points.reshape(-1, 3)
    valid = ~np.isnan(pts).any(axis=1)

    R = np.array(extrinsics['rotation'], dtype=np.float64).reshape(3, 3)
    T = np.array(extrinsics['translation'], dtype=np.float64).reshape(3, 1)
    if mode == 'color2tof':
        R = R.T
        T = -R @ T

    K = np.array([[color_intr['fx'], 0, color_intr['cx']],
                  [0, color_intr['fy'], color_intr['cy']],
                  [0, 0, 1]], dtype=np.float64)
    dist_keys = ['k1', 'k2', 'p1', 'p2', 'k3']
    dist = np.array([color_intr.get(k, 0.0) for k in dist_keys], dtype=np.float64)

    uv = np.full((pts.shape[0], 2), np.nan)
    if valid.any():
        rvec, _ = cv2.Rodrigues(R)
        proj, _ = cv2.projectPoints(pts[valid].astype(np.float64), rvec, T, K, dist)
        uv[valid] = proj.reshape(-1, 2)
    return uv.reshape(*shape, 2)


def bbox_mask_direct(depth, bbox):
    """アライン済み（color==depth座標系）セッション用: BBoxをそのままdepth画素矩形として使う。"""
    x1, y1, x2, y2 = [int(round(v)) for v in bbox]
    h, w = depth.shape
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    mask = np.zeros(depth.shape, dtype=bool)
    if x2 > x1 and y2 > y1:
        mask[y1:y2, x1:x2] = depth[y1:y2, x1:x2] > 0
    return mask


def bbox_mask_via_projection(depth, tof_intr, color_intr, extrinsics, bbox, mode='tof2color'):
    """未アラインセッション用: depth点群をcolor画像へ射影し、BBox内に入る画素のマスクを返す。"""
    x1, y1, x2, y2 = bbox
    pts = backproject_depth(depth, tof_intr)
    uv = project_to_color(pts, extrinsics, color_intr, mode=mode)
    u, v = uv[..., 0], uv[..., 1]
    with np.errstate(invalid='ignore'):
        mask = (depth > 0) & (u >= x1) & (u <= x2) & (v >= y1) & (v <= y2)
    return mask


def crop_depth_by_mask(depth, mask):
    """マスクの外接矩形でdepthを切り出す（矩形外は0埋め）。マスクが空ならNoneを返す。"""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None, None
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    cropped = np.where(mask, depth, 0)[y1:y2, x1:x2]
    return cropped, (x1, y1, x2, y2)


def load_yolo_boxes(txt_path, img_w, img_h):
    """YOLO形式ラベル(class xc yc w h; 正規化)を画素座標のBBoxリストに変換する。

    戻り値: [(class_id, x1, y1, x2, y2), ...]（座標はラベル対象画像の画素系）
    """
    boxes = []
    if not os.path.exists(txt_path):
        return boxes
    with open(txt_path) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 5:
                continue
            cls, xc, yc, bw, bh = parts
            cls = int(float(cls))
            xc, yc, bw, bh = float(xc), float(yc), float(bw), float(bh)
            x1 = (xc - bw / 2) * img_w
            y1 = (yc - bh / 2) * img_h
            x2 = (xc + bw / 2) * img_w
            y2 = (yc + bh / 2) * img_h
            boxes.append((cls, x1, y1, x2, y2))
    return boxes
