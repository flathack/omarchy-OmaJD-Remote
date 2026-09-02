# CI and release reproducibility

CI and release jobs use GitHub-hosted `ubuntu-24.04` runners. The runner label
selects the orchestration virtual machine and is maintained by GitHub; it is an
explicit trust boundary and is not an immutable build input. See the [GitHub-hosted
runner documentation](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners).

The build userland is pinned separately: every job runs inside the same
digest-pinned Arch Linux container, GitHub Actions are referenced by commit SHA,
the Arch package repository is an archive snapshot, and Python dependencies are
installed from hash-locked requirements files. Changes to any of those inputs
must be reviewed together with the workflow maintenance tests.
