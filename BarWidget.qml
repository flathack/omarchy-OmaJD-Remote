import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import qs.Commons
import qs.Ui

Panel {
    id: root

    moduleName: "io.github.flathack.omajd-remote"
    ipcTarget: "io.github.flathack.omajd-remote"

    property string removeTarget: ""
    property bool confirmForget: false
    property string keyboardHint: ""
    property string pendingLinkRequest: ""
    property string pendingRenameRequest: ""
    property string renameTarget: ""
    property string renameDraft: ""
    property bool renameEditorFocused: false
    property string cnlRejectTarget: ""
    property Item focusedControl: null
    readonly property var backend: bar && bar.shell ? bar.shell.serviceFor(root.moduleName) : null
    readonly property color foreground: bar ? bar.foreground : Color.foreground
    readonly property color dim: Qt.darker(foreground, 1.55)
    readonly property color urgent: bar ? bar.urgent : Color.urgent
    readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
    readonly property bool hasClickNLoadLinks: backend ? backend.cnlInbox.length > 0 : false
    readonly property bool hasGrabberLinks: backend ? backend.grabber.length > 0 : false
    readonly property int clickNLoadLinkCount: countLinks(backend ? backend.cnlInbox : [], "link_count")
    readonly property int grabberLinkCount: countLinks(backend ? backend.grabber : [], "child_count")
    readonly property string barStatusText: statusText()

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight

    function countLinks(items, countProperty) {
        var total = 0;
        for (var index = 0; index < items.length; index++) {
            var count = Number(items[index][countProperty] || 0);
            total += count > 0 ? count : 1;
        }
        return total;
    }

    function linkLabel(count, singular, plural) {
        return count + " " + (count === 1 ? singular : plural);
    }

    function statusText() {
        var states = [];
        if (hasClickNLoadLinks)
            states.push(linkLabel(clickNLoadLinkCount, "Click'n'Load link", "Click'n'Load links") + " awaiting review");
        if (hasGrabberLinks)
            states.push(linkLabel(grabberLinkCount, "LinkGrabber link", "LinkGrabber links") + " waiting");
        if (states.length > 0)
            return "OmaJD-Remote · " + states.join(" · ");
        if (backend && backend.configured && !backend.connected)
            return "OmaJD-Remote · JDownloader offline";
        return "OmaJD-Remote";
    }

    onOpenedChanged: {
        if (!opened) {
            resetDestructiveState();
            keyboardHint = "";
            if (!backend || !backend.renameBusy) {
                renameTarget = "";
                renameDraft = "";
                renameEditorFocused = false;
                pendingRenameRequest = "";
            }
            if (backend)
                backend.clearClickNLoadDetails();
            focusedControl = null;
            return;
        }
        if (backend)
            backend.refresh();
        Qt.callLater(function () {
            keyCatcher.forceActiveFocus();
            root.moveTabFocus(1);
        });
    }

    function collectFocusable(item, result) {
        if (!item)
            return;
        if (item !== keyCatcher && item.visible !== false && item.enabled !== false && item.activeFocusOnTab === true)
            result.push(item);
        var children = item.children || [];
        for (var index = 0; index < children.length; index++)
            collectFocusable(children[index], result);
    }

    function focusableItems() {
        var result = [];
        collectFocusable(content, result);
        result.sort(function (left, right) {
            var leftPosition = left.mapToItem(content, 0, 0);
            var rightPosition = right.mapToItem(content, 0, 0);
            if (Math.abs(leftPosition.y - rightPosition.y) > 2)
                return leftPosition.y - rightPosition.y;
            return leftPosition.x - rightPosition.x;
        });
        return result;
    }

    function moveTabFocus(direction) {
        var items = focusableItems();
        if (items.length === 0)
            return;
        var currentIndex = items.indexOf(focusedControl);
        var nextIndex = currentIndex < 0 ? (direction >= 0 ? 0 : items.length - 1) : (currentIndex + (direction >= 0 ? 1 : -1) + items.length) % items.length;
        focusedControl = items[nextIndex];
        focusedControl.forceActiveFocus();
        Qt.callLater(root.ensureFocusVisible);
    }

    function activateFocused() {
        var target = focusedControl;
        if (!target || target === keyCatcher) {
            moveTabFocus(1);
            return;
        }
        if (typeof target.clicked === "function")
            target.clicked();
    }

    function deleteFocused() {
        var target = focusedControl;
        if (target && target.destructiveAction === true && typeof target.clicked === "function")
            target.clicked();
    }

    function ensureFocusVisible() {
        var target = focusedControl;
        if (!target || target === keyCatcher || !content)
            return;
        var pos = target.mapToItem(content, 0, 0);
        var top = pos.y - Style.space(8);
        var bottom = pos.y + target.height + Style.space(8);
        if (top < panelFlick.contentY)
            panelFlick.contentY = Math.max(0, top);
        else if (bottom > panelFlick.contentY + panelFlick.height)
            panelFlick.contentY = Math.min(Math.max(0, panelFlick.contentHeight - panelFlick.height), bottom - panelFlick.height);
    }

    function leaveEditor() {
        keyCatcher.forceActiveFocus();
        keyboardHint = "Panel navigation";
        Qt.callLater(root.ensureFocusVisible);
    }

    function resetDestructiveState() {
        removeTarget = "";
        cnlRejectTarget = "";
        confirmForget = false;
        destructiveReset.stop();
    }

    function restoreRenameFocus() {
        if (renameTarget === "")
            return;
        for (var index = 0; index < grabberRepeater.count; index++) {
            var row = grabberRepeater.itemAt(index);
            if (row && row.uuid === renameTarget) {
                row.focusRenameEditor();
                return;
            }
        }
        renameTarget = "";
        renameDraft = "";
        renameEditorFocused = false;
        keyboardHint = "Renamed package is no longer available";
        keyCatcher.forceActiveFocus();
        moveTabFocus(1);
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        tooltipText: root.barStatusText
        Accessible.name: root.barStatusText
        iconComponent: Component {
            DownloadMark {
                anchors.centerIn: parent
                width: Style.space(19)
                height: width
                foreground: root.barForeground
                accent: Color.accent
                urgent: root.urgent
                active: root.backend ? root.backend.running : false
                paused: root.backend ? root.backend.paused : false
                offline: root.backend ? (!root.backend.connected && root.backend.configured) : false
                inboxAttention: root.hasClickNLoadLinks && !root.opened
                grabberWaiting: root.hasGrabberLinks
            }
        }
        onPressed: function (buttonCode) {
            if (!root.backend)
                return;
            if (buttonCode === Qt.RightButton)
                root.backend.pauseDownloads(!root.backend.paused);
            else if (buttonCode === Qt.MiddleButton)
                root.backend.refresh();
            else
                root.toggle();
        }
    }

    Rectangle {
        visible: root.hasClickNLoadLinks
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.rightMargin: Style.space(1)
        anchors.topMargin: Style.space(1)
        width: Style.space(7)
        height: width
        radius: width / 2
        color: Color.accent
        border.width: 1
        border.color: Color.background
    }

    Rectangle {
        visible: root.hasGrabberLinks
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(1)
        width: Style.space(9)
        height: Math.max(2, Style.space(2))
        radius: height / 2
        color: Color.accent
    }

    KeyboardPanel {
        id: panel
        anchorItem: button
        owner: root
        bar: root.bar
        open: root.opened
        focusTarget: keyCatcher
        contentWidth: panel.fittedContentWidth(Style.space(430))
        contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(650))

        PanelKeyCatcher {
            id: keyCatcher
            anchors.fill: parent
            blocked: emailField.activeFocus || passwordField.activeFocus || linkInput.activeFocus || root.renameEditorFocused
            onMoveRequested: function (dx, dy) {
                root.moveTabFocus(dy !== 0 ? dy : dx);
            }
            onActivateRequested: root.activateFocused()
            onCloseRequested: {
                if (root.renameTarget !== "") {
                    root.renameTarget = "";
                    root.renameDraft = "";
                    root.renameEditorFocused = false;
                    root.keyboardHint = "Rename cancelled";
                    keyCatcher.forceActiveFocus();
                } else {
                    root.close();
                }
            }
            onDeleteRequested: root.deleteFocused()
            onTabRequested: function (direction) {
                root.moveTabFocus(direction);
            }
            onTextKey: function (t) {
                if (t === "r" || t === "R")
                    root.backend.refresh();
                else if (t === "s" || t === "S")
                    root.backend.startDownloads();
                else if (t === "p" || t === "P")
                    root.backend.pauseDownloads(!root.backend.paused);
                else if ((t === "a" || t === "A") && root.backend && root.backend.configured)
                    linkInput.forceActiveFocus();
            }

            Flickable {
                id: panelFlick
                anchors.fill: parent
                contentWidth: width
                contentHeight: content.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                interactive: contentHeight > height
                Controls.ScrollBar.vertical: Controls.ScrollBar {
                    policy: Controls.ScrollBar.AsNeeded
                }

                Column {
                    id: content
                    width: parent.width
                    spacing: Style.space(12)

                    PanelHero {
                        width: parent.width
                        title: root.backend && root.backend.configured ? root.backend.selectedDeviceName : "OmaJD-Remote"
                        meta: !root.backend ? "STARTING HELPER" : !root.backend.helperReady ? "ONE-TIME HELPER SETUP" : !root.backend.configured ? "MYJDOWNLOADER SETUP" : root.backend.connected ? (root.backend.controllerState + " · " + root.backend.speedText) : "OFFLINE"
                        detail: root.backend && root.backend.connected ? String(root.backend.activeDownloads) : ""
                        foreground: root.foreground
                        fontFamily: root.fontFamily
                        iconComponent: Component {
                            DownloadMark {
                                width: Style.space(44)
                                height: width
                                foreground: root.foreground
                                accent: Color.accent
                                urgent: root.urgent
                                active: root.backend ? root.backend.running : false
                                paused: root.backend ? root.backend.paused : false
                                offline: root.backend ? (!root.backend.connected && root.backend.configured) : false
                            }
                        }
                    }

                    Text {
                        visible: root.backend && (root.backend.lastError !== "" || root.backend.actionStatus !== "")
                        width: parent.width
                        text: root.backend && root.backend.actionStatus !== "" ? root.backend.actionStatus : (root.backend ? root.backend.lastError : "")
                        color: root.backend && root.backend.actionStatus !== "" ? (root.backend.lastActionOk ? root.dim : root.urgent) : root.urgent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.bodySmall
                        wrapMode: Text.WordWrap
                    }

                    ActionButton {
                        visible: root.backend && root.backend.helperRetryPaused
                        text: "Retry JDownloader helper"
                        iconText: "󰑓"
                        foreground: root.foreground
                        bordered: true
                        onClicked: root.backend.retryHelper()
                    }

                    Text {
                        visible: root.backend && root.backend.helperRetryStatus !== ""
                        width: parent.width
                        text: root.backend ? root.backend.helperRetryStatus : ""
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        wrapMode: Text.WordWrap
                    }

                    Text {
                        visible: root.keyboardHint !== ""
                        width: parent.width
                        text: "KEYBOARD · " + root.keyboardHint
                        color: Color.accent
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        font.letterSpacing: 0.5
                        elide: Text.ElideRight
                    }

                    Column {
                        visible: root.backend && !root.backend.helperReady
                        width: parent.width
                        spacing: Style.space(10)

                        Text {
                            width: parent.width
                            text: "OmaJD-Remote uses an isolated Python helper for MyJDownloader's encrypted API. Install it once in your user profile."
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            wrapMode: Text.WordWrap
                        }

                        ActionButton {
                            width: parent.width
                            text: root.backend && root.backend.installingHelper ? (root.backend.helperOutdated ? "Repairing helper…" : "Installing helper…") : (root.backend && root.backend.helperOutdated ? "Repair helper" : "Install helper")
                            iconText: root.backend && root.backend.installingHelper ? "󰑓" : "󰇚"
                            iconSpinning: root.backend && root.backend.installingHelper
                            foreground: root.foreground
                            bordered: true
                            enabled: root.backend && !root.backend.installingHelper
                            onClicked: root.backend.installHelper()
                        }
                    }

                    Column {
                        id: setupForm
                        visible: root.backend && root.backend.helperReady && !root.backend.configured
                        width: parent.width
                        spacing: Style.space(10)

                        Text {
                            width: parent.width
                            text: "Connect your MyJDownloader account. The password is stored in the desktop keyring, never in shell.json."
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.body
                            wrapMode: Text.WordWrap
                        }

                        Text {
                            text: "Email address"
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                        }

                        TextField {
                            id: emailField
                            width: parent.width
                            foreground: root.foreground
                            placeholderText: "MyJDownloader email"
                            Accessible.name: "MyJDownloader email"
                            onActiveFocusChanged: if (activeFocus) {
                                root.focusedControl = emailField;
                                root.keyboardHint = Accessible.name;
                            }
                            Keys.onEscapePressed: root.leaveEditor()
                        }

                        Text {
                            text: "Password"
                            color: root.dim
                            font.family: root.fontFamily
                            font.pixelSize: Style.font.caption
                        }

                        TextField {
                            id: passwordField
                            width: parent.width
                            foreground: root.foreground
                            placeholderText: "Password"
                            password: true
                            Accessible.name: "MyJDownloader password"
                            onActiveFocusChanged: if (activeFocus) {
                                root.focusedControl = passwordField;
                                root.keyboardHint = Accessible.name;
                            }
                            onAccepted: connectButton.clicked()
                            Keys.onEscapePressed: root.leaveEditor()
                        }

                        ActionButton {
                            id: connectButton
                            width: parent.width
                            text: root.backend && root.backend.busy ? "Connecting…" : "Connect account"
                            iconText: "󰌾"
                            foreground: root.foreground
                            bordered: true
                            enabled: root.backend && !root.backend.busy
                            onClicked: {
                                if (!root.backend)
                                    return;
                                root.backend.configure(emailField.text, passwordField.text);
                                passwordField.text = "";
                            }
                        }
                    }

                    Column {
                        visible: root.backend && root.backend.configured
                        width: parent.width
                        spacing: Style.space(12)

                        Column {
                            width: parent.width
                            spacing: Style.space(6)

                            PanelSectionHeader {
                                text: "TRANSPORT"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            Row {
                                spacing: Style.space(5)

                                ActionButton {
                                    iconText: "󰐊"
                                    tooltipText: "Start downloads"
                                    foreground: root.foreground
                                    bordered: true
                                    horizontalPadding: Style.space(7)
                                    verticalPadding: Style.space(4)
                                    onClicked: root.backend.startDownloads()
                                }
                                ActionButton {
                                    iconText: root.backend && root.backend.paused ? "󰐊" : "󰏤"
                                    tooltipText: root.backend && root.backend.paused ? "Resume downloads" : "Pause downloads"
                                    foreground: root.foreground
                                    selected: root.backend && root.backend.paused
                                    bordered: true
                                    horizontalPadding: Style.space(7)
                                    verticalPadding: Style.space(4)
                                    onClicked: root.backend.pauseDownloads(!root.backend.paused)
                                }
                                ActionButton {
                                    iconText: "󰓛"
                                    tooltipText: "Stop downloads"
                                    foreground: root.foreground
                                    bordered: true
                                    horizontalPadding: Style.space(7)
                                    verticalPadding: Style.space(4)
                                    onClicked: root.backend.stopDownloads()
                                }
                                ActionButton {
                                    iconText: "󰑐"
                                    tooltipText: "Refresh"
                                    foreground: root.dim
                                    horizontalPadding: Style.space(7)
                                    verticalPadding: Style.space(4)
                                    onClicked: root.backend.refresh()
                                }
                            }
                        }

                        PanelSeparator {
                            foreground: root.foreground
                        }

                        Column {
                            width: parent.width
                            spacing: Style.space(7)

                            PanelSectionHeader {
                                text: "CLICK'N'LOAD"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            Row {
                                width: parent.width
                                spacing: Style.space(7)

                                Rectangle {
                                    width: Style.space(7)
                                    height: width
                                    radius: width / 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: root.backend && root.backend.cnlListening ? Color.accent : root.urgent
                                }

                                Text {
                                    width: Math.max(0, parent.width - Style.space(14))
                                    text: root.backend && root.backend.cnlListening ? "Ready on this computer · port " + root.backend.cnlPort : "Listener unavailable" + (root.backend && root.backend.cnlError !== "" ? " · " + root.backend.cnlError : "")
                                    color: root.dim
                                    font.family: root.fontFamily
                                    font.pixelSize: Style.font.caption
                                    elide: Text.ElideRight
                                }
                            }

                            Text {
                                visible: root.backend && root.backend.cnlInbox.length === 0
                                width: parent.width
                                text: "Click'n'Load buttons will appear here before anything is sent to JDownloader."
                                color: root.dim
                                font.family: root.fontFamily
                                font.pixelSize: Style.font.bodySmall
                                wrapMode: Text.WordWrap
                            }

                            Repeater {
                                model: root.backend ? root.backend.cnlInbox : []
                                ClickNLoadRow {
                                    required property var modelData
                                    width: parent.width
                                    item: modelData
                                }
                            }
                        }

                        Column {
                            visible: root.backend && root.backend.devices.length > 1
                            width: parent.width
                            spacing: Style.space(6)

                            PanelSectionHeader {
                                text: "INSTANCE"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            Repeater {
                                model: root.backend ? root.backend.devices : []
                                ActionButton {
                                    required property var modelData
                                    width: parent.width
                                    text: String(modelData.name || "JDownloader")
                                    iconText: String(modelData.id || "") === root.backend.selectedDeviceId ? "󰄬" : "󰒋"
                                    selected: String(modelData.id || "") === root.backend.selectedDeviceId
                                    foreground: root.foreground
                                    leftAlign: true
                                    onClicked: root.backend.selectDevice(String(modelData.id || ""))
                                }
                            }
                        }

                        Column {
                            width: parent.width
                            spacing: Style.space(8)

                            PanelSectionHeader {
                                text: "ADD LINKS"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            BorderSurface {
                                width: parent.width
                                height: Math.ceil(Style.font.body * 1.4) * 2 + Style.space(16)
                                color: Style.normalFillFor(root.foreground, Color.accent)
                                borderSpec: Border.controlSpec(linkInput.activeFocus ? "focus" : "normal", root.foreground, Color.accent)
                                radius: Style.cornerRadius

                                Controls.ScrollView {
                                    id: linkScroll
                                    anchors.fill: parent
                                    anchors.margins: Style.space(8)
                                    clip: true
                                    Controls.ScrollBar.horizontal.policy: Controls.ScrollBar.AlwaysOff
                                    Controls.ScrollBar.vertical.policy: Controls.ScrollBar.AsNeeded

                                    Controls.TextArea {
                                        id: linkInput
                                        width: linkScroll.availableWidth
                                        placeholderText: "Paste one or more links…"
                                        color: root.foreground
                                        placeholderTextColor: root.dim
                                        font.family: root.fontFamily
                                        font.pixelSize: Style.font.body
                                        wrapMode: TextEdit.WrapAnywhere
                                        activeFocusOnTab: true
                                        Accessible.name: "Download links"
                                        background: null
                                        onActiveFocusChanged: if (activeFocus) {
                                            root.focusedControl = linkInput;
                                            root.keyboardHint = Accessible.name + " · Ctrl+Enter sends to LinkGrabber";
                                        }
                                        Keys.onEscapePressed: root.leaveEditor()
                                        Keys.onPressed: function (event) {
                                            if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
                                                root.moveTabFocus((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1);
                                                event.accepted = true;
                                                return;
                                            }
                                            if ((event.modifiers & Qt.ControlModifier) && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
                                                if (!root.backend.addLinksBusy)
                                                    root.pendingLinkRequest = root.backend.addLinks(linkInput.text, false);
                                                event.accepted = true;
                                            }
                                        }
                                    }
                                }
                            }

                            Row {
                                spacing: Style.space(6)
                                ActionButton {
                                    text: root.backend && root.backend.addLinksUncertain && !root.backend.uncertainAddLinksAutostart && String(linkInput.text || "").trim() === root.backend.uncertainAddLinksText ? "Submit again (may duplicate)" : "To LinkGrabber"
                                    iconText: "󰌷"
                                    foreground: root.foreground
                                    bordered: true
                                    enabled: root.backend && !root.backend.addLinksBusy
                                    onClicked: {
                                        root.pendingLinkRequest = root.backend.addLinks(linkInput.text, false);
                                    }
                                }
                                ActionButton {
                                    text: root.backend && root.backend.addLinksUncertain && root.backend.uncertainAddLinksAutostart && String(linkInput.text || "").trim() === root.backend.uncertainAddLinksText ? "Start again (may duplicate)" : "Add & start"
                                    iconText: "󰐊"
                                    foreground: root.foreground
                                    bordered: true
                                    enabled: root.backend && !root.backend.addLinksBusy
                                    onClicked: {
                                        root.pendingLinkRequest = root.backend.addLinks(linkInput.text, true);
                                    }
                                }
                            }
                        }

                        PanelSeparator {
                            visible: root.backend && (root.backend.downloads.length > 0 || root.backend.downloadError !== "" || root.backend.downloadsTruncated)
                            foreground: root.foreground
                        }

                        Column {
                            visible: root.backend && (root.backend.downloads.length > 0 || root.backend.downloadError !== "" || root.backend.downloadsTruncated)
                            width: parent.width
                            spacing: Style.space(6)

                            PanelSectionHeader {
                                text: "DOWNLOADS"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            Text {
                                visible: root.backend && (root.backend.downloadError !== "" || root.backend.downloadsTruncated)
                                width: parent.width
                                text: root.backend && root.backend.downloadError !== "" ? "Download list refresh failed · " + root.backend.downloadError : "Showing the first 6000 download packages"
                                color: root.backend && root.backend.downloadError !== "" ? root.urgent : root.dim
                                font.family: root.fontFamily
                                font.pixelSize: Style.font.caption
                                wrapMode: Text.WordWrap
                            }

                            Repeater {
                                model: root.backend ? root.backend.downloads : []
                                PackageRow {
                                    required property var modelData
                                    width: parent.width
                                    item: modelData
                                    grabber: false
                                }
                            }
                        }

                        PanelSeparator {
                            visible: root.backend && (root.backend.grabber.length > 0 || root.backend.grabberError !== "" || root.backend.grabberTruncated)
                            foreground: root.foreground
                        }

                        Column {
                            visible: root.backend && (root.backend.grabber.length > 0 || root.backend.grabberError !== "" || root.backend.grabberTruncated)
                            width: parent.width
                            spacing: Style.space(6)

                            PanelSectionHeader {
                                text: "LINKGRABBER"
                                foreground: root.foreground
                                fontFamily: root.fontFamily
                            }

                            Text {
                                visible: root.backend && (root.backend.grabberError !== "" || root.backend.grabberTruncated)
                                width: parent.width
                                text: root.backend && root.backend.grabberError !== "" ? "LinkGrabber refresh failed · " + root.backend.grabberError : "Showing the first 6000 LinkGrabber packages"
                                color: root.backend && root.backend.grabberError !== "" ? root.urgent : root.dim
                                font.family: root.fontFamily
                                font.pixelSize: Style.font.caption
                                wrapMode: Text.WordWrap
                            }

                            Repeater {
                                id: grabberRepeater
                                model: root.backend ? root.backend.grabber : []
                                PackageRow {
                                    required property var modelData
                                    width: parent.width
                                    item: modelData
                                    grabber: true
                                }
                            }
                        }

                        PanelSeparator {
                            foreground: root.foreground
                        }

                        ActionButton {
                            property bool destructiveAction: true
                            text: root.confirmForget ? "Confirm account removal" : "Disconnect account"
                            iconText: root.confirmForget ? "󰅙" : "󰌺"
                            foreground: root.confirmForget ? root.urgent : root.dim
                            onClicked: {
                                if (!root.confirmForget) {
                                    root.confirmForget = true;
                                    destructiveReset.restart();
                                } else {
                                    root.backend.forgetAccount();
                                    root.confirmForget = false;
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Connections {
        target: root.backend
        ignoreUnknownSignals: true
        function onAddLinksFinished(requestId, ok, uncertain) {
            if (requestId === "" || requestId !== root.pendingLinkRequest)
                return;
            if (ok)
                linkInput.text = "";
            else {
                linkInput.forceActiveFocus();
                if (uncertain)
                    root.keyboardHint = "Previous submission may already have reached JDownloader · explicit retry may duplicate";
            }
            root.pendingLinkRequest = "";
        }
        function onRenameFinished(requestId, ok) {
            if (requestId === "" || requestId !== root.pendingRenameRequest)
                return;
            root.pendingRenameRequest = "";
            if (ok) {
                root.renameTarget = "";
                root.renameDraft = "";
                root.renameEditorFocused = false;
                root.keyboardHint = "LinkGrabber package renamed";
                keyCatcher.forceActiveFocus();
                Qt.callLater(function () { root.moveTabFocus(1); });
            } else {
                root.keyboardHint = "Rename failed · edit the preserved name and try again";
                Qt.callLater(root.restoreRenameFocus);
            }
        }
        function onSelectedDeviceIdChanged() {
            root.resetDestructiveState();
        }
        function onConfiguredChanged() {
            root.resetDestructiveState();
        }
        function onDownloadsChanged() {
            root.removeTarget = "";
        }
        function onGrabberChanged() {
            root.removeTarget = "";
            if (root.renameTarget !== "")
                Qt.callLater(root.restoreRenameFocus);
        }
        function onCnlInboxChanged() {
            if (root.cnlRejectTarget !== "" && !root.backend.cnlInbox.some(function (item) {
                return String(item.id || "") === root.cnlRejectTarget;
            }))
                root.cnlRejectTarget = "";
        }
    }

    Timer {
        id: destructiveReset
        interval: 10000
        repeat: false
        onTriggered: root.resetDestructiveState()
    }

    component PackageRow: BorderSurface {
        id: packageRow
        property var item: ({})
        property bool grabber: false
        readonly property string uuid: String(item.uuid || "")
        readonly property bool confirming: root.removeTarget === (grabber ? "g:" : "d:") + uuid
        readonly property bool editingName: grabber && root.renameTarget === uuid
        readonly property bool renamePending: editingName && root.pendingRenameRequest !== ""

        function beginRename() {
            root.removeTarget = "";
            root.renameTarget = packageRow.uuid;
            root.renameDraft = String(packageRow.item.name || "");
            Qt.callLater(function () {
                renameField.forceActiveFocus();
            });
        }

        function focusRenameEditor() {
            if (!packageRow.editingName)
                return;
            renameField.forceActiveFocus();
            root.renameEditorFocused = true;
        }

        function finishRename(save) {
            var newName = String(root.renameDraft || "").trim();
            if (save && newName === "") {
                root.keyboardHint = "Package name is required";
                renameField.forceActiveFocus();
                return;
            }
            if (save && root.backend) {
                root.pendingRenameRequest = root.backend.renameGrabberPackage(packageRow.uuid, newName);
                if (root.pendingRenameRequest !== "") {
                    root.keyboardHint = "Renaming LinkGrabber package…";
                    return;
                }
                root.keyboardHint = "Rename could not be sent · name preserved";
                renameField.forceActiveFocus();
                return;
            }
            root.renameTarget = "";
            root.renameDraft = "";
            root.renameEditorFocused = false;
            Qt.callLater(function () {
                renameButton.forceActiveFocus();
            });
        }

        color: Style.normalFillFor(root.foreground, Color.accent)
        borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
        radius: Style.cornerRadius
        implicitHeight: rowContent.implicitHeight + Style.space(12)

        Column {
            id: rowContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.space(9)
            anchors.rightMargin: Style.space(7)
            spacing: Style.space(5)

            Row {
                width: parent.width
                spacing: Style.space(6)

                Column {
                    width: Math.max(0, parent.width - actionRow.implicitWidth - Style.space(8))
                    spacing: Style.space(2)

                    Text {
                        visible: !packageRow.editingName
                        width: parent.width
                        text: String(packageRow.item.name || "Unnamed package")
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: packageRow.item.running === true
                        elide: Text.ElideMiddle
                    }
                    TextField {
                        id: renameField
                        visible: packageRow.editingName
                        enabled: !packageRow.renamePending
                        width: parent.width
                        foreground: root.foreground
                        verticalPadding: Style.space(2)
                        text: packageRow.editingName ? root.renameDraft : ""
                        placeholderText: "Package name"
                        Accessible.name: "Rename LinkGrabber package"
                        onTextChanged: if (packageRow.editingName)
                            root.renameDraft = text
                        onActiveFocusChanged: {
                            root.renameEditorFocused = activeFocus;
                            if (activeFocus) {
                                root.focusedControl = renameField;
                                root.keyboardHint = Accessible.name + " · Enter saves · Escape cancels";
                            }
                        }
                        onAccepted: packageRow.finishRename(true)
                        Keys.onEscapePressed: packageRow.finishRename(false)
                        Keys.onPressed: function (event) {
                            if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
                                root.moveTabFocus((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1);
                                event.accepted = true;
                            }
                        }
                    }
                    Text {
                        width: parent.width
                        text: packageRow.grabber ? (String(packageRow.item.size_text || "") + (packageRow.item.child_count ? " · " + packageRow.item.child_count + " links" : "")) : (String(packageRow.item.progress || 0) + "% · " + String(packageRow.item.speed_text || "0 B/s"))
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                    }
                }

                Row {
                    id: actionRow
                    spacing: Style.space(2)

                    ActionButton {
                        visible: !packageRow.editingName
                        iconText: packageRow.grabber ? "󰐊" : "󰐕"
                        tooltipText: packageRow.grabber ? "Move to downloads" : "Force download"
                        foreground: root.foreground
                        onClicked: {
                            root.removeTarget = "";
                            if (packageRow.grabber)
                                root.backend.moveGrabberPackage(packageRow.uuid);
                            else
                                root.backend.forcePackage(packageRow.uuid);
                        }
                    }
                    ActionButton {
                        id: renameButton
                        visible: packageRow.grabber && !packageRow.editingName
                        iconText: "󰏫"
                        tooltipText: "Rename package"
                        foreground: root.dim
                        onClicked: packageRow.beginRename()
                    }
                    ActionButton {
                        visible: packageRow.editingName
                        iconText: "󰄬"
                        tooltipText: "Save package name"
                        foreground: root.foreground
                        bordered: true
                        enabled: !packageRow.renamePending && String(root.renameDraft || "").trim() !== ""
                        onClicked: packageRow.finishRename(true)
                    }
                    ActionButton {
                        visible: packageRow.editingName
                        iconText: "󰅖"
                        tooltipText: "Cancel rename"
                        foreground: root.dim
                        enabled: !packageRow.renamePending
                        onClicked: packageRow.finishRename(false)
                    }
                    ActionButton {
                        visible: !packageRow.editingName
                        property bool destructiveAction: true
                        iconText: packageRow.confirming ? "󰄬" : "󰆴"
                        tooltipText: packageRow.confirming ? "Confirm removal" : (packageRow.grabber ? "Remove from LinkGrabber" : "Remove entry; keep files")
                        foreground: packageRow.confirming ? root.urgent : root.dim
                        onClicked: {
                            var key = (packageRow.grabber ? "g:" : "d:") + packageRow.uuid;
                            if (!packageRow.confirming) {
                                root.removeTarget = key;
                                destructiveReset.restart();
                                return;
                            }
                            root.removeTarget = "";
                            if (packageRow.grabber)
                                root.backend.removeGrabberPackage(packageRow.uuid);
                            else
                                root.backend.removeDownloadPackage(packageRow.uuid);
                        }
                    }
                }
            }

            Rectangle {
                visible: !packageRow.grabber
                width: parent.width
                height: Style.space(3)
                radius: height / 2
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.13)

                Rectangle {
                    width: parent.width * Math.max(0, Math.min(100, Number(packageRow.item.progress || 0))) / 100
                    height: parent.height
                    radius: parent.radius
                    color: root.foreground
                    Behavior on width {
                        NumberAnimation {
                            duration: 180
                            easing.type: Easing.OutCubic
                        }
                    }
                }
            }
        }
    }

    component ClickNLoadRow: BorderSurface {
        id: cnlRow
        property var item: ({})
        readonly property string uuid: String(item.id || "")
        readonly property bool linksExpanded: root.backend && root.backend.cnlExpandedId === uuid
        readonly property bool confirmingDismiss: root.cnlRejectTarget === uuid
        readonly property string submissionStatus: String(item.status || "pending")
        readonly property bool uncertain: submissionStatus === "uncertain"
        readonly property bool submitting: submissionStatus === "submitting"
        readonly property string hostSummary: {
            var hosts = item.link_hosts instanceof Array ? item.link_hosts : [];
            var result = hosts.join(", ");
            var hidden = Number(item.hidden_host_count || 0);
            return result + (hidden > 0 ? " · +" + hidden + " more" : "");
        }

        color: Style.normalFillFor(root.foreground, Color.accent)
        borderSpec: Border.controlSpec("normal", root.foreground, Color.accent)
        radius: Style.cornerRadius
        implicitHeight: cnlContent.implicitHeight + Style.space(14)

        Column {
            id: cnlContent
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.space(9)
            anchors.rightMargin: Style.space(7)
            spacing: Style.space(5)

            Row {
                width: parent.width
                spacing: Style.space(6)

                Column {
                    width: Math.max(0, parent.width - cnlActions.implicitWidth - Style.space(8))
                    spacing: Style.space(2)

                    Text {
                        width: parent.width
                        text: cnlRow.item.origin_verified === true ? String(cnlRow.item.origin || "Verified browser page") : "Unverified Click'n'Load request"
                        color: root.foreground
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.body
                        font.bold: true
                        elide: Text.ElideMiddle
                    }
                    Text {
                        width: parent.width
                        text: "Claims " + String(cnlRow.item.source || "unknown source") + " · " + String(cnlRow.item.link_count || 0) + (Number(cnlRow.item.link_count || 0) === 1 ? " link" : " links") + (cnlRow.item.encrypted === true ? " · CNL2" : " · CNL") + (cnlRow.item.received_at ? " · " + cnlRow.item.received_at : "")
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                    }
                    Text {
                        visible: cnlRow.hostSummary !== ""
                        width: parent.width
                        text: "Destinations · " + cnlRow.hostSummary
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideMiddle
                    }
                    Text {
                        visible: cnlRow.linksExpanded
                        width: parent.width
                        text: root.backend && root.backend.cnlDetailsId === String(cnlRow.item.id || "") ? root.backend.cnlDetailsUrls.join("\n") + (root.backend.cnlDetailsHiddenCount > 0 ? "\n… " + root.backend.cnlDetailsHiddenCount + " additional links not shown" : "") : "Loading URL preview…"
                        color: root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        wrapMode: Text.WrapAnywhere
                    }
                    Text {
                        visible: cnlRow.uncertain || cnlRow.submitting
                        width: parent.width
                        text: cnlRow.uncertain ? "Previous submission may already have reached JDownloader" : "Submitting to JDownloader…"
                        color: cnlRow.uncertain ? root.urgent : root.dim
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        wrapMode: Text.WordWrap
                    }
                }

                Row {
                    id: cnlActions
                    spacing: Style.space(2)

                    ActionButton {
                        visible: !cnlRow.submitting
                        iconText: cnlRow.linksExpanded ? "󰈈" : "󰈉"
                        tooltipText: cnlRow.linksExpanded ? "Hide full download URLs" : "Show full download URLs"
                        foreground: root.dim
                        onClicked: {
                            if (cnlRow.linksExpanded)
                                root.backend.clearClickNLoadDetails();
                            else
                                root.backend.requestClickNLoadDetails(cnlRow.uuid);
                        }
                    }

                    ActionButton {
                        visible: !cnlRow.submitting
                        iconText: "󰌷"
                        tooltipText: cnlRow.uncertain ? "Submit again to LinkGrabber; may duplicate links" : "Send to LinkGrabber"
                        foreground: root.foreground
                        onClicked: {
                            root.cnlRejectTarget = "";
                            if (cnlRow.uncertain)
                                root.backend.retryClickNLoad(String(cnlRow.item.id || ""), false);
                            else
                                root.backend.acceptClickNLoad(String(cnlRow.item.id || ""), false);
                        }
                    }
                    ActionButton {
                        visible: !cnlRow.submitting
                        iconText: "󰐊"
                        tooltipText: cnlRow.uncertain ? "Submit again and start; may duplicate downloads" : "Add and start"
                        foreground: root.foreground
                        onClicked: {
                            root.cnlRejectTarget = "";
                            if (cnlRow.uncertain)
                                root.backend.retryClickNLoad(String(cnlRow.item.id || ""), true);
                            else
                                root.backend.acceptClickNLoad(String(cnlRow.item.id || ""), true);
                        }
                    }
                    ActionButton {
                        property bool destructiveAction: true
                        iconText: cnlRow.confirmingDismiss ? "󰄬" : "󰆴"
                        tooltipText: cnlRow.confirmingDismiss ? "Confirm dismiss" : "Dismiss Click'n'Load request"
                        foreground: cnlRow.confirmingDismiss ? root.urgent : root.dim
                        onClicked: {
                            if (!cnlRow.confirmingDismiss) {
                                root.removeTarget = "";
                                root.cnlRejectTarget = cnlRow.uuid;
                                destructiveReset.restart();
                                return;
                            }
                            root.cnlRejectTarget = "";
                            root.backend.rejectClickNLoad(cnlRow.uuid);
                        }
                    }
                }
            }
        }
    }

    component ActionButton: Button {
        id: actionButton
        readonly property string keyboardName: text !== "" ? text : tooltipText
        focusable: true
        Accessible.role: Accessible.Button
        Accessible.name: keyboardName
        onActiveFocusChanged: if (activeFocus) {
            root.focusedControl = actionButton;
            root.keyboardHint = keyboardName;
            Qt.callLater(root.ensureFocusVisible);
        }
    }
}
