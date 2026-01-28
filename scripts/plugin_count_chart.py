#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pygit2",
#     "matplotlib",
# ]
# ///
"""Generate a chart showing plugin count over time from git history."""

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pygit2


@dataclass
class DataPoint:
    date: datetime
    plugin_count: int

    @classmethod
    def from_commit(cls, commit: pygit2.Commit, plugin_count: int) -> "DataPoint":
        return cls(
            date=datetime.fromtimestamp(commit.commit_time),
            plugin_count=plugin_count,
        )


def count_plugins_in_json(content: bytes) -> int | None:
    try:
        data = json.loads(content.decode("utf-8"))
        plugins = data.get("plugins", [])
        return len(plugins)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def extract_file_at_commit(repo: pygit2.Repository, commit: pygit2.Commit, file_path: str) -> bytes | None:
    try:
        entry = commit.tree[file_path]
        blob = repo.get(entry.id)
        return blob.data
    except KeyError:
        return None


def collect_data_points(repo_path: Path, json_file: str = "plugin-repository.json") -> list[DataPoint]:
    repo = pygit2.Repository(str(repo_path))

    data_points: list[DataPoint] = []
    seen_counts: dict[int, bool] = {}

    for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_TIME | pygit2.GIT_SORT_REVERSE):
        content = extract_file_at_commit(repo, commit, json_file)
        if content is None:
            continue

        count = count_plugins_in_json(content)
        if count is None:
            continue

        if count not in seen_counts:
            seen_counts[count] = True
            data_points.append(DataPoint.from_commit(commit, count))

    return data_points


def render_chart(data_points: list[DataPoint], output_path: Path) -> None:
    if not data_points:
        raise ValueError("No data points to plot")

    dates = [dp.date for dp in data_points]
    counts = [dp.plugin_count for dp in data_points]

    with plt.xkcd():
        fig, ax = plt.subplots(figsize=(8, 3), dpi=100)

        ax.plot(dates, counts, "k-", linewidth=2)
        ax.fill_between(dates, counts, alpha=0.1, color="black")

        ax.set_title("Hex-Rays Plugin Repository size", fontsize=12, fontweight="bold")

        ax.xaxis.set_major_locator(mdates.YearLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
        ax.xaxis.set_minor_locator(mdates.MonthLocator())

        ax.tick_params(axis="x", which="major", length=8, width=1.5)
        ax.tick_params(axis="x", which="minor", length=4, width=1)

        ax.set_facecolor("white")
        fig.patch.set_facecolor("white")

        ax.set_ylim(bottom=0)
        ax.set_xlim(left=min(dates), right=max(dates))

        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()
        plt.savefig(output_path, format="png", facecolor="white", edgecolor="none")
        plt.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate plugin count chart from git history")
    parser.add_argument("repo_path", type=Path, help="Path to the git repository")
    parser.add_argument("output_file", type=Path, help="Output PNG file path")
    args = parser.parse_args()

    if not args.repo_path.is_dir():
        print(f"error: repository path does not exist: {args.repo_path}", file=sys.stderr)
        return 1

    if not (args.repo_path / ".git").exists():
        print(f"error: not a git repository: {args.repo_path}", file=sys.stderr)
        return 1

    data_points = collect_data_points(args.repo_path)

    if not data_points:
        print("error: no data points found in git history", file=sys.stderr)
        return 1

    print(f"Found {len(data_points)} distinct plugin counts across git history", file=sys.stderr)
    print(f"Date range: {data_points[0].date.date()} to {data_points[-1].date.date()}", file=sys.stderr)
    print(f"Plugin count range: {data_points[0].plugin_count} to {data_points[-1].plugin_count}", file=sys.stderr)

    render_chart(data_points, args.output_file)
    print(f"Chart saved to: {args.output_file}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
