#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "GitPython>=3.1.0",
# ]
# ///
"""Summarize changes to plugin-repository.json for commit messages.

This script compares versions of plugin-repository.json and generates
a summary suitable for commit messages.
"""

import argparse
import json
import sys
from typing import Dict, List, Optional

try:
    import git
except ImportError:
    print("Error: GitPython library is required. Install with: pip install GitPython", file=sys.stderr)
    sys.exit(1)


def clean_github_url(url: str) -> str:
    """Clean GitHub URL to show only org/repo.

    Args:
        url: Full GitHub URL

    Returns:
        Cleaned org/repo string
    """
    if url.startswith("https://github.com/"):
        return url[19:]
    return url


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

        if 'versions' in plugin and plugin['versions']:
            for version, releases in plugin['versions'].items():
                if releases and isinstance(releases, list):
                    release_info = releases[0]

                    version_hash = hash(json.dumps(release_info, sort_keys=True))
                    plugins[name]['version_details'][version] = {
                        'hash': version_hash,
                        'has_metadata': bool(release_info.get('metadata')),
                        'sha256': release_info.get('sha256', ''),
                        'url': release_info.get('url', '')
                    }

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
    if symbol_type == "add":
        emoji_symbol = "(+)"
    elif symbol_type == "remove":
        emoji_symbol = "(−)"
    elif symbol_type == "modify":
        emoji_symbol = "(~)"
    else:
        emoji_symbol = f"({symbol})"

    if plugin_url:
        formatted_plugin = f"[**{plugin_name}**]({plugin_url})"
    else:
        formatted_plugin = f"**{plugin_name}**"

    if details and "(" in details and ")" in details:
        before_paren = details[:details.find("(")]
        paren_start = details.find("(")
        paren_end = details.find(")", paren_start) + 1
        paren_content = details[paren_start+1:paren_end-1]
        after_paren = details[paren_end:]

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


