"""Byte-for-byte golden harness for the option CLIs in IBKR mode (share plan, Task 1).

A GoldenCase runs one CLI `main(argv, today=...)` inside a throw-away root that mirrors the real
repo layout (`data/paper-trading` next to `wiki/options`, so the public page is written too).
`run_case` replays the case's `depends_on` chain first (open needs suggest, and so on), then
returns every file under the root plus the case's own console as bytes. The only normalisation is
replacing the root path (native and posix spelling) with `<ROOT>`.

Goldens live in tests/golden/ibkr/<case>/ and are written only when OPTION_GOLDEN_REGEN=1.
Regenerating them after Task 1 means the IBKR output changed, which the share plan forbids.
They are Windows-only and CRLF-sensitive and never shipped to the share repo; see
tests/golden/README.md.
"""
from __future__ import annotations

import importlib
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping

from _pytest.monkeypatch import MonkeyPatch

GOLDEN_TODAY = date(2026, 10, 23)   # Friday
GOLDEN_DIR = Path(__file__).resolve().parent / "golden" / "ibkr"
CALLS_DIR = Path(__file__).resolve().parent / "golden" / "ibkr_calls"
REGEN_ENV = "OPTION_GOLDEN_REGEN"
CONSOLE_KEY = "<console>"
CONSOLE_FILE = "__console__.txt"
ROOT_TOKEN = b"<ROOT>"
LEDGER_PARTS = ("data", "paper-trading")
NO_NETWORK_ENV = "OPTION_TESTS_NO_NETWORK"   # read by the conftest guard at connect time
SCRIPTS_DIR = (Path(__file__).resolve().parent.parent / "scripts").resolve()


@dataclass(frozen=True)
class GoldenCase:
    name: str
    module: str
    argv: tuple[str, ...]
    setup: Callable[[Path, MonkeyPatch], None]
    depends_on: str | None = None
    today: date = GOLDEN_TODAY
    ledger_arg: bool = True   # prepend --ledger-dir <root>/data/paper-trading
    expect_exit: int = 0


def ledger_dir(root: Path) -> Path:
    return root.joinpath(*LEDGER_PARTS)


def regen_enabled() -> bool:
    return os.environ.get(REGEN_ENV) == "1"


def invoke(module: str, argv: tuple[str, ...] | list[str], root: Path, today: date,
           ledger_arg: bool = True) -> int:
    """Call `<module>.main` the way the CLI tests do: explicit today, ledger under the root."""
    mod = importlib.import_module(module)
    full = (["--ledger-dir", str(ledger_dir(root))] if ledger_arg else []) + list(argv)
    return mod.main(full, today=today)


def _chain(case: GoldenCase, registry: Mapping[str, GoldenCase]) -> list[GoldenCase]:
    chain, seen = [case], {case.name}
    while chain[0].depends_on is not None:
        parent = registry[chain[0].depends_on]
        if parent.name in seen:
            raise ValueError(f"depends_on cycle at {parent.name}")
        seen.add(parent.name)
        chain.insert(0, parent)
    return chain


def _default_registry() -> Mapping[str, GoldenCase]:
    import golden_ibkr_cases
    return golden_ibkr_cases.CASES_BY_NAME


def _console(code: int, out: str, err: str) -> bytes:
    return f"exit={code}\n--- stdout ---\n{out}--- stderr ---\n{err}".encode("utf-8")


def normalise(data: bytes, root: Path) -> bytes:
    for spelling in {str(root), root.as_posix()}:
        data = data.replace(spelling.encode("utf-8"), ROOT_TOKEN)
    return data


def collect(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): normalise(p.read_bytes(), root)
            for p in sorted(root.rglob("*")) if p.is_file()}


