"""Uplift agent plane — definitions as code, behind a swappable runtime adapter.

Managed Agents (beta) runs the reasoning loop; your VPC runs the tools. Nothing in this package
creates real Anthropic resources on import — the real runtime is constructed lazily and only the
FakeRuntime is exercised in tests.
"""
