"""Runtime configuration.

Everything is read from environment variables (optionally a local `.env`), with
safe defaults so the application starts without any credentials. None of the
Phase 1 providers that are enabled by default require a secret.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Provider registry, rules and recommendation templates ship inside the package; a deployment can
# point URBANOVA_CONFIG_DIR at its own reviewed copy.
CONFIG_DIR = Path(os.environ.get("URBANOVA_CONFIG_DIR") or Path(__file__).resolve().parent / "config")


def _load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Default: ./data in a source checkout; ~/.urbanova for an installed package (never site-packages).
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("URBANOVA_DATA_DIR")
                                                        or (ROOT / "data" if (ROOT / "pyproject.toml").exists() else Path.home() / ".urbanova")))
    max_upload_mb: float = float(os.environ.get("URBANOVA_MAX_UPLOAD_MB", "25"))
    session_hours: float = float(os.environ.get("URBANOVA_SESSION_HOURS", "12"))
    # Disable all outbound provider calls (tests, offline demos). Analyses that
    # need external data then report "unavailable" instead of failing silently.
    offline: bool = _bool("URBANOVA_OFFLINE", False)
    http_timeout_s: float = float(os.environ.get("URBANOVA_HTTP_TIMEOUT_S", "45"))
    http_max_retries: int = int(os.environ.get("URBANOVA_HTTP_MAX_RETRIES", "3"))
    user_agent: str = os.environ.get(
        "URBANOVA_USER_AGENT", "URBANOVA-Phase1/1.0 (environmental decision-support prototype)"
    )
    workers: int = int(os.environ.get("URBANOVA_WORKERS", "2"))
    # Licence context for provider terms. "non_commercial_evaluation" (default) is this
    # prototype's use; "commercial" disables providers whose free tier forbids commercial use
    # unless their commercial key is configured (e.g. OPEN_METEO_API_KEY).
    usage_context: str = os.environ.get("URBANOVA_USAGE_CONTEXT", "non_commercial_evaluation")
    # Basemap tiles. The default OSM volunteer servers are acceptable for light development and
    # demo use under https://operations.osmfoundation.org/policies/tiles/ — NOT for production;
    # point these at a commercial provider or your own tile server before deployment.
    tile_url: str = os.environ.get("URBANOVA_TILE_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png")
    tile_attribution: str = os.environ.get(
        "URBANOVA_TILE_ATTRIBUTION", '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors')
    tile_max_zoom: int = int(os.environ.get("URBANOVA_TILE_MAX_ZOOM", "19"))

    def __post_init__(self) -> None:
        if self.usage_context not in ("non_commercial_evaluation", "commercial"):
            raise ValueError("URBANOVA_USAGE_CONTEXT must be 'non_commercial_evaluation' or 'commercial'.")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "urbanova.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.cache_dir, self.projects_dir):
            d.mkdir(parents=True, exist_ok=True)


settings = Settings()


def load_config(name: str) -> dict:
    """Load a JSON configuration file from `urbanova/config/` (providers, rules, ...)."""
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)
