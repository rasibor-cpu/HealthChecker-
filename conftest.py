"""Repository-wide pytest configuration.

Windows sandbox sessions can inherit temp directories whose ACL belongs to an
older sandbox identity. Give every pytest invocation a fresh temp root, fall
back to an ignored repository-local root when necessary, and remove it after
the session.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
_SESSION_TEMP_ATTRIBUTE = "_healthchecker_session_temp"
_ORIGINAL_TEMP_ATTRIBUTE = "_healthchecker_original_temp"


def pytest_configure(config: pytest.Config) -> None:
    name = f"healthchecker-pytest-{os.getpid()}-{uuid.uuid4().hex}"
    configured_temp = os.environ.get("TEMP") or os.environ.get("TMP")
    candidates = ([Path(configured_temp)] if configured_temp else []) + [ROOT]
    session_temp = None
    for candidate in candidates:
        proposed = candidate / (name if candidate != ROOT else f".{name}")
        try:
            proposed.mkdir(mode=0o700)
        except OSError:
            continue
        session_temp = proposed
        break
    if session_temp is None:
        raise pytest.UsageError("no writable pytest temporary directory is available")
    system_temp = session_temp / "system"
    system_temp.mkdir(mode=0o700)

    setattr(config, _SESSION_TEMP_ATTRIBUTE, session_temp)
    setattr(
        config,
        _ORIGINAL_TEMP_ATTRIBUTE,
        (os.environ.get("TEMP"), os.environ.get("TMP"), tempfile.tempdir),
    )
    os.environ["TEMP"] = str(system_temp)
    os.environ["TMP"] = str(system_temp)
    tempfile.tempdir = str(system_temp)

    if config.option.basetemp is None:
        config.option.basetemp = str(session_temp / "pytest")


def pytest_unconfigure(config: pytest.Config) -> None:
    session_temp = getattr(config, _SESSION_TEMP_ATTRIBUTE, None)
    original = getattr(config, _ORIGINAL_TEMP_ATTRIBUTE, None)
    if original is not None:
        original_temp, original_tmp, original_tempdir = original
        for name, value in (("TEMP", original_temp), ("TMP", original_tmp)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        tempfile.tempdir = original_tempdir
    if isinstance(session_temp, Path):
        shutil.rmtree(session_temp, ignore_errors=True)
