"""1ショットの画像と任意のPLYを、同じ番号で保存する。ROSは不要。"""
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time

import cv2
import numpy as np
from utils import Session


def create_session(base_dir, cam, tag=None):
    # 同じ秒に再撮影しても既存セッションへ上書きしない。
    while True:
        now = datetime.now()
        prefix = f"{cam}_{now:%y%m%d_%H%M%S}"
        directory = Path(base_dir) / now.strftime('%y%m%d') / (f'{prefix}_{tag}' if tag else prefix)
        try:
            directory.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            time.sleep(0.02)
            continue
        return Session(base_dir, cam=cam, tag=tag, started_at=now)


def write_bundle(session, index, images, pointcloud_writer=None):
    """書き込み失敗を検出し、指定した画像・任意のPLYが揃ってから保存先へ移す。"""
    targets = {name: Path(session.path(index, name, ext=ext))
               for name, (_, ext) in images.items()}
    if pointcloud_writer is not None:
        targets['pointcloud'] = Path(session.path(index, 'pointcloud', ext='ply'))
    for path in targets.values():
        if path.exists():
            raise FileExistsError(f'既存データは上書きしません: {path}')
    published = []
    with tempfile.TemporaryDirectory(prefix='.shot-', dir=session.dir) as directory:
        staged = {}
        for name, (image, _) in images.items():
            path = Path(directory) / targets[name].name
            if not cv2.imwrite(str(path), image):
                raise OSError(f'画像を書き込めません: {targets[name]}')
            staged[name] = path
        if pointcloud_writer is not None:
            path = Path(directory) / targets['pointcloud'].name
            pointcloud_writer(str(path))
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError('PLYを書き込めません')
            staged['pointcloud'] = path
        try:
            for name, path in staged.items():
                os.replace(path, targets[name])
                published.append(targets[name])
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            raise
    return {name: str(path.relative_to(session.dir)) for name, path in targets.items()}


def preview_grid(color, depth_colormap, infrared):
    height, width = depth_colormap.shape[:2]
    tiles = [cv2.resize(color, (width, height)), depth_colormap]
    for image in infrared:
        if image is not None:
            if image.dtype != np.uint8:
                image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            tiles.append(cv2.applyColorMap(cv2.resize(image, (width, height)), cv2.COLORMAP_JET))
    if len(tiles) % 2:
        tiles.append(np.zeros_like(depth_colormap))
    return np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)])


class SnapshotReader:
    """別タイミングで届くRGB・深度・IRを保持し、古い組み合わせは保存しない。"""
    def __init__(self, frame_types, fps=30, include_pointcloud=True, require_ir=True):
        self.frame_types = frame_types
        self.include_pointcloud = include_pointcloud
        self.require_ir = require_ir
        self.max_age = max(0.25, 3.0 / fps)
        self.cache = {'ir': None}
        self.updated = {}
        self.streams = {}

    def read(self, camera):
        from ctypes import c_uint16
        from utils import extract_color, extract_depth, extract_ir
        ret, ready = camera.scGetFrameReady(c_uint16(500))
        if ret != 0:
            return None
        specs = [('color', self.frame_types.SC_COLOR_FRAME, extract_color),
                 ('depth', self.frame_types.SC_DEPTH_FRAME, extract_depth),
                 ('ir', self.frame_types.SC_IR_FRAME, extract_ir)]
        for name, kind, extract in specs:
            if not getattr(ready, name):
                continue
            ret, frame = camera.scGetFrame(kind)
            if ret != 0:
                continue
            self.cache[name] = extract(frame)
            if name == 'depth' and self.include_pointcloud:
                ret, points = camera.scConvertDepthFrameToPointCloudVector(frame)
                if ret != 0:
                    raise RuntimeError(f'PLY用の点群変換に失敗しました: {ret}')
                # SDKラッパーが生成した所有バッファ。次のSDKフレームで上書きされない。
                self.cache['points'] = points
            self.updated[name] = time.monotonic()
            self.streams[name] = {'hardwaretimestamp': int(frame.hardwaretimestamp),
                                  'frame_number': int(frame.frameIndex)}
        now = time.monotonic()
        if any(name not in self.updated or now - self.updated[name] > self.max_age
               for name in (('color', 'depth', 'ir') if self.require_ir else ('color', 'depth'))):
            return None
        result = dict(self.cache)
        if not self.require_ir and now - self.updated.get('ir', float('-inf')) > self.max_age:
            result['ir'] = None
        result['streams'] = {key: dict(value) for key, value in self.streams.items() if result.get(key) is not None}
        result['frame_age_ms'] = {key: (now - value) * 1000 for key, value in self.updated.items()
                                  if result.get(key) is not None}
        return result



def save_frame_bundle(session, index, frame, depth_colormap, all_data=False, intrinsics=None):
    """intrinsics（save_intrinsics() の戻り値）を渡すと，PLY の各点にカラー画像の色を付ける。"""
    from utils import save_ply
    images = {'color': (frame['color'], 'jpg'), 'depth': (frame['depth'], 'png'),
              'depth_colormap': (depth_colormap, 'jpg')}
    if frame['ir'] is not None:
        images['ir'] = (frame['ir'], 'png' if all_data else 'jpg')
    if all_data:
        images.update(color_raw=(frame['color'], 'png'),
                      ir_color=(cv2.applyColorMap(frame['ir'], cv2.COLORMAP_JET), 'jpg'))
    writer = (lambda path: save_ply(path, frame['points'], frame['depth'].size,
                                   color=frame['color'], intrinsics=intrinsics)) if all_data else None
    files = write_bundle(session, index, images, writer)
    return {'index': index, 'files': files, 'streams': frame['streams'],
            'frame_age_ms': frame['frame_age_ms'],
            'shapes': {name: list(image.shape) for name, (image, _) in images.items()}}
