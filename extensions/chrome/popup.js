// popup.js — UI logic for the Marginalia extension popup.
//
// Two responsibilities:
// 1. "Send page" button → message the service worker to POST the
//    current tab's URL to the configured endpoint.
// 2. Settings panel → set/persist endpoint + token in chrome.storage.local.

const DEFAULT_ENDPOINT = "http://127.0.0.1:7777";

const $ = (id) => document.getElementById(id);

async function getCurrentTab() {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
    return tab;
}

async function loadSettings() {
    const settings = await chrome.storage.local.get(["endpoint", "token"]);
    return {
        endpoint: settings.endpoint || DEFAULT_ENDPOINT,
        token: settings.token || "",
    };
}

async function saveSettings(endpoint, token) {
    await chrome.storage.local.set({endpoint, token});
}

function setStatus(text, kind = "") {
    const el = $("status");
    el.textContent = text;
    el.className = `status ${kind}`;
}

async function init() {
    const tab = await getCurrentTab();
    $("url").textContent = tab?.url || "(no active tab)";

    const settings = await loadSettings();
    $("endpoint").value = settings.endpoint;
    $("token").value = settings.token;

    // Surface a setup nudge if the user hasn't pasted a token yet.
    if (!settings.token) {
        setStatus("Open Settings → paste your auth token first", "err");
        $("config").classList.add("shown");
    }

    $("send").addEventListener("click", async () => {
        const target = tab?.url;
        if (!target) {
            setStatus("no active tab URL", "err");
            return;
        }
        setStatus("sending…");

        // Talk to the service worker via runtime message — keeps fetch +
        // settings-read in one place (background.js).
        chrome.runtime.sendMessage(
            {type: "send-to-marginalia", payload: {target}},
            (response) => {
                if (chrome.runtime.lastError) {
                    setStatus(`extension error: ${chrome.runtime.lastError.message}`, "err");
                    return;
                }
                if (!response?.ok) {
                    setStatus(`failed: ${response?.error || "unknown"}`, "err");
                    return;
                }
                const result = response.result;
                if (result.ok) {
                    setStatus(`staged ${result.body?.staged?.split("/").pop() || ""}`, "ok");
                } else {
                    setStatus(
                        `${result.status}: ${result.body?.error || "unknown error"}`,
                        "err"
                    );
                }
            }
        );
    });

    $("settings-toggle").addEventListener("click", () => {
        $("config").classList.toggle("shown");
    });

    $("save").addEventListener("click", async () => {
        const endpoint = $("endpoint").value.trim() || DEFAULT_ENDPOINT;
        const token = $("token").value.trim();
        await saveSettings(endpoint, token);
        setStatus("settings saved", "ok");
    });
}

init().catch((err) => setStatus(`init failed: ${err}`, "err"));
