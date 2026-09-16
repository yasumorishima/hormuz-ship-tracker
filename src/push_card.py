"""Publish docs/DATASET_CARD.md as the Hub dataset's README.

Anyone who finds the dataset before the repository needs to be told, in the
place they are standing, that the rows are a sample and not a track. Keeping
the card in git and pushing it from here means the warning cannot drift away
from the code that makes it true.

  python src/push_card.py --dry-run
"""

import argparse
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

import hf_store

CARD = Path(__file__).resolve().parent.parent / "docs" / "DATASET_CARD.md"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    text = CARD.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        print("the card has no YAML front matter; the Hub needs it", file=sys.stderr)
        return 2

    api = hf_store._api()
    try:
        current = hf_hub_download(
            repo_id=hf_store.REPO_ID, filename="README.md",
            repo_type=hf_store.REPO_TYPE, token=api.token)
        if Path(current).read_text(encoding="utf-8") == text:
            print("card is already up to date")
            return 0
    except Exception:
        pass  # no card there yet, or it cannot be read; push and find out

    if args.dry_run:
        print(f"would push {len(text)} chars to {hf_store.REPO_ID}/README.md")
        return 0

    api.upload_file(
        path_or_fileobj=text.encode("utf-8"),
        path_in_repo="README.md",
        repo_id=hf_store.REPO_ID,
        repo_type=hf_store.REPO_TYPE,
        commit_message="update the dataset card from docs/DATASET_CARD.md",
    )
    print(f"pushed {len(text)} chars to {hf_store.REPO_ID}/README.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
