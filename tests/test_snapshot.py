import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

SCRIPT_ROOT = Path(__file__).resolve().parents[1] / 'nyx660_script'
sys.path.insert(0, str(SCRIPT_ROOT))
import snapshot
from utils import Session, point_colors, UNKNOWN_COLOR, undistort_points, is_sdk_raw_points, read_ply, write_ply

spec = importlib.util.spec_from_file_location('photo_capture', SCRIPT_ROOT / 'collect/dataset_collect_photo.py')
photo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(photo)


class BundleIOTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = Session(self.temp.name, cam='test')
        self.image = np.arange(12, dtype=np.uint8).reshape(2, 2, 3)

    def test_lossless_images_and_same_shot_number(self):
        def write_ply(path):
            Path(path).write_bytes(b'ply\nformat ascii 1.0\nend_header\n')
        depth = np.array([[0, 1], [1000, 65535]], np.uint16)
        files = snapshot.write_bundle(self.session, 7,
                                      {'color_raw': (self.image, 'png'), 'depth': (depth, 'png')}, write_ply)
        for name, expected in [('color_raw', self.image), ('depth', depth)]:
            path = self.session.dir / files[name]
            self.assertIn('_00007_', path.name)
            np.testing.assert_array_equal(cv2.imread(str(path), cv2.IMREAD_UNCHANGED), expected)
        self.assertIn('_00007_pc.ply', files['pointcloud'])

    def test_image_failure_leaves_no_partial_shot(self):
        with patch.object(snapshot.cv2, 'imwrite', return_value=False):
            with self.assertRaises(OSError):
                snapshot.write_bundle(self.session, 1, {'color': (self.image, 'jpg')}, lambda path: None)
        self.assertFalse(any(p.is_file() for p in self.session.dir.rglob('*')))
        self.assertFalse(list(self.session.dir.glob('.shot-*')))

    def test_ply_failure_leaves_no_partial_shot(self):
        def fail(path):
            raise RuntimeError('PLY conversion failed')
        with self.assertRaises(RuntimeError):
            snapshot.write_bundle(self.session, 1, {'color': (self.image, 'jpg')}, fail)
        self.assertFalse(any(p.is_file() for p in self.session.dir.rglob('*')))

    def test_existing_shot_is_not_overwritten(self):
        old = Path(self.session.path(1, 'color'))
        old.write_bytes(b'original capture')
        with self.assertRaises(FileExistsError):
            snapshot.write_bundle(self.session, 1, {'color': (self.image, 'jpg')}, lambda path: None)
        self.assertEqual(old.read_bytes(), b'original capture')

    def test_sessions_do_not_share_directory(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 1, 1, 0, 0, 0)
        with patch.object(snapshot, 'datetime') as clock, patch.object(snapshot.time, 'sleep'):
            clock.now.side_effect = [now, now, now + timedelta(seconds=1)]
            a = snapshot.create_session(self.temp.name, 'test')
            b = snapshot.create_session(self.temp.name, 'test')
        self.assertNotEqual(a.dir, b.dir)

    def test_preview_keeps_source_images_unmodified(self):
        original = self.image.copy()
        preview = snapshot.preview_grid(self.image, self.image, [np.ones((3, 4), np.uint8)])
        preview[:] = 0
        np.testing.assert_array_equal(self.image, original)


from ctypes import Structure, c_float, c_uint8, POINTER
from types import SimpleNamespace, ModuleType


class Point(Structure):
    _fields_ = [('x', c_float), ('y', c_float), ('z', c_float)]


FRAME_TYPES = SimpleNamespace(SC_COLOR_FRAME=3, SC_DEPTH_FRAME=0, SC_IR_FRAME=1)


def make_frame(image, number=42):
    image = np.ascontiguousarray(image)
    data = (c_uint8 * image.nbytes).from_buffer_copy(image.tobytes())
    return SimpleNamespace(width=image.shape[1], height=image.shape[0], dataLen=image.nbytes,
                           pFrameData=__import__('ctypes').cast(data, POINTER(c_uint8)),
                           hardwaretimestamp=1000000 + number, frameIndex=number, _buffer=data)


