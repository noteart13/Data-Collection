# PropertyMaster skipped-field source research (NSW)

Date: 2026-11-02

## Executive conclusion

The skipped fields can be enriched, but not all from open government data and not all with the same confidence.

1. **Open, authoritative enrichment is feasible now** for address and coordinates, explicit address level/floor, lot/plan/parcel identifiers and area, sale history, statutory property classification, zoning, height of buildings, floor-space ratio, minimum lot size, and some public strata-scheme metadata.
2. **Current listing attributes require an authorised listing feed or licensed property-data API**: bedrooms, bathrooms, car spaces, indoor features, headline/title, description, images, listing URL, listing status, listing option, current asking price, and exact advertised rent.
3. **Price and rental estimates are estimates, not observed facts.** Domain, PropTrack and Cotality offer commercial APIs/products. A local AVM is possible, but its values must be stored as `estimated` with model/version/as-of metadata, never as `verified`.
4. **Exact council and water bills are not suitable for unauthenticated bulk enrichment.** They are property/account notices generally limited to owners or authorised agents. Public tariffs or council rate formulas are not the same as a property's actual bill.
5. **The current repository contains legacy synthetic defaults and incomplete adapters.** These must remain outside the trusted enrichment path. In particular, `floorNo=1`, `propertyType=House`, `status=off_market`, `constructionYear=N/A`, static suburb zoning and generic water tariffs cannot be treated as property evidence.

## Field-by-field source matrix

| Payload field | Best available source | What can be written | Confidence/status | Main limitation |
|---|---|---|---|---|
| `bed`, `bath` | Domain Listings API; PropTrack Listings/Properties API; authorised agency CRM feed (REAXML); Cotality property data | Exact advertised/recorded count with listing/property ID and retrieval time | `verified` when exact address/property match | Portal scraping is blocked and brittle; APIs are licensed or quota-limited |
| `features.garage` | Same listing/property APIs and agency feed | Advertised car spaces/parking count | `verified` when exact match | “Garage” and “carspace” are not always semantically identical; preserve source field |
| `features.floorNo` | G-NAF `LEVEL_NUMBER` when explicitly populated; authorised listing; strata/building documents | Explicit level only | `verified` | Never infer from unit number; current G-NAF loader ignores `LEVEL_NUMBER` and hardcodes `1` |
| `features.indoorFeatures` | Listing API/agency feed; licensed property provider | Source-provided feature list | `verified` or `observed` | Marketing text can be stale; retain listing date and source |
| `constructionYear` | Licensed property provider; listing/agency feed; authorised Strata Hub role data; DA/certificate data where matched | Year built, occupation-certificate date, or earliest building approval as separate concepts | `verified` only for explicit source field; otherwise `derived` | Scheme registration, approval, occupation and actual completion are different dates; do not collapse silently |
| `expectedPrice` | Domain Price Estimate; PropTrack AVM; Cotality IntelliVal; local AVM | Estimate, range, confidence, valuation date and model/provider | `estimated` only | Not a professional valuation or observed price |
| `valuation.*` | Same AVM providers; local model | Low/high/point estimate with confidence and `asOf` | `estimated` | Coverage and licensing; model drift |
| `pricing.weeklyRent` | Current rental listing/agency feed for advertised rent; Domain/PropTrack rental AVM for estimate | Exact advertised rent or separately named estimated rent | `verified` for current listing; `estimated` for AVM | NSW Rental Bond data is postcode-level, not an exact-property source |
| `pricing.estimatedYield` | Derived from a source-backed rent estimate/observation and sale/AVM price | Yield plus exact formula and input provenance | `derived` | Must not be presented as observed; stale inputs distort result |
| `pricing.councilBill` | Owner/authorised-agent rate notice; council Section 603 certificate/workflow where appropriate | Actual notice amount and period | `verified`, restricted | No uniform open bulk endpoint; public rate formula cannot reproduce every actual notice reliably |
| `pricing.waterBillPeriod` and water bill values | Owner/authorised-agent utility notice/account | Actual charge and billing period | `verified`, restricted | Sydney Water account data is protected; generic tariffs are not property bills |
| `description`, `title` | Listing API or authorised agency feed | Listing headline and description | `observed`/`verified` for that listing version | Copyright/retention/API terms; mutable data |
| `images` | Listing API/agency feed or licensed provider | Prefer URLs and media metadata; retain only if licence permits | `observed` | User policy says do not download/add images; existing images should be preserved. API terms may restrict storage |
| `status`, `listingOption` | Current listing API/feed with update timestamp/webhook | Sale/rent/sold/leased/withdrawn/under-offer status and channel | `verified` for timestamped listing | Government sales data cannot prove current listing status |
| `realestate_url` | Listing API/feed | Canonical listing URL/SEO URL | `verified` | URL may expire; tie it to listing ID/provider |
| `strataDetails` | NSW Strata Hub public search/registered public view; Spatial NSW cadastre; NSW PSI strata lot; NSW LRS products if licensed | Plan/lot, scheme registration date, lot use/count, building/storey counts where publicly accessible | `verified` with access-level metadata | Financials, occupation date and many details are role-restricted; no confirmed public bulk API |
| `planning`, top-level `zoning` | NSW Planning Portal EPI spatial services queried by property coordinates | Zone, LEP/EPI, HOB, FSR, minimum lot size and source layer/version | `verified` | Must be point-in-polygon on official layers; suburb-wide static maps are unsafe |
| address fields, `coordinates` | G-NAF address detail + default geocode | Structured address, G-NAF PID, lat/lon, geocode reliability | `verified` | Preserve geocode reliability and do not substitute portal coordinates without source comparison |
| vectors | Locally generated from source-backed fields | Embedding vector, model/version, input hash and generation date | `derived` | Never copy an unrelated vector or regenerate from synthetic/default text |

