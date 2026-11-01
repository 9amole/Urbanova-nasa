"""Entry point: `python -m urbanova.main` (or `uvicorn urbanova.api:app`)."""

from __future__ import annotations

import logging
import os

import uvicorn


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("URBANOVA_HOST", "127.0.0.1")
    port = int(os.environ.get("URBANOVA_PORT", "8000"))
    uvicorn.run("urbanova.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
