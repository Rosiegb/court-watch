# Court Watch — free version

This version is designed for £0 operation using a public GitHub repository and GitHub Actions.

## Architecture
- `app/` = iPhone-friendly web app/PWA
- `.github/workflows/check.yml` = 5-minute scheduled checker
- `checker.py` = Better availability checker (replace selectors/logic after inspecting Better's rendered page)
- `alerts.json` = saved watch configuration for the GitHub-run checker

## Important
GitHub scheduled workflows have a minimum supported interval of 5 minutes. Public-repository standard GitHub-hosted runners are free. The checker therefore targets 5-minute checks, not 1-minute checks.

For iPhone, deploy `app/` through GitHub Pages, then Safari → Share → Add to Home Screen.
