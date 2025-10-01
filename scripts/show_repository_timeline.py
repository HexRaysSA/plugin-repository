#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "GitPython>=3.1.0",
# ]
# ///
"""Generate a Markdown timeline of IDA Pro plugin repository changes.

This script examines git commits that modify the plugin repository JSON file
and generates a Markdown document showing the timeline of changes.
"""

import json
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple, Any, Optional

try:
    import git
except ImportError:
    print("Error: GitPython library is required. Install with: pip install GitPython")
    sys.exit(1)


def get_time_group(commit_date: datetime, now: datetime) -> str:
    """Determine which time group a commit belongs to.

    Args:
        commit_date: The commit's datetime
        now: Current datetime

    Returns:
        Time group name
    """
    diff = now - commit_date

    if diff <= timedelta(days=1):
        return "Within the last day"
    elif diff <= timedelta(days=7):
        return "Within the last week"
    elif diff <= timedelta(days=30):
        return "Within the last month"
    elif diff <= timedelta(days=90):
        return "Within the last three months"
    elif diff <= timedelta(days=365):
        return "Within the last year"
    else:
        return "Over a year ago"


def group_commits_by_time(commits: List[Tuple[str, datetime, str, str, str]]) -> Dict[str, List[Tuple[str, datetime, str, str, str]]]:
    """Group commits by time periods.

    Args:
        commits: List of commit tuples with author info

    Returns:
        Dictionary mapping time group names to commit lists
    """
    now = datetime.now(commits[0][1].tzinfo) if commits else datetime.now()
    groups = {}

    for commit in commits:
        group = get_time_group(commit[1], now)
        if group not in groups:
            groups[group] = []
        groups[group].append(commit)

    # Return groups in desired order
    ordered_groups = {}
    group_order = [
        "Within the last day",
        "Within the last week",
        "Within the last month",
        "Within the last three months",
        "Within the last year",
        "Over a year ago"
    ]

    for group_name in group_order:
        if group_name in groups:
            ordered_groups[group_name] = groups[group_name]

    return ordered_groups


def clean_github_url(url: str) -> str:
    """Clean GitHub URL to show only org/repo.

    Args:
        url: Full GitHub URL

    Returns:
        Cleaned org/repo string
    """
    if url.startswith("https://github.com/"):
        return url[19:]  # Remove "https://github.com/"
    return url


def get_json_modifying_commits(repo: git.Repo) -> List[Tuple[str, datetime, str, str, str]]:
    """Get all commits that modify plugin-repository.json in reverse chronological order.

    Args:
        repo: Git repository object

    Returns:
        List of (commit_hash, datetime, date_string, message, author) tuples
    """
    commits = []

    # Get all commits that modify the plugin-repository.json file
    for commit in repo.iter_commits(paths='plugin-repository.json'):
        commit_hash = commit.hexsha
        commit_datetime = commit.committed_datetime
        date_string = commit_datetime.strftime('%Y-%m-%d %H:%M:%S')
        message = commit.message.strip().split('\n')[0]  # Take only first line
        author = f"{commit.author.name} <{commit.author.email}>"
        commits.append((commit_hash, commit_datetime, date_string, message, author))

    return commits


