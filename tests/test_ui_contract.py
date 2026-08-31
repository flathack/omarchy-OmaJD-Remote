import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class UiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.widget = (PROJECT / "BarWidget.qml").read_text(encoding="utf-8")
        cls.service = (PROJECT / "Service.qml").read_text(encoding="utf-8")

    def test_transport_controls_precede_configured_content(self):
        transport = self.widget.index('text: "TRANSPORT"')
        clicknload = self.widget.index('text: "CLICK\'N\'LOAD"')
        add_links = self.widget.index('text: "ADD LINKS"')
        self.assertLess(transport, clicknload)
        self.assertLess(transport, add_links)
        for label in ("Start downloads", "Pause downloads", "Stop downloads"):
            self.assertIn(label, self.widget)

    def test_add_links_editor_has_two_line_height_and_scrollbar(self):
        self.assertIn("Math.ceil(Style.font.body * 1.4) * 2 + Style.space(16)", self.widget)
        self.assertIn("Controls.ScrollBar.vertical.policy: Controls.ScrollBar.AsNeeded", self.widget)

    def test_linkgrabber_rename_is_keyboard_accessible(self):
        self.assertIn('Accessible.name: "Rename LinkGrabber package"', self.widget)
        self.assertIn("onAccepted: packageRow.finishRename(true)", self.widget)
        self.assertIn("Keys.onEscapePressed: packageRow.finishRename(false)", self.widget)
        self.assertIn("function renameGrabberPackage(uuid, name)", self.service)
        self.assertIn("signal renameFinished(string requestId, bool ok)", self.service)
        self.assertIn("root.pendingRenameRequest = root.backend.renameGrabberPackage", self.widget)
        self.assertIn("Rename failed · edit the preserved name and try again", self.widget)

    def test_destructive_confirmations_are_reset_and_timed(self):
        self.assertIn("function resetDestructiveState()", self.widget)
        self.assertIn("interval: 10000", self.widget)
        self.assertIn("onSelectedDeviceIdChanged", self.widget)
        self.assertIn('root.cnlRejectTarget = cnlRow.uuid', self.widget)
        self.assertIn('tooltipText: cnlRow.confirmingDismiss ? "Confirm dismiss"', self.widget)

    def test_ci_requires_qml_tools(self):
        workflow = (PROJECT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("qt6-declarative-dev-tools", workflow)
        self.assertIn("REQUIRE_QML_TOOLS=1", workflow)
        self.assertIn("REQUIRE_BROWSER_TESTS=1", workflow)

    def test_clicknload_review_exposes_provenance_and_destinations(self):
        self.assertIn("origin_verified", self.widget)
        self.assertIn("Destinations · ", self.widget)
        self.assertIn("Show full download URLs", self.widget)
        self.assertIn("cnlDetailsUrls", self.widget)
        self.assertIn('command: "cnl_details"', self.service)
        self.assertIn("Previous submission may already have reached JDownloader", self.widget)

    def test_rename_focus_is_rebound_after_model_refresh(self):
        self.assertIn("function restoreRenameFocus()", self.widget)
        self.assertIn("Qt.callLater(root.restoreRenameFocus)", self.widget)
        self.assertIn("function focusRenameEditor()", self.widget)

    def test_helper_crash_loop_has_backoff_and_manual_retry(self):
        self.assertIn("helperRetryPaused", self.service)
        self.assertIn("Math.min(60000", self.service)
        self.assertIn("Retry JDownloader helper", self.widget)
        self.assertIn("root.cnlListening = false", self.service)

    def test_manual_add_links_uncertainty_is_explicit(self):
        self.assertIn('command: retrying ? "retry_add_links" : "add_links"', self.service)
        self.assertIn("may duplicate", self.widget)

    def test_bar_icon_distinguishes_clicknload_and_linkgrabber_attention(self):
        mark = (PROJECT / "DownloadMark.qml").read_text(encoding="utf-8")
        self.assertIn("inboxAttention: root.hasClickNLoadLinks && !root.opened", self.widget)
        self.assertIn("grabberWaiting: root.hasGrabberLinks", self.widget)
        self.assertIn("SequentialAnimation on attentionPulse", mark)
        self.assertIn("root.grabberWaiting ? root.accent : root.foreground", mark)
        self.assertIn("clickNLoadLinkCount", self.widget)
        self.assertIn("grabberLinkCount", self.widget)
        self.assertIn("Accessible.name: root.barStatusText", self.widget)

    def test_connection_switch_is_persistent_keyboard_accessible_and_keeps_cnl_local(self):
        helper = (PROJECT / "jdctl.py").read_text(encoding="utf-8")
        self.assertIn("trailingControl: Component", self.widget)
        self.assertIn("component ConnectionSwitch: Item", self.widget)
        self.assertIn('Accessible.name: "MyJDownloader connection"', self.widget)
        self.assertIn("activeFocusOnTab: true", self.widget)
        self.assertIn("root.backend.setConnectionEnabled(!checked)", self.widget)
        self.assertIn('command: "set_connection_enabled"', self.service)
        self.assertIn('if command == "set_connection_enabled":', helper)
        self.assertIn("if not self.connection_enabled:", helper)
        action_guard = helper.index('raise RuntimeError("MyJDownloader connection is off; switch it on first")')
        self.assertLess(helper.index('if command == "cnl_reject":'), action_guard)
        self.assertLess(helper.index('if command == "cnl_details":'), action_guard)
        self.assertIn("id: clickNLoadSection", self.widget)
        self.assertIn("id: disconnectAccountButton", self.widget)

    def test_connection_off_hides_every_remote_section(self):
        self.assertIn("readonly property bool showRemoteSections", self.widget)
        self.assertIn("backend.connectionEnabled === true", self.widget)
        for section in ("transportSection", "addLinksSection", "downloadsSection", "grabberSection"):
            marker = f"id: {section}\n                            visible: root.showRemoteSections"
            self.assertIn(marker, self.widget)
        self.assertGreaterEqual(self.widget.count("height: visible ? implicitHeight : 0"), 5)
        self.assertIn("id: transportSeparator\n                            visible: root.showRemoteSections", self.widget)
        self.assertIn("id: instanceSection\n                            visible: root.showRemoteSections", self.widget)
        self.assertIn("function onConnectionEnabledChanged()", self.widget)

    def test_empty_package_refresh_errors_remain_visible(self):
        self.assertIn('root.backend.downloads.length > 0 || root.backend.downloadError !== ""', self.widget)
        self.assertIn('root.backend.grabber.length > 0 || root.backend.grabberError !== ""', self.widget)

    def test_clicknload_details_are_single_target_and_late_responses_are_ignored(self):
        self.assertIn("property string cnlExpandedId", self.service)
        self.assertIn('String(data.id || "") !== cnlExpandedId', self.service)
        self.assertIn("root.backend.cnlExpandedId === uuid", self.widget)


if __name__ == "__main__":
    unittest.main()
