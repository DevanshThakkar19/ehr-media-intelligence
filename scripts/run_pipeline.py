"""CLI entry to run the full pipeline without starting the web server."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.api.main import run_pipeline  # noqa: E402


def main() -> None:
    result = run_pipeline(reindex=True)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
