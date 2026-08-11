# TaxDeepResearch — TaxWatch

稅法異動自動化偵測系統。持續監控台灣、中國、美國官方稅法來源，偵測條文層級的異動，
用 LLM 搭配外部佐證產出影響分析，並以 Web Dashboard 與 JSON API 呈現。

## 能回答的問題

- **每個稅種現在是什麼狀態？** — 最後更新時間、法規數、累積版本、近期異動
- **某部法規六年來怎麼演變的？** — 完整版本時間軸，任兩個日期的條文級 diff
- **2023 年當時的條文長什麼樣？** — 指定日期還原當時生效的版本
- **這次異動影響誰？** — LLM 分析：生效日、受影響對象、母法連動、風險評估、引用來源

## 快速開始

```bash
pip install -e ".[dev]"
cp .env.example .env          # 填入 DATABASE_URL、LLM_*、BRAVE_SEARCH_API_KEY

docker compose up -d postgres
taxwatch init-db
taxwatch seed-sources

taxwatch run --source cn-chinatax    # 抓取 → 正規化 → 快照 → diff → 分析
taxwatch serve                        # http://localhost:8000
```

Docker 一次起完：`docker compose up`（web 服務會等 postgres healthy）。

## 架構

```
Connectors ─▶ Normalizers ─▶ Snapshot ─▶ Diff ─▶ Legal Graph ─▶ LLM 分析 ─▶ Report / Web
  TW/CN/US      HTML/JSON      內容雜湊    條文級    子母法關聯     + Brave Search
```

| 層 | 模組 | 說明 |
|---|---|---|
| 抓取 | `taxwatch/connectors/` | 每個來源一個 connector，`config/sources.yaml` 宣告式設定 |
| 正規化 | `taxwatch/normalize/` | HTML/JSON → 條文陣列，處理 GB18030、中文數字條號 |
| 快照 | `taxwatch/jobs/` | 正規化後文字的 SHA-256，避開廣告與時間戳造成的假異動 |
| Diff | `taxwatch/diff/` | 以條號對齊，偵測新增／刪除／修改／條號變更 |
| 關聯圖 | `taxwatch/graph/` | 從條文抽引用，建立子母法與函釋關聯 |
| 語料庫 | `taxwatch/corpus/` | 本地法規庫，引用先查表再決定要不要搜尋 |
| 分析 | `taxwatch/analysis/` | LLM 結構化輸出 + 語料庫原文 + Brave Search |
| 查詢 | `taxwatch/services/` | API 與 Web 共用的查詢邏輯 |
| 呈現 | `taxwatch/api/`、`taxwatch/web/` | JSON API 與 server-rendered Dashboard |

## 監控來源

**台灣** — 全國法規資料庫（稅法）、財政部解釋函令、司法院釋字／憲判字
**中國** — 國家稅務總局、財政部（稅政司／條法司），以企業／製造業稅目為主
**美國** — Federal Register（IRS）

### 子母法層級

```
法律（增值税法 / 所得稅法）
  └─ 行政法规（增值税法实施条例 / 所得稅法施行細則）
       └─ 部门规章（管理办法／细则／查核準則）
            └─ 规范性文件（公告／通知／批复／函釋）
```

施行細則不是「另一部相關法規」，而是母法的操作性內容——`增值税法实施条例第3条`
定義了 `增值税法第1条` 的適用範圍，拆開看兩邊都不完整。因此系統用兩條互補的路徑
把它們接成同一棵樹（`taxwatch/graph/hierarchy.py`）：

1. **標題推導** — `增值税法实施条例` 的名稱本身就宣告了母法。條文還沒解析就能建立
   關聯，避免內文解析失敗的子法變成孤兒節點。
2. **引用抽取** — 補上標題看不出來的關聯（`營利事業所得稅查核準則` 的授權依據只寫在
   第 1 條），並產生條文級的邊，讓「這次異動實際碰到哪些條文」可以回答。