def run_case(case: GoldenCase, root: Path, monkeypatch: MonkeyPatch, capsys,
             registry: Mapping[str, GoldenCase] | None = None,
             before_step: Callable[[GoldenCase], None] | None = None) -> dict[str, bytes]:
    """Run the dependency chain then the case; return {relative path: bytes, "<console>": ...}.

    `before_step` runs after each step's setup, right before its CLI call (boundary logs).
    The conftest no-network guard is switched on for the whole run.
    """
    monkeypatch.setenv(NO_NETWORK_ENV, "1")
    ledger_dir(root).mkdir(parents=True, exist_ok=True)
    console = b""
    for step in _chain(case, registry or _default_registry()):
        step.setup(root, monkeypatch)
        capsys.readouterr()   # setup / earlier steps do not belong to this case's console
        if before_step is not None:
            before_step(step)
        code = invoke(step.module, step.argv, root, step.today, step.ledger_arg)
        captured = capsys.readouterr()
        assert code == step.expect_exit, (step.name, code, captured.err)
        console = normalise(_console(code, captured.out, captured.err), root)
    outputs = collect(root)
    outputs[CONSOLE_KEY] = console
    return outputs


def _disk_name(key: str) -> str:
    return CONSOLE_FILE if key == CONSOLE_KEY else key


def write_golden(name: str, outputs: Mapping[str, bytes], base: Path = GOLDEN_DIR) -> None:
    folder = base / name
    if folder.exists():
        shutil.rmtree(folder)
    for key, data in outputs.items():
        path = folder / _disk_name(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def read_golden(name: str, base: Path = GOLDEN_DIR) -> dict[str, bytes]:
    folder = base / name
    out = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            rel = p.relative_to(folder).as_posix()
            out[CONSOLE_KEY if rel == CONSOLE_FILE else rel] = p.read_bytes()
    return out


def pin(name: str, outputs: Mapping[str, bytes], base: Path = GOLDEN_DIR) -> None:
    """Write goldens when OPTION_GOLDEN_REGEN=1, otherwise assert byte equality."""
    if regen_enabled():
        write_golden(name, outputs, base)
        return
    expected = read_golden(name, base)
    assert expected, f"no golden files for {name} under {base}"
    assert sorted(outputs) == sorted(expected), f"{name}: file set differs"
    for key in expected:
        assert outputs[key] == expected[key], f"{name}: {key} differs from the golden bytes"


def pin_bytes(path: Path, data: bytes) -> None:
    """Single-file pin (boundary call logs): write under regen, else compare exactly."""
    if regen_enabled():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return
    assert path.is_file(), f"no golden file {path.name}"
    assert data == path.read_bytes(), f"{path.name} differs from the golden bytes"


# ---- wall-clock guard ---------------------------------------------------------------------

class _RealCheck(type):
    """isinstance(x, NoClockDate) behaves like isinstance(x, date) for real date objects."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, cls.__mro__[1])

    def __subclasscheck__(cls, sub):
        return issubclass(sub, cls.__mro__[1])


def _clock_read(*args, **kwargs):
    raise AssertionError("wall clock read during a golden run")


class NoClockDate(date, metaclass=_RealCheck):
    today = classmethod(_clock_read)


class NoClockDatetime(datetime, metaclass=_RealCheck):
    today = classmethod(_clock_read)
    now = classmethod(_clock_read)
    utcnow = classmethod(_clock_read)


def _script_modules() -> list:
    mods = []
    for mod in list(sys.modules.values()):
        path = getattr(mod, "__file__", None)
        if path and Path(path).resolve().parent == SCRIPTS_DIR:
            mods.append(mod)
    return mods


def forbid_wall_clock(monkeypatch: MonkeyPatch, module_names: tuple[str, ...]) -> list[str]:
    """Swap every `date` / `datetime` name in the scripts modules for clock-less subclasses."""
    for name in module_names:
        importlib.import_module(name)
    patched = []
    for mod in _script_modules():
        for attr, fake in (("date", NoClockDate), ("datetime", NoClockDatetime)):
            if getattr(mod, attr, None) is (date if attr == "date" else datetime):
                monkeypatch.setattr(mod, attr, fake)
                patched.append(f"{mod.__name__}.{attr}")
    return sorted(patched)
