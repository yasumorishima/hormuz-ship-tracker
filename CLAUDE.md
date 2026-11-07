# CLAUDE.md — hormuz-ship-tracker（cloud セッション用の引き継ぎ）

ホルムズ海峡の船舶監視。AIS（aisstream）の標本収集と、Sentinel-1 SAR からの船影検出を GitHub Actions で回し、
HF dataset `yasumorishima/hormuz-ais` に積んで、GitHub Pages の地図（`docs/`）がブラウザから HF を直接読む。
設計の根拠は `docs/PIPELINE.md` が正。

## 🔴 進め方（2026-09-24 から Claude Code cloud で進める）

- **作業は cloud だけで完結させる**。ユーザーの PC・Raspberry Pi には依存しない（旧作業クローンは RPi5 にあったが使わない）。
- この repo は **public**＝Actions の分数は無制限。収集・統合・公開は Actions の schedule が自走しているので、cloud は**開発と検証**を担う。
- cloud から workflow の dispatch はできない（403）。run の状態は GitHub MCP で読む。変更は作業ブランチ → PR → **自分で merge**。
- merge したら master の run（`tests.yml` と、触った workflow の次回実行）を見るまで「完了」と言わない。
- **ネットワーク**：必要な外部ホストは `planetarycomputer.microsoft.com`（STAC 検索・SAS 署名・匿名）、
  `sentinel1euwest.blob.core.windows.net`（S1 GRD の COG）、`ai4edataeuwest.blob.core.windows.net`（ESA WorldCover・マスク生成だけ）、
  `huggingface.co` 系（読み取りは匿名）。**cloud 環境「クラウド許可1」はネットワーク制限なし（unrestricted）**＝許可リストの話は不要
  （11-07 実測：STAC・SAS 署名・両 blob の署名付き一覧・HF の API と resolve がすべて 200）。届かないときは環境ではなく、
  下の「cloud サンドボックス」の穴（HF の Xet 停止・headless Chromium の CDN 403）を先に疑う。
- **秘密情報は使わない**。`HF_TOKEN` と aisstream のキーは Actions の secrets にだけある。SAR 側は匿名で取れるので、
  smoke も検証も秘密なしで回す（**本番キーをバグ探しに使わない**）。
- 依存：`pip install -r requirements-test.txt`（SAR の依存も含む・`websockets==15.0.1` と `shapely==2.0.7` は固定のまま緩めない）。
  検査は `tests.yml` と同じ `unittest.defaultTestLoader.discover("tests")`（10-06 時点で 138 件・skip 1 件でも赤扱い）＋ `python scripts/sar_smoke.py`（実シーン 1 枚）。
  `scripts/generate_sar_water_mask.py` はピーク約 4.4GB（コンテナなら回せる。RPi5 では回せなかった）。

## 構成

| 面 | 実体 |
|---|---|
| AIS 収集 | `collect.yml`（15 分ごとの設定・実配信は best-effort）が aisstream に **180 秒だけ**接続 → 陸地フィルタ → HF `raw/<日>/<HHMMSS>.parquet` |
| 統合 | `compact.yml`（日次）が終わった日を `daily/<日>.parquet` へ＋データセットカードを HF へ（`push_card.py`） |
| 公開 | `publish.yml`（3 時間ごと）が HF → 使い捨て SQLite（`positions` のみ）→ 描画 → `docs/` を commit。5 workflow（見張り含む）の enable を打ち直す（60 日停止対策） |
| SAR | `sar-collect.yml`（日次 04:41 UTC）＝`src/sar_scene.py`（STAC＋SAS＋GCP 付き COG）→ `src/sar_detect.py`（ブロック中央値＋MAD の背景 → `bg + 10·sd` → 連結成分 → 形状）→ `src/sar_store.py`（HF `sar/det/v1/` と `sar/scenes/v1/` を 1 コミット） |
| 陸マスク | `data/sar_water_mask.tif`＝ESA WorldCover v200 10m（**CC BY 4.0**）。生成器 `scripts/generate_sar_water_mask.py` |
| 地図 | GitHub Pages（master の `/docs`）。サーバーなし。背景は同梱の Natural Earth 陸地（`docs/land_mask.geojson`・`data/` とバイト一致をテストで固定）。SAR 層＝最新パスの `sar/det/v1/` をブラウザが直接読む |
| 見張り | `schedule-watch.yml`（15 分ごと・`actions: read`/`issues: write`/`contents: read`・secrets なし）＝`src/watch_schedules.py`。定時 workflow の連続失敗を Issue でメールする（下の「見張りの判定規則」） |
| SAR 評価 | `src/sar_survey.py`（手動）＝距離帯の率・パス間の再出現（対照つき）・`--vh` で VH 交差偏波の下限。結果 `docs/sar_survey.json` |

