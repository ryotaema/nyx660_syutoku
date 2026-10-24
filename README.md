# nyx660_syutoku

Vzense NYX660（3D ToF + RGB カメラ）を用いた学習データ収集スクリプト群。

## ディレクトリ構成

```
nyx660_syutoku/
├── nyx660_script/
│   ├── config.yaml
│   ├── utils.py
│   ├── gui.py                        # 統合GUI（各スクリプトをボタンで起動）
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
├── tools/
│   ├── rename_legacy.py     # 旧命名データを新命名規則へ変換（CLI）
│   ├── rename_legacy_gui.py # 同上（GUI）
│   └── convert_ply_to_m.py  # 旧形式（mm）の点群 PLY を m・RealSense Viewer 座標系へ変換
└── data/
    ├── images/           # 収集画像（YYMMDD/nyx_YYMMDD_HHMMSS/）
    ├── pointcloud/       # 点群データ（YYMMDD/nyx_YYMMDD_HHMMSS/）
    ├── timelapse_data/   # タイムラプスセッション（YYMMDD/nyx_YYMMDD_HHMMSS/）
    └── mp4/              # 録画動画
```

## セットアップ

PythonとメーカーSDKを使ってカメラへ直接接続します．ROSの導入やROS2ドライバのビルドは不要です．
Dockerを使う場合は，下記「Docker環境内で利用する場合」を参照してください．

### 必要なパッケージ

リポジトリのルートで実行します．

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

