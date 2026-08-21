# 工單:階段 2 — 讓申報規範的身分穩定

## 為什麼

同一個指令、同一份資料，連跑兩次的實測結果：

| | 第一次 | 第二次 |
|---|---|---|
| 列數 | 19 | 20 |
| 居住者那列 | `綜合所得稅：中華民國境內居住之個人`／`一般納稅人（居住者）` | `個人（中華民國境內居住者）取得中華民國來源所得`／`個人 - 綜合所得稅納稅義務人` |
| 境內營利事業 | `營利事業所得稅：總機構在中華民國境內`／`一般納稅人（境內總機構）` | `營利事業（總機構在中華民國境內）`／`營利事業 - 總機構在境內` |

**列數穩定，身分不穩定。** 唯一鍵是 `(country, tax_key, scenario, taxpayer_role)`，
而 `scenario` 與 `taxpayer_role` 都是模型每次自由生成的文字 —— 兩次執行之間
沒有任何一列會對上。重跑不會更新矩陣，只會把它變成兩倍。

涵蓋範圍同樣在飄：第一次有證券交易所得與期貨交易所得，第二次沒有；
第二次有變更會計年度、虧損扣除與兩種扣繳義務人，第一次沒有。

**這不是 prompt 能修的。** `req-v3` 到 `req-v5` 已經把列數從 349 收到 20，
那部分成功了；措辭與涵蓋的穩定性是機率性的，必須由程式強制。

## 目標

讓身分由**受控值**構成，使兩次執行的同一情境落在同一列。
`upsert` 的累積特性因此從負擔變成優勢 —— 跑三次會聯集出更完整的涵蓋，
而不是產生三倍的近似列。

## 基準與分支

- 基準：`origin/main`
- 分支名：`feat/stable-requirement-identity`

## 硬性限制

1. **不得放寬既有的防捏造機制**（`_verify_citations` 對 `node_key` 的驗證）。
2. **不得用字串相似度自動合併既有規範列。**「個人 - 綜合所得稅納稅義務人」與
   「營利事業 - 總機構在境內」字面有共同片段，合錯會產生法律上錯誤的指引。
   本工單的收斂完全依靠受控值，不依靠相似度。
3. **不得刪除任何既有規範列。**
4. **不得更動 `SYSTEM_PROMPT` 既有的七條規則。** 只能新增段落。
5. **不得改動唯一鍵 `uq_requirement_identity`。** 見項目 3 的說明。

---

## 項目 1 — 維度詞彙表

新檔 `taxwatch/requirements/dimensions.py`。

詞彙**以 (country, tax_key) 為單位**維護，不共用 —— 加值稅的「計稅方式」與
所得稅的「課稅方式」是不同概念，先前已因強行統一而返工一次。

`TW / tw_income` 的初始詞彙，**由 39 列真實產出歸納，不要自行擴充**：

| 維度 | 值 |
|---|---|
| `taxpayer_class` | `resident_individual`、`nonresident_individual`、`domestic_enterprise`、`foreign_enterprise`、`sole_proprietorship`、`trustee`、`beneficiary`、`withholding_agent` |
| `tax_scheme` | `annual_filing`、`withholding`、`profit_distribution`、`not_taxable` |
| `subject_matter` | `general_income`、`real_estate`、`securities`、`futures`、`trust_income`、`salary_interest` |

`scenario_key`：受控 slug，在上述三維度之下再區分子項目。
`TW / tw_income` 的初始值（同樣由實測歸納）：
`standard`、`post_2016_acquisition`、`presale_or_superficies`、`indirect_shareholding`、
`beneficiary_identified`、`beneficiary_unidentified`、`public_trust`、
`change_of_fiscal_year`、`loss_carryforward`、`offshore_banking_unit`。

其他稅種**先不定義** —— 沒有真實產出就不要憑空設計，這是本專案已經付過兩次
學費的教訓。未定義的稅種沿用現行行為。

提供查詢函式：給定 `(country, tax_key)` 回傳各維度的合法值與說明。

## 項目 2 — 模型輸出這些維度

- `RequirementOut` 新增四個欄位：`taxpayer_class`、`tax_scheme`、
  `subject_matter`、`scenario_key`。預設空字串。
- `EXTRACTION_TEMPLATE` 新增段落，列出該稅種的合法值與各值的說明，
  要求模型從中選擇。**未定義詞彙的稅種不輸出此段落。**