`compact.yml` / `publish.yml` / `sar-collect.yml` は同じ concurrency group `hormuz-hub`。

### 見張りの判定規則（`src/watch_schedules.py`・テスト `tests/test_watch_schedules.py`）

- 対象＝`.github/workflows` で `schedule` を持つもの全部（見張り自身は除く・今は collect/compact/publish/sar-collect）。
  見るのは `event=schedule&status=completed` の run だけ（手動 dispatch・実行中・待機中は数えない）。
- **success 以外は全部失敗**（failure/cancelled/timed_out/startup_failure/skipped…）。4 workflow ともジョブ単位の `if` が無いので skipped＝ジョブが走らなかった。
  実行機の割り当て失敗（10-05T20:49Z の collect）は run の conclusion が `failure`（ジョブは cancelled・ステップ 0）。
- 新しい順に先頭から **2 回以上連続**で success 以外 → Issue「定時実行が連続で失敗: <file>」を開く（本文に @yasumorishima・run の URL・conclusion・時刻・回数）。
  同名の open Issue があれば本文の更新だけ（編集はメールが飛ばない）。単発の失敗は GitHub 本体のメールだけ（アカウント設定・触らない）。
- 連続が success で途切れたら、コメントを付けて close。open のままの Issue の連続が途切れ、見ていない間に別の 2 連続ができていたら、古い方を close して新しく開く（メールを飛ばすため）。
- 定時 run は数時間遅れて発火する（collect は 18 日で 114 回・間隔最大 8.4h）＝見張りも同じく寝る。見張りが見る前に 2 連続→回復まで済んだものは、
  **開いてすぐ閉じる**（48h 以内のものだけ・本文末尾の `<!-- schedule-watch file=… last-failed-run=… -->` と重なる連続は二度報告しない）。
- run 0 件・API エラー・workflow が読めない → 見張り自身が赤（他の workflow の判定は済ませてから）。**新しく定時 workflow を足すと、その初回の定時 run が終わるまで見張りが赤**になる
  （仕様どおり・`TheRealRepositoryTest` の監視対象一覧も直す）。
- 失敗が続いている最中に Issue を手で閉じると、次の見張りで新しい Issue が開く（＝まだ失敗中の再通知）。
- **実働確認（10-06）**：PR #15 merge（03:39Z）後の初発火は 7 時間後の 10:47Z（run 37452134102・success・4 workflow とも `none`・Issue なし）。15 分 cron でも実発火は collect 同様に 1 日数回＝通知の遅れは GitHub の schedule 次第。
- 見えないもの：見張り自身の割り当て失敗（GitHub 本体のメールだけ）・定時 run がそもそも発火しなくなった状態（最新が success のまま止まる＝未検知）。

## 現在地（2026-11-07 13:30 UTC・cloud で実測）

- **定時 workflow は全部自走している**（GitHub API で直近 run を数えた）：collect 直近 100 回・compact 55 回・publish 直近 100 回・
  sar-collect 54 回が**全部 success**。見張りは 165 回中 failure 1 回だけ（10-22T15:29Z・run 39805345967＝API が sar-collect の
  完了 run を一時的に 0 件で返し「no completed scheduled runs to judge」。前後の run は正常・単発なので Issue なし＝仕様どおり）。Issue は 0 件。
  テストは 138 件 OK（cloud で discover）。
- **AIS は「ほぼゼロ」から「湾岸の港だけ少し」に変わった**：HF `daily/` は 10-06〜11-06 の 30 日分・計 2,715 行
  （10-10 と 11-02 は collect が走ったが 0 隻＝raw も daily も無い）。10-11〜10-22 は 1 日 118〜368 行、10-23 以降は 1〜10 行に戻った。
  74% がドバイ周辺（東経 55°・北緯 25° の 1 度枡）。**海峡の枠（55.8–57.5E・25.8–27.0N）は 30 日で 47 行・36 MMSI だけ**＝海峡はまだ実質未観測。
  （09-16・09-17 の再現：全世界 180 秒で 9,500〜12,300 隻・海峡 0＝aisstream に受信機が無い。今も同じ構図で、受信機が湾岸の港に少し付いた程度と読める。）
- **SAR は取りこぼしゼロ**：PC の STAC（09-09 以降）83 枚と HF `sar/scenes/v1/`・`sar/det/v1/` 各 83 枚が**完全一致**。
  09-15T02:14:05Z の欠けも埋まった（PR #13 の「504h・6 枚・古い順」が効いた）。最新は S1D 2026-11-06T14:16Z。
  パスは 37＝取得スロット別に 02:0x 7・02:1x 10・14:1x 10・14:2x 10（全スロット 3 パス以上）。実発火は 09:30〜12:45 UTC。
