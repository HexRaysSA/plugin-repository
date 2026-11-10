#!/usr/bin/env python3
"""Add the archive URLs that the indexer rejected to ignored-urls.txt.

For each archive with no valid plugin, `hcli plugin --repo github ... repo snapshot` logs a
structured entry (with HCLI_DEBUG set):

    no valid plugin in archive: <url> <structured: {"message": ..., "url": <url>, ...}>

This script reads these entries from the hcli log and appends the URLs that are not in the
list yet, under a "# Discovered on" line.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

MESSAGE = "no valid plugin in archive"


def squash(s: str) -> str:
    return "".join(s.split())


def get_rejected_urls(log: str) -> set[str]:
    urls = set()
    for match in re.finditer(r"<structured:\s*(\{.*?\})>", log, re.DOTALL):
        # rich wraps long log lines, also in the middle of a URL, so remove all whitespace.
        # The URLs contain none, so they come out intact; the messages lose their spaces.
        try:
            data = json.loads(squash(match.group(1)))
        except json.JSONDecodeError:
            continue
        if data.get("message", "").startswith(squash(MESSAGE)) and "url" in data:
            urls.add(data["url"])
    return urls


def get_listed_urls(text: str) -> set[str]:
    lines = (line.strip() for line in text.splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", type=Path, help="log of the hcli snapshot")
    parser.add_argument("ignored_urls", type=Path, help="path to ignored-urls.txt")
    args = parser.parse_args()

    text = args.ignored_urls.read_text(encoding="utf-8")
    new_urls = sorted(get_rejected_urls(args.log.read_text(encoding="utf-8")) - get_listed_urls(text))
    if not new_urls:
        print("No new ignored URLs found")
        return

    print(f"Found {len(new_urls)} new ignored URLs to add:")
    for url in new_urls:
        print(f"  {url}")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with args.ignored_urls.open("a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        f.write(f"\n# Discovered on {timestamp}\n")
        f.write("".join(f"{url}\n" for url in new_urls))


if __name__ == "__main__":
    main()
