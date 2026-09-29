import QtQuick
import Quickshell
import "./Plugin" as Plugin

ShellRoot {
    Plugin.Service { id: helperService }
    Plugin.BarWidget {}
    Plugin.DownloadMark {}

    Timer {
        interval: 100
        running: true
        onTriggered: {
            if (!helperService.helperPath.endsWith("/Plugin/launcher.sh")
                    || !helperService.installerPath.endsWith("/Plugin/install.sh")) {
                console.error("Service script paths did not resolve without manifest.__sourceDir")
                Qt.exit(1)
                return
            }
            Qt.quit()
        }
    }
}
