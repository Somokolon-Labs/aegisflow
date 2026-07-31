"""AegisFlow shared core: config, storage, broker, resilience, chaos, inference."""

from .config import settings

__all__ = ["settings"]
__version__ = settings.version
