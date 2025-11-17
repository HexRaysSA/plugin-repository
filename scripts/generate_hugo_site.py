# Generate Hugo content files from the plugin repository
#
# Example:
#
#     $ uv run scripts/generate_hugo_content.py public/ content/
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ida-hcli",
#     "rich",
# ]
# ///

import argparse
import json
import logging
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

import rich.console
import rich.progress
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from rich.logging import RichHandler

logger = logging.getLogger(__name__)

stderr_console = rich.console.Console(stderr=True)


def sanitize_email(email: str) -> str:
    return email.replace("@", "-at-").replace(".", "-")


def get_plugin_path(host: str, name: str) -> str:
    assert host.startswith("https://")
    host = host[len("https://") :]
    return f"plugins/{host}/{name}"


def load_plugin_metadata(repo_path: Path, plugin) -> dict:
    assert plugin.host.startswith("https://")
    host = plugin.host[len("https://") :]

    metadata_path = repo_path / "plugins" / host / "metadata.json"

    if metadata_path.exists():
        return json.loads(metadata_path.read_text())
    return {}


def generate_index(repo_path: Path, content_path: Path, tags_data: list) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_by_category = defaultdict(list)
    plugins_by_keyword = defaultdict(list)
    plugins_by_tag = defaultdict(list)
    contributors = defaultdict(list)
    plugin_repo_metadata = {}

    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin
        repo_meta = load_plugin_metadata(repo_path, plugin)
        plugin_repo_metadata[plugin.name] = repo_meta

        for category in metadata.categories or []:
            plugins_by_category[category].append((plugin, metadata))

        for keyword in metadata.keywords or []:
            plugins_by_keyword[keyword].append((plugin, metadata))

        for author in metadata.authors or []:
            if author.email:
                contributors[author.email].append((plugin, metadata, "author"))

        for maintainer in metadata.maintainers or []:
            if maintainer.email:
                contributors[maintainer.email].append((plugin, metadata, "maintainer"))

    tags_by_plugin = defaultdict(list)
    for tag_entry in tags_data:
        key = (tag_entry["host"], tag_entry["name"])
        tags_by_plugin[key].append(tag_entry["tag"])

        for plugin in plugins:
            if plugin.host == tag_entry["host"] and plugin.name == tag_entry["name"]:
                latest_version = next(iter(plugin.versions.keys()))
                metadata = plugin.versions[latest_version][0].metadata.plugin
                plugins_by_tag[tag_entry["tag"]].append((plugin, metadata))
                break

    plugins_with_stars = []
    plugins_with_dates = []
    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin
        repo_meta = plugin_repo_metadata.get(plugin.name, {})
        if repo_meta.get("stargazers_count") is not None:
            plugins_with_stars.append((plugin, metadata, repo_meta["stargazers_count"]))
        if repo_meta.get("updated_at"):
            plugins_with_dates.append((plugin, metadata, repo_meta["updated_at"]))

    popular_plugins = sorted(plugins_with_stars, key=lambda x: x[2], reverse=True)[:10]
    recent_plugins = sorted(plugins_with_dates, key=lambda x: x[2], reverse=True)[:10]

    index_content = """---
title: "IDA Pro Plugin Repository"
---

"""

    index_content += "## [Recently Updated]({{< ref \"recent\" >}})\n\n"

    for plugin, metadata, updated_at in recent_plugins:
        plugin_path = get_plugin_path(plugin.host, plugin.name)
        index_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

    index_content += "\n## [Popular Plugins]({{< ref \"popular\" >}})\n\n"

    for plugin, metadata, stars in popular_plugins:
        plugin_path = get_plugin_path(plugin.host, plugin.name)
        index_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'} (⭐ {stars})\n"

    index_content += "\n## By Tag\n\n"

    for tag in sorted(plugins_by_tag.keys()):
        tag_slug = tag.replace("_", "-")
        index_content += f"\n### [{tag}]({{{{< ref \"tags/{tag_slug}\" >}}}})\n\n"
        for plugin, metadata in sorted(plugins_by_tag[tag], key=lambda x: x[0].name.lower())[:5]:
            plugin_path = get_plugin_path(plugin.host, plugin.name)
            index_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

    index_content += "\n## By Category\n\n"

    for category in sorted(plugins_by_category.keys()):
        category_slug = category.replace("_", "-")
        index_content += f"\n### [{category}]({{{{< ref \"categories/{category_slug}\" >}}}})\n\n"
        for plugin, metadata in sorted(plugins_by_category[category], key=lambda x: x[0].name.lower())[:5]:
            plugin_path = get_plugin_path(plugin.host, plugin.name)
            index_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

    index_content += "\n## Alphabetical\n\n"

    sorted_plugins = sorted(plugins, key=lambda p: p.name.lower())
    for plugin in sorted_plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin
        plugin_path = get_plugin_path(plugin.host, plugin.name)

        plugin_tags = tags_by_plugin.get((plugin.host, plugin.name), [])
        tags_html = " ".join([f'<span class="tag">{tag}</span>' for tag in plugin_tags])

        index_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'} {tags_html}\n"

    index_content += "\n## [Contributors]({{< ref \"contributors\" >}})\n\n"

    sorted_contributors = sorted(contributors.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    for email, plugin_set in sorted_contributors:
        email_slug = sanitize_email(email)
        index_content += f"- [{email}]({{{{< ref \"contributors/{email_slug}\" >}}}}) ({len(plugin_set)} plugins)\n"

    index_path = content_path / "_index.md"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(index_content)
    logger.info(f"Generated index page: {index_path}")


def generate_tag_pages(repo_path: Path, content_path: Path, tags_data: list) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_by_tag = defaultdict(list)

    for tag_entry in tags_data:
        for plugin in plugins:
            if plugin.host == tag_entry["host"] and plugin.name == tag_entry["name"]:
                latest_version = next(iter(plugin.versions.keys()))
                metadata = plugin.versions[latest_version][0].metadata.plugin
                plugins_by_tag[tag_entry["tag"]].append((plugin, metadata))
                break

    for tag, plugin_list in plugins_by_tag.items():
        tag_slug = tag.replace("_", "-")
        tag_dir = content_path / "tags" / tag_slug
        tag_dir.mkdir(parents=True, exist_ok=True)

        tag_content = f"""---
title: "{tag}"
---

# Tag: {tag}

"""

        for plugin, metadata in sorted(plugin_list, key=lambda x: x[0].name.lower()):
            plugin_path = get_plugin_path(plugin.host, plugin.name)
            tag_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

        tag_path = tag_dir / "_index.md"
        tag_path.write_text(tag_content)
        logger.debug(f"Generated tag page: {tag_path}")


def generate_popular_page(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_with_stars = []
    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin
        repo_meta = load_plugin_metadata(repo_path, plugin)
        if repo_meta.get("stargazers_count") is not None:
            plugins_with_stars.append((plugin, metadata, repo_meta["stargazers_count"]))

    popular_plugins = sorted(plugins_with_stars, key=lambda x: x[2], reverse=True)

    popular_dir = content_path / "popular"
    popular_dir.mkdir(parents=True, exist_ok=True)

    popular_content = """---
title: "Popular Plugins"
---

# Popular Plugins

Plugins sorted by GitHub star count:

"""

    for plugin, metadata, stars in popular_plugins:
        plugin_path = get_plugin_path(plugin.host, plugin.name)
        popular_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'} (⭐ {stars})\n"

    popular_path = popular_dir / "_index.md"
    popular_path.write_text(popular_content)
    logger.info(f"Generated popular page: {popular_path}")


def generate_recent_page(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_with_dates = []
    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin
        repo_meta = load_plugin_metadata(repo_path, plugin)
        if repo_meta.get("updated_at"):
            plugins_with_dates.append((plugin, metadata, repo_meta["updated_at"]))

    recent_plugins = sorted(plugins_with_dates, key=lambda x: x[2], reverse=True)

    recent_dir = content_path / "recent"
    recent_dir.mkdir(parents=True, exist_ok=True)

    recent_content = """---
title: "Recently Updated Plugins"
---

# Recently Updated Plugins

Plugins sorted by most recent GitHub repository update:

"""

    for plugin, metadata, updated_at in recent_plugins:
        plugin_path = get_plugin_path(plugin.host, plugin.name)
        recent_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'} (updated: {updated_at[:10]})\n"

    recent_path = recent_dir / "_index.md"
    recent_path.write_text(recent_content)
    logger.info(f"Generated recent page: {recent_path}")


def generate_category_pages(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_by_category = defaultdict(list)

    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin

        for category in metadata.categories or []:
            plugins_by_category[category].append((plugin, metadata))

    categories_dir = content_path / "categories"
    categories_dir.mkdir(parents=True, exist_ok=True)

    index_content = """---
title: "Categories"
---

# Categories

"""

    for category in sorted(plugins_by_category.keys()):
        category_slug = category.replace("_", "-")
        plugin_count = len(plugins_by_category[category])
        index_content += f"- [{category}]({{{{< ref \"categories/{category_slug}\" >}}}}) ({plugin_count} plugins)\n"

    index_path = categories_dir / "_index.md"
    index_path.write_text(index_content)

    for category, plugin_list in plugins_by_category.items():
        category_slug = category.replace("_", "-")
        category_dir = content_path / "categories" / category_slug
        category_dir.mkdir(parents=True, exist_ok=True)

        category_content = f"""---
title: "{category}"
---

# Category: {category}

"""

        for plugin, metadata in sorted(plugin_list, key=lambda x: x[0].name.lower()):
            plugin_path = get_plugin_path(plugin.host, plugin.name)
            category_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

        category_path = category_dir / "_index.md"
        category_path.write_text(category_content)
        logger.debug(f"Generated category page: {category_path}")


def generate_keyword_pages(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    plugins_by_keyword = defaultdict(list)

    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin

        for keyword in metadata.keywords or []:
            plugins_by_keyword[keyword].append((plugin, metadata))

    keywords_dir = content_path / "keywords"
    keywords_dir.mkdir(parents=True, exist_ok=True)

    index_content = """---
title: "Keywords"
---

# Keywords

"""

    for keyword in sorted(plugins_by_keyword.keys()):
        keyword_slug = keyword.replace("_", "-")
        plugin_count = len(plugins_by_keyword[keyword])
        index_content += f"- [{keyword}]({{{{< ref \"keywords/{keyword_slug}\" >}}}}) ({plugin_count} plugins)\n"

    index_path = keywords_dir / "_index.md"
    index_path.write_text(index_content)

    for keyword, plugin_list in plugins_by_keyword.items():
        keyword_slug = keyword.replace("_", "-")
        keyword_dir = content_path / "keywords" / keyword_slug
        keyword_dir.mkdir(parents=True, exist_ok=True)

        keyword_content = f"""---
title: "{keyword}"
---

# Keyword: {keyword}

"""

        for plugin, metadata in sorted(plugin_list, key=lambda x: x[0].name.lower()):
            plugin_path = get_plugin_path(plugin.host, plugin.name)
            keyword_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

        keyword_path = keyword_dir / "_index.md"
        keyword_path.write_text(keyword_content)
        logger.debug(f"Generated keyword page: {keyword_path}")


def generate_contributor_pages(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    contributors = defaultdict(lambda: {"name": None, "plugins": []})

    for plugin in plugins:
        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin

        for author in metadata.authors or []:
            if author.email:
                email = author.email
                contributors[email]["name"] = author.name or email
                contributors[email]["plugins"].append((plugin, metadata, "author"))

        for maintainer in metadata.maintainers or []:
            if maintainer.email:
                email = maintainer.email
                contributors[email]["name"] = maintainer.name or email
                contributors[email]["plugins"].append((plugin, metadata, "maintainer"))

    contrib_index = content_path / "contributors" / "_index.md"
    contrib_index.parent.mkdir(parents=True, exist_ok=True)

    index_content = """---
title: "Contributors"
---

# Contributors

"""

    for email in sorted(contributors.keys()):
        email_slug = sanitize_email(email)
        name = contributors[email]["name"]
        plugin_count = len(contributors[email]["plugins"])
        index_content += f"- [{name or email}]({{{{< ref \"contributors/{email_slug}\" >}}}}) ({plugin_count} plugins)\n"

    contrib_index.write_text(index_content)

    for email, data in contributors.items():
        email_slug = sanitize_email(email)
        contrib_dir = content_path / "contributors" / email_slug
        contrib_dir.mkdir(parents=True, exist_ok=True)

        name = data["name"] or email
        contrib_content = f"""---
title: "{name}"
---

# {name}

Email: {email}

## Plugins

"""

        plugins_seen = set()
        for plugin, metadata, role in sorted(data["plugins"], key=lambda x: x[0].name.lower()):
            if plugin.name not in plugins_seen:
                plugins_seen.add(plugin.name)
                plugin_path = get_plugin_path(plugin.host, plugin.name)
                contrib_content += f"- [{plugin.name}]({{{{< ref \"{plugin_path}\" >}}}}) - {metadata.description or 'No description'}\n"

        contrib_path = contrib_dir / "_index.md"
        contrib_path.write_text(contrib_content)
        logger.debug(f"Generated contributor page: {contrib_path}")


def generate_plugin_pages(repo_path: Path, content_path: Path) -> None:
    repo = JSONFilePluginRepo.from_file(repo_path / "plugin-repository.json")
    plugins = repo.get_plugins()

    for plugin in rich.progress.track(
        plugins, description="Generating plugin pages", transient=True, console=stderr_console
    ):
        assert plugin.host.startswith("https://")
        host = plugin.host[len("https://") :]

        plugin_dir = content_path / "plugins" / host / plugin.name
        plugin_dir.mkdir(parents=True, exist_ok=True)

        latest_version = next(iter(plugin.versions.keys()))
        metadata = plugin.versions[latest_version][0].metadata.plugin

        host_parts = host.split("/")
        if len(host_parts) >= 2:
            plugin_source_dir = repo_path / "plugins" / host_parts[0] / host_parts[1] / plugin.name / plugin.name
        else:
            plugin_source_dir = repo_path / "plugins" / host / plugin.name / plugin.name

        plugin_content = f"""---
title: "{metadata.name}"
---

# {metadata.name}

"""

        if metadata.logo_path:
            logo_source = plugin_source_dir / metadata.logo_path
            if logo_source.exists():
                logo_dest = plugin_dir / metadata.logo_path
                if logo_source.resolve() != logo_dest.resolve():
                    logo_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(logo_source, logo_dest)
                plugin_content += f"![{metadata.name} Logo]({metadata.logo_path})\n\n"

        plugin_content += f"{metadata.description or 'No description available'}\n\n"

        plugin_content += f"""<div style="background-color: #f0f8ff; border-left: 4px solid #2196F3; padding: 12px 16px; margin: 16px 0;">
<strong>Installation</strong>
<pre style="margin: 8px 0 0 0; background: none; padding: 0;"><code>hcli plugin install {plugin.name}</code></pre>
</div>

"""

        readme_path = plugin_source_dir / "README.md"
        if readme_path.exists():
            readme_content = readme_path.read_text()
            plugin_content += f"""## README

{readme_content}

"""

        plugin_content += f"""## Metadata

| Field | Value |
|-------|-------|
| **Version** | {metadata.version} |
| **License** | {metadata.license or 'Unknown'} |
| **Entry Point** | `{metadata.entry_point}` |
| **Platforms** | {', '.join(metadata.platforms or [])} |
| **Repository** | [{metadata.urls.repository}]({metadata.urls.repository}) |
"""

        if metadata.urls.homepage:
            plugin_content += f"| **Homepage** | [{metadata.urls.homepage}]({metadata.urls.homepage}) |\n"

        plugin_content += "\n## Categories\n\n"
        for category in metadata.categories or []:
            category_slug = category.replace("_", "-")
            plugin_content += f"- [{category}]({{{{< ref \"categories/{category_slug}\" >}}}})\n"

        if metadata.keywords:
            plugin_content += "\n## Keywords\n\n"
            for keyword in metadata.keywords:
                keyword_slug = keyword.replace("_", "-")
                plugin_content += f"- [{keyword}]({{{{< ref \"keywords/{keyword_slug}\" >}}}})\n"

        plugin_content += "\n## Authors\n\n"
        for author in metadata.authors or []:
            if author.email:
                email_slug = sanitize_email(author.email)
                plugin_content += f"- [{author.name}]({{{{< ref \"contributors/{email_slug}\" >}}}}) ({author.email})\n"
            else:
                plugin_content += f"- {author.name}\n"

        if metadata.maintainers:
            plugin_content += "\n## Maintainers\n\n"
            for maintainer in metadata.maintainers:
                if maintainer.email:
                    email_slug = sanitize_email(maintainer.email)
                    plugin_content += f"- [{maintainer.name}]({{{{< ref \"contributors/{email_slug}\" >}}}}) ({maintainer.email})\n"
                else:
                    plugin_content += f"- {maintainer.name}\n"

        plugin_content += "\n## Available Versions\n\n"
        for version, locations in plugin.versions.items():
            plugin_content += f"### Version {version}\n\n"
            ida_versions = locations[0].metadata.plugin.ida_versions
            if ida_versions:
                if isinstance(ida_versions, str):
                    plugin_content += f"**IDA Versions:** {ida_versions}\n\n"
                else:
                    plugin_content += f"**IDA Versions:** {', '.join(str(v) for v in ida_versions[:5])}"
                    if len(ida_versions) > 5:
                        plugin_content += f" (and {len(ida_versions) - 5} more)"
                    plugin_content += "\n\n"

            for location in locations:
                plugin_content += f"- [Download]({location.url})\n"
                plugin_content += f"  - SHA256: `{location.sha256}`\n"

        if metadata.python_dependencies:
            plugin_content += "\n## Python Dependencies\n\n"
            for dep in metadata.python_dependencies:
                plugin_content += f"- `{dep}`\n"

        ida_plugin_json_path = plugin_source_dir / "ida-plugin.json"
        if ida_plugin_json_path.exists():
            ida_plugin_json_content = ida_plugin_json_path.read_text()
            plugin_content += "\n## ida-plugin.json\n\n"
            plugin_content += "```json\n"
            plugin_content += ida_plugin_json_content
            plugin_content += "\n```\n"

        plugin_path = plugin_dir / "_index.md"
        plugin_path.write_text(plugin_content)
        logger.debug(f"Generated plugin page: {plugin_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Hugo content from plugin repository")
    parser.add_argument(
        "public_dir",
        type=Path,
        metavar="public-dir",
        help="path to public directory containing plugin-repository.json",
    )
    parser.add_argument(
        "content_dir", type=Path, metavar="content-dir", help="path to Hugo content directory"
    )
    parser.add_argument("--verbose", action="store_true", help="enable verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True)],
    )

    if not args.public_dir.exists():
        raise ValueError("public-dir does not exist")

    repo_json = args.public_dir / "plugin-repository.json"
    if not repo_json.exists():
        raise ValueError("plugin-repository.json not found in public-dir")

    tags_json = args.public_dir / "tags.json"
    tags_data = []
    if tags_json.exists():
        tags_data = json.loads(tags_json.read_text())

    args.content_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Generating index page...")
    generate_index(args.public_dir, args.content_dir, tags_data)

    logger.info("Generating tag pages...")
    generate_tag_pages(args.public_dir, args.content_dir, tags_data)

    logger.info("Generating popular plugins page...")
    generate_popular_page(args.public_dir, args.content_dir)

    logger.info("Generating recent plugins page...")
    generate_recent_page(args.public_dir, args.content_dir)

    logger.info("Generating category pages...")
    generate_category_pages(args.public_dir, args.content_dir)

    logger.info("Generating keyword pages...")
    generate_keyword_pages(args.public_dir, args.content_dir)

    logger.info("Generating contributor pages...")
    generate_contributor_pages(args.public_dir, args.content_dir)

    logger.info("Generating plugin pages...")
    generate_plugin_pages(args.public_dir, args.content_dir)

    logger.info("Done!")


if __name__ == "__main__":
    main()
