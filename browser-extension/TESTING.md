# Browser companion release matrix

Run the matrix against current stable Chromium and Firefox with the desktop
plugin open. Every successful case must create exactly one review-inbox entry;
none may start a download without panel confirmation.

| Source page | Transport | Payload | Expected result |
|---|---|---|---|
| HTTP | HTML form submit, submitter button, and `form.submit()` | Plain `urls` | Inbox entry; form emits `omajdownload:success`; iframe target receives `success` |
| HTTPS | HTML form submit | Plain `urls` | Mixed-content restriction is bypassed by the companion; inbox entry appears |
| HTTP and HTTPS | `fetch()` POST | Plain `urls` | Promise resolves only after listener HTTP success |
| HTTP and HTTPS | `XMLHttpRequest` POST | Plain `urls` | `readystatechange`, `load`, and `loadend` report HTTP 200 |
| HTTPS | Fetch/XHR/script-tag availability probe | `/flash/` and `/jdcheck.js`, with and without cache-busting queries | Companion reports availability and script load only while the local listener responds |
| HTTPS | form, Fetch, and XHR | CNL2 `jk` + `crypted` | Decrypted links appear in the review inbox |
| Any | form, Fetch, and XHR | Dynamic JavaScript `jk` | Visible unsupported-key failure; no website code is evaluated |
| Any | form, Fetch, and XHR | More than 1 MiB | Visible failure; no inbox entry |
| Any | form, Fetch, and XHR | Listener stopped or inbox full | Visible failure; website is not told the request succeeded |
| Any | Fetch AbortSignal and XHR abort/timeout | Plain `urls` | Standard cancellation events/errors; no late load event or inbox entry |
| Any | XHR timeout `0` and values above 10 seconds | Plain `urls` | No hidden ten-second bridge timeout; the configured XHR timeout is authoritative |
| Two tabs/frames | Equal synthetic request IDs and cancellation | Plain `urls` | Cancelling one sender never aborts or detaches the other sender's request |

Before publication, also inspect both built ZIP files and confirm they contain
no credentials, remote code, or host access beyond the declared page scripts
and loopback listener.
