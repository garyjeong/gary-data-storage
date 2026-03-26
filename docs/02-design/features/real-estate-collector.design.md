# Real Estate Collector Design Document

> **Summary**: Technical design for apartment sale/jeonse real transaction data and listing data collection & storage system
>
> **Project**: gary-data-storage
> **Version**: 0.1.0
> **Author**: Gary
> **Date**: 2026-03-25
> **Status**: Draft
> **Planning Doc**: [real-estate-collector.plan.md](../../01-plan/features/real-estate-collector.plan.md)

---

## 1. Overview

### 1.1 Design Goals

- Reliable automated data collection from 9 sources (6 public + 3 private)
- Modular source architecture — each source is independently addable/removable/updatable
- Single Docker Compose command to run the entire system
- Web-based admin for non-code configuration changes

### 1.2 Design Principles

- **Source Independence**: Each collector source is a self-contained module with no cross-source dependencies
- **Fail-Safe**: One source failure never affects other sources or crashes the system
- **Deduplication by Default**: All data inserts use upsert logic to prevent duplicates
- **Configuration over Code**: Regions, schedules, API keys are all configurable without code changes

---

## 2. Architecture

### 2.1 Component Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                        Docker Compose                            │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │                     app (Python 3.12)                      │  │
│  │                                                            │  │
│  │  ┌─────────────────────────────────────────────────────┐   │  │
│  │  │                  main.py (Entry Point)              │   │  │
│  │  │  - Starts FastAPI (uvicorn)                         │   │  │
│  │  │  - Starts APScheduler                               │   │  │
│  │  │  - Loads configuration                              │   │  │
│  │  └───────┬─────────────────┬──────────────┬────────────┘   │  │
│  │          │                 │              │                │  │
│  │  ┌───────▼──────┐  ┌──────▼───────┐  ┌──▼─────────────┐  │  │
│  │  │  Collector    │  │  Scheduler   │  │  Admin Server  │  │  │
│  │  │  Manager      │  │  (APSched)   │  │  (FastAPI)     │  │  │
│  │  │              │  │              │  │  :8080         │  │  │
│  │  │  ┌─────────┐ │  │  interval    │  │               │  │  │
│  │  │  │public_api│ │  │  jobs        │  │  GET /        │  │  │
│  │  │  │ molit   │ │  │              │  │  GET /regions │  │  │
│  │  │  │ building│ │  │  Triggers    │  │  GET /schedule│  │  │
│  │  │  │ price   │ │  │  Manager     │  │  POST /collect│  │  │
│  │  │  │ reb     │ │  │  .collect()  │  │  GET /logs   │  │  │
│  │  │  │ seoul   │ │  │              │  │               │  │  │
│  │  │  │ gyeonggi│ │  └──────────────┘  └───────────────┘  │  │
│  │  │  ├─────────┤ │                                        │  │
│  │  │  │ naver   │ │                                        │  │
│  │  │  │ zigbang │ │                                        │  │
│  │  │  │hogangnono│ │                                       │  │
│  │  │  └─────────┘ │                                        │  │
│  │  └───────┬──────┘                                        │  │
│  │          │                                                │  │
│  └──────────┼────────────────────────────────────────────────┘  │
│             │                                                    │
│  ┌──────────▼────────────────────────────────────────────────┐  │
│  │                   PostgreSQL 16 :5432                      │  │
│  │                                                            │  │
│  │  regions │ schedules │ transactions │ listings │ buildings │  │
│  │  prices  │ statistics │ collection_logs                    │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Volume: pgdata (persistent)                                     │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 Data Flow

```
[Scheduler: every N minutes]
        │
        ▼
[Collector Manager]
        │
        ├── Load active regions from DB
        ├── Load active source configs from DB
        │
        ▼
[For each source × each region] (sequential per source, parallel-ready)
        │
        ├── Source.collect(region_code, params)
        │       │
        │       ├── HTTP request to API/platform
        │       ├── Parse response
        │       └── Return normalized data list
        │
        ├── Deduplicate (upsert via unique constraints)
        ├── Insert/Update to PostgreSQL
        └── Log result (success/fail/count) to collection_logs
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| Collector Manager | DB (regions, schedules) | Load collection targets |
| Each Source | httpx, DB models | HTTP calls, data storage |
| Scheduler | Collector Manager | Trigger collection jobs |
| Admin Server | DB, Scheduler, Collector Manager | CRUD + manual trigger |
| All components | PostgreSQL | Data persistence |

---

## 3. Data Model

### 3.1 Entity Definitions

```python
# Region — 수집 대상 지역
class Region:
    id: int                    # PK, auto-increment
    name: str                  # 지역명 (e.g., "강남구")
    region_code: str           # 시군구코드 5자리 (e.g., "11680")
    parent_area: str           # 상위 지역 (e.g., "서울", "경기남부", "경기동부")
    is_active: bool            # 수집 활성화 여부
    created_at: datetime
    updated_at: datetime

