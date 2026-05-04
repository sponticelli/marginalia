// background.js — Marginalia Chrome extension service worker (Manifest V3).
//
// Handles two capture flows:
//
// 1. Toolbar button click (via action.default_popup → popup.js calling
//    `sendToMarginalia` directly — popup runs in its own context).
// 2. Context-menu "Send selection to Marginalia" — registered here so
//    selection-text is available without a popup.
//
// Settings live in chrome.storage.local under:
//   - "endpoint" : full URL of the serve endpoint, e.g. "http://127.0.0.1:7777"
//   - "token"    : the auth token from `marginalia serve --init-token`
//
// The popup is what writes those values; this worker just reads them.

const DEFAULT_ENDPOINT = "http://127.0.0.1:7777";

async function readSettings() {
    const settings = await chrome.storage.local.get(["endpoint", "token"]);
    return {
        endpoint: settings.endpoint || DEFAULT_ENDPOINT,
        token: settings.token || "",
    };
}

// Exported as a worker-scoped function so popup.js can import it via
// `chrome.runtime.getBackgroundPage` (legacy) or by message-passing
// (MV3). We use message-passing below.
export async function sendToMarginalia({target, hint = ""}) {
    const {endpoint, token} = await readSettings();
    if (!token) {
        throw new Error("no auth token configured — open the popup and paste one");
    }
    if (!target) {
        throw new Error("no target URL");
    }

    const body = {target};
    if (hint) {
        body.hint = hint;
    }

    const resp = await fetch(`${endpoint}/add`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify(body),
    });
    const text = await resp.text();
    let parsed = {};
    try {
        parsed = text ? JSON.parse(text) : {};
    } catch {
        parsed = {raw: text};
    }
    return {ok: resp.ok, status: resp.status, body: parsed};
}

// ── context menu (right-click → "Send selection to Marginalia") ──

chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: "marginalia-send-selection",
        title: "Send selection to Marginalia",
        contexts: ["selection"],
    });
    chrome.contextMenus.create({
        id: "marginalia-send-page",
        title: "Send page to Marginalia",
        contexts: ["page"],
    });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
    if (!tab || !tab.url) {
        return;
    }
    try {
        const target = tab.url;
        const hint = info.menuItemId === "marginalia-send-selection" ? info.selectionText : "";
        const result = await sendToMarginalia({target, hint});
        // Surface a notification so the user knows it worked. Falls back
        // to console if the notifications permission isn't granted (we
        // don't request it in manifest.json — minimal-permissions
        // approach).
        console.log("[marginalia]", result);
    } catch (err) {
        console.error("[marginalia] send failed:", err);
    }
});

// Allow the popup to message-trigger the same flow via chrome.runtime.sendMessage.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type === "send-to-marginalia") {
        sendToMarginalia(message.payload)
            .then((result) => sendResponse({ok: true, result}))
            .catch((err) => sendResponse({ok: false, error: String(err)}));
        return true;  // keep the message channel open for the async response
    }
});
