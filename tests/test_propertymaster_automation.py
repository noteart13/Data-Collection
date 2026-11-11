from argparse import Namespace

import pytest

import automate_propertymaster


def test_target_run_directory_is_scoped_by_endpoint_and_collection(tmp_path):
    local = automate_propertymaster.target_run_directory(
        "http://127.0.0.1:6333", "PropertyMaster", tmp_path
    )
    remote = automate_propertymaster.target_run_directory(
        "https://qdrant.example", "PropertyMaster", tmp_path
    )

    assert local != remote
    assert local.name.startswith("PropertyMaster_")


def test_all_states_use_small_to_large_order():
    assert automate_propertymaster.normalize_states(["ALL"]) == [
        "ACT", "NT", "TAS", "SA", "WA", "VIC", "QLD", "NSW"
    ]


def test_retry_is_bounded_and_uses_exponential_backoff():
    attempts = []
    delays = []

    def operation():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("temporary outage")
        return {"status": "completed"}

    result = automate_propertymaster.run_with_retries(
        operation,
        attempts=5,
        base_delay_seconds=2,
        sleep=delays.append,
    )

    assert result["status"] == "completed"
    assert len(attempts) == 3
    assert delays == [2, 4]


def test_http_api_key_requires_explicit_opt_in(tmp_path):
    args = Namespace(
        url="http://127.0.0.1:6333",
        api_key="secret",
        allow_insecure_api_key=False,
        collection="PropertyMaster",
        states=["NSW"],
        run_root=str(tmp_path),
    )

    with pytest.raises(RuntimeError, match="Refusing API key over HTTP"):
        automate_propertymaster.run_automation(args)
