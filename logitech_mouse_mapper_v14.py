#!/usr/bin/env python3
"""
Logitech LIFT Button Mapper  (logiops GUI front-end)
====================================================

Extended version with:
  * DPI & scroll settings (slider, SmartShift threshold, natural/hi‑res scroll)
  * Gesture mapping (up/down/left/right) for thumb & middle buttons
  * VID/PID detection and manual override
  * Battery status readout (shows only percentage when status unknown)
  * Tooltips on schematic showing assigned functions
  * Service management: enable/restart logid, persistent across reboots

Run:  python lift_mapper.py
Flags: --skip-deps, --auto-deps, --qt5
"""

# =============================================================================
#  ENVIRONMENT SETUP  --  suppress noisy DBus warnings, prefer Wayland
# =============================================================================
import os
import sys

os.environ["QT_LOGGING_RULES"] = "qt.dbus.*=false"
if os.environ.get("WAYLAND_DISPLAY") and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "wayland"

if os.geteuid() == 0:
    print("⚠️  Running with sudo may cause DBus session issues.")
    print("   → It's recommended to run without sudo (the app uses pkexec internally).")
    print("   → Press Ctrl+C to cancel, or continue anyway.\n")

# =============================================================================
#  STDLIB-ONLY BOOTSTRAP  --  runs BEFORE importing PyQt so we can install it.
# =============================================================================
import re
import glob
import shutil
import tempfile
import subprocess
import importlib.util

def _read_os_release():
    data = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    data[k] = v.strip().strip('"')
    except OSError:
        pass
    return data

def _detect_distro():
    info = _read_os_release()
    ident = (info.get("ID", "") + " " + info.get("ID_LIKE", "")).lower()
    if any(x in ident for x in ("arch", "manjaro", "endeavour", "cachyos")):
        return "arch"
    if any(x in ident for x in ("fedora", "rhel", "centos", "rocky", "alma")):
        return "fedora"
    if any(x in ident for x in ("debian", "ubuntu", "mint", "pop")):
        return "debian"
    return ""

def _prefer_qt6():
    return "--qt5" not in sys.argv

def _has_pyqt6():
    return importlib.util.find_spec("PyQt6") is not None

def _has_pyqt5():
    return importlib.util.find_spec("PyQt5") is not None

def _available_qt():
    if _prefer_qt6() and _has_pyqt6():
        return "6"
    if _has_pyqt5():
        return "5"
    if _has_pyqt6():
        return "6"
    return None

def _has_wayland_plugin(qt_version="6"):
    bases = [
        f"/usr/lib/qt{qt_version}/plugins",
        "/usr/lib/qt/plugins",
        "/usr/lib/qt5/plugins",
        f"/usr/lib64/qt{qt_version}/plugins",
        "/usr/lib64/qt5/plugins",
        f"/usr/lib/x86_64-linux-gnu/qt{qt_version}/plugins",
        f"/usr/lib/aarch64-linux-gnu/qt{qt_version}/plugins",
        "/usr/lib/x86_64-linux-gnu/qt5/plugins",
        "/usr/lib/aarch64-linux-gnu/qt5/plugins",
    ]
    seen = set()
    bases = [b for b in bases if not (b in seen or seen.add(b))]
    for b in bases:
        if glob.glob(os.path.join(b, "platforms", "libqwayland*.so")):
            return True
    return False

def _missing_dependencies():
    missing = []
    qt_ver = _available_qt()
    if qt_ver is None:
        if _prefer_qt6():
            missing.append(("pyqt6", "PyQt6 (python GUI toolkit)"))
        else:
            missing.append(("pyqt5", "PyQt5 (python GUI toolkit)"))
    else:
        if os.environ.get("WAYLAND_DISPLAY"):
            if qt_ver == "6" and not _has_wayland_plugin("6"):
                missing.append(("wayland6", "Qt6 Wayland platform plugin"))
            elif qt_ver == "5" and not _has_wayland_plugin("5"):
                missing.append(("wayland5", "Qt5 Wayland platform plugin"))
    if not shutil.which("pkexec"):
        missing.append(("polkit", "polkit / pkexec (privilege prompt)"))
    if not shutil.which("logid"):
        missing.append(("logiops", "logiops / logid (the actual mouse driver)"))
    return missing

def _arch_aur_helper():
    for h in ("paru", "yay"):
        if shutil.which(h):
            return h
    return None

def _run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, **kw)

def _install_arch(keys):
    helper = _arch_aur_helper()
    repo = {
        "pyqt6": "python-pyqt6",
        "pyqt5": "python-pyqt5",
        "wayland6": "qt6-wayland",
        "wayland5": "qt5-wayland",
        "polkit": "polkit",
    }
    repo_pkgs = [repo[k] for k in keys if k in repo]
    if repo_pkgs:
        _run(["sudo", "pacman", "-S", "--needed", "--noconfirm", *repo_pkgs])
    if "logiops" in keys:
        if helper:
            _run([helper, "-S", "--needed", "--noconfirm", "logiops"])
        else:
            print("  ! No AUR helper (paru/yay) found. Install one, then:")
            print("      paru -S logiops")

def _install_fedora(keys):
    repo = {
        "pyqt6": "python3-pyqt6",
        "pyqt5": "python3-qt5",
        "wayland6": "qt6-qtwayland",
        "wayland5": "qt5-qtwayland",
        "polkit": "polkit",
    }
    repo_pkgs = [repo[k] for k in keys if k in repo]
    if repo_pkgs:
        _run(["sudo", "dnf", "install", "-y", *repo_pkgs])
    if "logiops" in keys:
        _run(["sudo", "dnf", "copr", "enable", "-y", "peterwu/logiops"])
        r = _run(["sudo", "dnf", "install", "-y", "logiops"])
        if r.returncode != 0:
            print("  ! COPR install failed -- build from source instead:")
            _print_source_build()

def _install_debian(keys):
    repo = {
        "pyqt6": "python3-pyqt6",
        "pyqt5": "python3-pyqt5",
        "wayland6": "qt6-wayland",
        "wayland5": "qtwayland5",
        "polkit": "policykit-1",
    }
    repo_pkgs = [repo[k] for k in keys if k in repo]
    if repo_pkgs:
        _run(["sudo", "apt-get", "update"])
        _run(["sudo", "apt-get", "install", "-y", *repo_pkgs])
    if "logiops" in keys:
        if _confirm("logiops isn't in apt. Build it from source now?"):
            _build_logiops_from_source()
        else:
            _print_source_build()

def _print_source_build():
    print("""
    Build logiops from source:
      sudo apt-get install -y cmake libevdev-dev libudev-dev \\
          libconfig++-dev libglib2.0-dev build-essential git
      git clone https://github.com/PixlOne/logiops.git
      cd logiops && mkdir build && cd build
      cmake .. -DCMAKE_BUILD_TYPE=Release
      make -j"$(nproc)"
      sudo make install
      sudo systemctl daemon-reload
""")

