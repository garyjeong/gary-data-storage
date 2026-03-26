# Real Estate Collector Planning Document

> **Summary**: Apartment sale/jeonse real transaction data and listing data collection & storage system
>
> **Project**: gary-data-storage
> **Version**: 0.1.0
> **Author**: Gary
> **Date**: 2026-03-25
> **Status**: Draft

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Real estate transaction data and listing data are scattered across multiple public/private platforms, making integrated collection and long-term storage difficult for personal analysis |
| **Solution** | Dockerized Python collector that aggregates data from 6 public APIs + 3 private platforms into PostgreSQL, with a FastAPI admin page for configuration and manual control |
| **Function/UX Effect** | Automated 30-min interval collection with web-based admin for region/schedule management and manual triggers |
| **Core Value** | Personal data sovereignty — own all apartment transaction & listing data in a single, queryable database |

---

## 1. Overview

### 1.1 Purpose

Collect and store apartment sale (매매) and jeonse (전세) real transaction data, listing (매물) data, and supplementary building information from both public and private data sources into a personal PostgreSQL database.

### 1.2 Background

- Real estate data is fragmented across government portals and private platforms
- No single service provides unified access to all data sources
- Personal data ownership enables future custom analysis without platform dependency
- This is the first module of the `gary-data-storage` project (a personal data collection hub)

### 1.3 Related Documents

- Public API Portal: https://www.data.go.kr
- Seoul Open Data: https://data.seoul.go.kr
- Gyeonggi Data Dream: https://data.gg.go.kr
- Korea Real Estate Board R-ONE: https://www.reb.or.kr/r-one/

---

## 2. Scope

### 2.1 In Scope

- [ ] Public API data collection (6 sources)
- [ ] Private platform data collection (3 sources: Naver, Zigbang, Hogangnono)
- [ ] PostgreSQL database schema and storage
- [ ] Automated scheduling (configurable interval, default 30min)
- [ ] Manual collection trigger
- [ ] Admin web page (FastAPI + Jinja2)
  - [ ] Region (시군구) management (add/edit/delete)
  - [ ] Schedule management (interval config, add/edit/delete)
  - [ ] Manual collection execution
- [ ] Docker Compose setup (postgres + app)
- [ ] Configurable target regions (Seoul / South Gyeonggi / East Gyeonggi initial)

### 2.2 Out of Scope

- Data analysis / visualization dashboard (future phase)
- Multi-user access / authentication
- Building types other than apartments
- Transaction types other than sale (매매) and jeonse (전세)
- Cloud deployment (local Docker only)
- Mobile app

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | Collect apartment sale real transaction data from MOLIT API | High | Pending |
| FR-02 | Collect apartment jeonse real transaction data from MOLIT API | High | Pending |
| FR-03 | Collect building registry data from Building Ledger API | Medium | Pending |
| FR-04 | Collect apartment official price data | Medium | Pending |
| FR-05 | Collect price index/statistics from Korea Real Estate Board | Low | Pending |
| FR-06 | Collect Seoul-specific real transaction data | Low | Pending |
| FR-07 | Collect Gyeonggi-specific real transaction data | Low | Pending |
| FR-08 | Collect listing (호가) data from Naver Real Estate | High | Pending |
| FR-09 | Collect listing data from Zigbang | High | Pending |
| FR-10 | Collect market analysis data from Hogangnono | Medium | Pending |
| FR-11 | Automated collection on configurable interval (default 30min) | High | Pending |
| FR-12 | Manual collection trigger via admin page | High | Pending |
| FR-13 | Admin page: region (시군구) CRUD management | High | Pending |
| FR-14 | Admin page: schedule configuration management | High | Pending |
| FR-15 | Deduplicate data on insert (prevent duplicate records) | High | Pending |
| FR-16 | Store collection logs and error records | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|----------|----------|-------------------|
| Reliability | Collection failure should not crash the system; retry with backoff | Log review |
| Storage | Support growing data volume (years of historical data) | DB size monitoring |
| Configurability | Regions, schedules, API keys manageable without code changes | Admin page / config files |
| Portability | Runs on any machine with Docker installed | Docker Compose up |
| Maintainability | Each data source is an independent module; adding/removing sources is easy | Code review |

---

## 4. Success Criteria

### 4.1 Definition of Done

- [ ] All 6 public API sources collecting data successfully
- [ ] All 3 private platform sources collecting data successfully
- [ ] Data stored in PostgreSQL with proper schema and deduplication
- [ ] Automated scheduler running at configured intervals
- [ ] Admin page functional (region/schedule management, manual trigger)
- [ ] Docker Compose starts all services with single command
- [ ] README with setup instructions (API key registration, etc.)

### 4.2 Quality Criteria

- [ ] No data loss on collection failures (graceful error handling per source)
- [ ] Duplicate records prevented
- [ ] Collector runs without manual intervention when Docker is running
- [ ] Admin page loads and responds within 2 seconds

---

