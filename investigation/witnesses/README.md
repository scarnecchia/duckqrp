# Witnesses

One self-contained text file per falsifying Hypothesis example, written
automatically by `pytest_exception_interact` in
`tests/properties/conftest.py` for any failing property under
`tests/properties/`. Files are committed here as campaign evidence once
the finding they support is confirmed.

Each file records the test node id, timestamp, active profile, hypothesis
version, a reproduce command line, and the verbatim shrunk example
("Falsifying example" block). Reproduce from the FILE, not from a saved
seed: strategy changes silently change what a seed generates, while the
shrunk example in a witness keeps replaying.

This directory intentionally starts empty — an empty witness list is a
clean bill of health, not a missing artifact.
