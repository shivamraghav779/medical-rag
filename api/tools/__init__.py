"""LLM-callable clinical tools.

Every function here is a thin, dependency-injected wrapper over the real
business logic living in api/services and api/agents — no new business
logic, no new prompts, nothing wired into the live Orchestrator pipeline.
They exist as ready-to-use, individually callable building blocks for a
future LLM tool-calling agent (not yet built — see project decision to
defer LLM-driven tool selection rather than bundle it into this refactor).
"""