## 5. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Private platform API structure changes | High | High | Modular source design; each source independently updatable |
| Private platform IP blocking | Medium | Medium | Request rate limiting; configurable delays between requests |
| Public API daily call limit (1,000/day dev account) | Medium | Medium | Apply for production account (1,000,000/day); batch requests efficiently |
| Data volume growth slows queries | Low | Medium | DB indexing strategy; partition by year/month if needed |
| Public API key expiration/renewal | Low | Low | Config-based key management; admin page shows key status |
| Hogangnono/Zigbang structural changes | Medium | High | Isolate each crawler; graceful degradation (other sources continue) |

---

## 6. Architecture Considerations

### 6.1 Project Level Selection

| Level | Characteristics | Recommended For | Selected |
|-------|-----------------|-----------------|:--------:|
| **Starter** | Simple structure | Static sites, portfolios | ☐ |
| **Dynamic** | Feature-based modules, BaaS integration | Web apps with backend, SaaS MVPs | ☑ |
| **Enterprise** | Strict layer separation, DI, microservices | High-traffic systems | ☐ |

### 6.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|----------|---------|----------|-----------|
| Language | Python / Node.js / Go | Python | Best ecosystem for API calls, crawling, data processing |
| Database | PostgreSQL / SQLite / File-based | PostgreSQL | Handles growing data volume, strong query capabilities |
| Web Framework | FastAPI / Flask / Django | FastAPI | Async support, lightweight, same Python ecosystem as collector |
| Admin UI | Jinja2 SSR / React SPA / Vue SPA | Jinja2 SSR | Simplest, no build step, single container with collector |
| Scheduler | APScheduler / Celery / cron | APScheduler | In-process, Python-native, no extra infrastructure |
| ORM | SQLAlchemy / raw SQL / Tortoise | SQLAlchemy | Mature, migration support (Alembic), well-documented |
| HTTP Client | httpx / requests / aiohttp | httpx | Async support, modern API, sync/async dual mode |
| Container | Docker Compose | Docker Compose | Two services (postgres + app), simple local setup |

### 6.3 System Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Docker Compose                      │
│                                                      │
│  ┌─────────────────────────────────────────────┐    │
│  │              app (Python)                    │    │
│  │                                              │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  │    │
│  │  │ Collector │  │ Scheduler│  │  Admin   │  │    │
│  │  │ Sources   │  │(APSched) │  │(FastAPI) │  │    │
│  │  │          │  │          │  │ :8080    │  │    │
│  │  │ public_api│  │ 30min    │  │          │  │    │
│  │  │ naver    │  │ interval │  │ Regions  │  │    │
│  │  │ zigbang  │  │          │  │ Schedule │  │    │
│  │  │ hogangnono│ │          │  │ Trigger  │  │    │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  │    │
│  │       │             │             │         │    │
│  │       └─────────────┼─────────────┘         │    │
│  │                     │                        │    │
│  └─────────────────────┼────────────────────────┘    │
│                        │                             │
│  ┌─────────────────────┼────────────────────────┐    │
│  │            PostgreSQL :5432                   │    │
│  │                                              │    │
│  │  transactions / listings / buildings / ...    │    │
│  └──────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

### 6.4 Folder Structure

```
gary-data-storage/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── README.md
├── .env.example                    # API keys, DB config
├── config/
│   └── regions.yaml                # Default region configuration
├── src/
│   ├── main.py                     # Entry point (starts scheduler + admin)
│   ├── collector/
│   │   ├── __init__.py
│   │   ├── base.py                 # Base collector interface
│   │   ├── manager.py              # Collection orchestrator
│   │   └── sources/
│   │       ├── __init__.py
│   │       ├── public_api/
│   │       │   ├── __init__.py
│   │       │   ├── molit.py        # MOLIT real transaction (sale/jeonse)
│   │       │   ├── building.py     # Building ledger
│   │       │   ├── price.py        # Official apartment price
│   │       │   ├── reb.py          # Korea Real Estate Board statistics
│   │       │   ├── seoul.py        # Seoul Open Data
│   │       │   └── gyeonggi.py     # Gyeonggi Data Dream
│   │       ├── naver/
│   │       │   ├── __init__.py
│   │       │   └── crawler.py      # Naver Real Estate crawler
│   │       ├── zigbang/
│   │       │   ├── __init__.py
│   │       │   └── crawler.py      # Zigbang crawler
│   │       └── hogangnono/
│   │           ├── __init__.py
│   │           └── crawler.py      # Hogangnono crawler
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py                 # APScheduler job definitions
│   ├── admin/
│   │   ├── __init__.py
│   │   ├── app.py                  # FastAPI app
│   │   ├── routes/
│   │   │   ├── regions.py          # Region CRUD
│   │   │   ├── schedules.py        # Schedule management
│   │   │   └── triggers.py         # Manual trigger
│   │   └── templates/
│   │       ├── base.html
│   │       ├── dashboard.html
│   │       ├── regions.html
│   │       └── schedules.html
│   └── db/
│       ├── __init__.py
│       ├── models.py               # SQLAlchemy models
│       ├── connection.py           # DB connection
│       └── migrations/             # Alembic migrations
│           └── ...
├── docs/
│   └── 01-plan/
│       └── features/
│           └── real-estate-collector.plan.md
└── tests/
    └── ...
```