- **HF カード**：配信物 README.md は `docs/DATASET_CARD.md` とバイト一致（11-07 確認）。

以下の評価値は **09-29 時点の 17 シーン**で測ったまま（83 枚で測り直していない）：

- **距離帯（17 シーン・9 パス・vessel_sized 12,524・`src/sar_survey.py`・生データ `docs/sar_survey.json`）**：100km² あたり
  0.2–1km **94.5**／1–3km **12.0**／3–10km **5.0**／10km+ **4.1**（細かく 2–3km 6.2・3–5km 4.1＝3km から外洋水準）。
  0.2–1km が vessel_sized の 50%（水面の 5%）。SNR 中央値は帯で差が無い（13–15）＝SNR で海岸は掃除できない。
- **VH 交差偏波で「実在の散乱体」の下限**（VH が bg+5sd を ±2px で超える率 − 500m 先の対照）：3km+ で SNR>20 は 86–93%（対照 0.3–0.6%）、
  SNR≤20 は 3.5–12%。**弱い方は小舟か clutter か区別できない**。海岸 1km 以内は地形も VH で光る＝VH では分けられない。
- **再出現（他パスの 100m 以内・対照 500m 先）**：10km+ 5.5%（偶然 0.9%）＝固定物は多くて約 5%。0.2–1km 53%（偶然 21%）。
- **地図に SAR 層を足した**：最新パスのみ・既定は「海岸 3km 以上かつ SNR>20」（`MAP_MIN_SHORE_KM`/`MAP_MIN_SNR`＝`sar_columns.py` と
  `map.js` をテストで一致）。弱い検出・1–3km・1km 以内は層として OFF。画面に「not ships」「once every two days」「one instant, not a track」
  「Within 1 km … mixed」「unobserved, not empty」（パスが見た箱の割合を表示）を明記。no_overlap だけのパスは飛ばす。09-27 パスで既定 252・弱 1,400・海岸寄り 1,344。
- **precision**：海峡では未測定のまま。測れたのは「実在散乱体の下限」と「固定物の上限」まで（PIPELINE.md「Precision in the strait, without AIS」）。

## ▶▶ 次にやること

1. 再出現を**取得スロット別**（`sar_survey.geometry_of`）に分ける：同スロットだけで再出現＝地形の倒れ込み、スロットを跨いで再出現＝構造物。
   **前提の「各スロット 3 パス以上」は 11-07 に満たした**（7〜10 パス）。あわせて距離帯・VH・再出現を 83 枚で測り直し、上の 17 シーンの数字を置き換える。
2. 弱い検出（SNR≤20・外洋）の正体：Sentinel-2（PC・無料）で同日の静止物だけ照合できるか試す。移動船は時刻差で無理。
3. 地図の SAR 層：パス選択（過去パス）や再出現フラグを出すかは 1 の結果次第。今は最新パスのみ。
4. AIS 側は触らない（コスト 0 の待機）。海峡の枠に日々まとまって入るようになったら実配信間隔を測り、SAR の precision を AIS で測り直す
   （一致＝船・不一致＝不明のまま）。今の 30 日 47 行では足りない。
5. 既知の弱点（未発生）：読めないシーンが 1 枚あると窓（21 日）を出るまで sar-collect が毎日赤。起きたら失敗回数で status=failed 行を書く案。
   見張りは API の一時的な空返事でも赤になる（10-22 に 1 回）。単発なら放置でよい。

## 🔴 主張の前に必ず併記すること

①AIS の窓と窓の間は未観測 ②transit 検知は hosted 側では走っていない（`transit_events` は構造的にゼロ）
③船の静的情報は build 時に同じ MMSI の直近の非空値で埋めている ④**SAR は 2 日に 1 枚の瞬間であって連続航跡ではない**
⑤海岸 1km 以内は地形の倒れ込みと実船が混ざる。⛔ **「1,633 隻」のように検出数を船の数と言わない**。

## ⛔ この形を崩さない

- HF の Docker/Gradio Space は個人アカウントだと有料 → 使わない。無料は Static Space だけ。
- 6 時間 job をループさせて Actions に常駐させない（利用規約）。
- タイル業者に依存しない（CARTO の無キー版は「API KEY REQUIRED」を焼き込む＝実測）。
- **GSHHG は public domain ではなく LGPLv3**。陸マスクは WorldCover（CC BY 4.0・出典併記）。
- HF の split 名は `train` のまま（改名すると既存の `split="train"` 利用が壊れる）。カードの `data_files` は具体パスを先頭に。

## 🔴 踏んだ穴（再発させない）