def get_file_content_at_commit(repo: git.Repo, commit_hash: str, file_path: str) -> Optional[Dict]:
    """Get the content of a file at a specific commit.

    Args:
        repo: Git repository object
        commit_hash: Git commit hash
        file_path: Path to the file

    Returns:
        Parsed JSON content or None if file doesn't exist
    """
    try:
        commit = repo.commit(commit_hash)
        blob = commit.tree / file_path
        content = blob.data_stream.read().decode('utf-8')
        return json.loads(content)
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def extract_plugin_info(data: Dict) -> Dict[str, Dict]:
    """Extract plugin information from repository data.

    Args:
        data: Repository JSON data

    Returns:
        Dictionary mapping plugin names to their info
    """
    plugins = {}
    if not data or 'plugins' not in data:
        return plugins

    for plugin in data['plugins']:
        name = plugin['name']
        plugins[name] = {
            'host': plugin['host'],
            'versions': set(plugin['versions'].keys()) if 'versions' in plugin else set(),
            'version_details': {}
        }

        # Extract detailed info for each version to detect metadata changes
        if 'versions' in plugin and plugin['versions']:
            for version, releases in plugin['versions'].items():
                if releases and isinstance(releases, list):
                    release_info = releases[0]  # Take first release of this version

                    # Create a hash of the version content to detect changes
                    version_hash = hash(json.dumps(release_info, sort_keys=True))
                    plugins[name]['version_details'][version] = {
                        'hash': version_hash,
                        'has_metadata': bool(release_info.get('metadata')),
                        'sha256': release_info.get('sha256', ''),
                        'url': release_info.get('url', '')
                    }

                    # Also store metadata for detailed comparison
                    if release_info.get('metadata'):
                        metadata = release_info['metadata'].get('plugin', {})
                        plugins[name]['version_details'][version]['metadata'] = {
                            'description': metadata.get('description', ''),
                            'authors': [a.get('name', '') for a in metadata.get('authors', [])],
                            'categories': metadata.get('categories', []),
                            'license': metadata.get('license', ''),
                            'platforms': metadata.get('platforms', [])
                        }

    return plugins


def compare_plugins(old_data: Optional[Dict], new_data: Optional[Dict]) -> List[str]:
    """Compare plugin data between two commits and generate change descriptions.

    Args:
        old_data: Previous commit's plugin data
        new_data: Current commit's plugin data

    Returns:
        List of human-readable change descriptions
    """
    changes = []

    if old_data is None and new_data is None:
        return changes

    old_plugins = extract_plugin_info(old_data) if old_data else {}
    new_plugins = extract_plugin_info(new_data) if new_data else {}

    old_names = set(old_plugins.keys())
    new_names = set(new_plugins.keys())

def format_change_line(symbol: str, symbol_type: str, plugin_name: str, details: str, plugin_url: str = "") -> str:
    """Format a change line using Markdown syntax.

    Args:
        symbol: The +, -, or ~ symbol
        symbol_type: Type of change (add, remove, modify)
        plugin_name: Name of the plugin
        details: Additional details about the change
        plugin_url: URL to the plugin repository

    Returns:
        Formatted string with Markdown syntax
    """
    # Use subtle Unicode symbols for different change types
    if symbol_type == "add":
        emoji_symbol = "(+)"
    elif symbol_type == "remove":
        emoji_symbol = "(−)"  # En dash (U+2212), not hyphen
    elif symbol_type == "modify":
        emoji_symbol = "(~)"
    else:
        emoji_symbol = f"({symbol})"

    # Make plugin name a link if URL provided
    if plugin_url:
        formatted_plugin = f"[**{plugin_name}**]({plugin_url})"
    else:
        formatted_plugin = f"**{plugin_name}**"

    # Handle details with repository info
    if details and "(" in details and ")" in details:
        # Extract parts before, within, and after parentheses
        before_paren = details[:details.find("(")]
        paren_start = details.find("(")
        paren_end = details.find(")", paren_start) + 1
        paren_content = details[paren_start+1:paren_end-1]  # Remove parentheses for URL construction
        after_paren = details[paren_end:]

        # Create repository URL from the cleaned content
        repo_url = f"https://github.com/{paren_content}" if paren_content else ""

        if repo_url:
            formatted_repo = f"([{paren_content}]({repo_url}))"
        else:
            formatted_repo = f"({paren_content})"

        return f"- {emoji_symbol} {formatted_plugin}{before_paren}{formatted_repo}{after_paren}"
    else:
        if details:
            return f"- {emoji_symbol} {formatted_plugin}{details}"
        else:
            return f"- {emoji_symbol} {formatted_plugin}"


