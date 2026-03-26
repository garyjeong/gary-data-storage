# Gary Data Storage - Real Estate Collector

Apartment sale/jeonse real transaction data and listing data collection & storage system.

## Data Sources

### Public API (data.go.kr)
- MOLIT apartment sale/jeonse real transaction data
- Building ledger (건축물대장)
- Official apartment price (공시가격)
- Korea Real Estate Board statistics (한국부동산원)
- Seoul Open Data (서울 열린데이터광장)
- Gyeonggi Data Dream (경기데이터드림)

### Private Platforms
- Naver Real Estate (네이버부동산)
- Zigbang (직방)
- Hogangnono (호갱노노)

## Quick Start

### 1. Prerequisites
- Docker & Docker Compose
- data.go.kr API key ([Register here](https://www.data.go.kr))

### 2. Setup

```bash
# Clone and enter project
cd gary-data-storage

# Create .env from template
cp .env.example .env

# Edit .env - add your API key
# DATA_GO_KR_API_KEY=your_actual_key_here
```

### 3. Run

```bash
# Start all services
docker compose up -d

# Check logs
docker compose logs -f app

# Access admin page
open http://localhost:8080
```

### 4. Stop

```bash
docker compose down

# To also remove data volume
docker compose down -v
```

## Admin Page

Access at `http://localhost:8080`

- **Dashboard**: Collection status, recent logs, manual trigger
- **Regions**: Add/edit/delete target regions (시군구)
- **Schedules**: Configure collection intervals

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_PASSWORD` | PostgreSQL password | `collector_pass` |
| `DATA_GO_KR_API_KEY` | data.go.kr API key | (required) |
| `COLLECTION_INTERVAL_MINUTES` | Auto-collection interval | `30` |
| `ADMIN_PORT` | Admin page port | `8080` |
| `LOG_LEVEL` | Logging level | `INFO` |
| `PRIVATE_CRAWLER_DELAY` | Delay between crawler requests (sec) | `2` |

### Region Management

Default regions (Seoul 25 districts + Gyeonggi) are seeded from `config/regions.yaml` on first startup. Additional regions can be added via the admin page.

## Architecture

```
Docker Compose
├── postgres (PostgreSQL 16)
│   └── 9 tables (transactions, listings, complexes, buildings, prices, statistics, logs, regions, schedules)
└── app (Python 3.12)
    ├── Collector (9 source modules)
    ├── Scheduler (APScheduler, configurable interval)
    └── Admin (FastAPI + Jinja2, :8080)
```

## Tech Stack

- Python 3.12
- FastAPI + Jinja2 (admin page)
- SQLAlchemy 2.0 + asyncpg (async PostgreSQL)
- APScheduler (job scheduling)
- httpx (HTTP client)
- Alembic (DB migrations)
- Docker Compose
