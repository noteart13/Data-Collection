from types import SimpleNamespace
import csv
import io
import inspect
import zipfile

from app.operations.property_evidence import (
    annotate_portal_result,
    evidence_matches_value,
    is_accepted_evidence,
    make_evidence,
    quarantine_unverified_legacy,
)
import bulk_enrich_master
from bulk_enrich_master import _process_point_chunk_trusted
from app.operations.nsw_psi_adapter import NswPsiSalesAdapter, NswPsiSqliteAdapter


def test_bulk_enrichment_does_not_read_another_property_collection_by_default():
    parameter = inspect.signature(bulk_enrich_master.run_bulk_enrichment).parameters[
        "skip_property_source"
    ]

    assert parameter.default is True


class DummyHydrator:
    def __init__(self, result):
        self.result = result

    def fetch_profile(self, **kwargs):
        return self.result


class DummyPropertySource:
    def __init__(self, result):
        self.result = result

    def lookup(self, street, suburb, state, postcode):
        return self.result


class DummyBrowserAdapter:
    def __init__(self, result):
        self.result = result
        self.lookups = []

    def lookup(self, **kwargs):
        self.lookups.append(kwargs)
        return self.result


def test_evidence_requires_public_url_exact_match_and_hash():
    accepted = make_evidence(
        value=2,
        source_name="PUBLIC_SOURCE",
        source_url="https://example.gov.au/property/1",
        source_field="bedrooms",
        method="observed",
        verification_status="verified",
    )
    assert is_accepted_evidence(accepted)
    assert evidence_matches_value(2, accepted)
    assert not evidence_matches_value(3, accepted)

    rejected = dict(accepted, addressMatch="suburb_only")
    assert not is_accepted_evidence(rejected)


def test_legacy_value_without_field_evidence_is_quarantined():
    legacy = quarantine_unverified_legacy({"bed": 4, "expectedPrice": 900000}, {})
    assert legacy["bed"]["value"] == 4
    assert legacy["expectedPrice"]["value"] == 900000


def test_trusted_pipeline_leaves_unsupported_values_for_separate_cleanup():
    point = SimpleNamespace(
        id=1,
        payload={
            "street": "10 Main Street",
            "suburb": "Lane Cove North",
            "state": "NSW",
            "postcode": "2066",
            "bed": 9,
            "bath": 7,
            "expectedPrice": 9999999,
            "constructionYear": "1901",
            "features": {"garage": 5},
        },
    )
    engine = SimpleNamespace(portal_hydrator=DummyHydrator({}))

    updates, count = _process_point_chunk_trusted([point], engine, False, "2026-11-01T00:00:00+00:00")

    assert count == 0
    assert updates == []


