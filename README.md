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

### 子母法層級（中國）

```
法律（企业所得税法）
  └─ 行政法规（实施条例）
       └─ 部门规章（管理办法／细则）
            └─ 规范性文件（公告／通知／批复）
```

以文號（`财税〔2026〕15号`、`国家税务总局公告2026年第5号`）作為穩定識別鍵，
citation 抽取會同時建立母法引用與廢止關係。

## Web Dashboard

| 路徑 | 內容 |
|---|---|
| `/` | 儀表板：統計、稅種卡片、最近異動時間軸 |
| `/tax-types` | 每個稅種的最新狀態與更新時間 |
| `/tax-types/{key}` | 單一稅種：相關法規（子母法）、異動與分析 |
| `/documents` | 法規清單與版本數 |
| `/documents/{id}` | 版本時間軸 + 任兩版本的條文並排比對 |
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
GET  /api/documents/{id}/at/{date}           指定日期的版本
GET  /api/documents/{id}/diff?from=&to=      任兩日期的條文級 diff
GET  /api/changes                            異動清單
GET  /api/changes/{id}                       異動詳情與分析
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
pytest              # 179 tests
ruff check .
```

測試全部使用離線 fixture 與 in-memory SQLite，CI 不會打真實網站。

## ⚠️ 免責

分析內容由 LLM 生成，僅供參考，非法律意見。生效日、稅率、金額等關鍵欄位
請以官方原文為準；實際適用請諮詢專業稅務人員。
