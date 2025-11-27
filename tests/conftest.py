import sys
from pathlib import Path

# Ensure repository root (where examples/ lives) is on sys.path for test imports.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
