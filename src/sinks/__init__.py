"""Output sinks: places we send filtered events after the local store updates.

A sink is one-way (write-only) by contract. The aggregator never reads back
from a sink, so a sink failure can never corrupt our state — it just logs
and we try again next run.
"""
