# Security policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository. Do
not include MyJDownloader credentials, session tokens, device IDs, or download
URLs in an issue.

## Credential handling

OmaJDownLoad stores passwords in the desktop Secret Service keyring through
`secret-tool`. The QML interface sends a password to the helper over stdin;
credentials are never passed as command-line arguments or written to plugin
configuration.

The helper keeps an authenticated MyJDownloader session in memory and disables
the optional direct-connection path. API calls therefore remain on the
encrypted MyJDownloader transport instead of probing device LAN endpoints.