def get_file_at_ref(repo: git.Repo, ref: str, file_path: str) -> Optional[Dict]:
    """Get JSON content of a file at a specific git ref.

    Args:
        repo: Git repository object
        ref: Git reference (e.g., 'HEAD', ':0:' for staged, commit hash)
        file_path: Path to the file

    Returns:
        Parsed JSON content or None if file doesn't exist
    """
    try:
        if ref == ':0:':
            content = repo.git.show(f':0:{file_path}')
        else:
            content = repo.git.show(f'{ref}:{file_path}')
        return json.loads(content)
    except (git.exc.GitCommandError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def count_changes(old_data: Optional[Dict], new_data: Optional[Dict]) -> Dict[str, int]:
    """Count different types of changes between two versions.

    Args:
        old_data: Previous version's plugin data
        new_data: Current version's plugin data

    Returns:
        Dictionary with change counts
    """
    counts = {
        'plugins_added': 0,
        'plugins_removed': 0,
        'releases_added': 0,
        'releases_removed': 0,
        'releases_changed': 0
    }

    if old_data is None and new_data is None:
        return counts

    old_plugins = extract_plugin_info(old_data) if old_data else {}
    new_plugins = extract_plugin_info(new_data) if new_data else {}

    old_names = set(old_plugins.keys())
    new_names = set(new_plugins.keys())

    counts['plugins_added'] = len(new_names - old_names)
    counts['plugins_removed'] = len(old_names - new_names)

    for name in new_names - old_names:
        counts['releases_added'] += len(new_plugins[name]['versions'])

    for name in old_names - new_names:
        counts['releases_removed'] += len(old_plugins[name]['versions'])

    common_plugins = old_names & new_names
    for name in common_plugins:
        old_plugin = old_plugins[name]
        new_plugin = new_plugins[name]

        old_versions = old_plugin['versions']
        new_versions = new_plugin['versions']

        added_versions = new_versions - old_versions
        removed_versions = old_versions - new_versions

        counts['releases_added'] += len(added_versions)
        counts['releases_removed'] += len(removed_versions)

        common_versions = old_versions & new_versions
        for version in common_versions:
            old_details = old_plugin['version_details'].get(version, {})
            new_details = new_plugin['version_details'].get(version, {})

            if old_details.get('hash') != new_details.get('hash'):
                counts['releases_changed'] += 1

    return counts


def compare_plugins_for_message(old_data: Optional[Dict], new_data: Optional[Dict]) -> List[str]:
    """Compare plugin data and generate change descriptions for commit message.

    Args:
        old_data: Previous version's plugin data
        new_data: Current version's plugin data

    Returns:
        List of formatted change descriptions
    """
    output = []

    if old_data is None and new_data is None:
        return output

    old_plugins = extract_plugin_info(old_data) if old_data else {}
    new_plugins = extract_plugin_info(new_data) if new_data else {}

    old_names = set(old_plugins.keys())
    new_names = set(new_plugins.keys())

    # Section 1: New plugins
    added_plugins = new_names - old_names
    new_plugin_entries = []
    for name in sorted(added_plugins):
        plugin = new_plugins[name]
        versions = sorted(plugin['versions'], reverse=True)
        plugin_url = plugin['host']
        version_list = ", ".join(versions)
        new_plugin_entries.append(f"- [{name}]({plugin_url}) ({version_list})")

    # Section 2: New releases (for existing plugins)
    new_release_entries = []
    common_plugins = old_names & new_names
    for name in sorted(common_plugins):
        old_plugin = old_plugins[name]
        new_plugin = new_plugins[name]

        old_versions = old_plugin['versions']
        new_versions = new_plugin['versions']
        added_versions = new_versions - old_versions

        if added_versions:
            plugin_url = new_plugin['host']
            version_list = ", ".join(sorted(added_versions, reverse=True))
            new_release_entries.append(f"- [{name}]({plugin_url}): {version_list}")

    # Section 3: Changes (version-specific changes and host changes)
    change_entries = []
    for name in sorted(common_plugins):
        old_plugin = old_plugins[name]
        new_plugin = new_plugins[name]

        plugin_changes = []
        plugin_url = new_plugin['host']

        old_versions = old_plugin['versions']
        new_versions = new_plugin['versions']
        removed_versions = old_versions - new_versions

        # Host changes
        if old_plugin['host'] != new_plugin['host']:
            old_host_short = clean_github_url(old_plugin['host'])
            new_host_short = clean_github_url(new_plugin['host'])
            plugin_changes.append(f"  - host changed: {old_host_short} → {new_host_short}")

        # Removed versions
        if removed_versions:
            version_list = ", ".join(sorted(removed_versions, reverse=True))
            plugin_changes.append(f"  - removed version(s): {version_list}")

        # Version-specific changes (metadata, content, URL)
        common_versions = old_versions & new_versions
        version_changes = {}

        for version in sorted(common_versions):
            old_details = old_plugin['version_details'].get(version, {})
            new_details = new_plugin['version_details'].get(version, {})

            version_specific_changes = []

            if old_details.get('hash') != new_details.get('hash'):
                if not old_details.get('has_metadata') and new_details.get('has_metadata'):
                    version_specific_changes.append("metadata added")
                elif old_details.get('has_metadata') and not new_details.get('has_metadata'):
                    version_specific_changes.append("metadata removed")
                elif old_details.get('has_metadata') and new_details.get('has_metadata'):
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

                if old_details.get('sha256') != new_details.get('sha256'):
                    version_specific_changes.append("archive contents changed")

                if old_details.get('url') != new_details.get('url'):
                    version_specific_changes.append("download URL changed")

            if version_specific_changes:
                version_changes[version] = version_specific_changes

        # Add version-specific changes to plugin changes
        if version_changes:
            for version in sorted(version_changes.keys(), reverse=True):
                changes_str = ", ".join(version_changes[version])
                plugin_changes.append(f"  - {version}: {changes_str}")

        if plugin_changes:
            change_entries.append(f"- [{name}]({plugin_url}):")
            change_entries.extend(plugin_changes)

    # Section 4: Removed plugins
    removed_plugins = old_names - new_names
    removed_plugin_entries = []
    for name in sorted(removed_plugins):
        removed_plugin_entries.append(f"- {name}")

    # Build output with sections
    if new_plugin_entries:
        output.append("## New plugins")
        output.extend(new_plugin_entries)
        output.append("")

    if new_release_entries:
        output.append("## New releases")
        output.extend(new_release_entries)
        output.append("")

    if change_entries:
        output.append("## Changes")
        output.extend(change_entries)
        output.append("")

    if removed_plugin_entries:
        output.append("## Removed plugins")
        output.extend(removed_plugin_entries)
        output.append("")

    # Remove trailing empty line if present
    if output and output[-1] == "":
        output.pop()

    return output


def format_short_message(counts: Dict[str, int]) -> str:
    """Format a short commit subject line from change counts.

    Args:
        counts: Dictionary of change counts

    Returns:
        Formatted subject line
    """
    parts = []

    if counts['plugins_added'] > 0:
        plural = "s" if counts['plugins_added'] != 1 else ""
        parts.append(f"+{counts['plugins_added']} plugin{plural}")

    if counts['plugins_removed'] > 0:
        plural = "s" if counts['plugins_removed'] != 1 else ""
        parts.append(f"-{counts['plugins_removed']} plugin{plural}")

    if counts['releases_added'] > 0:
        plural = "s" if counts['releases_added'] != 1 else ""
        parts.append(f"+{counts['releases_added']} release{plural}")

    if counts['releases_removed'] > 0:
        plural = "s" if counts['releases_removed'] != 1 else ""
        parts.append(f"-{counts['releases_removed']} release{plural}")

    if counts['releases_changed'] > 0:
        parts.append(f"~{counts['releases_changed']} changed")

    if not parts:
        return "sync plugin-repository.json"

    return "sync repo: " + ", ".join(parts)


def summarize_changes(repo_path: str = '.', short: bool = False, at_commit: str = None):
    """Summarize changes to plugin-repository.json.

    Args:
        repo_path: Path to the Git repository
        short: If True, output short subject line; otherwise full markdown
        at_commit: If provided, compare this commit with its parent
    """
    try:
        repo = git.Repo(repo_path)
    except git.exc.InvalidGitRepositoryError:
        print(f"Error: '{repo_path}' is not a valid Git repository", file=sys.stderr)
        sys.exit(1)

    file_path = 'plugin-repository.json'

    if at_commit:
        commit = repo.commit(at_commit)

        if len(commit.parents) == 0:
            old_data = None
        else:
            parent_commit = commit.parents[0]
            old_data = get_file_at_ref(repo, parent_commit.hexsha, file_path)

        new_data = get_file_at_ref(repo, commit.hexsha, file_path)
    else:
        old_data = get_file_at_ref(repo, 'HEAD', file_path)
        new_data = get_file_at_ref(repo, ':0:', file_path)

    if new_data is None:
        if not short:
            print("No staged changes to plugin-repository.json")
        else:
            print("sync plugin-repository.json")
        return

    counts = count_changes(old_data, new_data)

    if short:
        print(format_short_message(counts))
    else:
        changes = compare_plugins_for_message(old_data, new_data)

        if not changes:
            if old_data is None:
                plugins = extract_plugin_info(new_data)
                print(f"Initial repository with {len(plugins)} plugins")
            else:
                print("No plugin changes detected")
        else:
            for change in changes:
                print(change)


def main():
    parser = argparse.ArgumentParser(
        description='Summarize changes to plugin-repository.json for commit messages'
    )
    parser.add_argument(
        '--short',
        action='store_true',
        help='Output short subject line instead of full markdown body'
    )
    parser.add_argument(
        '--at-commit',
        metavar='COMMIT',
        help='Compare specified commit with its parent instead of staged vs HEAD'
    )
    parser.add_argument(
        'repo_path',
        nargs='?',
        default='.',
        help='Path to the Git repository (default: current directory)'
    )

    args = parser.parse_args()

    try:
        summarize_changes(args.repo_path, args.short, args.at_commit)
    except git.exc.GitError as e:
        print(f"Git error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
