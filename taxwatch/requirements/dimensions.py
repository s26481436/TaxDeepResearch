"""Controlled dimension vocabularies for stable TaxRequirement identity.

Dimensions and vocabularies are maintained per (country, tax_key).
Do not share or force-unify vocabularies across distinct tax regimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DimensionValue:
    key: str
    label_zh: str
    description: str = ""


# Order of dimensions in identity_key:
# taxpayer_class | tax_scheme | subject_matter | scenario_key
DIMENSION_ORDER = (
    "taxpayer_class",
    "tax_scheme",
    "subject_matter",
    "scenario_key",
)

# Each dimension answers exactly one question. Where a dimension answered two,
# every row it touched had two defensible values and two runs picked different
# ones — the whole 信託 cluster swung between `trustee`, `beneficiary` and
# `resident_individual` across runs, because a trust beneficiary is genuinely
# all three: a role in a legal relationship, and a class of taxpayer.
#
# taxpayer_class answers only "what kind of entity is taxed" — residency and
# legal form. Roles in a particular relationship (受託人、受益人、扣繳義務人)
# belong to `scenario_key` and `tax_scheme`, which already carry them.
_TW_INCOME_TAXPAYER_CLASSES = (
    # Most provisions do not distinguish by taxpayer class at all — 房地合一,
    # 信託, and the withholding schedule apply to individuals and enterprises
    # alike. Without a value for that, the model had to name one of five
    # classes anyway, and two runs named different ones for the same row. An
    # answer the vocabulary cannot express gets invented.
    DimensionValue(
        "all_taxpayers",
        "不分主體類別（條文對各類納稅義務人一體適用）",
        "**預設值**。除非條文明確只適用某一類主體，或針對不同主體訂有不同規範，"
        "否則填本值。",
    ),
    DimensionValue("resident_individual", "中華民國境內居住之個人（居住者）"),
    DimensionValue("nonresident_individual", "非中華民國境內居住之個人（非居住者）"),
    DimensionValue("domestic_enterprise", "總機構在中華民國境內之營利事業"),
    DimensionValue("foreign_enterprise", "總機構在中華民國境外之營利事業"),
    DimensionValue("sole_proprietorship", "獨資、合夥組織之營利事業"),
)

# `not_taxable` answered a different question from the rest: the others say how
# the tax is collected, it says whether there is any. So every exemption could
# be filed as either 免稅 or 結算申報 with no way to choose, and 證券、期貨、
# 房地 each swung between the two. Whether an item is exempt is content of the
# row (`tax_base`, `formula`), not part of its identity.
_TW_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_filing", "結算申報／決算申報／清算申報"),
    DimensionValue(
        "withholding",
        "就源扣繳／扣繳申報",
        "本情境的規範內容是扣繳義務時選用。taxpayer_class 仍填所得歸屬人，"
        "不要填扣繳義務人。",
    ),
    DimensionValue("profit_distribution", "盈餘分配／未分配盈餘加徵"),
)

_TW_INCOME_SUBJECT_MATTERS = (
    DimensionValue("general_income", "一般所得（綜合所得／營利事業所得）"),
    DimensionValue("real_estate", "房屋土地交易所得（房地合一稅）"),
    DimensionValue("securities", "證券交易所得"),
    DimensionValue("futures", "期貨交易所得"),
    DimensionValue("trust_income", "信託財產發生之所得"),
    DimensionValue("salary_interest", "各類扣繳所得（薪資、利息、股利等）"),
)

_TW_INCOME_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般情境"),
    DimensionValue("post_2016_acquisition", "105年1月1日以後取得之房地交易（房地合一2.0）"),
    DimensionValue("presale_or_superficies", "預售屋買賣或設定地上權房地交易"),
    DimensionValue("indirect_shareholding", "符合特定條件之股權交易（視同房地交易）"),
    DimensionValue("beneficiary_identified", "受益人已確定存在"),
    DimensionValue("beneficiary_unidentified", "受益人不特定或尚未存在"),
    DimensionValue("public_trust", "公益信託"),
    DimensionValue("change_of_fiscal_year", "變更會計年度"),
    DimensionValue("loss_carryforward", "虧損扣除（前十年虧損互抵）"),
    DimensionValue("offshore_banking_unit", "國際金融業務分行（OBU）相關所得"),
)

# ---------------------------------------------------------------------------
# CN — 增值稅
#
# A separate regime, not a translation of the TW vocabulary. 中國的增值稅以
# 「一般納稅人／小規模納稅人」區分主體，以「一般計稅／簡易計稅」區分方法，
# 而台灣所得稅兩者皆無。Sharing values across jurisdictions would produce keys
# that look comparable and are not.
#
# The same three rules that took four rounds to settle on TW apply here:
#   1. 每個維度只回答一個問題
#   2. 「條文未區分」必須有值可填，否則模型會從具體值裡隨便挑一個
#   3. 非預設值 = 必須另成一列
_CN_VAT_TAXPAYER_CLASSES = (
    DimensionValue(
        "all_taxpayers",
        "不分納稅人類別（條文對各類納稅人一體適用）",
        "**預設值**。除非條文明確只適用某一類納稅人，否則填本值。",
    ),
    DimensionValue("general_taxpayer", "一般納稅人"),
    DimensionValue("small_scale_taxpayer", "小規模納稅人"),
    DimensionValue(
        "overseas_entity",
        "境外單位和個人（在境內發生應稅交易）",
        "境內購買方為扣繳義務人時，本維度仍填境外單位和個人——扣繳義務由 "
        "`tax_scheme=withholding` 表達。",
    ),
)

_CN_VAT_TAX_SCHEMES = (
    DimensionValue("general_method", "一般計稅方法（銷項稅額扣減進項稅額）"),
    DimensionValue("simplified_method", "簡易計稅方法（依徵收率計算）"),
    DimensionValue(
        "withholding",
        "扣繳",
        "境外單位和個人在境內發生應稅交易，由購買方扣繳時選用。",
    ),
)

_CN_VAT_SUBJECT_MATTERS = (
    DimensionValue("goods", "銷售貨物"),
    DimensionValue("processing_repair", "提供加工、修理修配勞務"),
    DimensionValue("services", "銷售服務"),
    DimensionValue("intangibles", "銷售無形資產"),
    DimensionValue("real_estate", "銷售不動產"),
    DimensionValue("imports", "進口貨物"),
)

_CN_VAT_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般情境"),
    DimensionValue("export_zero_rate", "出口貨物、跨境應稅交易適用零稅率"),
    DimensionValue("deemed_taxable", "視同應稅交易"),
    DimensionValue("mixed_sales", "混合銷售、兼營不同稅率或徵收率項目"),
    DimensionValue("used_fixed_assets", "銷售自己使用過的固定資產"),
    DimensionValue("real_estate_lease", "不動產經營租賃"),
    DimensionValue("small_scale_threshold", "小規模納稅人未達起徵點／月銷售額標準"),
)

_CN_VAT_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的主體」。扣繳義務人、代理人、"
    "承運人都不是合法值——那些義務由 `tax_scheme` 表達。",
    "條文若未依納稅人類別而有不同規範，填 `all_taxpayers`。"
    "**不要隨意挑一個具體類別。**",
    "`tax_scheme` 只回答「怎麼計稅」，不回答「課不課」。免稅、不徵稅、"
    "即徵即退**不是** tax_scheme 的值——那屬於該列的 `tax_base` 或 `formula` 內容。",
    "同一納稅人類別下，一般計稅與簡易計稅的稅率／徵收率完全不同，"
    "必須拆成不同的規範列。簡易計稅的各種徵收率（5%、3%、減按2%、減按1.5%、"
    "減按1%）屬於同一列的 `tax_rate` 內容，逐字照抄，不要只寫其中一種。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** `subject_matter` 與 "
    "`scenario_key` 只要有對應的專門值就必須使用並另成一列；"
    "`standard` 只用於沒有這些特別規定的情境。",
)

# ---------------------------------------------------------------------------
# CN — 企業所得稅 (cn_enterprise_income)
#
# 主體軸：居民企業／非居民企業（有無設立機構場所）
# 方法軸：查賬徵收／核定徵收、源泉扣繳、預繳申報、清算申報
# 標的軸：生產經營所得、清算所得、股息紅利、利息租金特許權使用費、財產轉讓所得
# 情境軸：小型微利企業、高新技術企業、研發費用加計扣除、特別納稅調整（關聯交易／受控外國企業）、常駐代表機構等
_CN_ENTERPRISE_INCOME_TAXPAYER_CLASSES = (
    DimensionValue(
        "all_taxpayers",
        "不分企業類別（條文對各類企業一體適用）",
        "**預設值**。除非條文明確只適用某一類企業，否則填本值。",
    ),
    DimensionValue("resident_enterprise", "居民企業（依法在中國境內成立，或實際管理機構在境內）"),
    DimensionValue(
        "nonresident_with_establishment",
        "非居民企業（在境內設立機構、場所）",
        "取得來源於中國境內且與其機構場所有實際聯繫之所得。",
    ),
    DimensionValue(
        "nonresident_without_establishment",
        "非居民企業（在境內未設立機構場所，或有機構場所但所得與之無實際聯系）",
        "取得來源於中國境內所得，通常適用源泉扣繳。",
    ),
)

_CN_ENTERPRISE_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_settlement", "年度匯算清繳（年終結算申報）"),
    DimensionValue("provisional_filing", "按月或按季預繳申報"),
    DimensionValue(
        "withholding",
        "源泉扣繳（支付人扣繳申報）",
        "非居民企業取得股息、利息、租金、特許權使用費或財產轉讓所得由支付人扣繳。",
    ),
    DimensionValue("deemed_profit_collection", "核定徵收（按核定應稅所得率或核定應納稅額徵收）"),
    DimensionValue("liquidation_filing", "清算所得申報"),
)

_CN_ENTERPRISE_INCOME_SUBJECT_MATTERS = (
    DimensionValue("production_business_income", "生產經營所得及其他一般所得"),
    DimensionValue("dividend_equity_income", "股息、紅利等權益性投資收益"),
    DimensionValue("interest_rental_royalty", "利息、租金、特許權使用費所得"),
    DimensionValue("property_transfer_income", "財產轉讓所得（含股權轉讓、不動產轉讓）"),
    DimensionValue("liquidation_income", "清算所得"),
    DimensionValue("deemed_sales_income", "視同銷售所得"),
)

_CN_ENTERPRISE_INCOME_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般情境"),
    DimensionValue("small_low_profit_enterprise", "小型微利企業（優惠稅率與減免）"),
    DimensionValue("high_tech_enterprise", "高新技術企業、技術先進型服務企業（15%優惠稅率）"),
    DimensionValue("rnd_expense_super_deduction", "研發費用加計扣除"),
    DimensionValue("accelerated_depreciation", "固定資產加速折舊／一次性扣除"),
    DimensionValue("loss_carryforward", "虧損結轉彌補（一般5年，特定企業最長8/10年）"),
    DimensionValue("special_tax_adjustment", "特別納稅調整（關聯交易、轉讓定價、受控外國企業、資本弱化）"),
    DimensionValue("enterprise_restructuring", "企業重組特殊性／一般性稅務處理"),
    DimensionValue("foreign_representative_office", "外國企業常駐代表機構經費支出換算收入徵稅等專門規定"),
    DimensionValue("nonprofit_organization", "非營利組織免稅收入與申報"),
)

_CN_ENTERPRISE_INCOME_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的主體」——居民企業／非居民企業。"
    "**扣繳義務人、清算人、代理人是角色，不是主體類別**——扣繳義務由 `tax_scheme=withholding` 表達。",
    "條文若未依企業類別而有不同規範（如收入認列通則、一般扣除項目、稅收徵管），"
    "`taxpayer_class` 填 `all_taxpayers`。**不要隨意挑一個具體類別。**",
    "`tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」——免稅、減計收入、"
    "稅額抵免**不是** tax_scheme 的值，屬於該列的 `tax_base` 或 `formula` 內容。",
    "非居民企業在境內未設立機構場所取得所得，由支付人代扣代繳時，"
    "`taxpayer_class` 填 `nonresident_without_establishment`，`tax_scheme` 填 `withholding`。",
    "外國企業常駐代表機構屬於在境內設立機構、場所（`taxpayer_class=nonresident_with_establishment`），"
    "其按經費支出換算收入等專門規定由 `scenario_key=foreign_representative_office` 表達。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** 條文若規範的是小型微利企業、"
    "高新技術企業、研發費用加計扣除、固定資產加速折舊、虧損結轉、特別納稅調整、企業重組、常駐代表機構等特定優惠或專項規則，"
    "必須使用對應的 `scenario_key` 並另成一列，不得填 `standard` 併入一般情境。",
    "標的若為股息紅利、利息租金特許權、財產轉讓、清算所得等特定標的，"
    "必須填對應的 `subject_matter` 並另成一列；`production_business_income` 僅用於一般生產經營所得。",
)

# ---------------------------------------------------------------------------
# CN — 個人所得稅 (cn_individual_income)
#
# 主體軸：居民個人／非居民個人
# 方法軸：綜合所得年度匯算、預扣預繳、分類所得按次／按月代扣代繳、經營所得按年申報
# 標的軸：工資薪金、勞務報酬、稿酬、特許權使用費、經營所得、利息股息紅利、財產租賃、財產轉讓、偶然所得
# 情境軸：專項附加扣除、全年一次性獎金、外籍個人補貼、股權激勵、個人養老金、經營所得核定徵收等
_CN_INDIVIDUAL_INCOME_TAXPAYER_CLASSES = (
    DimensionValue(
        "all_taxpayers",
        "不分個人類別（條文對全體個人一體適用）",
        "**預設值**。除非條文明確只適用某一類主體，否則填本值。",
    ),
    DimensionValue("resident_individual", "居民個人（在中國境內有住所，或無住所而在境內居住滿183天）"),
    DimensionValue("nonresident_individual", "非居民個人（在中國境內無住所且不居住，或居住不滿183天）"),
    DimensionValue("sole_proprietor_partner", "個體工商戶業主、個人獨資企業投資者、合夥企業個人合夥人"),
)

_CN_INDIVIDUAL_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_comprehensive_settlement", "綜合所得年度匯算清繳（次年3月1日至6月30日）"),
    DimensionValue("withholding_advance_payment", "扣繳義務人預扣預繳（工資薪金累計預扣法、勞務/稿酬/特許權預扣）"),
    DimensionValue("withholding_categorized", "分類所得按次／按月代扣代繳（利息股息紅利、財產租賃、財產轉讓、偶然所得）"),
    DimensionValue("business_income_annual_filing", "經營所得按年申報（按月/按季預繳，次年3月31日前匯算清繳）"),
    DimensionValue("self_declaration", "納稅人自覺申報（取得境外所得、無扣繳義務人等自行申報）"),
)

_CN_INDIVIDUAL_INCOME_SUBJECT_MATTERS = (
    DimensionValue("comprehensive_income", "綜合所得（工資薪金、勞務報酬、稿酬、特許權使用費合併）"),
    DimensionValue("wages_salaries", "工資、薪金所得"),
    DimensionValue("remuneration_for_services", "勞務報酬所得"),
    DimensionValue("manuscript_remuneration", "稿酬所得"),
    DimensionValue("royalties", "特許權使用費所得"),
    DimensionValue("business_income", "經營所得（個體工商戶、獨資合夥、承包承租經營）"),
    DimensionValue("interest_dividends", "利息、股息、紅利所得"),
    DimensionValue("property_leasing", "財產租賃所得"),
    DimensionValue("property_transfer", "財產轉讓所得（股權、不動產轉讓等）"),
    DimensionValue("contingent_income", "偶然所得（中獎、受贈等）"),
)

_CN_INDIVIDUAL_INCOME_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般情境"),
    DimensionValue("special_additional_deductions", "專項附加扣除（子女教育、繼續教育、大病醫療、住房貸款利息、住房租金、贍養老人、3歲以下嬰幼兒照護）"),
    DimensionValue("annual_one_off_bonus", "全年一次性獎金單獨計稅優惠"),
    DimensionValue("equity_incentives", "上市公司股權激勵、非上市公司股權獎勵遞延納稅"),
    DimensionValue("foreign_allowances", "外籍個人八項津貼補貼（住房、子女教育等）及稅收協定待遇"),
    DimensionValue("individual_pension", "個人養老金遞延納稅優惠"),
    DimensionValue("severance_pay", "解除勞動關係一次性補償金"),
    DimensionValue("personal_transfer_housing", "個人轉讓自用達5年以上且為唯一家庭生活用房"),
    DimensionValue("business_deemed_collection", "經營所得核定徵收"),
)

_CN_INDIVIDUAL_INCOME_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的主體」——居民個人／非居民個人／經營主體。"
    "**扣繳義務人是角色，不是主體類別**——扣繳責任由 `tax_scheme=withholding_advance_payment` 或 `withholding_categorized` 表達。"
    "無住所外籍個人依境內居住天數分別歸入 `resident_individual` 或 `nonresident_individual`，"
    "其外籍專屬津貼補貼等規定由 `scenario_key=foreign_allowances` 表達，不得混淆主體類別。",
    "條文若對居民與非居民一體適用，`taxpayer_class` 填 `all_taxpayers`。**不要隨意挑一個具體類別。**",
    "`tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」——免稅所得（如國債利息、保險賠款）、"
    "減徵所得**不是** tax_scheme 的值，屬於該列的 `tax_base` 或 `formula` 內容。",
    "綜合所得的四項（工資薪金、勞務報酬、稿酬、特許權使用費）在年度匯算時合併為 `comprehensive_income`，"
    "在預扣預繳階段則分別適用不同預扣規則，應按具體所得類型填寫 `subject_matter` 並與預扣方式匹配。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** 條文若規範的是專項附加扣除、"
    "全年一次性獎金、股權激勵、外籍津貼、個人養老金、離職補償金、換購自住房退稅等特定政策，"
    "必須使用對應的 `scenario_key` 並另成一列，不得填 `standard` 併入一般情境。",
    "不同分類所得（利息股息、財產租賃、財產轉讓、偶然所得、經營所得）稅率與計稅方式互異，"
    "必須使用專門的 `subject_matter` 並另成一列。",
)

# ---------------------------------------------------------------------------
# US — 聯邦所得稅 (us_income: IRC / 26 CFR)
#
# 主體軸：individual / corporation / partnership / s_corporation / trust_estate / nonresident_alien / foreign_corporation
# 方法軸：annual_return / withholding / estimated_tax / backup_withholding / information_return
# 標的軸：general_taxable_income / wages_compensation / capital_gains / dividends_interest / effectively_connected_income / fixed_determinable_annual_periodical / pass_through_income
# 情境軸：standard, alternative_minimum_tax, section_179_depreciation, net_operating_loss, global_intangible_low_taxed_income, foreign_tax_credit, qualified_business_income
_US_INCOME_TAXPAYER_CLASSES = (
    DimensionValue(
        "all_taxpayers",
        "不分納稅主體類別（適用於全體納稅人）",
        "**預設值**。除非條文明確只適用特定組織型態或個人，否則填本值。",
    ),
    DimensionValue("individual", "美國公民或稅務居民個人 (U.S. Citizen or Resident Alien)"),
    DimensionValue("corporation", "一般 C 公司 (C Corporation, IRC Sec. 11)"),
    DimensionValue("partnership", "合夥事業及多成員穿透個體 (Partnership, Form 1065)"),
    DimensionValue("s_corporation", "小型企業 S 公司 (S Corporation, Form 1120-S)"),
    DimensionValue("trust_estate", "信託與遺產 (Trusts and Estates, Form 1041)"),
    DimensionValue("nonresident_alien", "非居住外國人個人 (Nonresident Alien Individual, Form 1040-NR)"),
    DimensionValue("foreign_corporation", "外國公司 (Foreign Corporation, Form 1120-F)"),
    DimensionValue("tax_exempt_organization", "免稅機構及非營利組織 (Tax-Exempt Organization, Form 990)"),
)

_US_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_return", "年度所得稅申報 (Annual Income Tax Return, e.g. Form 1040/1120)"),
    DimensionValue("withholding", "就源扣繳申報 (Withholding Tax, e.g. Wage Withholding, NRA 30% or Treaty Withholding, Form 1042)"),
    DimensionValue("estimated_tax", "按季預估稅款申報與繳納 (Estimated Tax Payments, Form 1040-ES/1120-W)"),
    DimensionValue("backup_withholding", "後備扣繳 (Backup Withholding, IRC Sec. 3406)"),
    DimensionValue("information_return", "資訊性申報 (Information Returns, e.g. Form 1099/W-2/K-1 reporting)"),
)

_US_INCOME_SUBJECT_MATTERS = (
    DimensionValue("general_taxable_income", "一般應稅所得 (Gross Income / General Taxable Income)"),
    DimensionValue("wages_compensation", "薪資、獎金與勞務報酬 (Wages, Salaries, and Compensation)"),
    DimensionValue("capital_gains", "資本利得（長期與短期資本利得）(Capital Gains and Losses)"),
    DimensionValue("dividends_interest", "股利與利息所得 (Qualified/Ordinary Dividends and Interest)"),
    DimensionValue("effectively_connected_income", "與美國貿易或業務實際關聯之所得 (Effectively Connected Income - ECI)"),
    DimensionValue("fixed_determinable_income", "固定、可確定、年度或定期之所得 (FDAP Income, IRC Sec. 871/881)"),
    DimensionValue("pass_through_income", "穿透實體所得分派 (Distributive Share of Partnership/S-Corp Income)"),
    DimensionValue("branch_profits", "外國公司分公司利潤稅 (Branch Profits Tax, IRC Sec. 884)"),
)

_US_INCOME_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般申報情境 (Standard Filing Scenario)"),
    DimensionValue("alternative_minimum_tax", "最低稅負制 (Alternative Minimum Tax - AMT, Individual & Corporate)"),
    DimensionValue("section_179_depreciation", "第179條資本資產費用化扣除與加急折舊 (Section 179 and Bonus Depreciation)"),
    DimensionValue("net_operating_loss", "營業淨虧損結轉 (Net Operating Loss - NOL Carryover)"),
    DimensionValue("global_intangible_low_taxed_income", "全球無形低稅所得 (GILTI / Subpart F Income, IRC Sec. 951A)"),
    DimensionValue("foreign_tax_credit", "外國稅額扣抵 (Foreign Tax Credit, Form 1116/1118)"),
    DimensionValue("qualified_business_income", "合格商業所得扣除 (Section 199A QBI Deduction)"),
)

_US_INCOME_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的法律實體或個人主體」——個人、C公司、合夥事業、外國主體等。"
    "**扣繳義務人 (Withholding Agent)、受託人 (Fiduciary/Trustee)、發放人 (Payer) 是角色，不是主體類別**——扣繳義務由 `tax_scheme=withholding` 表達。",
    "條文若未針對特定實體類型區分（如廣泛適用的折舊、會計方法、申報一般規定），"
    "`taxpayer_class` 填 `all_taxpayers`。**不要隨意挑選一個具體實體。**",
    "`tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」——豁免 (Exclusion)、免稅 (Exemption) "
    "**不是** tax_scheme 的值，屬於該列的 `tax_base` 或 `formula` 內容。",
    "個人所得稅率級距依報稅身分（Single／Married Filing Jointly／Married Filing Separately／Head of Household／Qualifying Surviving Spouse）而異。"
    "各報稅身分的級距與標準扣除額屬於同一列 `tax_rate` 與 `deductions` 的內容，逐字照抄全部，不要為每個報稅身分另成一列。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** 條文若規範的是 "
    "AMT 最低稅負制、Section 179 加急折舊、NOL 虧損結轉、GILTI/Subpart F 境外所得、Section 199A QBI 扣除等專門制度，"
    "必須使用對應的 `scenario_key` 並另成一列，不得填 `standard` 併入一般情境。",
    "資本利得 (Capital Gains)、股利利息 (Dividends/Interest)、外國人 ECI 或 FDAP 所得等各具獨立稅率與課稅機制，"
    "必須填對應的 `subject_matter` 並另成一列。",
)

# Rules that resolve choices the vocabulary alone leaves open. Every rule here
# exists because two runs of the same statute answered it differently.
_TW_INCOME_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的主體」——居住者/非居住者、"
    "營利事業/個人。**不要填在法律關係中的角色**：受託人、受益人、"
    "扣繳義務人、代理人都不是合法值。",
    "條文若未依主體類別而有不同規範（房地合一、信託、各類所得扣繳多屬此類），"
    "`taxpayer_class` 填 `all_taxpayers`。**不要從五類主體中隨意挑一個** ——"
    "只有條文明確限定適用對象時，才填具體類別。",
    "信託所得填受益人本身的主體類別（通常是 `resident_individual` 或 "
    "`domestic_enterprise`），並以 `subject_matter=trust_income` 標示其為信託所得；"
    "受益人是否確定、是否為公益信託則由 `scenario_key` 表達"
    "（`beneficiary_identified`／`beneficiary_unidentified`／`public_trust`）。",
    "受益人不特定或尚未存在時以受託人為納稅義務人（所得稅法第3條之4第3項），"
    "此時 `scenario_key=beneficiary_unidentified`，`taxpayer_class` 仍填受託人的主體類別。",
    "`tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」。"
    "免稅、停徵、不計入所得總額**不是** tax_scheme 的值——"
    "那屬於該列的 `tax_base` 或 `formula` 內容，仍依實際申報方式填 tax_scheme。",
    "若同一條文同時規範所得人與扣繳義務人，視為同一個情境，"
    "填所得人的 taxpayer_class 並以 `tax_scheme=withholding` 標示。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** 條文若規範的標的"
    "對應到 `subject_matter` 的某個專門值（證券交易所得、期貨交易所得、"
    "房地交易所得、信託財產所得），必須使用該值並另成一列，"
    "不得併入 `general_income`。`general_income` 只用於條文未特別規範的一般所得。",
    "`scenario_key` 同理：條文若規範的是房地合一2.0、預售屋、視同房地交易、"
    "變更會計年度、虧損扣除、OBU 等特定情境，必須填對應的值並另成一列，"
    "不得填 `standard` 併入一般情境。`standard` 只用於沒有這些特別規定的情境。",
)

_RULES: dict[tuple[str, str], tuple[str, ...]] = {
    ("TW", "tw_income"): _TW_INCOME_RULES,
    ("CN", "cn_vat"): _CN_VAT_RULES,
    ("CN", "cn_enterprise_income"): _CN_ENTERPRISE_INCOME_RULES,
    ("CN", "cn_individual_income"): _CN_INDIVIDUAL_INCOME_RULES,
    ("US", "us_income"): _US_INCOME_RULES,
}


def get_identity_rules(country: str, tax_key: str) -> tuple[str, ...]:
    """Disambiguation rules for dimension choices in this tax regime."""
    return _RULES.get((country.upper(), tax_key), ())



_REGISTRY: dict[tuple[str, str], dict[str, tuple[DimensionValue, ...]]] = {
    ("TW", "tw_income"): {
        "taxpayer_class": _TW_INCOME_TAXPAYER_CLASSES,
        "tax_scheme": _TW_INCOME_TAX_SCHEMES,
        "subject_matter": _TW_INCOME_SUBJECT_MATTERS,
        "scenario_key": _TW_INCOME_SCENARIO_KEYS,
    },
    ("CN", "cn_vat"): {
        "taxpayer_class": _CN_VAT_TAXPAYER_CLASSES,
        "tax_scheme": _CN_VAT_TAX_SCHEMES,
        "subject_matter": _CN_VAT_SUBJECT_MATTERS,
        "scenario_key": _CN_VAT_SCENARIO_KEYS,
    },
    ("CN", "cn_enterprise_income"): {
        "taxpayer_class": _CN_ENTERPRISE_INCOME_TAXPAYER_CLASSES,
        "tax_scheme": _CN_ENTERPRISE_INCOME_TAX_SCHEMES,
        "subject_matter": _CN_ENTERPRISE_INCOME_SUBJECT_MATTERS,
        "scenario_key": _CN_ENTERPRISE_INCOME_SCENARIO_KEYS,
    },
    ("CN", "cn_individual_income"): {
        "taxpayer_class": _CN_INDIVIDUAL_INCOME_TAXPAYER_CLASSES,
        "tax_scheme": _CN_INDIVIDUAL_INCOME_TAX_SCHEMES,
        "subject_matter": _CN_INDIVIDUAL_INCOME_SUBJECT_MATTERS,
        "scenario_key": _CN_INDIVIDUAL_INCOME_SCENARIO_KEYS,
    },
    ("US", "us_income"): {
        "taxpayer_class": _US_INCOME_TAXPAYER_CLASSES,
        "tax_scheme": _US_INCOME_TAX_SCHEMES,
        "subject_matter": _US_INCOME_SUBJECT_MATTERS,
        "scenario_key": _US_INCOME_SCENARIO_KEYS,
    },
}


def registered_regimes() -> tuple[tuple[str, str], ...]:
    """Every (country, tax_key) with a controlled vocabulary.

    Exported so the invariants that took four rounds to settle can be asserted
    over every regime at once, rather than re-learned per jurisdiction.
    """
    return tuple(sorted(_REGISTRY))


def get_dimensions_vocabulary(country: str, tax_key: str) -> dict[str, tuple[DimensionValue, ...]]:
    """Return legal dimension values and explanations for (country, tax_key).

    Returns empty dict if no vocabulary is defined for the tax regime.
    """
    return _REGISTRY.get((country.upper(), tax_key), {})


def compute_identity_key(dimensions: dict[str, str] | None) -> str:
    """Build composite identity_key strictly following DIMENSION_ORDER.

    Requires ALL four dimensions to be non-empty. If any dimension is missing or empty,
    returns "" to prevent partial identity collisions and preserve safety.
    """
    if not dimensions:
        return ""
    values = [dimensions.get(k, "").strip() for k in DIMENSION_ORDER]
    if not all(values):
        return ""
    return "|".join(values)


def validate_dimensions(
    country: str,
    tax_key: str,
    raw_dimensions: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
    """Validate dimension values against vocabulary.

    Returns:
        (valid_dimensions, unknown_values_list, missing_dimensions_list)
        - unknown_values_list: list of (dimension_name, received_invalid_value)
        - missing_dimensions_list: list of dimension_names that were empty/missing (only when some other dimension was present)
    """
    vocab = get_dimensions_vocabulary(country, tax_key)
    if not vocab:
        # Undefined vocabulary: return empty dimensions, no errors
        return {}, [], []

    valid_dims: dict[str, str] = {}
    unknowns: list[tuple[str, str]] = []
    missing: list[str] = []

    has_any_input = any(bool(str(raw_dimensions.get(d) or "").strip()) for d in DIMENSION_ORDER)

    for dim_name in DIMENSION_ORDER:
        val = str(raw_dimensions.get(dim_name) or "").strip()
        if not val:
            valid_dims[dim_name] = ""
            if has_any_input:
                missing.append(dim_name)
            continue
        allowed_keys = {item.key for item in vocab.get(dim_name, ())}
        if val in allowed_keys:
            valid_dims[dim_name] = val
        else:
            unknowns.append((dim_name, val))
            valid_dims[dim_name] = ""

    return valid_dims, unknowns, missing
