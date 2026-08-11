# Taiwan MOJ Law Connector Validation Report

**Date**: 2026-08-11  
**Status**: ⚠️ Unvalidated - API Inaccessible  
**Severity**: High - Core TW connector cannot be tested

## Executive Summary

The Taiwan MOJ Law connector (`taxwatch.connectors.tw_moj_law`) is a critical part of the law-tracking system for Taiwan. However, **the API endpoints used by this connector are currently inaccessible (returning 403 Forbidden)**, and the connector has **never been validated against real data**.

### Key Findings

| Item | Status | Notes |
|------|--------|-------|
| **API Accessibility** | ❌ 403 Forbidden | All endpoints blocked |
| **Test Coverage** | ⚠️ Partial | 11 unit tests with mocks, 0 integration tests |
| **Real Data Validation** | ❌ Never tested | No verification against actual API responses |
| **Pcode List** | ✅ Defined | 8 hardcoded tax law pcodes |
| **Parser Implementation** | ✅ Complete | JSON parser exists but untested |
| **Date Parsing** | ✅ Working | ROC date parser tested and validated |

---

## Detailed Findings

### 1. API Inaccessibility

**Endpoints Tested**:
```
GET https://law.moj.gov.tw/api/LawData/LawInfo
GET https://law.moj.gov.tw/api/LawData/LawAllArticle
GET https://law.moj.gov.tw/LawClass/LawAll.aspx
```

**Result**: All endpoints return `403 Forbidden`

**Root Causes** (Likely):
- Web Application Firewall (WAF) blocking non-browser requests
- Geographic IP blocking (if environment is outside TW)
- API deprecated or moved to different endpoint
- Missing authentication/API key requirement
- User-Agent or referer header validation

**Tested Mitigations**:
- ✅ Added proper User-Agent headers
- ✅ Added Referer headers
- ✅ Tried alternative endpoint patterns
- ❌ All failed

### 2. Hardcoded Pcode List

The connector uses these 8 tax law pcodes (配置代號):

```python
{
    "G0340001": "所得稅法" (Income Tax Law),
    "G0340002": "營利事業所得稅查核準則" (Corp Income Tax Audit Standards),
    "G0340003": "營業稅法" (Business/Sales Tax Law),
    "G0340004": "遺產及贈與稅法" (Estate and Gift Tax Law),
    "G0340050": "房屋稅條例" (House Tax Rules),
    "G0340060": "土地稅法" (Land Tax Law),
    "G0340070": "稅捐稽徵法" (Tax Collection Law),
    "G0340080": "特種貨物及勞務稅條例" (Special Commodity & Service Tax),
}
```

**Status**: ⚠️ **Not Validated** - These pcodes have never been tested against the actual API

**Critical Questions**:
- Are these pcode values correct for the current API?
- Are there additional tax law pcodes we're missing?
- Do these pcodes still exist or have they changed?

### 3. Expected API Response Structure

The TW law JSON normalizer (`taxwatch.normalize.tw_law_json.py`) expects:

```json
{
    "LawName": "所得稅法",
    "LawArticles": [
        {
            "ArticleNo": "第 1 條",
            "ArticleContent": "content text..."
        },
        {
            "ArticleNo": "第 2 條",
            "ArticleContent": "content text..."
        }
    ]
}
```

**Status**: ⚠️ **Never verified** - This structure was assumed but never tested against real API responses

### 4. Date Parsing

**Status**: ✅ **Working** - ROC date parser is implemented and tested

Supports formats like:
- "民國 113 年 01 月 03 日" → 2024-01-03
- "113年01月03日" → 2024-01-03
- Various spacing variants

---

## Test Coverage

### Current Tests (11 passing)

**File**: `tests/test_tw_moj_connector.py`

1. ✅ `discover()` returns DocumentRefs for each pcode
2. ✅ `discover()` parses ROC dates correctly  
3. ✅ `discover()` handles API failures gracefully
4. ✅ `fetch()` returns RawDocument with JSON content
5. ✅ `fetch()` uses pcode from metadata
6. ✅ ROC date parsing (5 variants)
7. ✅ Pcode list validation

