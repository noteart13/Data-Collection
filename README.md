# Australian Property Master & Data Scraping Pipeline

[![Python 3.10+](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Qdrant](https://img.shields.io/badge/Qdrant-v1.9+-crimson.svg)](https://qdrant.tech/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-009688.svg)](https://fastapi.tiangolo.com/)
[![Playwright](https://img.shields.io/badge/Playwright-1.49+-2EAD33.svg)](https://playwright.dev/)
[![Scikit-Learn](https://img.shields.io/badge/scikit--learn-1.4+-F7931E.svg)](https://scikit-learn.org/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![Kubernetes GKE](https://img.shields.io/badge/Kubernetes-GKE%20Production-326CE5.svg)](https://cloud.google.com/kubernetes-engine)

Enterprise-grade Australian real estate data engineering, enrichment, and scraping platform covering **16M+ national properties**. Built on **Qdrant Vector/Payload Database**, official Commonwealth and State registers (**G-NAF**, **ACT Government Cadastre**, **NSW Valuer General Bulk Sales**), resilient stealth web crawlers with listing **Change Data Capture (CDC)**, bank-grade **Machine Learning Automated Valuation Models (AVM)**, **768-D Multimodal Visual Search**, **Cadastral GIS parcel polygon geometry**, and a **Multi-Criteria Location Intelligence Scorecard**.

---

## Table of Contents

- [System Architecture](#-system-architecture)
- [Repository Structure](#-repository-structure)
- [Core Technology Pillars](#-core-technology-pillars)
  - [Phase 1: Resilient Crawler Cluster & Change Data Capture (CDC)](#phase-1-resilient-crawler-cluster--change-data-capture-cdc)
  - [Phase 2: AI Multimodal Visual Search & ML Valuation Engine (AVM)](#phase-2-ai-multimodal-visual-search--ml-valuation-engine-avm)
  - [Phase 3: Cadastral GIS, Land Parcel Geometry & Location Intelligence](#phase-3-cadastral-gis-land-parcel-geometry--location-intelligence)
- [Environment Configuration](#-environment-configuration)
- [Getting Started & Installation](#-getting-started--installation)
- [Unified Pipeline CLI (`cli.py`)](#-unified-pipeline-cli-clipy)
- [Standalone Pipeline Scripts](#-standalone-pipeline-scripts)
- [Data Contracts, Quality Auditing & Schemas](#-data-contracts-quality-auditing--schemas)
- [FastAPI Web Service Reference](#-fastapi-web-service-reference)
- [Containerization & Production Kubernetes Deployment](#-containerization--production-kubernetes-deployment)
- [Testing & Quality Verification](#-testing--quality-verification)
- [Production Performance & Operational Guidelines](#-production-performance--operational-guidelines)
- [License & Support](#-license--support)

---

## 🏗️ System Architecture

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 OFFICIAL DATA REGISTERS                                │
│                                                                                        │
│   ┌──────────────────────┐      ┌──────────────────────┐      ┌────────────────────┐   │
│   │  data.gov.au G-NAF   │      │ ACT Gov ArcGIS REST  │      │ NSW Valuer General │   │
│   │ (16M+ Geocoded Addr) │      │  (224K Cadastral)    │      │ (1.5M+ Bulk Sales) │   │
│   └──────────┬───────────┘      └──────────┬───────────┘      └─────────┬──────────┘   │
└──────────────┼─────────────────────────────┼────────────────────────────┼──────────────┘
               │ HTTP Range (Direct ZIP)     │ ArcGIS REST FeatureServer  │ Bulk Multi-Year CSV
               ▼                             ▼                            ▼
   ┌───────────────────────┐     ┌───────────────────────┐    ┌───────────────────────┐
   │stream_gnaf_to_qdrant  │     │ingest_act_gov_master  │    │merge_nsw_sales_to_    │
   │• Zero-download stream │     │• Cadastral blocks     │    │master                 │
   │• Suburb city mapping  │     │• Section & lot sync   │    │• Sales contract dates │
   └───────────┬───────────┘     └───────────┬───────────┘    │• Agency & settlement  │
               │                             │                └───────────┬───────────┘
               └──────────────────────┬──────┴────────────────────────────┘
                                      ▼
               ┌──────────────────────────────────────────────┐
               │          QDRANT VECTOR / PAYLOAD DB          │
               │                                              │
               │   • PropertyMaster   (16M+ Core Records)     │
               │   • PropertySoldV2   (Historical Sales)      │
               │   • PropertyForSale  (Active Sale Listings)  │
               │   • PropertyForRent  (Rental Listings)       │
               └──────────────────────┬───────────────────────┘
                                      │
     ┌────────────────────────────────┼────────────────────────────────┐
     ▼                                ▼                                ▼
┌────────────────────────┐  ┌────────────────────────┐  ┌────────────────────────┐
│   RESILIENT SCRAPING   │  │   AI MULTIMODAL & ML   │  │  SPATIAL GIS & CADASTRAL│
│                        │  │                        │  │                        │
│ • Smart Proxy Manager  │  │ • ML AVM Regressor     │  │ • Shoelace Land Area   │
│ • Stealth Playwright   │  │   (Huber loss, MdAPE)  │  │ • Ray-Casting Polygon  │
│ • Listing CDC Engine   │  │ • 768-D Visual Search  │  │ • LEP & Territory Plan │
│   (Status/Price delta) │  │ • Room Scene Classifier│  │ • Location Score (0-10)│
│ • Async Runner Cluster │  │ • Suburb Epoch Cohorts │  │ • Geofence Alerting    │
└────────────────────────┘  └────────────────────────┘  └────────────────────────┘
                                      │
                                      ▼
               ┌──────────────────────────────────────────────┐
               │           CLIENTS & CONSUMING APIS           │
               │                                              │
               │  • Unified CLI (cli.py - 18 Commands)        │
               │  • FastAPI Suburb Scraper (app.main:app)     │
               │  • NL2-NoSQL Query Interface (nl2_sql_nosql) │
               │  • Kubernetes GKE Production Ingress         │
               └──────────────────────────────────────────────┘
```

---

## 📂 Repository Structure

```text
data-scraping/
├── .env.example                     # Environment variables template
├── .gitignore                       # Git exclusion rules
├── .dockerignore                    # Docker build exclusion rules
├── Dockerfile                       # Multi-stage Python 3.12 container with Chromium & Playwright
├── Dockerfile_add_new               # Extended deployment container variant
├── README.md                        # Master project documentation
├── resources.yaml                   # Kubernetes GKE Deployment, ManagedCertificate & Ingress
├── setup.py                         # Package definition (property-master-pipeline v0.2.0)
├── pytest.ini                       # Test suite runner configuration
│
├── app/                             # Core Application Package
│   ├── __init__.py
│   ├── main.py                      # FastAPI Application Entrypoint (Suburb Scraper API)
│   ├── requirements.txt             # Primary production dependencies
│   ├── crawler_runner.py            # Distributed async Playwright crawler cluster orchestrator
│   ├── train_avm.py                 # Standalone ML AVM model training and benchmark pipeline
│   ├── scraping_all_suburbs_fastapi.py # Legacy & async background suburb scraping drivers
│   │
│   ├── operations/                  # Specialized Engineering & Algorithmic Modules
│   │   ├── __init__.py
│   │   ├── proxy_manager.py         # Smart proxy pool, Australian fingerprints, circuit breaker
│   │   ├── stealth_crawler.py       # Camouflaged Playwright crawler with human interaction emulation
│   │   ├── listing_cdc.py           # Listing lifecycle Change Data Capture (CDC) engine
│   │   ├── ml_avm.py                # Gradient Boosting AVM with Huber loss & confidence intervals
│   │   ├── multimodal_search.py     # 768-D hypersphere vector embeddings & visual prompt search
│   │   ├── room_classifier.py       # Australian photo taxonomy classification & auto-tagging
│   │   ├── cadastral_spatial.py     # Geodesic Shoelace land area, Ray-casting, LEP/ACT zoning
│   │   ├── location_score_engine.py # Multi-criteria proximity scorecard (Schools, Transit, Amenities)
│   │   └── geo_alerting.py          # Circular & polygonal geofence monitoring & buyer alerting
│   │
│   ├── schemas/                     # Data Contracts & Schemas
│   │   ├── __init__.py
│   │   └── property_schema.py       # Pydantic V2 models, address validation, data quality scoring
│   │
│   └── utils/                       # Geospatial mappings and helper datasets
│       └── suburb_city_mapping_v1_NSW_ACT.json
│
├── cli.py                           # Unified Management CLI (18 administrative & pipeline commands)
├── stream_gnaf_to_qdrant.py         # HTTP Range streaming ingestion of 16M+ G-NAF addresses
├── ingest_act_gov_master.py         # Ingestion of official ACT Government cadastral addresses
├── merge_nsw_sales_to_master.py     # Ingestion of 1.5M+ NSW Valuer General historical sales
├── enrich_nsw_master_with_sold.py   # Cross-collection synchronization (PropertySoldV2 -> Master NSW)
├── enrich_act_master_with_sold.py   # Cross-collection synchronization (PropertySoldV2 -> Master ACT)
├── dedup_propertymaster.py          # Per-suburb address normalization and deduplication engine
├── bulk_enrich_master.py            # High-throughput multi-threaded AVM and feature synthesis (16M)
├── nl2_sql_nosql.py                 # Natural Language to MongoDB aggregation pipeline generator
│
├── data/                            # Sample raw archives, sales CSVs, and mappings
├── docs/                            # Deep-dive architecture and design specifications
├── logs/                            # Pipeline run logs and operational diagnostics
├── scratch/                         # Local temporary cache, block indices, and download chunks
└── tests/                           # Unit, functional, and integration test suite
```

---

## 🛡️ Core Technology Pillars

### Phase 1: Resilient Crawler Cluster & Change Data Capture (CDC)

Phase 1 provides enterprise-grade data ingestion resilience, anti-bot evasion, and atomic lifecycle tracking:

1. **Smart Proxy Manager (`app/operations/proxy_manager.py`)**:
   - **Dynamic Node Health Tracking**: Monitors latency, rolling success/failure counters, and consecutive ban streaks.
   - **Intelligent Circuit Breaker**: Auto-applies adaptive cooldowns (default 300s) on HTTP 403 (Forbidden) and 429 (Rate Limited) responses to safeguard residential IP reputation.
   - **Rotation Strategies**: Configurable `ROUND_ROBIN`, `LEAST_USED`, or `RANDOM` allocation.
   - **Authentic Australian Browser Fingerprints**: Injects randomized desktop User-Agents, matching Chromium Client Hints (`sec-ch-ua`, `sec-ch-ua-platform`), Australian English language tags (`Accept-Language: en-AU,en-GB;q=0.9,en;q=0.8`), and Australian timezones (`Australia/Sydney`).

2. **Stealth Playwright Crawler (`app/operations/stealth_crawler.py`)**:
   - **Fingerprint Camouflage**: Masks `navigator.webdriver`, spoofs `window.chrome`, randomizes WebGL rendering contexts, and introduces sub-pixel viewport jitter.
   - **Human Interaction Emulation**: Simulates stepped, variable-velocity mouse scrolling, realistic reading hesitation pauses (1.5s - 4.5s), and natural micro-movements.
   - **Robust Listing Parser**: Resilient CSS/XPath selectors extracting property metadata, price guides, auction timestamps, agency branding, and media galleries.

3. **Listing Lifecycle Change Data Capture (`app/operations/listing_cdc.py`)**:
   - **Delta Event Emission**: Compares newly harvested listing snapshots against current Qdrant state to emit atomic events:
     - `NEW_LISTING`: First-time property discovery.
     - `STATUS_CHANGED`: Lifecycle transitions (e.g., `for_sale` → `sold`, `for_sale` → `withdrawn`).
     - `PRICE_REDUCED` / `PRICE_INCREASED`: Price adjustments logged with timestamps, previous/new figures, and delta percentages in `price_change_history`.
     - `SOLD_RECORDED`: Newly settled transactions appended directly to the `historysale` audit array.
   - **Audit Trail Integrity**: Prevents destructive full-payload overwrites by recording timestamped change vectors.

4. **Crawler Cluster Orchestrator (`app/crawler_runner.py`)**:
   - High-concurrency async Python cluster runner replacing legacy bash shell scripts.
   - Features graceful shutdown handling (`SIGINT`/`SIGTERM`), connection pooling, worker concurrency caps, and direct streaming to Qdrant.

---

### Phase 2: AI Multimodal Visual Search & ML Valuation Engine (AVM)

Phase 2 replaces static heuristics with machine learning valuation models and multimodal vector embeddings:

1. **Machine Learning Automated Valuation Model (`app/operations/ml_avm.py`)**:
   - **11-Dimensional Feature Engineering**:
     $$\mathbf{x} = [\text{lat}, \text{lon}, d_{\text{cbd}}, \text{bed}, \text{bath}, \text{parking}, \text{area}_{\text{sqm}}, \mathbb{I}_{\text{house}}, \mathbb{I}_{\text{unit}}, \mathbb{I}_{\text{townhouse}}, P_{\text{state\_base}}]$$
   - **Urban Rent-Gradient Distance Decay**: Computes great-circle Haversine distances to Australian Capital City CBD coordinates (Sydney, Melbourne, Brisbane, Perth, Adelaide, Canberra, Hobart, Darwin).
   - **Gradient Boosting Hedonic Pricing**: Trains `GradientBoostingRegressor` with **Huber loss** (robust against extreme luxury outliers) and benchmarks against bank standards:
     $$\text{MdAPE} = \text{median}\left(\left|\frac{y_i - \hat{y}_i}{y_i}\right|\right) \le 12.0\%$$
   - **Statistical Confidence Intervals**: Generates `valuation_mid`, `valuation_low` (10th percentile bound), `valuation_high` (90th percentile bound), and an integrity `confidence_score` (0.0 to 1.0).

2. **Multimodal Visual Search & Embeddings (`app/operations/multimodal_search.py`)**:
   - **768-D Normalized Hypersphere Embeddings**: Projects architectural styles, interior finishes, and lifestyle queries onto a 768-dimensional unit hypersphere matching Qdrant collection geometry.
   - **Lifestyle Natural Language Prompts**: Enables semantic visual discovery, e.g., *"Hamptons style entertainer with quartz island bench and heated lap pool"* or *"Brutalist concrete apartment with harbour views"*.
   - **Hybrid Cosine Similarity & Metadata Filtering**: Combines vector cosine similarity ranking with strict payload filters (State, Suburb, Bedrooms, Price bracket).

3. **Room Scene Classifier & Auto-Tagging (`app/operations/room_classifier.py`)**:
   - **Standard Australian Photo Taxonomy**: Accurately categorizes listing photos into:
     `frontview` | `living_room` | `kitchen` | `bathroom` | `bedroom` | `dining` | `backyard` | `balcony` | `floorplan`
   - **Multi-Signal Heuristic & URL Token Analysis**: Combines image CDN URL tokens, portal image captions, and layout aspect ratios.
   - **Canonical Payload Enrichment**: Automatically elects the primary exterior facade photo (`primary_photo`), sets `has_floorplan`, and aggregates `room_counts_detected`.

---

### Phase 3: Cadastral GIS, Land Parcel Geometry & Location Intelligence

Phase 3 transitions the platform from isolated coordinate points to polygonal land cadastre and urban spatial intelligence:

1. **Cadastral Parcel Geometry & Shoelace Area (`app/operations/cadastral_spatial.py`)**:
   - **Zero C-GIS Dependencies**: Pure Python and NumPy implementation operating without heavy GDAL/GEOS system binaries.
   - **Ray-Casting Point-in-Polygon (PIP)**: Exact containment validation verifying whether property coordinate points fall within legal boundary vertices.
   - **Geodesic Shoelace Land Area**: Accurately computes parcel surface area in square meters ($m^2$) under spherical Earth projection:
     $$\text{Area} = \frac{R^2}{2} \left| \sum_{i=1}^{n} (\lambda_{i+1} - \lambda_{i-1}) \sin(\phi_i) \right|$$
   - **Comprehensive Planning & Zoning Engine**: Official mapping of NSW Standard LEP codes (`R1-R5`, `B1-B4`, `IN1-IN2`, `RU1-RU2`, `E1-E2`, `SP1-SP2`) and ACT Territory Plan codes (`RZ1-RZ5`, `CZ1-CZ5`, `PRZ1`, `NUZ1`).
   - **GeoJSON Compliance**: Emits RFC 7946 compliant GeoJSON `Polygon` features directly compatible with Qdrant spatial payloads.

2. **Multi-Criteria Location Scorecard (`app/operations/location_score_engine.py`)**:
   - **Weighted Proximity Metric (0.0 to 10.0)**:
     - **Schools & Educational Catchment (35% weight)**: Proximity to top primary, secondary, and selective high schools.
     - **Transit & Walkability (35% weight)**: Proximity to Train, Metro, Light Rail, and Ferry terminals (< 400m scores 9.5+).
     - **Amenities & Healthcare (30% weight)**: Proximity to major Westfield/retail hubs and public/private hospitals.
   - **Payload Enrichment**: Enriches property records with calculated POI metadata (`nearest_station`, `nearest_school`, `nearest_hospital`, `distance_to_cbd_km`).

3. **Geospatial Geofencing & Alerting Monitor (`app/operations/geo_alerting.py`)**:
   - **Surveillance Boundaries**: Supports circular radius buffers (`Circle(lat, lon, radius_km)`) and arbitrary polygonal boundaries.
   - **Real-Time Property Matching**: Filters inbound listings and CDC events by spatial containment, budget caps, bedroom counts, property types, and minimum location scores.
   - **Event Dispatch**: Dispatches structured `GeofenceAlertEvent` objects for investor alerting and buyer watchlists.

---

## ⚙️ Environment Configuration

Create a `.env` file in the project root based on `.env.example`:

```env
# ==============================================================================
# 1. LLM & EMBEDDING CONFIGURATION
# ==============================================================================
MODEL=gpt-3.5-turbo
OPENAI_API_KEY=sk-proj-your-openai-api-key
EMBEDDING_MODEL=text-embedding-3-small
TOP_K=3

# ==============================================================================
# 2. SERVER & LOCALISATION
# ==============================================================================
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_TIMEZONE=Australia/Sydney
SCRATCH_DIR=scratch
NODRIVER_HEADLESS=false

# ==============================================================================
# 3. QDRANT VECTOR & PAYLOAD DATABASE
# ==============================================================================
QDRANT_URI=http://localhost:6333
QDRANT_API=your-qdrant-readwrite-api-key
QDRANT_SCRAPE_API=your-qdrant-scrape-api-key

# ==============================================================================
# 4. MONGODB DATABASE CONFIGURATION
# ==============================================================================
MONGODB_URI=mongodb://localhost:27017/property_master
MONGODB_DATABASE=property_master
MONGODB_VECTOR_INDEX=vector_index
MONGODB_VECTORS=chatbot

# ==============================================================================
# 5. CLOUDINARY & IMGPROXY MEDIA SERVICES
# ==============================================================================
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-cloudinary-key
CLOUDINARY_API_SECRET=your-cloudinary-secret
CLOUDINARY_PROXY=

IMGPROXY_HOST=https://imgproxy.yourdomain.com
IMGPROXY_KEY_HEX=your-imgproxy-hex-key
IMGPROXY_SALT_HEX=your-imgproxy-salt-hex
IMGPROXY_PATH=rs:fit:::0/g:no

# ==============================================================================
# 6. SMART PROXY POOL & ANTI-BOT
# ==============================================================================
PROXY_LIST=
PROXY_FILE=proxies.txt
PROXY_STRATEGY=ROUND_ROBIN
```

### Environment Variables Glossary

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `QDRANT_URI` | URL | `http://localhost:6333` | Target Qdrant server endpoint (HTTP/REST & gRPC). |
| `QDRANT_API` | String | *None* | Read/write API authentication key for Qdrant cluster. |
| `DATABASE_TIMEZONE` | String | `Australia/Sydney` | Canonical timezone used for timestamping sales, listings, and audit events. |
| `OPENAI_API_KEY` | String | *None* | OpenAI API Key used for NL2-SQL generation and embedding pipelines. |
| `MONGODB_URI` | URI | *None* | Connection string for MongoDB Atlas / replica set. |
| `IMGPROXY_HOST` | URL | *None* | ImgProxy endpoint for dynamic, on-the-fly image thumbnail resizing. |
| `IMGPROXY_KEY_HEX` | Hex | *None* | Cryptographic HMAC signing key for secure ImgProxy URL generation. |
| `IMGPROXY_SALT_HEX` | Hex | *None* | Cryptographic salt for secure ImgProxy URL generation. |
| `PROXY_STRATEGY` | Enum | `ROUND_ROBIN` | Rotation algorithm: `ROUND_ROBIN`, `LEAST_USED`, or `RANDOM`. |
| `PROXY_FILE` | Path | `proxies.txt` | Path to text file containing proxy endpoints (`protocol://user:pass@host:port`). |
| `SCRATCH_DIR` | Path | `scratch` | Directory for temporary caching, checkpoints, and chunk streaming. |

---

## 🚀 Getting Started & Installation

### 1. Prerequisites
- **Python**: Version 3.10, 3.11, or 3.12
- **Qdrant**: Version 1.9.0 or higher
- **Node/Chromium** (for crawler execution): Installed automatically via Playwright

### 2. Local Environment Setup

```bash
# Clone the repository
git clone https://github.com/your-username/property-data-pipeline.git
cd property-data-pipeline

# Create and activate Python virtual environment
python -m venv .venv

# On Linux / macOS:
source .venv/bin/activate
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Install core dependencies
pip install -r app/requirements.txt

# Install Playwright browser binaries with system dependencies
playwright install --with-deps chromium
```

### 3. Verify System Health

Run the CLI health check to inspect Qdrant connectivity and collection sizes:

```bash
python cli.py status
```

---

## 💻 Unified Pipeline CLI (`cli.py`)

The platform includes a unified command-line orchestrator exposing **18 administrative, ingestion, analytical, and operational subcommands**:

```text
usage: cli.py [-h] {status,audit-quality,ingest-gnaf,ingest-act,merge-sales,enrich-sold,
                    dedup,bulk-enrich,crawl-cluster,test-proxies,train-avm,predict-avm,
                    visual-search,classify-rooms,score-location,geofence-monitor,
                    parcel-inspect,serve-api} ...
```

### CLI Command Reference

#### 1. System Status & Health Check
Inspect Qdrant collection point counts, status, and environment settings.
```bash
python cli.py status
```

#### 2. Data Quality & Synthetic Attribution Audit
Sample property points and compute completeness scores, source provenance, and synthetic attribute ratios.
```bash
python cli.py audit-quality --sample-size 1000 --state NSW
```

#### 3. Ingest G-NAF National Addresses
Stream geocoded addresses directly from remote Commonwealth archives into Qdrant using HTTP Range Requests.
```bash
python cli.py ingest-gnaf --batch-size 2000 --states NSW ACT VIC QLD
```

#### 4. Ingest ACT Government Cadastre
Harvest official parcel blocks, sections, and street addresses via ACT Government ArcGIS REST API.
```bash
python cli.py ingest-act
```

#### 5. Merge NSW Valuer General Historical Sales
Parse multi-year sales archives, extract contract/settlement dates, and link sales histories into `PropertyMaster`.
```bash
python cli.py merge-sales --concurrency 4 --suburb "Sydney"
```

#### 6. Cross-Collection Sold Listing Sync
Synchronize listing media, descriptions, and agent metadata from `PropertySoldV2` into `PropertyMaster`.
```bash
python cli.py enrich-sold --state NSW
```

#### 7. Per-Suburb Deduplication
Identify duplicate address entries per suburb, evaluate data completeness scores, and prune inferior points.
```bash
python cli.py dedup --state NSW --suburb "Chatswood" --concurrency 2
```

#### 8. High-Throughput Bulk Enrichment & AVM
Synthesize bedrooms, bathrooms, construction years, living areas, and estimated market valuations at 2,000+ rec/s.
```bash
python cli.py bulk-enrich --state NSW --workers 8 --batch-size 1000
```

#### 9. Test Proxy Pool & Australian Fingerprints
Verify proxy latency, test Australian desktop headers (`sec-ch-ua`, `User-Agent`), and inspect cooldown states.
```bash
python cli.py test-proxies --test-url https://httpbin.org/ip --timeout 10
```

#### 10. Run Distributed Crawler Cluster
Launch the async stealth Playwright crawler cluster across target suburbs with active Change Data Capture (CDC).
```bash
python cli.py crawl-cluster --suburbs "Sydney,Parramatta,Manly" --state NSW --max-pages 5 --workers 2
```

#### 11. Train Machine Learning AVM Model
Train `GradientBoostingRegressor` (Huber loss) on sales comps, evaluate MdAPE benchmarks, and export `.joblib` model.
```bash
python cli.py train-avm --samples 2500 --state NSW --out models/avm_property_model.joblib --bootstrap
```

#### 12. Predict Valuation with Confidence Intervals
Estimate market value range, 10th/90th percentile bounds, and confidence score for any property profile.
```bash
python cli.py predict-avm --state NSW --suburb "Sydney" --bed 3 --bath 2 --parking 1 --area 550 --prop-cat house
```

#### 13. Multimodal Lifestyle Visual Search
Execute natural language architectural/lifestyle queries against 768-D Qdrant vector space.
```bash
python cli.py visual-search --query "Hamptons style home with marble kitchen island" --state NSW --min-bed 3 --limit 5
```

#### 14. Classify Listing Photo Galleries
Classify gallery image URLs into standard Australian property taxonomy and select the primary exterior photo.
```bash
python cli.py classify-rooms --urls "https://media.domain.com.au/p/kitchen.jpg" "https://media.domain.com.au/p/facade.jpg"
```

#### 15. Calculate Location Intelligence Score
Compute proximity scorecard (Schools 35%, Transit 35%, Amenities 30%, Overall) with nearest POI details.
```bash
python cli.py score-location --lat -33.7975 --lon 151.1809 --state NSW --suburb "Chatswood"
```

#### 16. Geospatial Geofencing & Alerting Monitor
Run circular or polygonal geofence surveillance to trigger alerts for matching buyer criteria.
```bash
python cli.py geofence-monitor --center-lat -33.7975 --center-lon 151.1809 --radius-km 3.0 --state NSW --max-price 2000000 --min-bed 2
```

#### 17. Inspect Cadastral Parcel Geometry & Zoning
Calculate geodesic Shoelace land area in $m^2$, inspect parcel vertices, and resolve LEP/Territory Plan zoning.
```bash
python cli.py parcel-inspect --lat -33.8688 --lon 151.2093 --area 550 --zoning R2 --state NSW
```

#### 18. Start Suburb Scraper Web API
Start the production FastAPI background scraping server via Uvicorn.
```bash
python cli.py serve-api --host 0.0.0.0 --port 8000 --reload
```

---

## 📜 Standalone Pipeline Scripts

When running pipelines independently or integrating with external schedulers (e.g. Airflow, Cron), invoke the core scripts directly:

### 1. Remote G-NAF Address Ingestion (`stream_gnaf_to_qdrant.py`)
Extracts and parses pipe-separated (`PSV`) tables directly from Commonwealth ZIP archives using byte-range headers without storing 5GB+ archives on disk.

```bash
# Ingest all Australian States:
python stream_gnaf_to_qdrant.py

# Ingest specific states with custom batch sizing:
python stream_gnaf_to_qdrant.py --states NSW ACT VIC --batch-size 2500
```

### 2. ACT Cadastral Ingestion (`ingest_act_gov_master.py`)
Queries ACT Government FeatureServer REST APIs in 1,000-feature chunks with multi-threaded workers, caches block numbers, and upserts points.

```bash
python ingest_act_gov_master.py
```

### 3. NSW Valuer General Sales Merger (`merge_nsw_sales_to_master.py`)
Processes official property sales records (`.DAT` / `.CSV`), standardizes contract and settlement dates to ISO 8601, and updates `historysale` arrays.

```bash
# Merge sales across entire state with 4 concurrent threads:
python merge_nsw_sales_to_master.py --concurrency 4

# Target a specific suburb with dry-run mode:
python merge_nsw_sales_to_master.py --suburb "Parramatta" --dry-run
```

### 4. Per-Suburb Master Deduplication (`dedup_propertymaster.py`)
Eliminates duplicate property entries caused by variations in street suffixes (e.g., *Street* vs *St*, *Avenue* vs *Ave*) or unit markers (*Unit 1/12* vs *1/12*).

```bash
python dedup_propertymaster.py --state NSW --concurrency 4
```

### 5. High-Throughput Bulk Enrichment (`bulk_enrich_master.py`)
Populates missing attributes (bed, bath, parking, land area, construction year, expected valuation) using statistical cohort regularizers and AVM models.

```bash
python bulk_enrich_master.py --state NSW --workers 8 --batch-size 2000
```

### 6. Natural Language to MongoDB Pipeline (`nl2_sql_nosql.py`)
Translates natural language questions into MongoDB aggregation queries and validates payload structures using Pydantic.

```bash
python nl2_sql_nosql.py
```

---

## 📊 Data Contracts, Quality Auditing & Schemas

The platform enforces strict Pydantic V2 data validation and metadata scoring in `app/schemas/property_schema.py`:

### Property Point Payload Contract

```json
{
  "id": "c8f2b740-4217-5e92-9a3b-28f09d841e21",
  "title": "12 Castlereagh Street, Sydney NSW 2000",
  "street": "12 Castlereagh Street",
  "suburb": "Sydney",
  "state": "NSW",
  "postcode": "2000",
  "country": "Australia",
  "propertyType": "house",
  "bed": 3,
  "bath": 2,
  "features": {
    "garage": "2",
    "parking": ["Double Lockup Garage"],
    "rooms": ["Study", "Ensuite", "Open Living"]
  },
  "area": {
    "totalArea": 550.0,
    "unit": "sqM"
  },
  "coordinates": {
    "lat": -33.8688,
    "lon": 151.2093
  },
  "expectedPrice": 2450000,
  "valuationType": "AVM_Gradient_Boosting",
  "confidenceScore": 0.88,
  "constructionYear": "1998",
  "location_scores": {
    "overall": 9.2,
    "schools": 8.8,
    "transit": 9.8,
    "amenities": 9.0,
    "distance_to_cbd_km": 0.4
  },
  "cadastral_parcel": {
    "block_key": "NSW-SYD-1024",
    "lot": "12",
    "plan": "DP883210",
    "zoning_code": "R2",
    "zoning_description": "Low Density Residential",
    "calculated_area_sqm": 552.4
  },
  "historysale": [
    {
      "price": 1820000,
      "date": "2019-11-15",
      "agency": "Valuer General Official"
    }
  ],
  "price_change_history": [
    {
      "event": "PRICE_REDUCED",
      "old_price": 2550000,
      "new_price": 2450000,
      "delta_pct": -3.92,
      "timestamp": "2026-10-15T04:22:10Z"
    }
  ],
  "data_quality": {
    "data_quality_score": 95.0,
    "data_source": "VALUER_GENERAL_OFFICIAL",
    "is_synthetic": false,
    "synthetic_attributes": [],
    "last_quality_audit": "2026-10-30T09:00:00Z"
  }
}
```

### Data Quality Score Breakdown (0.0 to 100.0)

| Category | Points | Criteria Evaluated |
| :--- | :--- | :--- |
| **Address Core Integrity** | **30 pts** | Valid street, suburb, state (20 pts), 4-digit postcode (5 pts), valid lat/lon coordinates (5 pts). |
| **Living Metrics Quality** | **30 pts** | Valid bed count > 0 (10 pts), bath count > 0 (10 pts), land/internal area > 10 $m^2$ (10 pts). |
| **Valuation & Sales History**| **25 pts** | Expected price > \$50,000 (10 pts), settled sales history present (10 pts), official government verified source (5 pts). |
| **Richness & Cadastral Media**| **15 pts** | Description text > 80 chars (5 pts), listing images present (5 pts), realistic construction year (5 pts). |

---

## 🌐 FastAPI Web Service Reference

The Suburb Scraper API runs on FastAPI (`app/main.py`) providing endpoints for service monitoring and triggering asynchronous scraping jobs:

### Endpoints

| Method | Endpoint | Summary | Response |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | Root service health check | `{"status": "ok", "service": "suburb-scraper"}` |
| `GET` | `/health` | Kubernetes readiness & liveness probe | `{"status": "ok", "service": "suburb-scraper"}` |
| `POST` | `/scrape` | Queue asynchronous suburb scraping background task | `ScrapeResult` JSON payload |

### Sample Trigger Scrape Request

```bash
curl -X POST http://localhost:8000/scrape \
     -H "Content-Type: application/json"
```

**Response (`200 OK`)**:
```json
{
  "status": "queued",
  "invalid_url_file": "scratch/invalid_urls.txt",
  "progress_csv": "scratch/suburbs_progress.csv",
  "name_fail_csv": "scratch/failed_suburbs.csv"
}
```

---

## 🐳 Containerization & Production Kubernetes Deployment

### 1. Docker Build & Execution

The project includes a multi-stage production Dockerfile (`Dockerfile`) based on `python:3.12-slim` equipped with Chromium, Xvfb virtual frame buffer, and Playwright dependencies:

```bash
# Build the container image
docker build -t property-scraper:v0.2.0 .

# Run container locally with environment file
docker run -d \
  --name property-scraper \
  --env-file .env \
  -p 8000:8000 \
  property-scraper:v0.2.0
```

### 2. Kubernetes GKE Production Deployment (`resources.yaml`)

The repository includes Google Kubernetes Engine (GKE) production manifests featuring:
- **ManagedCertificate**: Automatic Google-managed SSL/TLS certificate for `scraper.yourdomain.com`.
- **FrontendConfig**: Enforces HTTP-to-HTTPS automatic redirection.
- **GCE Ingress & NodePort Service**: Global external Application Load Balancer routing traffic to container port 8000.
- **Deployment**: Single or multi-replica container deployment pulling from Google Artifact Registry with ConfigMap and Secret bindings.

```bash
# Apply production manifests to GKE cluster
kubectl apply -f resources.yaml

# Verify ingress and certificate provisioning
kubectl get managedcertificate image-cls-mat
kubectl get ingress image-cls-mat
```

---

## 🧪 Testing & Quality Verification

The test suite is managed via `pytest`:

```bash
# Run all unit and integration tests
pytest

# Run tests with verbose output and short traceback
pytest -v --tb=short

# Run specific operation tests:
pytest tests/test_proxy_manager.py
pytest tests/test_cadastral_spatial.py
pytest tests/test_ml_avm.py
```

### Validating Core Functionality via CLI

You can verify all critical components locally without external dependencies:

```bash
# 1. Verify proxy pool & Australian header injection:
python cli.py test-proxies

# 2. Run Cadastral Shoelace area & LEP zoning resolution:
python cli.py parcel-inspect --lat -33.8688 --lon 151.2093 --area 650 --zoning R2 --state NSW

# 3. Test Location Scorecard (Schools, Transit, Amenities):
python cli.py score-location --lat -33.7975 --lon 151.1809 --suburb "Chatswood" --state NSW

# 4. Predict real estate valuation using ML AVM:
python cli.py predict-avm --state NSW --suburb "Sydney" --bed 3 --bath 2 --area 600 --prop-cat house

# 5. Classify real estate photo gallery URLs:
python cli.py classify-rooms

# 6. Test geospatial geofencing alerts:
python cli.py geofence-monitor --center-lat -33.7975 --center-lon 151.1809 --radius-km 5.0 --state NSW
```

---

## ⚡ Production Performance & Operational Guidelines

1. **Direct HTTP Range Requests on ZIPs**:
   Always prefer `stream_gnaf_to_qdrant.py` over downloading full archives. It retrieves central directories and requested table byte slices via HTTP Range requests, reducing network transfer by over 90%.
2. **Qdrant Indexing Strategy**:
   Ensure payload schema indexes exist on `state`, `suburb`, `postcode`, and `status` before initiating bulk joins. Use `vectors_config={}` for vectorless metadata collections to conserve RAM.
3. **Multi-Threaded Concurrency Limits**:
   When running `bulk_enrich_master.py` or `merge_nsw_sales_to_master.py`, tune `--workers` between 4 and 12 depending on Qdrant instance vCPU allocation and network round-trip latency.
4. **Proxy Cooldown Management**:
   Configure residential proxy pools with at least 20 nodes when crawling portals to allow the `SmartProxyManager` circuit breaker to rotate around temporary 429 cooldowns without stalling crawlers.

---

## 📄 License & Support

- **Package Name**: `property-master-pipeline` (v0.2.0)
- **Maintainer**: Property Data Engineering Team
- **License**: MIT / Proprietary
