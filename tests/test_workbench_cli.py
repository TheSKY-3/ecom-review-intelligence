import io
import json
from importlib import import_module


def test_check_mode_reports_local_synthetic_url_without_starting_server():
    module = import_module("ecom_review_intelligence.workbench_cli")
    output = io.StringIO()
    calls = []

    code = module.workbench_main(
        ["--check", "--port", "8877"],
        stdout=output,
        server_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert code == 0
    assert json.loads(output.getvalue()) == {
        "schema_version": "p7-workbench-cli-v1",
        "status": "READY",
        "mode": "synthetic",
        "url": "http://127.0.0.1:8877",
    }
    assert calls == []


def test_server_runner_receives_only_loopback_and_no_reload():
    module = import_module("ecom_review_intelligence.workbench_cli")
    calls = []

    code = module.workbench_main(
        ["--port", "8878"],
        server_runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert code == 0 and len(calls) == 1
    assert calls[0][1] == {
        "host": "127.0.0.1",
        "port": 8878,
        "reload": False,
        "access_log": False,
    }


def test_external_binding_and_invalid_port_fail_with_fixed_error():
    module = import_module("ecom_review_intelligence.workbench_cli")
    for arguments in (["--host", "0.0.0.0"], ["--port", "80"]):
        errors = io.StringIO()
        code = module.workbench_main(arguments, stderr=errors)
        assert code == 2
        assert json.loads(errors.getvalue()) == {
            "schema_version": "p7-workbench-cli-v1",
            "status": "INVALID_ARGUMENT",
        }


def test_local_mode_requires_explicit_acknowledgement():
    module = import_module("ecom_review_intelligence.workbench_cli")
    errors = io.StringIO()

    code = module.workbench_main(["--mode", "local", "--check"], stderr=errors)

    assert code == 2
    assert json.loads(errors.getvalue()) == {
        "schema_version": "p7-workbench-cli-v1",
        "status": "INVALID_ARGUMENT",
    }
