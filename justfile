clean-site:
    rm -rf ./site

clean-public:
    rm -rf ./public


clean: clean-site clean-public


collect-repo:
    mkdir -p ./public/
    cp plugin-repository.json ./public/plugin-repository.json
    cp tags.json ./public/tags.json


mirror-content:
    mkdir -p ./public/plugins/
    uv run scripts/mirror_plugin_archive_contents.py --no-cache plugin-repository.json public/plugins/


collect-stars:
    mkdir -p ./public/plugins/
    uv run scripts/snapshot_github_repo_metadata.py plugin-repository.json public/plugins/


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


build-public: collect-repo mirror-content collect-stars summarize-logs generate-hugo hugo-build-site collect-site


export HCLI_DISABLE_UPDATES := "1"
export HCLI_DEBUG := "0"

build-repo:
    uv run --with ida-hcli hcli plugin --repo github --with-repos-list=known-repositories.txt --with-ignored-repos-list=ignored-repositories.txt repo snapshot > plugin-repository.json
