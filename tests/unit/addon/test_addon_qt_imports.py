"""Regression tests for the PySide2 -> PySide6 import fallbacks.

The bundled bridge server and ``init_gui.py`` import ``QtCore`` from PySide2
first and fall back to PySide6.  A partially initialised PySide2 installation
can raise ``NameError`` (not just ``ImportError``) during that import, which
previously aborted the fallback.

These tests cover:

* ``_get_qt_core``: PySide2 ``NameError`` falls back to PySide6, and both
  failures return ``None``.
* ``_start_queue_processor``: PySide2 ``NameError`` falls back to the Qt
  timer, and both failures use the background-thread path.
* ``init_gui.py``: a PySide2 ``NameError`` reaches the PySide6 fallback, and
  failure of both imports reaches the existing startup fallback (the warning
  logged by the outer ``except`` block).
"""

from __future__ import annotations

import importlib
import runpy
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

ADDON_DIR = Path(__file__).parent.parent.parent.parent / "freecad" / "RobustMCPBridge"
INIT_GUI_PATH = ADDON_DIR / "init_gui.py"


class _NameErrorModule(types.ModuleType):
    """Module whose ``Qt*`` attributes raise ``NameError`` on access.

    This mimics a PySide2 shim that exists in ``sys.modules`` but has not yet
    finished binding its submodules.
    """

    def __getattr__(self, name: str) -> Any:
        """Raise on every attribute lookup.

        Args:
            name: The attribute that was looked up.

        Returns:
            Never returns; the method always raises.

        Raises:
            NameError: If ``name`` starts with ``Qt``, mimicking a PySide2
                shim whose submodules are not bound yet.
            AttributeError: For any other attribute name.
        """
        if name.startswith("Qt"):
            raise NameError(f"name '{name}' is not defined")
        raise AttributeError(name)


def _pyside_module(qtcore: Any) -> types.ModuleType:
    """Build a fake PySide module exposing ``QtCore``."""
    module = types.ModuleType("PySide6")
    module.QtCore = qtcore  # type: ignore[attr-defined]
    return module


def _pyside2_nameerror() -> types.ModuleType:
    """Fake PySide2 whose ``QtCore`` access raises ``NameError``."""
    return _NameErrorModule("PySide2")


def _pyside2_importerror() -> None:
    """Make ``from PySide2 import ...`` raise ``ImportError``."""
    return None


# ---------------------------------------------------------------------------
# Bundled bridge server
# ---------------------------------------------------------------------------


