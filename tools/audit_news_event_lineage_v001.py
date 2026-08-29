from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.audits.news_event_lineage_review_v001 import main

if __name__ == "__main__":
    main()
