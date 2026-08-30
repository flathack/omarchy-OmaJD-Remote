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
    property int requestSequence: 0
    property int revision: 0

    signal addLinksFinished(string requestId, bool ok)

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
        if (!send({
            command: "add_links",
            request_id: requestId,
            links: String(links || ""),
            autostart: autostart === true
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
        addLinksFinished(requestId, false);
    }
    function moveGrabberPackage(uuid) {
        send({
            command: "move_grabber",
            package_ids: [String(uuid)]
        });
    }
    function renameGrabberPackage(uuid, name) {
        send({
            command: "rename_grabber",
            package_id: String(uuid),
            name: String(name || "")
        });
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
    function rejectClickNLoad(id) {
        send({
            command: "cnl_reject",
            id: String(id || "")
        });
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
            downloads = data.downloads instanceof Array ? data.downloads : [];
            grabber = data.grabber instanceof Array ? data.grabber : [];
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
            if (String(data.command || "") === "add_links") {
                addLinksBusy = false;
                pendingAddLinksRequest = "";
                addLinksFinished(String(data.request_id || ""), lastActionOk);
            }
            statusReset.restart();
            return;
        }

        if (data.type === "cnl") {
            cnlListening = data.listening === true;
            cnlPort = Number(data.port || 9666);
            cnlInbox = data.inbox instanceof Array ? data.inbox : [];
            cnlError = String(data.error || "");
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
            busy = false;
            connected = false;
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
            root.connected = false;
            if (exitCode !== 0 && root.lastError === "")
                root.lastError = "JDownloader helper exited (" + exitCode + ")";
            if (!root.helperMissing)
                restartTimer.restart();
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
        onTriggered: if (root.helperPath !== "" && !daemon.running)
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