def _build_logiops_from_source():
    deps = ["cmake", "libevdev-dev", "libudev-dev", "libconfig++-dev",
            "libglib2.0-dev", "build-essential", "git"]
    _run(["sudo", "apt-get", "install", "-y", *deps])
    tmp = tempfile.mkdtemp(prefix="logiops-")
    try:
        if _run(["git", "clone", "--depth", "1",
                 "https://github.com/PixlOne/logiops.git", tmp]).returncode != 0:
            print("  ! git clone failed."); return
        build = os.path.join(tmp, "build")
        os.makedirs(build, exist_ok=True)
        if _run(["cmake", "..", "-DCMAKE_BUILD_TYPE=Release"],
                cwd=build).returncode != 0:
            print("  ! cmake failed."); return
        if _run(["make", "-j", str(os.cpu_count() or 2)],
                cwd=build).returncode != 0:
            print("  ! make failed."); return
        _run(["sudo", "make", "install"], cwd=build)
        _run(["sudo", "systemctl", "daemon-reload"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def _confirm(question):
    if "--auto-deps" in sys.argv:
        return True
    if not sys.stdin.isatty():
        return False
    try:
        return input(f"{question} [Y/n]: ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False

def ensure_dependencies():
    if "--skip-deps" in sys.argv:
        return
    missing = _missing_dependencies()
    if not missing:
        return
    distro = _detect_distro()
    print("=" * 64)
    print(" Logitech LIFT Mapper -- dependency check")
    print("=" * 64)
    print(" Detected distro family: " + (distro or "unknown"))
    print(" Missing components:")
    for _k, label in missing:
        print("   [ NO ]  " + label)
    print("-" * 64)

    installer = {
        "arch": _install_arch,
        "fedora": _install_fedora,
        "debian": _install_debian,
    }.get(distro)

    if installer is None:
        print(" Unsupported distro for auto-install. Please install manually:")
        print("   PyQt5/PyQt6, the Qt Wayland plugin, polkit, and logiops.")
        if _available_qt() is None:
            sys.exit(1)
        return

    if not _confirm("Install the missing components now?"):
        print(" Skipping install. The program may not work correctly.")
        if _available_qt() is None:
            print(" PyQt is required to show the GUI -- exiting.")
            sys.exit(1)
        return

    keys = [k for k, _ in missing]
    pyqt_was_missing = any(k.startswith("pyqt") for k in keys)
    try:
        installer(keys)
    except Exception as e:
        print(f" Install step failed: {e}")

    if pyqt_was_missing and _available_qt() is None:
        importlib.invalidate_caches()
    if pyqt_was_missing:
        print("\n Re-launching with the newly installed dependencies...\n")
        os.execv(sys.executable,
                 [sys.executable, os.path.abspath(__file__),
                  "--skip-deps", *[a for a in sys.argv[1:]
                                   if a not in ("--skip-deps",)]])

ensure_dependencies()

# =============================================================================
#  PYQT IMPORT  --  try PyQt6 first, fallback to PyQt5
# =============================================================================

_USE_QT6 = _prefer_qt6() and _has_pyqt6()
if not _USE_QT6 and _has_pyqt5():
    _USE_QT6 = False
elif not _USE_QT6 and not _has_pyqt5():
    if _has_pyqt6():
        _USE_QT6 = True
    else:
        print("ERROR: Neither PyQt6 nor PyQt5 found!")
        sys.exit(1)

if _USE_QT6:
    from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, qInstallMessageHandler, QThread, QTimer
    from PyQt6.QtGui import (
        QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
        QRadialGradient, QPainterPath,
    )
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QMainWindow, QLabel, QComboBox, QPushButton,
        QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QMessageBox,
        QPlainTextEdit, QSizePolicy, QDialog, QDialogButtonBox, QFileDialog,
        QSlider, QCheckBox, QSpinBox,
    )
    ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
    ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft
    ALIGN_TOP = Qt.AlignmentFlag.AlignTop
    ALIGN_VCENTER = Qt.AlignmentFlag.AlignVCenter
    POLICY_EXPANDING = QSizePolicy.Policy.Expanding
    BUTTONBOX_OK = QDialogButtonBox.StandardButton.Ok
    BUTTONBOX_CANCEL = QDialogButtonBox.StandardButton.Cancel
    PEN_STYLE_NONE = Qt.PenStyle.NoPen
    CURSOR_POINTING = Qt.CursorShape.PointingHandCursor
    CURSOR_ARROW = Qt.CursorShape.ArrowCursor
else:
    from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal, qInstallMessageHandler, QThread, QTimer
    from PyQt5.QtGui import (
        QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
        QRadialGradient, QPainterPath,
    )
    from PyQt5.QtWidgets import (
        QApplication, QWidget, QMainWindow, QLabel, QComboBox, QPushButton,
        QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox, QLineEdit, QMessageBox,
        QPlainTextEdit, QSizePolicy, QDialog, QDialogButtonBox, QFileDialog,
        QSlider, QCheckBox, QSpinBox,
    )
    ALIGN_CENTER = Qt.AlignCenter
    ALIGN_LEFT = Qt.AlignLeft
    ALIGN_TOP = Qt.AlignTop
    ALIGN_VCENTER = Qt.AlignVCenter
    POLICY_EXPANDING = QSizePolicy.Expanding
    BUTTONBOX_OK = QDialogButtonBox.Ok
    BUTTONBOX_CANCEL = QDialogButtonBox.Cancel
    PEN_STYLE_NONE = Qt.NoPen
    CURSOR_POINTING = Qt.PointingHandCursor
    CURSOR_ARROW = Qt.ArrowCursor

# Qt message filter to suppress DBus noise
def _qt_message_filter(mode, context, message):
    noise = (
        "requestActivate",
        "QSocketNotifier: Can only be used with threads",
        "Wayland does not support QWindow::requestActivate",
        "Ignoring XDG_SESSION_TYPE",
        "DBus",
        "dbus",
        "D-Bus",
    )
    if any(n in message for n in noise):
        return
    sys.stderr.write(message + "\n")

qInstallMessageHandler(_qt_message_filter)

CONFIG_FILE = "/etc/logid.cfg"
DEFAULT_DEVICE_NAME = "Logitech Lift"

# ---------------------------------------------------------------------------
# Known Logitech / logiops functions.
# ---------------------------------------------------------------------------
def _keypress(*keys):
    joined = ", ".join(f'"{k}"' for k in keys)
    return f'action = {{ type: "Keypress"; keys: [{joined}]; }};'

KNOWN_FUNCTIONS = {
    "— Default (leave unchanged) —": "DEFAULT",
    "Copy  (Ctrl+C)":            lambda: _keypress("KEY_LEFTCTRL", "KEY_C"),
    "Paste  (Ctrl+V)":           lambda: _keypress("KEY_LEFTCTRL", "KEY_V"),
    "Cut  (Ctrl+X)":             lambda: _keypress("KEY_LEFTCTRL", "KEY_X"),
    "Undo  (Ctrl+Z)":            lambda: _keypress("KEY_LEFTCTRL", "KEY_Z"),
    "Redo  (Ctrl+Shift+Z)":      lambda: _keypress("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"),
    "Select All  (Ctrl+A)":      lambda: _keypress("KEY_LEFTCTRL", "KEY_A"),
    "Save  (Ctrl+S)":            lambda: _keypress("KEY_LEFTCTRL", "KEY_S"),
    "Find  (Ctrl+F)":            lambda: _keypress("KEY_LEFTCTRL", "KEY_F"),
    "Browser Back  (Alt+Left)":  lambda: _keypress("KEY_LEFTALT", "KEY_LEFT"),
    "Browser Forward (Alt+Right)": lambda: _keypress("KEY_LEFTALT", "KEY_RIGHT"),
    "Back key  (XF86Back)":       lambda: _keypress("KEY_BACK"),
    "Forward key  (XF86Forward)": lambda: _keypress("KEY_FORWARD"),
    "Switch Window  (Alt+Tab)":  lambda: _keypress("KEY_LEFTALT", "KEY_TAB"),
    "Show Desktop  (Super+D)":   lambda: _keypress("KEY_LEFTMETA", "KEY_D"),
    "Close Window  (Alt+F4)":    lambda: _keypress("KEY_LEFTALT", "KEY_F4"),
    "New Tab  (Ctrl+T)":         lambda: _keypress("KEY_LEFTCTRL", "KEY_T"),
    "Close Tab  (Ctrl+W)":       lambda: _keypress("KEY_LEFTCTRL", "KEY_W"),
    "Zoom In  (Ctrl++)":         lambda: _keypress("KEY_LEFTCTRL", "KEY_EQUAL"),
    "Zoom Out  (Ctrl+-)":        lambda: _keypress("KEY_LEFTCTRL", "KEY_MINUS"),
    "Play / Pause":              lambda: _keypress("KEY_PLAYPAUSE"),
    "Next Track":                lambda: _keypress("KEY_NEXTSONG"),
    "Previous Track":            lambda: _keypress("KEY_PREVIOUSSONG"),
    "Volume Up":                 lambda: _keypress("KEY_VOLUMEUP"),
    "Volume Down":               lambda: _keypress("KEY_VOLUMEDOWN"),
    "Mute":                      lambda: _keypress("KEY_MUTE"),
    "Screenshot  (PrintScreen)": lambda: _keypress("KEY_PRINT"),
    "Middle Click  (button 3)":  lambda: 'action = { type: "Keypress"; keys: ["BTN_MIDDLE"]; };',
    "Toggle SmartShift":         lambda: 'action = { type: "ToggleSmartShift"; };',
    "Toggle Hi-Res Scroll":      lambda: 'action = { type: "ToggleHiresScroll"; };',
    "Cycle DPI":                 lambda: 'action = { type: "CycleDPI"; sensors: [0]; };',
    "Custom keystroke…":         "CUSTOM",
}

FUNCTION_NAMES = list(KNOWN_FUNCTIONS.keys())

# Reverse map from key tuples to function names
REVERSE_KEY_MAP = {
    ("KEY_LEFTCTRL", "KEY_C"): "Copy  (Ctrl+C)",
    ("KEY_LEFTCTRL", "KEY_V"): "Paste  (Ctrl+V)",
    ("KEY_LEFTCTRL", "KEY_X"): "Cut  (Ctrl+X)",
    ("KEY_LEFTCTRL", "KEY_Z"): "Undo  (Ctrl+Z)",
    ("KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_Z"): "Redo  (Ctrl+Shift+Z)",
    ("KEY_LEFTCTRL", "KEY_A"): "Select All  (Ctrl+A)",
    ("KEY_LEFTCTRL", "KEY_S"): "Save  (Ctrl+S)",
    ("KEY_LEFTCTRL", "KEY_F"): "Find  (Ctrl+F)",
    ("KEY_LEFTALT", "KEY_LEFT"): "Browser Back  (Alt+Left)",
    ("KEY_LEFTALT", "KEY_RIGHT"): "Browser Forward (Alt+Right)",
    ("KEY_BACK",): "Back key  (XF86Back)",
    ("KEY_FORWARD",): "Forward key  (XF86Forward)",
    ("KEY_LEFTALT", "KEY_TAB"): "Switch Window  (Alt+Tab)",
    ("KEY_LEFTMETA", "KEY_D"): "Show Desktop  (Super+D)",
    ("KEY_LEFTALT", "KEY_F4"): "Close Window  (Alt+F4)",
    ("KEY_LEFTCTRL", "KEY_T"): "New Tab  (Ctrl+T)",
    ("KEY_LEFTCTRL", "KEY_W"): "Close Tab  (Ctrl+W)",
    ("KEY_LEFTCTRL", "KEY_EQUAL"): "Zoom In  (Ctrl++)",
    ("KEY_LEFTCTRL", "KEY_MINUS"): "Zoom Out  (Ctrl+-)",
    ("KEY_PLAYPAUSE",): "Play / Pause",
    ("KEY_NEXTSONG",): "Next Track",
    ("KEY_PREVIOUSSONG",): "Previous Track",
    ("KEY_VOLUMEUP",): "Volume Up",
    ("KEY_VOLUMEDOWN",): "Volume Down",
    ("KEY_MUTE",): "Mute",
    ("KEY_PRINT",): "Screenshot  (PrintScreen)",
    ("BTN_MIDDLE",): "Middle Click  (button 3)",
}

SPECIAL_ACTIONS = {
    "ToggleSmartShift": "Toggle SmartShift",
    "ToggleHiresScroll": "Toggle Hi-Res Scroll",
    "CycleDPI": "Cycle DPI",
}

# All possible buttons (full list)
ALL_BUTTONS = [
    ("left",    "Left click",          "0x50", (0.30, 0.08, 0.18, 0.26)),
    ("right",   "Right click",         "0x51", (0.55, 0.08, 0.18, 0.26)),
    ("middle",  "Wheel (middle) click", "0x52", (0.46, 0.13, 0.11, 0.17)),
    ("top",     "Top button",          "0xc4", (0.47, 0.36, 0.12, 0.07)),
    ("forward", "Forward (thumb, upper)", "0x56", (0.02, 0.20, 0.17, 0.11)),
    ("side3",   "Side button (middle)",   "0x57", (0.02, 0.34, 0.17, 0.11)),
    ("back",    "Back (thumb, lower)",    "0x53", (0.02, 0.48, 0.17, 0.12)),
]

DEFAULT_SELECTION = {
    "back": "Copy  (Ctrl+C)",
    "forward": "Paste  (Ctrl+V)",
    "side3": "— Default (leave unchanged) —",
}

SIDE_BUTTON_MAP = {
    "lift": 2,
    "mx master": 2,
    "mx anywhere": 2,
    "m720": 2,
    "m590": 2,
    "g603": 2,
    "g703": 2,
    "g900": 2,
    "g903": 2,
    "pro x": 2,
    "g502": 3,
    "g604": 3,
    "g pro": 3,
}

def _side_count_for_device(name):
    if not name:
        return None
    name_lower = name.lower()
    for pattern, count in SIDE_BUTTON_MAP.items():
        if pattern in name_lower:
            return count
    return None

# ---------------------------------------------------------------------------
# Custom keystroke dialog
# ---------------------------------------------------------------------------
class CustomKeyDialog(QDialog):
    def __init__(self, parent=None, initial=""):
        super().__init__(parent)
        self.setWindowTitle("Custom keystroke")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "Enter KEY_* codes separated by '+', e.g.\n"
            "  KEY_LEFTCTRL + KEY_LEFTSHIFT + KEY_ESC"))
        self.edit = QLineEdit(initial or "KEY_LEFTCTRL + KEY_")
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(BUTTONBOX_OK | BUTTONBOX_CANCEL)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def keys(self):
        parts = [k.strip().upper() for k in self.edit.text().split("+")]
        return [k for k in parts if k.startswith("KEY_") or k.startswith("BTN_")]

