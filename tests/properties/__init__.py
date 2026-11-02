# Property-based correctness suite (private investigation infrastructure).
#
# This package exists so tests/properties/ can be imported as a module by
# its own conftest, tests, and the harness self-tests. It is deliberately
# separate from the operator suite in tests/: everything here generates its
# own synthetic data, runs the shipped SQL through the production plumbing,
# and records its own witnesses under investigation/witnesses/.
