"""YOLO の薄いラッパー（画像 → core.tracker.Detection のリスト）。

モデルと信頼度閾値は既定で config.yaml の model.yolo_path / model.confidence_threshold を使う
（tools/param_tune/detect_eval.py と同じ）。

CachedDetector は同じ画像に対する推論結果を再利用する（オフライン再現では同じスイープ画像を
何度も読むため）。キャッシュをファイルに保存すれば次回以降の実行も速い。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'nyx660_script'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.tracker import Detection  # noqa: E402


class YoloDetector:
    def __init__(self, model_path=None, conf=None):
        from ultralytics import YOLO
        from utils import load_config
        model_cfg = load_config().get('model', {})
        self.model_path = str(model_path or (ROOT / 'nyx660_script' / model_cfg.get('yolo_path', 'model/best.pt')).resolve())
        self.conf = float(conf if conf is not None else model_cfg.get('confidence_threshold', 0.5))
        if not Path(self.model_path).exists():
            raise FileNotFoundError(f'YOLOモデルがありません: {self.model_path}')
        self.model = YOLO(self.model_path)

    def detect(self, image_bgr):
        res = self.model(image_bgr, conf=self.conf, verbose=False)[0]
        if res.boxes is None or len(res.boxes) == 0:
            return []
        boxes = res.boxes.xyxy.cpu().numpy().tolist()
        confs = res.boxes.conf.cpu().numpy().tolist()
        return [Detection(box=tuple(b), conf=float(c)) for b, c in zip(boxes, confs)]


class CachedDetector:
    """key（画像ファイル名など）ごとに検出結果を保持する。cache_path を渡すと JSON に保存する。"""

    def __init__(self, detector, cache_path=None):
        self.detector = detector
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache = {}
        if self.cache_path and self.cache_path.exists():
            meta = json.loads(self.cache_path.read_text())
            if meta.get('model') == detector.model_path and meta.get('conf') == detector.conf:
                self._cache = {k: [Detection(box=tuple(d['box']), conf=d['conf']) for d in v]
                               for k, v in meta['detections'].items()}

    def detect(self, image_bgr, key):
        if key not in self._cache:
            self._cache[key] = self.detector.detect(image_bgr)
        return self._cache[key]

    def save(self):
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps({
            'model': self.detector.model_path, 'conf': self.detector.conf,
            'detections': {k: [{'box': list(d.box), 'conf': d.conf} for d in v] for k, v in self._cache.items()},
        }))
