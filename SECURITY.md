# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not include MyJDownloader credentials, session tokens, device IDs, or download
URLs in an issue.

## Credential handling

OmaJD-Remote stores passwords in the desktop Secret Service keyring through
`secret-tool`. The QML interface sends a password to the helper over stdin;
credentials are never passed as command-line arguments or written to plugin
configuration.

The helper keeps an authenticated MyJDownloader session in memory and disables
the optional direct-connection path. API calls therefore remain on the
encrypted MyJDownloader transport instead of probing device LAN endpoints.

## Resource and process boundaries

Loopback, browser-companion, stdin, remote-response, model, state-file, and
subprocess streams have explicit byte/cardinality limits and absolute deadlines.
The QML service serializes commands and watchdogs both the daemon and installer;
each helper runs in a private process group so teardown includes descendants.

State and installer roots are opened without following symlinks, checked for
current-user ownership, made private, and mutated relative to held directory
descriptors. Environment publication is atomic, including migration from the
legacy real `venv` directory.