- `scenario` 與 `taxpayer_role` 保留，降級為**人類可讀描述**。
  提示中明講：這兩欄用來給人看，維度欄位才是身分。
- `PROMPT_VERSION` 升為 `req-v6`。

## 項目 3 — 身分鍵與資料表

**專案沒有 Alembic**，`FieldSource` 那類原生 enum 也因此不能新增成員。
本項目只做**新增欄位**（PostgreSQL 的 `ADD COLUMN` 可安全對既有資料執行），
**不動唯一鍵**。

- `TaxRequirement` 新增：
  - `identity_key: Mapped[str]`（`String(200)`，預設 `""`）
  - `dimensions: Mapped[dict]`（`JSON`，預設 `{}`）
- `identity_key` 由四個維度以固定順序組成，例如
  `resident_individual|annual_filing|general_income|standard`。
  四者皆空時為 `""`。
- **`_upsert_requirement()` 的比對邏輯**：
  - `identity_key` 非空 → 以 `(country, tax_key, identity_key)` 查找既有列
  - `identity_key` 為空 → 沿用現行的 `(country, tax_key, scenario, taxpayer_role)`
  - 找到既有列時，**更新** `scenario` 與 `taxpayer_role` 為本次的描述文字
    （描述可以演進，身分不能）
- 遷移腳本 `scripts/backfill_identity_keys.py`：為既有列補上 `dimensions` 與
  `identity_key`。**無法判定維度的列留空**，繼續走舊路徑，不要猜。
  與既有腳本一致：預設預覽，`--yes` 才執行。

**唯一鍵維持不變。** 兩列不同 `scenario` 但相同 `identity_key` 的情況，
由上述查找邏輯處理，不依賴資料庫約束。約束的替換留待身分穩定性經實測驗證後
另案處理。

## 項目 4 — 未知值不得靜默成為新身分

模型回傳詞彙表以外的值時：

- **不要拒絕該列** —— 那會丟失內容
- 該維度值記為空，`identity_key` 因此為空，該列走舊路徑
- 列入 stats 的 `unknown_dimension_values`，CLI 印出，格式為
  「維度名：收到的值（列：情境描述）」
- 標記該列 `needs_review`

理由：未知值可能是模型出錯，也可能是詞彙表真的缺一個值。
**兩者都必須讓人看見**，靜默接受會讓詞彙表永遠追不上實況，
靜默丟棄則會遺失真實的新情境。

## 項目 5 — CLI 可觀測

`--dry-run` 的輸出，每列加上 `identity_key`：

```
  - resident_individual|annual_filing|general_income|standard
      個人（中華民國境內居住者）取得中華民國來源所得 / 個人 - 綜合所得稅納稅義務人
```

這是本工單的驗收方式：**連跑兩次，比對 identity_key 集合。**

## 測試

至少涵蓋：

1. 詞彙表以 (country, tax_key) 為單位，未定義的稅種回傳空
2. `identity_key` 由四維度依固定順序組成，順序不因輸入順序改變
3. 四維度皆空時 `identity_key` 為空
4. 相同 `identity_key`、不同 `scenario` 文字 → 更新同一列，且描述被更新
5. `identity_key` 為空 → 沿用舊的 `(scenario, taxpayer_role)` 查找
6. 未知維度值 → 該列仍寫入、`identity_key` 為空、列入 `unknown_dimension_values`、
   標記 `needs_review`
7. **以兩次實測的真實措辭為輸入**，斷言它們產生相同的 `identity_key`：
   - `綜合所得稅：中華民國境內居住之個人` / `一般納稅人（居住者）`
   - `個人（中華民國境內居住者）取得中華民國來源所得` / `個人 - 綜合所得稅納稅義務人`
   兩者的維度皆為 `resident_individual|annual_filing|general_income|standard`
8. 遷移腳本可重複執行，且無法判定維度的列保持空白

現有測試必須全過。

## 本機環境

本機沒有資料庫，不要執行 `taxwatch` 指令或 `scripts/` 下的腳本。
驗證一律靠 `.venv/bin/pytest -q`。

## 交付

- 每個項目一個 commit，訊息用繁體中文描述意圖
- 開 PR，**不要自行合併**
- `REPORT:` 一行：分支、PR 連結、五項完成狀態、`pytest` 通過數、新增測試項數

## 遇到牴觸時

以程式碼實況為準，在 commit 訊息說明偏離之處。不要為了讓測試通過而放寬斷言。
