import QtQuick
import Quickshell
import "./Plugin" as Plugin

ShellRoot {
    Plugin.Service {}
    Plugin.BarWidget {}
    Plugin.DownloadMark {}

    Timer {
        interval: 100
        running: true
        onTriggered: Qt.quit()
    }
}