@pytest.fixture
def server_mod(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Import the bundled bridge server module in isolation.

    The package is dropped from ``sys.modules`` before the import so that a
    cached copy from another test cannot be reused.  Any module the import
    adds is removed again on teardown, because ``monkeypatch`` only restores
    the entries it deleted itself.
    """
    monkeypatch.syspath_prepend(str(ADDON_DIR))
    for name in list(sys.modules):
        if name == "freecad_mcp_bridge" or name.startswith("freecad_mcp_bridge."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module("freecad_mcp_bridge.server")
    yield module
    for name in list(sys.modules):
        if name == "freecad_mcp_bridge" or name.startswith("freecad_mcp_bridge."):
            del sys.modules[name]


@pytest.fixture
def fake_freecad() -> Any:
    """Minimal stand-in for the ``FreeCAD`` module."""
    freecad = types.ModuleType("FreeCAD")
    freecad.GuiUp = True  # type: ignore[attr-defined]
    freecad.Console = MagicMock()  # type: ignore[attr-defined]
    return freecad


def _enable_gui(
    server_mod: Any, monkeypatch: pytest.MonkeyPatch, fake_freecad: Any
) -> None:
    """Pretend we are running inside a FreeCAD GUI session."""
    monkeypatch.setattr(server_mod, "FREECAD_AVAILABLE", True, raising=False)
    monkeypatch.setattr(server_mod, "FreeCAD", fake_freecad, raising=False)


class TestGetQtCore:
    """Tests for the ``_get_qt_core`` import fallback."""

    def test_nameerror_falls_back_to_pyside6(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """A PySide2 ``NameError`` should fall back to PySide6."""
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        qtcore = MagicMock()
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))

        assert server_mod._get_qt_core() is qtcore

    def test_nameerror_on_both_returns_none(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """If both bindings fail the helper should return ``None``."""
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _NameErrorModule("PySide6"))

        assert server_mod._get_qt_core() is None

    def test_importerror_still_falls_back_to_pyside6(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """The pre-existing ``ImportError`` fallback must keep working."""
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        qtcore = MagicMock()
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_importerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))

        assert server_mod._get_qt_core() is qtcore

    def test_returns_none_without_gui(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """Without a GUI there is nothing to import."""
        fake_freecad.GuiUp = False
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        qtcore = MagicMock()
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))

        assert server_mod._get_qt_core() is None


class TestStartQueueProcessor:
    """Tests for the ``_start_queue_processor`` import fallback."""

    @staticmethod
    def _plugin(server_mod: Any) -> Any:
        """Create a bare plugin instance without running ``__init__``."""
        plugin = object.__new__(server_mod.FreecadMCPPlugin)
        plugin._timer = None
        plugin._headless = False
        plugin._queue_thread = None
        return plugin

    @staticmethod
    def _fake_thread(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
        """Replace ``threading.Thread`` with a recording stub."""
        created: list[Any] = []

        class _FakeThread:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.args = args
                self.kwargs = kwargs
                self.started = False
                created.append(self)

            def start(self) -> None:
                self.started = True

        monkeypatch.setattr("threading.Thread", _FakeThread)
        return created

    def test_nameerror_falls_back_to_qt_timer(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """A PySide2 ``NameError`` should still create the PySide6 timer."""
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        timer = MagicMock()
        qtcore = MagicMock()
        qtcore.QTimer.return_value = timer
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))
        threads = self._fake_thread(monkeypatch)

        plugin = self._plugin(server_mod)
        plugin._start_queue_processor()

        assert plugin._timer is timer
        assert plugin._headless is False
        assert threads == []
        timer.start.assert_called_once_with(server_mod.QUEUE_POLL_INTERVAL_MS)
        timer.timeout.connect.assert_called_once_with(plugin._process_queue)

    def test_nameerror_on_both_uses_background_thread(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """If no Qt binding is usable the background thread path is taken."""
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _NameErrorModule("PySide6"))
        threads = self._fake_thread(monkeypatch)

        plugin = self._plugin(server_mod)
        plugin._start_queue_processor()

        assert plugin._timer is None
        assert plugin._headless is True
        assert len(threads) == 1
        assert threads[0].started is True

    def test_headless_mode_uses_background_thread(
        self,
        server_mod: Any,
        monkeypatch: pytest.MonkeyPatch,
        fake_freecad: Any,
    ) -> None:
        """Headless sessions never create a Qt timer."""
        fake_freecad.GuiUp = False
        _enable_gui(server_mod, monkeypatch, fake_freecad)
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _NameErrorModule("PySide6"))
        threads = self._fake_thread(monkeypatch)

        plugin = self._plugin(server_mod)
        plugin._start_queue_processor()

        assert plugin._timer is None
        assert plugin._headless is True
        assert len(threads) == 1


# ---------------------------------------------------------------------------
# init_gui.py
# ---------------------------------------------------------------------------


def _install_init_gui_stubs(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Install the fake FreeCAD/Qt modules required by ``init_gui.py``."""
    freecad = types.ModuleType("FreeCAD")
    freecad.GuiUp = True  # type: ignore[attr-defined]
    freecad.Console = MagicMock()  # type: ignore[attr-defined]

    class _Workbench:
        """Stand-in for ``FreeCADGui.Workbench``."""

    freecad_gui = types.ModuleType("FreeCADGui")
    freecad_gui.Workbench = _Workbench  # type: ignore[attr-defined]
    freecad_gui.addWorkbench = MagicMock()  # type: ignore[attr-defined]
    freecad_gui.addIconPath = MagicMock()  # type: ignore[attr-defined]
    freecad_gui.addPreferencePage = MagicMock()  # type: ignore[attr-defined]

    path_utils = types.ModuleType("path_utils")
    path_utils.get_icons_dir = lambda: ""  # type: ignore[attr-defined]
    path_utils.get_workbench_icon = lambda: ""  # type: ignore[attr-defined]

    preferences = types.ModuleType("preferences")
    preferences.get_auto_start = lambda: False  # type: ignore[attr-defined]

    qt_package = types.ModuleType("Qt")
    qt_package.__path__ = []  # type: ignore[attr-defined]
    qt_preferences = types.ModuleType("Qt.preferences_page")
    qt_preferences.MCPBridgePreferencesPage = object  # type: ignore[attr-defined]
    qt_status = types.ModuleType("Qt.status_widget")
    qt_status.sync_status_with_bridge = lambda: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "FreeCAD", freecad)
    monkeypatch.setitem(sys.modules, "FreeCADGui", freecad_gui)
    monkeypatch.setitem(sys.modules, "path_utils", path_utils)
    monkeypatch.setitem(sys.modules, "preferences", preferences)
    monkeypatch.setitem(sys.modules, "Qt", qt_package)
    monkeypatch.setitem(sys.modules, "Qt.preferences_page", qt_preferences)
    monkeypatch.setitem(sys.modules, "Qt.status_widget", qt_status)
    return freecad


def _warnings(freecad: Any) -> list[str]:
    """Collect the warning messages logged to the FreeCAD console."""
    return [call.args[0] for call in freecad.Console.PrintWarning.call_args_list]


class TestInitGuiQtFallback:
    """Tests for the PySide import fallback in ``init_gui.py``."""

    def test_nameerror_falls_back_to_pyside6(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A PySide2 ``NameError`` should reach the PySide6 code path."""
        freecad = _install_init_gui_stubs(monkeypatch)
        qtcore = MagicMock()
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))

        runpy.run_path(str(INIT_GUI_PATH), run_name="init_gui_under_test")

        assert qtcore.QTimer.singleShot.call_count >= 1
        assert not any(
            "Could not schedule status bar sync" in msg for msg in _warnings(freecad)
        )

    def test_nameerror_on_both_hits_startup_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If both bindings fail the existing startup fallback runs."""
        freecad = _install_init_gui_stubs(monkeypatch)
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_nameerror())
        monkeypatch.setitem(sys.modules, "PySide6", _NameErrorModule("PySide6"))

        runpy.run_path(str(INIT_GUI_PATH), run_name="init_gui_under_test")

        warnings = _warnings(freecad)
        assert any("Could not schedule status bar sync" in msg for msg in warnings)
        assert any("is not defined" in msg for msg in warnings)

    def test_importerror_still_falls_back_to_pyside6(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-existing ``ImportError`` fallback must keep working."""
        freecad = _install_init_gui_stubs(monkeypatch)
        qtcore = MagicMock()
        monkeypatch.setitem(sys.modules, "PySide2", _pyside2_importerror())
        monkeypatch.setitem(sys.modules, "PySide6", _pyside_module(qtcore))

        runpy.run_path(str(INIT_GUI_PATH), run_name="init_gui_under_test")

        assert qtcore.QTimer.singleShot.call_count >= 1
        assert not any(
            "Could not schedule status bar sync" in msg for msg in _warnings(freecad)
        )