# Schedule — 수집 스케줄 설정
class Schedule:
    id: int                    # PK
    name: str                  # 스케줄 이름 (e.g., "기본 30분")
    source_type: str | None    # 특정 소스만 (null = 전체)
    interval_minutes: int      # 수집 간격 (분)
    is_active: bool
    last_run_at: datetime | None
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime

# AptTransaction — 아파트 매매/전세 실거래
class AptTransaction:
    id: int                    # PK
    source: str                # 데이터 소스 (e.g., "molit", "seoul", "gyeonggi")
    transaction_type: str      # "sale" | "jeonse"
    region_code: str           # 시군구코드
    dong_name: str             # 법정동명
    apt_name: str              # 아파트명
    exclusive_area: float      # 전용면적 (m²)
    floor: int | None          # 층
    deal_amount: int           # 거래금액 (만원)
    deposit: int | None        # 보증금 (전세, 만원)
    monthly_rent: int | None   # 월세 (만원, 전세=0)
    deal_year: int             # 거래년
    deal_month: int            # 거래월
    deal_day: int | None       # 거래일
    build_year: int | None     # 건축년도
    jibun: str | None          # 지번
    road_name: str | None      # 도로명
    cancel_deal_type: str | None  # 해제여부
    contract_date: date | None # 계약일자
    raw_data: dict             # 원본 응답 JSON 보존
    collected_at: datetime     # 수집 시각
    created_at: datetime
    updated_at: datetime

# AptListing — 아파트 매물 (호가)
class AptListing:
    id: int                    # PK
    source: str                # "naver" | "zigbang" | "hogangnono"
    listing_type: str          # "sale" | "jeonse"
    region_code: str           # 시군구코드
    dong_name: str | None      # 법정동명
    apt_name: str              # 아파트명
    exclusive_area: float | None  # 전용면적
    floor: int | None          # 층
    asking_price: int          # 호가 (만원)
    deposit: int | None        # 보증금 (전세)
    description: str | None    # 매물 설명
    source_listing_id: str | None  # 원천 플랫폼 매물 ID
    listing_url: str | None    # 매물 상세 URL
    is_active: bool            # 현재 유효 매물 여부
    listed_at: date | None     # 등록일
    raw_data: dict             # 원본 응답 JSON 보존
    collected_at: datetime
    created_at: datetime
    updated_at: datetime

# AptComplex — 아파트 단지 정보 (보조)
class AptComplex:
    id: int                    # PK
    source: str                # 데이터 소스
    region_code: str           # 시군구코드
    dong_name: str | None      # 법정동명
    apt_name: str              # 아파트명
    address: str | None        # 주소
    total_units: int | None    # 총 세대수
    total_dong: int | None     # 총 동수
    build_year: int | None     # 건축년도
    floor_area_max: float | None  # 최대 전용면적
    floor_area_min: float | None  # 최소 전용면적
    latitude: float | None     # 위도
    longitude: float | None    # 경도
    source_complex_id: str | None  # 원천 단지 ID (네이버, 직방 등)
    raw_data: dict
    collected_at: datetime
    created_at: datetime
    updated_at: datetime

# Building — 건축물대장 정보
class Building:
    id: int                    # PK
    region_code: str
    dong_code: str | None      # 법정동코드
    apt_name: str | None
    main_purpose: str | None   # 주용도
    structure: str | None      # 구조
    ground_floors: int | None  # 지상층수
    underground_floors: int | None  # 지하층수
    total_area: float | None   # 연면적
    build_date: date | None    # 사용승인일
    raw_data: dict
    collected_at: datetime
    created_at: datetime
    updated_at: datetime

# OfficialPrice — 공시가격
class OfficialPrice:
    id: int                    # PK
    region_code: str
    dong_name: str | None
    apt_name: str
    exclusive_area: float | None
    price_year: int            # 공시년도
    official_price: int        # 공시가격 (만원)
    raw_data: dict
    collected_at: datetime
    created_at: datetime
    updated_at: datetime