## Authoritative open sources

### G-NAF

The official G-NAF schema contains unit/flat fields, explicit `LEVEL_NUMBER`, lot number, legal parcel ID, address identifiers and default longitude/latitude. This supports address and geocode enrichment, and it supports `features.floorNo` only when `LEVEL_NUMBER` is actually populated. A unit number is not a floor number.

Source: https://docs.geoscape.com.au/projects/gnaf_desc/en/stable/appendix_c.html

### NSW Valuer General PSI

The local PSI adapter already reads purchase price, contract/settlement date and official attributes such as area, area source/type, zoning, nature of property, primary purpose, strata lot and unit number. These are valuable for sale history and legal/property classification. PSI area must preserve its source semantics; parcel or strata-record area must not automatically become internal apartment floor area.

Source: https://www.valuergeneral.nsw.gov.au/design/bulk_psi_content/bulk_psi

### NSW Planning Portal spatial data

The EPI Land Zoning dataset is an official weekly-updated spatial dataset and exposes WMS, WFS, ArcGIS REST and JSON. Equivalent official layers exist for Floor Space Ratio, Height of Buildings and Minimum Lot Size. Query the layers at the verified G-NAF coordinate and store the intersecting feature attributes plus service/layer/version.

Sources:

- https://www.planningportal.nsw.gov.au/opendata/dataset/environment-planning-instrument-local-environmental-plan-land-zoning
- https://www.planningportal.nsw.gov.au/opendata/dataset/environmental-planning-instrument-floor-space-ratio

### Spatial NSW cadastre

The public FeatureServer exposes parcel/lot polygons and fields including plan number/label, lot number, stratum indicators, plan lot area and units. This is suitable for cadastral identity, parcel geometry and plan/lot area. It is not automatically the habitable area of a unit.

Source: https://portal.spatial.nsw.gov.au/server/rest/services/NSW_Land_Parcel_Property_Theme/FeatureServer/layers

### NSW Strata Hub

Public search exposes scheme address, number and use of lots, registration date, map location and last reporting date. Registered members of the general public can see building count and above-ground storeys for reported class 2 buildings. Occupation-certificate dates, scheme financials and other details are restricted to scheme roles or government.

Source: https://www.nsw.gov.au/housing-and-construction/strata/strata-hub/strata-hub-security-and-privacy

### NSW Rental Bond data

The open dataset contains postcode, dwelling type, weekly rent and bedroom count. It is useful for market distributions, validation and AVM features. It must be stored under a market-statistics namespace; it cannot verify a particular property's exact rent or bedrooms.

