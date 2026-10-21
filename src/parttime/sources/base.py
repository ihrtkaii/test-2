"""What every fetcher gets handed besides the network."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..cache import DescCache
from ..models import Job


@dataclass
class Ctx:
    want: Callable[[Job], bool]          # worth a description fetch?
    cache: DescCache
    priority: Callable[[str], bool]      # company name -> priority employer?

    async def describe(self, job: Job, loader) -> None:
        """Fill job.description from the cache, or by awaiting `loader()` (which
        returns text or None) and caching the result. Failures leave it empty."""
        cached = self.cache.get(job)
        if cached is not None:
            job.description = cached
            return
        try:
            text = await loader()
        except Exception:  # noqa: BLE001 - a missing description is not fatal
            return
        if text:
            job.description = text
            self.cache.put(job, text)