兩者都寫入 `AUTHORITY_OF`（子 → 母）邊，因此影響擴散查詢從母法出發即可走到所有子法。

公告與部門規章（`国家税务总局关于…的公告`、`税务人员税收业务违法行为处分规定`）
的標題只講主題、不講母法，靠的是第 1 條的授權條款：「根据《消费税法》第四条的规定，
现将…公告如下」。系統只掃描開頭數條的 `依据/根据` 子句並提升為文件級關聯——再往後的
「根据……」是論述而非授權，一併採用會讓每部法都變成每則公告的母法。

實作上有三個容易踩到的細節：

- **中文數字**：中國法規條號一律寫「第一百三十三条」而非「第133条」，抽取與正規化
  共用 `taxwatch/cn_numerals.py` 轉成阿拉伯數字，兩邊 node key 才對得上。
- **指代**：子法在第 1 條定義簡稱後，全篇以「本法第14條」「税法第一条」稱呼母法。
  抽取時需傳入 `parent_key` 才能解析；沒有上下文時寧可捨棄，也不要建出全庫共用的
  「本法」節點。
- **正式全名**：`中华人民共和国增值税法` 與 `增值税法` 是同一部法，entity key 正規化
  時會去掉開頭的國名前綴，否則子法永遠找不到母法。

以文號（`财税〔2026〕15号`、`国家税务总局公告2026年第5号`）作為規範性文件的穩定識別鍵，
citation 抽取會同時建立母法引用與廢止關係。

### 合併檢視

`/documents/{id}/consolidated` 逐條列出母法條文，並把引用該條的子法／公告條文附在其後，
回答「這條現在實際上怎麼適用」——版本歷史回答的是「這份文件改了什麼」，而稅務問題問的
從來不是單一文件。

系統**不會**把母法與子法合併改寫成單一條文。哪一項補充規定優先適用屬於法律判斷，
逕行拼接會產出看似官方、實則無人發布的條文。補充規定一律標示出處並列，由讀者判讀。

### 日期：發布日 vs 抓取日

`snapshots.issued_at` 記錄發文機關標註的成文／發布日期，`fetched_at` 記錄抓取時間。
時間軸、版本排序、「某日生效的版本」查詢一律走 `COALESCE(issued_at, fetched_at)` ——
否則首次爬取會把數十年的法規全部壓在同一天，時間軸完全失去意義。API 與頁面都會標示
`official_date`，明確區分哪些日期來自來源、哪些是退而求其次用抓取時間。

> 本專案沒有 Alembic；`init_db()` 會在 `create_all` 之後補上模型新增的 nullable 欄位
> （僅限新增可空欄位，其餘變更仍需真正的 migration）。舊資料庫可直接升級，不必重爬。

### 診斷資料庫問題

看到 `relation "..." does not exist` 時：

```bash
taxwatch doctor         # 印出實際連到哪、search_path 在哪個 schema、缺哪些表與欄位
taxwatch doctor --fix   # 建立缺少的資料表與欄位
```

會一併指出兩個常見陷阱：套件是 `pip install .` 複製到 site-packages（`git pull` 更新不到），
以及資料表其實建在另一個 schema（`DB_SCHEMA` 設定不一致）。

## 申報規範（申報基準線）

異動偵測回答「什麼變了」，這一層回答財務真正的問題：**那我們現在要怎麼申報**。
就是財務原本用 Excel 維護的那張表——稅率、稅基、計算公式、申報與繳款期限、應備憑證，
一列對應一個（稅種 × 課稅情境 × 納稅人身分）。

放進系統而不是留在試算表的理由只有一個：**每一格都記錄了它依據哪些條文**。
條文一動，那一格（而且只有那一格）會被標記待覆核。

```
增值稅法#32 修正（15日 → 20日）
    └─ 申報期限 欄位 → 待覆核，附上新舊條文對照
       稅率 欄位     → 不受影響，維持已覆核
```

