# 시드 데이터 포맷 정의 (`data/competitors`, `data/pricing`, `data/reviews`)

> 새 시드 파일을 추가하기 전에 이 문서를 먼저 본다. 최초 90개 파일(3폴더 × 30개 회사)을 직접
> 비교해서 실제로 통일된 부분과 깨져 있는 부분을 그대로 적었다. 이후 B2B SaaS 20개, Fintech B2B
> 20개, HR Tech 20개(전부 2026-06-20)가 추가돼 현재 3폴더 × 90개 회사 = 270개 파일이다.

## 1. 전체 구조

- 3개 폴더(`competitors/`, `pricing/`, `reviews/`)가 **같은 90개 회사**에 대해 1:1로 존재한다.
  파일명이 폴더 간에 완전히 일치한다(예: `algolia_recommend.json`이 세 폴더 모두에 있음).
- 새 회사를 추가할 때는 **3개 폴더에 모두** 같은 파일명으로 추가해야 한다. 한 폴더만 추가하면
  `data/load_seed.py`의 `verify()`가 "시드 합계"에서 불일치를 잡아낸다.
- 적재 경로: `data/load_seed.py`가 이 JSON들을 읽어 `documents` 테이블(Tier 0)에 넣는다.
  - `source_type`: competitors→`seed_competitor`, pricing→`seed_pricing`, reviews→`seed_review`
  - `documents.reliability_score`/`freshness_score`는 JSON 안에 없고 로더가 **하드코딩**해서 채운다
    (`0.6`/`0.7`, 세 폴더 공통 — `load_seed.py:151-152`).
  - `documents.meta`는 jsonb라 DB가 내부 키를 검증하지 않는다. 즉 아래 "카테고리" 필드들은
    **DB 제약이 아니라 우리 애플리케이션(검색 필터)이 일관성을 책임져야** 한다.
  - `documents.ext_id`에 UNIQUE 제약이 있다(`db/init.sql:77`) — `ext_id`는 9개 테이블 통틀어
    전역적으로 겹치면 안 된다.

## 2. 공통 최상위 스키마 (3개 폴더 동일)

```json
{
  "source_type": "seed_competitor | seed_pricing | seed_review",
  "ext_id": "seed_{타입}_{회사슬러그}",
  "title": "한 줄 제목 — 빈 값이면 load_seed.py 사전검증에서 탈락",
  "clean_text": "RAG 검색 대상이 되는 본문 — 사람이 읽는 요약 문단",
  "meta": { "...": "폴더별로 다름, 3절 참조" }
}
```

- `ext_id` 규칙: `seed_{source_type 접미사}_{파일명(확장자 제외)}`. 예) `pricing/algolia_recommend.json`
  → `seed_pricing_algolia_recommend`. **파일명 = ext_id의 회사 슬러그**이므로 파일명을 바꾸면
  `ext_id`도 같이 바꿔야 한다.
- `load_seed.py`가 사전 검증하는 건 딱 2가지뿐이다: ① `meta.source_type`이 폴더 기대값과 일치,
  ② `title`이 비어있지 않음. 그 외 필드는 전부 느슨하다(강제 없음).
- `clean_text`는 2~5문장의 영어 평서문으로 통일되어 있다(전 파일 공통 패턴, 강제 규칙은 아니지만
  검색 품질을 위해 따르는 게 좋다).

## 3. 폴더별 `meta` 스키마

### 3-1. `competitors/` — 회사 개요·포지셔닝

핵심 키 7개가 **30개 파일 전부**에 있다:

| 키 | 타입 | 설명 |
|---|---|---|
| `product` | string | 제품명 |
| `category` | string | **컨트롤드 보캐뷸러리** — 아래 표 참조 |
| `platform` | list[string] | 자유 텍스트(플랫폼/유통채널 혼재, 통제 안 됨) |
| `target` | string | 자유 텍스트 타깃 고객 설명(고유값, 카테고리 아님) |
| `pricing_model` | string | 자유 텍스트 한 줄 설명(거의 전부 고유값, 카테고리 아님) |
| `source` | string | 출처 URL/설명 |
| `collected_date` | string | `YYYY-MM-DD` |

