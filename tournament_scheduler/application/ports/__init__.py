"""Versioned, backend-neutral contracts between the application and its I/O.

These modules are *contracts only*: frozen DTOs, enums, errors and structural
``Protocol`` ports. They contain no planning, verification or maintenance
policy and import nothing from Git, HTTP, the CLI, rendering or the RVV
filesystem layout. Adapters (the legacy Git-backed RVV store, workbook import,
the live calendar scraper, Pages publication) implement them; the application
receives them by injection. See ADR 0009.
"""

from .common import (
    PORT_CONTRACT_VERSION,
    PortCapabilities,
    PortError,
    PortErrorCode,
    SchemaCompatibility,
    SourceKind,
    SourceRef,
)

__all__ = [
    "PORT_CONTRACT_VERSION",
    "PortCapabilities",
    "PortError",
    "PortErrorCode",
    "SchemaCompatibility",
    "SourceKind",
    "SourceRef",
]
