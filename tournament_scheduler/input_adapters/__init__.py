"""Season-input adapters implementing :class:`SeasonInputPort` (#958).

The RVV ``input.xlsx`` workbook is one adapter; a local CSV/JSON bundle is
another. They share one normalizer so the normalized problem has identical
semantics whatever the origin. No adapter reads ``cwd``, Git or a fixed path:
the document location is always injected.
"""

from .local import LocalBundleAdapter
from .normalize import normalize_raw_config, to_legacy_config
from .workbook import WorkbookSeasonInputAdapter

__all__ = [
    "LocalBundleAdapter",
    "WorkbookSeasonInputAdapter",
    "normalize_raw_config",
    "to_legacy_config",
]
