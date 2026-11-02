"""Hypothesis plumbing for the property-based suite.

Everything this suite needs from Hypothesis is configured HERE and
nowhere else:

* **Profiles** — `quick` (max_examples=50), `discovery` (500), `deep`
  (2000 for the cheap pure properties). Select at the command line:
      python -m pytest tests/properties --hypothesis-profile=quick
  The profile is loaded from $HYPOTHESIS_PROFILE by default so ad hoc
  runs and the CLI behave the same; an explicit --hypothesis-profile
  always wins, because the hypothesis pytest plugin applies it after
  conftest import.

* **Example database** — this suite keeps its own persistent database
  under tests/properties/.hypothesis-db/ (git-ignored). It is set BEFORE
  any profile registration, because Hypothesis snapshots the directory
  when settings are first built. A repo-root .hypothesis/ directory is a
  USER artifact from whoever ran Hypothesis in the repo root; this suite
  never touches it.

* **Expensive budgets** — `expensive_settings()` is for database-backed
  properties that run real pipeline stages. It scales the example count
  per profile (10/50/100) and drops the deadline, because a stage that
  legitimately takes 300ms once can take 2s on a busy shared box; their
  input sizes are bounded instead, and the wall-clock budget is the
  profile choice, not a per-example timer.

* **Witnesses** — when a property fails, `pytest_exception_interact`
  writes the shrunk example (the "Falsifying example:" block verbatim)
  to investigation/witnesses/ as a plain text file, independent of the
  example database: the database can be deleted, seeds go stale when
  strategies change, but a witness file replays forever.
"""

from __future__ import annotations

import os
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import hypothesis
import pytest
from hypothesis import HealthCheck, settings
from hypothesis.configuration import set_hypothesis_home_dir

# tests/properties/conftest.py -> parents[0] tests/properties,
# parents[1] tests, parents[2] the repository root.
_SUITE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SUITE_DIR.parents[1]

# The suite's own example database. Kept under the suite directory so a
# copy of this tree is self-contained; git-ignored, never committed.
HYPOTHESIS_DB_DIR = _SUITE_DIR / ".hypothesis-db"
# Witness files are CAMPAIGN EVIDENCE (committed when produced), unlike
# the database. Plain text, one failing example per file.
WITNESS_DIR = _REPO_ROOT / "investigation" / "witnesses"

set_hypothesis_home_dir(str(HYPOTHESIS_DB_DIR))

# Cheap pure properties keep the deadline: a pure function whose runtime
# varies run to run is itself a finding. Database-backed properties opt
# out via expensive_settings() instead.
_PROFILE_DEADLINE = timedelta(seconds=1)

# Registered profile bodies, kept as plain kwargs so tests can assert on
# them without reaching into Hypothesis internals.
PROFILES: dict[str, dict[str, Any]] = {
    "quick": {"max_examples": 50, "deadline": _PROFILE_DEADLINE},
    "discovery": {"max_examples": 500, "deadline": _PROFILE_DEADLINE},
    "deep": {"max_examples": 2000, "deadline": _PROFILE_DEADLINE},
}

# Expensive stage/pipeline properties get their OWN budget per profile:
# each example runs real SQL stages, so 2000 of them is not "deep", it
# is a job. 10/50/100 keeps quick under a minute and discovery useful.
EXPENSIVE_BUDGETS: dict[str, int] = {
    "quick": 10,
    "discovery": 50,
    "deep": 100,
}

for _name, _kwargs in PROFILES.items():
    settings.register_profile(_name, **_kwargs)

# Default when nothing is selected. An explicit --hypothesis-profile
# takes precedence: the hypothesis pytest plugin loads it after conftest
# import (initial conftests load before pytest_configure hooks run).
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "quick"))


def expensive_settings() -> settings:
    """Settings for expensive database-backed properties.

    Example count scales with the active profile (10/50/100), deadline
    is off (per-example timing of real SQL stages is noise, not signal),
    and the health checks that a deliberately heavy example would
    trip are silenced. Keep the INPUT bound tight instead: these tests
    generate a handful of patients, never thousands.
    """
    budget = EXPENSIVE_BUDGETS[settings.get_current_profile_name()]
    return settings(
        max_examples=budget,
        deadline=None,
        suppress_health_check=list(HealthCheck),
    )


# ---------------------------------------------------------------------
# Witness recording
# ---------------------------------------------------------------------

_SANITIZE = re.compile(r"[^A-Za-z0-9._-]+")


def _witness_path(nodeid: str) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    stem = _SANITIZE.sub("-", nodeid).strip("-")
    return WITNESS_DIR / f"{stem}-{stamp}.txt"


def _witness_text(nodeid: str, report_text: str) -> str:
    profile = settings.get_current_profile_name()
    return (
        f"witness: {nodeid}\n"
        f"recorded: {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
        f"profile: {profile}\n"
        f"hypothesis: {hypothesis.__version__}\n"
        f"reproduce: python -m pytest '{nodeid}' "
        f"--hypothesis-profile={profile}\n"
        "----- shrunk example follows (verbatim) -----\n"
        f"{report_text}\n"
    )


def pytest_exception_interact(
    node: pytest.Item,
    call: pytest.CallInfo[Any],
    report: pytest.TestReport,
) -> None:
    """Record a failing Hypothesis example as a witness file.

    Fires only for tests under tests/properties/ (this conftest's
    directory scope). The shrunk example is whatever Hypothesis printed
    in the failure — the 'Falsifying example:' block — which replays the
    failure deterministically regardless of seeds or database state.
    """
    if not report.failed:
        return
    text = str(report.longrepr)
    if "Falsifying example" not in text:
        return
    WITNESS_DIR.mkdir(parents=True, exist_ok=True)
    path = _witness_path(node.nodeid)
    # Same second + same test = same stem; keep both, never overwrite.
    serial = 1
    while path.exists():
        serial += 1
        path = path.with_name(f"{path.stem}-{serial}{path.suffix}")
    path.write_text(_witness_text(node.nodeid, text), encoding="utf-8")