整列標記會讓每次修法都變成全表重審，覆核流程很快就會被忽略；因此是逐格判定。

### 內容從哪來

LLM 讀取**合併檢視**（母法條文＋各條的子法／公告補充）後抽取。只讀母法會得到
沒有範圍的稅率、沒有程序的期限——操作性內容都在實施條例與公告裡。

三道防線：

1. **引用必須存在。** 模型指向未出現在輸入中的條文時，該引用直接捨棄。
   模型可以誤讀條文的意思，但不能憑空發明條文。
2. **無引用即無信心。** 沒有條文依據的格子一律歸零信心並標記待覆核，
   不與有依據的內容混在一起呈現。
3. **人寫的贏過機器寫的。** 人工編輯或試算表匯入的格子，重新抽取時不會被覆蓋。

有兩個欄位標記為「人工判斷」，永遠不會被自動標記待覆核：
「租稅優惠」（「不適用特殊優惠」是從**沒有**條文推出來的結論，沒有任何 diff 能佐證）
與「徵收管理」（要哪些書表屬於稽徵實務，法條只寫到「向主管稅務機關申報」）。
無法靠讀 diff 關閉的標記，只會訓練覆核者忽略標記。

```bash
taxwatch documents --country CN                       # 先看有哪些法規可抽

# 引數接受 external_id、完整標題，或標題片段（片段撞到多份會列出候選）
taxwatch extract-requirements 中华人民共和国增值税法 --dry-run
taxwatch extract-requirements 中华人民共和国增值税法

pip install -e '.[xlsx]'                              # 匯入 .xlsx 需要
taxwatch import-requirements 申報規範.xlsx           # 匯入財務既有試算表
taxwatch review-queue                                 # 列出待覆核欄位
```

> 抓取器產生的 external_id 是機器編號（`c5251620`、文號），沒有人記得住，
> 所以指令一律也接受標題片段。`taxwatch documents` 列出目前收錄了什麼。

## Web Dashboard

| 路徑 | 內容 |
|---|---|
| `/` | 儀表板：統計、稅種卡片、最近異動時間軸 |
| `/tax-types` | 每個稅種的最新狀態與更新時間 |
| `/tax-types/{key}` | 單一稅種：相關法規（子母法）、異動與分析 |
| `/documents` | 法規清單與版本數 |
| `/documents/{id}` | 版本時間軸 + 任兩版本的條文並排比對 |
| `/documents/{id}/consolidated` | 合併檢視：母法逐條 + 子法／公告補充規定 |
| `/requirements` | 申報規範矩陣與待覆核清單 |
| `/requirements/{id}` | 單一課稅情境的各欄位、條文依據與引用原文 |
| `/changes` | 異動清單，可依區間／轄區／嚴重度篩選 |
| `/changes/{id}` | 條文對照、unified diff、LLM 深度分析與引用 |
| `/runs` | 抓取健康度與失敗稽核 |
| `/settings` | 目前生效的來源、LLM、Brave Search、Email 組態 |

## JSON API

`taxwatch serve` 同時提供 Dashboard 與完整 API（`/docs` 有 OpenAPI 文件）。

```
GET  /api/tax-types                          所有稅種狀態
GET  /api/tax-types/{key}                    單一稅種摘要
GET  /api/documents                          法規清單
GET  /api/documents/{id}/history             版本時間軸
GET  /api/documents/{id}/consolidated        母法條文 + 各條的子法補充
GET  /api/documents/{id}/at/{date}           指定日期的版本
GET  /api/documents/{id}/diff?from=&to=      任兩日期的條文級 diff
GET  /api/changes                            異動清單
GET  /api/changes/{id}                       異動詳情與分析
GET  /api/requirements                       申報規範清單
GET  /api/requirements/review                待覆核欄位
GET  /api/requirements/{id}                  單一情境的完整欄位與引用
PUT  /api/requirements/{id}/fields/{key}     人工覆核／修正單一欄位
GET  /api/entities/{key}/context             子母法脈絡
GET  /api/entities/{key}/impact              影響擴散
POST /api/runs                               觸發管線
GET  /api/runs                               執行紀錄與健康度
GET  /api/stats                              儀表板統計
```

