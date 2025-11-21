#!/usr/bin/env python3
"""Parse HCLI GitHub indexing logs and display results hierarchically.

This script parses structured logging output from the HCLI GitHub indexing
process, organizing messages by repository, release/tag, archive URL, and
metadata paths.
"""
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "requests",
#   "rich",
# ]
# ///

import argparse
import json
import os
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import requests
from rich.console import Console
from rich.table import Table


@dataclass
class LogMessage:
    """A single log message with its metadata."""

    message: str
    data: dict[str, Any]
    level: str = "DEBUG"

    @property
    def is_error(self) -> bool:
        return "error" in self.data

    @property
    def is_success(self) -> bool:
        msg_lower = self.message.lower()
        return "found valid" in msg_lower and "failed" not in msg_lower and not self.is_error


@dataclass
class MetadataPath:
    """Represents a metadata file found in an archive."""

    path: str
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class Archive:
    """Represents an archive (zip/tarball) being indexed."""

    url: str
    archive_type: str
    metadata_paths: dict[str, MetadataPath] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class ReleaseOrTag:
    """Represents a release, tag, or commit."""

    identifier: str
    ref_type: str
    date: str | None = None
    archives: dict[str, Archive] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class Repository:
    """Represents a GitHub repository."""

    owner: str
    repo: str
    releases: dict[str, ReleaseOrTag] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


class LogParser:
    """Parser for HCLI GitHub indexing logs."""

    def __init__(self):
        self.repos: dict[tuple[str, str], Repository] = {}
        self.repos_from_github: set[str] = set()
        self.repos_from_known_file: set[str] = set()
        self.repos_ignored: set[str] = set()
        self.repos_with_successful_plugins: set[str] = set()

    def parse_file(self, log_path: Path) -> None:
        """Parse a log file and build the hierarchical structure."""
        content = log_path.read_text()
        self.parse_content(content)

    def parse_content(self, content: str) -> None:
        """Parse log content string and build the hierarchical structure."""
        pattern = r"<structured:\s*(\{[^>]+\})>"
        matches = re.findall(pattern, content, re.DOTALL)

        for match in matches:
            json_str = " ".join(match.split())
            try:
                data = json.loads(json_str)
                self._process_log_entry(data)
            except json.JSONDecodeError:
                continue

        self._parse_repo_sources(content)

    def _process_log_entry(self, data: dict[str, Any]) -> None:
        """Process a single log entry and add it to the hierarchy."""
        if "owner" not in data or "repo" not in data:
            return

        owner = data["owner"]
        repo = data["repo"]
        message = data.get("message", "")

        repo_key = (owner, repo)
        if repo_key not in self.repos:
            self.repos[repo_key] = Repository(owner=owner, repo=repo)

        repository = self.repos[repo_key]
        log_msg = LogMessage(message=message, data=data)

        if log_msg.is_success:
            repo_name = f"{owner}/{repo}"
            self.repos_with_successful_plugins.add(repo_name)

        ref_key = self._get_ref_key(data)
        if ref_key:
            ref_identifier, ref_type = ref_key
            if ref_identifier not in repository.releases:
                date = data.get("date")
                repository.releases[ref_identifier] = ReleaseOrTag(identifier=ref_identifier, ref_type=ref_type, date=date)

            release = repository.releases[ref_identifier]
            if release.date is None and "date" in data:
                release.date = data["date"]

            url = data.get("url")
            if url:
                if url not in release.archives:
                    archive_type = data.get("type", "unknown")
                    release.archives[url] = Archive(url=url, archive_type=archive_type)

                archive = release.archives[url]

                path = data.get("path")
                if path:
                    if path not in archive.metadata_paths:
                        archive.metadata_paths[path] = MetadataPath(path=path)

                    metadata = archive.metadata_paths[path]
                    metadata.messages.append(log_msg)
                else:
                    archive.messages.append(log_msg)
            else:
                release.messages.append(log_msg)
        else:
            repository.messages.append(log_msg)

    def _get_ref_key(self, data: dict[str, Any]) -> tuple[str, str] | None:
        """Extract release/tag/commit identifier and type from log data."""
        if "release" in data:
            return (data["release"], "release")
        elif "tag" in data:
            return (data["tag"], "tag")
        elif "commit" in data:
            return (data["commit"], "commit")
        return None

    def _parse_repo_sources(self, content: str) -> None:
        """Parse repository sources from log content."""
        for line in content.split('\n'):
            if "extra repo already found by GitHub index:" in line:
                match = re.search(r'extra repo already found by GitHub index:\s*(\S+)', line)
                if match:
                    repo = match.group(1)
                    self.repos_from_known_file.add(repo)
                    self.repos_from_github.add(repo)
            elif "extra repo not yet found by GitHub index:" in line:
                match = re.search(r'extra repo not yet found by GitHub index:\s*(\S+)', line)
                if match:
                    repo = match.group(1)
                    self.repos_from_known_file.add(repo)
            elif "ignored repo:" in line:
                match = re.search(r'ignored repo:\s*(\S+)', line)
                if match:
                    self.repos_ignored.add(match.group(1))

        for (owner, repo) in self.repos.keys():
            repo_name = f"{owner}/{repo}"
            if repo_name not in self.repos_from_known_file:
                self.repos_from_github.add(repo_name)


