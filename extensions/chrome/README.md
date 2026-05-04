# Marginalia Chrome extension (Manifest V3)

One-click capture for the Marginalia wiki engine. Sends the current
tab URL (or right-click selection) to a locally-running
`marginalia serve` HTTP endpoint, which stages it for ingest.

## Install (developer mode)

1. Run `marginalia serve --init-token` and copy the token printed.
2. Run `marginalia serve` (leaves it listening on `127.0.0.1:7777`).
3. Open `chrome://extensions` (or `edge://extensions`).
4. Enable **Developer mode** (toggle, top right).
5. Click **Load unpacked** and select this directory
   (`extensions/chrome`).
6. Click the Marginalia icon in the toolbar → **Settings** → paste
   the token → **Save**.

## Use

- **Send page**: click the toolbar icon → **Send page**.
- **Send selection**: right-click any selected text →
  **Send selection to Marginalia** (the page URL is staged with the
  selection passed as a synthesis hint).

## Where things live

- `manifest.json` — Manifest V3 with `activeTab` + `storage` + a
  scoped `host_permissions` for `127.0.0.1` / `localhost` only. No
  broad host access.
- `background.js` — service worker. Holds `sendToMarginalia()` (the
  fetch + auth header) and registers the context-menu items.
- `popup.html` + `popup.js` — toolbar popup UI; settings
  (endpoint + token) persist in `chrome.storage.local`.

## Token security

The token is the only auth between the browser and the engine. Anyone
on your machine with read access to `chrome.storage.local` (i.e.
your own user account) can read it. The `marginalia serve` endpoint
binds to `127.0.0.1` by default — never expose it to a network
without firewall rules in front.
