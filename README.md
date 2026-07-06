# nyx660_syutoku

Vzense NYX660（3D ToF + RGB カメラ）を用いた学習データ収集スクリプト群。

## ディレクトリ構成

```
nyx660_syutoku/
├── nyx660_script/
│   ├── config.yaml
│   ├── utils.py
│   ├── collect/
│   │   ├── dataset_collect.py        # 連続収録（Enter 開始 / q 停止）
│   │   ├── dataset_collect_photo.py  # 1ショット（s 保存 / q 終了）
│   │   ├── dataset_point_collect.py  # 点群収集（auto / manual）
│   │   └── timelapse_detect.py       # タイムラプス定期撮影（+ YOLO検出オプション）
│   ├── detect/
│   │   └── yolo_detection.py         # YOLO リアルタイム検出（ToF 距離表示付き）
│   ├── record/
│   │   ├── mp4_collect.py            # MP4 録画（color + depth colormap）
│   │   └── record_with_yolo.py       # MP4 録画（YOLO + ToF 距離オーバーレイ）
│   ├── process/
│   │   ├── point_merge.py            # 点群 ICP 位置合わせ・マージ
│   │   └── timelapse_analysis.py     # タイムラプスログのグラフ生成
│   └── click_script/
│       ├── bbox_click.py             # 画像に YOLO 形式 BBox を手動アノテーション
│       └── click_dataset.py          # カメラ映像をクリックして座標＋画像を保存
└── data/
    ├── images/           # 収集画像
    ├── pointcloud/       # 点群データ
    ├── timelapse_data/   # タイムラプスセッション
    └── mp4/              # 録画動画
```

## セットアップ

### 必要なパッケージ

```bash
# 基本
pip install opencv-python numpy pyyaml

# YOLO 系スクリプトを使う場合
pip install ultralytics

# process/point_merge.py を使う場合
pip install open3d

# process/timelapse_analysis.py を使う場合
pip install matplotlib
```

### SDK

