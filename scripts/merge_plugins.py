# Merge plugin data from multiple sources into a single combined.json for the UI.
#
# Inputs:
#   A  api-plugins.json       All plugins from the Hex-Rays API (one-time dump)
#   B  plugin-repository.json  HCLI-indexed plugins (from GitHub)
#   C  tags.json              Curated tags (favourite, plugin_contest_*, etc.)
#      github-metadata.json   GitHub repo metadata (stars, forks, dates, topics)
#
# Output:
#   combined.json     All plugins (HCLI + legacy), enriched and ready for the UI
#                             Category data is stored as slugs only; the UI enriches
#                             them with presentation data (icons, descriptions).
#
# Usage:
#   uv run --script scripts/merge_plugins.py \
#     --hcli plugin-repository.json \
#     --tags tags.json \
#     --api api-plugins.json \
#     --metadata public/plugins/github.com/repositories-metadata.json \
#     --mirror-dir public/plugins/ \
#     --out public/plugins/
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "pydantic>=2",
#     "requests",
#     "rich",
# ]
# ///

import argparse
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import rich.console
import rich.progress
from pydantic import BaseModel
from rich.logging import RichHandler

logger = logging.getLogger(__name__)
stderr_console = rich.console.Console(stderr=True)


# ─── Output schemas (Pydantic) ──────────────────────────────────────────────
#
# These models define the exact shape of combined.json.
# Both transform functions return a Plugin instance; the final output is serialized
# via .model_dump().


class Author(BaseModel):
    """An author entry inside idaplugin_json.plugin.authors."""
    login: str
    name: str = ""
    email: str = ""
    repository_owner: str = ""
    derivedFromName: bool = False


class PluginInfo(BaseModel):
    """The ``plugin`` object inside ``idaplugin_json``."""
    name: str
    description: str = ""
    authors: list[Author] = []
    keywords: list[str] = []
    absoluteLogoUrl: str | None = None
    version: str | None = None
    idaVersions: list[str] = []
    license: str | None = None


class IdaPluginJson(BaseModel):
    """Wrapper for the ``idaplugin_json`` field in plugin metadata."""
    plugin: PluginInfo


class DynamicMetadata(BaseModel):
    """GitHub-sourced metadata that changes over time (stars, forks, dates)."""
    stars: int = 0
    forks: int = 0
    watchers: int = 0
    language: str | None = None
    created_at: str | None = None
    latest_update: str | None = None
    topics: list[str] = []
    homepage: str = ""
    default_branch: str = "master"
    owner_avatar_url: str | None = None
    owner_type: str | None = None


class PluginMetadata(BaseModel):
    """The ``metadata`` envelope for every plugin."""
    idaplugin_json: IdaPluginJson | None = None
    repository_owner: str
    repository_name: str
    repository_description: str = ""
    tags: list[str] = []
    badges: list[str] = []
    prettified_versions: list[str] | None = None
    license_type: str | None = None
    readme_url: str | None = None
    changelog_url: str | None = None
    dynamic_metadata: DynamicMetadata = DynamicMetadata()


class Plugin(BaseModel):
    """A single plugin entry in combined.json."""
    slug: str
    url: str
    host: str
    name: str
    metadata: PluginMetadata
    categories: list[str] = []
    versions: dict | None = None


class CombinedOutput(BaseModel):
    """Top-level wrapper for combined.json."""
    generated_at: str
    plugins: list[Plugin]


# ─── Author login derivation (ported from DataMerger.deriveLogin) ─────────────

def derive_login(author: dict, fallback: str) -> tuple[str, bool]:
    """Return (login, derived_from_name) for an author dict."""
    name = (author.get("name") or "").strip()
    if not name:
        return fallback, False
    # Looks like a GitHub username already?
    if re.fullmatch(r"[a-zA-Z0-9-]+", name):
        return name, False
    # Try email prefix
    email = author.get("email") or ""
    if email:
        email_user = email.split("@")[0]
        if email_user and re.fullmatch(r"[a-zA-Z0-9._-]+", email_user):
            return email_user, False
    # Normalize display name
    login = re.sub(r"[^a-z0-9-]", "", name.lower().replace(" ", "-"))
    return login, True


# ─── IDA version range (ported from DataMerger prettifiedVersions) ────────────

