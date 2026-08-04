# Fetch metadata for GitHub repositories referenced by IDA Pro plugins in the plugin repo.
#
# Example:
#
#     $ export GITHUB_TOKEN=ghp_...
#     $ mkdir output
#     $ uv run scripts/snapshot_github_repo_metadata.py plugin-repository.json output
#     $ cat output/github.com/HexRays-plugin-contributions/capa/metadata.json
#       {"id": 123456, "name": "capa", "full_name": "HexRays-plugin-contributions/capa", "stargazers_count": 42, ...}
#
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ida-hcli",
#     "rich",
#     "requests",
# ]
# ///

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests
import rich.console
import rich.progress
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from rich.logging import RichHandler

logger = logging.getLogger(__name__)

stderr_console = rich.console.Console(stderr=True)


def extract_github_org_repo(url: str) -> tuple[str, str] | None:
    """Extract organization and repository name from GitHub URL.

    Args:
        url: GitHub URL

    Returns:
        Tuple of (org, repo) or None if not a valid GitHub URL
    """
    if not url.startswith("https://github.com/"):
        return None

    parts = url[len("https://github.com/"):].rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return None


def get_github_repo_metadata(org: str, repo: str, token: str) -> dict:
    """Fetch metadata for a GitHub repository.

    Args:
        org: GitHub organization or user
        repo: Repository name
        token: GitHub personal access token

    Returns:
        Repository metadata dict from GitHub API
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    url = f"https://api.github.com/repos/{org}/{repo}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    return response.json()


def load_legacy_plugin_urls(api_path: Path) -> list[str]:
    """Repo URLs from the legacy api-plugins.json dump.

    Legacy plugins are not in the HCLI index, so without this their repos get
    no GitHub metadata at all — no stars, no owner avatar, and no owner type
    (the UI then can't label publishers as Organization/Individual).
    """
    raw = json.loads(api_path.read_text())
    if "plugins" in raw and "hits" in raw["plugins"]:
        hits = raw["plugins"]["hits"]
    elif "hits" in raw:
        hits = raw["hits"]
    else:
        hits = raw if isinstance(raw, list) else []
    return [p.get("url") or "" for p in hits]


def do_snapshot(json_path: Path, out_path: Path, token: str, api_path: Path | None = None):
    repo = JSONFilePluginRepo.from_file(json_path)
    plugins = repo.get_plugins()

    plugin_urls = [plugin.host for plugin in plugins]
    if api_path:
        plugin_urls += load_legacy_plugin_urls(api_path)

    seen_repos = set()
    all_metadata = {}

    for plugin_url in rich.progress.track(
        plugin_urls, description="Fetching repo metadata", transient=True, console=stderr_console
    ):
        github_info = extract_github_org_repo(plugin_url)
        if not github_info:
            logger.debug("skipping: %s (not a GitHub URL)", plugin_url)
            continue

        org, repo_name = github_info
        repo_key = (org, repo_name)

        if repo_key in seen_repos:
            logger.debug("skipping: %s/%s (already processed)", org, repo_name)
            continue

        seen_repos.add(repo_key)

        destination_path = out_path / "github.com" / org / repo_name
        metadata_path = destination_path / "metadata.json"

        if metadata_path.exists():
            file_age = time.time() - metadata_path.stat().st_mtime
            if file_age < 24 * 60 * 60:
                logger.debug("using cached metadata: %s/%s (age: %.0fh)", org, repo_name, file_age / 3600)
                cached = json.loads(metadata_path.read_text())
                all_metadata[f"{org}/{repo_name}"] = cached
                continue

        logger.debug("fetching metadata: %s/%s", org, repo_name)

        try:
            metadata = get_github_repo_metadata(org, repo_name, token)

            destination_path.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

            full_repo_name = f"{org}/{repo_name}"
            all_metadata[full_repo_name] = metadata

            logger.debug("wrote: %s (stars: %d)", metadata_path, metadata.get("stargazers_count", 0))
        except requests.exceptions.HTTPError as e:
            logger.error("failed to fetch metadata for %s/%s: %s", org, repo_name, e)
            continue

    consolidated_path = out_path / "github.com" / "repositories-metadata.json"
    consolidated_path.parent.mkdir(parents=True, exist_ok=True)
    consolidated_path.write_text(json.dumps(all_metadata, indent=2) + "\n")
    logger.info("wrote consolidated metadata: %s (%d repositories)", consolidated_path, len(all_metadata))


def main() -> None:
    parser = argparse.ArgumentParser(description="")
    parser.add_argument(
        "plugin_repo_json",
        type=Path,
        metavar="plugin-repo.json",
        help="path to `plugin-repo.json`",
    )
    parser.add_argument(
        "output_path", type=Path, metavar="output-path", help="path to output directory"
    )
    parser.add_argument(
        "--api",
        type=Path,
        help="path to the legacy api-plugins.json dump; its repos are snapshotted too",
    )
    parser.add_argument("--verbose", action="store_true", help="enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )

    if not args.plugin_repo_json.exists():
        raise ValueError("`plugin-repo.json` does not exist")

    if args.api and not args.api.exists():
        raise ValueError(f"--api file does not exist: {args.api}")

    if not args.output_path.exists():
        raise ValueError("output-path does not exist")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set")

    do_snapshot(args.plugin_repo_json, args.output_path, token, api_path=args.api)


if __name__ == "__main__":
    main()
