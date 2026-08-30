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


if __name__ == "__main__":
    unittest.main()
