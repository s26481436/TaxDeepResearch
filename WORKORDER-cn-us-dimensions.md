# 工單：補齊 CN／US 的受控身分維度詞彙表

## 背景（先讀這段，不要跳過）

`taxwatch/requirements/dimensions.py` 定義每個 `(country, tax_key)` 的受控身分維度。
目前只有 `("TW","tw_income")` 與 `("CN","cn_vat")` 有詞彙表。

這些維度組成 `identity_key`，而 `identity_key` 是 `_upsert_requirement` 的第一比對鍵——
它決定重跑抽取時，一列規範是「就地更新」還是「新增成另一列」。詞彙表設計錯誤的後果不是
美觀問題，是矩陣每跑一次就長出一批重複列。

`tw_income` 的詞彙表花了四輪（req-v7 → req-v10）才穩定下來，每一輪都是因為兩次執行
產生了不同的身分。那四輪的結論已經寫成 `tests/test_dimension_invariants.py`，會對
**每一組**已註冊的轄區自動斷言。**先把那個檔案讀完再動手。**

## 任務

在 `dimensions.py` 新增三組詞彙表與規則：

1. `("CN", "cn_enterprise_income")` — 企業所得稅
2. `("CN", "cn_individual_income")` — 個人所得稅
3. `("US", "us_income")` — 美國聯邦所得稅（IRC / CFR Title 26）

每一組都要：
- 在 `_REGISTRY` 註冊四個維度（`taxpayer_class` / `tax_scheme` / `subject_matter` / `scenario_key`）
- 在 `_RULES` 註冊取捨規則
- 依 `("CN","cn_vat")` 的寫法為範本（同一檔案內，就在上面）

## 必須遵守的設計原則

這五條不是風格偏好，每一條都對應一次實際的失敗：

1. **一個維度只回答一個問題。**
   `taxpayer_class` 只回答「納稅的是什麼樣的主體」。扣繳義務人、受託人、代理人是
   *角色*，不是主體類別 —— 曾經同時列出兩者，模型每次挑不同的，整群信託列在
   `trustee` / `beneficiary` / `resident_individual` 之間擺盪。
   同理 `tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」：免稅、不課稅、
   停徵**不是** tax_scheme 的值。

2. **「條文未區分」必須有值可填。**
   每組 `taxpayer_class` 的第一個值必須是 `all_taxpayers`，description 要含「預設值」
   三個字。沒有這個值時，模型面對「對各類納稅人一體適用」的條文仍會從具體類別裡
   挑一個，兩次挑不同的。`scenario_key` 同理必須有 `standard`。

3. **非預設值 = 必須另成一列。**
   規則中必須明寫這句話（測試會檢查「另成一列」四個字）。只說什麼*不該*拆列，
   拆列就變成選擇性的，涵蓋率於是每次執行都不同。

4. **不得跨轄區共用。**
   台灣跟中國是兩個不同的課稅主體。不要把 TW 的值複製過去改個標籤，也不要為了
   「一致」而讓兩邊長一樣。US 的 `filing_status`（single / married filing jointly …）
   在 CN 沒有對應物，CN 的「居民企業／非居民企業」在 US 也沒有。

5. **鍵是 ascii snake_case，標籤用繁體中文。**
   `identity_key` 是用 `|` 串接的字串，鍵含空白或 `|` 會直接壞掉。
   US 的鍵用英文（`single`、`married_filing_jointly`），`label_zh` 仍寫繁體中文——
   這個系統的讀者是台灣財務人員。

## 各稅種的提示（起點，不是標準答案）

**CN 企業所得稅**：居民企業／非居民企業（有無設立機構場所）是主體軸；
查賬徵收／核定徵收、源泉扣繳是方法軸；不同所得類型是標的軸。

**CN 個人所得稅**：居民個人／非居民個人是主體軸；綜合所得年度匯算、分類所得、
經營所得、預扣預繳是方法軸；工資薪金、勞務報酬、稿酬、特許權使用費、經營所得、
利息股息紅利、財產租賃、財產轉讓、偶然所得是標的軸。

**US 所得稅**：individual / corporation / partnership / trust_estate 是主體軸；
annual_return、withholding、estimated_tax 是方法軸；filing status 屬於 `scenario_key`。

## 驗收標準

```
.venv/bin/python -m pytest -q
```

必須全綠。`test_dimension_invariants.py` 會對你新增的三組逐一檢查上述原則。
**不要為了讓測試過而修改 `test_dimension_invariants.py`** —— 那個檔案是規格。
如果你認為某條不變式本身有問題，回報給我，不要自己改。

## 範圍限制

- **只動 `taxwatch/requirements/dimensions.py`，以及必要時新增測試。**
- 不要碰 `extract.py`、`prompts.py`、`resolver.py`、`cli.py`。
- 不要調整 `PROMPT_VERSION`（詞彙表是資料，不是 prompt 結構的改變）。
- 不要順手重構其他東西。先前有過把不相關的 `normalize_entity_key` 改動夾帶在
  一個 commit 裡的情況，那次被退回了。

## 交付

開一個 PR，標題 `feat(requirements): CN 企業／個人所得稅與 US 所得稅受控維度`。
PR 內文說明每一組的主體軸／方法軸／標的軸是怎麼切的，以及**你對哪些值沒有把握**——
不確定的地方講出來比猜一個填進去有用，因為未被使用的值會在抽取時以
`○ N/M 個受控維度值本次沒有對應的規範列` 顯示出來，是可以事後修正的。

完成後回報給我進行檢查。