class FakeCamera:
    def __init__(self):
        self.color = np.arange(36, dtype=np.uint8).reshape(3, 4, 3)
        self.depth = np.array([[1000, 0], [2000, 65535]], np.uint16)
        self.ir = np.arange(4, dtype=np.uint8).reshape(2, 2)
        self.frames = {3: make_frame(self.color), 0: make_frame(self.depth), 1: make_frame(self.ir)}
        self.ready = SimpleNamespace(color=True, depth=True, ir=True)
        self.conversion_status = 0
        self.stopped = self.closed = False
        self.points = (Point * 4)(Point(100, 200, 1000), Point(0, 0, 0),
                                  Point(200, 300, 2000), Point(0, 0, 65535))

    def scGetFrameReady(self, timeout):
        return 0, self.ready

    def scGetFrame(self, kind):
        return 0, self.frames[kind]

    def scConvertDepthFrameToPointCloudVector(self, frame):
        return self.conversion_status, self.points

    def scGetSensorIntrinsicParameters(self, sensor):
        return 0, SimpleNamespace(fx=100., fy=100., cx=1., cy=1., **{k: 0. for k in ['k1','k2','k3','k4','k5','k6','p1','p2']})

    def scGetSensorExtrinsicParameters(self):
        return 0, SimpleNamespace(rotation=[1,0,0,0,1,0,0,0,1], translation=[0,0,0])

    def scStopStream(self):
        self.stopped = True

    def scCloseDevice(self):
        self.closed = True


class ReaderTests(unittest.TestCase):
    def test_separate_arrivals_and_stale_frames(self):
        camera = FakeCamera()
        reader = snapshot.SnapshotReader(FRAME_TYPES)
        now = 0
        with patch.object(snapshot.time, 'monotonic', side_effect=lambda: now):
            camera.ready = SimpleNamespace(color=True, depth=False, ir=False)
            self.assertIsNone(reader.read(camera))
            # SDKの次フレームでバッファが再利用されてもキャッシュのRGBは変わらない。
            camera.frames[3]._buffer[0] = 250
            now = .05
            camera.ready = SimpleNamespace(color=False, depth=True, ir=False)
            self.assertIsNone(reader.read(camera))
            now = .1
            camera.ready = SimpleNamespace(color=False, depth=False, ir=True)
            bundle = reader.read(camera)
            np.testing.assert_array_equal(bundle['color'], camera.color)
            np.testing.assert_array_equal(bundle['depth'], camera.depth)
            now = 1
            self.assertIsNone(reader.read(camera))

    def test_point_conversion_failure_is_reported(self):
        camera = FakeCamera()
        camera.conversion_status = -1
        with self.assertRaises(RuntimeError):
            snapshot.SnapshotReader(FRAME_TYPES).read(camera)


