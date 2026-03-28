# Real Estate Collector — Gap Analysis Report

> **Feature**: gary-data-storage (real-estate-collector)
> **Date**: 2026-03-27
> **Design Document**: [real-estate-collector.design.md](../02-design/features/real-estate-collector.design.md)
> **Match Rate**: 93%

---

## Executive Summary

| Perspective | Content |
|-------------|---------|
| **Problem** | Design-to-implementation gap analysis needed after initial Do phase |
| **Solution** | Compared 14 design sections against actual codebase; identified 6 gaps out of 59 checkpoints |
| **Match Rate** | **93%** — 55/59 items matched, 6 gaps identified (2 Medium, 4 Low) |
| **Core Value** | Near-complete implementation with minor gaps; Docker runtime verified working |

---

## 1. Docker Runtime Verification

### 1.1 Build & Startup

| Check | Result | Notes |
|-------|--------|-------|
| `docker compose build` | PASS | Image builds cleanly (python:3.12-slim) |
| `docker compose up` | PASS | Both postgres + app containers start |
| PostgreSQL healthcheck | PASS | `pg_isready` passes within 10s |
| App startup | PASS | Uvicorn starts, all 10 collectors registered |
| DB migration (tables) | PASS | `Base.metadata.create_all` succeeds |
| Region seed (56 regions) | PASS | Seoul 25 + Gyeonggi regions loaded |
| Default schedule seed | PASS | "기본 수집" 30min schedule created |
| APScheduler start | PASS | Scheduler running, next_run in 30min |