## 外部佐證：語料庫優先，搜尋墊底

分析每筆異動時要回答「這條引用的文件到底寫了什麼」。兩個來源，權重不同：

```
異動條文 → 抽出引用的文號
   ├─ 1. 查本地語料庫  → 命中即為官方原文，可權威引用，不發網路請求
   └─ 2. 查不到才搜尋  → 第三方摘要，僅供指路，與原文衝突時以原文為準
```

prompt 把兩者分成不同章節呈現，語料庫段落**不帶**「非官方原文」的但書，
搜尋段落則保留。標記為廢止／失效的法規會加上 ⛔ 並要求 LLM 不得當作現行依據。

### 匯入語料庫

```bash
pip install -e ".[corpus]"
taxwatch import-corpus corpus.parquet --version 2026-02-27
```

以 [chinatax-policy-corpus](https://huggingface.co/datasets/salpt/chinatax-policy-corpus)
實測（5,593 筆、1984–2026、920 萬字）：

| 指標 | 數值 |
|---|---|
| 匯入耗時 | 0.8 秒 |
| 帶文號可供查表 | 4,876 筆（87%）|
| 標記廢止／失效 | 738 筆 |
| 正文引用可本地解析 | **55.3%** — 這些不再發出搜尋 |

> ⚠️ 該語料庫為 **CC-BY-NC-4.0（非商業）**。匯入的資料僅存於本地資料庫，
> TaxWatch 不會再散布；`.gitignore` 已排除 `*.parquet`。商業用途前請先確認授權。

### Brave Search

```bash
BRAVE_SEARCH_API_KEY=your-key
BRAVE_SEARCH_ENABLED=true
BRAVE_SEARCH_MAX_RESULTS=5
```

搜尋是 best-effort — 失敗或未設定 key 時會退化為「僅依原文分析」並下修 confidence，
不會讓分析中斷。

## 以語料庫當評測集

語料庫的 `tax_type` 是官方標註，可以拿來量測我們啟發式分類的真實準確率。
這輪評測（n=2,123）直接抓出三個 bug：

| 項目 | 修正前 | 修正後 |
|---|---|---|
| 文號辨識覆蓋率 | 65.9% | **94.9%** |
| 稅種分類準確率 | 60.5% | **69.1%** |

修掉的三個 bug：

1. **全形括號漏配** — `税总[发函]\[?\d{4}\]?` 只吃 ASCII `[]`，
   所有 `税总函〔2024〕5号` 全數對不上（313 筆）
2. **`土地增值税` 被判成增值稅** — 排序把 `vat` 放在 `property` 前面，
   而「土地增值税」含有「增值税」子字串
3. **引用抽取吃進前導詞** — `根据财税〔2026〕15号` 被抽成 `根据财税…`，
   導致語料庫永遠查不到、白白發出搜尋

`税费征管` 的歸類是真實語意歧義（「关于优化**企业所得税**预缴纳税申报事项」
官方標成徵管）。實測比較後採「徵管當 fallback」：只補其他分類判為 unknown 的情況，
總體 +5.2%，且無任何稅種退步。語料庫內的文件一律直接採用官方標籤，不猜。

## 開發

```bash
pytest              # 387 tests
ruff check .
```

測試全部使用離線 fixture 與 in-memory SQLite，CI 不會打真實網站。

## ⚠️ 免責

分析內容由 LLM 生成，僅供參考，非法律意見。生效日、稅率、金額等關鍵欄位
請以官方原文為準；實際適用請諮詢專業稅務人員。