class CaptureTests(unittest.TestCase):
    def run_capture(self, camera, failed_write=False, once=True, all_data=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cfg = {'camera': {'color_width': 4, 'color_height': 3, 'fps': 30, 'depth_alpha': .4},
               'output': {'images_dir': temp.name}, 'model': {}}
        api = ModuleType('API')
        enums = ModuleType('API.ScepterDS_enums')
        enums.ScFrameType = FRAME_TYPES
        enums.ScSensorType = SimpleNamespace(SC_TOF_SENSOR=0, SC_COLOR_SENSOR=1)
        with patch.dict(sys.modules, {'API': api, 'API.ScepterDS_enums': enums}), \
             patch.object(photo, 'load_config', return_value=cfg), \
             patch.object(photo, 'init_sdk'), patch.object(photo, 'open_camera', return_value=camera), \
             patch.object(sys, 'argv', ['capture', '--warmup', '0'] + (['--all-data'] if all_data else []) + (['--once', '--no-preview'] if once else [])):
            if failed_write:
                with patch.object(snapshot.cv2, 'imwrite', return_value=False):
                    status = photo.main()
            else:
                status = photo.main()
        metadata_path = next(Path(temp.name).rglob('metadata.json'))
        return status, json.loads(metadata_path.read_text()), metadata_path.parent

    def test_single_capture_saves_raw_color_depth_ir_and_ply(self):
        camera = FakeCamera()
        status, metadata, directory = self.run_capture(camera)
        self.assertEqual(status, 0)
        self.assertTrue(camera.stopped and camera.closed)
        self.assertEqual(metadata['shot_count'], 1)
        files = metadata['frames'][0]['files']
        self.assertEqual(set(files), {'color', 'color_raw', 'depth', 'depth_colormap', 'ir', 'ir_color', 'pointcloud'})
        for name, image in [('color_raw', camera.color), ('depth', camera.depth), ('ir', camera.ir)]:
            np.testing.assert_array_equal(cv2.imread(str(directory / files[name]), cv2.IMREAD_UNCHANGED), image)
        self.assertTrue((directory / 'intrinsics.json').is_file())
        self.assertEqual(metadata['ply_format']['unit'], 'm')
        self.assertTrue(metadata['ply_format']['color'])
        header, data = (directory / files['pointcloud']).read_bytes().split(b'end_header\n')
        self.assertIn(b'property uchar red', header)
        vertices = np.frombuffer(data, dtype=[('xyz', '<f4', 3), ('rgb', 'u1', 3)])
        np.testing.assert_allclose(vertices['xyz'], [[.1, -.2, -1], [.2, -.3, -2]])
        # 模擬カメラの点はカラー画像（4x3）の外に写るので，色は「不明」の灰色
        np.testing.assert_array_equal(vertices['rgb'], [UNKNOWN_COLOR] * 2)
        self.assertEqual(metadata['frames'][0]['streams']['depth']['frame_number'], 42)

    def test_failed_image_save_is_not_counted_and_camera_is_closed(self):
        camera = FakeCamera()
        status, metadata, directory = self.run_capture(camera, failed_write=True)
        self.assertEqual(status, 1)
        self.assertEqual(metadata['shot_count'], 0)
        self.assertTrue(metadata['error'])
        self.assertTrue(camera.stopped and camera.closed)
        self.assertFalse(list(directory.rglob('*.ply')))


    def test_manual_save_key_captures_one_complete_shot(self):
        camera = FakeCamera()
        with patch.object(photo.cv2, 'namedWindow'), patch.object(photo.cv2, 'imshow'), \
             patch.object(photo.cv2, 'destroyAllWindows'), \
             patch.object(photo.cv2, 'waitKey', side_effect=[-1, ord('s'), ord('q')]):
            result = self.run_capture(camera, once=False)
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1]['shot_count'], 1)
        self.assertIn('pointcloud', result[1]['frames'][0]['files'])


    def test_normal_capture_skips_point_conversion_and_ply(self):
        camera = FakeCamera()
        with patch.object(camera, 'scConvertDepthFrameToPointCloudVector',
                          side_effect=AssertionError('point conversion must not run')) as convert:
            status, metadata, directory = self.run_capture(camera, all_data=False)
        self.assertEqual(status, 0)
        self.assertEqual(metadata['capture_mode'], 'images')
        self.assertNotIn('ply_format', metadata)
        self.assertEqual(set(metadata['frames'][0]['files']), {'color', 'depth', 'depth_colormap', 'ir'})
        self.assertFalse((directory / 'pointcloud').exists())
        self.assertFalse(list(directory.rglob('*.ply')))
        convert.assert_not_called()

    def test_normal_capture_allows_images_when_ir_is_not_ready(self):
        camera = FakeCamera()
        camera.ready.ir = False
        status, metadata, _, = self.run_capture(camera, all_data=False)
        self.assertEqual(status, 0)
        self.assertNotIn('ir', metadata['frames'][0]['files'])



if __name__ == '__main__':
    unittest.main()


class PointColorTests(unittest.TestCase):
    INTR = {'tof': {'fx': 10., 'fy': 10., 'cx': 5., 'cy': 5., **{k: 0. for k in ['k1', 'k2', 'k3', 'k4', 'k5', 'k6', 'p1', 'p2']}},
            'color': {'fx': 10., 'fy': 10., 'cx': 5., 'cy': 5., **{k: 0. for k in ['k1', 'k2', 'k3', 'p1', 'p2']}},
            'extrinsics': {'rotation': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'translation': [100., 0, 0]}}

    def setUp(self):
        self.color = np.zeros((10, 10, 3), np.uint8)
        self.color[5, 6] = (255, 0, 0)    # BGR の青
        self.color[5, 5] = (0, 0, 255)    # BGR の赤

    def test_point_is_moved_to_color_camera_and_gets_rgb(self):
        # x=0 の点は，カラーカメラから見ると 100mm 右にずれて u=5+10*100/1000=6 に写る
        rgb = point_colors([[0, 0, 1000]], self.color, self.INTR)
        np.testing.assert_array_equal(rgb, [[0, 0, 255]])

    def test_aligned_color_uses_tof_pixels(self):
        rgb = point_colors([[0, 0, 1000]], self.color, self.INTR, aligned=True)
        np.testing.assert_array_equal(rgb, [[255, 0, 0]])

    def test_hidden_and_outside_points_are_unknown(self):
        # 2点目は1点目と同じ画素に写るがずっと奥 → 隠れている．3点目は画像の外
        rgb = point_colors([[0, 0, 1000], [100, 0, 2000], [5000, 0, 1000]], self.color, self.INTR)
        np.testing.assert_array_equal(rgb, [[0, 0, 255], UNKNOWN_COLOR, UNKNOWN_COLOR])


