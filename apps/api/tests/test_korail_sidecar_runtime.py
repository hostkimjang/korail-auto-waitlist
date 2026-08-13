import ast
from pathlib import Path

import rail_waitlist.korail_browser_adapter_service as compatibility_service
from rail_waitlist.korail_sidecar import runtime


def test_sidecar_service_reexports_the_canonical_runtime_objects() -> None:
    assert compatibility_service.KorailBrowserEngine is runtime.KorailBrowserEngine
    assert compatibility_service._ReadinessGate is runtime.ReadinessGate
    assert compatibility_service._browser_engine_setting is runtime.browser_engine_setting
    assert compatibility_service._build_browser_client is runtime.build_browser_client
    assert compatibility_service._readiness_probe_for_engine is runtime.readiness_probe_for_engine
    assert compatibility_service._integer_setting is runtime.integer_setting
    assert compatibility_service._float_setting is runtime.float_setting
    assert compatibility_service.build_automation is runtime.build_automation


def test_boolean_setting_accepts_only_explicit_true_or_false(
    monkeypatch,
) -> None:
    monkeypatch.delenv("KORAIL_BROWSER_GUI_ENABLED", raising=False)
    assert runtime.boolean_setting("KORAIL_BROWSER_GUI_ENABLED", False) is False

    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", " true ")
    assert runtime.boolean_setting("KORAIL_BROWSER_GUI_ENABLED", False) is True

    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", "false")
    assert runtime.boolean_setting("KORAIL_BROWSER_GUI_ENABLED", True) is False

    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", "1")
    try:
        runtime.boolean_setting("KORAIL_BROWSER_GUI_ENABLED", False)
    except RuntimeError as error:
        assert str(error) == "KORAIL_BROWSER_GUI_ENABLED must be true or false"
    else:
        raise AssertionError("invalid boolean setting must fail closed")


def test_build_browser_client_forwards_dialog_auto_action_setting(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(
        "rail_waitlist.korail_pydoll_browser.PydollKorailBrowserClient",
        FakeClient,
    )
    monkeypatch.setenv("KORAIL_BROWSER_GUI_ENABLED", "false")
    monkeypatch.delenv("KORAIL_RESERVATION_DIALOG_AUTO_ACTION_ENABLED", raising=False)

    runtime.build_browser_client(
        runtime.KorailBrowserEngine.PYDOLL,
        page_url="https://www.korail.com/ticket/search/general",
        timeout_seconds=25,
        allow_fullstack_fixture=False,
    )

    assert captured["auto_handle_dialogs"] is False

    monkeypatch.setenv("KORAIL_RESERVATION_DIALOG_AUTO_ACTION_ENABLED", "true")

    runtime.build_browser_client(
        runtime.KorailBrowserEngine.PYDOLL,
        page_url="https://www.korail.com/ticket/search/general",
        timeout_seconds=25,
        allow_fullstack_fixture=False,
    )

    assert captured["auto_handle_dialogs"] is True


def test_sidecar_runtime_does_not_reverse_depend_on_http_or_the_compatibility_facade() -> None:
    module_path = (
        Path(__file__).parents[1] / "src" / "rail_waitlist" / "korail_sidecar" / "runtime.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported_roots = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert "fastapi" not in imported_roots
    assert "korail_browser_adapter_service" not in imported_modules
