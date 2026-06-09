clean-site:
    rm -rf ./site

clean-public:
    rm -rf ./public


clean: clean-site clean-public


generate-chart:
    mkdir -p ./public/resources/
    uv run --script scripts/plugin_count_chart.py . public/resources/repo_size.png


collect-repo:
    mkdir -p ./public/
    cp plugin-repository.json ./public/plugin-repository.json
    cp tags.json ./public/tags.json


mirror-content:
    mkdir -p ./public/plugins/
    uv run --no-cache scripts/mirror_plugin_archive_contents.py --no-cache plugin-repository.json public/plugins/


collect-stars:
    mkdir -p ./public/plugins/
    uv run scripts/snapshot_github_repo_metadata.py plugin-repository.json public/plugins/


merge-plugins:
    mkdir -p ./public/plugins/
    uv run --script scripts/merge_plugins.py \
        --hcli plugin-repository.json \
        --tags tags.json \
        --api api-plugins.json \
        --metadata public/plugins/github.com/repositories-metadata.json \
        --mirror-dir public/plugins/ \
        --known-repos known-repositories.txt \
        --out public/plugins/


summarize-logs:
    mkdir -p ./public/logs/
    uv run scripts/summarize_github_indexer_logs.py --html > public/logs/indexer.html


generate-hugo:
    mkdir -p ./site/content/
    uv run scripts/generate_hugo_site.py public/ site/content/
    cp .github/pages/hugo.toml site/
    cp -r .github/pages/layouts site/layouts


hugo-build-site:
    hugo --source site --baseURL "/_/"


collect-site:
    mkdir -p ./public/_/
    rsync -av ./site/public/ ./public/_/


build-public: generate-chart collect-repo mirror-content collect-stars merge-plugins summarize-logs generate-hugo hugo-build-site collect-site


export HCLI_DISABLE_UPDATES := "1"
export HCLI_DEBUG := "1"

build-repo:
    uv run --with ~/code/hex-rays/ida-hcli --no-cache hcli plugin --repo github --with-repos-list=known-repositories.txt --with-ignored-repos-list=ignored-repositories.txt repo snapshot > plugin-repository.json


update-known-repos:
    #!/usr/bin/env bash
    set -euo pipefail
    # Extract GitHub repository URLs from plugin-repository.json
    repos=$(jq -r '.plugins[].host' plugin-repository.json | \
        grep '^https://github.com/' | \
        sed 's|https://github.com/||' | \
        tr '[:upper:]' '[:lower:]' | \
        sort -u)
    # Read existing known repositories (excluding empty lines and comments)
    existing=$(grep -v '^#' known-repositories.txt | grep -v '^[[:space:]]*$' | tr '[:upper:]' '[:lower:]' | sort -u)
    # Find new repositories (those in plugin-repository.json but not in known-repositories.txt)
    new_repos=$(comm -23 <(echo "$repos") <(echo "$existing"))
    # Append new repositories if any were found
    if [ -n "$new_repos" ]; then
        echo "Found $(echo "$new_repos" | wc -l) new repositories to add:"
        echo "$new_repos" | sed 's/^/  /'
        echo "" >> known-repositories.txt
        echo "# Discovered on $(date '+%Y-%m-%d %H:%M:%S')" >> known-repositories.txt
        echo "$new_repos" >> known-repositories.txt
        echo "Successfully appended new repositories to known-repositories.txt"
    else
        echo "No new repositories found"
    fi