SDK本体はこのリポジトリに含まれていません．
[Vzense ScepterSDK](https://github.com/ScepterSW/ScepterSDK) をUbuntu上で別途取得します．
例えば，このリポジトリとSDKを次のように並べて配置できます．親ディレクトリの場所は任意です．

```text
任意の親ディレクトリ/
├── ScepterSDK/
└── nyx660_syutoku/
    └── nyx660_script/
        └── config.yaml
```

上記の親ディレクトリで実行します．既にSDKが配置されている場合は再clone不要です．

**このリポジトリと同じ階層**（同じ親フォルダの中）に置く。`config.yaml` の `sdk_path` の既定値
（`../../ScepterSDK`）はこの配置を前提にしているので、この通りに置けば設定の変更は不要。

```
<作業フォルダ>/            # 例: ~/annot_labelimg/Tof_camera/nyx660/
├── nyx660_syutoku/        # このリポジトリ
│   └── nyx660_script/
│       └── config.yaml    # sdk_path: ../../ScepterSDK → <作業フォルダ>/ScepterSDK
└── ScepterSDK/            # ← ここに clone
```

```bash
git clone https://github.com/ScepterSW/ScepterSDK.git ScepterSDK
```

> **注意**: SDK v26.03 以降が必要。旧バージョン（v24.12 以前）では depth/IR フレームが取得できない。

### ネットワーク設定

| 項目 | 値 |
|------|-----|
| カメラ IP | 192.168.1.101（デフォルト） |
| PC 側 IP | 192.168.1.100 / 255.255.255.0 |
| リンク速度 | **ギガビット必須**（LAN ランプ黄=GbE / 緑=100M） |

実際のカメラのネットワーク設定に合わせ，PCからカメラへ疎通できるように設定します．

### config.yaml の編集

既定の `sdk_path` は，上記のようにSDKをリポジトリと並べた場合の指定です．
異なる配置では `nyx660_script/config.yaml` の値を変更するか，環境変数で上書きしてください．

```yaml
sdk_path: ../../ScepterSDK
```

環境変数での指定例です．

```bash
export SCEPTER_SDK_PATH=../../ScepterSDK
```

環境変数が `config.yaml` より優先されます．どちらも相対パスの基準は
`nyx660_script/config.yaml` のあるディレクトリで，コマンドを実行する場所ではありません．

### Docker環境内で利用する場合（任意）

Dockerを使う場合は，Python依存とSDKをコンテナ内から利用できるようにします．
ホストにインストールしたPythonパッケージはコンテナには引き継がれません．
SDKもマウント範囲内に置き，`sdk_path` または `SCEPTER_SDK_PATH` で指定してください．

用意済みのHumbleベースのDocker環境を使う場合も，このスクリプトのために
ROS2ドライバをビルドする必要はありません．
共有SDKを利用する構成の例は次のとおりです．SDKの取得方法は
[共有SDKの配置手順](../../vendor/README.md#別pcでsdkを準備する) を参照してください．

```text
ros2_ws/
├── vendor/ScepterSDK_hr/
└── workspaces/nyx660_syutoku/
```

Docker構成ディレクトリから，起動時と同じCompose設定でコンテナに入ります．
以下はCPU構成の例です．NVIDIA構成では
`docker compose -f compose.yaml -f compose.nvidia.yaml exec humble bash` を使います．

```bash
docker compose exec humble bash
```

コンテナ内のワークスペースルートから実行します．
共有SDKの指定はこのシェル内でのみ有効で，通常の `config.yaml` は変更しません．

```bash
export SCEPTER_SDK_PATH=../../../vendor/ScepterSDK_hr
cd workspaces/nyx660_syutoku
python3 nyx660_script/collect/dataset_collect_photo.py
```

用意済みのDockerイメージにはPythonの基本依存が含まれています．
独自のイメージでは「必要なパッケージ」をコンテナ内へ導入してください．
`network_mode: host` の構成では，ホスト側で有線LANを設定してカメラへ疎通させます．

## 設定ファイル

`nyx660_script/config.yaml` で全スクリプトの設定を一元管理。パスは config.yaml からの相対パスで記述できる。

```yaml
sdk_path: ../../ScepterSDK

camera:
  fps: 30
  depth_alpha: 0.4   # depth colormap の倍率（大きいほど近距離で飽和）
  color_width: 1600   # color 解像度（このカメラは640x480/1600x1200の2モードのみ対応。1280x960等は不可）
  color_height: 1200  # 将来のRGB-D融合を見据え depth(640x480) より高解像度で撮影する方針

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

## 使い方（GUI）

はじめての人はまず GUI から使ってください（real_syutoku の GUI と同じ画面構成です）．

```bash
python3 nyx660_script/gui.py
```

1. 上の「カメラ」に検出された NYX660（シリアル・IP）が表示されます．出ないときは LAN 接続を確認して「再検出」．
2. やりたいことのタブ（画像・点群・動画・タイムラプス・推論・アノテーション）を開き，ボタンを押します．
3. カメラ映像のウィンドウが開いたら，ボタン横の「操作」に書かれたキーで保存・終了します．
4. 途中でやめたいときは「停止」を押します（データは保存されたまま安全に終了します）．
5. 「保存先を開く」で，最後に使った機能の保存フォルダが開きます．

GUI は下記のスクリプトをボタンで起動しているだけなので，コマンドラインからの使い方も今までどおりです．
細かいオプション（FPS・color 解像度など）を変えたいときはコマンドラインか `config.yaml` を使ってください．

### RGB・深度・IR・PLYの一括取得

「画像」タブの **全データを1枚取得** は，約2秒の露光安定待ちの後に1ショット分を保存して終了します．
**全データを1枚ずつ撮る** はプレビューを開き，`s` を押すたびに同じ組のデータを保存します（`q` で終了）．
従来の `dataset_collect_photo.py` からも，画像とPLYをまとめて保存できます．ROSは不要です．

保存先は `config.yaml` の `output.images_dir` 配下です．各ファイルは同じショット番号を共有します．
RGBは既存の処理用JPEGとロスレスPNGの両方を保存し，深度・IRの生データもPNGで残します．
メタデータの `frames[].files` に，そのショットの各ファイルの相対パスが入ります．

| データ | 保存先 | 内容 |
|---|---|---|
| RGB | `color/*_c.jpg` | 既存の収集画像と同じJPEG |
| RGB（ロスレス） | `color_raw/*_cr.png` | 取得画素を保持したPNG |
| 深度 | `depth/*_d.png` | 16bit PNG．距離は画素値 × `metadata.camera.depth_scale` [m] |
| 深度可視化 | `depth_colormap/*_dc.jpg` | プレビュー用の疑似カラー画像 |
| IR | `ir/*_i1.png` | 単眼IRの8bit PNG |
| IR可視化 | `ir_color/*_i1c.jpg` | IRの疑似カラー画像 |
| 点群 | `pointcloud/*_pc.ply` | 無効深度を除いた点群．単位m，RealSense Viewer座標系（x右・y上・前方−z） |
| カメラ情報 | `intrinsics.json` / `camera_settings.json` | 内部パラメータ・撮影設定 |
| 取得記録 | `metadata.json` | 機種・シリアル番号・保存ファイル・フレーム番号・取得時刻・画像サイズ |

RGB・深度・IRはネイティブの解像度と座標系で保存します．プレビューだけを縮小します．
PLYはSDKが深度から変換したXYZ点群で，RGBは別ファイルに保存します．
`intrinsics.json` にはToF・カラーの内部パラメータと両センサー間の外部パラメータが入ります．
設定JSONを使う場合は，原本も `camera_parameters.json` にコピーします．
別タイミングで到着するフレームは，既定で250ms以内（低FPS時は3フレーム周期以内）の
最新データを組み合わせます．厳密な同時露光は保証せず，SDKのハードウェア時刻と
キャッシュの経過時間を各ショットに記録します．古いフレームやIR未取得のまま保存しません．

コマンドラインでは，リポジトリのルートから次のように実行できます．

```bash
# 1ショット分の全データを保存して終了（GUIの「全データを1枚取得」と同じ）
python3 nyx660_script/collect/dataset_collect_photo.py --once

# sで全データを1組ずつ保存，qで終了
python3 nyx660_script/collect/dataset_collect_photo.py

# 画面を使わず1ショット保存．待ち時間は任意に変更できる
python3 nyx660_script/collect/dataset_collect_photo.py --once --no-preview --warmup 2
```

必要なフレームが揃わない場合は既定で10秒後にエラーにします（`--timeout` で変更可）．
画像とPLYの保存が完了したショットだけを撮影枚数に数えます．
既存の同名ファイルは上書きせず，同じ秒に別セッションを開始する場合も保存先を分けます．

保存処理と模擬カメラによるテストは，カメラを接続せずに実行できます．

```bash
python3 -m unittest discover -s tests -p test_snapshot.py
```


## CLI オプション

全スクリプト共通のオプション：

| オプション | 説明 | 例 |
|---|---|---|
| `--fps N` | FPS を上書き | `--fps 15` |
| `--color-size WxH` | color 解像度を上書き | `--color-size 800x600` |
| `--tag NAME` | セッションディレクトリ名にタグを付与（ファイル名には付かない） | `--tag greenhouse` |

スクリプト固有のオプション：

| オプション | 対象スクリプト | 説明 |
|---|---|---|
| `--model PATH` | yolo_detection / record_with_yolo | YOLO モデル（`config.yaml` の `model.yolo_path` を上書き） |
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

ToF 深度を利用して各検出 BBox に距離（X.XXm）を表示する。モデルは `config.yaml` の `model.yolo_path` で指定（`--model` で上書き可）。

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

## データ命名規則

すべての収集データは，ファイル名だけで「どのカメラの・いつの・何枚目の・何の画像か」が
分かるように統一されています．学習用に1つのフォルダへ集約しても，アノテーションツールに
まとめて読み込ませても，どのセッションのデータか判別できなくなることがありません．

```
{cam}_{YYMMDD}_{HHMMSS}_{NNNNN}_{mod}.{ext}

nyx_260707_101741_00042_c.jpg
 │       │      │      │     └─ モダリティコード
 │       │      │      └─────── セッション内のショット連番（撮影時刻ではない）
 │       │      └────────────── セッション開始時刻
 │       └───────────────────── 取得日
 └───────────────────────────── カメラコード
```

| 要素 | 説明 |
|------|------|
| `cam` | `nyx`（NYX660）．real_syutoku 側は `d435` / `d405` / `g336` を使うため，リポジトリを跨いでも衝突しません |
| `YYMMDD` | 取得日 |
| `HHMMSS` | セッション開始時刻．1プロセス＝1セッションで固定です |
| `NNNNN` | セッション内のショット連番．**同一ショットの color/depth/ir は同じ番号を共有します** |
| `mod` | モダリティコード（下表） |

**モダリティコード**

| コード | 内容 | コード | 内容 |
|--------|------|--------|------|
| `c` | color | `pc` | 点群（.ply） |
| `d` | depth（16bit raw PNG） | `pt` | クリック座標（.txt） |
| `dc` | depth colormap | `det` | YOLO 描画済み |
| `i1` | IR（NYX660 は単眼なのでこれ） | | |

連番はショット単位で共有されるため，末尾のモダリティコードを差し替えるだけで対応する
ファイルを引けます（`..._00042_c.jpg` ↔ `..._00042_d.png`）．YOLO のラベル `.txt` や
labelImg の `.xml` も stem が一致するので自動的に紐づきます．

撮影条件など，ファイル名に載せない情報は各セッションの `metadata.json` に記録されます．
`camera` には real_syutoku と同じキーで機種・個体・条件が残ります（NYX660 はパラメータJSONのパスも記録）．

```json
"camera": {
  "name": "NYX660", "model": "NYX660", "serial": "NYX_SERIAL_NUMBER", "backend": "scepter",
  "resolution": [1600, 1200], "fps": 30, "depth_scale": 0.001,
  "params_json": ".../camera_params/NYX660_ros_match_2026_08_17.json"
}
```

### セッションディレクトリとタグ

```
<出力先>/<YYMMDD>/<prefix>[_<tag>]/<モダリティ>/<ファイル>
```

`--tag` を付けると，セッションディレクトリ名にだけ任意の名前が付きます
（ファイル名の桁は増えません）．

```bash
python3 collect/dataset_collect_photo.py --tag greenhouse
# → images_dir/260707/nyx_260707_101741_greenhouse/color/nyx_260707_101741_00001_c.jpg
```

## 保存形式

### 連続画像収集（dataset_collect）

```
images_dir/YYMMDD/nyx_YYMMDD_HHMMSS/
├── color/           {prefix}_{NNNNN}_c.jpg    # RGB（デフォルト 1600×1200）
├── depth/           {prefix}_{NNNNN}_d.png    # 生深度 16bit PNG（640×480、mm単位）
├── depth_colormap/  {prefix}_{NNNNN}_dc.jpg   # 深度可視化
├── ir/              {prefix}_{NNNNN}_i1.jpg   # IR グレースケール（640×480）
└── metadata.json                              # カメラ設定・撮影枚数
```

### 点群収集（dataset_point_collect）

point_merge.py がセッション直下を走査するため，フラットに配置されます．

```
pointcloud_dir/YYMMDD/nyx_YYMMDD_HHMMSS/
├── {prefix}_00001_c.jpg
├── {prefix}_00001_d.png     # 16bit PNG（mm単位）
├── {prefix}_00001_pc.ply    # バイナリ PLY（m 単位・RealSense Viewer 座標系，z=0 / z=65535 除外済み）
├── intrinsics.json          # ToF / Color 内部パラメータ + 外部パラメータ
└── metadata.json            # 取得モード・フレーム数・hardwaretimestamp 一覧・ply_format
```

`point_merge.py` の出力は `{prefix}_merged_pc.ply` です．

**点群 PLY の形式は real_syutoku と共通**です：単位は **m**，座標系は RealSense Viewer と同じ
（x 右・y 上・カメラ前方が **-z**）．SDK の点群（mm・x 右・y 下・z 前方）を保存時に変換しています．
`metadata.json` の `ply_format` に `{"unit": "m", "axes": "realsense_viewer"}` が記録されます．
`depth` の PNG は従来どおり mm 単位の 16bit です．

2026年10月より前に保存した PLY は **mm・SDK の座標系のまま**です．混ぜて使う場合は
[旧形式の点群 PLY の変換](#旧形式の点群-ply-の変換) で変換してください．

### タイムラプス（timelapse_detect）

```
timelapse_data/YYMMDD/nyx_YYMMDD_HHMMSS/
├── color/           {prefix}_{NNNNN}_c.jpg
├── depth/           {prefix}_{NNNNN}_d.png
├── depth_colormap/  {prefix}_{NNNNN}_dc.jpg
├── ir/              {prefix}_{NNNNN}_i1.jpg
├── annotated/       {prefix}_{NNNNN}_det.jpg   # --detect 時のみ（距離付きBBox）
├── detection_log.csv                           # --detect 時のみ
└── metadata.json
```

連番は5桁なので，長時間のタイムラプス（10秒間隔×24時間＝8640枚）でも桁が溢れません．

`detection_log.csv` の列: `timestamp, elapsed_min, num_detections, avg_conf, max_conf, avg_depth_m, classes`

### MP4 録画（mp4_collect / record_with_yolo）

動画はショット連番を持たないため，`{cam}_{YYMMDD}_{HHMMSS}_{種別}` で命名されます．

```
mp4_dir/
├── nyx_YYMMDD_HHMMSS_c.mp4     # RGB 動画
├── nyx_YYMMDD_HHMMSS_dc.mp4    # depth colormap 動画（640×480）
└── nyx_YYMMDD_HHMMSS_det.mp4   # YOLO + 距離オーバーレイ動画（640×480）
```

## 旧形式の点群 PLY の変換

2026年10月より前の PLY（mm・SDK 座標系）を，現在の形式（m・RealSense Viewer 座標系）に変換します．
`metadata.json` の `ply_format` と点の z の値から旧形式かどうかを判定し，変換済みのものは自動でスキップします．

```bash
# 何が変換されるか確認（dry-run）
python3 tools/convert_ply_to_m.py data

# 実行（変換前のファイルは *.ply.mm_bak として残る。不要なら --no-backup）
python3 tools/convert_ply_to_m.py data --apply
```

マージ結果（`*_merged_pc.ply`）は変換しないので，変換後に `point_merge.py` をやり直してください．

## 旧データの変換

2026年7月より前に取得した旧命名（`imageN_YYYY-MM-DD_HHMMSS_NYX660/` など）のデータは，
そのまま置いておけます．新命名に揃えたい場合は変換ツールを使ってください．

旧ファイル名のモダリティ接尾辞（`_color` / `_depth_colormap` など）を手がかりに，
同一ショットのファイルへ同じ連番を振り直します．`labels/*.txt` や `*.xml` も追従します．
既定はコピーなので原データは残ります．

### GUI（推奨）

フォルダを選んで，変換後のファイル名を一覧で確認してから実行できます．
データが複数の場所に散らばっている場合も，変換元フォルダをいくつでも登録できます．

```bash
python3 tools/rename_legacy_gui.py
```

1. **変換元フォルダ** — 「フォルダを追加...」で登録（複数可）．
   データルート（`data/images`）を指定すれば配下のセッションをまとめて拾います．
2. **設定** — カメラコードは通常「自動判別」のままで構いません．フォルダ名から
   判別できないデータ（`click_test_data` など）はスキップ理由が表示されるので，
   そのときだけ `nyx` を指定します．タグ・出力先・コピー/移動もここで指定．
3. **プレビューを作成** — セッションごとに「変換前 → 変換後」が並びます．
   行を開くと個々のファイル名を確認できます．
4. **変換を実行** — プレビューを作るまで実行ボタンは押せません．

`tkinter` が必要です（`sudo apt install python3-tk`）．

### CLI

```bash
# 何が起きるか確認（dry-run。既定ではファイルを書き換えません）
python3 tools/rename_legacy.py data/images

# 実行（コピーで出力するため原データは残ります）
python3 tools/rename_legacy.py data/images --apply

# ディレクトリ名からカメラ・日時が分からないデータ
python3 tools/rename_legacy.py data/click_test_data/250911_testdata_click --cam nyx --apply
```

`--move` を付けると移動になります（原データが残らないので注意）．

## カメラ仕様（NYX660）

| 項目 | 値 |
|------|-----|
| 深度解像度 | 640×480 @最大30fps |
| 深度範囲 | 0.3〜4.5m |
| 深度フォーマット | 16bit（mm単位） |
| RGB 解像度 | 1600×1200（デフォルト。このカメラは640×480とのみ2モード対応、1280×960等は不可） |
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