Source: https://data.nsw.gov.au/data/dataset/rental-bond-lodgement

## Licensed and commercial sources

### Domain Developer API

Domain's residential listing search response includes bedrooms, bathrooms, car spaces, features, land/building area, headline, description summary, media, displayed price, address, coordinates and listing slug. Listing details add full description, media and canonical SEO URL. Domain also documents property price-estimate and rental-estimate endpoints.

Access is not an unrestricted bulk-data licence: a corporate account and suitable plan/quota are required, available properties vary by data-access level, and Domain warns about storing mutable listing data. Integration should use API credentials, respect attribution/retention terms, keep listing IDs and refresh or expire mutable fields.

Sources:

- https://developer.domain.com.au/docs/v1/apis/pkg_property/references/listings_detailedresidentialsearch/
- https://developer.domain.com.au/docs/v1/apis/pkg_agents_listings/references/listings_get/
- https://developer.domain.com.au/docs/latest/apis/pkg_price_estimation/
- https://developer.domain.com.au/docs/latest/apis/pkg_rental_avm/
- https://developer.domain.com.au/docs/v2/support/faq/

### PropTrack / realestate.com.au

PropTrack's official Listings API material states that it provides current and historic listings, attributes/features, description, images and statuses including current, sold and leased. PropTrack also offers property attributes, transactions and sale/rental AVMs. This is the supported route to REA data; unauthorised web scraping remains anti-bot-sensitive and should not be the foundation of a statewide enrichment job.

Source: https://www.proptrack.com.au/wp-content/uploads/2022/12/proptrack-listingsapiguide.pdf

### Cotality (formerly CoreLogic RP Data)

Cotality advertises property-level attributes, construction attributes, market values, digital assets, custom API and bulk delivery. It is a commercial option for filling broad property attributes and valuations. Its own disclaimers say building-consent coverage is incomplete and AVMs/modelled values are estimates, so provenance and status still matter.

Sources:

- https://www.cotality.com/au/our-data
- https://www.cotality.com/au/legal/disclaimers

### Agency CRM / REAXML

If the user controls or partners with agencies, an authorised CRM feed is often the most direct source for current listing fields and avoids scraping. It can cover bedrooms, bathrooms, car spaces, features, status/channel, headline, description, media and price/rent. The feed should carry agency/listing identifiers, update timestamps and withdrawal/deletion events.

## GitHub findings

GitHub is useful for integration examples, not as an authority for property facts.

- `minus34/gnaf-loader` is a mature community implementation for loading open G-NAF and administrative boundaries: https://github.com/minus34/gnaf-loader
- `fsingletonthorn/domain-public-api-client` demonstrates a generated Python client for Domain's published OpenAPI document: https://github.com/fsingletonthorn/domain-public-api-client
- `grosstoad/proptrack_md` mirrors/document-indexes PropTrack API surfaces, including property attributes, listings, transactions and AVMs: https://github.com/grosstoad/proptrack_md
- Scrapers that call undocumented REA/Domain endpoints or bypass bot controls may demonstrate parsing techniques, but they are not reliable, licensed or authoritative sources for production enrichment. They should not be promoted into the trusted evidence path.

## Repository gap audit

### Confirmed gaps and unsafe defaults

- `app/operations/portal_profile_hydrator.py`: `get_planning_controls()` currently returns `{}`. `get_statutory_council_bill()` and `get_statutory_water_bill()` return empty strings. The file also contains static suburb planning schedules and generic tariffs; these are not property-specific evidence.
- `app/operations/cadastral_spatial.py`: constructors default zoning to `R2`. A default zone must never be written to a property.
- `stream_gnaf_to_qdrant.py`: consumes `FLAT_NUMBER`, address PID and coordinates but does not consume `LEVEL_NUMBER`; it writes legacy defaults including `floorNo=1`, `propertyType=House`, `constructionYear=N/A`, `status=off_market` and `listingOption=off_market`.
- Legacy crawler modules also contain `floorNo=1`, `constructionYear=N/A`, `waterBillPeriod=monthly` and similar placeholders. These values are not evidence.
- `bulk_enrich_master.py` has a useful field-level evidence gate and PSI mapping, but the official-only path currently removes `planning` and `zoning`, so the open NSW spatial layers are not yet integrated.
- Any internal statistical valuation constants must be treated as an explicit model output. References to market benchmarks do not verify a particular property's value.

