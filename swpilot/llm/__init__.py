"""LLM translation layer (natural language -> command JSON).

Deliberately empty in v0.1. The integration point is fixed: the LLM
produces a JSON document matching ``swpilot.commands.schema.CommandFile``
and everything downstream (validation, macro expansion, execution,
reporting) is already in place and identical for LLM-authored and
hand-written files.
"""
