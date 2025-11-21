# The IDA Plugin Repository
*A modern ecosystem for discovering, installing, and sharing IDA plugins.*

![banner](https://hex-rays.com/hubfs/plugin%20manager%20-%20blog%20hero%20image.png)

This repository contains the official index of discovered IDA plugins, and provides the underlying data for [plugins.hex-rays.com](https://plugins.hex-rays.com/) and the IDA Plugin Manager. Please read the article [Introducing the IDA Plugin Manager](https://hex-rays.com/blog/introducing-the-ida-plugin-manager) for a detailed introduction.

In summary:

- The JSON manifest is here: [./plugin-repository.json](./plugin-repository.json).
- The periodic index job is done via the GitHub Action named [sync](./.github/workflows/sync.yml). Results and logs can be seen here: [actions/workflows/sync.yml](https://github.com/HexRaysSA/plugin-repository/actions/workflows/sync.yml).
- The explicitly known and ignored repositories are here: [./known-repositories.txt](./known-repositories.txt) and [./ignored-repositories.txt](./ignored-repositories.txt).

Watch the Git history to see plugin additions and (hopefully unlikely) moderation events.
Commits like [3c0f21c](https://github.com/HexRaysSA/plugin-repository/commit/3c0f21c6672c46ff3cbb071b86e5389bc64583cb) are made by the indexer, and include a nice summary in the commit message:

```markdwon
sync repo: +2 plugins, +3 releases
## New plugins
- [iOSHelper](https://github.com/yoavst/ida-ios-helper) (1.0.19, 1.0.17)
- [rhabdomancer](https://github.com/0xdea/rhabdomancer) (0.7.4)
```

## Resources for Plugin Authors

- Packaging Guide: https://hcli.docs.hex-rays.com/reference/packaging-your-existing-plugin
- Plugin Format Reference: https://hcli.docs.hex-rays.com/reference/plugin-packaging-and-format
- Architecture Details: https://hcli.docs.hex-rays.com/reference/plugin-repository-architecture
- Plugin Repository: https://github.com/HexRaysSA/plugin-repository
- Get Help: Open an issue at https://github.com/HexRaysSA/ida-hcli/issues

Need assistance? We're here to help. The Hex-Rays team actively supports plugin authors through the packaging process. Contact us through the repository or reach out to support@hex-rays.com.

If you're having trouble getting your plugin recognized:

1. run the linter: `hcli plugin lint /path/to/plugin-archive.zip`
2. check the indexer log report: https://hexrayssa.github.io/plugin-repository/logs/indexer.html
   a. Has the indexer run since you published? If not, wait 4 hours and check back.
   b. Is your repo recognized anywhere? If so, find and fix the listed errors.
   c. Otherwise, open an issue or PR adding your repo to [./known-repositories.txt](./known-repositories.txt)
