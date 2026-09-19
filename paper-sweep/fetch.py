#!/usr/bin/env python3
"""Compatibility entry point for research-radar collection."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_radar.collection import main

if __name__ == '__main__':
    sys.exit(main())
