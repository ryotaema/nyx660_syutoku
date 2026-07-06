import yaml
import argparse
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
from pathlib import Path
from ctypes import c_uint8, c_int32

_CFG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    with open(_CFG_PATH) as f:
        cfg = yaml.safe_load(f)
    cfg_dir = _CFG_PATH.parent
    # sdk_path: 相対パスならconfig.yaml基準で解決
    p = cfg['sdk_path']
    cfg['sdk_path'] = str((cfg_dir / p).resolve()) if not os.path.isabs(p) else p
    # output paths: ~展開 → 相対パスならconfig.yaml基準で解決
    for key in ('images_dir', 'pointcloud_dir', 'timelapse_dir', 'mp4_dir'):
        if key not in cfg['output']:
            continue
        p = os.path.expanduser(cfg['output'][key])
        cfg['output'][key] = str((cfg_dir / p).resolve()) if not os.path.isabs(p) else p
    return cfg


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument('--fps', type=int, default=None, metavar='N',
                   help='FPS（config.yaml の値を上書き）')
    p.add_argument('--color-size', type=str, default=None, metavar='WxH',
                   help='color解像度 例: 800x600（config.yaml の値を上書き）')
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


def init_sdk(cfg):
    """SDKのPythonパスをsys.path[0]に挿入する。SDK型のimport前に必ず呼ぶこと。"""
    python_path = os.path.join(cfg['sdk_path'], "MultilanguageSDK/Python")
    if python_path not in sys.path:
        sys.path.insert(0, python_path)


def open_camera(cfg):
    """デバイスを検出・オープンし、ストリームを開始して返す。"""
    init_sdk(cfg)
    from API.ScepterDS_api import ScepterTofCam
    from API.ScepterDS_types import (ScTimeFilterParams, ScConfidenceFilterParams,
                                      ScFlyingPixelFilterParams)
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

    # --- StartStream 前: FPS・解像度・ワークモード ---
    fps = int(cfg['camera'].get('fps', 30))
    cam.scSetFrameRate(c_uint8(fps))

    color_w = int(cfg['camera'].get('color_width', 1600))
    color_h = int(cfg['camera'].get('color_height', 1200))
    cam.scSetColorResolution(c_int32(color_w), c_int32(color_h))
    print(f"color解像度: {color_w}×{color_h}")

    # アクティブモードを明示設定（ScepterGUIToolがトリガーモードで終了した場合の対処）
    ret = cam.scSetWorkMode(ScWorkMode.SC_ACTIVE_MODE)
    if ret != 0:
        print(f"警告: scSetWorkMode failed: {ret}")

    ret = cam.scStartStream()
    if ret != 0:
        cam.scCloseDevice()
        raise RuntimeError(f"scStartStream failed: {ret}")

    # --- StartStream 後: カメラ状態をリセット ---
    # GUITool が有効にしたまま終了した設定を解除する
    cam.scSetTransformDepthImgToColorSensorEnabled(False)
    cam.scSetTransformColorImgToDepthSensorEnabled(False)
    cam.scSetHDRModeEnabled(False)   # HDR有効時はToFが正常に動かない場合がある
    cam.scSetWDRModeEnabled(False)

    # --- フィルタ設定（SDK サンプルの正しい順序）---
    flt = cfg.get('filters', {})

    tf = ScTimeFilterParams()
    tf.threshold = int(flt.get('time_filter', 3))
    tf.enable = True
    cam.scSetTimeFilterParams(tf)

    cf = ScConfidenceFilterParams()
    cf.threshold = int(flt.get('confidence', 15))
    cf.enable = True
    cam.scSetConfidenceFilterParams(cf)

    fp = ScFlyingPixelFilterParams()
    fp.threshold = int(flt.get('flying_pixel', 5))
    fp.enable = True
    cam.scSetFlyingPixelFilterParams(fp)

    cam.scSetSpatialFilterEnabled(bool(flt.get('spatial', False)))

    return cam


def close_camera(cam):
    cam.scStopStream()
    cam.scCloseDevice()


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
