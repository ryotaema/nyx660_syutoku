"""画像を[s]で保存する。--all-dataを付けるとPLY等もまとめて保存する。"""
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from utils import (load_config, build_parser, apply_args, init_sdk, open_camera, close_camera,
                   make_depth_colormap, save_intrinsics, camera_metadata, PLY_FORMAT)
from snapshot import create_session, SnapshotReader, save_frame_bundle, preview_grid


def main():
    parser = build_parser()
    parser.add_argument('--all-data', action='store_true', help='PLYとロスレス画像も含めて全データを保存')
    parser.add_argument('--once', action='store_true', help='1枚保存して終了')
    parser.add_argument('--warmup', type=float, default=None, help='露光安定待ち時間（秒、通常0・all-dataは2）')
    parser.add_argument('--timeout', type=float, default=10.0, help='必要なフレームの取得待ち上限（秒）')
    parser.add_argument('--no-preview', action='store_true', help='--once時に画面を表示しない')
    args = parser.parse_args()
    if args.warmup is None:
        args.warmup = 2.0 if args.all_data else 0.0
    if args.warmup < 0 or args.timeout <= 0 or (args.no_preview and not args.once):
        parser.error('warmupは0以上、timeoutは正の値、no-previewはonceと併用してください')
    cfg = apply_args(load_config(), args)
    camera = session = None
    frames_meta = []
    metadata = {'shot_count': 0, 'frames': frames_meta,
                'capture_mode': 'all_data' if args.all_data else 'images'}
    error = None
    try:
        if not args.no_preview:
            cv2.namedWindow('NYX660', cv2.WINDOW_AUTOSIZE)
            cv2.waitKey(1)
        init_sdk(cfg)
        from API.ScepterDS_enums import ScFrameType
        camera = open_camera(cfg)
        session = create_session(cfg['output']['images_dir'], 'nyx', args.tag)
        save_intrinsics(camera, str(session.dir))
        preset = cfg['camera'].get('params_json')
        if args.all_data and preset:
            shutil.copyfile(preset, session.dir / 'camera_parameters.json')
        settings = dict(cfg['camera'])
        if preset:
            settings['params_json'] = 'camera_parameters.json'
        if args.all_data:
            (session.dir / 'camera_settings.json').write_text(json.dumps(settings, indent=2))
        metadata.update(camera=camera_metadata(camera, cfg),
                        depth_aligned_to=None, unavailable_modalities=['ir_right'],
                        frame_pairing='latest RGB/depth/IR within max_frame_age_ms; see hardware timestamps')
        if args.all_data:
            metadata['ply_format'] = dict(PLY_FORMAT, color=False)
        reader = SnapshotReader(ScFrameType, cfg['camera'].get('fps', 30), include_pointcloud=args.all_data, require_ir=args.all_data)
        metadata['max_frame_age_ms'] = reader.max_age * 1000
        alpha = cfg['camera'].get('depth_alpha', 0.4)
        print(f'保存先: {session.dir}', flush=True)
        label = '全データ（PLY含む）' if args.all_data else '画像（PLYなし）'
        print(f'{label}を保存します。[s] 保存  [q] 終了', flush=True)
        begin = last_frame = time.monotonic()
        while True:
            frame = reader.read(camera)
            key = -1
            if frame is not None:
                last_frame = time.monotonic()
                depth_cm = make_depth_colormap(frame['depth'], alpha)
                if not args.no_preview:
                    preview = preview_grid(frame['color'], depth_cm, [frame['ir']])
                    cv2.putText(preview, f'[s] Save {"all" if args.all_data else "images"}  [q] Quit   Saved: {len(frames_meta)}',
                                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                    cv2.imshow('NYX660', preview)
            if not args.no_preview:
                key = cv2.waitKey(1) & 0xff
            if key == ord('q'):
                break
            if frame is None:
                if time.monotonic() - last_frame > args.timeout:
                    raise RuntimeError('RGB・深度・IRが揃いません。接続・ワークモード・FPSを確認してください。')
                continue
            if time.monotonic() - begin < args.warmup:
                continue
            if args.once or key == ord('s'):
                record = save_frame_bundle(session, len(frames_meta) + 1, frame, depth_cm, all_data=args.all_data)
                record['saved_at'] = datetime.now(timezone.utc).isoformat()
                frames_meta.append(record)
                metadata.update(camera=camera_metadata(camera, cfg,
                                                       resolution=[frame['color'].shape[1], frame['color'].shape[0]]),
                                modalities=list(record['files']), shot_count=len(frames_meta))
                session.write_metadata(**metadata)
                print(f'[{len(frames_meta)}枚目] {label}の保存完了', flush=True)
                if args.once:
                    break
    except Exception as exc:  # CLIの境界で失敗を記録し、カメラはfinallyで解放する。
        error = f'{type(exc).__name__}: {exc}'
        print(f'エラー: {exc}', file=sys.stderr, flush=True)
    finally:
        try:
            if session is not None:
                session.write_metadata(**metadata, error=error)
        finally:
            try:
                if camera is not None:
                    close_camera(camera)
            finally:
                if not args.no_preview:
                    cv2.destroyAllWindows()
    return 1 if error else 0


if __name__ == '__main__':
    sys.exit(main())
