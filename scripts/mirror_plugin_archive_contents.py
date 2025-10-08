# Fetch the contents of the latest versions of IDA Pro plugins available in the plugin repo,
# which can be used to host and reference copies of READMEs, logos, etc.
#
# Example:
#
#     $ mkdir output
#     $ uv run scripts/mirror_plugin_archive_contents.py plugin-repository.json output
#     $ eza --tree --level=5 output/
#       output
#       └── github.com
#           ├── HexRays-plugin-contributions
#           │   ├── capa
#           │   │   └── capa
#           │   │       ├── __init__.py
#           │   │       ├── cache.py
#           │   │       ├── capa_explorer.py
#           │   │       ├── error.py
#           │   │       ├── extractor.py
#           │   │       ├── form.py
#           │   │       ├── hooks.py
#           │   │       ├── icon.py
#           │   │       ├── ida-plugin.json
#           │   │       ├── item.py
#           │   │       ├── model.py
#           │   │       ├── plugin.zip
#           │   │       ├── proxy.py
#           │   │       ├── README.md
#           │   │       └── view.py
#           │   ├── comida
#           │   │   └── comida
#           │   │       ├── comida.py
#           │   │       ├── doc
#           │   │       ├── ida-plugin.json
#           │   │       ├── LICENSE
#           │   │       ├── plugin.zip
#           │   │       └── README.md
#           ...
#
#
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "ida-hcli",
#     "rich",
# ]
# ///

import argparse
import hashlib
import logging
import shutil
from pathlib import Path

import rich.console
import rich.progress
from hcli.lib.ida.plugin import (
    get_metadata_from_plugin_archive,
    get_metadata_path_from_plugin_archive,
    validate_metadata_in_plugin_archive,
)
from hcli.lib.ida.plugin.install import extract_zip_subdirectory_to
from hcli.lib.ida.plugin.repo import fetch_plugin_archive
from hcli.lib.ida.plugin.repo.file import JSONFilePluginRepo
from rich.logging import RichHandler

logger = logging.getLogger(__name__)

stderr_console = rich.console.Console(stderr=True)


def do_cache(json_path: Path, out_path: Path):
    repo = JSONFilePluginRepo.from_file(json_path)
    plugins = repo.get_plugins()

    for plugin in rich.progress.track(
        plugins, description="Caching plugins", transient=True, console=stderr_console
    ):
        logger.debug("caching: %s", plugin.name)
        version = None
        locations = []
        for version, locations in plugin.versions.items():
            break

        assert version is not None
        location = locations[0]

        assert plugin.host.startswith("https://")
        host = plugin.host[len("https://") :]

        destination_path = out_path / host / plugin.name
        plugin_zip_path = destination_path / "plugin.zip"

        if plugin_zip_path.exists():
            existing_hash = hashlib.sha256(plugin_zip_path.read_bytes()).hexdigest()
            if existing_hash == location.sha256:
                logger.debug("skipping: %s (already cached)", plugin.name)
                continue

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            shutil.rmtree(destination_path)

        assert location.url.startswith("https://")

        zip_data = fetch_plugin_archive(location.url)

        metadata = get_metadata_from_plugin_archive(zip_data, plugin.name)
        validate_metadata_in_plugin_archive(zip_data, metadata)

        # path within the zip to ida-plugin.json
        metadata_path = get_metadata_path_from_plugin_archive(zip_data, plugin.name)
        plugin_subdirectory = metadata_path.parent

        extract_zip_subdirectory_to(zip_data, plugin_subdirectory, destination_path)
        plugin_zip_path.write_bytes(zip_data)


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

    do_cache(args.plugin_repo_json, args.output_path)


if __name__ == "__main__":
    main()