선택 키(일부 파일에만, **이름이 통일 안 됨** — 새로 쓸 땐 `parent` 권장):
`parent`/`parent_company`(같은 의미, 이름 불일치), `current_name`+`former_name` 또는
`former_name`만, `founded`.

**`category` 분포(현재 30건)** — 검색 필터로 바로 쓸 수 있는 실제 컨트롤드 보캐뷸러리:

| category | 건수 | 의미 |
|---|---|---|
| `recommendation_engine` | 11 | 상품 추천 알고리즘/모델 자체가 핵심 제품 |
| `search_discovery` | 5 | 검색·탐색(사이트 검색, 자동완성 등)이 핵심, 추천은 부가 |
| `personalization_platform` | 5 | 채널 전반 개인화(이메일·웹·앱 등) 플랫폼 |
| `marketing_automation` | 4→7 | 마케팅 캠페인·메시징 자동화 중심(2026-06-20 B2B SaaS 3건 추가: HubSpot/Marketo/Mailchimp) |
| `ecommerce_platform` | 2 | 이커머스 플랫폼 본체(추천은 내장 기능 중 하나) |
| `visual_ai` | 1 | 이미지/비주얼 기반 추천·검색 |
| `ugc_reviews` | 1 | 사용자 생성 콘텐츠(리뷰) 기반 |
| `b2b_wholesale` | 1 | B2B 도매 특화 |

**확장(B2B SaaS 20건 추가 시 신규 채택, 2026-06-20)** — 추천 시스템 도메인 밖의 일반 B2B SaaS를
다루기 위해 추가:

| category | 건수 | 의미 |
|---|---|---|
| `hcm_erp` | 1 | 인사관리(HCM)·ERP 결합 클라우드 SaaS |
| `itsm_workflow_automation` | 1 | IT 서비스관리·이슈해결 워크플로우 자동화, 거버넌스/리스크 |
| `work_management` | 9 | 프로젝트·업무 관리(범용 PM, PSA 포함) |
| `collaboration_productivity` | 2 | 팀 커뮤니케이션·문서·협업 도구 |
| `developer_tools` | 1 | 엔지니어링 내부툴/개발자 도구 |
| `observability` | 1 | 옵저버빌리티·모니터링 |
| `customer_support` | 1 | 고객지원 SaaS |
| `crm` | 1 | 고객관계관리 플랫폼 |

**확장(Fintech B2B 20건 추가 시 신규 채택, 2026-06-20)** — 결제·핀테크 도메인을 다루기 위해 추가:

| category | 건수 | 의미 |
|---|---|---|
| `payment_processing` | 6 | 결제 처리/PG(Stripe, Adyen, PayPal, Square, Checkout.com, Razorpay) |
| `card_network` | 3 | 카드 네트워크(Visa, Mastercard, American Express) |
| `cross_border_payments` | 3 | 국경간 결제(Payoneer, Wise, Airwallex) |
| `b2b_invoicing_credit` | 2 | B2B 외상거래/청구 자동화(Bill.com, TreviPay) |
| `banking_infrastructure` | 2 | 은행 코어시스템/금융 인프라(Finastra, FIS) |
| `open_banking_api` | 1 | 오픈뱅킹 은행연결 API(Plaid) |
| `corporate_card_spend_mgmt` | 1 | 기업카드·지출관리(Brex) |
| `accounting_software` | 1 | 회계 소프트웨어(Intuit QuickBooks) |
| `supply_chain_finance` | 1 | ERP 연동 공급망 금융(Taulia) |