def compare_plugins(old_data: Optional[Dict], new_data: Optional[Dict]) -> List[str]:
    """Compare plugin data between two commits and generate change descriptions.

    Args:
        old_data: Previous commit's plugin data
        new_data: Current commit's plugin data

    Returns:
        List of formatted change descriptions
    """
    changes = []

    if old_data is None and new_data is None:
        return changes

    old_plugins = extract_plugin_info(old_data) if old_data else {}
    new_plugins = extract_plugin_info(new_data) if new_data else {}

    old_names = set(old_plugins.keys())
    new_names = set(new_plugins.keys())

    # New plugins
    added_plugins = new_names - old_names
    for name in sorted(added_plugins):
        plugin = new_plugins[name]
        versions = sorted(plugin['versions'], reverse=True)  # Greatest to least
        host_short = clean_github_url(plugin['host'])
        details = f" ({host_short})"
        plugin_url = plugin['host']
        changes.append(format_change_line("+", "add", name, details, plugin_url))

        # Add version list as nested Markdown list
        for version in versions:
            changes.append(f"  - {version}")

    # Removed plugins
    removed_plugins = old_names - new_names
    for name in sorted(removed_plugins):
        changes.append(format_change_line("-", "remove", name, "", ""))

    # Modified plugins
    common_plugins = old_names & new_names
    for name in sorted(common_plugins):
        old_plugin = old_plugins[name]
        new_plugin = new_plugins[name]

        plugin_changes = []

        # Version changes
        old_versions = old_plugin['versions']
        new_versions = new_plugin['versions']

        added_versions = new_versions - old_versions
        removed_versions = old_versions - new_versions

        if added_versions:
            plugin_changes.append(f"added version(s): {', '.join(sorted(added_versions))}")

        if removed_versions:
            plugin_changes.append(f"removed version(s): {', '.join(sorted(removed_versions))}")

        # Host changes
        if old_plugin['host'] != new_plugin['host']:
            old_host_short = clean_github_url(old_plugin['host'])
            new_host_short = clean_github_url(new_plugin['host'])
            plugin_changes.append(f"host changed: {old_host_short} → {new_host_short}")

        # Version detail changes (metadata, hashes, URLs) - group by version
        common_versions = old_versions & new_versions
        version_changes = {}  # Dict to group changes by version

        for version in sorted(common_versions):
            old_details = old_plugin['version_details'].get(version, {})
            new_details = new_plugin['version_details'].get(version, {})

            version_specific_changes = []

            # Check if the version content changed (different hash)
            if old_details.get('hash') != new_details.get('hash'):
                # Check if metadata was added
                if not old_details.get('has_metadata') and new_details.get('has_metadata'):
                    version_specific_changes.append("metadata added")
                elif old_details.get('has_metadata') and not new_details.get('has_metadata'):
                    version_specific_changes.append("metadata removed")
                elif old_details.get('has_metadata') and new_details.get('has_metadata'):
                    # Both have metadata, check what changed
                    old_meta = old_details.get('metadata', {})
                    new_meta = new_details.get('metadata', {})

                    if old_meta != new_meta:
                        meta_changes = []
                        if old_meta.get('description') != new_meta.get('description'):
                            meta_changes.append("description")
                        if old_meta.get('authors') != new_meta.get('authors'):
                            meta_changes.append("authors")
                        if old_meta.get('license') != new_meta.get('license'):
                            meta_changes.append("license")
                        if set(old_meta.get('platforms', [])) != set(new_meta.get('platforms', [])):
                            meta_changes.append("platforms")
                        if set(old_meta.get('categories', [])) != set(new_meta.get('categories', [])):
                            meta_changes.append("categories")

                        if meta_changes:
                            version_specific_changes.append(f"metadata updated ({', '.join(meta_changes)})")

                # Check SHA256 changes
                if old_details.get('sha256') != new_details.get('sha256'):
                    version_specific_changes.append("archive contents changed")

                # Check URL changes
                if old_details.get('url') != new_details.get('url'):
                    version_specific_changes.append("download URL changed")

            if version_specific_changes:
                version_changes[version] = version_specific_changes

        # Add other non-version-specific changes
        if added_versions:
            plugin_changes.append(f"added version(s): {', '.join(sorted(added_versions))}")

        if removed_versions:
            plugin_changes.append(f"removed version(s): {', '.join(sorted(removed_versions))}")

        # Output format: plugin -> version -> changes
        if version_changes or plugin_changes:
            host_short = clean_github_url(new_plugin['host'])
            details = f" ({host_short})"
            plugin_url = new_plugin['host']
            changes.append(format_change_line("~", "modify", name, details, plugin_url))

            # Add non-version-specific changes first
            for change in plugin_changes:
                changes.append(f"  - {change}")

            # Add version-specific changes grouped by version
            for version in sorted(version_changes.keys(), reverse=True):  # Newest first
                changes.append(f"  - **{version}**")
                for change in version_changes[version]:
                    changes.append(f"    - {change}")

    return changes