# PriceStatistics — 한국부동산원 통계
class PriceStatistics:
    id: int                    # PK
    source: str                # "reb"
    stat_type: str             # "sale_index" | "jeonse_index" | "trade_volume"
    region_code: str | None
    region_name: str
    period: str                # "2026-03" (YYYY-MM)
    value: float               # 지수값 또는 거래량
    base_date: str | None      # 기준일
    raw_data: dict
    collected_at: datetime
    created_at: datetime
    updated_at: datetime

# CollectionLog — 수집 실행 로그
class CollectionLog:
    id: int                    # PK
    source: str                # 소스명
    region_code: str | None    # 대상 지역
    status: str                # "success" | "error" | "partial"
    records_collected: int     # 수집 건수
    records_inserted: int      # 신규 저장 건수
    records_updated: int       # 업데이트 건수
    error_message: str | None  # 에러 시 메시지
    duration_seconds: float    # 수집 소요 시간
    triggered_by: str          # "scheduler" | "manual"
    started_at: datetime
    finished_at: datetime
```

### 3.2 Entity Relationships

```
[Region] 1 ──── N [AptTransaction]     (via region_code)
    │    1 ──── N [AptListing]          (via region_code)
    │    1 ──── N [AptComplex]          (via region_code)
    │    1 ──── N [Building]            (via region_code)
    │    1 ──── N [OfficialPrice]       (via region_code)
    │    1 ──── N [CollectionLog]       (via region_code)
    │
[Schedule] ──── triggers ──── [CollectionLog]

[AptComplex] 1 ──── N [AptTransaction]   (via apt_name + region_code, loose)
             1 ──── N [AptListing]        (via apt_name + region_code, loose)