def prettify_ida_versions(ida_versions: list[str]) -> list[str]:
    if not ida_versions:
        return []

    def clean(ver: str) -> str:
        v = re.sub(r"sp\d+$", "", ver)
        parts = v.split(".")
        if len(parts) == 3 and parts[2] == "0":
            v = f"{parts[0]}.{parts[1]}"
        return v

    first = clean(ida_versions[0])
    last = clean(ida_versions[-1])
    if len(ida_versions) == 1 or first == last:
        return [first]
    return [f"{first} to {last}"]


# The latest publicly released IDA version. The upstream HCLI index expands
# open-ended specs like ">=9.0" into a concrete list that includes forward-
# looking placeholders (e.g. "10.0") not yet released (EA-762). cap_ida_versions
# trims the list here so plugin pages never advertise an unreleased IDA.
#
# There is no machine-readable "latest released IDA" feed to derive this from
# (Hex-Rays only publishes HTML release notes), so it's a reviewed default that
# CI can override without a code change when a new IDA ships:
#   LATEST_RELEASED_IDA=9.5 just merge-plugins
_DEFAULT_LATEST_RELEASED_IDA = "9.4"
LATEST_RELEASED_IDA = os.environ.get("LATEST_RELEASED_IDA", _DEFAULT_LATEST_RELEASED_IDA)


def _ida_version_key(version: str) -> tuple[int, int, int, int] | None:
    """Parse "9.0", "9.0sp1", "6.95", "9.0.0" into a sortable key, or None."""
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:sp(\d+))?", version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0), int(m.group(4) or 0)


def cap_ida_versions(ida_versions: list[str], latest: str = LATEST_RELEASED_IDA) -> list[str]:
    """Drop versions newer than ``latest``; keep unparseable entries untouched."""
    cap = _ida_version_key(latest)
    if cap is None:
        return list(ida_versions)
    return [
        v for v in ida_versions
        if (key := _ida_version_key(v)) is None or key <= cap
    ]


# ─── Logo URL resolution ──────────────────────────────────────────────────────

def resolve_logo_url(logo_path: str | None, owner: str, repo: str, default_branch: str) -> str | None:
    if not logo_path:
        return None
    if logo_path.startswith("http"):
        # Convert GitHub blob URLs to raw URLs
        if "github.com" in logo_path and "/blob/" in logo_path:
            logo_path = re.sub(
                r"github\.com/([^/]+)/([^/]+)/blob/([^/]+)/",
                r"raw.githubusercontent.com/\1/\2/\3/",
                logo_path,
            )
        return logo_path
    # Relative path: strip leading ../ and ./
    clean_path = re.sub(r"^(\.\./)+", "", logo_path)
    clean_path = re.sub(r"^\./", "", clean_path)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{clean_path}"


# ─── README / changelog URL probing ───────────────────────────────────────────

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "ida-plugin-merge-script/1.0"})


_README_FILENAMES: list[str] = [
    "README.md",
    "README.MD",
    "readme.md",
    "Readme.md",
    "README",
    "README.txt",
    "README.rst",
    "README.adoc",
    "docs/README.md",
    "docs/readme.md",
    "doc/README.md",
    "doc/readme.md",
]


_CHANGELOG_FILENAMES: list[str] = [
    "CHANGELOG.md",
    "CHANGELOG.MD",
    "changelog.md",
    "Changelog.md",
    "CHANGES.md",
    "changes.md",
    "HISTORY.md",
    "history.md",
    "docs/CHANGELOG.md",
    "docs/changelog.md",
]


_MIRROR_PUBLIC_BASE = "https://hexrayssa.github.io/plugin-repository/plugins"


def _probe_doc_mirror(
    owner: str, repo: str, plugin_name: str, mirror_dir: Path, filenames: list[str],
) -> str | None:
    """Check the local mirror for one of *filenames* and return its public URL, or None."""
    local_base = mirror_dir / "github.com" / owner / repo / plugin_name
    if local_base.is_dir():
        for fn in filenames:
            if (local_base / fn).is_file():
                return f"{_MIRROR_PUBLIC_BASE}/github.com/{owner}/{repo}/{plugin_name}/{fn}"
    return None