# ---------------------------------------------------------------------------
# Gesture dialog
# ---------------------------------------------------------------------------
class GestureDialog(QDialog):
    def __init__(self, parent=None, current=None):
        super().__init__(parent)
        self.setWindowTitle("Gesture Actions")
        self.setMinimumWidth(400)
        lay = QVBoxLayout(self)
        self.combos = {}
        directions = ["up", "down", "left", "right"]
        grid = QGridLayout()
        for i, d in enumerate(directions):
            grid.addWidget(QLabel(d.capitalize()), i, 0)
            combo = QComboBox()
            combo.addItems(FUNCTION_NAMES)
            if current and d in current and current[d] in FUNCTION_NAMES:
                combo.setCurrentText(current[d])
            else:
                combo.setCurrentText(FUNCTION_NAMES[0])
            grid.addWidget(combo, i, 1)
            self.combos[d] = combo
        lay.addLayout(grid)

        btn_layout = QHBoxLayout()
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self.clear_all)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        bb = QDialogButtonBox(BUTTONBOX_OK | BUTTONBOX_CANCEL)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        btn_layout.addWidget(bb)
        lay.addLayout(btn_layout)

    def clear_all(self):
        for combo in self.combos.values():
            combo.setCurrentText(FUNCTION_NAMES[0])

    def get_gestures(self):
        result = {}
        for d, combo in self.combos.items():
            txt = combo.currentText()
            if txt != FUNCTION_NAMES[0]:
                result[d] = txt
        return result

