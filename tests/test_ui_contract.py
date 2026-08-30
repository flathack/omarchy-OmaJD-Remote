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

    def test_destructive_confirmations_are_reset_and_timed(self):
        self.assertIn("function resetDestructiveState()", self.widget)
        self.assertIn("interval: 10000", self.widget)
        self.assertIn("onSelectedDeviceIdChanged", self.widget)

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

    def test_manual_add_links_uncertainty_is_explicit(self):
        self.assertIn('command: retrying ? "retry_add_links" : "add_links"', self.service)
        self.assertIn("may duplicate", self.widget)


if __name__ == "__main__":
    unittest.main()
