from __future__ import annotations

from types import ModuleType

import pytest


def _configure_openai_imports(
    monkeypatch,
    patch,
    *,
    missing_openai: bool = False,
    present_openai: bool = False,
    broken_deep_import: BaseException | None = None,
) -> None:
    real_import_module = patch.importlib.import_module
    fake_openai_module = ModuleType("openai")

    def fake_import_module(name: str, package=None):
        if missing_openai and name == "openai":
            raise ModuleNotFoundError("No module named 'openai'", name="openai")
        if name == "openai" and (present_openai or broken_deep_import is not None):
            return fake_openai_module
        if broken_deep_import is not None and name == "openai.resources.chat.completions.completions":
            raise broken_deep_import
        return real_import_module(name, package)

    monkeypatch.setattr(patch.importlib, "import_module", fake_import_module)


def _reset_patch_state(monkeypatch, patch) -> None:
    monkeypatch.setattr(patch, "_installed", False)
    monkeypatch.setattr(patch, "_original_async_create", None)
    monkeypatch.setattr(patch, "_original_sync_create", None)
    monkeypatch.setattr(patch, "_async_completions_class", None, raising=False)
    monkeypatch.setattr(patch, "_sync_completions_class", None, raising=False)


def test_openai_patch_noops_when_openai_missing(monkeypatch) -> None:
    import replay.openai_patch as patch

    _configure_openai_imports(monkeypatch, patch, missing_openai=True)
    _reset_patch_state(monkeypatch, patch)

    patch.install_openai_patch()

    assert patch._installed is False
    assert patch._original_async_create is None
    assert patch._original_sync_create is None

    monkeypatch.setattr(patch, "_installed", True)
    patch.uninstall_openai_patch()

    assert patch._installed is False


def test_replay_install_uninstall_noops_without_openai(monkeypatch) -> None:
    import replay
    import replay.openai_patch as patch

    _configure_openai_imports(monkeypatch, patch, missing_openai=True)
    _reset_patch_state(monkeypatch, patch)

    replay.install(semantic=False, langchain=False, langgraph=False)
    replay.uninstall()

    assert patch._installed is False


def test_openai_patch_propagates_non_missing_import_failures(monkeypatch) -> None:
    import replay.openai_patch as patch

    _configure_openai_imports(
        monkeypatch,
        patch,
        present_openai=True,
        broken_deep_import=ImportError("broken deep import"),
    )
    _reset_patch_state(monkeypatch, patch)

    with pytest.raises(ImportError, match="broken deep import"):
        patch.install_openai_patch()

    assert patch._installed is False


def test_openai_patch_propagates_transitive_module_not_found_from_top_level_import(
    monkeypatch,
) -> None:
    import replay.openai_patch as patch

    real_import_module = patch.importlib.import_module

    def fake_import_module(name: str, package=None):
        if name == "openai":
            raise ModuleNotFoundError("No module named 'httpx'", name="httpx")
        return real_import_module(name, package)

    _reset_patch_state(monkeypatch, patch)
    monkeypatch.setattr(patch.importlib, "import_module", fake_import_module)

    with pytest.raises(ModuleNotFoundError, match="No module named 'httpx'"):
        patch.install_openai_patch()

    assert patch._installed is False


def test_uninstall_openai_patch_restores_from_captured_classes(monkeypatch) -> None:
    import replay.openai_patch as patch

    async def original_async_create(self, *args, **kwargs):
        return None

    def original_sync_create(self, *args, **kwargs):
        return None

    class AsyncCompletions:
        create = original_async_create

    class Completions:
        create = original_sync_create

    _reset_patch_state(monkeypatch, patch)
    monkeypatch.setattr(
        patch,
        "_load_openai_completion_classes",
        lambda: (AsyncCompletions, Completions),
    )

    patch.install_openai_patch()

    assert AsyncCompletions.create is patch._patched_async_create
    assert Completions.create is patch._patched_sync_create

    monkeypatch.setattr(patch, "_load_openai_completion_classes", lambda: None)
    patch.uninstall_openai_patch()

    assert AsyncCompletions.create is original_async_create
    assert Completions.create is original_sync_create
    assert patch._installed is False


def test_openai_patch_rolls_back_when_second_assignment_fails(monkeypatch) -> None:
    import replay.openai_patch as patch

    async def original_async_create(self, *args, **kwargs):
        return None

    def original_sync_create(self, *args, **kwargs):
        return None

    class AsyncCompletions:
        create = original_async_create

    class FailingSetAttr(type):
        def __setattr__(cls, name, value):
            if name == "create":
                raise RuntimeError("sync patch failed")
            return super().__setattr__(name, value)

    class Completions(metaclass=FailingSetAttr):
        create = original_sync_create

    _reset_patch_state(monkeypatch, patch)
    monkeypatch.setattr(
        patch,
        "_load_openai_completion_classes",
        lambda: (AsyncCompletions, Completions),
    )

    with pytest.raises(RuntimeError, match="sync patch failed"):
        patch.install_openai_patch()

    assert AsyncCompletions.create is original_async_create
    assert patch._installed is False
    assert patch._original_async_create is None
    assert patch._original_sync_create is None
    assert patch._async_completions_class is None
    assert patch._sync_completions_class is None
