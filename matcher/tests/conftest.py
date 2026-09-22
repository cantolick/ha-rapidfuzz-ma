import sys
from pathlib import Path

# Tests import `core`, `catalog`, `app`, etc. as top-level modules, matching
# how the service itself runs (matcher/ as the working directory, no package
# structure) — so matcher/ needs to be on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
