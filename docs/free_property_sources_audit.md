# Free property sources audit

Audit date: 2026-11-03 (Asia/Bangkok)

Scope: primary-source verification for NSW PropertyMaster enrichment. "Exact-address feasible" means resolving or returning a supplied full address with enough identity evidence to avoid a suburb/postcode false match. A code licence does not grant rights to scrape or reuse third-party portal data.

## Verdict

None of the candidates is simultaneously free, current, exact-address capable, source-verifiable, and permitted by the target portal's published terms.

| Candidate | Existence / licence | Last verified activity | Exact-address and operational verdict |
|---|---|---|---|
| `riverpaper/realestatebot` (formerly `silvncr`) | Exists; MIT code; PyPI 0.0.2 | 2024-10-29 dependency merge; scraper last changed 2024-06-17 | No address input; postcode-wide sold search. Reject: unsafe null-to-zero coercion, broken state handling, unverified current runtime, REA terms prohibit scraping. |
| `mpbagot/rustscape` | Repository exists; component licences only | 2026-01-10 | Advertised API is not implemented. No server, routes, schema, G-NAF loader, or deployment files. Reject now. |
| `thehooklab/nsw-property` | Public/ungated parquet; card says MIT, but upstream rights/provenance are not shown per row | 2026-01-15 | Sales is not a reliable exact-address feature source; rentals/enriched are not exact-address datasets. Aggregate research only. |
| `geshan/domain-scraper` | Exists; no licence file; `package.json` declares ISC | 2026-10-21 Dependabot; logic last changed 2019-11-13 | Requires a known Domain listing ID; no address resolver. Local pilot failed immediately with HTTP 403. Reject. |
| `vanhiepaus06/RealEstateScraper` | Exists; no licence file | 2024-06-21 | Postcode result crawler, not exact-address lookup. Notebook demo only; reject. |
| ScrapFly `realestatecom-scraper` | Exists; NPOSL-3.0 non-profit licence | scraper 2026-10-23; docs 2026-10-28 | Strong only with an already-known REA property URL/ID. Requires API key/credits and conflicts with REA terms. Not a free source. |

## 1. `realestatebot` Python scraper