### Recommended adapters

1. **`GnafEvidenceAdapter` extension**
   - Preserve `LEVEL_TYPE_CODE`, `LEVEL_NUMBER`, `LEGAL_PARCEL_ID`, `GNAF_PROPERTY_PID`, geocode type and reliability.
   - Set `features.floorNo` only from a non-null explicit level field.
   - Scrub legacy structural/listing defaults from records unless separately evidenced.

2. **`NswSpatialPlanningAdapter`**
   - Use coordinates for point-in-polygon queries against official zoning, HOB, FSR and minimum-lot-size layers.
   - Query Spatial NSW lot/property layers for lot/plan, stratum and parcel area.
   - Store feature ID, layer URL, layer update/version, retrieval time and response hash.
   - Never fall back to suburb-wide static zoning.

3. **`NswStrataPublicAdapter`**
   - Use only documented public/registered-public access and comply with authentication/captcha/terms.
   - Cache scheme-level data, then link units by exact scheme address and plan/lot identifiers.
   - Do not attempt to access role-restricted financial or certificate data without user authorisation.

4. **`LicensedListingProviderAdapter`**
   - Prefer Domain, PropTrack or an authorised agency feed over direct portal scraping.
   - Require exact address/property-ID match and record the listing ID, provider, source URL, update time, channel and status.
   - Handle deletions/withdrawals and TTLs; do not assume old listing data is current.
   - Follow provider storage, attribution and media licence terms.

5. **`MarketEstimateAdapter`**
   - Put NSW Rental Bond aggregates in `marketStats`, not exact-property fields.
   - Put AVM/rental estimates in `valuation` or explicit estimate fields with provider/model/as-of/confidence.
   - Compute yield only from provenance-backed inputs and store the formula/input evidence.

6. **`OwnerDocumentAdapter`**
   - Accept user-authorised council/water notices and relevant strata/occupation documents.
   - Record document date, billing period and extracted source location; protect account/personal data.

7. **`VectorDerivationAdapter`**
   - Generate embeddings only from accepted source-backed fields.
   - Persist embedding model, dimensions, input-field list, input hash and generated time.
   - Recompute only when the accepted input hash changes.

## Proposed evidence policy

- `verified`: exact-address/property match from an authoritative official dataset or authorised provider field.
- `observed`: exact listing/document value that may be mutable or marketing-authored.
- `estimated`: AVM/statistical model output with provider/model/as-of/confidence.
- `derived`: deterministic calculation from other provenance-backed fields.
- Missing, inaccessible or ambiguous values remain `null`; no placeholder/default is allowed to satisfy completeness.
- Every accepted field should have `sourceName`, `sourceUrl`, `sourceRecordId`, `sourceField`, `retrievedAt`, `method`, `status`, `valueHash`, and where applicable `datasetVersion`/`listingUpdatedAt`/`modelVersion`.

## Recommended rollout order

1. Remove/scrub legacy defaults and extend the G-NAF loader for explicit level and parcel identifiers.
2. Implement NSW Planning + Spatial NSW adapters. This is the highest-value free and source-verifiable expansion.
3. Add Strata Hub public metadata within documented access rules.
4. Obtain and pilot one licensed listing/property API on 100 exact-address NSW records; measure exact-match rate, field coverage, refresh cost and storage restrictions.
5. Add AVM/rental estimates only after the schema clearly separates estimates from facts.
6. Support council/water values through authorised document ingestion, not bulk guessing.
7. Run a dry-run audit showing proposed field changes and provenance before any Qdrant write.

## Practical answer

Yes, the skipped fields can be enriched substantially. The free official route can safely unlock planning/zoning, cadastre, strata public metadata, explicit floor where present, and richer sale/property classification. Bed/bath/garage/features/listing/title/description/status/URL and exact advertised price/rent need a licensed listing source or authorised agency feed. Construction year has partial coverage and must retain its date semantics. AVM and yield fields must remain estimates/derived. Exact council and water bills require owner/agent-authorised documents and should otherwise remain null.
