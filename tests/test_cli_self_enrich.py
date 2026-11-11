from types import SimpleNamespace

import pytest

import cli


def args(**overrides):
    values = {
        "state": "NSW",
        "suburb": None,
        "apply": False,
        "refresh": False,
        "workers": None,
        "batch_size": None,
        "limit": None,
        "allow_http": False,
        "local": False,
        "local_api_key": None,
        "fast": False,
        "fields": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_self_enrich_defaults_to_a_five_record_self_scrape_dry_run(monkeypatch):
    monkeypatch.setenv("QDRANT_URI", "http://127.0.0.1:6333")

    params = cli.build_self_enrich_params(args())

    assert params[:2] == ["--state", "NSW"]
    assert ["--workers", "1"] == params[params.index("--workers"):params.index("--workers") + 2]
    assert ["--batch-size", "5"] == params[params.index("--batch-size"):params.index("--batch-size") + 2]
    assert ["--limit", "5"] == params[params.index("--limit"):params.index("--limit") + 2]
    assert "--dry-run" in params
    assert "--no-retry-outages" in params
    assert "--property-browser-fallback" in params
    assert "--skip-property-source" in params
    assert "--no-images" in params
    assert "--preserve-payload-shape" in params
    assert ["--nsw-psi-zip", "scratch\\archive.zip"] == params[
        params.index("--nsw-psi-zip"):params.index("--nsw-psi-zip") + 2
    ]
    assert "--allow-insecure-api-key" in params
    assert "--rollback-journal" not in params
    assert "--use-property-source" not in params


def test_self_enrich_fast_mode_is_a_bounded_bed_bath_probe(monkeypatch):
    monkeypatch.setenv("QDRANT_URI", "http://127.0.0.1:6333")

    params = cli.build_self_enrich_params(args(fast=True))

    portal_fields = params.index("--portal-fields")
    assert params[portal_fields + 1:portal_fields + 3] == ["bed", "bath"]
    assert params[portal_fields + 3] == "--no-images"
    assert params[params.index("--limit") + 1] == "1"
    assert params[params.index("--portal-http-max-candidates") + 1] == "1"
    assert params[params.index("--portal-browser-max-candidates") + 1] == "1"
    assert params[params.index("--portal-browser-timeout") + 1] == "30"


def test_self_enrich_apply_refresh_resets_scoped_checkpoint_and_writes_rollback():
    params = cli.build_self_enrich_params(
        args(suburb="Lane Cove North", apply=True, refresh=True, allow_http=True)
    )

    assert ["--suburb", "Lane Cove North"] == params[
        params.index("--suburb"):params.index("--suburb") + 2
    ]
    assert ["--workers", "2"] == params[params.index("--workers"):params.index("--workers") + 2]
    assert ["--batch-size", "25"] == params[params.index("--batch-size"):params.index("--batch-size") + 2]
    assert "--dry-run" not in params
    assert "--limit" not in params
    assert "--force" in params
    assert "--reset-checkpoint" in params
    assert "scratch\\self_enrich_NSW_lane-cove-north_checkpoint.json" in params
    assert "scratch\\self_enrich_NSW_lane-cove-north_rollback.jsonl.gz" in params


def test_self_enrich_rejects_all_states():
    with pytest.raises(ValueError, match="one state"):
        cli.build_self_enrich_params(args(state="ALL"))


def test_remote_http_api_key_requires_short_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("QDRANT_URI", "http://34.69.4.94:6333")
    monkeypatch.setenv("QDRANT_API", "secret")

    with pytest.raises(ValueError, match="--allow-http"):
        cli.build_self_enrich_params(args())

    params = cli.build_self_enrich_params(args(allow_http=True))

    assert "--allow-insecure-api-key" in params


def test_local_preset_overrides_remote_env_without_remote_http_opt_in(monkeypatch):
    monkeypatch.setenv("QDRANT_URI", "http://34.69.4.94:6333")
    monkeypatch.setenv("QDRANT_API", "remote-secret")

    params = cli.build_self_enrich_params(args(local=True))

    assert ["--url", "http://127.0.0.1:6333"] == params[
        params.index("--url"):params.index("--url") + 2
    ]
    assert ["--api-key", "local-qdrant-key"] == params[
        params.index("--api-key"):params.index("--api-key") + 2
    ]
    assert "--allow-insecure-api-key" in params