def analyze_repository_timeline(repo_path: str = '.'):
    """Generate a Markdown timeline of plugin repository changes.

    Args:
        repo_path: Path to the Git repository
    """
    print("# Timeline: IDA Pro plugin repository")
    print()

    try:
        repo = git.Repo(repo_path)
    except git.exc.InvalidGitRepositoryError:
        print(f"**Error:** '{repo_path}' is not a valid Git repository")
        return

    commits = get_json_modifying_commits(repo)
    if not commits:
        print("No commits found that modify plugin-repository.json.")
        return

    # Group commits by time periods
    grouped_commits = group_commits_by_time(commits)

    # Process commits in reverse chronological order for comparison
    all_commits_data = []
    prev_data = None

    # First, collect all data (process oldest to newest for comparison)
    for commit_hash, commit_datetime, date_string, message, author in reversed(commits):
        current_data = get_file_content_at_commit(repo, commit_hash, 'plugin-repository.json')
        changes = compare_plugins(prev_data, current_data) if current_data else []

        all_commits_data.append((commit_hash, commit_datetime, date_string, message, author, current_data, changes))
        prev_data = current_data

    # Now display by time groups (newest first)
    all_commits_data.reverse()  # Back to newest first
    commit_data_map = {commit[0]: commit for commit in all_commits_data}

    for group_name, group_commits in grouped_commits.items():
        print(f"## {group_name}")
        print()

        for commit_hash, commit_datetime, date_string, message, author in group_commits:
            commit_data = commit_data_map[commit_hash]
            _, _, _, _, _, current_data, changes = commit_data

            # Extract author name (without email)
            author_name = author.split('<')[0].strip()

            # Format commit line with Markdown link
            commit_url = f"https://github.com/HexRaysSA/plugin-repository/commit/{commit_hash}"
            print(f"### {date_string} - [{commit_hash[:8]}]({commit_url}) - {author_name}: {message}")
            print()

            if current_data is None:
                print("⚠️ Could not read plugin-repository.json at this commit")
                print()
                continue

            if changes:
                for change in changes:
                    print(change)
                print()
            else:
                # Check if this is the first commit (no previous data to compare)
                is_first = all(c[6] == [] for c in all_commits_data if c[1] < commit_datetime)
                if is_first and current_data:
                    plugins = extract_plugin_info(current_data)
                    print(f"Initial repository with **{len(plugins)}** plugins")
                else:
                    print("No plugin changes detected")
                print()

        print()


if __name__ == "__main__":
    try:
        repo_path = sys.argv[1] if len(sys.argv) > 1 else '.'
        analyze_repository_timeline(repo_path)
    except git.exc.GitError as e:
        print(f"Git error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)