"""Independent oracles for package D (denominators, optional stages).

Every oracle here is written from the DOCUMENTED contract (docs/SAS_PARITY.md,
docs/PARITY_FINDINGS.md, docs/OUTPUTS.md and the SAS line references quoted
in the shipped SQL comments), not from the SQL text. Nothing in this package
executes or copies a shipped query; date arithmetic is done by day
enumeration over small domains, so no span/gap expression of the
implementation can be mirrored by accident.
"""
