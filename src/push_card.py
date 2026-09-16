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

import yaml
from huggingface_hub import hf_hub_download
from huggingface_hub.errors import (
    EntryNotFoundError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)

import hf_store

CARD = Path(__file__).resolve().parent.parent / "docs" / "DATASET_CARD.md"
FENCE = "---\n"


def front_matter(text: str):
    """Return the parsed front matter, or a reason it cannot be used.

    Checking only that the file starts with a fence would miss the one failure
    the Hub cannot recover from: YAML that breaks below the first line uploads
    happily and then renders as raw text with the viewer erroring.
    """
    parts = text.split(FENCE, 2)
    if not text.startswith(FENCE) or len(parts) < 3:
        return None, "the card has no YAML front matter; the Hub needs it"
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        return None, f"the front matter is not valid YAML: {e}"
    if not isinstance(meta, dict) or not meta.get("configs"):
        return None, "the front matter declares no configs; the viewer needs them"
    return meta, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    text = CARD.read_text(encoding="utf-8")
    meta, problem = front_matter(text)
    if problem:
        print(problem, file=sys.stderr)
        return 2

    api = hf_store._api()
    try:
        current = hf_hub_download(
            repo_id=hf_store.REPO_ID, filename="README.md",
            repo_type=hf_store.REPO_TYPE, token=api.token)
        if Path(current).read_text(encoding="utf-8") == text:
            print("card is already up to date")
            return 0
    except (EntryNotFoundError, RepositoryNotFoundError, HfHubHTTPError, OSError):
        pass  # no card there yet, or it cannot be read; push and find out

    if args.dry_run:
        configs = [c.get("config_name") for c in meta["configs"]]
        print(f"would push {len(text)} chars to {hf_store.REPO_ID}/README.md "
              f"(configs: {', '.join(configs)})")
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
