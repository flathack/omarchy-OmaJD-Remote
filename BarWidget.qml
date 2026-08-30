import QtQuick
import QtQuick.Controls as Controls
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root

  moduleName: "io.github.flathack.omajdownload"
  ipcTarget: "io.github.flathack.omajdownload"

  property string removeTarget: ""
  property bool confirmForget: false
  readonly property var backend: bar && bar.shell ? bar.shell.serviceFor(root.moduleName) : null
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onOpenedChanged: if (opened && backend) backend.refresh()

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
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
      }
    }
    onPressed: function(buttonCode) {
      if (!root.backend) return
      if (buttonCode === Qt.RightButton) root.backend.pauseDownloads(!root.backend.paused)
      else if (buttonCode === Qt.MiddleButton) root.backend.refresh()
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    contentWidth: panel.fittedContentWidth(Style.space(430))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(650))

    Flickable {
      anchors.fill: parent
      contentWidth: width
      contentHeight: content.implicitHeight
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      flickableDirection: Flickable.VerticalFlick
      interactive: contentHeight > height
      Controls.ScrollBar.vertical: Controls.ScrollBar { policy: Controls.ScrollBar.AsNeeded }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(12)

        PanelHero {
          width: parent.width
          title: root.backend && root.backend.configured ? root.backend.selectedDeviceName : "OmaJDownLoad"
          meta: !root.backend ? "STARTING HELPER"
            : !root.backend.helperReady ? "ONE-TIME HELPER SETUP"
            : !root.backend.configured ? "MYJDOWNLOADER SETUP"
            : root.backend.connected ? (root.backend.controllerState + " · " + root.backend.speedText)
            : "OFFLINE"
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
          color: root.backend && root.backend.actionStatus !== "" ? root.dim : root.urgent
          font.family: root.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        Column {
          visible: root.backend && !root.backend.helperReady
          width: parent.width
          spacing: Style.space(10)

          Text {
            width: parent.width
            text: "OmaJDownLoad uses an isolated Python helper for MyJDownloader's encrypted API. Install it once in your user profile."
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            wrapMode: Text.WordWrap
          }

          Button {
            width: parent.width
            text: root.backend && root.backend.installingHelper ? "Installing helper…" : "Install helper"
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

          TextField {
            id: emailField
            width: parent.width
            foreground: root.foreground
            placeholderText: "MyJDownloader email"
          }

          TextField {
            id: passwordField
            width: parent.width
            foreground: root.foreground
            placeholderText: "Password"
            password: true
            onAccepted: connectButton.clicked()
          }

          Button {
            id: connectButton
            width: parent.width
            text: root.backend && root.backend.busy ? "Connecting…" : "Connect account"
            iconText: "󰌾"
            foreground: root.foreground
            bordered: true
            enabled: root.backend && !root.backend.busy
            onClicked: {
              if (!root.backend) return
              root.backend.configure(emailField.text, passwordField.text)
              passwordField.text = ""
            }
          }
        }

        Column {
          visible: root.backend && root.backend.configured
          width: parent.width
          spacing: Style.space(12)

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
              Button {
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

          PanelSeparator {
            foreground: root.foreground
          }

          Column {
            width: parent.width
            spacing: Style.space(8)

            PanelSectionHeader {
              text: "CONTROLS"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Row {
              width: parent.width
              spacing: Style.space(6)

              Button {
                text: "Start"
                iconText: "󰐊"
                foreground: root.foreground
                bordered: true
                onClicked: root.backend.startDownloads()
              }
              Button {
                text: root.backend && root.backend.paused ? "Resume" : "Pause"
                iconText: root.backend && root.backend.paused ? "󰐊" : "󰏤"
                foreground: root.foreground
                bordered: true
                onClicked: root.backend.pauseDownloads(!root.backend.paused)
              }
              Button {
                text: "Stop"
                iconText: "󰓛"
                foreground: root.foreground
                bordered: true
                onClicked: root.backend.stopDownloads()
              }
              Button {
                iconText: "󰑐"
                tooltipText: "Refresh"
                foreground: root.foreground
                onClicked: root.backend.refresh()
              }
            }
          }

          PanelSeparator {
            foreground: root.foreground
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
              height: Style.space(92)
              color: Style.normalFillFor(root.foreground, Color.accent)
              borderSpec: Border.controlSpec(linkInput.activeFocus ? "focus" : "normal", root.foreground, Color.accent)
              radius: Style.cornerRadius

              Controls.TextArea {
                id: linkInput
                anchors.fill: parent
                anchors.margins: Style.space(8)
                placeholderText: "Paste one or more links…"
                color: root.foreground
                placeholderTextColor: root.dim
                font.family: root.fontFamily
                font.pixelSize: Style.font.body
                wrapMode: TextEdit.WrapAnywhere
                background: null
              }
            }

            Row {
              spacing: Style.space(6)
              Button {
                text: "To LinkGrabber"
                iconText: "󰌷"
                foreground: root.foreground
                bordered: true
                onClicked: {
                  root.backend.addLinks(linkInput.text, false)
                  if (linkInput.text.trim() !== "") linkInput.text = ""
                }
              }
              Button {
                text: "Add & start"
                iconText: "󰐊"
                foreground: root.foreground
                bordered: true
                onClicked: {
                  root.backend.addLinks(linkInput.text, true)
                  if (linkInput.text.trim() !== "") linkInput.text = ""
                }
              }
            }
          }

          PanelSeparator {
            visible: root.backend && root.backend.downloads.length > 0
            foreground: root.foreground
          }

          Column {
            visible: root.backend && root.backend.downloads.length > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "DOWNLOADS"
              foreground: root.foreground
              fontFamily: root.fontFamily
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
            visible: root.backend && root.backend.grabber.length > 0
            foreground: root.foreground
          }

          Column {
            visible: root.backend && root.backend.grabber.length > 0
            width: parent.width
            spacing: Style.space(6)

            PanelSectionHeader {
              text: "LINKGRABBER"
              foreground: root.foreground
              fontFamily: root.fontFamily
            }

            Repeater {
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

          Button {
            text: root.confirmForget ? "Confirm account removal" : "Disconnect account"
            iconText: root.confirmForget ? "󰅙" : "󰌺"
            foreground: root.confirmForget ? root.urgent : root.dim
            onClicked: {
              if (!root.confirmForget) {
                root.confirmForget = true
              } else {
                root.backend.forgetAccount()
                root.confirmForget = false
              }
            }
          }
        }
      }
    }
  }

  component PackageRow: BorderSurface {
    id: packageRow
    property var item: ({})
    property bool grabber: false
    readonly property string uuid: String(item.uuid || "")
    readonly property bool confirming: root.removeTarget === (grabber ? "g:" : "d:") + uuid

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
            width: parent.width
            text: String(packageRow.item.name || "Unnamed package")
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
            font.bold: packageRow.item.running === true
            elide: Text.ElideMiddle
          }
          Text {
            width: parent.width
            text: packageRow.grabber
              ? (String(packageRow.item.size_text || "") + (packageRow.item.child_count ? " · " + packageRow.item.child_count + " links" : ""))
              : (String(packageRow.item.progress || 0) + "% · " + String(packageRow.item.speed_text || "0 B/s"))
            color: root.dim
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
          }
        }

        Row {
          id: actionRow
          spacing: Style.space(2)

          Button {
            iconText: packageRow.grabber ? "󰐊" : "󰐕"
            tooltipText: packageRow.grabber ? "Move to downloads" : "Force download"
            foreground: root.foreground
            onClicked: {
              root.removeTarget = ""
              if (packageRow.grabber) root.backend.moveGrabberPackage(packageRow.uuid)
              else root.backend.forcePackage(packageRow.uuid)
            }
          }
          Button {
            iconText: packageRow.confirming ? "󰄬" : "󰆴"
            tooltipText: packageRow.confirming ? "Confirm removal" : (packageRow.grabber ? "Remove from LinkGrabber" : "Remove entry; keep files")
            foreground: packageRow.confirming ? root.urgent : root.dim
            onClicked: {
              var key = (packageRow.grabber ? "g:" : "d:") + packageRow.uuid
              if (!packageRow.confirming) {
                root.removeTarget = key
                return
              }
              root.removeTarget = ""
              if (packageRow.grabber) root.backend.removeGrabberPackage(packageRow.uuid)
              else root.backend.removeDownloadPackage(packageRow.uuid)
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
          Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        }
      }
    }
  }
}
