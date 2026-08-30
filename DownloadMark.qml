import QtQuick

Canvas {
    id: root

    property color foreground: "white"
    property color accent: foreground
    property color urgent: "#ef4444"
    property bool active: false
    property bool paused: false
    property bool offline: false
    property bool inboxAttention: false
    property bool grabberWaiting: false
    property bool motionEnabled: true
    property real phase: 0
    property real attentionPulse: 0

    implicitWidth: 24
    implicitHeight: 24
    antialiasing: true
    scale: root.inboxAttention && root.motionEnabled ? 1 + root.attentionPulse * 0.09 : 1
    opacity: root.inboxAttention && root.motionEnabled ? 0.72 + root.attentionPulse * 0.28 : 1

    onForegroundChanged: requestPaint()
    onAccentChanged: requestPaint()
    onUrgentChanged: requestPaint()
    onActiveChanged: requestPaint()
    onPausedChanged: requestPaint()
    onOfflineChanged: requestPaint()
    onInboxAttentionChanged: {
        if (!inboxAttention)
            attentionPulse = 0;
        requestPaint();
    }
    onGrabberWaitingChanged: requestPaint()
    onPhaseChanged: requestPaint()
    onWidthChanged: requestPaint()
    onHeightChanged: requestPaint()

    NumberAnimation on phase {
        from: 0
        to: Math.PI * 2
        duration: 1700
        loops: Animation.Infinite
        running: root.active && !root.paused && !root.offline
    }

    SequentialAnimation on attentionPulse {
        loops: Animation.Infinite
        running: root.inboxAttention && root.motionEnabled

        NumberAnimation {
            from: 0
            to: 1
            duration: 520
            easing.type: Easing.OutCubic
        }

        NumberAnimation {
            from: 1
            to: 0
            duration: 620
            easing.type: Easing.InOutSine
        }

        PauseAnimation {
            duration: 380
        }
    }

    onPaint: {
        var ctx = getContext("2d");
        var w = width;
        var h = height;
        var size = Math.min(w, h);
        var cx = w / 2;
        var cy = h / 2;
        var radius = size * 0.39;
        var line = Math.max(1.35, size * 0.075);
        ctx.reset();
        ctx.lineCap = "round";
        ctx.lineJoin = "round";

        ctx.strokeStyle = root.offline ? Qt.darker(root.foreground, 1.65) : (root.grabberWaiting ? root.accent : root.foreground);
        ctx.lineWidth = line;
        ctx.beginPath();
        if (root.active && !root.paused && !root.offline) {
            ctx.arc(cx, cy, radius, root.phase + 0.25, root.phase + Math.PI * 1.63);
        } else {
            ctx.arc(cx, cy, radius, -Math.PI * 0.78, Math.PI * 1.28);
        }
        ctx.stroke();

        ctx.strokeStyle = root.paused ? Qt.darker(root.foreground, 1.35) : root.foreground;
        ctx.beginPath();
        if (root.paused) {
            ctx.moveTo(cx - size * 0.095, cy - size * 0.16);
            ctx.lineTo(cx - size * 0.095, cy + size * 0.16);
            ctx.moveTo(cx + size * 0.095, cy - size * 0.16);
            ctx.lineTo(cx + size * 0.095, cy + size * 0.16);
        } else {
            ctx.moveTo(cx, cy - size * 0.22);
            ctx.lineTo(cx, cy + size * 0.15);
            ctx.moveTo(cx - size * 0.15, cy + size * 0.02);
            ctx.lineTo(cx, cy + size * 0.18);
            ctx.lineTo(cx + size * 0.15, cy + size * 0.02);
        }
        ctx.stroke();

        if (root.offline) {
            ctx.fillStyle = root.urgent;
            ctx.beginPath();
            ctx.arc(cx + radius * 0.72, cy - radius * 0.72, size * 0.09, 0, Math.PI * 2);
            ctx.fill();
        }
    }
}
