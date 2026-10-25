# 点群を見る（CloudCompare）

撮影した点群（`*_pc.ply`・つなぎ合わせた `*_merged_pc.ply`）は [CloudCompare](https://www.cloudcompare.org/) で見られます．

## インストール（Ubuntu）

```bash
sudo snap install cloudcompare
cloudcompare.CloudCompare      # 起動
```

## 開き方

1. CloudCompare を起動し，PLY ファイルをウィンドウにドラッグ＆ドロップします（または「File → Open」）．
2. 読み込み時に「Ply File Open」の画面が出たら，そのまま **Apply** を押します（x, y, z と red, green, blue が自動で割り当てられます）．
3. 左上の「DB Tree」に読み込んだ点群が並びます．チェックを外すと非表示にできます．複数の PLY を重ねて見比べることもできます．

## 操作

| 操作 | 動き |
|---|---|
| 左ドラッグ | 回転 |
| 右ドラッグ | 平行移動 |
| ホイール | 拡大・縮小 |
| 左のツールバーの立方体ボタン | 正面・上・横など決まった向きから見る |
| 「Zoom and center on the entity」ボタン（虫めがね） | 点群全体が画面に入るように合わせる |

座標は RealSense Viewer と同じ向き（x 右・y 上・カメラの前方が −z，単位 m）です．
カメラから見た向きにするには，ツールバーの「Back View」（後ろから）を選ぶと見やすくなります．

## 色について

2026-10-25 以降に撮った PLY は，カラー画像の色が付いています．

- **灰色の点**: 色が分からなかった点です．カラーカメラの画角の外，またはカラーカメラから見て手前の物に隠れている所です（ToF とカラーのカメラは約 3cm 離れているため）．透明な物や黒い物など，深度が取れない物の後ろにある点は，隠れていても色が付くことがあります．
- **全体が灰色・白一色になる**: 色の無い PLY です．GUI の「ツール」タブ →「点群に色を付ける」（`tools/colorize_ply.py`）で後から色を付けられます（[legacy_conversion.md](legacy_conversion.md#点群に色を付ける)）．
- **色が付いているはずなのに表示されない**: DB Tree で点群を選び，左下の「Properties」の **Colors** を `RGB` にします（`None` や `Scalar field` になっていると色が出ません）．

## 形のゆがみについて

保存した PLY は，画面の端ほど少し横にずれています（カメラの SDK がレンズの歪みを補正していないため）．
壁や机がわずかに曲がって見えるときは，GUI の「ツール」タブ →「点群の歪みを補正」で直せます（[legacy_conversion.md](legacy_conversion.md#点群の歪みを補正)）．

## 見やすくするには

- 点が小さいときは，Properties の **Point size** を 2〜3 にします（または画面左上の「+」）．
- 背景が黒くて見にくいときは，「Display → Display settings → Colors and Materials」で背景色を変えられます．
