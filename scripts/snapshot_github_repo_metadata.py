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


def do_snapshot(json_path: Path, out_path: Path, token: str):
    repo = JSONFilePluginRepo.from_file(json_path)
    plugins = repo.get_plugins()

    seen_repos = set()

    for plugin in rich.progress.track(
        plugins, description="Fetching repo metadata", transient=True, console=stderr_console
    ):
        plugin_url = plugin.host

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

        logger.debug("fetching metadata: %s/%s", org, repo_name)

        try:
            metadata = get_github_repo_metadata(org, repo_name, token)

            destination_path.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")

            logger.debug("wrote: %s (stars: %d)", metadata_path, metadata.get("stargazers_count", 0))
        except requests.exceptions.HTTPError as e:
            logger.error("failed to fetch metadata for %s/%s: %s", org, repo_name, e)
            continue


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
    parser.add_argument("--verbose", action="store_true", help="enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )

    if not args.plugin_repo_json.exists():
        raise ValueError("`plugin-repo.json` does not exist")

    if not args.output_path.exists():
        raise ValueError("output-path does not exist")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN environment variable is not set")

    do_snapshot(args.plugin_repo_json, args.output_path, token)


if __name__ == "__main__":
    main()
