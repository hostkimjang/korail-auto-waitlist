from __future__ import annotations

import ast
import pickle
from pathlib import Path

from rail_waitlist import models as legacy
from rail_waitlist.admin_auth import models as admin_models
from rail_waitlist.browser_companion import models as companion_models
from rail_waitlist.database import Base
from rail_waitlist.idempotency import models as idempotency_models
from rail_waitlist.notification_management import models as notification_models
from rail_waitlist.official_page_confirmation import models as confirmation_models
from rail_waitlist.outbox_management import models as outbox_models
from rail_waitlist.provider_account_management import models as account_models
from rail_waitlist.provider_circuit import models as circuit_models
from rail_waitlist.provider_execution import models as execution_models
from rail_waitlist.timetable_management import models as timetable_models
from rail_waitlist.watch_management import models as watch_models

API_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_BY_SYMBOL = {
    "AdminAccount": admin_models.AdminAccount,
    "AdminSession": admin_models.AdminSession,
    "BrowserCompanionChallenge": companion_models.BrowserCompanionChallenge,
    "BrowserCompanionCredential": companion_models.BrowserCompanionCredential,
    "BrowserCompanionPairing": companion_models.BrowserCompanionPairing,
    "IdempotencyRecord": idempotency_models.IdempotencyRecord,
    "KorailBrowserSeatSnapshot": companion_models.KorailBrowserSeatSnapshot,
    "KorailBrowserSnapshotBatch": companion_models.KorailBrowserSnapshotBatch,
    "NativePushCredential": notification_models.NativePushCredential,
    "NativePushPairing": notification_models.NativePushPairing,
    "NotificationChannel": notification_models.NotificationChannel,
    "OfficialPageSeatConfirmation": confirmation_models.OfficialPageSeatConfirmation,
    "OutboxEvent": outbox_models.OutboxEvent,
    "ProviderCircuit": circuit_models.ProviderCircuit,
    "ProviderExecutionLease": execution_models.ProviderExecutionLease,
    "RailProviderAccount": account_models.RailProviderAccount,
    "ReservationAttempt": watch_models.ReservationAttempt,
    "SeatObservation": watch_models.SeatObservation,
    "StationCatalogCache": timetable_models.StationCatalogCache,
    "TimetableSeatEvidence": timetable_models.TimetableSeatEvidence,
    "Watch": watch_models.Watch,
    "WatchCandidate": watch_models.WatchCandidate,
    "WatchTransitionHistory": watch_models.WatchTransitionHistory,
    "utcnow": watch_models.utcnow,
}


def test_central_models_is_a_definition_free_exact_alias_facade() -> None:
    module_path = API_ROOT / "src" / "rail_waitlist" / "models.py"
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    definitions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assigned_symbols = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    owner_imports = {
        (node.module, node.level, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert definitions == set()
    assert assigned_symbols == set(CANONICAL_BY_SYMBOL)
    assert owner_imports == {
        ("admin_auth", 1, "models", "admin_auth_models"),
        ("browser_companion", 1, "models", "browser_companion_models"),
        ("idempotency", 1, "models", "idempotency_models"),
        ("notification_management", 1, "models", "notification_management_models"),
        ("official_page_confirmation", 1, "models", "official_page_confirmation_models"),
        ("outbox_management", 1, "models", "outbox_management_models"),
        ("provider_account_management", 1, "models", "provider_account_management_models"),
        ("provider_circuit", 1, "models", "provider_circuit_models"),
        ("provider_execution", 1, "models", "provider_execution_models"),
        ("timetable_management", 1, "models", "timetable_management_models"),
        ("watch_management", 1, "models", "watch_management_models"),
    }
    assert len({name for name in vars(legacy) if not name.startswith("_")}) == 35
    assert {
        name for name in vars(legacy) if name.startswith("_") and not name.startswith("__")
    } == set()
    assert not hasattr(legacy, "__all__")
    assert called_names.isdisjoint({"Table", "mapper", "registry"})
    assert called_attributes.isdisjoint({"map_imperatively", "mapped", "mapped_as_dataclass"})
    for symbol, owner in CANONICAL_BY_SYMBOL.items():
        assert getattr(legacy, symbol) is owner


def test_central_models_registers_each_canonical_mapper_once() -> None:
    model_classes = {owner for symbol, owner in CANONICAL_BY_SYMBOL.items() if symbol != "utcnow"}
    mapped_classes = {mapper.class_ for mapper in Base.registry.mappers}

    assert len(model_classes) == 23
    assert len(Base.metadata.tables) == 23
    assert len(Base.registry.mappers) == 23
    assert mapped_classes == model_classes


def test_all_legacy_model_globals_restore_the_exact_canonical_objects() -> None:
    for symbol, owner in CANONICAL_BY_SYMBOL.items():
        payload = f"crail_waitlist.models\n{symbol}\n.".encode()
        assert pickle.loads(payload) is owner


def test_central_models_has_only_metadata_bootstrap_consumers() -> None:
    source_root = API_ROOT / "src" / "rail_waitlist"
    consumers: set[str] = set()
    for path in source_root.rglob("*.py"):
        if path.name == "models.py" and path.parent == source_root:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(
            isinstance(node, ast.ImportFrom)
            and node.level == 1
            and node.module is None
            and any(alias.name == "models" for alias in node.names)
            for node in ast.walk(tree)
        ):
            consumers.add(path.relative_to(API_ROOT / "src").as_posix())

    migration_path = API_ROOT / "migrations" / "env.py"
    migration_tree = ast.parse(
        migration_path.read_text(encoding="utf-8"),
        filename=str(migration_path),
    )
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "rail_waitlist"
        and any(alias.name == "models" for alias in node.names)
        for node in ast.walk(migration_tree)
    )
    assert consumers == {"rail_waitlist/main.py"}