SAR：
- **`WarpedVRT` に `transform`/`width`/`height` を GCP と一緒に渡すと、定数画像が返って例外も出ない**。GCP 付きの S1 GRD は
  `crs is None` で `transform` が単位行列＝`src.xy()` や `windows.from_bounds()` も画素番号を度として返す。
  正解は ①`src_crs=gcp_crs` だけ渡した自動構成の `WarpedVRT` で窓を読む ②同一 CRS の `reproject` で固定格子へ合わせる、の 2 段。
- **緯度経度格子は正方形でない**（南北 22.26m / 東西 19.92m）。等方の画素長を使うと東西を 12% 過大評価する（出荷コードと検証で設定が食い違っていた前科あり）。
- `GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR` を export したまま `gdal_translate -of ENVI` すると出力 2 バイトで exit 0。/vsicurl 用の設定は読み出しの中だけに閉じる。
- blob 読み出しは一過性に落ちる。`sar_scene.read_scene` が**署名し直して**最大 3 回再試行する（SAS は約 45 分で失効）。
- PC の item id は SAFE 名の末尾 4 桁を落とす。出力パスの主キーは item id。
- **窓＋上限＋新しい順は取りこぼす**：72h・最大 4 枚・新しい順で、5 枚あった初日の最古 1 枚が翌日には窓外＝永久欠損（09-15T02:14:05Z）。
  台帳＝ファイルの存在なので誰も穴を探さない。今は 504h・6 枚・古い順（`sar_collect.select`）。STAC は新しい順に返す＝満ページは最古を落とす（警告を出す）。
- **scene id の部分一致で探さない**：スライスの終了時刻＝次スライスの開始時刻（`…T021405_20260927T021430…` と `…T021430_…`）。
  `"20260927T021430" in id` は 2 枚に当たる。開始時刻は `id[17:32]`、パスは `id[:3]+id[17:25]`。
- 検出器の数字を書くときは**出荷する検出器で測る**（粗い検出器の「533 対 30」を docs に書いた前科）。出荷検出器では外洋で両マスク一致、海岸 1km 以内で差が出る。

cloud サンドボックス：
- HF の Xet 転送（`snapshot_download`/`hf_hub_download`）が無言で止まる → `HF_HUB_DISABLE_XET=1` で HTTP に落とすと通る。`curl -L …/resolve/main/…` も可。
- `pkill -f <文字列>`／`kill $(pgrep -f …)` は同じ文字列を含む自分の shell まで殺す（exit 144）。`pgrep -f "^python src/…"` のように先頭で固定。
- headless Chromium から unpkg・jsdelivr は proxy が 403、HF の resolve もリダイレクトで落ちる。検証は Playwright の `context.route` で
  CDN を npm（registry は通る）から esbuild で束ねた同版ファイルに、HF の parquet を curl で落とした実物（Range 対応で 206）に差し替える。
  proxy は `bypass: '<-loopback>'`、証明書は `ignoreHTTPSErrors`。

AIS・公開：
- `time.monotonic()` は boot からの秒数。「未記録」の番兵を 0 にしない。
- 切断後の再接続は 1 秒から倍々・上限 15 秒・窓の残りでクランプ（固定 1 秒はキーが弾かれたとき 1 日 17,000 回の連打になる）。
  websockets の `open_timeout` 既定 10 秒は窓に縛られない＝`min(10, 残り)` を明示。
- `received_at` は ISO の `T` 入り。SQLite の `datetime()` と比べるときは `strftime('%Y-%m-%dT%H:%M:%f', ...)`。
- 時刻は UTC 明示（`datetime.now(tz=None)` を UTC と刻んでいた前科。CI の smoke はわざと `TZ: Asia/Tokyo` で描画して刺す）。
- `build_sqlite` は `positions` しか作らない＝`transit_events` を無防備に引かない。
- `ship_name`/`destination` は空文字であって null ではない。違う船から静的情報をコピーしない。
- テストの `sys.modules["land_filter"]` stub は discovery 下で後から import した側に漏れる＝差し替えは `ais_parse.is_on_land` を直接。
- 地図：CSP に `'wasm-unsafe-eval'` が要る（`hyparquet-compressors` が WebAssembly）。無いと "Loading" のまま無言で止まる。
  headless ブラウザで確かめるときはページの console を必ず拾う。`mmsi` はブラウザでは BigInt（`JSON.stringify` で落ちる）。
- 図の中にも主張が焼かれている（`heatmap.py` が描いた文言）。文章を直したら画像も直す。
- CodeRabbit は star 10 未満の repo を自動レビューしない（`SUCCESS` の緑はレビュー済みではない）。走らせるときは bot コメントの
  `- [ ] 🔍 Trigger review` を `- [x]` にする。
