# セットアップ詳細

[README](../README.md) の「セットアップ」の補足です．

PythonとメーカーSDKを使ってカメラへ直接接続します．ROSの導入やROS2ドライバのビルドは不要です．

## 必要なパッケージ

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

GUI（`gui.py`・`tools/rename_legacy_gui.py`）には `tkinter` が必要です（`sudo apt install python3-tk`）．

## SDK

SDK本体はこのリポジトリに含まれていません．
[Vzense ScepterSDK](https://github.com/ScepterSW/ScepterSDK) をUbuntu上で別途取得します
（別OSからコピーすると `.so` が壊れることがあります）．

**このリポジトリと同じ階層**（同じ親フォルダの中）に置きます．`config.yaml` の `sdk_path` の既定値
（`../../ScepterSDK`）はこの配置を前提にしているので，この通りに置けば設定の変更は不要です．

```
<作業フォルダ>/            # 任意の場所 (例: ~/work/nyx660/)
├── nyx660_syutoku/        # このリポジトリ
│   └── nyx660_script/
│       └── config.yaml    # sdk_path: ../../ScepterSDK → <作業フォルダ>/ScepterSDK
└── ScepterSDK/            # ← ここに clone
```

```bash
git clone https://github.com/ScepterSW/ScepterSDK.git ScepterSDK
```

> **注意**: SDK v26.03 以降が必要です．旧バージョン（v24.12 以前）では depth/IR フレームが取得できません．

- SDK リポジトリ: https://github.com/ScepterSW/ScepterSDK
- API リファレンス: https://wiki.vzense.com

### SDK の場所を変える

異なる配置では `nyx660_script/config.local.yaml`（後述）で `sdk_path` を上書きするか，環境変数で指定してください．

```bash
export SCEPTER_SDK_PATH=../../ScepterSDK
```

環境変数が `config.yaml`・`config.local.yaml` より優先されます．どちらも相対パスの基準は
`nyx660_script/config.yaml` のあるディレクトリで，コマンドを実行する場所ではありません．

## ネットワーク設定

| 項目 | 値 |
|------|-----|
| カメラ IP | 192.168.1.101（デフォルト） |
| PC 側 IP | 192.168.1.100 / 255.255.255.0 |
| リンク速度 | **ギガビット必須**（LAN ランプ黄=GbE / 緑=100M） |

実際のカメラのネットワーク設定に合わせ，PCからカメラへ疎通できるように設定します．

## 設定ファイル（config.yaml）

`nyx660_script/config.yaml` で全スクリプトの設定を一元管理します．パスは config.yaml からの相対パスで記述できます．
各項目の意味はファイル内のコメントを参照してください．主な項目は次のとおりです．

| キー | 内容 |
|---|---|
| `sdk_path` | ScepterSDK の場所 |
| `camera.params_json` | ScepterGUITool でエクスポートしたカメラパラメータJSON．解像度・FPS・露光・フィルタ等はここから適用されます |
| `camera.color_width` / `color_height` | color 解像度の上書き（このカメラは 640×480 / 1600×1200 の2モードのみ） |
| `camera.depth_alpha` | depth colormap の倍率 |
| `output.*_dir` | 保存先 |
| `model.yolo_path` / `confidence_threshold` | YOLO モデルと信頼度しきい値 |
| `pointcloud.*` | 点群取得フレーム数・ICP のパラメータ |

フィルタや露光時間は `params_json` で一括管理しています（`config.yaml` に個別に書いても反映されません）．

### 環境ごとの設定（config.local.yaml）

手元のモデル名や SDK の場所，保存先など，自分の環境だけで変えたい値は `config.yaml` を直接編集せず，
`nyx660_script/config.local.yaml` に書いてください．このファイルは git 管理外で，存在すれば `config.yaml` にキー単位で上書きされます．

```bash
cp nyx660_script/config.local.yaml.example nyx660_script/config.local.yaml
```

```yaml
# nyx660_script/config.local.yaml（変えたいキーだけを書く）
model:
  yolo_path: model/my_model.pt
```

優先順位は「CLI オプション・環境変数 > `config.local.yaml` > `config.yaml`」です．相対パスの基準は `config.yaml` と同じ `nyx660_script/` です．

## Docker環境内で利用する場合（任意）

Dockerを使う場合は，Python依存とSDKをコンテナ内から利用できるようにします．
ホストにインストールしたPythonパッケージはコンテナには引き継がれません．
SDKもマウント範囲内に置き，`sdk_path` または `SCEPTER_SDK_PATH` で指定してください．

用意済みのHumbleベースのDocker環境を使う場合も，このスクリプトのために
ROS2ドライバをビルドする必要はありません．
共有SDKを利用する構成の例は次のとおりです．SDKの取得方法は
[共有SDKの配置手順](../../../vendor/README.md#別pcでsdkを準備する) を参照してください．

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
