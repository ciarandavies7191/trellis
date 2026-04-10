"""Trellis registry package.

Exports:
  - SchemaRegistry    — named schema lookup for load_schema tool
  - FunctionRegistry  — named deterministic function lookup for compute tool
  - RegisteredFunction — entry type for FunctionRegistry
"""

from .schema import SchemaRegistry
from .functions import FunctionRegistry, RegisteredFunction

__all__ = ["SchemaRegistry", "FunctionRegistry", "RegisteredFunction"]