**교체된 회사 3건(원래 후보 → 최종 채택, 사유)**: 검색 결과 자료가 너무 빈약해 같은 주제의
다른 회사로 바꿨다(기존 데이터는 만들지 않음).
- `Apruve` → `TreviPay`: Apruve는 2022년 TreviPay에 인수되어 독립 운영하지 않음.
- `Traxpay` → `Taulia`: Traxpay는 직원 26명 소기업이라 리뷰 자체가 없고, 검색 결과가 동명의
  무관한 회사(TRAXPayroll Solutions)와 계속 섞임. 같은 "ERP 연동 공급망 금융" 자리를 SAP 소유의
  Taulia로 대체.
- `Fexco` → `Airwallex`: Fexco 리뷰는 본 상품(국경간 결제)과 무관한 부서(부동산 서비스, 직원
  리뷰)와 섞여 실질 자료가 거의 없음.
- `Apple Pay for Business` → `Checkout.com`: Apple Pay는 다른 PG사에 내장되는 결제수단이라
  독립된 가격정책·리뷰가 없음.
- `Tenpay` → `Razorpay`: Tenpay는 WeChat Pay에 흡수되어 최신 리뷰가 거의 없음. 같은 "아시아
  B2B 결제 대표주자" 자리를 인도의 Razorpay로 대체.

**확장(HR Tech 20건 추가 시 신규 채택, 2026-06-20)** — HR테크 도메인을 다루기 위해 추가:

| category | 건수 | 의미 |
|---|---|---|
| `hcm_erp` | +8 | (기존 카테고리 재사용) SAP SuccessFactors, ADP Workforce Now, UKG Pro, Paycor, Paychex Flex, Paylocity, isolved, ServiceNow HR Service Delivery |
| `hr_it_payroll_platform` | 1 | HR·IT·페이롤 통합 플랫폼(Rippling) |
| `global_eor_payroll` | 2 | 글로벌 EOR·해외 고용 대행(Deel, Remote) |
| `core_hr_hris` | 6 | 중소~중견 대상 코어 HR(Gusto, BambooHR, HiBob, Zelt, Personio, Namely) |
| `ai_recruiting` | 1 | AI 채용 소싱(Juicebox) |
| `ai_native_hr_tech` | 1 | AI 네이티브 신흥 페이롤(Warp) |
| `performance_management` | 1 | 성과관리·인재관리(Lattice) |

**교체된 회사 2건**:
- `Workday`(목록 1번) → `SAP SuccessFactors`: Workday는 B2B SaaS 배치에서 이미 만들었음(중복).
  같은 "대규모 엔터프라이즈 최상위 HRIS" 자리를 SAP SuccessFactors로 대체.
- `Bolto` → `Remote`: Bolto는 G2·Trustpilot 등 독립 리뷰가 전혀 없음(2025년 시리즈A 갓 받은
  신생사). 같은 "글로벌 EOR·150개국 이상 확장" 자리를 자료가 풍부한 Remote로 대체.
- 나머지 18개(isolved, Zelt, Juicebox, Warp 포함)는 처음 의심했던 것과 달리 자료가 충분해
  교체 없이 그대로 진행함.

새 회사를 추가할 때는 **이 32개(8+8+9+7) 카테고리 중 하나를 그대로 재사용**한다(새 카테고리가 꼭
필요하면 이 표에 줄을 추가하고 이유를 적는다 — 카테고리가 늘어날수록 검색 필터의 의미가 흐려진다).

### 3-2. `pricing/` — 가격 구조

공통 키 6개:

| 키 | 타입 | 설명 |
|---|---|---|
| `product` | string | |
| `pricing_model` | string | 자유 텍스트 |
| `min_price_usd` | number \| null | 가격 구간 하한(비교/정렬용) |
| `max_price_usd` | number \| null | 가격 구간 상한(비공개면 `null`) |
| `source` | string | |
| `collected_date` | string | |

