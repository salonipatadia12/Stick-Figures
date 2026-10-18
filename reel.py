#!/usr/bin/env python3
"""Repository-root entry point for the reusable reel builder."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'outputs' / 'the-message-source'))
from builder.cli import main
if __name__ == '__main__':
    raise SystemExit(main())
