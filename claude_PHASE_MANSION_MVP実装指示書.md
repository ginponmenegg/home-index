# マンションMVP 実装指示書（Claude Code 用）

Version 0.1 / 2026-08-16
対象リポジトリ：`ginponmenegg/ouchi-tsushinbo`（おうちの通信簿）

---

## 0. この指示書の使い方（Claude Code への最初の指示）

あなたはこのプロジェクトの Lead Engineer として作業する。**いきなり全部を書かず**、次の順で進めること。

1. まずリポジトリ全体、特に以下の既存ファイルを読み、現状の実装を正確に把握する：
   `src/models.py` / `src/pipeline.py`（`run_pipeline`）/ `src/scoring.py`（`score_finance`, `score_asset`, `score_risk`, `build_diagnosis`, `CategoryScore`, `WEIGHTS`）/ `src/comparable.py` / `src/price_analysis.py` / `src/loan.py` / `src/reinfolib.py`（不動産情報ライブラリのクライアント）/ `src/geocoding.py` / `src/citycode.py` / `src/config.py` / `config.json` / `app.py`（特に `/diagnose`, `_run_diagnose`, `run_pipeline` 呼び出し, `RESULT` テンプレート, `FORM` テンプレート, `brand_header`, `FOOTER`, `to_yen`, `man`, `_legal_page`）。
2. 読み終えたら、**実装前に**「既存コードのどの関数・シグネチャに接続するか」「不明点・判断が必要な点」を箇条書きで報告し、確認を取る。
3. 確認後、本書の「実装スコープ」を上から順に、小さくコミットしながら実装する。

**絶対厳守（PROJECT BRIEF 第13・14・53章より）**
- 既存の戸建フロー（`run_pipeline`, `score_*`, `/diagnose` など）を**壊さない・改変しない**。マンションは独立して追加する。
- AIに事実を作らせない。**取得できない項目は「不明/未確認」を返し、勝手に埋めない**。
- スコアはルールで計算する。AIに点数を出させない。
- 無断スクレイピングをしない。API仕様は記憶で決めず、必要なら公式ドキュメントを確認する。
- 迷ったら勝手に仕様を決めず、A/B/C案とメリット/デメリットを提示して質問する。

---

## 1. ゴールとスコープ

### やること（マンションMVP）
中古マンションを、戸建とは**別ページ・別フロー（方式ア）**で診断できるようにする。MVPで評価するのは次の2軸のみ。

- **① 価格の妥当性**：専有面積あたりの㎡単価を、近隣・同一市区町村のマンション成約事例から出し、推定価格レンジと売出価格を比較して「割安/概ね適正/割高」を判定する。
- **③ 資産性**：駅徒歩・築年（**新耐震＝1981年6月以降か否かを重視**）・所在階/総階数・向き から評価する。

返済負担率（資金）は、**既存の `score_finance` をそのまま流用**する（先日、年収400万円で基準を30%/35%に切り替える修正を入れ済み。マンションでも同じロジックを使う）。

リスク（ハザード等）は、既存のハザード取得が座標ベースで戸建と共通に使えるなら**流用してよい**。ただしMVPで無理に組み込まず、まずは①③＋資金を確実に動かすことを優先する。

### やらないこと（次段に回す）
- **② 管理の健全性**（管理費・修繕積立金・積立金残高・大規模修繕履歴・管理形態）。入力項目もMVPでは設けない。次段で追加する。
- 同一マンション内の事例に限定した高精度分析（MVPは市区町村＋近隣の㎡単価中央値で十分）。
- マンションのURL/PDF/画像からの自動抽出（当面は手入力フォームのみ。将来、既存 `extract.py` の枠組みに合流させる）。

---

## 2. 入力項目（`/mansion` フォーム）

戸建フォーム（`app.py` の `FORM`）を参考に、マンション専用フォームを作る。

### 戸建と共通で聞く
- 所在地（住所・必須）
- 売出価格（万円・必須）※ 既存 `to_yen` で円換算する
- 築年（西暦・任意）
- 駅/バス停まで徒歩（分・任意）
- 市区町村コード・町名（住所から自動補完。既存の `/resolve_city` 相当の仕組みを流用）
- 世帯年収（万円・任意）※ 既存 `to_yen` で円換算 → `run_mansion_pipeline` に渡す
- 頭金（万円・任意）※ 同上
- 借入年数（年・任意、未入力は35）

