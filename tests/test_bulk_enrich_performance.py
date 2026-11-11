from types import SimpleNamespace

import bulk_enrich_master


def test_process_batch_uses_requested_workers_for_25_point_batch(monkeypatch):
    calls = []

    def fake_process_chunk(points, engine, force_update, now_iso, progress=False):
        calls.append(
            {
                "point_ids": [point.id for point in points],
                "preserve_payload_shape": engine.preserve_payload_shape,
            }
        )
        return [], 0

    monkeypatch.setattr(
        bulk_enrich_master,
        "_process_point_chunk_trusted",
        fake_process_chunk,
    )
    points = [SimpleNamespace(id=index, payload={}) for index in range(25)]
    engine = SimpleNamespace(
        portal_hydrator=None,
        official_sales_adapter=None,
        property_source_adapter=None,
        property_browser_adapter=None,
        official_matches_only=False,
        portal_fields=bulk_enrich_master.DEFAULT_PORTAL_FIELDS,
        no_images=True,
        preserve_payload_shape=True,
    )

    bulk_enrich_master.process_batch(points, engine, workers=2)

    assert len(calls) == 2
    assert sorted(
        point_id for call in calls for point_id in call["point_ids"]
    ) == list(range(25))
    assert all(call["preserve_payload_shape"] for call in calls)


def test_process_batch_keeps_small_live_source_batches_serial(monkeypatch):
    calls = []

    def fake_process_chunk(points, engine, force_update, now_iso, progress=False):
        calls.append([point.id for point in points])
        return [], 0

    monkeypatch.setattr(
        bulk_enrich_master,
        "_process_point_chunk_trusted",
        fake_process_chunk,
    )
    points = [SimpleNamespace(id=index, payload={}) for index in range(25)]
    engine = SimpleNamespace(
        portal_hydrator=object(),
        official_sales_adapter=None,
        property_source_adapter=None,
        property_browser_adapter=None,
        official_matches_only=False,
        portal_fields=bulk_enrich_master.DEFAULT_PORTAL_FIELDS,
        no_images=True,
        preserve_payload_shape=True,
    )

    bulk_enrich_master.process_batch(points, engine, workers=2)

    assert len(calls) == 1