[Vzense ScepterSDK](https://github.com/ScepterSW/ScepterSDK) を **Ubuntu 機で直接 clone** する（別 OS からのコピーは `.so` が壊れる）。

```bash
git clone https://github.com/ScepterSW/ScepterSDK
```

> **注意**: SDK v26.03 以降が必要。旧バージョン（v24.12 以前）では depth/IR フレームが取得できない。

### ネットワーク設定

| 項目 | 値 |
|------|-----|
| カメラ IP | 192.168.1.101（デフォルト） |
| PC 側 IP | 192.168.1.100 / 255.255.255.0 |
| リンク速度 | **ギガビット必須**（LAN ランプ黄=GbE / 緑=100M） |

### config.yaml の編集

`nyx660_script/config.yaml` の `sdk_path` を実際の SDK の場所に合わせて変更する（相対パス可）。

```yaml
sdk_path: ../../ScepterSDK
```

## 設定ファイル

`nyx660_script/config.yaml` で全スクリプトの設定を一元管理。パスは config.yaml からの相対パスで記述できる。

```yaml
sdk_path: ../../ScepterSDK

camera:
  fps: 30
  depth_alpha: 0.4   # depth colormap の倍率（大きいほど近距離で飽和）
  color_width: 1600  # color 解像度（1600x1200 / 800x600 / 640x480 など）
  color_height: 1200

output:
  images_dir: ../data/images
  pointcloud_dir: ../data/pointcloud
  timelapse_dir: ../data/timelapse_data
  mp4_dir: ../data/mp4

pointcloud:
  capture_frames: 50   # auto モードのデフォルトフレーム数
  voxel_size: 0.005    # ICP ダウンサンプリング解像度 (m)
  icp_threshold: 0.02  # ICP 最大対応点距離 (m)

filters:
  time_filter: 3    # [1,3]  大きいほどジッタ減
  confidence: 15    # [1,100]
  flying_pixel: 5   # [1,16]
  spatial: false

model:
  yolo_path: model/best.pt   # YOLO モデルファイル（config.yaml からの相対パス）
  confidence_threshold: 0.5
```

## CLI オプション

全スクリプト共通のオプション：

| オプション | 説明 | 例 |
|---|---|---|
| `--fps N` | FPS を上書き | `--fps 15` |
| `--color-size WxH` | color 解像度を上書き | `--color-size 800x600` |

スクリプト固有のオプション：

| オプション | 対象スクリプト | 説明 |
|---|---|---|
| `--frames N` | dataset_point_collect | auto モードのフレーム数 |
| `--mode auto\|manual` | dataset_point_collect | 取得モード |
| `--sequential` | point_merge | ICP を前フレーム順に適用 |
| `--voxel-size F` | point_merge | ダウンサンプリング解像度 (m) |
| `--icp-threshold F` | point_merge | ICP 最大対応点距離 (m) |
| `--interval N` | timelapse_detect | 撮影間隔（秒） |
| `--duration H` | timelapse_detect | 継続時間（時間） |
| `--detect` | timelapse_detect | YOLO 検出ログを有効化 |
| `--relative-depth` | timelapse_detect | 深度カラーマップを相対値で正規化 |

## 使い方

各スクリプトはそれぞれのディレクトリから実行する。

### collect/ — データ収集

```bash
cd nyx660_script/collect

# 連続収録（Enter で開始、q で停止）
python3 dataset_collect.py
python3 dataset_collect.py --color-size 800x600

# 1ショット収録（s で保存、q で終了）
python3 dataset_collect_photo.py

# 点群収集
python3 dataset_point_collect.py               # auto 50フレーム
python3 dataset_point_collect.py --frames 100  # フレーム数指定
python3 dataset_point_collect.py --mode manual # s で1枚ずつ

# タイムラプス（5分間隔・12時間）
python3 timelapse_detect.py --interval 300 --duration 12.0
# YOLO 検出ログ付き（起動時にモデルを GUI で選択）
python3 timelapse_detect.py --detect
```

### detect/ — YOLO リアルタイム検出

```bash
cd nyx660_script/detect
python3 yolo_detection.py
```

ToF 深度を利用して各検出 BBox に距離（X.XXm）を表示する。モデルは `config.yaml` の `model.yolo_path` で指定。

### record/ — MP4 録画

```bash
cd nyx660_script/record

# color + depth colormap の MP4 録画（Enter 開始、q 停止）
python3 mp4_collect.py

# YOLO 検出 + 距離オーバーレイの MP4 録画
python3 record_with_yolo.py
```

### process/ — 後処理

```bash
cd nyx660_script/process

# 点群マージ（省略時は最新セッションを自動選択）
python3 point_merge.py
python3 point_merge.py <session_dir>
python3 point_merge.py <session_dir> --sequential

# タイムラプスログのグラフ生成（analysis.png を出力）
python3 timelapse_analysis.py
python3 timelapse_analysis.py <session_dir>
```

### click_script/ — アノテーション補助

```bash
cd nyx660_script/click_script

# カメラ映像をクリックして座標＋画像を保存（s 保存、q 終了）
python3 click_dataset.py

# 既存画像に YOLO 形式 BBox をアノテーション（d/a で移動、s で保存）
python3 bbox_click.py
```

## 保存形式

### 画像収集（dataset_collect / dataset_collect_photo）

```
images_dir/YYYY_MMDD/imageN_YYYY-MM-DD_HHMMSS_NYX660/
├── color/           {n}_color.jpg            # RGB（デフォルト 1600×1200）
├── depth/           {n}_depth.png            # 生深度 16bit PNG（640×480、mm単位）
├── depth_colormap/  {n}_depth_colormap.jpg   # 深度可視化
└── ir/              {n}_ir.jpg               # IR グレースケール（640×480）
```

### 点群収集（dataset_point_collect）

```
pointcloud_dir/YYYY_MMDD/pcN_YYYY-MM-DD_HHMMSS_NYX660/
├── 0000_color.jpg
├── 0000_depth.png        # 16bit PNG（mm単位）
├── 0000_pointcloud.ply   # バイナリ PLY（z=0 / z=65535 除外済み）
├── intrinsics.json       # ToF / Color 内部パラメータ + 外部パラメータ
└── metadata.json         # 取得モード・フレーム数・hardwaretimestamp 一覧
```

### タイムラプス（timelapse_detect）

```
timelapse_data/YYYY_MMDD/timelapseN_YYYY-MM-DD_HHMMSS_NYX660/
├── color/       {n}_color.jpg
├── depth/       {n}_depth.png  /  {n}_depth_colormap.jpg
├── ir/          {n}_ir.jpg
├── annotated/   {n}_annotated.jpg   # --detect 時のみ（距離付きBBox）
└── detection_log.csv                # --detect 時のみ
```

`detection_log.csv` の列: `timestamp, elapsed_min, num_detections, avg_conf, max_conf, avg_depth_m, classes`

### MP4 録画（mp4_collect）

```
mp4_dir/
├── color_YYYYMMDD_HHMMSS.mp4   # RGB 動画
└── depth_YYYYMMDD_HHMMSS.mp4   # depth colormap 動画（640×480）
```

### YOLO 録画（record_with_yolo）

```
mp4_dir/
└── detected_YYYYMMDD_HHMMSS.mp4   # YOLO + 距離オーバーレイ動画（640×480）
```

## カメラ仕様（NYX660）

| 項目 | 値 |
|------|-----|
| 深度解像度 | 640×480 @最大30fps |
| 深度範囲 | 0.3〜4.5m |
| 深度フォーマット | 16bit（mm単位） |
| RGB 解像度 | 1600×1200（変更可） |
| IR | 1ch（640×480） |
| 接続 | GigE（PoE+） |

## SDK について

- SDK リポジトリ: https://github.com/ScepterSW/ScepterSDK
- API リファレンス: https://wiki.vzense.com

## ライセンス

このリポジトリは [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE) のもとで公開されています。

本プロジェクトは [Ultralytics YOLO](https://github.com/ultralytics/ultralytics)（AGPL-3.0）を使用しているため、AGPL-3.0 に従い同ライセンスを適用しています。

### サードパーティライセンス

本プロジェクトは以下のライブラリを使用しています。

| ライブラリ | ライセンス |
|---|---|
| [ScepterSDK](https://github.com/ScepterSW/ScepterSDK) | BSD 3-Clause（Copyright © 2024 Scepter Software） |
| [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) | AGPL-3.0 |
| [Open3D](https://github.com/isl-org/Open3D) | MIT |
| [OpenCV](https://github.com/opencv/opencv) | Apache 2.0 |
| [NumPy](https://github.com/numpy/numpy) | BSD 3-Clause |
| [PyYAML](https://github.com/yaml/pyyaml) | MIT |
| [Matplotlib](https://github.com/matplotlib/matplotlib) | PSF-based（BSD 互換） |