### マンション固有で追加
- **専有面積（㎡・必須）**：㎡単価計算の土台
- **所在階**（例：5）：任意
- **総階数**（例：10）：任意
- **向き**：任意。選択肢は「南 / 南東 / 南西 / 東 / 西 / 北東 / 北西 / 北 / 不明」

### 戸建から外す
- 土地面積、建物面積、リフォーム有無

---

## 3. 追加・変更するファイル

### 3.1 `src/models.py`（追加）
`MansionSubject` データクラスを追加する（既存 `SubjectProperty` を参考に、フィールドだけマンション用にする）。

```
@dataclass
class MansionSubject:
    address: str
    price: Optional[int] = None            # 円（to_yen 変換後）
    build_year: Optional[int] = None       # 西暦
    station_walk: Optional[int] = None      # 分
    exclusive_area_m2: Optional[float] = None  # 専有面積（㎡）必須級
    floor: Optional[int] = None            # 所在階
    total_floors: Optional[int] = None     # 総階数
    direction: Optional[str] = None        # 向き（南/南東/…/不明）
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    municipality_code: Optional[str] = None
    district_name: Optional[str] = None
```

必要なら診断結果を束ねる `MansionDiagnosis`（または既存 `Diagnosis`/`DiagnosisResult` を流用）も用意する。既存の結果構造（`total_score`, `grade`, `categories: List[CategoryScore]`, `strengths`, `weaknesses`, `comment` 等）に合わせ、**結果テンプレートを戸建と共通化できる形**にすること。

### 3.2 `src/mansion_price.py`（新規）
㎡単価ベースの価格分析。既存 `comparable.py` / `price_analysis.py` の思想（地理的近さ・築年・取引時期で類似性、中央値/加重平均、事例が薄いときは信頼度を下げる）を**㎡単価版に移植**する。

- 不動産情報ライブラリ（既存 `reinfolib.py` のクライアント）から、対象座標の近隣・同一市区町村の**マンション（中古マンション等）の成約事例**を取得する。
  - 既存の戸建向け取得ロジック（`comparable.py` の `extract_comparables` 等）を読み、**物件種別でマンションに絞る**方法を確認する。ライブラリAPI側の種別区分（例：「中古マンション等」）が既存コードでどう扱われているかに合わせる。
- 各事例の **㎡単価 = 取引価格 ÷ 専有面積** を算出。異常値（極端に高い/低い）は除外。
- 近隣優先で件数が足りなければ同一市区町村へ広げる（戸建の `pipeline.py` にある「近接が少なければ市内全域で参考価格」と同じ段階的フォールバックを踏襲）。
- **中央㎡単価 × 対象の専有面積** を推定価格の中心とし、事例のばらつきからレンジ（例：中央値±分散）を出す。
- 出力は既存 `PriceAnalysis` 相当（`estimate_low/mid/high`, `unit_price_median`(㎡単価中央値), `comparable_count`, `confidence`, `verdict`(割安/概ね適正/割高), `deviation_pct`, `dispersion_pct`, `warnings`）に**そろえる**。結果テンプレートを戸建と共通化するため、フィールド名は既存に合わせること。
- 事例が極端に少ない/取れない場合は、`verdict="判定不可"`・低信頼度を返し、勝手に価格を断定しない（第14・41章）。

### 3.3 `src/mansion_scoring.py`（新規）
マンション用スコアリング。既存 `scoring.py` の `CategoryScore` と `WEIGHTS` の枠組みをそのまま使う。

- **価格スコア**：`mansion_price` の推定レンジに対する売出価格の位置で採点（既存 `score_finance` 以外の、戸建の価格スコアの出し方＝推定レンジ比の考え方を参考にする）。
- **資産性スコア**：
  - 駅徒歩：分数が短いほど高評価（戸建 `score_asset` の駅近接評価を流用可）。
  - 築年：`current_year - build_year` で築年数。**新耐震判定＝build_year が 1982 以降（1981年6月の新耐震基準。年単位運用なら1982年築以降を新耐震扱い）かどうかを重視**し、旧耐震（1981年以前）は明確に減点。築浅ほど加点。
  - 所在階/総階数：極端な低層（1階）はやや減点、それ以外は中立〜微加点程度（MVPは軽め）。総階数が取れないなら中立。
  - 向き：南系（南/南東/南西）を微加点、北を微減点、不明は中立。**あくまで軽い加減点**にとどめる。