def fetch_github_actions_logs(console: Console) -> tuple[str, str | None]:
    """Fetch logs from the most recent sync workflow run in GitHub Actions.

    Returns:
        Tuple of (log content as a string with GitHub Actions prefixes stripped, log URL or None)
    """
    token = os.environ.get('GITHUB_TOKEN')
    if not token:
        print("Error: GitHub token not found. Set GITHUB_TOKEN environment variable.", file=sys.stderr)
        sys.exit(1)

    owner, repo = 'HexRaysSA', 'plugin-repository'

    headers = {
        'Accept': 'application/vnd.github+json',
        'Authorization': f'Bearer {token}',
        'X-GitHub-Api-Version': '2022-11-28'
    }

    api_base = 'https://api.github.com'

    try:
        list_url = f'{api_base}/repos/{owner}/{repo}/actions/runs'
        params = {'per_page': 1, 'status': 'completed'}

        response = requests.get(list_url, headers=headers, params=params)
        response.raise_for_status()

        runs_data = response.json()
        workflow_runs = runs_data.get('workflow_runs', [])

        sync_runs = [run for run in workflow_runs if run.get('path') == '.github/workflows/sync.yml']

        if not sync_runs:
            params['per_page'] = 100
            response = requests.get(list_url, headers=headers, params=params)
            response.raise_for_status()
            runs_data = response.json()
            workflow_runs = runs_data.get('workflow_runs', [])
            sync_runs = [run for run in workflow_runs if run.get('path') == '.github/workflows/sync.yml']

        if not sync_runs:
            print("Error: No sync.yml workflow runs found", file=sys.stderr)
            sys.exit(1)

        run_id = sync_runs[0]['id']
        log_view_url = f'https://github.com/{owner}/{repo}/actions/runs/{run_id}'

        logs_url = f'{api_base}/repos/{owner}/{repo}/actions/runs/{run_id}/logs'
        response = requests.get(logs_url, headers=headers, allow_redirects=True)
        response.raise_for_status()

        zip_data = BytesIO(response.content)
        full_logs = []

        with zipfile.ZipFile(zip_data) as zf:
            for name in zf.namelist():
                with zf.open(name) as f:
                    full_logs.append(f.read().decode('utf-8', errors='replace'))

        full_logs_text = '\n'.join(full_logs)

        lines = full_logs_text.split('\n')
        cleaned_lines = []
        in_snapshot = False

        gh_prefix_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}T[\d:.]+Z ')

        for line in lines:
            if 'Run uvx --from ida-hcli' in line or 'run: uvx --from ida-hcli' in line:
                in_snapshot = True

            if in_snapshot:
                cleaned_line = gh_prefix_pattern.sub('', line)
                cleaned_lines.append(cleaned_line)

            if in_snapshot and ('Run git config --global user.email' in line or 'run: git config --global user.email' in line):
                break

        result_lines = []
        for line in cleaned_lines:
            if line and line[0] == ' ' and result_lines:
                result_lines[-1] += line.lstrip()
            else:
                result_lines.append(line)

        return '\n'.join(result_lines), log_view_url

    except requests.exceptions.RequestException as e:
        print(f"Error fetching GitHub Actions logs: {e}", file=sys.stderr)
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response status: {e.response.status_code}", file=sys.stderr)
            print(f"Response body: {e.response.text[:500]}", file=sys.stderr)
        sys.exit(1)


