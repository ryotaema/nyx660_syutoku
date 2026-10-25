# データ形式

保存されるデータの命名規則・フォルダ構成・ファイル形式です．
旧形式のデータの変換は [legacy_conversion.md](legacy_conversion.md) を参照してください．

## 命名規則

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
| `cr` | color（ロスレスPNG） | `pt` | クリック座標（.txt） |
| `d` | depth（16bit raw PNG） | `det` | YOLO 描画済み |
| `dc` | depth colormap | `i1c` | IR 疑似カラー |
| `i1` | IR（NYX660 は単眼なのでこれ） | | |

連番はショット単位で共有されるため，末尾のモダリティコードを差し替えるだけで対応する
ファイルを引けます（`..._00042_c.jpg` ↔ `..._00042_d.png`）．YOLO のラベル `.txt` や
labelImg の `.xml` も stem が一致するので自動的に紐づきます．

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

### metadata.json

撮影条件など，ファイル名に載せない情報は各セッションの `metadata.json` に記録されます．
`camera` には real_syutoku と同じキーで機種・個体・条件が残ります（NYX660 はパラメータJSONのパスも記録）．

```json
"camera": {
  "name": "NYX660", "model": "NYX660", "serial": "NYX_SERIAL_NUMBER", "backend": "scepter",
  "resolution": [1600, 1200], "fps": 30, "depth_scale": 0.001,
  "params_json": ".../camera_params/NYX660_ros_match_2026_08_17.json"
}
```

## 保存形式

### 全データ取得（dataset_collect_photo --all-data）

保存先は `output.images_dir` 配下です．各ファイルは同じショット番号を共有します．
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

RGB・深度・IRはネイティブの解像度と座標系で保存します（プレビューだけを縮小します）．
PLYはSDKが深度から変換したXYZ点群で，RGBは別ファイルに保存します．
`intrinsics.json` にはToF・カラーの内部パラメータと両センサー間の外部パラメータが入ります．
設定JSONを使う場合は，原本も `camera_parameters.json` にコピーします．

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
[旧形式の点群 PLY の変換](legacy_conversion.md#旧形式の点群-ply-の変換) で変換してください．

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

## カメラ仕様（NYX660）

| 項目 | 値 |
|------|-----|
| 深度解像度 | 640×480 @最大30fps |
| 深度範囲 | 0.3〜4.5m |
| 深度フォーマット | 16bit（mm単位） |
| RGB 解像度 | 1600×1200（デフォルト．このカメラは640×480との2モードのみ対応，1280×960等は不可） |
| IR | 1ch（640×480） |
| 接続 | GigE（PoE+） |

RealSense との違いは [RealSense_vs_NYX660.md](../RealSense_vs_NYX660.md) を参照してください．