- 各カテゴリで `sufficiency`（情報充足度）を返し、**入力が無い項目は評価に反映しない**（戸建と同じ思想）。
- **重み**：`config.json` の `category_weights` を読み、マンションでは②管理を使わない分を①価格・③資産性・資金へ配分し直す。具体値は Claude Code が `config.json` の現行値を読んだ上で提案し、**ユーザーに確認してから確定**する（勝手に決めない）。MVPの暫定案：価格を最重視、資産性・資金を次点、リスクは流用できれば加える。
- 総合点・グレード・強み/弱み/コメントの組み立ては、既存 `build_diagnosis` の作り方に合わせる（可能なら共通関数を再利用）。

### 3.4 `src/pipeline.py`（追加）
`run_mansion_pipeline(subject: MansionSubject, ...)` を**新規関数として追加**する（既存 `run_pipeline` は触らない）。処理の流れ：

1. 住所 → 座標（既存 `geocoding` を流用）
2. 座標・市区町村 → マンション成約事例取得 → `mansion_price` で㎡単価分析
3. ローン計算（既存 `compute_loan` を流用。年収・頭金・借入年数を受け取る）
4. `mansion_scoring` で①価格・③資産性を採点、資金は `score_finance` を流用
5. （可能なら）ハザードを既存の座標ベース取得で付与
6. 結果オブジェクトを組み立てて返す（戸建結果と同じ構造にそろえる）

引数は戸建の `run_pipeline` に倣い、`reinfolib_key`, `google_key`, `annual_income`, `down_payment`, `loan_years`, `mock` 等を受け取れるようにする。

### 3.5 `app.py`（追加）
既存を壊さず、次を追加する。

- **`GET /mansion`**：マンション入力フォームを表示（`FORM` を参考にした `MANSION_FORM` テンプレート）。ブランドヘッダー・フッター・CSS は既存の `brand_header()` / `FOOTER` / `BRAND_CSS` を流用（ロゴCSSは先日 `max-width:100%` 済み）。
- **`POST /mansion_diagnose`**：フォーム値を受け取り、`to_yen` で価格・年収・頭金を円換算、`MansionSubject` を作り、`run_mansion_pipeline` を呼び、結果を表示。結果テンプレートは戸建の `RESULT` を流用/共通化し、**画像保存・共有ボタン（html2canvas）もそのまま使える**ようにする（`id="report"` を結果ラッパに付ける）。
- **トップ（`FORM`）に導線を1つ追加**：「マンションはこちら」リンク（`<a href="/mansion">`）。目立つ位置（説明文の下あたり）に置く。逆にマンションページには「戸建はこちら（`/`）」リンクを置く。

### 3.6 テスト（`tests/` に追加）
- 新耐震判定：build_year=1981→旧耐震判定、1982→新耐震判定 になること。
- ㎡単価分析：ダミー事例（取引価格・専有面積）を渡し、㎡単価中央値と推定価格が期待どおり出ること。事例0件のとき `判定不可`/低信頼度を返すこと。
- 価格 verdict：推定レンジ内→概ね適正、大幅超過→割高、大幅下回り→割安 の分岐。

---

## 4. 実装順序（小さくコミット）

1. `models.py` に `MansionSubject` 追加（コミット）
2. `mansion_price.py`（㎡単価分析）＋テスト（コミット）
3. `mansion_scoring.py`（価格・資産性）＋テスト（コミット）
4. `pipeline.py` に `run_mansion_pipeline` 追加（コミット）
5. `app.py` に `/mansion` と `/mansion_diagnose`、トップ導線（コミット）
6. 実物件1件で通し確認 → 微調整

各ステップ後、**戸建の既存フロー（`/`→`/diagnose`）が壊れていないことを必ず確認**すること。

---

## 5. 確認を取るべきポイント（勝手に決めない）

- **重み配分**：`config.json` の現行 `category_weights` を読み、マンション用の配分案を提示してユーザー承認を得る。
- **向き・階数の加減点の強さ**：MVPでは軽めにする方針だが、具体的な点差は提案して確認。
- **新耐震の境界運用**：build_year が年単位（月が無い）の場合、1982年築以降を新耐震扱いとするかを確認。
- **ハザードをMVPに含めるか**：既存の座標ベース取得がそのまま流用できるかをコード確認の上で報告し、含めるか判断を仰ぐ。

---

## 6. 参照（PROJECT BRIEF 該当章）

- 第22章：中古マンションの想定データ項目（管理費・修繕積立金等は次段）
- 第13・14章：AIと計算ロジックの分離／事実を作らせない
- 第40・41章：価格分析ロジック／割安・適正・割高判定
- 第53章：避けること（無断スクレイピング・AIだけで査定・不明情報の補完 等）
- 第55章：迷ったら質問する
