"""Package-local oracles for the property investigation.

Each worker owns modules prefixed with its package letter (a_, b_, c_, d_,
e_, f_); nothing here is shared infrastructure. Every oracle is written
from the DOCUMENTED contract (INPUTS.md, docs/, and the SAS line references
quoted in the shipped SQL comments), not from the SQL text: date arithmetic
is done by day enumeration over small domains, so no span/gap expression of
the implementation can be mirrored by accident, and nothing here executes
or copies a shipped query.
"""
