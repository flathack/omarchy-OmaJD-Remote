# Contributing

Issues and focused pull requests are welcome.

1. Fork the repository and create a branch from `main`.
2. Keep secrets and real account data out of fixtures and logs.
3. Run `./scripts/check.sh`.
4. Test the plugin on Omarchy Quattro when changing QML or shell integration.
5. Describe user-visible behavior and manual test coverage in the pull request.

Do not add automatic file deletion without a separate, explicit confirmation
flow and tests that prove the selected IDs are the only affected targets.
