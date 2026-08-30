import QtQuick
import Quickshell.Io

Item {
    id: root

    property var shell: null
    property var manifest: null

    property bool configured: false
    property bool connected: false
    property bool busy: false
    property bool helperReady: false
    property bool helperMissing: false
    property bool helperOutdated: false
    property bool installingHelper: false
    property var devices: []
    property string selectedDeviceId: ""
    property string selectedDeviceName: "JDownloader"
    property string controllerState: "OFFLINE"
    property int speed: 0
    property string speedText: "0 B/s"
    property var downloads: []
    property var grabber: []
    property string downloadError: ""
    property string grabberError: ""
    property bool downloadsTruncated: false
    property bool grabberTruncated: false
    property int activeDownloads: 0
    property bool cnlListening: false
    property int cnlPort: 9666
    property var cnlInbox: []
    property string cnlError: ""
    property string lastError: ""
    property string actionStatus: ""
    property bool lastActionOk: true
    property bool addLinksBusy: false
    property string pendingAddLinksRequest: ""
    property bool addLinksUncertain: false
    property string addLinksRetryToken: ""
    property string uncertainAddLinksText: ""
    property bool uncertainAddLinksAutostart: false
    property bool renameBusy: false
    property string pendingRenameRequest: ""
    property string cnlDetailsId: ""
    property string cnlExpandedId: ""
    property var cnlDetailsUrls: []
    property int cnlDetailsHiddenCount: 0
    property int requestSequence: 0
    property int revision: 0
    property int helperCrashCount: 0
    property int helperRetryDelay: 2500
    property bool helperRetryPaused: false
    property string helperRetryStatus: ""
    property double daemonStartedAt: 0

    signal addLinksFinished(string requestId, bool ok, bool uncertain)
    signal renameFinished(string requestId, bool ok)

    readonly property bool paused: controllerState.toUpperCase().indexOf("PAUSE") !== -1
    readonly property bool running: connected && (activeDownloads > 0 || controllerState.toUpperCase().indexOf("RUN") !== -1)
    readonly property string helperPath: manifest && manifest.__sourceDir ? String(manifest.__sourceDir).replace(/\/$/, "") + "/launcher.sh" : ""
    readonly property string installerPath: manifest && manifest.__sourceDir ? String(manifest.__sourceDir).replace(/\/$/, "") + "/install.sh" : ""

    function send(message) {
        if (!daemon.running) {
            lastError = "JDownloader helper is not running";
            restartTimer.restart();
            return false;
        }
        daemon.write(JSON.stringify(message) + "\n");
        return true;
    }

    function refresh() {
        send({
            command: "refresh"
        });
    }
    function selectDevice(id) {
        send({
            command: "select_device",
            device_id: String(id || "")
        });
    }
    function startDownloads() {
        send({
            command: "control",
            action: "start"
        });
    }
    function stopDownloads() {
        send({
            command: "control",
            action: "stop"
        });
    }
    function pauseDownloads(value) {
        send({
            command: "control",
            action: value ? "pause" : "resume"
        });
    }
    function forcePackage(uuid) {
        send({
            command: "force_download",
            package_ids: [String(uuid)]
        });
    }
    function addLinks(links, autostart) {
        if (addLinksBusy)
            return "";
        var requestId = "links-" + (++requestSequence);
        var normalizedLinks = String(links || "").trim();
        var retrying = addLinksUncertain && normalizedLinks === uncertainAddLinksText && autostart === uncertainAddLinksAutostart;
        uncertainAddLinksText = normalizedLinks;
        uncertainAddLinksAutostart = autostart === true;
        if (!send({
            command: retrying ? "retry_add_links" : "add_links",
            request_id: requestId,
            links: normalizedLinks,
            autostart: autostart === true,
            retry_token: retrying ? addLinksRetryToken : "",
            duplicate_confirmed: retrying
        }))
            return "";
        addLinksBusy = true;
        pendingAddLinksRequest = requestId;
        return requestId;
    }
    function failPendingAddLinks() {
        if (!addLinksBusy)
            return;
        var requestId = pendingAddLinksRequest;
        addLinksBusy = false;
        pendingAddLinksRequest = "";
        addLinksUncertain = true;
        addLinksRetryToken = "";
        addLinksFinished(requestId, false, true);
    }
    function moveGrabberPackage(uuid) {
        send({
            command: "move_grabber",
            package_ids: [String(uuid)]
        });
    }
    function renameGrabberPackage(uuid, name) {
        if (renameBusy)
            return "";
        var requestId = "rename-" + (++requestSequence);
        if (!send({
            command: "rename_grabber",
            request_id: requestId,
            package_id: String(uuid),
            name: String(name || "")
        }))
            return "";
        renameBusy = true;
        pendingRenameRequest = requestId;
        return requestId;
    }
    function failPendingRename() {
        if (!renameBusy)
            return;
        var requestId = pendingRenameRequest;
        renameBusy = false;
        pendingRenameRequest = "";
        renameFinished(requestId, false);
    }
    function removeDownloadPackage(uuid) {
        send({
            command: "remove_downloads",
            package_ids: [String(uuid)]
        });
    }
    function removeGrabberPackage(uuid) {
        send({
            command: "remove_grabber",
            package_ids: [String(uuid)]
        });
    }
    function acceptClickNLoad(id, autostart) {
        send({
            command: "cnl_accept",
            id: String(id || ""),
            autostart: autostart === true
        });
    }
    function retryClickNLoad(id, autostart) {
        send({
            command: "cnl_retry",
            id: String(id || ""),
            autostart: autostart === true
        });
    }
    function rejectClickNLoad(id) {
        send({
            command: "cnl_reject",
            id: String(id || "")
        });
    }
    function requestClickNLoadDetails(id) {
        var itemId = String(id || "");
        cnlExpandedId = itemId;
        cnlDetailsId = "";
        cnlDetailsUrls = [];
        cnlDetailsHiddenCount = 0;
        if (!send({
            command: "cnl_details",
            id: itemId
        }))
            clearClickNLoadDetails();
    }
    function clearClickNLoadDetails() {
        cnlExpandedId = "";
        cnlDetailsId = "";
        cnlDetailsUrls = [];
        cnlDetailsHiddenCount = 0;
    }
    function retryHelper() {
        helperRetryPaused = false;
        helperCrashCount = 0;
        helperRetryDelay = 2500;
        helperRetryStatus = "";
        if (helperPath !== "" && !daemon.running)
            daemon.running = true;
    }
    function configure(email, password) {
        busy = true;
        lastError = "";
        actionStatus = "Connecting to MyJDownloader…";
        if (!send({
            command: "configure",
            email: String(email || ""),
            password: String(password || "")
        }))
            busy = false;
    }
    function forgetAccount() {
        send({
            command: "forget"
        });
    }
    function installHelper() {
        if (installingHelper || installerPath === "")
            return;
        installingHelper = true;
        lastError = "";
        actionStatus = "Installing the isolated Python helper…";
        installer.command = [installerPath];
        installer.running = true;
    }

    function handleLine(line) {
        var data;
        try {
            data = JSON.parse(String(line || ""));
        } catch (error) {
            lastError = "Unreadable response from JDownloader helper";
            return;
        }

        if (data.type === "snapshot") {
            helperReady = true;
            helperMissing = false;
            helperOutdated = false;
            configured = data.configured === true;
            connected = data.connected === true;
            devices = data.devices instanceof Array ? data.devices : [];
            selectedDeviceId = String(data.selected_device_id || "");
            selectedDeviceName = String(data.selected_device_name || "JDownloader");
            controllerState = String(data.controller_state || (connected ? "IDLE" : "OFFLINE"));
            speed = Number(data.speed || 0);
            speedText = String(data.speed_text || "0 B/s");
            var nextDownloads = data.downloads instanceof Array ? data.downloads : [];
            var nextGrabber = data.grabber instanceof Array ? data.grabber : [];
            if (JSON.stringify(downloads) !== JSON.stringify(nextDownloads))
                downloads = nextDownloads;
            if (JSON.stringify(grabber) !== JSON.stringify(nextGrabber))
                grabber = nextGrabber;
            downloadError = String(data.download_error || "");
            grabberError = String(data.grabber_error || "");
            downloadsTruncated = data.downloads_truncated === true;
            grabberTruncated = data.grabber_truncated === true;
            if (data.add_links_uncertain === true) {
                addLinksUncertain = true;
                addLinksRetryToken = String(data.add_links_retry_token || addLinksRetryToken);
            }
            activeDownloads = Number(data.active_downloads || 0);
            lastError = String(data.error || "");
            busy = false;
            revision++;
            return;
        }

        if (data.type === "action") {
            busy = false;
            actionStatus = String(data.message || "");
            lastActionOk = data.ok === true;
            if (!lastActionOk)
                lastError = actionStatus || "JDownloader action failed";
            else
                lastError = "";
            if (String(data.command || "") === "add_links" || String(data.command || "") === "retry_add_links") {
                addLinksBusy = false;
                pendingAddLinksRequest = "";
                addLinksUncertain = data.uncertain === true;
                addLinksRetryToken = String(data.retry_token || "");
                if (addLinksUncertain) {
                    uncertainAddLinksText = String(data.links || uncertainAddLinksText);
                } else if (lastActionOk) {
                    uncertainAddLinksText = "";
                    uncertainAddLinksAutostart = false;
                }
                addLinksFinished(String(data.request_id || ""), lastActionOk, addLinksUncertain);
            }
            if (String(data.command || "") === "rename_grabber"
                    && String(data.request_id || "") === pendingRenameRequest) {
                var renameRequestId = pendingRenameRequest;
                renameBusy = false;
                pendingRenameRequest = "";
                renameFinished(renameRequestId, lastActionOk);
            }
            statusReset.restart();
            return;
        }

        if (data.type === "cnl") {
            cnlListening = data.listening === true;
            cnlPort = Number(data.port || 9666);
            cnlInbox = data.inbox instanceof Array ? data.inbox : [];
            if (cnlExpandedId !== "" && !cnlInbox.some(function (item) {
                return String(item.id || "") === cnlExpandedId;
            }))
                clearClickNLoadDetails();
            cnlError = String(data.error || "");
            revision++;
            return;
        }

        if (data.type === "cnl_details") {
            if (String(data.id || "") !== cnlExpandedId)
                return;
            cnlDetailsId = String(data.id || "");
            cnlDetailsUrls = data.link_urls instanceof Array ? data.link_urls : [];
            cnlDetailsHiddenCount = Number(data.hidden_link_count || 0);
            revision++;
            return;
        }

        if (data.type === "cnl_error") {
            lastError = String(data.message || "Click'n'Load request could not be read");
            statusReset.restart();
            return;
        }

        if (data.type === "fatal") {
            failPendingAddLinks();
            failPendingRename();
            busy = false;
            connected = false;
            cnlListening = false;
            cnlError = String(data.message || "JDownloader helper failed");
            clearClickNLoadDetails();
            helperReady = false;
            helperMissing = String(data.code || "") === "helper_missing" || String(data.code || "") === "helper_outdated";
            helperOutdated = String(data.code || "") === "helper_outdated";
            lastError = String(data.message || "JDownloader helper failed");
        }
    }

    Process {
        id: daemon
        command: root.helperPath ? [root.helperPath, "daemon"] : []
        stdinEnabled: true
        stdout: SplitParser {
            onRead: function (line) {
                root.handleLine(line);
            }
        }
        stderr: SplitParser {
            onRead: function (line) {
                var message = String(line || "").trim();
                if (message !== "")
                    root.lastError = message;
            }
        }
        onExited: function (exitCode) {
            root.failPendingAddLinks();
            root.failPendingRename();
            root.connected = false;
            root.cnlListening = false;
            root.cnlError = "JDownloader helper is not running";
            root.clearClickNLoadDetails();
            if (exitCode !== 0 && root.lastError === "")
                root.lastError = "JDownloader helper exited (" + exitCode + ")";
            if (root.helperMissing)
                return;
            var runtime = root.daemonStartedAt > 0 ? Date.now() - root.daemonStartedAt : 0;
            root.helperCrashCount = runtime >= 60000 ? 1 : root.helperCrashCount + 1;
            if (root.helperCrashCount >= 5) {
                root.helperRetryPaused = true;
                root.helperRetryStatus = "Automatic retries paused after 5 failures";
                root.lastError = (root.lastError !== "" ? root.lastError + " · " : "") + "Automatic helper restart paused";
                return;
            }
            root.helperRetryDelay = Math.min(60000, 2500 * Math.pow(2, root.helperCrashCount - 1));
            root.helperRetryStatus = "Helper retry " + (root.helperCrashCount + 1) + " in " + (root.helperRetryDelay / 1000) + " s";
            restartTimer.interval = root.helperRetryDelay;
            restartTimer.restart();
        }
        onRunningChanged: if (running) {
            root.daemonStartedAt = Date.now();
            root.helperRetryStatus = "";
        }
    }

    Process {
        id: installer
        stdout: SplitParser {
            onRead: function (line) {
                var message = String(line || "").trim();
                if (message !== "")
                    root.actionStatus = message;
            }
        }
        stderr: SplitParser {
            onRead: function (line) {
                var message = String(line || "").trim();
                if (message !== "")
                    root.actionStatus = message;
            }
        }
        onExited: function (exitCode) {
            root.installingHelper = false;
            if (exitCode !== 0) {
                root.lastError = "Helper installation failed (exit " + exitCode + ")";
                statusReset.restart();
                return;
            }
            root.helperMissing = false;
            root.helperOutdated = false;
            root.helperReady = true;
            root.lastError = "";
            root.actionStatus = "Helper installed";
            statusReset.restart();
            restartTimer.restart();
        }
    }

    Timer {
        id: restartTimer
        interval: 2500
        repeat: false
        onTriggered: if (root.helperPath !== "" && !daemon.running && !root.helperRetryPaused)
            daemon.running = true
    }

    Timer {
        id: statusReset
        interval: 2600
        repeat: false
        onTriggered: root.actionStatus = ""
    }

    onHelperPathChanged: if (helperPath !== "" && !daemon.running)
        daemon.running = true
    Component.onDestruction: if (daemon.running)
        daemon.running = false
}