```

> Note: Region과 데이터 테이블은 `region_code` 컬럼으로 논리적으로 연결되지만, FK constraint는 설정하지 않음 (수집 데이터 유연성 확보).

### 3.3 Database Schema

```sql
-- Region management
CREATE TABLE regions (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    region_code VARCHAR(5) NOT NULL UNIQUE,
    parent_area VARCHAR(20) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_regions_active ON regions(is_active) WHERE is_active = true;

-- Schedule management
CREATE TABLE schedules (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    source_type VARCHAR(30),
    interval_minutes INTEGER NOT NULL DEFAULT 30,
    is_active BOOLEAN DEFAULT true,
    last_run_at TIMESTAMPTZ,
    next_run_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Apartment transactions (sale + jeonse)
CREATE TABLE apt_transactions (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(20) NOT NULL,
    transaction_type VARCHAR(10) NOT NULL,  -- 'sale' | 'jeonse'
    region_code VARCHAR(5) NOT NULL,
    dong_name VARCHAR(50),
    apt_name VARCHAR(100) NOT NULL,
    exclusive_area NUMERIC(10,2),
    floor INTEGER,
    deal_amount INTEGER,                     -- 만원
    deposit INTEGER,                         -- 보증금 (전세)
    monthly_rent INTEGER,                    -- 월세
    deal_year INTEGER NOT NULL,
    deal_month INTEGER NOT NULL,
    deal_day INTEGER,
    build_year INTEGER,
    jibun VARCHAR(50),
    road_name VARCHAR(100),
    cancel_deal_type VARCHAR(10),
    contract_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_apt_tx_dedup ON apt_transactions(
    source, transaction_type, region_code, apt_name, exclusive_area,
    deal_year, deal_month, deal_day, floor
) WHERE deal_day IS NOT NULL;

CREATE INDEX idx_apt_tx_region ON apt_transactions(region_code, transaction_type);
CREATE INDEX idx_apt_tx_date ON apt_transactions(deal_year, deal_month);
CREATE INDEX idx_apt_tx_apt ON apt_transactions(apt_name, region_code);

-- Apartment listings (호가)
CREATE TABLE apt_listings (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(20) NOT NULL,
    listing_type VARCHAR(10) NOT NULL,       -- 'sale' | 'jeonse'
    region_code VARCHAR(5) NOT NULL,
    dong_name VARCHAR(50),
    apt_name VARCHAR(100) NOT NULL,
    exclusive_area NUMERIC(10,2),
    floor INTEGER,
    asking_price INTEGER,                     -- 만원
    deposit INTEGER,
    description TEXT,
    source_listing_id VARCHAR(50),
    listing_url TEXT,
    is_active BOOLEAN DEFAULT true,
    listed_at DATE,
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_apt_listing_dedup ON apt_listings(source, source_listing_id)
    WHERE source_listing_id IS NOT NULL;

CREATE INDEX idx_apt_listing_region ON apt_listings(region_code, listing_type);
CREATE INDEX idx_apt_listing_active ON apt_listings(is_active) WHERE is_active = true;

-- Apartment complex info
CREATE TABLE apt_complexes (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(20) NOT NULL,
    region_code VARCHAR(5) NOT NULL,
    dong_name VARCHAR(50),
    apt_name VARCHAR(100) NOT NULL,
    address TEXT,
    total_units INTEGER,
    total_dong INTEGER,
    build_year INTEGER,
    floor_area_max NUMERIC(10,2),
    floor_area_min NUMERIC(10,2),
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    source_complex_id VARCHAR(50),
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_apt_complex_dedup ON apt_complexes(source, source_complex_id)
    WHERE source_complex_id IS NOT NULL;

CREATE INDEX idx_apt_complex_region ON apt_complexes(region_code);

-- Building ledger
CREATE TABLE buildings (
    id BIGSERIAL PRIMARY KEY,
    region_code VARCHAR(5) NOT NULL,
    dong_code VARCHAR(10),
    apt_name VARCHAR(100),
    main_purpose VARCHAR(50),
    structure VARCHAR(50),
    ground_floors INTEGER,
    underground_floors INTEGER,
    total_area NUMERIC(12,2),
    build_date DATE,
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_building_region ON buildings(region_code);

-- Official apartment price
CREATE TABLE official_prices (
    id BIGSERIAL PRIMARY KEY,
    region_code VARCHAR(5) NOT NULL,
    dong_name VARCHAR(50),
    apt_name VARCHAR(100) NOT NULL,
    exclusive_area NUMERIC(10,2),
    price_year INTEGER NOT NULL,
    official_price INTEGER NOT NULL,         -- 만원
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_official_price_dedup ON official_prices(
    region_code, apt_name, exclusive_area, price_year
);

-- Price statistics (Korea Real Estate Board)
CREATE TABLE price_statistics (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(20) NOT NULL DEFAULT 'reb',
    stat_type VARCHAR(30) NOT NULL,
    region_code VARCHAR(5),
    region_name VARCHAR(50) NOT NULL,
    period VARCHAR(7) NOT NULL,              -- 'YYYY-MM'
    value NUMERIC(12,4),
    base_date VARCHAR(10),
    raw_data JSONB,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_price_stat_dedup ON price_statistics(
    source, stat_type, region_name, period
);

-- Collection logs
CREATE TABLE collection_logs (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(30) NOT NULL,
    region_code VARCHAR(5),
    status VARCHAR(10) NOT NULL,             -- 'success' | 'error' | 'partial'
    records_collected INTEGER DEFAULT 0,
    records_inserted INTEGER DEFAULT 0,
    records_updated INTEGER DEFAULT 0,
    error_message TEXT,
    duration_seconds NUMERIC(8,2),
    triggered_by VARCHAR(10) NOT NULL,       -- 'scheduler' | 'manual'
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ
);

CREATE INDEX idx_collection_log_source ON collection_logs(source, started_at DESC);
CREATE INDEX idx_collection_log_date ON collection_logs(started_at DESC);
```

---

## 4. API Specification (Admin Page)

### 4.1 Endpoint List

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| GET | `/` | Dashboard (collection status overview) | HTML |
| GET | `/regions` | Region management page | HTML |
| POST | `/api/regions` | Add region | JSON |
| PUT | `/api/regions/{id}` | Update region | JSON |
| DELETE | `/api/regions/{id}` | Delete region | JSON |
| GET | `/schedules` | Schedule management page | HTML |
| POST | `/api/schedules` | Add schedule | JSON |
| PUT | `/api/schedules/{id}` | Update schedule | JSON |
| DELETE | `/api/schedules/{id}` | Delete schedule | JSON |
| POST | `/api/collect` | Manual collection trigger | JSON |
| POST | `/api/collect/{source}` | Manual trigger for specific source | JSON |
| GET | `/api/logs` | Recent collection logs | JSON |
| GET | `/api/status` | Current scheduler status | JSON |

### 4.2 Detailed Specification

#### `POST /api/regions`

**Request:**
```json
{
    "name": "강남구",
    "region_code": "11680",
    "parent_area": "서울",
    "is_active": true
}
```

**Response (201):**
```json
{
    "id": 1,
    "name": "강남구",
    "region_code": "11680",
    "parent_area": "서울",
    "is_active": true,
    "created_at": "2026-03-25T10:00:00Z"
}
```

#### `POST /api/collect`

**Request:**
```json
{
    "sources": ["all"],
    "region_codes": ["all"]
}
```

**Response (202 Accepted):**
```json
{
    "status": "started",
    "job_id": "manual-20260325-100000",
    "sources": ["molit", "building", "price", "reb", "seoul", "gyeonggi", "naver", "zigbang", "hogangnono"],
    "region_count": 45
}
```

#### `PUT /api/schedules/{id}`

**Request:**
```json
{
    "interval_minutes": 60,
    "is_active": true
}
```

**Response (200):**
```json
{
    "id": 1,
    "name": "기본 수집",
    "interval_minutes": 60,
    "is_active": true,
    "next_run_at": "2026-03-25T11:00:00Z"
}
```

---

## 5. UI/UX Design (Admin Page)

### 5.1 Dashboard Layout

```
┌──────────────────────────────────────────────────────┐
│  Real Estate Collector Admin                         │
├──────────┬──────────┬──────────┬─────────────────────┤
│ Dashboard│ Regions  │ Schedule │ Logs                 │
├──────────┴──────────┴──────────┴─────────────────────┤
│                                                      │
│  ┌─────────────┐ ┌─────────────┐ ┌────────────────┐  │
│  │ Total       │ │ Last Run    │ │ Next Run       │  │
│  │ Regions: 45 │ │ 10:30 AM    │ │ 11:00 AM       │  │
│  │ Active: 42  │ │ OK (38/45)  │ │ in 28 min      │  │
│  └─────────────┘ └─────────────┘ └────────────────┘  │
│                                                      │
│  [Collect Now] button                                │
│                                                      │
│  Recent Collection Logs (last 20)                    │
│  ┌────────┬────────┬────────┬───────┬──────┐        │
│  │ Time   │ Source │ Region │ Count │ Status│        │
│  ├────────┼────────┼────────┼───────┼──────┤        │
│  │ 10:30  │ molit  │ 11680  │  125  │  OK  │        │
│  │ 10:30  │ naver  │ 11680  │   43  │  OK  │        │
│  │ 10:29  │ zigbang│ 11680  │   31  │ ERR  │        │
│  └────────┴────────┴────────┴───────┴──────┘        │
└──────────────────────────────────────────────────────┘
```

### 5.2 Regions Page

```
┌──────────────────────────────────────────────────────┐
│  Region Management                    [+ Add Region] │
├──────────────────────────────────────────────────────┤
│  Filter: [서울 ▼] [경기남부 ▼] [경기동부 ▼] [All]    │
├──────────────────────────────────────────────────────┤
│  ┌──────┬────────┬────────┬────────┬───────┬──────┐  │
│  │ Code │ Name   │ Area   │ Active │ Last  │ Ctrl │  │
│  ├──────┼────────┼────────┼────────┼───────┼──────┤  │
│  │11680 │ 강남구 │ 서울   │   ON   │ 10:30 │ E  D │  │
│  │11650 │ 서초구 │ 서울   │   ON   │ 10:30 │ E  D │  │
│  │41135 │ 성남시 │경기남부│   ON   │ 10:30 │ E  D │  │
│  │41390 │ 하남시 │경기동부│  OFF   │  N/A  │ E  D │  │
│  └──────┴────────┴────────┴────────┴───────┴──────┘  │
│                                                      │
│  Add Region Dialog:                                  │
│  ┌──────────────────────────────────────┐            │
│  │ Name:        [        ]             │            │
│  │ Region Code: [     ] (5 digits)     │            │
│  │ Parent Area: [서울 ▼]               │            │
│  │              [Save] [Cancel]        │            │
│  └──────────────────────────────────────┘            │
└──────────────────────────────────────────────────────┘
```

### 5.3 Schedule Page

```
┌──────────────────────────────────────────────────────┐
│  Schedule Management                [+ Add Schedule] │
├──────────────────────────────────────────────────────┤
│  ┌──────┬──────────┬──────────┬────────┬───────────┐ │
│  │ Name │ Source   │ Interval │ Active │ Next Run  │ │
│  ├──────┼──────────┼──────────┼────────┼───────────┤ │
│  │기본  │ All      │ 30 min   │  ON    │ 11:00 AM  │ │
│  │공공  │public_api│ 60 min   │  OFF   │   N/A     │ │
│  └──────┴──────────┴──────────┴────────┴───────────┘ │
│                                                      │
│  Manual Trigger:                                     │
│  ┌──────────────────────────────────────┐            │
│  │ Source: [All Sources ▼]             │            │
│  │ Region: [All Regions ▼]             │            │
│  │         [Run Collection Now]        │            │
│  └──────────────────────────────────────┘            │
└──────────────────────────────────────────────────────┘
```

---

## 6. Collector Module Design

### 6.1 Base Collector Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class CollectionResult:
    source: str
    region_code: str | None
    records_collected: int
    records_inserted: int
    records_updated: int
    status: str                # "success" | "error" | "partial"
    error_message: str | None
    duration_seconds: float

class BaseCollector(ABC):
    """All collectors must implement this interface."""

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Unique source identifier (e.g., 'molit_sale')"""

    @abstractmethod
    async def collect(self, region_code: str, **params) -> CollectionResult:
        """Collect data for a single region. Must handle own errors."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the source API is reachable."""
```

### 6.2 Collector Manager

```python
class CollectorManager:
    """Orchestrates collection across all sources and regions."""

    def __init__(self, db_session, collectors: list[BaseCollector]):
        self.db = db_session
        self.collectors = {c.source_name: c for c in collectors}

    async def collect_all(self, triggered_by: str = "scheduler") -> list[CollectionResult]:
        """Run all active collectors for all active regions."""
        regions = await self.get_active_regions()
        results = []

        for collector in self.collectors.values():
            for region in regions:
                try:
                    result = await collector.collect(region.region_code)
                    await self.log_result(result, triggered_by)
                    results.append(result)
                    await asyncio.sleep(self.get_delay(collector))  # rate limit
                except Exception as e:
                    # Log error but continue with next region/source
                    error_result = CollectionResult(
                        source=collector.source_name,
                        region_code=region.region_code,
                        records_collected=0, records_inserted=0, records_updated=0,
                        status="error", error_message=str(e), duration_seconds=0
                    )
                    await self.log_result(error_result, triggered_by)
                    results.append(error_result)

        return results

    async def collect_source(self, source_name: str, region_codes: list[str] | None = None, triggered_by: str = "manual"):
        """Run a specific collector for specified or all active regions."""

    def get_delay(self, collector: BaseCollector) -> float:
        """Return delay between requests (higher for private platforms)."""
        if collector.source_name in ("naver", "zigbang", "hogangnono"):
            return 2.0  # 2 seconds between requests for crawlers
        return 0.5      # 0.5 seconds for public APIs
```

### 6.3 Source Implementation Pattern

Each source follows this pattern:

```python
class MolitSaleCollector(BaseCollector):
    source_name = "molit_sale"

    def __init__(self, api_key: str, db_session):
        self.api_key = api_key
        self.client = httpx.AsyncClient(timeout=30.0)
        self.db = db_session

    async def collect(self, region_code: str, **params) -> CollectionResult:
        start = time.time()
        try:
            # 1. Fetch from API
            raw_data = await self._fetch(region_code, params.get("deal_ym"))

            # 2. Parse & normalize
            records = self._parse(raw_data)

            # 3. Upsert to DB
            inserted, updated = await self._upsert(records)

            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                records_collected=len(records),
                records_inserted=inserted,
                records_updated=updated,
                status="success",
                error_message=None,
                duration_seconds=time.time() - start
            )
        except Exception as e:
            return CollectionResult(
                source=self.source_name,
                region_code=region_code,
                records_collected=0, records_inserted=0, records_updated=0,
                status="error",
                error_message=str(e),
                duration_seconds=time.time() - start
            )

    async def _fetch(self, region_code: str, deal_ym: str | None = None) -> dict:
        """Call MOLIT API and return raw response."""
        # Default to current month if not specified
        if not deal_ym:
            deal_ym = datetime.now().strftime("%Y%m")

        url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
        params = {
            "serviceKey": self.api_key,
            "LAWD_CD": region_code,
            "DEAL_YMD": deal_ym,
            "pageNo": 1,
            "numOfRows": 1000,
            "type": "json"
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def _parse(self, raw_data: dict) -> list[dict]:
        """Normalize API response to internal format."""
        # Extract items from MOLIT response structure
        # Return list of normalized dicts matching AptTransaction columns

    async def _upsert(self, records: list[dict]) -> tuple[int, int]:
        """Insert or update records using ON CONFLICT."""
        # Use PostgreSQL INSERT ... ON CONFLICT DO UPDATE
        # Return (inserted_count, updated_count)
```

---

## 7. Error Handling

### 7.1 Error Strategy by Layer

| Layer | Strategy | Action |
|-------|----------|--------|
| HTTP Request | Retry with exponential backoff (max 3) | Log warning, return error result |
| API Response Parse | Try/except per record | Skip bad record, log warning, continue |
| DB Insert | Upsert (ON CONFLICT) | No error on duplicate |
| Collector | Catch all exceptions | Log error, return error CollectionResult |
| Collector Manager | Never crash on source failure | Log and continue to next source/region |
| Scheduler | Never crash on collection failure | Log and wait for next scheduled run |

### 7.2 Retry Configuration

```python
RETRY_CONFIG = {
    "public_api": {
        "max_retries": 3,
        "backoff_base": 2,       # seconds
        "backoff_max": 30,       # seconds
    },
    "private_platform": {
        "max_retries": 2,
        "backoff_base": 5,
        "backoff_max": 60,
    }
}
```

---

## 8. Security Considerations

- [x] No external user access (local Docker only, no auth needed)
- [ ] API keys stored in `.env` file, never in code or Git
- [ ] `.env` added to `.gitignore`
- [ ] Rate limiting on private platform crawlers to avoid IP blocking
- [ ] JSONB `raw_data` column preserves original data for audit/debug
- [ ] Admin page binds to `0.0.0.0:8080` (Docker internal), exposed only via Docker port mapping

---

## 9. Clean Architecture

### 9.1 Layer Structure

| Layer | Responsibility | Location |
|-------|---------------|----------|
| **Presentation** | Admin web pages, API routes | `src/admin/` |
| **Application** | Collection orchestration, scheduling | `src/collector/manager.py`, `src/scheduler/` |
| **Domain** | Data models, collection interfaces | `src/db/models.py`, `src/collector/base.py` |
| **Infrastructure** | HTTP clients, DB connections, source implementations | `src/collector/sources/`, `src/db/connection.py` |

### 9.2 Dependency Rules

```
Admin (Presentation) ──→ Manager (Application) ──→ Models (Domain)
                                │                        ↑
                                └──→ Sources (Infra) ────┘
```

- Sources depend on Models (domain) only
- Manager depends on Models + Sources
- Admin depends on Manager + Models
- Models depend on nothing

---

## 10. Coding Convention

### 10.1 Naming Conventions

| Target | Rule | Example |
|--------|------|---------|
| Modules | snake_case | `molit.py`, `collection_manager.py` |
| Classes | PascalCase | `MolitSaleCollector`, `CollectionResult` |
| Functions | snake_case | `collect_all()`, `get_active_regions()` |
| Constants | UPPER_SNAKE_CASE | `RETRY_CONFIG`, `DEFAULT_INTERVAL` |
| DB Tables | snake_case, plural | `apt_transactions`, `collection_logs` |
| DB Columns | snake_case | `region_code`, `deal_amount` |

### 10.2 Import Order

```python
# 1. Standard library
import asyncio
import time
from datetime import datetime

# 2. Third-party
import httpx
from fastapi import FastAPI
from sqlalchemy import Column, Integer, String

# 3. Local
from src.collector.base import BaseCollector
from src.db.models import AptTransaction
```

### 10.3 Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql://user:pass@postgres:5432/realestate` |
| `DATA_GO_KR_API_KEY` | Public Data Portal key | (required) |
| `COLLECTION_INTERVAL_MINUTES` | Default interval | `30` |
| `ADMIN_PORT` | Admin page port | `8080` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `PRIVATE_CRAWLER_DELAY` | Delay between crawler requests (sec) | `2` |

---

## 11. Implementation Guide

### 11.1 File Structure

```
src/
├── main.py                         # Entry point
├── config.py                       # Settings (pydantic-settings)
├── collector/
│   ├── __init__.py
│   ├── base.py                     # BaseCollector ABC
│   ├── manager.py                  # CollectorManager
│   └── sources/
│       ├── __init__.py             # Source registry
│       ├── public_api/
│       │   ├── __init__.py
│       │   ├── molit.py            # Sale + Jeonse collectors
│       │   ├── building.py
│       │   ├── price.py
│       │   ├── reb.py
│       │   ├── seoul.py
│       │   └── gyeonggi.py
│       ├── naver/
│       │   ├── __init__.py
│       │   └── crawler.py
│       ├── zigbang/
│       │   ├── __init__.py
│       │   └── crawler.py
│       └── hogangnono/
│           ├── __init__.py
│           └── crawler.py
├── scheduler/
│   ├── __init__.py
│   └── jobs.py                     # APScheduler setup
├── admin/
│   ├── __init__.py
│   ├── app.py                      # FastAPI app factory
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── dashboard.py
│   │   ├── regions.py
│   │   ├── schedules.py
│   │   └── triggers.py
│   └── templates/
│       ├── base.html
│       ├── dashboard.html
│       ├── regions.html
│       └── schedules.html
└── db/
    ├── __init__.py
    ├── models.py                   # SQLAlchemy models
    ├── connection.py               # async engine + session
    ├── crud.py                     # Common CRUD operations
    └── migrations/
        ├── env.py
        └── versions/
```

### 11.2 Implementation Order

**Phase 1: Foundation**
1. [ ] `docker-compose.yml` + `Dockerfile` + `requirements.txt`
2. [ ] `src/config.py` — pydantic-settings
3. [ ] `src/db/connection.py` — async SQLAlchemy engine
4. [ ] `src/db/models.py` — all SQLAlchemy models
5. [ ] Alembic setup + initial migration
6. [ ] `src/collector/base.py` — BaseCollector interface
7. [ ] `src/collector/manager.py` — CollectorManager skeleton
8. [ ] `config/regions.yaml` — default regions seed data

**Phase 2: Public API Collectors**
9. [ ] `src/collector/sources/public_api/molit.py` — sale + jeonse
10. [ ] `src/collector/sources/public_api/building.py`
11. [ ] `src/collector/sources/public_api/price.py`
12. [ ] `src/collector/sources/public_api/reb.py`
13. [ ] `src/collector/sources/public_api/seoul.py`
14. [ ] `src/collector/sources/public_api/gyeonggi.py`

**Phase 3: Private Platform Crawlers**
15. [ ] `src/collector/sources/naver/crawler.py`
16. [ ] `src/collector/sources/zigbang/crawler.py`
17. [ ] `src/collector/sources/hogangnono/crawler.py`

**Phase 4: Scheduler + Admin**
18. [ ] `src/scheduler/jobs.py` — APScheduler integration
19. [ ] `src/admin/app.py` — FastAPI app
20. [ ] `src/admin/routes/dashboard.py` + template
21. [ ] `src/admin/routes/regions.py` + template
22. [ ] `src/admin/routes/schedules.py` + template
23. [ ] `src/admin/routes/triggers.py`
24. [ ] `src/main.py` — entry point wiring all components

**Phase 5: Stabilization**
25. [ ] Deduplication verification across all sources
26. [ ] Error handling + retry logic hardening
27. [ ] Rate limiting tuning for private platforms
28. [ ] `.env.example` + `README.md`
29. [ ] Docker Compose end-to-end test

---

## 12. Docker Configuration

### 12.1 docker-compose.yml

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: realestate
      POSTGRES_USER: collector
      POSTGRES_PASSWORD: ${DB_PASSWORD:-collector_pass}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U collector -d realestate"]
      interval: 5s
      timeout: 5s
      retries: 5

  app:
    build: .
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://collector:${DB_PASSWORD:-collector_pass}@postgres:5432/realestate
      DATA_GO_KR_API_KEY: ${DATA_GO_KR_API_KEY}
      COLLECTION_INTERVAL_MINUTES: ${COLLECTION_INTERVAL_MINUTES:-30}
      ADMIN_PORT: ${ADMIN_PORT:-8080}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    ports:
      - "${ADMIN_PORT:-8080}:8080"
    restart: unless-stopped

volumes:
  pgdata:
```

### 12.2 Dockerfile

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

CMD ["python", "-m", "src.main"]
```

### 12.3 Key Dependencies (requirements.txt)

```
fastapi>=0.115.0
uvicorn>=0.32.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.30.0
alembic>=1.14.0
httpx>=0.28.0
apscheduler>=3.10.0
jinja2>=3.1.0
pydantic-settings>=2.6.0
pyyaml>=6.0.0
python-multipart>=0.0.12
```

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-25 | Initial draft | Gary |