# ---------------------------------------------------------------------------
# Worker thread for detection
# ---------------------------------------------------------------------------
class DetectWorker(QThread):
    finished = pyqtSignal(str)

    def run(self):
        try:
            subprocess.run(["pkexec", "systemctl", "stop", "logid"],
                           check=False, timeout=5)
        except Exception:
            pass
        try:
            proc = subprocess.run(
                ["pkexec", "bash", "-c", "timeout 6 logid -v 2>&1 || true"],
                capture_output=True, text=True, timeout=15)
            out = proc.stdout
        except Exception as e:
            out = f"Detection error: {e}"
        self.finished.emit(out)

# ---------------------------------------------------------------------------
# Schematic widget - prettier drawing with tooltips
# ---------------------------------------------------------------------------
class MouseSchematic(QWidget):
    buttonClicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 440)
        self.setSizePolicy(QSizePolicy(POLICY_EXPANDING, POLICY_EXPANDING))
        self.setMouseTracking(True)
        self._hover = None
        self._assigned = {}
        self._function_names = {}
        self._buttons = ALL_BUTTONS

    def set_assigned(self, assigned, function_names=None):
        self._assigned = assigned
        if function_names is not None:
            self._function_names = function_names
        self.update()

    def set_buttons(self, buttons):
        self._buttons = buttons
        self.update()

    def _body_rect(self):
        w, h = self.width(), self.height()
        m = 0.12
        return QRectF(w * m, h * 0.05, w * (1 - 2 * m), h * 0.90)

    def _hotspot_rect(self, spec):
        b = self._body_rect()
        x, y, ww, hh = spec
        return QRectF(b.x() + b.width() * x, b.y() + b.height() * y,
                      b.width() * ww, b.height() * hh)

    def _pt(self, fx, fy):
        b = self._body_rect()
        return QPointF(b.x() + b.width() * fx, b.y() + b.height() * fy)

    def mouseMoveEvent(self, ev):
        hit = None
        pos = QPointF(ev.pos())
        for key, _l, _c, spec in self._buttons:
            if self._hotspot_rect(spec).contains(pos):
                hit = key
                break
        if hit != self._hover:
            self._hover = hit
            self.setCursor(CURSOR_POINTING if hit else CURSOR_ARROW)
            if hit and hit in self._function_names:
                self.setToolTip(f"{hit}: {self._function_names[hit]}")
            else:
                self.setToolTip("")
            self.update()

    def mousePressEvent(self, ev):
        pos = QPointF(ev.pos())
        for key, _l, _c, spec in self._buttons:
            if self._hotspot_rect(spec).contains(pos):
                self.buttonClicked.emit(key)
                return

    def leaveEvent(self, ev):
        self._hover = None
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        bg = QLinearGradient(0, 0, 0, self.height())
        bg.setColorAt(0, QColor("#24262f"))
        bg.setColorAt(1, QColor("#191a20"))
        p.fillRect(self.rect(), QBrush(bg))

        b = self._body_rect()
        pt = self._pt

        shadow = QPainterPath()
        shadow.addEllipse(QRectF(b.x() + b.width() * 0.10,
                                 b.y() + b.height() * 0.90,
                                 b.width() * 0.80, b.height() * 0.10))
        p.setPen(PEN_STYLE_NONE)
        p.setBrush(QColor(0, 0, 0, 70))
        p.drawPath(shadow)

        wing = QPainterPath()
        wing.moveTo(pt(0.22, 0.24))
        wing.cubicTo(pt(-0.06, 0.24), pt(-0.10, 0.44), pt(-0.06, 0.58))
        wing.cubicTo(pt(-0.02, 0.68), pt(0.12, 0.66), pt(0.24, 0.60))
        wing.closeSubpath()
        wg = QLinearGradient(pt(-0.05, 0.2), pt(0.25, 0.6))
        wg.setColorAt(0, QColor("#2b2e39"))
        wg.setColorAt(1, QColor("#3a3e4c"))
        p.setBrush(QBrush(wg))
        p.setPen(QPen(QColor("#565b6e"), 2))
        p.drawPath(wing)

        shell = QPainterPath()
        shell.moveTo(pt(0.50, 0.00))
        shell.cubicTo(pt(0.86, 0.00), pt(0.98, 0.20), pt(0.96, 0.46))
        shell.cubicTo(pt(0.95, 0.74), pt(0.86, 0.99), pt(0.55, 0.99))
        shell.cubicTo(pt(0.30, 0.99), pt(0.20, 0.80), pt(0.20, 0.55))
        shell.cubicTo(pt(0.20, 0.28), pt(0.24, 0.00), pt(0.50, 0.00))
        shell.closeSubpath()
        sg = QLinearGradient(b.topLeft(), b.bottomRight())
        sg.setColorAt(0.0, QColor("#454a5b"))
        sg.setColorAt(0.4, QColor("#3a3f4d"))
        sg.setColorAt(0.7, QColor("#2d313e"))
        sg.setColorAt(1.0, QColor("#1e202a"))
        p.setBrush(QBrush(sg))
        p.setPen(QPen(QColor("#6d7286"), 2.2))
        p.drawPath(shell)

        p.save()
        p.setClipPath(shell)
        hl = QRadialGradient(pt(0.60, 0.18), b.width() * 0.55)
        hl.setColorAt(0, QColor(255, 255, 255, 55))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(PEN_STYLE_NONE)
        p.setBrush(QBrush(hl))
        p.drawRect(b)
        p.restore()

        seam = QPainterPath()
        seam.moveTo(pt(0.50, 0.00))
        seam.cubicTo(pt(0.49, 0.14), pt(0.49, 0.26), pt(0.50, 0.40))
        p.setPen(QPen(QColor("#20222b"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(seam)
        lower = QPainterPath()
        lower.moveTo(pt(0.50, 0.40))
        lower.cubicTo(pt(0.52, 0.60), pt(0.55, 0.78), pt(0.55, 0.95))
        p.setPen(QPen(QColor(32, 34, 43, 120), 1.5))
        p.drawPath(lower)

        wheel_spec = [x for x in self._buttons if x[0] == "middle"][0][3]
        wr = self._hotspot_rect(wheel_spec)
        p.setPen(QPen(QColor("#5b6070"), 1.5))
        wheel_bg = QLinearGradient(wr.topLeft(), wr.bottomLeft())
        wheel_bg.setColorAt(0, QColor("#20222b"))
        wheel_bg.setColorAt(1, QColor("#3a3e4c"))
        p.setBrush(QBrush(wheel_bg))
        p.drawRoundedRect(wr, wr.width() * 0.45, wr.width() * 0.45)
        p.setPen(QPen(QColor(150, 156, 175, 120), 1))
        for i in range(1, 5):
            yy = wr.y() + wr.height() * i / 5.0
            p.drawLine(int(wr.x() + wr.width() * 0.22), int(yy),
                       int(wr.x() + wr.width() * 0.78), int(yy))
        wh = QLinearGradient(wr.topLeft(), wr.bottomLeft())
        wh.setColorAt(0, QColor(255, 255, 255, 30))
        wh.setColorAt(0.5, QColor(255, 255, 255, 0))
        wh.setColorAt(1, QColor(0, 0, 0, 30))
        p.setPen(PEN_STYLE_NONE)
        p.setBrush(QBrush(wh))
        p.drawRoundedRect(wr, wr.width() * 0.45, wr.width() * 0.45)

        tr = [x for x in self._buttons if x[0] == "top"][0][3]
        trr = self._hotspot_rect(tr)
        p.setPen(QPen(QColor("#5b6070"), 1.3))
        p.setBrush(QColor("#2c2f3a"))
        p.drawRoundedRect(trr, trr.height() * 0.5, trr.height() * 0.5)
        th = QLinearGradient(trr.topLeft(), trr.bottomLeft())
        th.setColorAt(0, QColor(255, 255, 255, 30))
        th.setColorAt(1, QColor(0, 0, 0, 30))
        p.setBrush(QBrush(th))
        p.drawRoundedRect(trr, trr.height() * 0.5, trr.height() * 0.5)

        f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        for key, label, _c, spec in self._buttons:
            if key in ("middle", "top"):
                continue
            r = self._hotspot_rect(spec)
            assigned = self._assigned.get(key, False)
            if key == self._hover:
                fill, edge = QColor(76, 139, 245, 150), QColor("#a9c9ff")
            elif assigned:
                fill, edge = QColor(46, 125, 79, 140), QColor("#63d998")
            else:
                fill, edge = QColor(255, 255, 255, 22), QColor(139, 144, 163, 160)
            p.setBrush(QBrush(fill))
            p.setPen(QPen(edge, 2))
            p.drawRoundedRect(r, 7, 7)

        for key in ("middle", "top"):
            spec = [x for x in self._buttons if x[0] == key][0][3]
            r = self._hotspot_rect(spec)
            if key == self._hover:
                p.setPen(QPen(QColor("#a9c9ff"), 2.5)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 8, 8)
            elif self._assigned.get(key, False):
                p.setPen(QPen(QColor("#63d998"), 2.2)); p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(-3, -3, 3, 3), 8, 8)

        p.setPen(QColor("#e2e4ec"))
        f = QFont(); f.setPointSize(8); f.setBold(True); p.setFont(f)

        for key, label, _c, spec in self._buttons:
            r = self._hotspot_rect(spec)
            short = label.split(" (")[0]
            if key == "middle":
                p.save()
                p.translate(r.center())
                p.rotate(-90)
                p.drawText(QRectF(-40, -12, 80, 24), ALIGN_CENTER, short)
                p.restore()
            elif key == "top":
                label_rect = QRectF(r.x() + r.width() + 6,
                                    r.y(),
                                    b.width() * 0.5,
                                    r.height())
                p.drawText(label_rect, ALIGN_LEFT | ALIGN_VCENTER, short)
            else:
                p.drawText(r, ALIGN_CENTER, short)

        p.end()

# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Logitech LIFT Button Mapper")
        self.resize(1100, 680)
        self._custom_keys = {}
        self._gesture_actions = {}
        self._side_buttons = 3
        self._detect_thread = None

        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central)

        left = QVBoxLayout()
        self.schematic = MouseSchematic()
        self.schematic.buttonClicked.connect(self._focus_button)
        left.addWidget(self.schematic, 1)
        hint = QLabel("Click a button on the mouse to jump to its dropdown.")
        hint.setAlignment(ALIGN_CENTER)
        hint.setStyleSheet("color:#9aa0b4;")
        left.addWidget(hint)
        root.addLayout(left, 3)

        right = QVBoxLayout()

        # --- Device group (with VID/PID) ---
        dev_box = QGroupBox("Device")
        dev_lay = QGridLayout(dev_box)
        dev_lay.addWidget(QLabel("Name:"), 0, 0)
        self.device_edit = QLineEdit(DEFAULT_DEVICE_NAME)
        dev_lay.addWidget(self.device_edit, 0, 1, 1, 2)

        self.detect_btn = QPushButton("Detect mouse")
        self.detect_btn.clicked.connect(self.detect_mouse)
        dev_lay.addWidget(self.detect_btn, 0, 3)

        dev_lay.addWidget(QLabel("Vendor ID:"), 1, 0)
        self.vid_edit = QLineEdit("0x046d")
        self.vid_edit.setFixedWidth(80)
        dev_lay.addWidget(self.vid_edit, 1, 1)
        dev_lay.addWidget(QLabel("Product ID:"), 1, 2)
        self.pid_edit = QLineEdit("0xc548")
        self.pid_edit.setFixedWidth(80)
        dev_lay.addWidget(self.pid_edit, 1, 3)

        dev_lay.addWidget(QLabel("Side buttons:"), 2, 0)
        self.side_combo = QComboBox()
        self.side_combo.addItems(["2", "3"])
        self.side_combo.setCurrentText("3")
        self.side_combo.currentTextChanged.connect(self._on_side_changed)
        dev_lay.addWidget(self.side_combo, 2, 1)

        # Battery
        self.battery_btn = QPushButton("Read Battery")
        self.battery_btn.clicked.connect(self.read_battery)
        dev_lay.addWidget(self.battery_btn, 2, 2)
        self.battery_label = QLabel("Unknown")
        dev_lay.addWidget(self.battery_label, 2, 3)

        dev_lay.setColumnStretch(1, 1)
        right.addWidget(dev_box)

        # --- Mouse Settings group (DPI & Scrolling) ---
        settings_box = QGroupBox("Mouse Settings")
        settings_lay = QGridLayout(settings_box)

        settings_lay.addWidget(QLabel("DPI:"), 0, 0)
        self.dpi_slider = QSlider(Qt.Orientation.Horizontal)
        self.dpi_slider.setRange(400, 4000)
        self.dpi_slider.setValue(1600)
        self.dpi_slider.setTickInterval(200)
        self.dpi_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.dpi_slider.valueChanged.connect(self._update_dpi_label)
        settings_lay.addWidget(self.dpi_slider, 0, 1, 1, 2)
        self.dpi_label = QLabel("1600")
        self.dpi_label.setFixedWidth(40)
        settings_lay.addWidget(self.dpi_label, 0, 3)

        settings_lay.addWidget(QLabel("SmartShift threshold:"), 1, 0)
        self.smartshift_spin = QSpinBox()
        self.smartshift_spin.setRange(0, 255)
        self.smartshift_spin.setValue(20)
        self.smartshift_spin.setToolTip("0 = always free‑spin, high = harder to activate")
        settings_lay.addWidget(self.smartshift_spin, 1, 1)

        self.natural_scroll_check = QCheckBox("Natural scrolling")
        self.natural_scroll_check.setToolTip("Reverse scroll direction")
        settings_lay.addWidget(self.natural_scroll_check, 1, 2)

        self.hires_scroll_check = QCheckBox("Hi‑res scrolling")
        self.hires_scroll_check.setToolTip("Enable high‑resolution wheel")
        settings_lay.addWidget(self.hires_scroll_check, 1, 3)

        right.addWidget(settings_box)

        # --- Button functions (with gesture support) ---
        self.map_box = QGroupBox("Button functions")
        self.map_layout = QGridLayout(self.map_box)
        self.map_layout.addWidget(QLabel("<b>Button</b>"), 0, 0)
        self.map_layout.addWidget(QLabel("<b>CID</b>"), 0, 1)
        self.map_layout.addWidget(QLabel("<b>Function</b>"), 0, 2)
        self.map_layout.addWidget(QLabel("<b>Gesture</b>"), 0, 3)
        right.addWidget(self.map_box)

        self.rows = {}

        # Bottom buttons
        btn_row = QHBoxLayout()
        self.preview_btn = QPushButton("Preview config")
        self.preview_btn.clicked.connect(self.preview_config)
        self.export_btn = QPushButton("Export .cfg…")
        self.export_btn.clicked.connect(self.export_config)
        self.apply_btn = QPushButton("Save && Apply  (pkexec)")
        self.apply_btn.setStyleSheet(
            "background:#2e7d4f;color:white;font-weight:bold;padding:6px;")
        self.apply_btn.clicked.connect(self.save_and_apply)
        self.restart_btn = QPushButton("Restart Service")
        self.restart_btn.setToolTip("Restart logid without changing config")
        self.restart_btn.clicked.connect(self.restart_service)
        btn_row.addWidget(self.preview_btn)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.restart_btn)
        btn_row.addWidget(self.apply_btn)
        right.addLayout(btn_row)

        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setStyleSheet("font-family:monospace;font-size:11px;")
        right.addWidget(self.log, 1)

        root.addLayout(right, 4)

        self._rebuild_map()
        self._refresh_assigned()
        self._log("Ready. Configure buttons, then 'Save & Apply'.")
        if not shutil.which("logid"):
            self._log("WARNING: 'logid' not found. Install with:  paru -S logiops")
        else:
            self._check_service_status()

    # -----------------------------------------------------------------------
    def _check_service_status(self):
        """Check if logid service is enabled and running; show warning if not."""
        enabled = False
        active = False
        try:
            r = subprocess.run(["systemctl", "is-enabled", "logid"],
                               capture_output=True, text=True, timeout=5)
            enabled = r.returncode == 0 and "enabled" in r.stdout.lower()
        except:
            pass
        try:
            r = subprocess.run(["systemctl", "is-active", "logid"],
                               capture_output=True, text=True, timeout=5)
            active = r.returncode == 0 and "active" in r.stdout.lower()
        except:
            pass

        if not enabled:
            self._log("⚠️  logid service is NOT enabled at boot. Click 'Enable & Start' below.")
            self._show_service_buttons(True)
        elif not active:
            self._log("⚠️  logid service is enabled but NOT running. Click 'Restart Service' or 'Enable & Start'.")
            self._show_service_buttons(False)
        else:
            self._log("✅ logid service is enabled and running.")
            self._show_service_buttons(False)

    def _show_service_buttons(self, show_enable):
        if show_enable:
            self.restart_btn.setText("Enable & Start")
            self.restart_btn.setToolTip("Enable and start logid service")
        else:
            self.restart_btn.setText("Restart Service")
            self.restart_btn.setToolTip("Restart logid without changing config")

    def restart_service(self):
        """Restart (or enable & start) the logid service using pkexec."""
        if not shutil.which("pkexec"):
            QMessageBox.critical(self, "pkexec missing",
                                 "pkexec (polkit) is required to manage the service.")
            return
        if self.restart_btn.text() == "Enable & Start":
            cmd = "systemctl enable --now logid"
        else:
            cmd = "systemctl restart logid"
        self._log(f"Running: pkexec bash -c '{cmd}'")
        try:
            res = subprocess.run(["pkexec", "bash", "-c", cmd],
                                 capture_output=True, text=True, timeout=30)
            if res.stdout:
                self._log(res.stdout.strip())
            if res.stderr:
                self._log(res.stderr.strip())
            if res.returncode == 0:
                self._log("✅ Service operation successful.")
                self._check_service_status()
            else:
                self._log("❌ Service operation failed.")
        except Exception as e:
            self._log(f"Error: {e}")

    # -----------------------------------------------------------------------
    def _update_dpi_label(self, val):
        self.dpi_label.setText(str(val))

    def _active_buttons(self):
        count = int(self.side_combo.currentText())
        side_buttons = [("forward", "Forward (thumb, upper)", "0x56", (0.02, 0.20, 0.17, 0.11)),
                        ("side3",   "Side button (middle)",   "0x57", (0.02, 0.34, 0.17, 0.11)),
                        ("back",    "Back (thumb, lower)",    "0x53", (0.02, 0.48, 0.17, 0.12))]
        if count == 2:
            side_buttons = [side_buttons[0], side_buttons[2]]
        mandatory = [b for b in ALL_BUTTONS if b[0] in ("left", "right", "middle", "top")]
        return mandatory + side_buttons

    def _rebuild_map(self):
        while self.map_layout.count():
            item = self.map_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows.clear()

        self.map_layout.addWidget(QLabel("<b>Button</b>"), 0, 0)
        self.map_layout.addWidget(QLabel("<b>CID</b>"), 0, 1)
        self.map_layout.addWidget(QLabel("<b>Function</b>"), 0, 2)
        self.map_layout.addWidget(QLabel("<b>Gesture</b>"), 0, 3)

        active = self._active_buttons()
        for i, (key, label, cid, _spec) in enumerate(active, start=1):
            self.map_layout.addWidget(QLabel(label), i, 0)
            cid_edit = QLineEdit(cid); cid_edit.setFixedWidth(70)
            self.map_layout.addWidget(cid_edit, i, 1)
            combo = QComboBox(); combo.addItems(FUNCTION_NAMES)
            combo.setCurrentText(DEFAULT_SELECTION.get(key, FUNCTION_NAMES[0]))
            combo.currentTextChanged.connect(
                lambda text, k=key: self._on_combo_changed(k, text))
            self.map_layout.addWidget(combo, i, 2)

            gesture_btn = QPushButton("Gesture…")
            gesture_btn.clicked.connect(lambda checked, k=key: self._edit_gesture(k))
            self.map_layout.addWidget(gesture_btn, i, 3)

            self.rows[key] = (cid_edit, combo, gesture_btn)

        self.map_layout.setColumnStretch(2, 2)
        self.schematic.set_buttons(active)

    def _on_side_changed(self, text):
        self._rebuild_map()
        self._refresh_assigned()

    def _log(self, msg):
        self.log.appendPlainText(msg)

    def _on_combo_changed(self, key, text):
        if text == "Custom keystroke…":
            dlg = CustomKeyDialog(self, " + ".join(self._custom_keys.get(key, [])))
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.keys():
                self._custom_keys[key] = dlg.keys()
                self._log(f"{key}: custom -> {' + '.join(dlg.keys())}")
            else:
                self.rows[key][1].setCurrentText(
                    DEFAULT_SELECTION.get(key, FUNCTION_NAMES[0]))
        self._refresh_assigned()

    def _edit_gesture(self, key):
        current = self._gesture_actions.get(key, {})
        dlg = GestureDialog(self, current)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_gesture = dlg.get_gestures()
            if new_gesture:
                self._gesture_actions[key] = new_gesture
            elif key in self._gesture_actions:
                del self._gesture_actions[key]
            self._refresh_assigned()
            self._log(f"{key}: gesture updated")

    def _refresh_assigned(self):
        function_names = {}
        assigned = {}
        for key, (cid_edit, combo, gesture_btn) in self.rows.items():
            func = combo.currentText()
            has_func = func != FUNCTION_NAMES[0]
            has_gesture = key in self._gesture_actions and bool(self._gesture_actions[key])
            assigned[key] = has_func or has_gesture
            parts = []
            if has_func:
                parts.append(f"Click: {func}")
            if has_gesture:
                g = self._gesture_actions[key]
                parts.append("Gesture: " + ", ".join(f"{d}:{g[d]}" for d in g))
            function_names[key] = "; ".join(parts) if parts else ""
        self.schematic.set_assigned(assigned, function_names)

    def _focus_button(self, key):
        if key in self.rows:
            _cid, combo, _gbtn = self.rows[key]
            combo.setFocus()
            combo.showPopup()

    # -----------------------------------------------------------------------
    # Config parser (extended)
    # -----------------------------------------------------------------------
    def _load_current_config(self):
        if not os.path.exists(CONFIG_FILE):
            self._log("No existing config file found.")
            return
        try:
            with open(CONFIG_FILE, "r") as f:
                content = f.read()
        except Exception as e:
            self._log(f"Could not read {CONFIG_FILE}: {e}")
            return

        devices_match = re.search(r"devices\s*:\s*\(\s*((?:[^()]|\([^()]*\))*)\)", content, re.DOTALL)
        if not devices_match:
            self._log("No devices section found.")
            return
        devices_body = devices_match.group(1)
        device_blocks = []
        depth = 0; start = -1
        for i, ch in enumerate(devices_body):
            if ch == '{':
                if depth == 0: start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    device_blocks.append(devices_body[start:i+1])
                    start = -1
        device_name = self.device_edit.text().strip()
        chosen_block = None
        for block in device_blocks:
            name_match = re.search(r'name\s*:\s*"([^"]+)"', block)
            if name_match:
                name = name_match.group(1)
                if device_name and device_name.lower() in name.lower():
                    chosen_block = block
                    break
        if chosen_block is None and device_blocks:
            chosen_block = device_blocks[0]
        if chosen_block is None:
            self._log("Could not find any device block.")
            return

        dpi_match = re.search(r'sensors\s*:\s*\(\s*\{[^}]*type\s*:\s*"DPI"[^}]*dpi\s*:\s*(\d+)', chosen_block, re.DOTALL)
        if dpi_match:
            dpi_val = int(dpi_match.group(1))
            self.dpi_slider.setValue(dpi_val)
            self._update_dpi_label(dpi_val)

        ss_match = re.search(r'smartshift\s*:\s*\{\s*threshold\s*:\s*(\d+)', chosen_block)
        if ss_match:
            self.smartshift_spin.setValue(int(ss_match.group(1)))

        if re.search(r'scrolldirection\s*:\s*"Reversed"', chosen_block, re.I):
            self.natural_scroll_check.setChecked(True)
        if re.search(r'hires\s*:\s*true', chosen_block, re.I):
            self.hires_scroll_check.setChecked(True)

        buttons_match = re.search(r"buttons\s*:\s*\(\s*((?:[^()]|\([^()]*\))*)\)", chosen_block, re.DOTALL)
        if not buttons_match:
            self._log("No buttons section found.")
            return
        buttons_body = buttons_match.group(1)
        button_blocks = []
        depth = 0; start = -1
        for i, ch in enumerate(buttons_body):
            if ch == '{':
                if depth == 0: start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start != -1:
                    button_blocks.append(buttons_body[start:i+1])
                    start = -1

        for block in button_blocks:
            cid_match = re.search(r"cid\s*:\s*(0x[0-9a-fA-F]+)", block)
            if not cid_match:
                continue
            cid = cid_match.group(1)
            button_key = None
            for k, label, default_cid, _spec in ALL_BUTTONS:
                if default_cid.lower() == cid.lower():
                    button_key = k
                    break
            if button_key is None:
                continue

            action_match = re.search(r'action\s*=\s*\{([^}]*)\}', block, re.DOTALL)
            if action_match:
                action_body = action_match.group(1)
                type_match = re.search(r'type\s*:\s*"([^"]+)"', action_body)
                if type_match:
                    action_type = type_match.group(1)
                    if action_type == "Keypress":
                        keys_match = re.search(r'keys\s*:\s*\[\s*((?:[^\]\n]|\\\n)*)\]', action_body, re.DOTALL)
                        if keys_match:
                            keys_str = keys_match.group(1)
                            keys = re.findall(r'"([^"]+)"', keys_str)
                            if keys:
                                key_tuple = tuple(keys)
                                func_name = REVERSE_KEY_MAP.get(key_tuple)
                                if func_name:
                                    self._set_button_mapping(button_key, func_name, None)
                                else:
                                    self._custom_keys[button_key] = keys
                                    self._set_button_mapping(button_key, "Custom keystroke…", keys)
                    elif action_type in SPECIAL_ACTIONS:
                        func_name = SPECIAL_ACTIONS[action_type]
                        self._set_button_mapping(button_key, func_name, None)

            gesture_match = re.search(r'gesture\s*=\s*\{([^}]*)\}', block, re.DOTALL)
            if gesture_match:
                gesture_body = gesture_match.group(1)
                gestures = {}
                for direction in ("up", "down", "left", "right"):
                    dir_pattern = rf'{direction}\s*:\s*\{{\s*action\s*=\s*\{{([^}}]*)\}}\s*}}'
                    dir_match = re.search(dir_pattern, gesture_body, re.DOTALL)
                    if dir_match:
                        dir_action = dir_match.group(1)
                        keys_match = re.search(r'keys\s*:\s*\[\s*((?:[^\]\n]|\\\n)*)\]', dir_action, re.DOTALL)
                        if keys_match:
                            keys_str = keys_match.group(1)
                            keys = re.findall(r'"([^"]+)"', keys_str)
                            if keys:
                                key_tuple = tuple(keys)
                                func_name = REVERSE_KEY_MAP.get(key_tuple)
                                if func_name:
                                    gestures[direction] = func_name
                                else:
                                    gestures[direction] = "Custom keystroke…"
                                    if not hasattr(self, '_custom_gesture_keys'):
                                        self._custom_gesture_keys = {}
                                    if button_key not in self._custom_gesture_keys:
                                        self._custom_gesture_keys[button_key] = {}
                                    self._custom_gesture_keys[button_key][direction] = keys
                if gestures:
                    self._gesture_actions[button_key] = gestures

        self._log("Loaded current mappings from config.")

    def _set_button_mapping(self, button_key, func_name, custom_keys):
        if button_key not in self.rows:
            return
        cid_edit, combo, gesture_btn = self.rows[button_key]
        idx = combo.findText(func_name)
        if idx >= 0:
            combo.setCurrentIndex(idx)
            if func_name == "Custom keystroke…" and custom_keys is not None:
                self._custom_keys[button_key] = custom_keys
        else:
            if custom_keys is not None:
                self._custom_keys[button_key] = custom_keys
                idx_custom = combo.findText("Custom keystroke…")
                if idx_custom >= 0:
                    combo.setCurrentIndex(idx_custom)

    # -----------------------------------------------------------------------
    # Build config (FIXED)
    # -----------------------------------------------------------------------
    def build_config(self):
        device = self.device_edit.text().strip() or DEFAULT_DEVICE_NAME
        vid = self.vid_edit.text().strip() or "0x046d"
        pid = self.pid_edit.text().strip() or "0xc548"

        lines = ["devices:", "(", "  {", f'    name: "{device}";',
                 f'    vid: {vid};', f'    pid: {pid};']

        # --- Device-wide settings ---
        dpi = self.dpi_slider.value()
        lines.append("    sensors:")
        lines.append("    (")
        lines.append("      {")
        lines.append(f'        type: "DPI";')
        lines.append(f'        dpi: {dpi};')
        lines.append("      }")
        lines.append("    );")

        threshold = self.smartshift_spin.value()
        if threshold > 0:
            lines.append(f'    smartshift: {{ threshold: {threshold}; }};')

        if self.natural_scroll_check.isChecked():
            lines.append('    scrolldirection: "Reversed";')
        if self.hires_scroll_check.isChecked():
            lines.append('    hires: true;')

        lines.append("    buttons:")
        lines.append("    (")
        blocks = []

        def action_body_from_func(func_name):
            if func_name == "Custom keystroke…":
                return None
            gen = KNOWN_FUNCTIONS.get(func_name)
            if gen and gen != "DEFAULT":
                full = gen()
                if full.startswith("action = "):
                    inner = full[9:].rstrip(';')
                    return inner
            return None

        for key, (cid_edit, combo, gesture_btn) in self.rows.items():
            name = combo.currentText()
            gen = KNOWN_FUNCTIONS[name]
            cid = cid_edit.text().strip()
            if not cid:
                continue

            action_lines = []
            gesture_lines = []

            if gen == "CUSTOM":
                keys = self._custom_keys.get(key)
                if keys:
                    action_lines.append(
                        f'        action = {{ type: "Keypress"; keys: [{", ".join(f'"{k}"' for k in keys)}]; }};'
                    )
            elif gen != "DEFAULT":
                action_str = gen()
                if action_str:
                    action_lines.append(f"        {action_str}")

            gesture = self._gesture_actions.get(key, {})
            for direction, func_name in gesture.items():
                if func_name == "Custom keystroke…":
                    custom_keys = getattr(self, '_custom_gesture_keys', {}).get(key, {}).get(direction)
                    if custom_keys:
                        keys_str = ", ".join(f'"{k}"' for k in custom_keys)
                        gesture_lines.append(
                            f'        {direction}: {{ action = {{ type: "Keypress"; keys: [{keys_str}]; }}; }}'
                        )
                else:
                    inner = action_body_from_func(func_name)
                    if inner:
                        gesture_lines.append(
                            f'        {direction}: {{ action = {inner}; }}'
                        )

            if action_lines or gesture_lines:
                block = f"      {{\n        cid: {cid};"
                if action_lines:
                    block += "\n        " + "\n        ".join(action_lines)
                if gesture_lines:
                    block += "\n        gesture: {\n" + "\n".join(gesture_lines) + "\n        }"
                block += "\n      }"
                blocks.append(block)

        lines.append(",\n".join(blocks))
        lines += ["    );", "  }", ");", ""]
        return "\n".join(lines)

    # -----------------------------------------------------------------------
    def preview_config(self):
        self.log.clear()
        self._log(self.build_config())

    def export_config(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export logid.cfg", os.path.expanduser("~/logid.cfg"),
            "Config (*.cfg);;All files (*)")
        if not path:
            return
        with open(path, "w") as f:
            f.write(self.build_config())
        self._log(f"Exported to {path}")

    # detect -----------------------------------------------------------------
    def detect_mouse(self):
        if not shutil.which("logid"):
            QMessageBox.warning(self, "logid missing",
                                "logid not found. Install with: paru -S logiops")
            return
        self.detect_btn.setEnabled(False)
        self.detect_btn.setText("Detecting…")
        self._log("Detecting mouse (this may take a few seconds)…")
        QApplication.processEvents()

        self._detect_thread = DetectWorker()
        self._detect_thread.finished.connect(self._detection_done)
        self._detect_thread.start()
        QTimer.singleShot(20000, self._detection_timeout)

    def _detection_timeout(self):
        if self._detect_thread and self._detect_thread.isRunning():
            self._detect_thread.terminate()
            self._detect_thread.wait()
            self._detection_done("Detection timed out. Please try again.")

    def _detection_done(self, out):
        self.detect_btn.setEnabled(True)
        self.detect_btn.setText("Detect mouse")

        if "timed out" in out:
            self._log(out)
            return

        self._log(out.strip() or "(no output)")
        m = re.search(r"Device found:\s*(.+?)\s+on\s+/dev", out)
        if not m:
            m = re.search(r"(Logitech\s+Lift[^\n]*)", out, re.I)
        if m:
            name = m.group(1).strip()
            self.device_edit.setText(name)
            self._log(f'==> Using device name: "{name}"')
            count = _side_count_for_device(name)
            if count is not None:
                self.side_combo.setCurrentText(str(count))
                self._log(f"   → Detected {count} side button(s).")
            else:
                self._log("   → Could not auto‑detect side‑button count; defaulting to 2 (you can change it).")
        else:
            self._log("Could not auto-detect a name; keep the field as-is or "
                      "copy it from the output above.")

        vid_match = re.search(r"idVendor:\s*(0x[0-9a-fA-F]+)", out)
        if vid_match:
            self.vid_edit.setText(vid_match.group(1))
            self._log(f"   Vendor ID: {vid_match.group(1)}")
        pid_match = re.search(r"idProduct:\s*(0x[0-9a-fA-F]+)", out)
        if pid_match:
            self.pid_edit.setText(pid_match.group(1))
            self._log(f"   Product ID: {pid_match.group(1)}")

        self._load_current_config()

        if self._detect_thread:
            self._detect_thread.deleteLater()
            self._detect_thread = None

    # -----------------------------------------------------------------------
    # Battery reading – now shows only percentage when status unknown
    # -----------------------------------------------------------------------
    def read_battery(self):
        self.battery_label.setText("Reading…")
        QApplication.processEvents()

        # ----- 1. Scan sysfs -----
        battery_paths = glob.glob("/sys/class/power_supply/*/capacity")
        for path in battery_paths:
            parent = os.path.dirname(path)
            name_file = os.path.join(parent, "name")
            if os.path.exists(name_file):
                with open(name_file, "r") as f:
                    name = f.read().strip().lower()
                if "hidpp" in name or "mouse" in name or "logitech" in name:
                    with open(path, "r") as f:
                        capacity = f.read().strip()
                    status = "unknown"
                    status_path = os.path.join(parent, "status")
                    if os.path.exists(status_path):
                        with open(status_path, "r") as f:
                            status = f.read().strip()
                    # Only show status if it's not "unknown"
                    if status.lower() != "unknown":
                        self.battery_label.setText(f"{capacity}% ({status})")
                    else:
                        self.battery_label.setText(f"{capacity}%")
                    return

        # ----- 2. Fallback to upower -----
        if shutil.which("upower"):
            try:
                proc = subprocess.run(["upower", "-e"], capture_output=True, text=True, timeout=5)
                devices = proc.stdout.strip().splitlines()
                for dev in devices:
                    if "mouse" not in dev.lower() and "hid" not in dev.lower():
                        continue
                    info = subprocess.run(["upower", "-i", dev], capture_output=True, text=True, timeout=5)
                    if info.returncode != 0:
                        continue
                    lines = info.stdout.splitlines()
                    percentage = None
                    state = None
                    for line in lines:
                        if "percentage" in line:
                            percentage = line.split(":")[1].strip()
                        if "state" in line:
                            state = line.split(":")[1].strip()
                    if percentage:
                        # Only show state if it's not "unknown"
                        if state and state.lower() != "unknown":
                            self.battery_label.setText(f"{percentage} ({state})")
                        else:
                            self.battery_label.setText(f"{percentage}")
                        return
            except Exception:
                pass

        # ----- 3. Nothing found -----
        self.battery_label.setText("No battery data found")

    # apply ------------------------------------------------------------------
    def save_and_apply(self):
        cfg = self.build_config()
        self.log.clear()
        self._log("Generated config:\n" + cfg)
        if not shutil.which("pkexec"):
            QMessageBox.critical(self, "pkexec missing",
                                 "pkexec (polkit) is required to write "
                                 f"{CONFIG_FILE}. Install polkit.")
            return
        with tempfile.NamedTemporaryFile("w", suffix=".cfg", delete=False) as tf:
            tf.write(cfg)
            tmp = tf.name
        script = (
            'set -e; '
            f'if [ -f "{CONFIG_FILE}" ]; then cp "{CONFIG_FILE}" '
            f'"{CONFIG_FILE}.bak.$(date +%s)"; fi; '
            f'install -m 644 "{tmp}" "{CONFIG_FILE}"; '
            'systemctl daemon-reload; '
            'systemctl enable logid; '
            'systemctl restart logid; '
            'sleep 1; systemctl is-active logid'
        )
        try:
            res = subprocess.run(["pkexec", "bash", "-c", script],
                                 capture_output=True, text=True, timeout=60)
        except Exception as e:
            self._log(f"Apply failed: {e}")
            return
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        if res.stdout:
            self._log(res.stdout.strip())
        if res.stderr:
            self._log(res.stderr.strip())
        if res.returncode == 0 and "active" in res.stdout:
            self._log(f"\n✔ Applied. {CONFIG_FILE} written, service enabled and restarted.\n"
                      "Settings will persist across reboots.")
        else:
            self._log("\n✗ Something went wrong. Check:  "
                      "sudo journalctl -u logid -e")
        self._check_service_status()

# ---------------------------------------------------------------------------
def main():
    qt_args = [a for a in sys.argv if a not in ("--skip-deps", "--auto-deps", "--qt5")]
    app = QApplication(qt_args)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()