class LogRenderer:
    """Renders parsed logs in a hierarchical format."""

    def __init__(self, parser: LogParser, console: Console, log_url: str | None = None):
        self.parser = parser
        self.console = console
        self.log_url = log_url

    def render(self) -> None:
        """Render all repositories and their data."""
        repos = sorted(self.parser.repos.values(), key=lambda r: (r.owner, r.repo))

        self.console.print(f"\n{'=' * 80}")
        self.console.print("GitHub Indexing Log Analysis")
        self.console.print(f"{'=' * 80}")

        if self.log_url:
            self.console.print(f"Log URL: [link={self.log_url}]{self.log_url}[/link]")

        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self.console.print(f"Generated at: {current_time}\n")

        self.console.print(f"Total repositories: {len(repos)}\n")

        self._render_repo_sources_table()

        for repo in repos:
            self._render_repository(repo)

    def _render_repo_sources_table(self) -> None:
        """Render a table showing repository sources."""
        all_repos = (
            self.parser.repos_from_github |
            self.parser.repos_from_known_file |
            self.parser.repos_ignored
        )

        if not all_repos:
            return

        repo_data = []
        for repo in sorted(all_repos):
            in_github = "✓" if repo in self.parser.repos_from_github else ""
            in_known = "✓" if repo in self.parser.repos_from_known_file else ""
            in_ignored = "✓" if repo in self.parser.repos_ignored else ""
            has_plugins = "✓" if repo in self.parser.repos_with_successful_plugins else ""
            repo_data.append((repo, in_github, in_known, in_ignored, has_plugins))

        table = Table(title="Repository Sources")
        table.add_column("Repository", style="cyan", no_wrap=True)
        table.add_column("GitHub Indexer", justify="center", style="green")
        table.add_column("Known Repo", justify="center", style="yellow")
        table.add_column("Ignored Repo", justify="center", style="red")
        table.add_column("Plugins Found?", justify="center", style="bright_green")

        for repo, in_github, in_known, in_ignored, has_plugins in repo_data:
            table.add_row(repo, in_github, in_known, in_ignored, has_plugins)

        self.console.print()
        self.console.print(table)
        self.console.print()

        self.console.print(f"Summary:")
        self.console.print(f"  Repositories from GitHub indexer: {len(self.parser.repos_from_github)}")
        self.console.print(f"  Repositories from known file: {len(self.parser.repos_from_known_file)}")
        self.console.print(f"  Repositories ignored: {len(self.parser.repos_ignored)}")
        self.console.print(f"  Repositories with successful plugin extractions: {len(self.parser.repos_with_successful_plugins)}")
        self.console.print()

    def _render_repository(self, repo: Repository) -> None:
        """Render a single repository and all its releases."""
        self.console.print(f"\n[yellow]{repo.owner}/{repo.repo}[/yellow]")
        self.console.print(f"{'-' * 80}")

        if repo.messages:
            self.console.print("  Repository-level messages:")
            for msg in repo.messages:
                self._render_message(msg, indent=4)

        if not repo.releases:
            self.console.print("  No releases/tags found")
            return

        sorted_releases = sorted(
            repo.releases.values(),
            key=lambda r: (r.date or "", r.identifier)
        )
        for release in sorted_releases:
            self._render_release(release)

    def _render_release(self, release: ReleaseOrTag) -> None:
        """Render a release/tag and all its archives."""
        date_str = f" ([default]{release.date}[/default])" if release.date else ""
        self.console.print(f"\n  {release.ref_type} [blue]{release.identifier}[/blue]{date_str}:")

        if release.messages:
            for msg in release.messages:
                self._render_message(msg, indent=4)

        for archive in release.archives.values():
            self._render_archive(archive)

    def _render_archive(self, archive: Archive) -> None:
        """Render an archive and all its metadata paths."""
        self.console.print(f"\n    Archive ({archive.archive_type}):")
        self.console.print(f"      URL: [default]{archive.url}[/default]")

        if archive.messages:
            for msg in archive.messages:
                self._render_message(msg, indent=6)

        if archive.metadata_paths:
            for metadata in archive.metadata_paths.values():
                self._render_metadata_path(metadata)

    def _render_metadata_path(self, metadata: MetadataPath) -> None:
        """Render a metadata path and all its messages."""
        self.console.print(f"\n      Metadata: {metadata.path}")

        errors = [m for m in metadata.messages if m.is_error]
        successes = [m for m in metadata.messages if m.is_success]
        other = [m for m in metadata.messages if not m.is_error and not m.is_success]

        if successes:
            self.console.print("        ✓ [green]Successes[/green]:")
            for msg in successes:
                self._render_message(msg, indent=10)

        if errors:
            self.console.print("        ✗ [red]Errors[/red]:")
            for msg in errors:
                self._render_message(msg, indent=10)

        if other:
            self.console.print("        • Other:")
            for msg in other:
                self._render_message(msg, indent=10)

    def _render_message(self, msg: LogMessage, indent: int = 0) -> None:
        """Render a single log message."""
        prefix = " " * indent
        self.console.print(f"{prefix}• [default]{msg.message.replace('skipping', '[red]skipping[/red]')}[/default]")

        if msg.is_error and "error" in msg.data:
            error_text = msg.data["error"]
            for line in error_text.split("\n"):
                if line.strip():
                    self.console.print(f"{prefix}  [default]{line}[/default]")

        interesting_keys = [
            "plugin_name",
            "plugin_version",
            "asset",
        ]
        for key in interesting_keys:
            if key in msg.data:
                self.console.print(f"{prefix}  {key}: [green]{msg.data[key]}[/green]")


def main() -> None:
    arg_parser = argparse.ArgumentParser(
        description="Parse HCLI GitHub indexing logs and display results hierarchically."
    )
    arg_parser.add_argument(
        "log_file",
        nargs="?",
        help="Path to log file (if not specified, fetches from GitHub Actions)"
    )
    arg_parser.add_argument(
        "--html",
        action="store_true",
        help="Export output as HTML to stdout"
    )
    args = arg_parser.parse_args()

    if args.html:
        buffer = StringIO()
        console = Console(file=buffer, record=True)
    else:
        console = Console()

    log_parser = LogParser()
    log_url = None

    if not args.log_file:
        print("No log file specified. Fetching logs from most recent GitHub Actions run...", file=sys.stderr)
        log_content, log_url = fetch_github_actions_logs(console)
        log_parser.parse_content(log_content)
    else:
        log_path = Path(args.log_file)
        if not log_path.exists():
            print(f"Error: File not found: {log_path}", file=sys.stderr)
            sys.exit(1)
        log_parser.parse_file(log_path)

    renderer = LogRenderer(log_parser, console, log_url)
    renderer.render()

    if args.html:
        html_output = console.export_html()
        sys.stdout.write(html_output)


if __name__ == "__main__":
    main()
