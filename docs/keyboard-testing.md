# Keyboard regression matrix

Run this matrix on Omarchy Quattro with the plugin helper installed. Test both
an account with an online device and a configured account whose devices are all
offline.

| Area | Keys | Expected result |
|---|---|---|
| Open/close | Open through the configured Omarchy keybind, then `Escape` | Panel receives focus without a pointer click; Escape closes it |
| Traversal | `Tab`, `Shift+Tab`, arrows, `h j k l` | Every visible enabled action is reached in both directions; focus ring and keyboard label are visible |
| Scrolling | Traverse past the bottom and back to the top | Focused control is automatically scrolled fully into view |
| Activation | `Enter`, keypad Enter, `Space` | Focused non-destructive action runs exactly once |
| Shortcuts | `r`, `s`, `p`, `a` | Refresh, start, pause/resume, and link-editor focus work |
| Link editor | Type/paste more than two lines, scroll, then `Ctrl+Enter` | The editor stays two visible lines high, additional lines scroll, and text remains until the helper confirms success; on error it remains focused and unchanged |
| Editor exit | Focus email, password, or link editor; press `Escape` twice | First press returns to panel navigation; second closes the panel |
| Removal | Focus a download removal action, press `x`, then activate confirmation | First action only arms confirmation; confirmed action removes only the selected entry |
| Account removal | Focus disconnect, press `x`, then activate confirmation | First action changes to explicit confirmation; failure is shown as an error |
| Offline account | Traverse the configured panel while no device is online | Panel stays configured, reports no online instance, and remains closable/navigable |
| Click'n'Load inbox | Traverse accept, add-and-start, and dismiss actions | All three are keyboard reachable; dismiss affects only the focused request |
| LinkGrabber rename | Focus a package rename action, edit, then use `Enter` or `Escape` | Enter sends the trimmed name for only that package; Escape cancels without a remote action; Tab and Shift+Tab leave the editor predictably |
| Rename during refresh | Keep typing in a LinkGrabber rename field for more than 30 seconds and trigger manual refresh | Draft and text-field focus survive refresh; if the package disappears focus moves to a valid action |
| Helper crash retry | Exercise repeated helper startup failure | Retry delays increase, automatic retries stop after five failures, and the manual retry action is keyboard reachable |
| Setup | Fresh profile: traverse email, password, connect | Labels remain visible with placeholders filled; password does not appear in process arguments |

Also verify mouse controls after the keyboard pass so focus handling does not
regress pointer use.
