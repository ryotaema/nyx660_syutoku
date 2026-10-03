# gear_ae — ギア方式の Color 露光制御（試作）

カメラ内蔵の Auto 露光は、空・夜間のライト・暗い背景に引っ張られて対象が暗くなったり白飛びしたりする。
そこで、機種 × 環境ごとに「安全な露光・ゲインの段（ギア）」を決めておき、その範囲内だけで1段ずつ動かす。
背景と検討の経緯は `../../../log_nyx/2026-10-03.md`。

## 方針

- **段を動かすのは明るさだけ**: 空を除いた輝度の中央値（lum）を目標の帯に入れる。検出結果に頼らないので、
  対象が写っていなくても動ける。
- **conf は「戻す」判定だけに使う**: 段を変えた前後で conf の合計を比べ、大きく下がったら元の段に戻し、
  その方向をしばらく禁止する。conf を理由に段を進めることはしない（平均 conf は極端な露光で高く出るため使わない）。
- **安全側の動き**: 起動時・リセット時は既定の段から始める。白飛びは待たずに下げる。計測不能が続いたら既定の段に戻す。
  段はギア表の範囲外に出ない。
- **SDK とは疎結合**: ここは判断だけを行い、カメラへの設定は backends に任せる。ROS2 では
  `scepter_manager` のパラメータ（`color_auto_exposure=false` / `color_exposure_time` / `color_gain`）を書き換える想定。

## 構成

| パス | 内容 |
|---|---|
| `core/metrics.py` | 明るさの計測（lum・白飛び率・空の割合） |
| `core/tracker.py` | 数フレーム続いた検出だけを数える。conf の合計を計算する |
| `core/gear_table.py` | ギア表の読み込み・検証。判断の基準値（`ControllerParams`）の既定値 |
| `core/controller.py` | 判断ロジック本体（入力 → 次の段）。判断の優先順位は docstring 参照 |
| `gears/<機種>_<環境>.yaml` | ギア表 |
| `detector.py` | YOLO のラッパーと検出結果のキャッシュ |
| `backends/replay.py` | Color スイープの画像を「その段で撮れた画像」として返す（オフライン再現） |
| `backends/sdk_direct.py` | ScepterSDK で実機の Color 露光・ゲインを設定する（上限に丸める・終了時に元の設定へ戻す） |
| `run_offline.py` | スイープ上で制御を再現する |
| `run_live.py` | 実機で制御を回す／露光の反映遅れを測る（`--latency-test`） |
| `report.py` | ログCSV・推移グラフの出力（両 run 共通） |
| `tests/` | core と run_live（仮想カメラ）のテスト（実機・YOLO 不要） |

## 使い方

```bash
# テスト
python3 -m pytest tools/gear_ae/tests

# オフライン再現（全開始段。機種は ToF fx から自動判別）
python3 tools/gear_ae/run_offline.py <Colorスイープのセッション>

# 悪条件を混ぜる（起動時に対象なし20フレーム・検出20%欠落・誤検出30%）
python3 tools/gear_ae/run_offline.py <session> --blank-start 20 --drop-prob 0.2 --fp-prob 0.3

# 実機: まず露光の反映遅れを測り、settle_frames を決める（YOLO なし・約150フレーム）
python3 tools/gear_ae/run_live.py --latency-test

# 実機: 制御を回す（Ctrl-C で終了。YOLO は CPU で1枚約1秒）
python3 tools/gear_ae/run_live.py --env outdoor_day --detect-every 3 --save-every 30
```

実機の出力は `data/gear_ae/live/` 配下（フレームごとのログ・段の切替ログ・推移グラフ・画像）。

オフライン再現の出力は `data/gear_ae/offline/` 配下（フレームごとのログ CSV・段ごとの参考値・timeline.png）。
YOLO の検出結果は `data/gear_ae/det_cache/` にキャッシュされる。

## 未実装・未確認

- ROS2（`backends/ros2_param.py`）の設定先
- `sdk_direct.py` / `run_live.py` は仮想カメラでのテストのみ。実機ではまだ動かしていない
- 屋外昼以外のギア表（ハウス昼・夜間ライトなし・夜間ライトあり）。環境ごとの Color スイープが必要
- 「戻す」判定は単体テストのみで確認。静止画を繰り返すオフライン再現では、段を上げて検出が大きく減る場面がまだ無い
- 実機での露光の反映遅れ（`settle_frames` の妥当な値）→ `run_live.py --latency-test` で測る
- DS 実機の `productName`（ギア表 `DS86_outdoor_day.yaml` の名前が合っているか）