class UndistortTests(unittest.TestCase):
    TOF = {'fx': 500., 'fy': 500., 'cx': 320., 'cy': 240., 'k1': -0.1, 'k2': 0.05, 'p1': 0., 'p2': 0.,
           'k3': 0., 'k4': 0., 'k5': 0., 'k6': 0.}

    def sdk_points(self):
        # SDK と同じく，画素を歪みなしのピンホールで 3D に戻した点
        u, v = np.meshgrid(np.arange(0, 640, 40.), np.arange(0, 480, 40.))
        z = np.full(u.size, 2000.)
        return np.c_[(u.ravel() - 320) / 500 * z, (v.ravel() - 240) / 500 * z, z]

    def test_undistort_matches_opencv_and_keeps_depth(self):
        pts = self.sdk_points()
        fixed = undistort_points(pts, self.TOF)
        np.testing.assert_array_equal(fixed[:, 2], pts[:, 2])
        pix = (pts[:, :2] / pts[:, 2:] * 500 + [320, 240]).reshape(-1, 1, 2)
        K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1.]])
        ray = cv2.undistortPoints(pix, K, np.array([-0.1, 0.05, 0, 0, 0, 0, 0, 0.])).reshape(-1, 2)
        np.testing.assert_allclose(fixed[:, :2], ray * 2000, atol=1e-6)
        # 中心は動かず，端ほど動く
        center = np.argmin(np.abs(pix.reshape(-1, 2) - [320, 240]).sum(1))
        np.testing.assert_allclose(fixed[center], pts[center])
        self.assertGreater(np.abs(fixed - pts).max(), 10)

    def test_raw_points_are_detected_so_undistort_runs_once(self):
        pts = self.sdk_points()
        self.assertTrue(is_sdk_raw_points(pts, self.TOF))
        self.assertFalse(is_sdk_raw_points(undistort_points(pts, self.TOF), self.TOF))

    def test_colors_do_not_change_after_undistort(self):
        intr = {'tof': self.TOF,
                'color': {'fx': 500., 'fy': 500., 'cx': 320., 'cy': 240., 'k1': 0., 'k2': 0., 'p1': 0., 'p2': 0., 'k3': 0.},
                'extrinsics': {'rotation': [1, 0, 0, 0, 1, 0, 0, 0, 1], 'translation': [30., 0, 0]}}
        color = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
        pts = self.sdk_points()
        before = point_colors(pts, color, intr)
        after = point_colors(undistort_points(pts, self.TOF), color, intr, undistorted=True)
        np.testing.assert_array_equal(before, after)
        before = point_colors(pts, color, intr, aligned=True)
        after = point_colors(undistort_points(pts, self.TOF), color, intr, aligned=True, undistorted=True)
        np.testing.assert_array_equal(before, after)

    def test_ply_round_trip_with_and_without_color(self):
        with tempfile.TemporaryDirectory() as d:
            pts = np.array([[1, 2, 3], [4, 5, 6]], np.float32)
            rgb = np.array([[1, 2, 3], [250, 251, 252]], np.uint8)
            write_ply(Path(d) / 'a.ply', pts)
            write_ply(Path(d) / 'b.ply', pts, rgb)
            p, c = read_ply(Path(d) / 'a.ply')
            np.testing.assert_array_equal(p, pts); self.assertIsNone(c)
            p, c = read_ply(Path(d) / 'b.ply')
            np.testing.assert_array_equal(p, pts); np.testing.assert_array_equal(c, rgb)