---

## 7. Convention Prerequisites

### 7.1 Existing Project Conventions

- [ ] `CLAUDE.md` has coding conventions section
- [ ] Python linting configuration (ruff / flake8)
- [ ] Python formatting configuration (black / ruff format)
- [ ] Type checking configuration (mypy / pyright)

### 7.2 Conventions to Define/Verify

| Category | Current State | To Define | Priority |
|----------|---------------|-----------|:--------:|
| **Naming** | Missing | snake_case for Python, PascalCase for classes | High |
| **Folder structure** | Missing | See 6.4 above | High |
| **Import order** | Missing | stdlib → third-party → local (isort) | Medium |
| **Environment variables** | Missing | .env file with prefix convention | High |
| **Error handling** | Missing | Per-source try/catch, log and continue | Medium |
| **Logging** | Missing | structlog or Python logging, JSON format | Medium |

### 7.3 Environment Variables Needed

| Variable | Purpose | Scope | To Be Created |
|----------|---------|-------|:-------------:|
| `DATABASE_URL` | PostgreSQL connection string | Server | ☑ |
| `DATA_GO_KR_API_KEY` | Public Data Portal API key | Server | ☑ |
| `COLLECTION_INTERVAL_MINUTES` | Default collection interval | Server | ☑ |
| `ADMIN_PORT` | Admin page port (default 8080) | Server | ☑ |
| `LOG_LEVEL` | Logging level (default INFO) | Server | ☐ |

### 7.4 Pipeline Integration

Not using 9-phase pipeline. This is a data engineering project, not a web frontend project.

---

## 8. Data Sources Detail

### 8.1 Public API (data.go.kr)

| Source | API ID | Data | Params |
|--------|--------|------|--------|
| MOLIT Apartment Sale | 15126469 | Sale transaction records | 시군구코드(5) + 계약년월(YYYYMM) |
| MOLIT Apartment Jeonse | 15126474 | Jeonse transaction records | 시군구코드(5) + 계약년월(YYYYMM) |
| Building Ledger | 15134735 | Building detail info | 시군구코드 + 법정동코드 |
| Official Apartment Price | 15124003 | Officially assessed price | 시군구코드 |
| Real Estate Board (R-ONE) | 15134761 | Price index, statistics | Region code + period |
| Seoul Open Data | OA-21275 | Seoul transaction integrated | 자치구 + 기간 |
| Gyeonggi Data Dream | - | Gyeonggi transaction | 시군구 + 기간 |

### 8.2 Private Platforms (Crawling)

| Source | Target Data | Method |
|--------|------------|--------|
| Naver Real Estate | Listings (호가), complex info, market price | Internal API reverse engineering |
| Zigbang | Listings, complex info, area-based price | Internal API reverse engineering |
| Hogangnono | Market analysis, school district, price trends | Internal API reverse engineering |

### 8.3 Target Regions (Initial)

| Area | Districts (Examples) |
|------|---------------------|
| Seoul | All 25 districts (강남구, 서초구, 송파구, ...) |
| South Gyeonggi | 수원시, 성남시, 용인시, 화성시, 평택시, 안양시, 안산시, ... |
| East Gyeonggi | 하남시, 광주시, 이천시, 여주시, 양평군, 구리시, 남양주시, ... |

Regions are managed via admin page (add/edit/delete). Each region is stored with its 시군구코드 (5-digit code).

---

## 9. Implementation Phases

### Phase 1: Foundation (DB + Project Structure + Docker)
- PostgreSQL schema design and migration setup
- Docker Compose configuration
- Project skeleton with base collector interface
- Configuration management (regions, env)

### Phase 2: Public API Collectors
- MOLIT apartment sale collector (FR-01)
- MOLIT apartment jeonse collector (FR-02)
- Building ledger collector (FR-03)
- Official price collector (FR-04)
- R-ONE statistics collector (FR-05)
- Seoul/Gyeonggi collectors (FR-06, FR-07)

### Phase 3: Private Platform Crawlers
- Naver Real Estate crawler (FR-08)
- Zigbang crawler (FR-09)
- Hogangnono crawler (FR-10)

### Phase 4: Scheduler + Admin Page
- APScheduler integration (FR-11)
- Admin page: region management (FR-13)
- Admin page: schedule management (FR-14)
- Admin page: manual trigger (FR-12)
- Collection logging (FR-16)

### Phase 5: Stabilization
- Deduplication logic (FR-15)
- Error handling and retry
- Rate limiting for private platforms
- End-to-end testing with Docker Compose

---

## 10. Next Steps

1. [ ] Write design document (`real-estate-collector.design.md`)
2. [ ] Register data.go.kr API keys
3. [ ] Reverse-engineer Naver/Zigbang/Hogangnono internal APIs
4. [ ] Start Phase 1 implementation

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-25 | Initial draft | Gary |
