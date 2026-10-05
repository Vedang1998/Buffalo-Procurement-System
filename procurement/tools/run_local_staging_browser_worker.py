#!/usr/bin/env python3
"""Reserved isolated worker for the LOCAL staging browser acceptance.

This artifact is intentionally inert until the supervisor can prove the full
source, dependency, runtime, and containment envelope before READY.  Keeping
the worker in a separate file lets the supervisor pin its exact bytes without
a circular self-hash.
"""

from __future__ import annotations

import sys


def main(arguments: list[str] | None = None) -> int:
    """Refuse every invocation until the complete worker gate is assembled."""

    _ = sys.argv[1:] if arguments is None else arguments
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