def test_portal_miss_does_not_emit_room_nulls_in_payload_patch():
    point = SimpleNamespace(
        id=11,
        payload={
            "street": "3 Napier Close",
            "suburb": "Wantirna",
            "state": "VIC",
            "postcode": "3152",
            "bed": None,
            "bath": None,
            "features": {},
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=DummyHydrator({"_fetch_status": "blocked"}),
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted(
        [point], engine, False, "2026-11-01T00:00:00+00:00"
    )

    assert updates == []


def test_source_miss_does_not_emit_unrelated_gnaf_or_quarantine_patch():
    point = SimpleNamespace(
        id=14,
        payload={
            "street": "1 Unknown Street",
            "suburb": "Nowhere",
            "state": "NSW",
            "postcode": "2000",
            "gnaf_pid": "GANSW000000001",
            "source": "G_NAF",
            "features": {"garage": 1},
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=None,
        property_source_adapter=DummyPropertySource({}),
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted(
        [point], engine, False, "2026-11-01T00:00:00+00:00"
    )

    assert updates == []


def test_verified_room_enrichment_does_not_patch_unrelated_payload_fields():
    portal_result = annotate_portal_result(
        {
            "_addressSlug": "3-napier-close-wantirna-vic-3152",
            "bed": 4,
            "bath": 2,
        },
        source_name="PUBLIC_PROPERTY_PROFILE",
        source_url="https://example.com/property/3-napier-close-wantirna-vic-3152",
        retrieved_at="2026-11-01T00:00:00+00:00",
    )
    point = SimpleNamespace(
        id=12,
        payload={
            "street": "3 Napier Close",
            "suburb": "Wantirna",
            "state": "VIC",
            "postcode": "3152",
            "title": "Existing title",
            "expectedPrice": 1095000,
            "status": "sold",
            "images": [{"url": "https://legacy.example/image.jpg"}],
            "features": {"garage": 2, "floorNo": 1},
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=DummyHydrator(portal_result),
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted(
        [point], engine, False, "2026-11-01T00:00:00+00:00"
    )
    patch = updates[0]["payload"]

    assert not {"title", "expectedPrice", "status", "images", "features"} & set(patch)


def test_exact_scraped_source_enriches_rooms_when_live_portal_is_unavailable():
    source_result = annotate_portal_result(
        {
            "_addressSlug": "3-napier-close-wantirna-vic-3152",
            "bed": 4,
            "bath": 2,
        },
        source_name="SCRAPED_PROPERTY_COM_AU",
        source_url=(
            "https://www.property.com.au/vic/wantirna-3152/"
            "napier-close/3-pid-3735391/"
        ),
        retrieved_at="2026-11-01T00:00:00+00:00",
    )
    point = SimpleNamespace(
        id=13,
        payload={
            "street": "3 Napier Close",
            "suburb": "Wantirna",
            "state": "VIC",
            "postcode": "3152",
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=None,
        property_source_adapter=DummyPropertySource(source_result),
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted(
        [point], engine, False, "2026-11-01T00:00:00+00:00"
    )

    assert (updates[0]["payload"]["bed"], updates[0]["payload"]["bath"]) == (4, 2)


def test_browser_fallback_enriches_missing_rooms_without_a_source_collection():
    browser_result = annotate_portal_result(
        {
            "_addressSlug": "3-napier-close-wantirna-vic-3152",
            "bed": 4,
            "bath": 2,
        },
        source_name="PROPERTY_COM_AU_BROWSER",
        source_url=(
            "https://www.property.com.au/vic/wantirna-3152/"
            "napier-close/3-pid-3735391/"
        ),
        retrieved_at="2026-11-01T00:00:00+00:00",
    )
    browser = DummyBrowserAdapter(browser_result)
    point = SimpleNamespace(
        id=15,
        payload={
            "street": "3 Napier Close",
            "suburb": "Wantirna",
            "state": "VIC",
            "postcode": "3152",
            "realestate_url": (
                "https://www.property.com.au/vic/wantirna-3152/"
                "napier-close/3-pid-3735391/"
            ),
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=DummyHydrator({"_fetch_status": "blocked"}),
        property_source_adapter=None,
        property_browser_adapter=browser,
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted(
        [point], engine, False, "2026-11-01T00:00:00+00:00"
    )

    assert (updates[0]["payload"]["bed"], updates[0]["payload"]["bath"]) == (4, 2)
    assert browser.lookups[0]["known_url"] == point.payload["realestate_url"]


def test_trusted_pipeline_accepts_exact_portal_evidence_and_typed_area():
    portal_result = annotate_portal_result(
        {
            "_addressSlug": "10-main-street-lane-cove-north-nsw-2066",
            "_areaType": "landArea",
            "bed": 3,
            "bath": 2,
            "totalArea": 501,
            "expectedPrice": 1500000,
            "valuationType": "Portal_AVM",
        },
        source_name="PUBLIC_PROPERTY_PROFILE",
        source_url="https://example.com/property/10-main-street-lane-cove-north-nsw-2066",
        retrieved_at="2026-11-01T00:00:00+00:00",
    )
    point = SimpleNamespace(
        id=2,
        payload={
            "street": "10 Main Street",
            "suburb": "Lane Cove North",
            "state": "NSW",
            "postcode": "2066",
            "features": {},
        },
    )
    engine = SimpleNamespace(portal_hydrator=DummyHydrator(portal_result))

    updates, _ = _process_point_chunk_trusted([point], engine, False, "2026-11-01T00:00:00+00:00")
    payload = updates[0]["payload"]

    assert payload["bed"] == 3
    assert payload["bath"] == 2
    assert payload["area"]["totalArea"] == 501
    assert payload["expectedPrice"] == 1500000
    assert payload["fieldProvenance"]["expectedPrice"]["verificationStatus"] == "estimated"
    assert payload["provenance"]["verificationStatus"] == "partially_verified"


def test_qdrant_batch_write_waits_for_acknowledgement(monkeypatch):
    class Operation:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_models = SimpleNamespace(
        SetPayloadOperation=Operation,
        SetPayload=Operation,
        DeletePayloadOperation=Operation,
        DeletePayload=Operation,
    )

    class Client:
        def __init__(self):
            self.wait = None

        def batch_update_points(self, **kwargs):
            self.wait = kwargs["wait"]
            return "ack"

    monkeypatch.setattr(bulk_enrich_master, "models", fake_models)
    client = Client()
    result = bulk_enrich_master.robust_update_payload(
        client,
        "PropertyMaster",
        [{"id": 1, "payload": {"bed": 2}}],
    )

    assert result == "ack"
    assert client.wait is True


def test_nsw_psi_adapter_exact_address_enriches_official_history(tmp_path):
    archive_path = tmp_path / "psi.zip"
    buffer = io.StringIO()
    fieldnames = [
        "Property locality", "Property house number", "Property street name",
        "Property unit number", "Purchase price", "Contract date",
        "Settlement date", "Property ID", "Area (m2)", "Area (source)",
        "Area type (source)", "Zoning", "Nature of property (description)",
        "Primary purpose (normalised)", "Strata lot number",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerow({
        "Property locality": "LANE COVE NORTH",
        "Property house number": "8-14",
        "Property street name": "MINDARIE STREET",
        "Property unit number": "6",
        "Purchase price": "2090000",
        "Contract date": "27/05/2021",
        "Settlement date": "01/07/2021",
        "Property ID": "12345",
        "Area (m2)": "304",
        "Area (source)": "Valuation roll",
        "Area type (source)": "Land area",
        "Zoning": "R4",
        "Nature of property (description)": "Strata unit",
        "Primary purpose (normalised)": "Residential",
        "Strata lot number": "6",
    })
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("nsw-property-sales-data-updated20260914.csv", buffer.getvalue())

    adapter = NswPsiSalesAdapter(archive_path, "Lane Cove North")
    sales = adapter.lookup("6/8 Mindarie Street")
    assert len(sales) == 1
    assert sales[0]["price"] == 2090000
    assert sales[0]["areaSqm"] == 304.0
    assert sales[0]["areaTypeSource"] == "Land area"
    assert sales[0]["zoning"] == "R4"
    assert sales[0]["propertyTypeSource"] == "Strata unit"
    assert sales[0]["strataLotNumber"] == "6"
    assert adapter.lookup("7/8 Mindarie Street") == []

    point = SimpleNamespace(
        id=3,
        payload={
            "street": "6/8 Mindarie Street",
            "suburb": "Lane Cove North",
            "state": "NSW",
            "postcode": "2066",
            "features": {},
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=None,
        official_sales_adapter=adapter,
        portal_fields={"bed", "bath"},
    )
    updates, enriched = _process_point_chunk_trusted([point], engine, False, "2026-11-01T00:00:00+00:00")
    payload = updates[0]["payload"]

    assert enriched == 1
    assert payload["historysale"][0]["soldPrice"] == 2090000
    assert "status" not in payload
    assert payload["fieldProvenance"]["historysale"]["sourceRecordId"] == "12345"
    assert payload["fieldProvenance"]["historysale"]["datasetVersion"] == "20260914"
    assert payload["propertyType"] == "Strata unit"
    assert payload["fieldProvenance"]["propertyType"]["sourceName"] == "NSW_VALUER_GENERAL_PSI"
    assert payload["officialPropertyAttributes"] == {
        "areaSqm": 304.0,
        "areaSource": "Valuation roll",
        "areaTypeSource": "Land area",
        "zoning": "R4",
        "propertyTypeSource": "Strata unit",
        "primaryPurpose": "Residential",
        "strataLotNumber": "6",
        "unitNumber": "6",
    }
    assert "area" not in payload

    sqlite_adapter = NswPsiSqliteAdapter(archive_path, tmp_path / "psi.db")
    sqlite_sales = sqlite_adapter.lookup("6/8 Mindarie Street", "Lane Cove North")
    assert len(sqlite_sales) == 1
    assert sqlite_sales[0]["sourceRecordId"] == "12345"
    assert sqlite_sales[0]["areaSqm"] == 304.0
    assert sqlite_sales[0]["propertyTypeSource"] == "Strata unit"


def test_statewide_official_mode_skips_unmatched_payloads():
    point = SimpleNamespace(
        id=4,
        payload={
            "street": "1 Unknown Street",
            "suburb": "Nowhere",
            "state": "NSW",
            "postcode": "2000",
            "bed": 8,
        },
    )
    adapter = SimpleNamespace(lookup=lambda street, suburb: [])
    engine = SimpleNamespace(
        portal_hydrator=None,
        official_sales_adapter=adapter,
        official_matches_only=True,
    )

    updates, enriched = _process_point_chunk_trusted([point], engine, True, "2026-11-01T00:00:00+00:00")

    assert updates == []
    assert enriched == 0


def test_portal_policy_accepts_only_bed_bath_and_drops_images():
    portal_result = annotate_portal_result(
        {
            "_addressSlug": "21-parkwood-close-castle-hill-nsw-2154",
            "bed": 4,
            "bath": 2,
            "expectedPrice": 1800000,
            "images": [{"url": "https://images.example/property.jpg"}],
        },
        source_name="PUBLIC_PROPERTY_PROFILE",
        source_url="https://example.com/property/21-parkwood-close-castle-hill-nsw-2154",
        retrieved_at="2026-11-01T00:00:00+00:00",
    )
    point = SimpleNamespace(
        id=5,
        payload={
            "street": "21 Parkwood Close",
            "suburb": "Castle Hill",
            "state": "NSW",
            "postcode": "2154",
            "gnaf_pid": "GANSW704377305",
            "images": [{"url": "https://legacy.example/image.jpg"}],
            "features": {},
        },
    )
    engine = SimpleNamespace(
        portal_hydrator=DummyHydrator(portal_result),
        portal_fields=frozenset({"bed", "bath"}),
        no_images=True,
    )

    updates, _ = _process_point_chunk_trusted([point], engine, False, "2026-11-01T00:00:00+00:00")
    payload = updates[0]["payload"]

    assert payload["bed"] == 4
    assert payload["bath"] == 2
    assert "expectedPrice" not in payload
    assert "images" not in payload
    assert "images" not in payload["fieldProvenance"]
    assert "legacyUnverified" not in payload
    assert payload["realestate_url"] == "https://example.com/property/21-parkwood-close-castle-hill-nsw-2154"
    assert payload["fieldProvenance"]["realestate_url"]["sourceUrl"] == payload["realestate_url"]
