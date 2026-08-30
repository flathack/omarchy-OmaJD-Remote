# Browser companion store submission checklist

Complete this checklist for every Chromium or Firefox store submission.

## Shared review

- Build from the intended signed release tag with `./scripts/build-extension.sh`.
- Verify the manifest version, changelog section, tag, ZIP checksum, and source
  revision all match.
- Confirm the published privacy-policy URL points to `PRIVACY.md` for that tag.
- Re-run the real Chromium and Firefox matrix in `TESTING.md`.
- Inspect both ZIP allowlists and confirm they contain no secrets or remote code.

## Data inventory

- Source page URL: used locally as verified provenance for an intercepted CNL
  submission; not retained by the extension.
- Click'n'Load form fields: forwarded to the loopback helper for the requested
  user-facing feature; not retained by the extension.
- Download URLs and optional passwords: stored only in the helper's private
  review inbox until the user submits or dismisses the request.
- No analytics, advertising identifiers, telemetry, MyJDownloader credentials,
  cookies, or unrelated page content are collected.

## Chromium declarations

- Declare website activity and website content or form data accurately even
  though processing and storage are local.
- Provide the privacy-policy URL in the Developer Dashboard.
- Explain `<all_urls>` content-script scope: Click'n'Load buttons can appear on
  arbitrary HTTP(S) download pages, and the scripts act only on standard
  loopback Click'n'Load routes.
- Explain loopback host access: the service worker can contact only
  `127.0.0.1:9666` and `localhost:9666`.
- Verify that the store description prominently explains the local forwarding
  and user-review inbox behavior.

## Firefox declarations

- Keep `browser_specific_settings.gecko.data_collection_permissions` aligned
  with the shared inventory and privacy policy. The current required categories
  are `websiteActivity` and `websiteContent` because selected page URLs and
  CNL form data leave the extension for the local helper.
- Confirm the fixed Gecko extension ID and minimum Firefox version.
- Submit the Firefox-specific ZIP, not the Chromium ZIP.