The PyPI package points to `silvncr/realestatebot`, which now redirects to [`riverpaper/realestatebot`](https://github.com/riverpaper/realestatebot). [`realestatebot` 0.0.2 on PyPI](https://pypi.org/project/realestatebot/) was uploaded 2024-06-18, requires Python 3.12+, and declares MIT. The repository's latest commit is a [2024-10-29 dependency merge](https://github.com/riverpaper/realestatebot/commit/afaee00e0d275f4301b749e318b56b239fb8661a); scraper code last changed [2024-06-17](https://github.com/riverpaper/realestatebot/commit/eedf5ca786564ca918cd5fa469cc13e4d9039f11).

The [README](https://github.com/riverpaper/realestatebot/blob/main/README.md) calls it untested/personal-use software. The [single implementation file](https://github.com/riverpaper/realestatebot/blob/main/realestatebot/__init__.py) performs broad sold searches by postcode and outputs `address`, `postcode`, bedrooms, bathrooms, parking, coordinates, distance, and price. It cannot query a supplied address. Missing room/parking values and unparseable prices become `0`; the loop also hard-codes Queensland despite accepting a state argument. This is neither exact-address nor fail-closed.

An unrelated two-commit [`Josh3005/realestatebot`](https://github.com/Josh3005/realestatebot) also exists, with no release or licence; it is not the PyPI-backed package.

REA's current [Terms of Use](https://about.realestate.com.au/terms-use/) expressly prohibit automated access, retrieval, scraping, or indexing. MIT covers the wrapper code, not REA data rights.

## 2. Rustscape

The exact repository is [`mpbagot/rustscape`](https://github.com/mpbagot/rustscape), described as a free-software, self-hosted Geoscape replacement backed by G-NAF. Latest commit: [2026-01-10](https://github.com/mpbagot/rustscape/commit/ea2dc8461aa859092039f51d8e5491e92cb03c2d).

The complete tree currently contains a fuzzy-match library, an [`gnaf/README.md`](https://github.com/mpbagot/rustscape/blob/master/gnaf/README.md) consisting of one sentence with no loader/scripts, and a [`rustscape/README.md`](https://github.com/mpbagot/rustscape/blob/master/rustscape/README.md) whose body is `TODO` plus licence text. There is no API server source, route/OpenAPI definition, database schema, ingestion implementation, image, deployment configuration, or release.

`fuzzbunny-rs` and `gnaf` say MIT; the proposed server says AGPLv3-or-later with an [internal/indirect network-use exception](https://github.com/mpbagot/rustscape/blob/master/rustscape/LICENSE-EXCEPTION). GitHub reports no repository-level SPDX licence. G-NAF could support address identity in principle, but it does not supply beds, baths, sales, or rental listing attributes. With no runnable product, exact-address matching is currently infeasible.

## 3. Hugging Face `thehooklab/nsw-property`

The live [dataset card](https://huggingface.co/datasets/thehooklab/nsw-property) and [file tree](https://huggingface.co/datasets/thehooklab/nsw-property/tree/main) show public, ungated parquet files, last modified 2026-01-15. The card declares MIT and claims:

| Config | Rows | Advertised content |
|---|---:|---|
| `sales` | 3,493,361 | address components, sale date/price, `area`, `property_type`, coordinates, `num_bed`, `num_bath`, `num_parking`, `land_area` |
| `enriched` | 83,548 | market/demographic/transport analytics |
| `rental` | 48,158 | lodgement date, postcode, dwelling type, bedrooms, weekly rent, bond, annual rent |
| **Total** | **3,625,067** | card claim |

The card says "official NSW government records and verified sources" but gives no upstream dataset URLs, transformation code, per-field provenance, or upstream-licence analysis. MIT metadata therefore does not establish rights to every value, especially portal-like features and derived analytics.

### Full-file measurements

Measured locally on 2026-11-03 from `scratch/nsw_property_sales_master.parquet`, `scratch/nsw_property_master_enriched.parquet`, and `scratch/nsw_rental_market_master.parquet`:

- Sales contains exactly 3,493,361 rows.
- `num_bed`, `num_bath`, `num_parking`, and `land_area` each have exactly 10,725 non-null rows: **0.3070%**.
- `source` is blank on 3,482,636 rows and `domain` on exactly 10,725. All feature coverage is concentrated in that tiny Domain-derived subset.
- Feature rows have blank `house_number`/`street_name`, often null postcode, and repeated suburb-centroid coordinates; they cannot be safely exact-address joined.
- `602/8 Duntroon Ave` has zero exact-address and zero street matches.
- One `VACANT LAND` row reports 3 beds, 1 bath, and 2 parking spaces.
- Enriched (83,548 rows) lacks exact address and rooms. Rental (48,158 rows) lacks exact address and is postcode/dwelling-level bond-market data.

The [sales viewer](https://huggingface.co/datasets/thehooklab/nsw-property/viewer/sales/train) also shows the schema and sparse feature/coordinate values. The record-count claim is true; the implied exact-property feature utility is not. Do not use it for PropertyMaster room, parking, property-type, or land-area writes.

## 4. `geshan/domain-scraper`

[`geshan/domain-scraper`](https://github.com/geshan/domain-scraper) has no licence file, although `package.json` declares `ISC`. Its latest change is a [2026-10-21 Dependabot merge](https://github.com/geshan/domain-scraper/commit/25c684890bf50db397e63c6d9f4154064a8fd8f6); logic last changed [2019-11-13](https://github.com/geshan/domain-scraper/commit/d54c0080f96e4e7309b00faace2aa866bb34d5e9).

The [README](https://github.com/geshan/domain-scraper#simple-domaincomau-scraper) calls it hacky, says it may break randomly, supports rentals only, and requires registration/login plus manually extracted shortlist IDs. The [source](https://github.com/geshan/domain-scraper/blob/master/index.js) visits known IDs and writes address, beds, baths, parking, rent, bond, availability, next inspection, headline, and truncated description. It ships six IDs (one duplicate), not a dataset. It cannot resolve an address.

### Local pilot

The repository was cloned to `scratch/vendor/domain-scraper`, installed with its lockfile using `npm ci --ignore-scripts`, and executed unchanged with its five unique sample listing IDs. It failed on the first request (`https://www.domain.com.au/11444889`) with **HTTP 403 Forbidden**. The response server was `AkamaiGHost` and the body said `Access Denied`; no CSV or property record was produced. Dependency installation also reported one high-severity and one low-severity vulnerability. This directly confirms that the wrapper does not solve the current network/anti-bot block.

Domain's [Conditions of Use](https://www.domain.com.au/group/conditions-of-use/) prohibit direct/indirect scraping or indexing and use for building a property-information database or derivative product. Public source without a licence is not reusable software permission.

## 5. `vanhiepaus06/RealEstateScraper`

[`vanhiepaus06/RealEstateScraper`](https://github.com/vanhiepaus06/RealEstateScraper) has no licence and only three commits; latest: [2024-06-21](https://github.com/vanhiepaus06/RealEstateScraper/commit/c426719b33800b13674da395cc5e43c535c0f5d5).

Its [notebook](https://github.com/vanhiepaus06/RealEstateScraper/blob/main/Realestate.ipynb) drives Selenium over Domain sold-house searches by postcode, up to 50 pages each. It emits eight columns: sold date, price, address lines 1/2, bedrooms, bathrooms, parking, and square metres; missing values become `N/A`. Embedded old output displays 264,280 merged rows, but no CSV dataset is published. The configured list contains 347 entries (343 unique postcodes), and the README estimates about 20 hours.

There is no exact-address input, resolver, test suite, provenance, or evidence the 2024 selectors still work. The live workflow is also prohibited by [Domain's conditions](https://www.domain.com.au/group/conditions-of-use/).

## 6. ScrapFly realestate.com.au scraper

The current source is [`scrapfly-scrapers/realestatecom-scraper`](https://github.com/scrapfly/scrapfly-scrapers/tree/main/realestatecom-scraper). The scraper changed [2026-10-23](https://github.com/scrapfly/scrapfly-scrapers/commit/214483bfe575204547c35bb4703547e4ce498d67) and generated docs [2026-10-28](https://github.com/scrapfly/scrapfly-scrapers/commit/6451290400a0ff90965e58ae1eab36f327b91282). The repository uses [Non-Profit Open Software License 3.0](https://github.com/scrapfly/scrapfly-scrapers/blob/main/LICENSE), not a permissive commercial licence, and labels examples educational/non-production.

The [implementation](https://github.com/scrapfly/scrapfly-scrapers/blob/main/realestatecom-scraper/realestate.py) accepts REA search URLs or property URLs, reads `window.ArgonautExchange`, and returns `id`, property type/description/link/address/sizes/features, media, company, listers, and auction. Fixtures exist for [property pages](https://github.com/scrapfly/scrapfly-scrapers/blob/main/realestatecom-scraper/results/properties.json) and [search pages](https://github.com/scrapfly/scrapfly-scrapers/blob/main/realestatecom-scraper/results/search.json).

It requires `SCRAPFLY_KEY`, browser rendering, proxying, and anti-bot unblocker credits. [Pricing](https://scrapfly.io/pricing) advertises 1,000 signup credits; protected-site/browser/residential settings consume multiple credits and ongoing plans are paid. Exact identity is strong only when a property URL/ID is already known; it does not resolve arbitrary text addresses. REA's [terms](https://about.realestate.com.au/terms-use/) prohibit the automated scraping/indexing it performs.

## Disposition

1. Do not integrate or bulk-run these portal scrapers.
2. Do not populate exact-property fields from the Hugging Face feature subset.
3. Treat Rustscape as a placeholder until a runnable tagged release exists.
4. Use an implemented G-NAF resolver only for address identity. A G-NAF match is not evidence for beds, baths, property type, sales, or rent.
5. Require written portal access rights, exact address/URL evidence, per-field provenance, sparse writes, null for unavailable values, and a no-write pilot before any Qdrant change.