나머지는 **회사마다 다른 가격 구간 필드**가 자유롭게 추가된다(예:
`entry_monthly_usd`, `enterprise_monthly_usd`, `recommend_per_1k_usd`,
`average_annual_contract_usd` 등) — 가격 구조가 회사마다 달라서 의도적으로 느슨하게 둔 부분이다.
새 회사를 추가할 때 이 필드명을 억지로 통일하려 하지 않아도 된다. **단, `min_price_usd`/
`max_price_usd`만은 항상 채운다** — 검색/정렬이 의존하는 유일한 공통 숫자 필드다.

**⚠️ 발견된 불일치 — `free_tier` 타입이 섞여 있음**: 어떤 파일은 boolean(`true`/`false`),
어떤 파일은 문자열(`"2 months"`)이다. 새로 쓸 땐 **boolean으로 통일**하고, 무료 기간이 있으면
별도 키(예: `free_trial_period`)로 분리하는 걸 권장한다.

### 3-3. `reviews/` — 부정적 피드백·이슈

키가 정확히 5개로 **세 폴더 중 가장 균일**하다:

| 키 | 타입 | 설명 |
|---|---|---|
| `product` | string | |
| `sentiment` | string | 현재 30건 전부 `"negative"`(아래 참고) |
| `issue_type` | list[string] | **컨트롤드 보캐뷸러리** — 아래 표 참조, 1건당 1~3개 |
| `source` | string | |
| `collected_date` | string | |

**`issue_type` 분포(현재 30건, 중복 태깅 가능)**:

| issue_type | 건수 | 의미 |
|---|---|---|
| `Pricing_Complexity` | 15 | 가격 구조가 복잡/불투명 |
| `Implementation_Complexity` | 13 | 도입·연동이 어려움 |
| `Price` | 12 | 단순히 비쌈 |
| `Feature_Limitation` | 9 | 기능이 제한적 |
| `Product_Stability` | 6 | 안정성/버그 |
| `Vendor_Lock_in` | 4 | 벤더 종속 |
| `Feature_Missing` | 4 | 특정 기능 부재 |
| `Target_Mismatch` | 3 | 타깃 고객과 안 맞음 |
| `Performance` | 3 | 성능 문제 |
| `Contract_Terms` | 2 | 계약 조건(약정·환불 등) 문제 |

새 회사를 추가할 때 이 10개 중 가장 가까운 값을 재사용한다.

**`sentiment`가 변동이 없음 — 의도된 것으로 확인됨**: 30건 전부 `"negative"`다. 첫 커밋(`3d66df8`)부터
이미 그랬고, 다음날 `issue_type` 표기만 정규화한 수정 커밋(`015c780`)에서도 `sentiment`는 그대로
두었다. `clean_text` 본문도 전부 비용·lock-in·복잡성 등 단점만 다룬다 — `reviews` 폴더는 처음부터
"각 경쟁사의 알려진 약점 풀"(Critic의 반박 근거용)로 기획된 것이다.

## 4. 새 시드 파일 작성 체크리스트

1. 같은 회사 이름(슬러그)으로 `competitors/`·`pricing/`·`reviews/` 세 파일을 모두 만든다.
2. `ext_id = seed_{타입}_{슬러그}`, `title` 비어있지 않게.
3. `competitors`의 `category`는 3-1 표의 8개 값 중에서 고른다.
4. `pricing`의 `min_price_usd`/`max_price_usd`는 반드시 채우고, `free_tier`는 boolean으로.
5. `reviews`의 `issue_type`은 3-3 표의 10개 값 중에서 1~3개 고르고, `sentiment`는 기존 패턴대로
   `"negative"`(다른 값을 쓰려면 먼저 팀에 의도를 공유).
6. 다 채운 뒤 `python data/load_seed.py`로 적재하고 `verify()` 출력에서 "시드 합계"가 늘어난
   파일 수와 맞는지 확인한다.