**Gap**: All tests use **mocked API responses**. Zero integration tests with real API.

### Missing Tests

- 🔴 Integration test against actual API
- 🔴 Verification of real pcode values
- 🔴 Validation of API response structure
- 🔴 Testing all 8 law pcodes
- 🔴 Article parsing and normalization end-to-end
- 🔴 Comparing results with official law.moj.gov.tw website

---

## Recommendations

### Immediate Actions (This Session)

#### Option A: Fix the API Access (Preferred)
1. Investigate why 403 errors occur:
   - Try using Playwright (browser automation) as fallback
   - Contact Taiwan MOJ for API documentation/authentication
   - Check if there's a new API endpoint documented

2. Once API is working:
   - Run integration tests against real data
   - Validate all 8 pcode values
   - Verify JSON response structure
   - Test article parsing end-to-end

#### Option B: Fallback to HTML Scraping
1. Since the HTML pages might be accessible when the API isn't:
   - Implement HTML scraper (law.moj.gov.tw uses .aspx pages)
   - Parse article content from HTML tables/divs
   - Extract dates and law names from page structure

2. Advantages:
   - More resilient than API (less likely to require authentication)
   - Can handle page structure changes with CSS selector updates
   - Still provides article-level granularity

3. Implementation:
   ```python
   # In tw_moj_law.py, add fallback:
   def fetch(self, ref):
       try:
           # Try API first
           return self._fetch_from_api(ref)
       except (403, 404, ConnectionError):
           # Fallback to HTML scraping
           return self._fetch_from_html(ref)
   ```

### Medium Term (Next Session)

1. **Validate Against Data.gov.tw**
   - Once TW data.gov.tw datasets are confirmed accessible
   - Cross-validate TW connector output against official data
   - Check datasets 18289 (laws), 18290 (orders), 7382, 8372

2. **Discover Missing Laws**
   - Use data.gov.tw to find any tax-related laws not in our 8-pcode list
   - Check for recent law additions (last 2 years)
   - Expand pcode list if needed

3. **Version History**
   - If data.gov.tw includes 沿革 (revision history)
   - Load 6-year historical timeline for TW laws
   - Populate empty version_history for existing documents

### Long Term

1. **Automated Validation**
   - Add CI test that validates TW connector monthly (if/when API works)
   - Cross-reference results with official law.moj.gov.tw
   - Alert if missing expected laws or articles

2. **Connector Redundancy**
   - If API continues to be unreliable:
   - Maintain both HTML scraper and API versions
   - Use HTML scraper as primary, API as secondary verification

3. **Documentation**
   - Document actual API behavior (when accessible)
   - Create integration test fixtures from real API responses
   - Maintain list of known pcode values with official Chinese names

---

## Environment Context

**Session Date**: 2026-08-11  
**User Comment**: "放行了 data.gov.tw" (unblocked data.gov.tw access)  
**Network Status**: law.moj.gov.tw returns 403 from this environment

This suggests a possible environmental blocking (WAF, geo-IP, or proxy) rather than fundamental API deprecation.

---

## Files Modified

- ✅ `tests/test_tw_moj_connector.py` — New test file (11 tests)
- ✅ `CONNECTOR_VALIDATION_REPORT.md` — This report

## Files Not Changed (Because Not Validated)

- `taxwatch/connectors/tw_moj_law.py` — Would need real API data to test changes
- `taxwatch/normalize/tw_law_json.py` — JSON structure assumption unverified
- `config/sources.yaml` — Pcode list needs validation before use

---

## Next Steps

**Recommended Priority**: 
1. **Fix API access** (Option A) or implement **HTML scraper fallback** (Option B)
2. Once working: Run integration tests
3. Validate against data.gov.tw datasets

**Current Blocker**: law.moj.gov.tw returns 403, preventing validation

**Proposed Action**: Implement Playwright-based HTML fallback while investigating 403 errors.