def _probe_doc_github(
    owner: str, repo: str, default_branch: str, filenames: list[str],
) -> str | None:
    """Probe GitHub raw URLs via HEAD requests. Returns first 200, or None."""
    branches = [default_branch]
    if default_branch == "main":
        branches.append("master")
    elif default_branch == "master":
        branches.append("main")
    else:
        branches += ["main", "master"]

    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}"
    candidates = [f"{raw_base}/{branch}/{fn}" for branch in branches for fn in filenames]

    for url in candidates:
        try:
            resp = _SESSION.head(url, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                return url
        except requests.RequestException:
            continue

    return None


def probe_doc(
    owner: str,
    repo: str,
    plugin_name: str | None,
    default_branch: str,
    is_hcli: bool,
    filenames: list[str],
    mirror_dir: Path | None = None,
) -> str | None:
    """Return a URL for the first of *filenames* found for the plugin, or None.

    HCLI plugins: checks the local mirror first, falls back to GitHub
    only when the mirror has no match (e.g. archive didn't include one).
    API-only plugins: probes GitHub raw URLs via HEAD requests.
    """
    if is_hcli and plugin_name and mirror_dir:
        url = _probe_doc_mirror(owner, repo, plugin_name, mirror_dir, filenames)
        if url:
            return url

    return _probe_doc_github(owner, repo, default_branch, filenames)


# ─── Build tags map from tags.json ────────────────────────────────────────────

def build_tags_map(tags_array: list[dict]) -> dict[tuple[str, str], list[str]]:
    """Key: (host.lower(), name) -> list of tag strings."""
    result: dict[tuple[str, str], list[str]] = {}
    for entry in tags_array:
        host = (entry.get("host") or "").lower()
        name = entry.get("name") or ""
        tag = entry.get("tag") or ""
        if not tag:
            continue
        key = (host, name)
        if key not in result:
            result[key] = []
        result[key].append(tag)
    return result


def get_tags_for_plugin(tags_map: dict, host: str, name: str) -> list[str]:
    key = (host.lower(), name)
    return tags_map.get(key, [])


# ─── Badge generation (ported from DataMerger.generateBadges) ─────────────────

def generate_badges(tags: list[str]) -> list[str]:
    badge_set = {
        "favourite", "plugin_contest_2024", "plugin_contest_2023",
        "plugin_contest_2022", "recently_added", "recently_updated", "hidden_gem",
    }
    return [t for t in tags if t in badge_set]


# ─── GitHub URL parsing ───────────────────────────────────────────────────────

def parse_github_url(url: str) -> tuple[str, str] | None:
    m = re.search(r"github\.com/([^/]+)/([^/]+)", url)
    if not m:
        return None
    return m.group(1), m.group(2).rstrip("/").removesuffix(".git")


# ─── Transform functions ──────────────────────────────────────────────────────

def transform_hcli_plugin(hcli_plugin: dict, github_meta: dict, tags_map: dict, readme_url: str | None, changelog_url: str | None, tags_from_a: list[str] | None = None) -> Plugin | None:
    """Transform a single HCLI plugin entry into the combined output shape."""
    parsed = parse_github_url(hcli_plugin.get("host", ""))
    if not parsed:
        logger.warning("Cannot parse host URL: %s", hcli_plugin.get("host"))
        return None

    owner, repo = parsed
    versions = hcli_plugin.get("versions") or {}
    version_keys = list(versions.keys())
    latest_key = version_keys[-1] if version_keys else None
    version_data = versions[latest_key][0] if latest_key and versions[latest_key] else {}
    plugin_meta = (version_data.get("metadata") or {}).get("plugin") or {}

    slug = f"{owner}/{repo}/{hcli_plugin['name']}"
    default_branch = github_meta.get("default_branch") or "master"

    # Logo
    absolute_logo_url = resolve_logo_url(
        plugin_meta.get("logoPath"),
        owner, repo, default_branch,
    )

    # Authors
    raw_authors = plugin_meta.get("authors") or []
    normalized_authors: list[Author] = []
    for author in raw_authors:
        login, derived = derive_login(author, owner)
        normalized_authors.append(Author(
            login=login,
            name=author.get("name") or "",
            email=author.get("email") or "",
            repository_owner=owner,
            derivedFromName=derived,
        ))
    if not normalized_authors:
        normalized_authors = [Author(login=owner, name=owner, email="", repository_owner=owner, derivedFromName=False)]

    # Versions
    ida_versions = cap_ida_versions(plugin_meta.get("idaVersions") or [])
    prettified = prettify_ida_versions(ida_versions)

    # Tags: from A (metadata.tags) + C (tags.json) + auto-tag plugin_manager_ready
    # A has the full tag set (contest years, placements, award_winning, favourite, etc.)
    # C may have additions not yet in A
    tags_from_c = get_tags_for_plugin(tags_map, hcli_plugin.get("host", ""), hcli_plugin["name"])
    tags = list(dict.fromkeys((tags_from_a or []) + tags_from_c + ["plugin_manager_ready"]))

    # Categories (slugs only — UI enriches with presentation data)
    categories = plugin_meta.get("categories") or ["other"]

    return Plugin(
        slug=slug,
        url=hcli_plugin["host"],
        host=hcli_plugin["host"],
        name=hcli_plugin["name"],
        metadata=PluginMetadata(
            idaplugin_json=IdaPluginJson(
                plugin=PluginInfo(
                    name=plugin_meta.get("name") or hcli_plugin["name"],
                    description=plugin_meta.get("description") or "",
                    authors=normalized_authors,
                    keywords=plugin_meta.get("keywords") or [],
                    absoluteLogoUrl=absolute_logo_url,
                    version=plugin_meta.get("version") or latest_key,
                    idaVersions=ida_versions,
                    license=plugin_meta.get("license"),
                ),
            ),
            repository_owner=owner,
            repository_name=repo,
            repository_description=plugin_meta.get("description") or github_meta.get("description") or "",
            tags=tags,
            badges=generate_badges(tags),
            prettified_versions=prettified,
            license_type=plugin_meta.get("license"),
            readme_url=readme_url,
            changelog_url=changelog_url,
            dynamic_metadata=DynamicMetadata(
                stars=github_meta.get("stargazers_count") or 0,
                forks=github_meta.get("forks_count") or 0,
                watchers=github_meta.get("watchers_count") or 0,
                language=github_meta.get("language"),
                created_at=github_meta.get("created_at"),
                latest_update=github_meta.get("pushed_at") or github_meta.get("updated_at"),
                topics=github_meta.get("topics") or [],
                homepage=github_meta.get("homepage") or "",
                default_branch=default_branch,
                owner_avatar_url=(github_meta.get("owner") or {}).get("avatar_url"),
                owner_type=(github_meta.get("owner") or {}).get("type"),
            ),
        ),
        categories=categories,
        versions=hcli_plugin.get("versions"),
    )


def transform_legacy_plugin(api_plugin: dict, github_meta: dict, tags_map: dict, readme_url: str | None, changelog_url: str | None) -> Plugin | None:
    """Transform an API-only plugin into the combined output shape.
    Passes through idaplugin_json when present (some API plugins have metadata but aren't in the HCLI index yet).
    """
    url = api_plugin.get("url") or ""
    parsed = parse_github_url(url)
    if not parsed:
        logger.warning("Cannot parse URL for legacy plugin: %s", api_plugin.get("slug"))
        return None

    owner, repo = parsed
    meta = api_plugin.get("metadata") or {}
    default_branch = github_meta.get("default_branch") or "master"
    raw_idaplugin_json = meta.get("idaplugin_json")  # may be None or a dict

    slug = api_plugin.get("slug") or f"{owner}/{repo}"

    # Tags: from A (metadata.tags) + C (tags.json), deduplicated
    tags_from_a = meta.get("tags") or []
    plugin_name = (raw_idaplugin_json or {}).get("plugin", {}).get("name") or meta.get("repository_name") or repo
    tags_from_c = get_tags_for_plugin(tags_map, url, plugin_name)
    tags = list(dict.fromkeys(tags_from_a + tags_from_c))

    # Categories (slugs only — UI enriches with presentation data)
    categories = [
        cat.get("slug") or "other"
        for cat in (api_plugin.get("categories") or [])
    ]
    if not categories:
        categories = ["other"]

    # dynamic_metadata: prefer github_meta (fresher), fall back to API dynamic_metadata
    api_dyn = meta.get("dynamic_metadata") or {}

    # License: prefer idaplugin_json.plugin.license, then API metadata.license_type, then GitHub repo license
    idaplugin_license = (raw_idaplugin_json or {}).get("plugin", {}).get("license")
    api_license = meta.get("license_type")
    github_license = (github_meta.get("license") or {}).get("name") or (github_meta.get("license") or {}).get("spdx_id")
    license_type = idaplugin_license or api_license or github_license

    # Build typed idaplugin_json if the API has it
    idaplugin_json: IdaPluginJson | None = None
    if raw_idaplugin_json and isinstance(raw_idaplugin_json.get("plugin"), dict):
        raw_plugin = raw_idaplugin_json["plugin"]
        raw_ida_versions = raw_plugin.get("idaVersions") or []
        if isinstance(raw_ida_versions, str):
            raw_ida_versions = [raw_ida_versions]
        raw_ida_versions = cap_ida_versions(raw_ida_versions)
        idaplugin_json = IdaPluginJson(
            plugin=PluginInfo(
                name=raw_plugin.get("name") or plugin_name,
                description=raw_plugin.get("description") or "",
                authors=[Author(**a) if isinstance(a, dict) else Author(login=str(a)) for a in (raw_plugin.get("authors") or [])],
                keywords=raw_plugin.get("keywords") or [],
                absoluteLogoUrl=raw_plugin.get("absoluteLogoUrl"),
                version=raw_plugin.get("version"),
                idaVersions=raw_ida_versions,
                license=raw_plugin.get("license"),
            ),
        )

    return Plugin(
        slug=slug,
        url=url,
        host=url,
        name=plugin_name,
        metadata=PluginMetadata(
            idaplugin_json=idaplugin_json,
            repository_owner=meta.get("repository_owner") or owner,
            repository_name=meta.get("repository_name") or repo,
            repository_description=meta.get("repository_description") or github_meta.get("description") or "",
            tags=tags,
            badges=generate_badges(tags),
            prettified_versions=None,
            license_type=license_type,
            readme_url=readme_url,
            changelog_url=changelog_url,
            dynamic_metadata=DynamicMetadata(
                stars=github_meta.get("stargazers_count") or api_dyn.get("stars") or 0,
                forks=github_meta.get("forks_count") or api_dyn.get("forks") or 0,
                watchers=github_meta.get("watchers_count") or 0,
                language=github_meta.get("language") or api_dyn.get("language"),
                created_at=github_meta.get("created_at") or api_dyn.get("created_at"),
                latest_update=github_meta.get("pushed_at") or github_meta.get("updated_at") or api_dyn.get("latest_update"),
                topics=github_meta.get("topics") or [],
                homepage=github_meta.get("homepage") or "",
                default_branch=default_branch,
                owner_avatar_url=(github_meta.get("owner") or {}).get("avatar_url"),
                owner_type=(github_meta.get("owner") or {}).get("type"),
            ),
        ),
        categories=categories,
        versions=None,
    )


# ─── Main merge logic ─────────────────────────────────────────────────────────

def do_merge(
    api_path: Path | None,
    hcli_path: Path,
    tags_path: Path,
    metadata_path: Path | None,
    out_path: Path,
    mirror_dir: Path | None = None,
) -> None:
    # ── Load inputs ──────────────────────────────────────────────────────────
    logger.info("Loading inputs...")

    if api_path and api_path.exists():
        with open(api_path) as f:
            api_raw = json.load(f)
        # Handle both response shapes: {plugins: {hits: [...]}} or {hits: [...]}
        if "plugins" in api_raw and "hits" in api_raw["plugins"]:
            api_plugins_raw: list[dict] = api_raw["plugins"]["hits"]
        elif "hits" in api_raw:
            api_plugins_raw = api_raw["hits"]
        else:
            # Flat list
            api_plugins_raw = api_raw if isinstance(api_raw, list) else []
    else:
        if api_path:
            logger.warning("--api file not found (%s); skipping legacy plugins", api_path)
        api_plugins_raw = []

    with open(hcli_path) as f:
        hcli_raw = json.load(f)
    hcli_plugins_raw: list[dict] = hcli_raw.get("plugins") or []

    with open(tags_path) as f:
        tags_array: list[dict] = json.load(f)

    if metadata_path and metadata_path.exists():
        with open(metadata_path) as f:
            github_metadata: dict[str, dict] = json.load(f)
    else:
        if metadata_path:
            logger.warning("--metadata file not found (%s); GitHub stats will be empty", metadata_path)
        github_metadata = {}
    # Normalize metadata keys to lowercase for lookup
    github_metadata_lower = {k.lower(): v for k, v in github_metadata.items()}

    def get_meta(owner: str, repo: str) -> dict:
        return github_metadata_lower.get(f"{owner}/{repo}".lower()) or {}

    tags_map = build_tags_map(tags_array)
    logger.info("Loaded: %d API plugins, %d HCLI plugins, %d tag entries, %d repo metadata entries",
                len(api_plugins_raw), len(hcli_plugins_raw), len(tags_array), len(github_metadata))

    # ── Build lookup maps ────────────────────────────────────────────────────
    # HCLI: key = "owner/repo/plugin_name".lower() (one plugin per entry, unique)
    hcli_map: dict[str, dict] = {}
    # Also track which owner/repo combos have HCLI coverage (for skipping API duplicates)
    hcli_repo_keys: set[str] = set()
    for p in hcli_plugins_raw:
        parsed = parse_github_url(p.get("host", ""))
        if parsed:
            owner, repo = parsed
            key = f"{owner}/{repo}/{p['name']}".lower()
            hcli_map[key] = p
            hcli_repo_keys.add(f"{owner}/{repo}".lower())

    # API: key = "owner/repo".lower() from url or slug
    api_map: dict[str, dict] = {}
    for p in api_plugins_raw:
        url = p.get("url") or ""
        parsed = parse_github_url(url)
        if parsed:
            key = f"{parsed[0]}/{parsed[1]}".lower()
            api_map[key] = p
        elif p.get("slug"):
            # slug is already "owner/repo"
            api_map[p["slug"].lower()] = p

    # ── Collect all plugins that need README/changelog probing ────────────────
    # Assemble (owner, repo, plugin_name, default_branch, is_hcli) per unique repo
    probe_params: list[tuple[str, str, str | None, str, bool, str]] = []  # + key

    for key, p in hcli_map.items():
        parsed = parse_github_url(p.get("host", ""))
        if not parsed:
            continue
        owner, repo = parsed
        meta = get_meta(owner, repo)
        default_branch = meta.get("default_branch") or "master"
        probe_params.append((owner, repo, p["name"], default_branch, True, key))

    for key, p in api_map.items():
        if key in hcli_repo_keys:
            continue  # covered by HCLI path
        parsed = parse_github_url(p.get("url") or "")
        if not parsed:
            continue
        owner, repo = parsed
        meta = get_meta(owner, repo)
        default_branch = meta.get("default_branch") or "master"
        probe_params.append((owner, repo, None, default_branch, False, key))

    # ── Probe README/changelog URLs in parallel ───────────────────────────────
    logger.info("Probing README/changelog URLs for %d plugins (10 workers)...", len(probe_params))
    probe_results: dict[str, tuple[str | None, str | None]] = {}

    def _probe(params):
        owner, repo, plugin_name, default_branch, is_hcli, key = params
        readme_url = probe_doc(owner, repo, plugin_name, default_branch, is_hcli, _README_FILENAMES, mirror_dir)
        changelog_url = probe_doc(owner, repo, plugin_name, default_branch, is_hcli, _CHANGELOG_FILENAMES, mirror_dir)
        return key, readme_url, changelog_url

    with rich.progress.Progress(
        rich.progress.SpinnerColumn(),
        rich.progress.TextColumn("[progress.description]{task.description}"),
        rich.progress.BarColumn(),
        rich.progress.MofNCompleteColumn(),
        console=stderr_console,
        transient=True,
    ) as progress:
        task = progress.add_task("Probing READMEs & changelogs", total=len(probe_params))
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(_probe, p): p for p in probe_params}
            for future in as_completed(futures):
                key, readme_url, changelog_url = future.result()
                probe_results[key] = (readme_url, changelog_url)
                progress.advance(task)

    # ── Transform HCLI plugins ────────────────────────────────────────────────
    output_plugins: list[Plugin] = []
    hcli_count = 0

    for key, p in hcli_map.items():
        parsed = parse_github_url(p.get("host", ""))
        if not parsed:
            continue
        owner, repo = parsed
        meta = get_meta(owner, repo)
        readme_url, changelog_url = probe_results.get(key) or (None, None)
        # Rule 6: merge tags from A (full tag set: contest years, placements, etc.)
        repo_key = f"{owner}/{repo}".lower()
        tags_from_a = (api_map.get(repo_key, {}).get("metadata") or {}).get("tags") or []
        transformed = transform_hcli_plugin(p, meta, tags_map, readme_url, changelog_url, tags_from_a)
        if transformed:
            output_plugins.append(transformed)
            hcli_count += 1

    # ── Transform legacy (API-only, idaplugin_json: null) plugins ─────────────
    legacy_count = 0

    for key, p in api_map.items():
        if key in hcli_repo_keys:
            continue  # B wins
        parsed = parse_github_url(p.get("url") or "")
        if not parsed:
            logger.debug("Skipping legacy plugin with unparseable URL: %s", p.get("slug"))
            continue
        owner, repo = parsed
        meta = get_meta(owner, repo)
        readme_url, changelog_url = probe_results.get(key) or (None, None)
        transformed = transform_legacy_plugin(p, meta, tags_map, readme_url, changelog_url)
        if transformed:
            output_plugins.append(transformed)
            legacy_count += 1

    logger.info("Merged: %d HCLI + %d legacy = %d total plugins", hcli_count, legacy_count, len(output_plugins))
    logger.info("IDA version cap: idaVersions trimmed to <= %s (latest released)", LATEST_RELEASED_IDA)

    # Verify no duplicates
    slugs = [p.slug for p in output_plugins]
    if len(slugs) != len(set(slugs)):
        dupes = [s for s in slugs if slugs.count(s) > 1]
        logger.warning("Duplicate slugs found: %s", list(set(dupes)))

    # ── Write output ──────────────────────────────────────────────────────────
    out_path.mkdir(parents=True, exist_ok=True)

    combined = CombinedOutput(
        generated_at=datetime.now(timezone.utc).isoformat(),
        plugins=output_plugins,
    )
    combined_path = out_path / "combined.json"
    combined_path.write_text(json.dumps(combined.model_dump(), indent=2, ensure_ascii=False) + "\n")
    logger.info("Wrote %s (%d plugins)", combined_path, len(output_plugins))

    # ── Summary ───────────────────────────────────────────────────────────────
    pm_ready = sum(1 for p in output_plugins if "plugin_manager_ready" in p.metadata.tags)
    no_readme = sum(1 for p in output_plugins if not p.metadata.readme_url)
    no_changelog = sum(1 for p in output_plugins if not p.metadata.changelog_url)
    stderr_console.print(f"\n[bold green]Done![/bold green] {len(output_plugins)} plugins "
                         f"({pm_ready} PM-ready, {legacy_count} legacy, {no_readme} without README, "
                         f"{no_changelog} without changelog)")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge HCLI + API plugin data into combined.json for the UI.",
    )
    parser.add_argument("--api", type=Path, default=None,
                        help="API export (api-plugins.json) — optional, omit to process HCLI plugins only")
    parser.add_argument("--hcli", type=Path, default=Path("plugin-repository.json"),
                        help="HCLI plugin index (plugin-repository.json)")
    parser.add_argument("--tags", type=Path, default=Path("tags.json"),
                        help="Tags file (tags.json)")
    parser.add_argument("--metadata", type=Path, default=None,
                        help="GitHub repo metadata JSON — optional, omit to skip GitHub stats")
    parser.add_argument("--out", type=Path, default=Path("."),
                        help="Output directory for combined.json")
    parser.add_argument("--mirror-dir", type=Path, default=None,
                        help="Local mirror directory (e.g. public/plugins/) — avoids GitHub HTTP requests for HCLI plugins")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, console=stderr_console)],
    )

    for p, name in [
        (args.hcli, "--hcli"),
        (args.tags, "--tags"),
    ]:
        if not p.exists():
            raise SystemExit(f"File not found: {p} (pass with {name})")

    do_merge(
        api_path=args.api,
        hcli_path=args.hcli,
        tags_path=args.tags,
        metadata_path=args.metadata,
        out_path=args.out,
        mirror_dir=args.mirror_dir,
    )


if __name__ == "__main__":
    main()