### 1.2 Admin Page Endpoints

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /` (Dashboard) | 200 OK | HTML rendered correctly |
| `GET /regions` | 200 OK | Region management page |
| `GET /schedules` | 200 OK | Schedule management page |
| `GET /api/status` | 200 OK | Returns scheduler status + 10 source names |
| `GET /api/logs` | 200 OK | Returns collection log JSON array |
| `POST /api/regions/` (Create) | 201 OK | Region CRUD working |
| `DELETE /api/regions/{id}` | 200 OK | Region deletion working |
| `GET /api/regions/` (List) | 200 OK | Returns 56 regions |
| `GET /api/schedules/` (List) | 200 OK | Returns 1 default schedule |

### 1.3 Runtime Issues Found

| Issue | Severity | Detail |
|-------|----------|--------|
| Hogangnono 404 errors | Medium | All regions return 404 — API URL structure incorrect (appends `00000` to 5-digit code, gets 10-digit code rejected) |
| Port inconsistency | Low | Design says 8080, docker-compose maps to 9000 externally (ADMIN_PORT default=9000 in compose, 8080 in Dockerfile) |

---

## 2. Design vs Implementation Gap Analysis

### 2.1 Architecture & Structure (MATCH: 100%)

| Design Item | Status | Implementation |
|-------------|--------|----------------|
| Docker Compose (postgres + app) | MATCH | `docker-compose.yml` with healthcheck |
| Python 3.12 / FastAPI / SQLAlchemy | MATCH | All in `requirements.txt` |
| APScheduler integration | MATCH | `src/scheduler/jobs.py` |
| Modular collector architecture | MATCH | `BaseCollector` ABC + `CollectorManager` |
| Jinja2 SSR admin page | MATCH | `src/admin/templates/` with 4 templates |
| Alembic migrations | MATCH | `alembic/` directory with initial migration |

### 2.2 Data Model (MATCH: 100%)

| Table | Design | Implementation | Status |
|-------|--------|----------------|--------|
| regions | 7 columns | 7 columns (Mapped) | MATCH |
| schedules | 8 columns | 8 columns (Mapped) | MATCH |
| apt_transactions | 20+ columns | 22 columns + 4 indexes + partial unique | MATCH |
| apt_listings | 17+ columns | 17 columns + 2 indexes + partial unique | MATCH |
| apt_complexes | 16+ columns | 16 columns + 2 indexes + partial unique | MATCH |
| buildings | 12 columns | 12 columns + 1 index | MATCH |
| official_prices | 11 columns | 11 columns + unique constraint | MATCH |
| price_statistics | 12 columns | 12 columns + unique constraint | MATCH |
| collection_logs | 12 columns | 12 columns + 2 indexes | MATCH |

### 2.3 Collector Modules (MATCH: 90%)

| Collector | Design | Implementation | Lines | Status |
|-----------|--------|----------------|-------|--------|
| molit_sale | FR-01 | Full: fetch, parse, upsert, retry | 555 | MATCH |
| molit_jeonse | FR-02 | Full: fetch, parse, upsert, retry | (shared file) | MATCH |
| building | FR-03 | Full implementation | 255 | MATCH |
| official_price | FR-04 | Full implementation | 276 | MATCH |
| reb | FR-05 | Full implementation | 332 | MATCH |
| seoul | FR-06 | Full implementation | 364 | MATCH |
| gyeonggi | FR-07 | Full implementation | 360 | MATCH |
| naver | FR-08 | Full: complex list + articles + upsert | 779 | MATCH |
| zigbang | FR-09 | Full implementation | 732 | MATCH |
| hogangnono | FR-10 | Implemented but API URL broken (404) | 698 | **GAP** |

### 2.4 Admin API Endpoints (MATCH: 85%)

| Design Endpoint | Status | Notes |
|-----------------|--------|-------|
| `GET /` Dashboard | MATCH | Implemented with status cards + log table |
| `GET /regions` page | MATCH | HTML with region table + add form |
| `POST /api/regions` | MATCH | Create region working |
| `PUT /api/regions/{id}` | MATCH | Update region working |
| `DELETE /api/regions/{id}` | MATCH | Delete region working |
| `GET /schedules` page | MATCH | HTML with schedule table |
| `POST /api/schedules` | MATCH | Create schedule working |
| `PUT /api/schedules/{id}` | MATCH | Update schedule working |
| `DELETE /api/schedules/{id}` | MATCH | Delete schedule working |
| `POST /api/collect` | MATCH | Manual trigger (all sources) |
| `POST /api/collect/{source}` | MATCH | Source-specific trigger |
| `GET /api/logs` | MATCH | Returns recent logs |
| `GET /api/status` | MATCH | Returns scheduler + source status |
| `GET /regions` HTML page | **GAP** | Design shows filter by parent_area; not verified in implementation |

### 2.5 Scheduler (MATCH: 100%)

| Design Item | Status | Implementation |
|-------------|--------|----------------|
| APScheduler interval job | MATCH | `setup_scheduler()` with configurable interval |
| Dynamic interval update | MATCH | `update_interval()` function |
| No immediate run on startup | MATCH | `next_run_time = now + interval` |
| Error handling (never crash) | MATCH | Try/except in `collection_job()` |

### 2.6 Error Handling (MATCH: 85%)

| Design Item | Status | Notes |
|-------------|--------|-------|
| Retry with backoff (public API) | MATCH | `_fetch_with_retry()` with [2, 4, 8]s |
| Retry with backoff (private) | MATCH | Naver: [3, 6, 12]s; others similar |
| Per-record error skip | MATCH | Try/except per item in parse loop |
| Upsert (ON CONFLICT) dedup | MATCH | PostgreSQL INSERT ON CONFLICT |
| Source failure isolation | MATCH | Manager catches all exceptions |
| Scheduler never crashes | MATCH | Try/except in collection_job |
| `RETRY_CONFIG` constants | **GAP** | Design specifies separate config dict; implementation uses inline constants |

### 2.7 Configuration (MATCH: 90%)

| Design Variable | Implementation | Status |
|-----------------|----------------|--------|
| `DATABASE_URL` | `settings.database_url` | MATCH |
| `DATA_GO_KR_API_KEY` | `settings.data_go_kr_api_key` | MATCH |
| `COLLECTION_INTERVAL_MINUTES` | `settings.collection_interval_minutes` | MATCH |
| `ADMIN_PORT` | `settings.admin_port` | MATCH |
| `LOG_LEVEL` | `settings.log_level` | MATCH |
| `PRIVATE_CRAWLER_DELAY` | `settings.private_crawler_delay` | MATCH |
| `VWORLD_API_KEY` | `settings.vworld_api_key` (extra) | EXTRA (not in design) |
| `REB_API_KEY` | `settings.reb_api_key` (extra) | EXTRA (not in design) |
| `SEOUL_API_KEY` | `settings.seoul_api_key` (extra) | EXTRA (not in design) |
| `GYEONGGI_API_KEY` | `settings.gyeonggi_api_key` (extra) | EXTRA (not in design) |

### 2.8 Missing from Design (Implementation Extras)

| Item | Notes |
|------|-------|
| `VWORLD_API_KEY`, `REB_API_KEY`, `SEOUL_API_KEY`, `GYEONGGI_API_KEY` | Additional API keys not in original design but needed for real API calls |
| `psycopg2-binary` in requirements | Needed for Alembic sync operations |
| `public_api/utils.py` shared fetch utility | Extracted common code (good practice) |
| Port 9000 in docker-compose | Differs from design's 8080 |

### 2.9 Missing from Implementation (Design Gaps)

| Item | Design Section | Severity | Notes |
|------|---------------|----------|-------|
| `src/db/crud.py` | 11.1 File Structure | Low | Design lists CRUD utility module; not implemented (CRUD is inline in routes) |
| Hogangnono API working | 6.3 Source Pattern | Medium | Crawler exists (698 lines) but API endpoint returns 404 for all regions |
| `.env.example` 불완전 | 10.3 Env Variables | Medium | `SEOUL_API_KEY`, `GYEONGGI_API_KEY`, `REB_API_KEY` 누락 — 설정 시 혼란 유발 |
| `utils.py` 미사용 | 6.1 Base Interface | Low | 공용 `fetch_with_retry()` 존재하나 어디서도 import하지 않음 (dead code) |
| `RETRY_CONFIG` dict | 7.2 Retry Config | Low | Design shows centralized config dict; implementation uses per-module constants |
| Dockerfile CMD difference | 12.2 Dockerfile | Low | Design: `python -m src.main`; Implementation: `uvicorn src.main:app ...` (better) |
| Port inconsistency | 12.1 Docker Compose | Low | Design: 8080 default; Implementation: 9000 default |

---

## 3. Gap Summary

| # | Gap | Severity | Category |
|---|-----|----------|----------|
| G-01 | Hogangnono crawler 404 errors (API URL incorrect) | Medium | Collector |
| G-02 | `.env.example`에 `SEOUL_API_KEY`, `GYEONGGI_API_KEY`, `REB_API_KEY` 누락 | Medium | Config |
| G-03 | `src/db/crud.py` not implemented | Low | Structure |
| G-04 | `public_api/utils.py` dead code (미사용) | Low | Code Quality |
| G-05 | `RETRY_CONFIG` not centralized (per-module 중복) | Low | Config |
| G-06 | Port inconsistency (design=8080, compose default=9000) | Low | Config |

---

## 4. Match Rate Calculation

| Category | Total Items | Matched | Rate |
|----------|-------------|---------|------|
| Architecture | 6 | 6 | 100% |
| Data Model (9 tables) | 9 | 9 | 100% |
| Collectors (10 modules) | 10 | 9 | 90% |
| Admin Endpoints (13) | 13 | 13 | 100% |
| Admin UI (Templates) | 4 | 4 | 100% |
| Scheduler | 4 | 4 | 100% |
| Error Handling | 6 | 5 | 83% |
| Configuration | 7 | 5 | 71% |
| **Total** | **59** | **55** | **~93%** |

> Note: 서브에이전트 검증에서 Region 페이지에 parent_area 필터가 **이미 구현**되어 있음을 확인 (regions.html에 filter bar + applyFilter() JS). Admin 전체 엔드포인트 100% 매칭으로 상향 조정.

---

## 5. Recommendations

### Immediate (to reach 95%+)
1. **Fix Hogangnono API URL** — 현재 5자리 코드를 10자리로 패딩하여 404 발생. 정확한 엔드포인트 구조 조사 필요
2. **`.env.example` 보완** — `SEOUL_API_KEY`, `GYEONGGI_API_KEY`, `REB_API_KEY` 추가

### Nice-to-have
3. `public_api/utils.py` 제거하거나, 각 모듈에서 import하여 중복 제거
4. `src/db/crud.py` 공용 CRUD 헬퍼 추출 (현재 inline으로 동작에는 문제없음)
5. 포트 기본값 통일 (docker-compose의 ADMIN_PORT를 8080으로)

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-03-27 | Initial gap analysis | Claude |
