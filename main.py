import datetime
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from kivy.app import App
from kivy.clock import Clock
from kivy.core.image import Image as CoreImage
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.modalview import ModalView
from kivy.uix.screenmanager import FadeTransition, Screen, ScreenManager
from kivy.uix.scrollview import ScrollView
from kivy.uix.widget import Widget
from kivy.utils import escape_markup, platform

if platform not in ("android", "ios"):
    Window.size = (1280, 720)
    Window.minimum_width = 960
    Window.minimum_height = 540

DEFAULT_PICON_DIR = "/usr/share/enigma2/picon"
PICON_PRESETS = [
    "/usr/share/enigma2/picon",
    "/media/hdd/picon",
    "/media/usb/picon",
    "/picon",
]

PIG_MODES = [
    ("follow", "Auto (podąża za kursorem)"),
    ("enigma2", "Enigma2 (ostatni oglądany)"),
]


def pig_mode_label(mode_key):
    for k, lbl in PIG_MODES:
        if k == mode_key:
            return lbl
    return PIG_MODES[0][1]


# === PALETA KOLORÓW ENIGMA2 DARK SKIN ===
C_BLACK = (0.015, 0.018, 0.025, 1)
C_PANEL = (0.025, 0.030, 0.042, 1)
C_BORDER = (0.28, 0.31, 0.36, 0.75)
C_BORDER_SUB = (0.18, 0.21, 0.26, 0.7)
C_CYAN = (0.40, 0.78, 0.96, 1)
C_MINT = (0.34, 0.80, 0.62, 1)
C_WHITE = (0.95, 0.96, 0.98, 1)
C_GRAY = (0.72, 0.76, 0.81, 1)
C_MUTED = (0.48, 0.52, 0.58, 1)
C_SEL_BG = (0.12, 0.14, 0.18, 0.95)
C_FOCUS_BG = (0.12, 0.34, 0.54, 0.98)
C_ERROR = (0.95, 0.35, 0.35, 1)

PL_DAYS = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
PL_MONTHS = [
    "", "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
    "lipca", "sierpnia", "września", "października", "listopada", "grudnia",
]
PL_MONTHS_SHORT = ["", "sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]

_RAINBOW_TEX = None


def get_rainbow_texture():
    global _RAINBOW_TEX
    if _RAINBOW_TEX is not None:
        return _RAINBOW_TEX
    stops = [
        (0.00, (145, 70, 255)), (0.25, (40, 140, 255)), (0.50, (35, 220, 235)),
        (0.72, (95, 230, 95)), (0.88, (250, 225, 45)), (1.00, (255, 125, 35)),
    ]
    buf = bytearray()
    for i in range(64):
        t = i / 63.0
        r, g, b = stops[-1][1]
        for s in range(len(stops) - 1):
            t0, c0 = stops[s]
            t1, c1 = stops[s + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0) if t1 > t0 else 0
                r = int(c0[0] + (c1[0] - c0[0]) * f)
                g = int(c0[1] + (c1[1] - c0[1]) * f)
                b = int(c0[2] + (c1[2] - c0[2]) * f)
                break
        buf.extend((r, g, b, 255))
    tex = Texture.create(size=(64, 1), colorfmt="rgba")
    tex.blit_buffer(bytes(buf), colorfmt="rgba", bufferfmt="ubyte")
    tex.wrap = "clamp_to_edge"
    _RAINBOW_TEX = tex
    return tex


def make_label(text="", font_size=15, color=C_WHITE, halign="left", valign="middle",
               bold=False, markup=False, shorten=True, **kwargs):
    lbl = Label(
        text=text, font_size=sp(font_size), color=color, halign=halign, valign=valign,
        bold=bold, markup=markup, shorten=shorten, shorten_from="right", **kwargs
    )
    lbl.bind(size=lambda inst, val: setattr(inst, "text_size", val))
    return lbl


def fmt_epg_date_short(ts):
    if not ts:
        return ""
    dt = datetime.datetime.fromtimestamp(ts)
    return f"{dt.day} {PL_MONTHS_SHORT[dt.month]}"


def fmt_epg_time(ts):
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "--:--"


def normalize_remote_key(key, scancode):
    """Normalizuje kody klawiszy pilota Android TV (SDL2 / Mi Box) oraz klawiatury PC."""
    if key in (273, 19) or scancode == 82:
        return 273
    if key in (274, 20) or scancode == 81:
        return 274
    if key in (276, 21) or scancode == 80:
        return 276
    if key in (275, 22) or scancode == 79:
        return 275
    if key in (13, 271, 23, 1073741943) or scancode in (40, 88, 119):
        return 13
    if key in (27, 4, 1073742094) or scancode in (41, 270):
        return 27
    if key in (319, 1073741942) or scancode == 118:
        return ord("m")
    if key in (280,) or scancode == 75:
        return 280
    if key in (281,) or scancode == 78:
        return 281
    return key


# =============================================================================
# WARSTWA ODTWARZANIA WIDEO: Android (natywny LibVLC HW) / Windows (VLC)
# =============================================================================
class DesktopVLCBackend:
    """Na Windows/Linux uruchamia płynny, sprzętowy VLC (okno podglądu PIG lub pełny ekran)."""
    def __init__(self):
        self.current_sref = ""
        self.is_playing = False
        self.is_embedded = False
        self._proc = None

    @staticmethod
    def _find_vlc():
        return next((p for p in (
            shutil.which("vlc"),
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ) if p and os.path.exists(p)), None)

    def _kill_previous(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None

    def get_status(self):
        return ""

    def play_pig(self, url, sref, name, widget=None):
        vlc = self._find_vlc()
        if not vlc:
            return False, f"Zainstaluj VLC lub otwórz ręcznie: {url}"
        self._kill_previous()
        self._proc = subprocess.Popen([
            vlc, "--qt-minimal-view", "--no-video-title-show",
            "--video-on-top", "--width=520", "--height=292", url,
        ])
        self.current_sref = sref
        self.is_playing = True
        return True, f"Podgląd PIG w VLC: {name}"

    def play_fullscreen(self, url, sref, name, infobar_data=None):
        vlc = self._find_vlc()
        if not vlc:
            return False, f"Nie znaleziono VLC. URL: {url}"
        self._kill_previous()
        self._proc = subprocess.Popen([vlc, "--fullscreen", "--no-video-title-show", url])
        self.current_sref = sref
        self.is_playing = True
        return True, f"Pełny ekran VLC: {name}"

    def sync_pig_widget(self, widget):
        pass

    def show_infobar_rich(self, data, duration=6.0):
        pass

    def update_infobar_picon(self, path):
        pass

    def show_toast(self, title, subtitle, duration=2.0):
        pass

    def toggle_infobar(self):
        pass

    def is_track_menu_open(self):
        return False

    def open_track_menu(self, tab=0):
        pass

    def close_track_menu(self):
        pass

    def move_track_menu(self, delta):
        pass

    def toggle_track_tab(self):
        pass

    def confirm_track_menu(self):
        pass

    def toggle_mute(self):
        return False

    def change_volume(self, delta):
        return 100

    def hide(self):
        pass

    def stop(self):
        self._kill_previous()
        self.is_playing = False
        self.current_sref = ""


class AndroidLibVLCBackend:
    """Na Android TV: natywny sprzętowy LibVLC (TvVlcController.java na TextureView) + Infobar Enigma2."""
    def __init__(self):
        self.current_sref = ""
        self.is_playing = False
        self.is_embedded = False
        self._initialized = False
        self._use_intent = False
        self._muted = False
        self._volume = 100
        self._ctrl = None

    def _ensure_init(self):
        if self._initialized:
            return not self._use_intent
        self._initialized = True
        try:
            from jnius import autoclass
            self._ctrl = autoclass("org.kivy.android.TvVlcController")
            self.is_embedded = True
            return True
        except Exception:
            self._use_intent = True
            self.is_embedded = False
            return False

    @staticmethod
    def _open_intent(url):
        from jnius import autoclass
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        intent = Intent(Intent.ACTION_VIEW)
        intent.setDataAndType(Uri.parse(url), "video/*")
        PythonActivity.mActivity.startActivity(intent)

    @staticmethod
    def _widget_norm_bounds(widget):
        ww = max(1.0, float(Window.width))
        wh = max(1.0, float(Window.height))
        if not widget:
            return 0.02, 0.14, 0.33, 0.34
        x, y = widget.to_window(*widget.pos)
        w, h = widget.size
        if w < 10 or h < 10:
            return 0.02, 0.14, 0.33, 0.34
        pad = float(dp(3))
        fx = max(0.0, (x + pad) / ww)
        fy = max(0.0, (wh - (y + h) + pad) / wh)
        fw = max(0.05, (w - pad * 2.0) / ww)
        fh = max(0.05, (h - pad * 2.0) / wh)
        return fx, fy, fw, fh

    def get_status(self):
        if self.is_embedded and self._ctrl:
            try:
                return str(self._ctrl.getStatus() or "")
            except Exception:
                return ""
        return ""

    def sync_pig_widget(self, widget):
        if not self.is_embedded or not self.is_playing or not self._ctrl or not widget:
            return
        fx, fy, fw, fh = self._widget_norm_bounds(widget)
        try:
            self._ctrl.setBounds(fx, fy, fw, fh, False)
        except Exception:
            pass

    def play_pig(self, url, sref, name, widget=None):
        if not self._ensure_init():
            self._open_intent(url)
            return True, f"Otwarto w zewnętrznym odtwarzaczu: {name}"
        fx, fy, fw, fh = self._widget_norm_bounds(widget)
        self.current_sref = sref
        self.is_playing = True
        try:
            self._ctrl.play(url, fx, fy, fw, fh, False)
        except Exception as e:
            return False, f"Błąd startu PIG: {e}"
        if widget:
            Clock.schedule_once(lambda dt: self.sync_pig_widget(widget), 0.12)
        return True, f"PIG: {name} (łączenie...)"

    def play_fullscreen(self, url, sref, name, infobar_data=None):
        if not self._ensure_init():
            self._open_intent(url)
            return False, f"Otwarto w zewnętrznym odtwarzaczu: {name}"
        self.current_sref = sref
        self.is_playing = True
        try:
            self._ctrl.play(url, 0.0, 0.0, 1.0, 1.0, True)
        except Exception as e:
            return False, f"Błąd startu Fullscreen: {e}"
        if infobar_data:
            self.show_infobar_rich(infobar_data, 6.0)
        return True, f"Fullscreen: {name} (łączenie...)"

    def show_infobar_rich(self, data, duration=6.0):
        if not self.is_embedded or not self._ctrl or not data:
            return
        try:
            self._ctrl.showInfobarRich(
                str(data.get("ch_title", "")),
                str(data.get("now_time", "")),
                str(data.get("now_title", "")),
                int(data.get("now_begin", 0) or 0),
                int(data.get("now_dur", 0) or 0),
                str(data.get("next_time", "")),
                str(data.get("next_title", "")),
                str(data.get("next_dur_text", "")),
                str(data.get("picon_path", "")),
                str(data.get("source_tag", "ENIGMA2")),
                int(duration * 1000),
            )
        except Exception:
            pass

    def update_infobar_picon(self, path):
        if self.is_embedded and self._ctrl and path:
            try:
                self._ctrl.updateInfobarPicon(str(path))
            except Exception:
                pass

    def show_toast(self, title, subtitle, duration=2.0):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.showToast(str(title), str(subtitle), int(duration * 1000))
            except Exception:
                pass

    def toggle_infobar(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.toggleInfobar()
            except Exception:
                pass

    def is_track_menu_open(self):
        if self.is_embedded and self._ctrl:
            try:
                return bool(self._ctrl.isTrackMenuOpen())
            except Exception:
                return False
        return False

    def open_track_menu(self, tab=0):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.openTrackMenu(int(tab))
            except Exception:
                pass

    def close_track_menu(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.closeTrackMenu()
            except Exception:
                pass

    def move_track_menu(self, delta):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.moveTrackMenu(int(delta))
            except Exception:
                pass

    def toggle_track_tab(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.toggleTrackTab()
            except Exception:
                pass

    def confirm_track_menu(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.confirmTrackMenu()
            except Exception:
                pass

    def toggle_mute(self):
        self._muted = not self._muted
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.setVolume(0 if self._muted else self._volume)
            except Exception:
                pass
        return self._muted

    def change_volume(self, delta):
        self._volume = max(0, min(100, self._volume + int(delta * 100)))
        if self.is_embedded and self._ctrl and not self._muted:
            try:
                self._ctrl.setVolume(self._volume)
            except Exception:
                pass
        return self._volume

    def hide(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.hide()
            except Exception:
                pass
        self.is_playing = False
        self.current_sref = ""

    def stop(self):
        if self.is_embedded and self._ctrl:
            try:
                self._ctrl.stopAndRelease()
            except Exception:
                pass
        self.is_playing = False
        self.current_sref = ""

    def finish_app(self):
        try:
            from jnius import autoclass
            autoclass("org.kivy.android.TvVlcController").finishApp()
            return
        except Exception:
            pass
        self.stop()


# =============================================================================
# KOMPONENTY UI (ENIGMA2 SKIN)
# =============================================================================
class EnigmaFrame(BoxLayout):
    def __init__(self, bg=C_PANEL, border=C_BORDER, radius=10, lw=1.0, **kwargs):
        super().__init__(**kwargs)
        self._r = dp(radius)
        with self.canvas.before:
            self._bg_col = Color(*bg)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[self._r])
            self._bd_col = Color(*border)
            self._bd_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, self._r), width=lw)
        self.bind(pos=self._redraw, size=self._redraw)

    def _redraw(self, *args):
        self._bg_rect.pos, self._bg_rect.size = self.pos, self.size
        self._bd_line.rounded_rectangle = (self.x, self.y, self.width, self.height, self._r)


class GradientProgressBar(Widget):
    def __init__(self, framed=True, **kwargs):
        super().__init__(**kwargs)
        self._progress = 0.0
        self._visible_bar = True
        self._framed = framed
        with self.canvas:
            self._bg_col = Color(0.08, 0.09, 0.12, 0.9 if framed else 0.45)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            self._fg_col = Color(1, 1, 1, 1)
            self._fg_rect = Rectangle(texture=get_rainbow_texture(), pos=self.pos, size=(0, self.height))
            self._bd_col = Color(0.55, 0.60, 0.68, 0.85 if framed else 0.0)
            self._bd_line = Line(rectangle=(self.x, self.y, self.width, self.height), width=1.0)
        self.bind(pos=self._redraw, size=self._redraw)

    def set_progress(self, value, visible=True):
        self._visible_bar = visible
        self._progress = max(0.0, min(1.0, float(value)))
        self._redraw()

    def _redraw(self, *args):
        if not self._visible_bar:
            self._bg_col.a = self._fg_col.a = self._bd_col.a = 0
            return
        self._bg_col.a = 0.9 if self._framed else 0.45
        self._fg_col.a = 1.0
        self._bd_col.a = 0.85 if self._framed else 0.0
        pad = dp(1.5) if self._framed else 0
        iw, ih = max(0.0, self.width - pad * 2), max(0.0, self.height - pad * 2)
        fw = iw * self._progress
        self._bg_rect.pos, self._bg_rect.size = self.pos, self.size
        self._fg_rect.pos, self._fg_rect.size = (self.x + pad, self.y + pad), (fw, ih)
        p = max(0.01, self._progress) if fw > 0 else 0.0
        self._fg_rect.tex_coords = (0, 0, p, 0, p, 1, 0, 1)
        self._bd_line.rectangle = (self.x, self.y, self.width, self.height)


class PillButton(ButtonBehavior, BoxLayout):
    def __init__(self, text, accent=C_CYAN, underline=False, font_size=14, **kwargs):
        super().__init__(**kwargs)
        self._underline = underline
        self.padding = (dp(10), dp(4))
        with self.canvas.before:
            self._bg_col = Color(0.07, 0.08, 0.11, 0.95 if not underline else 0.0)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(7)])
            self._bd_col = Color(0.32, 0.36, 0.42, 0.7 if not underline else 0.0)
            self._bd_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(7)), width=1.0)
            self._acc_col = Color(*accent)
            self._acc_rect = Rectangle(pos=self.pos, size=(0, 0))
        self.bind(pos=self._redraw, size=self._redraw)
        self.lbl = make_label(text, font_size=font_size, halign="center", bold=not underline, shorten=False)
        self.add_widget(self.lbl)

    def _redraw(self, *args):
        self._bg_rect.pos, self._bg_rect.size = self.pos, self.size
        self._bd_line.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(7))
        if self._underline:
            bw = min(self.width * 0.72, dp(96))
            self._acc_rect.pos = (self.center_x - bw / 2, self.y + dp(1))
            self._acc_rect.size = (bw, dp(2.5))
        else:
            self._acc_rect.pos = (self.x + dp(3), self.y + dp(5))
            self._acc_rect.size = (dp(2.5), max(0, self.height - dp(10)))


class TVSettingField(ButtonBehavior, BoxLayout):
    """Pole konfiguracji przyjazne pilotowi Android TV z wyraźnym fokusem i wskaźnikiem edycji."""
    def __init__(self, title, value, hint="", **kwargs):
        super().__init__(orientation="vertical", padding=(dp(12), dp(5)), spacing=dp(1), **kwargs)
        self._value = value
        self._focused = False
        self._editing = False
        with self.canvas.before:
            self._bg_col = Color(0.07, 0.09, 0.13, 0.95)
            self._bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(8)])
            self._bd_col = Color(0.28, 0.33, 0.40, 0.75)
            self._bd_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(8)), width=1.1)
        self.bind(pos=self._redraw, size=self._redraw)

        top = BoxLayout(size_hint_y=None, height=dp(16))
        self.title_lbl = make_label(title, 12, C_GRAY, bold=True)
        self.hint_lbl = make_label(hint, 11, C_MUTED, halign="right", size_hint_x=None, width=dp(135))
        top.add_widget(self.title_lbl)
        top.add_widget(self.hint_lbl)
        self.add_widget(top)

        self.val_lbl = make_label(value, 16, C_WHITE, bold=True)
        self.add_widget(self.val_lbl)

    def _redraw(self, *args):
        self._bg_rect.pos, self._bg_rect.size = self.pos, self.size
        self._bd_line.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(8))

    @property
    def text(self):
        return self._value

    @text.setter
    def text(self, val):
        self._value = str(val)
        self._refresh_visual()

    def set_state(self, focused, editing):
        self._focused = focused
        self._editing = editing
        self._refresh_visual()

    def _refresh_visual(self):
        if self._focused:
            self._bg_col.rgba = (0.11, 0.27, 0.44, 0.98)
            self._bd_col.rgba = C_CYAN
            self._bd_line.width = 1.9
            self.title_lbl.color = C_CYAN
            self.val_lbl.color = (1, 1, 1, 1)
            self.val_lbl.text = self._value + ("_" if self._editing else "")
        elif self._editing:
            self._bg_col.rgba = (0.07, 0.16, 0.24, 0.96)
            self._bd_col.rgba = C_MINT
            self._bd_line.width = 1.6
            self.title_lbl.color = C_MINT
            self.val_lbl.color = (1, 1, 1, 1)
            self.val_lbl.text = self._value + "_"
        else:
            self._bg_col.rgba = (0.065, 0.082, 0.115, 0.95)
            self._bd_col.rgba = (0.26, 0.31, 0.38, 0.65)
            self._bd_line.width = 1.0
            self.title_lbl.color = C_GRAY
            self.val_lbl.color = C_WHITE
            self.val_lbl.text = self._value


class PiconManager:
    """Asynchroniczne pobieranie i cache'owanie pikon z /usr/share/enigma2/picon."""
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self.executor = ThreadPoolExecutor(max_workers=6)
        self.textures, self.file_paths, self.missing, self.pending = {}, {}, set(), {}
        self.ip, self.web_port, self.picon_dir = "", "80", DEFAULT_PICON_DIR

    def configure(self, ip, web_port, picon_dir):
        pdir = (picon_dir or DEFAULT_PICON_DIR).rstrip("/")
        if (ip, web_port, pdir) != (self.ip, self.web_port, self.picon_dir):
            self.ip, self.web_port, self.picon_dir = ip, web_port, pdir
            self.missing.clear()

    @staticmethod
    def _candidate_names(sref, channel_name=""):
        names = []
        if sref:
            parts = sref.strip().split(":")
            if len(parts) >= 10:
                names.append("_".join(parts[:10]))
                if parts[0] != "1":
                    names.append("1_" + "_".join(parts[1:10]))
                    names.append("1_0_1_" + "_".join(parts[3:10]))
                    if parts[2] != "19":
                        names.append("1_0_19_" + "_".join(parts[3:10]))
            else:
                cleaned = sref.strip().rstrip(":").replace(":", "_")
                if cleaned:
                    names.append(cleaned)
        if channel_name:
            snp = re.sub(r"[^a-z0-9]+", "", channel_name.lower().replace("+", "plus"))
            if snp:
                names.append(snp)
        return list(dict.fromkeys(names))

    def get_cached_path(self, sref, channel_name=""):
        if not sref:
            return ""
        if sref in self.file_paths and os.path.exists(self.file_paths[sref]):
            return self.file_paths[sref]
        cands = self._candidate_names(sref, channel_name)
        if cands:
            cf = os.path.join(self.cache_dir, f"{cands[0]}.png")
            if os.path.exists(cf):
                self.file_paths[sref] = cf
                return cf
        return ""

    def get_picon(self, sref, channel_name, callback):
        if not sref:
            return callback(sref, None)
        if sref in self.textures:
            return callback(sref, self.textures[sref])
        if sref in self.missing:
            return callback(sref, None)
        if sref in self.pending:
            self.pending[sref].append(callback)
            return
        self.pending[sref] = [callback]
        self.executor.submit(self._worker, sref, channel_name)

    def _worker(self, sref, channel_name):
        cands = self._candidate_names(sref, channel_name)
        if not cands:
            return Clock.schedule_once(lambda dt: self._finish(sref, None, ""))

        cache_file = os.path.join(self.cache_dir, f"{cands[0]}.png")
        raw = None
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "rb") as f:
                    raw = f.read()
            except Exception:
                raw = None

        if not raw and self.ip:
            urls = [f"http://{self.ip}:{self.web_port}/picon/{c}.png" for c in cands]
            for c in cands[:2]:
                p1 = urllib.parse.quote(f"{self.picon_dir}/{c}.png", safe="/")
                urls.append(f"http://{self.ip}:{self.web_port}/file?file={p1}")
                if self.picon_dir != DEFAULT_PICON_DIR:
                    p2 = urllib.parse.quote(f"{DEFAULT_PICON_DIR}/{c}.png", safe="/")
                    urls.append(f"http://{self.ip}:{self.web_port}/file?file={p2}")
            for url in urls:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                    with urllib.request.urlopen(req, timeout=2.5) as resp:
                        data = resp.read()
                        if data and data[:8] == b"\x89PNG\r\n\x1a\n":
                            raw = data
                            try:
                                with open(cache_file, "wb") as f:
                                    f.write(raw)
                            except Exception:
                                pass
                            break
                except Exception:
                    continue

        Clock.schedule_once(lambda dt: self._finish(sref, raw, cache_file if raw else ""))

    def _finish(self, sref, raw, fpath):
        tex = None
        if raw:
            try:
                tex = CoreImage(io.BytesIO(raw), ext="png").texture
                self.textures[sref] = tex
                if fpath and os.path.exists(fpath):
                    self.file_paths[sref] = fpath
            except Exception:
                tex = None
        if tex is None:
            self.missing.add(sref)
        for cb in self.pending.pop(sref, []):
            cb(sref, tex)

    def shutdown(self):
        self.executor.shutdown(wait=False)


class ChannelRowWidget(ButtonBehavior, BoxLayout):
    def __init__(self, slot_idx, on_slot_click, **kwargs):
        super().__init__(orientation="horizontal", **kwargs)
        self.slot_idx, self.on_slot_click = slot_idx, on_slot_click
        self.current_sref, self.is_marker = "", False
        self.padding, self.spacing = (dp(8), dp(2)), dp(8)

        with self.canvas.before:
            self._sel_col = Color(0, 0, 0, 0)
            self._sel_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(5)])
            self._line_col = Color(0, 0, 0, 0)
            self._top_line = Rectangle(pos=self.pos, size=(self.width, dp(1)))
            self._bot_line = Rectangle(pos=self.pos, size=(self.width, dp(1)))
        self.bind(pos=self._redraw, size=self._redraw)

        self.num_lbl = make_label("", 14.5, halign="right", size_hint_x=None, width=dp(42), shorten=False)
        self.add_widget(self.num_lbl)

        self.bar_wrap = BoxLayout(size_hint_x=None, width=dp(42), padding=(0, dp(8)))
        self.progress = GradientProgressBar(framed=True)
        self.bar_wrap.add_widget(self.progress)
        self.add_widget(self.bar_wrap)

        self.picon_img = Image(size_hint_x=None, width=dp(48), allow_stretch=True, keep_ratio=True, opacity=0)
        self.add_widget(self.picon_img)

        self.text_lbl = make_label("", 15, markup=True)
        self.add_widget(self.text_lbl)

    def _redraw(self, *args):
        self._sel_rect.pos, self._sel_rect.size = self.pos, self.size
        w = max(0, self.width - dp(8))
        self._top_line.pos, self._top_line.size = (self.x + dp(4), self.top - dp(1)), (w, dp(1))
        self._bot_line.pos, self._bot_line.size = (self.x + dp(4), self.y), (w, dp(1))

    def on_release(self):
        if not self.is_marker and self.current_sref:
            self.on_slot_click(self.slot_idx)

    def bind_empty(self):
        self.current_sref, self.is_marker = "", False
        self.num_lbl.text = self.text_lbl.text = ""
        self.progress.set_progress(0.0, visible=False)
        self.picon_img.texture, self.picon_img.opacity = None, 0
        self._set_selected(False)

    def bind_marker(self, title):
        self.current_sref, self.is_marker = "", True
        self.num_lbl.text, self.num_lbl.width = "", dp(6)
        self.bar_wrap.width, self.bar_wrap.opacity = 0, 0
        self.progress.set_progress(0.0, visible=False)
        self.picon_img.width, self.picon_img.opacity = 0, 0
        t = escape_markup(title.strip())
        if not t.startswith("---"):
            t = f"--- {t} ---"
        self.text_lbl.text = f"[color=#66C7F5]»[/color]  [color=#B8C6D6][b]{t}[/b][/color]"
        self._set_selected(False)

    def bind_channel(self, item, selected, epg_event, picon_manager, is_live=False):
        self.is_marker = False
        sref, name = item.get("sref", ""), item.get("name", "")
        self.current_sref = sref
        self.num_lbl.width, self.bar_wrap.width, self.bar_wrap.opacity, self.picon_img.width = dp(42), dp(42), 1, dp(48)
        self.num_lbl.text = str(item.get("num", 0))
        self.num_lbl.color = C_CYAN if selected else C_WHITE

        epg_title = ""
        if epg_event and epg_event.get("title"):
            epg_title = epg_event.get("title", "")
            self.progress.set_progress(epg_event.get("_progress", 0.0), visible=True)
        else:
            self.progress.set_progress(0.0, visible=False)

        ch_hex, epg_hex = ("#5CC8FF", "#73B9D9") if selected else ("#F2F5FA", "#A6B0BD")
        live_dot = "[color=#4CE08A]● [/color]" if is_live else ""
        sn, se = escape_markup(name), escape_markup(epg_title)
        self.text_lbl.text = f"{live_dot}[color={ch_hex}]{sn}[/color]" + (f"  [color={epg_hex}]{se}[/color]" if se else "")
        self._set_selected(selected)

        self.picon_img.texture, self.picon_img.opacity = None, 0
        picon_manager.get_picon(sref, name, self._on_picon_ready)

    def _on_picon_ready(self, sref, tex):
        if sref == self.current_sref and not self.is_marker:
            self.picon_img.texture = tex
            self.picon_img.opacity = 1 if tex else 0

    def _set_selected(self, sel):
        self._sel_col.rgba = C_SEL_BG if sel else (0, 0, 0, 0)
        self._line_col.rgba = (0.35, 0.72, 0.95, 0.55) if sel else (0, 0, 0, 0)


class EPGTableRow(ButtonBehavior, BoxLayout):
    def __init__(self, idx, on_click, **kwargs):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(34),
                         padding=(dp(10), dp(2)), spacing=dp(8), **kwargs)
        self.idx, self.on_click = idx, on_click
        with self.canvas.before:
            self._bg = Color(0, 0, 0, 0)
            self._rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(5)])
            self._ln_col = Color(0.18, 0.22, 0.28, 0.35)
            self._ln = Rectangle(pos=self.pos, size=(self.width, dp(1)))
        self.bind(pos=self._redraw, size=self._redraw)

        self.col1 = make_label("", 13.5, C_GRAY, size_hint_x=None, width=dp(80), shorten=False)
        self.col2 = make_label("", 13.5, C_CYAN, size_hint_x=None, width=dp(100), shorten=False)
        self.col3 = make_label("", 14, C_WHITE, markup=True)
        self.col4 = make_label("", 13, C_MUTED, halign="right", size_hint_x=None, width=dp(64), shorten=False)
        for c in (self.col1, self.col2, self.col3, self.col4):
            self.add_widget(c)

    def _redraw(self, *args):
        self._rect.pos, self._rect.size = self.pos, self.size
        self._ln.pos, self._ln.size = (self.x + dp(4), self.y), (max(0, self.width - dp(8)), dp(1))

    def on_release(self):
        self.on_click(self.idx)

    def set_selected(self, sel):
        if sel:
            self._bg.rgba, self._ln_col.rgba = (0.12, 0.24, 0.38, 0.92), (0.35, 0.72, 0.95, 0.65)
        else:
            self._bg.rgba = (0.04, 0.05, 0.07, 0.35 if self.idx % 2 == 0 else 0.0)
            self._ln_col.rgba = (0.18, 0.22, 0.28, 0.25)


class BouquetModal(ModalView):
    """Menu bukietów i kolorowych opcji — otwiera się nad prawą kolumną, nie zasłania aktywnego PIG."""
    def __init__(self, bouquets, current_idx, current_pig_mode, on_select, on_pig,
                 on_toggle_pig_mode, on_epg, on_refresh, on_settings, on_exit, **kwargs):
        super().__init__(
            size_hint=(0.58, 0.88),
            pos_hint={"right": 0.98, "center_y": 0.5},
            background_color=(0, 0, 0, 0.55),
            **kwargs,
        )
        self.bouquets = bouquets
        # (tekst, callback, bg_idle, border_idle, accent_col, text_idle, bg_focus)
        self.actions = [
            (
                "▸  Podgląd PIP (bieżący kanał)", on_pig,
                (0.17, 0.14, 0.04, 0.94), (0.65, 0.52, 0.14, 0.70),
                (0.95, 0.78, 0.22, 1), (0.98, 0.89, 0.55, 1), (0.34, 0.26, 0.07, 0.98),
            ),
            (
                f"▸  Tryb PIP: {pig_mode_label(current_pig_mode)}", on_toggle_pig_mode,
                (0.05, 0.17, 0.12, 0.94), (0.20, 0.62, 0.44, 0.70),
                (0.28, 0.85, 0.58, 1), (0.64, 0.96, 0.78, 1), (0.09, 0.34, 0.24, 0.98),
            ),
            (
                "▸  Pełny EPG (bieżący kanał)", on_epg,
                (0.05, 0.13, 0.20, 0.94), (0.24, 0.58, 0.78, 0.70),
                (0.35, 0.78, 0.98, 1), (0.70, 0.91, 1.00, 1), (0.10, 0.30, 0.46, 0.98),
            ),
            (
                "▸  Odśwież listę bukietów", on_refresh,
                (0.12, 0.09, 0.21, 0.94), (0.48, 0.38, 0.78, 0.70),
                (0.68, 0.56, 0.98, 1), (0.85, 0.79, 1.00, 1), (0.24, 0.18, 0.44, 0.98),
            ),
            (
                "▸  Ustawienia IP, pikon i PIP", on_settings,
                (0.19, 0.11, 0.05, 0.94), (0.72, 0.44, 0.18, 0.70),
                (0.96, 0.60, 0.24, 1), (1.00, 0.82, 0.60, 1), (0.38, 0.21, 0.08, 0.98),
            ),
            (
                "✕  Zamknij aplikację", on_exit,
                (0.24, 0.06, 0.07, 0.95), (0.76, 0.22, 0.24, 0.80),
                (0.96, 0.28, 0.28, 1), (1.00, 0.66, 0.66, 1), (0.56, 0.12, 0.14, 0.98),
            ),
        ]
        self.on_select = on_select
        self.sel_idx = max(0, min(current_idx, len(bouquets) + len(self.actions) - 1))
        self.rows = []
        self.row_styles = []

        frame = EnigmaFrame(bg=(0.03, 0.04, 0.06, 0.98), border=C_CYAN, radius=12, lw=1.3,
                            orientation="vertical", padding=dp(16), spacing=dp(8))
        frame.add_widget(make_label("BUKIETY I MENU PILOTEM", 17, C_CYAN, bold=True, size_hint_y=None, height=dp(26)))

        self.scroll = ScrollView(bar_width=dp(4), bar_color=C_CYAN)
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(4))
        box.bind(minimum_height=box.setter("height"))

        for idx, b in enumerate(bouquets):
            cnt = sum(1 for c in b.get("subservices", []) if not c.get("servicereference", "").startswith("1:64:"))
            row = self._make_row(f"{b.get('servicename', 'Bukiet')}   ({cnt})", idx)
            box.add_widget(row)
            self.rows.append(row)
            self.row_styles.append(None)

        # Wizualny separator oddzielający bukiety od opcji aplikacji
        sep_lbl = make_label("──  OPCJE I USTAWIENIA APLIKACJI  ──", 12, C_CYAN, halign="center",
                             bold=True, size_hint_y=None, height=dp(24))
        box.add_widget(sep_lbl)

        for a_idx, spec in enumerate(self.actions):
            label = spec[0]
            row = self._make_row(label, len(bouquets) + a_idx)
            box.add_widget(row)
            self.rows.append(row)
            self.row_styles.append(spec[2:])

        self.scroll.add_widget(box)
        frame.add_widget(self.scroll)
        frame.add_widget(make_label("Góra/Dół = wybór  |  OK = zatwierdź  |  Wstecz = zamknij menu",
                                    12.5, C_MUTED, halign="center", size_hint_y=None, height=dp(22)))
        self.add_widget(frame)
        self._refresh()

    def _make_row(self, text, idx):
        btn = PillButton(text=text, accent=(0, 0, 0, 0), font_size=14.5, size_hint_y=None, height=dp(38))
        btn.lbl.halign = "left"
        btn.padding = (dp(14), dp(4))
        btn.bind(on_release=lambda *a: self._activate(idx))
        return btn

    def _refresh(self):
        for i, r in enumerate(self.rows):
            st = self.row_styles[i]
            if i == self.sel_idx:
                if st is not None:
                    _, _, acc, _, bg_foc = st
                    r._bg_col.rgba, r._bd_col.rgba, r._acc_col.rgba = bg_foc, acc, acc
                    r._bd_line.width = 1.8
                else:
                    r._bg_col.rgba, r._bd_col.rgba, r._acc_col.rgba = C_FOCUS_BG, C_CYAN, C_CYAN
                    r._bd_line.width = 1.6
                r.lbl.color = (1, 1, 1, 1)
            else:
                if st is not None:
                    bg_id, bd_id, acc, txt_id, _ = st
                    r._bg_col.rgba, r._bd_col.rgba, r._acc_col.rgba = bg_id, bd_id, acc
                    r._bd_line.width = 1.1
                    r.lbl.color = txt_id
                else:
                    r._bg_col.rgba, r._bd_col.rgba, r._acc_col.rgba = (0.06, 0.075, 0.10, 0.8), (0.2, 0.23, 0.28, 0.4), (0, 0, 0, 0)
                    r._bd_line.width = 1.0
                    r.lbl.color = C_GRAY
        if self.rows:
            self.scroll.scroll_to(self.rows[self.sel_idx], padding=dp(24), animate=False)

    def _activate(self, idx):
        self.dismiss()
        if idx < len(self.bouquets):
            self.on_select(idx)
        else:
            a_idx = idx - len(self.bouquets)
            if 0 <= a_idx < len(self.actions):
                self.actions[a_idx][1]()

    def handle_key(self, key):
        if key == 273:
            self.sel_idx = (self.sel_idx - 1) % len(self.rows)
            self._refresh()
            return True
        if key == 274:
            self.sel_idx = (self.sel_idx + 1) % len(self.rows)
            self._refresh()
            return True
        if key in (13, 271, 23):
            self._activate(self.sel_idx)
            return True
        if key in (27, 276):
            self.dismiss()
            return True
        return False


# =============================================================================
# EKRANY APLIKACJI
# =============================================================================
class SettingsScreen(Screen):
    """Konfiguracja z pełną obsługą pilota Android TV, klawiaturą numeryczną i nastawą trybu PIP."""
    PAD_LAYOUT = [
        ["1", "2", "3"],
        ["4", "5", "6"],
        ["7", "8", "9"],
        [".", "0", "⌫"],
        ["WYCZYŚĆ", "GOTOWE", "ZAPISZ"],
    ]

    def __init__(self, video_backend, **kwargs):
        super().__init__(**kwargs)
        self.video_backend = video_backend
        self.zone = "fields"
        self.field_idx = 0         # 0=IP, 1=WebPort, 2=StreamPort, 3=Picon, 4=Tryb PIP, 5=Zapisz, 6=Anuluj
        self.active_edit_idx = 0   # które pole (0..2) edytuje klawiatura numeryczna
        self.pad_r, self.pad_c = 0, 0
        self.picon_options = list(PICON_PRESETS)
        self.pig_mode_key = "follow"

        outer = BoxLayout(orientation="vertical", padding=(dp(24), dp(16)))
        card = EnigmaFrame(
            bg=(0.032, 0.042, 0.064, 1), border=C_CYAN, radius=14, lw=1.3,
            orientation="vertical", padding=(dp(16), dp(12)), spacing=dp(8),
        )

        hdr = BoxLayout(size_hint_y=None, height=dp(26), spacing=dp(12))
        hdr.add_widget(make_label("Konfiguracja dekodera Enigma2  (v1.2)", 18, C_CYAN, bold=True, size_hint_x=0.55))
        hdr.add_widget(make_label(
            "Pilot: Góra/Dół = pole  •  OK/Prawo = edytuj  •  Wstecz = powrót",
            12, C_GRAY, halign="right", size_hint_x=0.45,
        ))
        card.add_widget(hdr)

        body = BoxLayout(orientation="horizontal", spacing=dp(14))

        # === LEWA KOLUMNA: POLA KONFIGURACJI ===
        left_col = BoxLayout(orientation="vertical", size_hint_x=0.56, spacing=dp(6))

        self.ip_field = TVSettingField("Adres IP dekodera", "192.168.1.50", "[OK / Prawo] wpisz")
        self.web_field = TVSettingField("Port OpenWebif (domyślnie 80)", "80", "[OK / Prawo] wpisz")
        self.stream_field = TVSettingField("Port strumienia (domyślnie 8001)", "8001", "[OK / Prawo] wpisz")
        self.picon_field = TVSettingField("Ścieżka pikon w dekoderze", DEFAULT_PICON_DIR, "[OK / ◄ ►] zmień")
        self.pig_field = TVSettingField("Tryb podglądu PIP na liście", pig_mode_label("follow"), "[OK / ◄ ►] zmień")

        self.fields = [self.ip_field, self.web_field, self.stream_field, self.picon_field, self.pig_field]
        for idx, fld in enumerate(self.fields):
            fld.bind(on_release=lambda *a, i=idx: self._on_field_click(i))
            left_col.add_widget(fld)

        self.status_lbl = make_label("", 12.5, C_ERROR, size_hint_y=None, height=dp(18))
        left_col.add_widget(self.status_lbl)

        btn_row = BoxLayout(spacing=dp(8), size_hint_y=None, height=dp(40))
        self.save_btn = PillButton(text="ZAPISZ", accent=C_MINT, font_size=14)
        self.save_btn.bind(on_release=self.save_and_connect)
        self.cancel_btn = PillButton(text="ANULUJ", accent=C_MUTED, font_size=14)
        self.cancel_btn.bind(on_release=self.go_back)
        self.exit_btn = PillButton(text="ZAMKNIJ", accent=C_ERROR, font_size=14)
        self.exit_btn.bind(on_release=lambda *a: App.get_running_app().exit_application())
        btn_row.add_widget(self.save_btn)
        btn_row.add_widget(self.cancel_btn)
        btn_row.add_widget(self.exit_btn)
        left_col.add_widget(btn_row)
        body.add_widget(left_col)

        # === PRAWA KOLUMNA: KLAWIATURA NUMERYCZNA TV ===
        right_col = EnigmaFrame(
            bg=(0.022, 0.030, 0.048, 0.96), border=C_BORDER_SUB, radius=10,
            orientation="vertical", size_hint_x=0.44, padding=dp(10), spacing=dp(7),
        )
        self.pad_title = make_label("Edycja: Adres IP dekodera", 13.5, C_MINT, halign="center",
                                    bold=True, size_hint_y=None, height=dp(22))
        right_col.add_widget(self.pad_title)

        grid = GridLayout(cols=3, spacing=dp(7))
        self.pad_buttons = []
        for r, row_keys in enumerate(self.PAD_LAYOUT):
            row_btns = []
            for c, label in enumerate(row_keys):
                fs = 13.5 if len(label) > 2 else 19
                acc = C_MINT if label == "ZAPISZ" else (C_CYAN if label == "GOTOWE" else C_BORDER)
                btn = PillButton(text=label, accent=acc, font_size=fs)
                btn.bind(on_release=lambda *a, rr=r, cc=c: self._on_pad_click(rr, cc))
                grid.add_widget(btn)
                row_btns.append(btn)
            self.pad_buttons.append(row_btns)
        right_col.add_widget(grid)
        body.add_widget(right_col)

        card.add_widget(body)
        outer.add_widget(card)
        self.add_widget(outer)

    def _has_saved_ip(self):
        app = App.get_running_app()
        return bool(app and app.config_data.get("ip"))

    def on_pre_enter(self):
        self.video_backend.hide()
        cfg = App.get_running_app().config_data
        self.ip_field.text = cfg.get("ip", "192.168.1.50")
        self.web_field.text = str(cfg.get("web_port", "80"))
        self.stream_field.text = str(cfg.get("stream_port", "8001"))

        pdir = cfg.get("picon_dir", DEFAULT_PICON_DIR) or DEFAULT_PICON_DIR
        self.picon_options = list(PICON_PRESETS)
        if pdir not in self.picon_options:
            self.picon_options.insert(0, pdir)
        self.picon_field.text = pdir

        pm = cfg.get("pig_mode", "follow")
        self.pig_mode_key = pm if any(k == pm for k, _ in PIG_MODES) else "follow"
        self.pig_field.text = pig_mode_label(self.pig_mode_key)

        self.status_lbl.text = ""
        self.zone = "fields"
        self.field_idx = 0
        self.active_edit_idx = 0
        self.pad_r, self.pad_c = 0, 0
        has_ip = self._has_saved_ip()
        self.cancel_btn.disabled = not has_ip
        self.cancel_btn.opacity = 1.0 if has_ip else 0.35
        self._refresh_focus()

    def _refresh_focus(self):
        names = ["Adres IP dekodera", "Port OpenWebif", "Port strumienia"]
        self.pad_title.text = f"Edycja: {names[self.active_edit_idx]}"

        for idx, fld in enumerate(self.fields):
            fld.set_state(
                focused=(self.zone == "fields" and self.field_idx == idx),
                editing=(idx == self.active_edit_idx and idx in (0, 1, 2)),
            )

        for idx, btn, base_bg, foc_bg, acc in (
            (5, self.save_btn, (0.08, 0.26, 0.20, 0.95), (0.14, 0.44, 0.34, 0.98), C_MINT),
            (6, self.cancel_btn, (0.08, 0.09, 0.12, 0.95), C_FOCUS_BG, C_MUTED),
            (7, self.exit_btn, (0.25, 0.07, 0.08, 0.95), (0.56, 0.13, 0.15, 0.98), C_ERROR),
        ):
            focused = (self.zone == "fields" and self.field_idx == idx)
            btn._bg_col.rgba = foc_bg if focused else base_bg
            btn._bd_col.rgba = (acc if idx == 7 else C_CYAN) if focused else (0.30, 0.35, 0.42, 0.7)
            btn._bd_line.width = 2.0 if focused else 1.0
            btn._acc_col.rgba = (acc if idx == 7 else C_CYAN) if focused else acc
            btn.lbl.color = (1, 1, 1, 1) if focused else C_WHITE

        for r, row_btns in enumerate(self.pad_buttons):
            for c, btn in enumerate(row_btns):
                key_lbl = self.PAD_LAYOUT[r][c]
                focused = (self.zone == "pad" and self.pad_r == r and self.pad_c == c)
                if focused:
                    btn._bg_col.rgba = C_FOCUS_BG
                    btn._bd_col.rgba = C_CYAN
                    btn._bd_line.width = 2.0
                    btn.lbl.color = (1, 1, 1, 1)
                else:
                    if key_lbl == "ZAPISZ":
                        btn._bg_col.rgba = (0.09, 0.28, 0.22, 0.95)
                    elif key_lbl == "WYCZYŚĆ":
                        btn._bg_col.rgba = (0.22, 0.09, 0.11, 0.95)
                    else:
                        btn._bg_col.rgba = (0.075, 0.095, 0.135, 0.95)
                    btn._bd_col.rgba = (0.28, 0.33, 0.40, 0.65)
                    btn._bd_line.width = 1.0
                    btn.lbl.color = C_WHITE

    def _cycle_picon(self, step):
        cur = self.picon_field.text
        idx = self.picon_options.index(cur) if cur in self.picon_options else 0
        self.picon_field.text = self.picon_options[(idx + step) % len(self.picon_options)]

    def _cycle_pig_mode(self, step):
        keys = [k for k, _ in PIG_MODES]
        idx = keys.index(self.pig_mode_key) if self.pig_mode_key in keys else 0
        self.pig_mode_key = keys[(idx + step) % len(keys)]
        self.pig_field.text = pig_mode_label(self.pig_mode_key)

    def _apply_char(self, ch):
        fld = self.fields[self.active_edit_idx]
        cur = fld.text
        if ch == "⌫":
            fld.text = cur[:-1]
        elif ch == "WYCZYŚĆ":
            fld.text = ""
        elif ch == "GOTOWE":
            self.zone = "fields"
            self.field_idx = 5
        elif ch == "ZAPISZ":
            self.save_and_connect()
            return
        elif ch == ".":
            if self.active_edit_idx == 0 and len(cur) < 15:
                fld.text = cur + "."
        elif ch.isdigit():
            limit = 15 if self.active_edit_idx == 0 else 5
            if len(cur) < limit:
                fld.text = cur + ch
        self.status_lbl.text = ""
        self._refresh_focus()

    def _on_field_click(self, idx):
        self.field_idx = idx
        if idx in (0, 1, 2):
            self.active_edit_idx = idx
            self.zone = "pad"
        elif idx == 3:
            self._cycle_picon(1)
            self.zone = "fields"
        elif idx == 4:
            self._cycle_pig_mode(1)
            self.zone = "fields"
        self._refresh_focus()

    def _on_pad_click(self, r, c):
        self.zone = "pad"
        self.pad_r, self.pad_c = r, c
        self._apply_char(self.PAD_LAYOUT[r][c])

    def handle_dpad(self, key, codepoint=""):
        if codepoint and (codepoint.isdigit() or codepoint == "."):
            self._apply_char(codepoint)
            return True
        if key == 8:
            self._apply_char("⌫")
            return True

        valid = [0, 1, 2, 3, 4, 5, 6, 7] if self._has_saved_ip() else [0, 1, 2, 3, 4, 5, 7]

        if self.zone == "fields":
            if key == 273:
                pos = valid.index(self.field_idx) if self.field_idx in valid else 0
                self.field_idx = valid[(pos - 1) % len(valid)]
                if self.field_idx in (0, 1, 2):
                    self.active_edit_idx = self.field_idx
                self._refresh_focus()
                return True
            if key == 274:
                pos = valid.index(self.field_idx) if self.field_idx in valid else 0
                self.field_idx = valid[(pos + 1) % len(valid)]
                if self.field_idx in (0, 1, 2):
                    self.active_edit_idx = self.field_idx
                self._refresh_focus()
                return True
            if key == 275:
                if self.field_idx in (0, 1, 2):
                    self.active_edit_idx = self.field_idx
                    self.zone = "pad"
                elif self.field_idx == 3:
                    self._cycle_picon(1)
                elif self.field_idx == 4:
                    self._cycle_pig_mode(1)
                elif self.field_idx == 5:
                    self.field_idx = 6 if self._has_saved_ip() else 7
                elif self.field_idx == 6:
                    self.field_idx = 7
                self._refresh_focus()
                return True
            if key == 276:
                if self.field_idx == 3:
                    self._cycle_picon(-1)
                elif self.field_idx == 4:
                    self._cycle_pig_mode(-1)
                elif self.field_idx == 7:
                    self.field_idx = 6 if self._has_saved_ip() else 5
                elif self.field_idx == 6:
                    self.field_idx = 5
                self._refresh_focus()
                return True
            if key in (13, 271, 23):
                if self.field_idx in (0, 1, 2):
                    self.active_edit_idx = self.field_idx
                    self.zone = "pad"
                    self._refresh_focus()
                elif self.field_idx == 3:
                    self._cycle_picon(1)
                    self._refresh_focus()
                elif self.field_idx == 4:
                    self._cycle_pig_mode(1)
                    self._refresh_focus()
                elif self.field_idx == 5:
                    self.save_and_connect()
                elif self.field_idx == 6:
                    self.go_back()
                elif self.field_idx == 7:
                    App.get_running_app().exit_application()
                return True
            if key == 27:
                if self._has_saved_ip():
                    self.go_back()
                    return True
                self.status_lbl.text = "Naciśnij WSTECZ ponownie, aby zamknąć aplikację"
                return True

        else:
            if key == 273:
                self.pad_r = (self.pad_r - 1) % len(self.PAD_LAYOUT)
                self._refresh_focus()
                return True
            if key == 274:
                self.pad_r = (self.pad_r + 1) % len(self.PAD_LAYOUT)
                self._refresh_focus()
                return True
            if key == 275:
                self.pad_c = (self.pad_c + 1) % 3
                self._refresh_focus()
                return True
            if key == 276:
                if self.pad_c > 0:
                    self.pad_c -= 1
                else:
                    self.zone = "fields"
                self._refresh_focus()
                return True
            if key in (13, 271, 23):
                self._apply_char(self.PAD_LAYOUT[self.pad_r][self.pad_c])
                return True
            if key == 27:
                self.zone = "fields"
                self._refresh_focus()
                return True

        return False

    def go_back(self, *args):
        app = App.get_running_app()
        if app.config_data.get("ip"):
            app.root.current = "main"

    def save_and_connect(self, *args):
        ip = self.ip_field.text.strip()
        parts = ip.split(".")
        if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            self.status_lbl.text = "Podaj poprawny adres IPv4 (np. 192.168.1.50)"
            return
        app = App.get_running_app()
        app.save_config(
            ip,
            self.web_field.text.strip() or "80",
            self.stream_field.text.strip() or "8001",
            self.picon_field.text.strip() or DEFAULT_PICON_DIR,
            pig_mode=self.pig_mode_key,
        )
        app.root.current = "main"
        app.main_screen.load_bouquets()


class FullscreenPlayerScreen(Screen):
    """Ekran pełnoekranowy dla Android TV (obsługuje pilota, Infobar oraz wybór Audio/Napisów)."""
    def __init__(self, video_backend, **kwargs):
        super().__init__(**kwargs)
        self.video_backend = video_backend

    def handle_dpad(self, key):
        app = App.get_running_app()
        if self.video_backend.is_track_menu_open():
            if key == 27:
                self.video_backend.close_track_menu()
                return True
            if key == 273:
                self.video_backend.move_track_menu(-1)
                return True
            if key == 274:
                self.video_backend.move_track_menu(1)
                return True
            if key in (275, 276):
                self.video_backend.toggle_track_tab()
                return True
            if key in (13, 271, 23):
                self.video_backend.confirm_track_menu()
                return True
            return True

        if key == 27:
            app.root.current = "main"
            return True
        if key in (13, 271, 23):
            self.video_backend.toggle_infobar()
            return True
        if key == 273:
            app.main_screen.zap_in_fullscreen(-1)
            return True
        if key == 274:
            app.main_screen.zap_in_fullscreen(1)
            return True
        if key in (276, ord("a"), ord("m")):
            self.video_backend.open_track_menu(0)
            return True
        if key == ord("s"):
            self.video_backend.open_track_menu(1)
            return True
        if key in (ord("e"), 275):
            app.open_epg_from("player")
            return True
        if key in (ord("+"), ord("=")):
            v = self.video_backend.change_volume(0.1)
            self.video_backend.show_toast("Głośność", f"{v}%", 2.0)
            return True
        if key == ord("-"):
            v = self.video_backend.change_volume(-0.1)
            self.video_backend.show_toast("Głośność", f"{v}%", 2.0)
            return True
        return False


class EPGScreen(Screen):
    """Pełny ekran EPG: Info + Podobne audycje, Pojedynczy EPG, Multi EPG, Dodaj timer."""
    def __init__(self, video_backend, picon_manager, **kwargs):
        super().__init__(**kwargs)
        self.video_backend = video_backend
        self.picon_manager = picon_manager
        self.return_screen = "main"
        self.channel_item = None
        self.bouquet_items, self.bouquet_ref = [], ""
        self.mode, self.single_events, self.multi_rows = "single", [], []
        self.sel_idx, self.row_widgets = 0, []
        self._req_id = self._sim_req_id = 0

        root = BoxLayout(orientation="horizontal", padding=dp(14), spacing=dp(12))

        left_col = BoxLayout(orientation="vertical", size_hint_x=0.37, spacing=dp(6))
        left_box = EnigmaFrame(bg=C_PANEL, border=C_BORDER, radius=10, orientation="vertical",
                               padding=(dp(12), dp(8)), spacing=dp(5))

        self.left_ev_title = make_label("EPG", 18, C_CYAN, halign="center", size_hint_y=None, height=dp(26))
        self.left_ch_name = make_label("", 16.5, C_WHITE, halign="center", bold=True, size_hint_y=None, height=dp(24))
        left_box.add_widget(self.left_ev_title)
        left_box.add_widget(self.left_ch_name)

        self.pig_frame = EnigmaFrame(bg=(0.01, 0.015, 0.025, 1), border=C_BORDER, radius=6,
                                     padding=dp(3), size_hint_y=0.45)
        self.pig_image = Image(allow_stretch=True, keep_ratio=True, opacity=0)
        self.pig_frame.add_widget(self.pig_image)
        self.pig_frame.bind(pos=self._sync_pig, size=self._sync_pig, on_touch_down=self._on_pig_touch)
        left_box.add_widget(self.pig_frame)

        trow = BoxLayout(size_hint_y=None, height=dp(22), spacing=dp(8))
        self.left_start = make_label("", 14, C_WHITE, bold=True, size_hint_x=None, width=dp(48), shorten=False)
        bh = BoxLayout(padding=(0, dp(7)))
        self.left_progress = GradientProgressBar(framed=False)
        bh.add_widget(self.left_progress)
        self.left_end = make_label("", 14, C_WHITE, halign="right", bold=True, size_hint_x=None, width=dp(48), shorten=False)
        trow.add_widget(self.left_start)
        trow.add_widget(bh)
        trow.add_widget(self.left_end)
        left_box.add_widget(trow)

        self.left_sub_title = make_label("", 14, C_CYAN, halign="center", valign="top",
                                         shorten=False, size_hint_y=None, height=dp(36))
        left_box.add_widget(self.left_sub_title)

        info_card = EnigmaFrame(bg=(0.02, 0.03, 0.05, 0.9), border=C_BORDER_SUB, radius=10,
                                orientation="horizontal", padding=dp(10), spacing=dp(10), size_hint_y=0.35)
        self.left_picon = Image(allow_stretch=True, keep_ratio=True, size_hint_x=0.58, opacity=0)
        info_card.add_widget(self.left_picon)
        info_card.add_widget(make_label(
            "[b][color=#5CC8FF]i[/color][color=#D93838]i[/color][/b]\n[size=11sp][color=#8C99A8]INFO EPG[/color][/size]",
            34, C_WHITE, halign="center", markup=True, shorten=False, size_hint_x=0.42,
        ))
        left_box.add_widget(info_card)
        left_col.add_widget(left_box)

        lbtns = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(10), padding=(dp(10), 0))
        for txt, fn in (("EPG", self._toggle_info_single), ("MENU", self._open_settings), ("EXIT", self.go_back)):
            b = PillButton(text=txt, accent=C_CYAN, font_size=13.5)
            b.bind(on_release=lambda *a, f=fn: f())
            lbtns.add_widget(b)
        left_col.add_widget(lbtns)
        root.add_widget(left_col)

        right_col = BoxLayout(orientation="vertical", size_hint_x=0.63, spacing=dp(6))

        top_row = BoxLayout(size_hint_y=None, height=dp(48))
        self.toast_lbl = make_label("", 14, C_MINT, bold=True)
        top_row.add_widget(self.toast_lbl)
        clk_box = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(220))
        self.clock_lbl = make_label("00:00:00", 18, C_CYAN, halign="right", valign="bottom", shorten=False)
        self.date_lbl = make_label("", 13.5, C_CYAN, halign="right", valign="top", shorten=False)
        clk_box.add_widget(self.clock_lbl)
        clk_box.add_widget(self.date_lbl)
        top_row.add_widget(clk_box)
        right_col.add_widget(top_row)

        main_card = EnigmaFrame(bg=C_PANEL, border=C_BORDER, radius=10, orientation="vertical",
                                padding=(dp(14), dp(10)), spacing=dp(6))
        hdr = BoxLayout(size_hint_y=None, height=dp(28), spacing=dp(10))
        self.hdr_channel = make_label("", 18, C_MINT, bold=True, size_hint_x=0.46)
        self.hdr_time = make_label("", 16, C_CYAN, halign="center", size_hint_x=0.38, shorten=False)
        self.hdr_dur = make_label("", 16, C_CYAN, halign="right", size_hint_x=0.16, shorten=False)
        hdr.add_widget(self.hdr_channel)
        hdr.add_widget(self.hdr_time)
        hdr.add_widget(self.hdr_dur)
        main_card.add_widget(hdr)

        sep = Widget(size_hint_y=None, height=dp(1))
        with sep.canvas:
            Color(*C_BORDER)
            sep_r = Rectangle(pos=sep.pos, size=sep.size)
        sep.bind(pos=lambda i, v: setattr(sep_r, "pos", v), size=lambda i, v: setattr(sep_r, "size", v))
        main_card.add_widget(sep)

        self.body_container = BoxLayout(orientation="vertical", spacing=dp(6))
        main_card.add_widget(self.body_container)

        self.info_scroll = ScrollView(bar_width=dp(4), bar_color=C_CYAN)
        self.info_text = Label(text="", markup=True, font_size=sp(15), color=C_GRAY,
                               halign="left", valign="top", size_hint_y=None)
        self.info_text.bind(
            width=lambda inst, w: setattr(inst, "text_size", (w - dp(8), None)),
            texture_size=lambda inst, ts: setattr(inst, "height", ts[1] + dp(12)),
        )
        self.info_scroll.add_widget(self.info_text)

        self.table_view = BoxLayout(orientation="vertical", spacing=dp(5))
        self.tbl_hdr = BoxLayout(size_hint_y=None, height=dp(22), padding=(dp(10), 0), spacing=dp(8))
        self.th1 = make_label("DATA", 12, C_MUTED, bold=True, size_hint_x=None, width=dp(80), shorten=False)
        self.th2 = make_label("GODZINA", 12, C_MUTED, bold=True, size_hint_x=None, width=dp(100), shorten=False)
        self.th3 = make_label("AUDYCJA", 12, C_MUTED, bold=True)
        self.th4 = make_label("CZAS", 12, C_MUTED, halign="right", bold=True, size_hint_x=None, width=dp(64), shorten=False)
        for th in (self.th1, self.th2, self.th3, self.th4):
            self.tbl_hdr.add_widget(th)
        self.table_view.add_widget(self.tbl_hdr)

        self.tbl_scroll = ScrollView(bar_width=dp(4), bar_color=C_CYAN, size_hint_y=0.72)
        self.tbl_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(2))
        self.tbl_box.bind(minimum_height=self.tbl_box.setter("height"))
        self.tbl_scroll.add_widget(self.tbl_box)
        self.table_view.add_widget(self.tbl_scroll)

        desc_box = EnigmaFrame(bg=(0.018, 0.024, 0.036, 1), border=C_BORDER_SUB, radius=8,
                               padding=(dp(10), dp(6)), size_hint_y=0.28)
        self.tbl_desc = make_label("", 13, C_GRAY, valign="top", shorten=False)
        desc_box.add_widget(self.tbl_desc)
        self.table_view.add_widget(desc_box)

        right_col.add_widget(main_card)

        bbar = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(12), padding=(dp(8), 0))
        for txt, col, fn in (
            ("Info / Opis", (0.88, 0.24, 0.24, 1), lambda: self.set_mode("info")),
            ("Dodaj timer", (0.24, 0.78, 0.40, 1), self.add_timer_for_selected),
            ("Pojedynczy EPG", (0.92, 0.78, 0.22, 1), lambda: self.set_mode("single")),
            ("Multi EPG", C_CYAN, lambda: self.set_mode("multi")),
        ):
            btn = PillButton(text=txt, accent=col, underline=True, font_size=13.5)
            btn.bind(on_release=lambda *a, f=fn: f())
            bbar.add_widget(btn)
        right_col.add_widget(bbar)

        root.add_widget(right_col)
        self.add_widget(root)
        Clock.schedule_interval(self._update_clock, 1.0)
        self._update_clock(0)

    def on_enter(self):
        Clock.schedule_once(lambda dt: self._sync_pig(), 0.05)

    def _sync_pig(self, *args):
        app = App.get_running_app()
        if app and app.root and app.root.current == "epg":
            self.video_backend.sync_pig_widget(self.pig_frame)

    def _update_clock(self, dt):
        now = datetime.datetime.now()
        self.clock_lbl.text = now.strftime("%H:%M:%S")
        self.date_lbl.text = f"{PL_DAYS[now.weekday()]} {now.day} {PL_MONTHS[now.month]} {now.year}"

    def _open_settings(self):
        App.get_running_app().root.current = "settings"

    def _toggle_info_single(self):
        self.set_mode("info" if self.mode != "info" else "single")

    def _on_pig_touch(self, widget, touch):
        if widget.collide_point(*touch.pos) and touch.button == "left" and self.channel_item:
            App.get_running_app().main_screen.play_channel_fullscreen(self.channel_item)
            return True
        return False

    def go_back(self):
        app = App.get_running_app()
        if self.return_screen == "player" and self.channel_item and self.video_backend.is_embedded:
            app.main_screen.play_channel_fullscreen(self.channel_item)
        else:
            app.root.current = "main"

    def open_for_channel(self, channel_item, bouquet_items, bouquet_ref, return_screen="main", start_mode="single"):
        self.channel_item = channel_item
        self.bouquet_items = [it for it in bouquet_items if it["type"] == "channel"]
        self.bouquet_ref = bouquet_ref
        self.return_screen = return_screen
        self.toast_lbl.text = ""
        self.single_events, self.multi_rows, self.sel_idx = [], [], 0

        sref, name = channel_item.get("sref", ""), channel_item.get("name", "")
        self.left_ch_name.text = self.hdr_channel.text = name
        self.left_picon.opacity = self.pig_image.opacity = 0
        self.picon_manager.get_picon(sref, name, self._on_picon)
        self.set_mode(start_mode, fetch=True)

    def _on_picon(self, sref, tex):
        if self.channel_item and self.channel_item.get("sref") == sref and tex:
            self.left_picon.texture, self.left_picon.opacity = tex, 1.0
            if not (self.video_backend.is_embedded and self.video_backend.is_playing):
                self.pig_image.texture, self.pig_image.opacity = tex, 0.85

    def set_mode(self, mode, fetch=False):
        self.mode = mode
        self.body_container.clear_widgets()
        if mode == "info":
            self.body_container.add_widget(self.info_scroll)
            if not self.single_events or fetch:
                self._fetch_single_epg()
            else:
                self._render_info_view()
        elif mode == "single":
            self.th1.text, self.th2.text, self.th3.text, self.th4.text = "DATA", "GODZINA", "AUDYCJA", "CZAS"
            self.body_container.add_widget(self.table_view)
            if not self.single_events or fetch:
                self._fetch_single_epg()
            else:
                self._render_single_table()
        elif mode == "multi":
            self.th1.text, self.th2.text, self.th3.text, self.th4.text = "KANAŁ", "TERAZ", "AUDYCJA  /  NASTĘPNIE", "KONIEC"
            self.body_container.add_widget(self.table_view)
            self._fetch_multi_epg()

    def _fetch_single_epg(self):
        if not self.channel_item:
            return
        self._req_id += 1
        rid = self._req_id
        sref = self.channel_item.get("sref", "")
        cfg = App.get_running_app().config_data
        url = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgservice?sRef={urllib.parse.quote(sref, safe='')}"
        self.tbl_desc.text = "Pobieranie pełnej tabeli EPG z dekodera..."
        self.info_text.text = "Pobieranie danych EPG..."

        def worker():
            evs = []
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                with urllib.request.urlopen(req, timeout=7) as r:
                    evs = json.loads(r.read().decode("utf-8")).get("events", [])
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._on_single_epg(rid, evs))

        threading.Thread(target=worker, daemon=True).start()

    def _on_single_epg(self, rid, events):
        if rid != self._req_id:
            return
        now_ts = time.time()
        self.single_events = [e for e in events if (e.get("begin_timestamp") or 0) + (e.get("duration_sec") or 0) >= now_ts - 60] or events
        self.sel_idx = 0
        if self.mode == "info":
            self._render_info_view()
        elif self.mode == "single":
            self._render_single_table()

    def _render_single_table(self):
        self.tbl_box.clear_widgets()
        self.row_widgets = []
        if not self.single_events:
            self.tbl_box.add_widget(make_label("Brak danych EPG dla tego kanału.", 15, C_MUTED, halign="center",
                                               size_hint_y=None, height=dp(60)))
            self.tbl_desc.text = ""
            return

        for i, ev in enumerate(self.single_events[:120]):
            b, d = ev.get("begin_timestamp") or 0, ev.get("duration_sec") or 0
            row = EPGTableRow(i, self._on_table_row_click)
            row.col1.text = fmt_epg_date_short(b)
            row.col2.text = f"{fmt_epg_time(b)} - {fmt_epg_time(b + d)}" if b else ""
            row.col3.text = f"[b]{escape_markup(ev.get('title', ''))}[/b]"
            row.col4.text = f"{int(d // 60)} min" if d else ""
            self.tbl_box.add_widget(row)
            self.row_widgets.append(row)

        self.sel_idx = max(0, min(self.sel_idx, len(self.row_widgets) - 1))
        self._highlight_table_row()

    def _fetch_multi_epg(self):
        if not self.bouquet_ref:
            return
        self._req_id += 1
        rid = self._req_id
        cfg = App.get_running_app().config_data
        bref_q = urllib.parse.quote(self.bouquet_ref, safe="")
        url_now = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgnow?bRef={bref_q}"
        url_next = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgnext?bRef={bref_q}"
        self.tbl_desc.text = "Pobieranie Multi EPG dla całego bukietu..."

        def worker():
            now_map, next_map = {}, {}
            for url, target in ((url_now, now_map), (url_next, next_map)):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                    with urllib.request.urlopen(req, timeout=7) as r:
                        for ev in json.loads(r.read().decode("utf-8")).get("events", []):
                            if ev.get("sref"):
                                target[ev["sref"]] = ev
                except Exception:
                    pass
            Clock.schedule_once(lambda dt: self._on_multi_epg(rid, now_map, next_map))

        threading.Thread(target=worker, daemon=True).start()

    def _on_multi_epg(self, rid, now_map, next_map):
        if rid != self._req_id or self.mode != "multi":
            return
        self.multi_rows, self.row_widgets = [], []
        self.tbl_box.clear_widgets()
        cur_sref = self.channel_item.get("sref", "") if self.channel_item else ""
        start_sel = 0

        for idx, ch in enumerate(self.bouquet_items):
            sref = ch["sref"]
            ev_now, ev_nxt = now_map.get(sref), next_map.get(sref)
            self.multi_rows.append({"ch": ch, "now": ev_now, "next": ev_nxt})
            if sref == cur_sref:
                start_sel = idx

            row = EPGTableRow(idx, self._on_table_row_click)
            row.col1.width, row.col2.width = dp(145), dp(58)
            row.col1.text, row.col1.color = f"{ch['num']}. {ch['name']}", C_MINT
            if ev_now and ev_now.get("title"):
                b, d = ev_now.get("begin_timestamp") or 0, ev_now.get("duration_sec") or 0
                row.col2.text = fmt_epg_time(b)
                nxt_txt = f"  [color=#48BF84]| {fmt_epg_time(ev_nxt.get('begin_timestamp'))} {escape_markup(ev_nxt.get('title', ''))}[/color]" if ev_nxt and ev_nxt.get("title") else ""
                row.col3.text = f"[color=#F2F5FA]{escape_markup(ev_now.get('title', ''))}[/color]{nxt_txt}"
                row.col4.text = fmt_epg_time(b + d) if b and d else ""
            else:
                row.col2.text, row.col3.text, row.col4.text = "--:--", "[color=#6D7682]Brak danych EPG[/color]", ""
            self.tbl_box.add_widget(row)
            self.row_widgets.append(row)

        self.sel_idx = start_sel
        self._highlight_table_row()

    def _on_table_row_click(self, idx):
        if idx == self.sel_idx:
            self._activate_selected()
        else:
            self.sel_idx = idx
            self._highlight_table_row()

    def _highlight_table_row(self):
        if not self.row_widgets:
            return
        self.sel_idx = max(0, min(self.sel_idx, len(self.row_widgets) - 1))
        for i, rw in enumerate(self.row_widgets):
            rw.set_selected(i == self.sel_idx)
        self.tbl_scroll.scroll_to(self.row_widgets[self.sel_idx], padding=dp(36), animate=False)

        if self.mode == "single" and self.sel_idx < len(self.single_events):
            ev = self.single_events[self.sel_idx]
            self._update_event_headers(self.channel_item.get("name", ""), ev)
            desc = (ev.get("longdesc") or ev.get("shortdesc") or "").strip().replace("\n", " ")
            self.tbl_desc.text = (desc or "Brak szczegółowego opisu.") + "   [OK = opis | Prawo = Multi EPG]"
        elif self.mode == "multi" and self.sel_idx < len(self.multi_rows):
            rec = self.multi_rows[self.sel_idx]
            ev = rec["now"] or {}
            self._update_event_headers(rec["ch"]["name"], ev)
            desc = (ev.get("longdesc") or ev.get("shortdesc") or "").strip().replace("\n", " ")
            self.tbl_desc.text = (desc or "Brak opisu audycji.") + "   [OK = przełącz | Lewo = Pojedynczy EPG]"

    def _update_event_headers(self, ch_name, ev):
        title = ev.get("title", "") if ev else ""
        b, d = (ev.get("begin_timestamp") or 0) if ev else 0, (ev.get("duration_sec") or 0) if ev else 0
        now_ts = time.time()
        self.hdr_channel.text = self.left_ch_name.text = ch_name
        self.left_ev_title.text, self.left_sub_title.text = (title or ch_name), title

        if b > 0 and d > 0:
            t1, t2 = fmt_epg_time(b), fmt_epg_time(b + d)
            self.hdr_time.text = f"{fmt_epg_date_short(b)}, {t1} - {t2}"
            self.hdr_dur.text = f"{int(d // 60)} min"
            self.left_start.text, self.left_end.text = t1, t2
            self.left_progress.set_progress(max(0.0, min(1.0, (now_ts - b) / d)) if b <= now_ts else 0.0, visible=True)
        else:
            self.hdr_time.text = self.hdr_dur.text = self.left_start.text = self.left_end.text = ""
            self.left_progress.set_progress(0.0, visible=False)

    def _render_info_view(self):
        if not self.single_events:
            self.info_text.text = "Brak danych EPG dla wybranego kanału."
            return
        self.sel_idx = max(0, min(self.sel_idx, len(self.single_events) - 1))
        ev = self.single_events[self.sel_idx]
        self._update_event_headers(self.channel_item.get("name", "") if self.channel_item else "", ev)
        self._set_info_markup(ev, None)
        self._fetch_similar_events(ev)

    def _set_info_markup(self, ev, similar_lines):
        title = escape_markup((ev.get("title") or "").strip())
        shortd = escape_markup((ev.get("shortdesc") or "").strip())
        longd = escape_markup((ev.get("longdesc") or "").strip())
        parts = []
        if title:
            parts.append(f"[color=#F2F5FA][b]{title}[/b][/color]\n")
        if shortd and shortd != title:
            parts.append(f"[color=#D5DCE4]{shortd}[/color]\n")
        parts.append(f"[color=#C4CDD8]{longd}[/color]\n" if longd else ("" if shortd else "[color=#7A8492]Brak opisu audycji.[/color]\n"))
        if similar_lines is None:
            parts.append("\n[color=#7A8492]Szukam podobnych audycji...[/color]")
        elif similar_lines:
            parts.append("\n[color=#F2F5FA]Podobne audycje:[/color]")
            parts.extend(f"[color=#B5C0CE]{escape_markup(ln)}[/color]" for ln in similar_lines)
        self.info_text.text = "\n".join(parts)

    def _fetch_similar_events(self, ev):
        title = (ev.get("title") or "").strip()
        if not title:
            return self._set_info_markup(ev, [])
        self._sim_req_id += 1
        rid = self._sim_req_id
        q = title.split(":")[0].strip() if ":" in title and len(title.split(":")[0].strip()) >= 5 else title
        cfg = App.get_running_app().config_data
        url = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgsearch?search={urllib.parse.quote(q)}"

        def worker():
            lines = []
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    evs = json.loads(r.read().decode("utf-8")).get("events", [])
                for e in evs:
                    if e.get("id") == ev.get("id"):
                        continue
                    b = e.get("begin_timestamp") or 0
                    if b > 0:
                        dt = datetime.datetime.fromtimestamp(b)
                        lines.append(f"{dt.day} {PL_MONTHS_SHORT[dt.month]} {dt.year}, {dt.strftime('%H:%M')} - {e.get('sname', '')}")
                        if len(lines) >= 6:
                            break
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._apply_similar(rid, ev, lines))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_similar(self, rid, ev, lines):
        if rid == self._sim_req_id and self.mode == "info":
            self._set_info_markup(ev, lines)

    def add_timer_for_selected(self):
        ev, sref = None, self.channel_item.get("sref", "") if self.channel_item else ""
        if self.mode in ("single", "info") and self.sel_idx < len(self.single_events):
            ev = self.single_events[self.sel_idx]
        elif self.mode == "multi" and self.sel_idx < len(self.multi_rows):
            rec = self.multi_rows[self.sel_idx]
            sref, ev = rec["ch"]["sref"], rec["now"]

        event_id = ev.get("id") if ev else None
        if not sref or not event_id:
            self.toast_lbl.text = "Brak ID audycji do dodania timera."
            return
        cfg = App.get_running_app().config_data
        url = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/timeraddbyeventid?sRef={urllib.parse.quote(sref, safe='')}&eventid={event_id}"
        self.toast_lbl.text = "Wysyłam timer do dekodera..."

        def worker():
            msg = "Błąd dodawania timera"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    res = json.loads(r.read().decode("utf-8"))
                    msg = res.get("message") or ("Timer dodany pomyślnie!" if res.get("result") else "Nie udało się dodać timera")
            except Exception as e:
                msg = f"Błąd timera: {e}"
            Clock.schedule_once(lambda dt: setattr(self.toast_lbl, "text", msg))

        threading.Thread(target=worker, daemon=True).start()

    def _activate_selected(self):
        if self.mode == "single":
            self.set_mode("info")
        elif self.mode == "info":
            self.set_mode("single")
        elif self.mode == "multi" and self.sel_idx < len(self.multi_rows):
            App.get_running_app().main_screen.play_channel_fullscreen(self.multi_rows[self.sel_idx]["ch"])

    def handle_dpad(self, key):
        if key == 27:
            self.go_back()
            return True
        if key == 273:
            if self.mode == "info" and self.single_events:
                self.sel_idx = (self.sel_idx - 1) % len(self.single_events)
                self._render_info_view()
            elif self.row_widgets:
                self.sel_idx = max(0, self.sel_idx - 1)
                self._highlight_table_row()
            return True
        if key == 274:
            if self.mode == "info" and self.single_events:
                self.sel_idx = (self.sel_idx + 1) % len(self.single_events)
                self._render_info_view()
            elif self.row_widgets:
                self.sel_idx = min(len(self.row_widgets) - 1, self.sel_idx + 1)
                self._highlight_table_row()
            return True
        if key == 275:
            nxt = {"single": "multi", "multi": "info", "info": "single"}
            self.set_mode(nxt.get(self.mode, "single"))
            return True
        if key == 276:
            prv = {"single": "info", "info": "multi", "multi": "single"}
            self.set_mode(prv.get(self.mode, "single"))
            return True
        if key in (13, 271, 23):
            self._activate_selected()
            return True
        if key == ord("1"):
            self.set_mode("info")
            return True
        if key == ord("2"):
            self.add_timer_for_selected()
            return True
        if key == ord("3"):
            self.set_mode("single")
            return True
        if key == ord("4"):
            self.set_mode("multi")
            return True
        return False


class EnigmaChannelSelectionScreen(Screen):
    VISIBLE_ROWS = 12 if platform == "android" else 16

    def __init__(self, video_backend, picon_manager, **kwargs):
        super().__init__(**kwargs)
        self.video_backend = video_backend
        self.picon_manager = picon_manager
        self.bouquets, self.bouquet_idx = [], 0
        self.items, self.selected_idx, self.top_idx = [], 0, 0
        self.epg_now, self.epg_next, self.epg_service_cache = {}, {}, {}
        self._epg_b_req = self._epg_s_req = 0
        self._detail_ev = None
        self._pig_auto_ev = None
        self._toast_until = 0.0
        self.active_modal = None
        self.playing_item = None

        root = BoxLayout(orientation="horizontal", padding=dp(14), spacing=dp(12))

        left_col = BoxLayout(orientation="vertical", size_hint_x=0.37, spacing=dp(6))
        left_box = EnigmaFrame(bg=C_PANEL, border=C_BORDER, radius=10, orientation="vertical",
                               padding=(dp(12), dp(8)), spacing=dp(5))

        self.bouquet_lbl = make_label("Enigma2 TV", 18, C_CYAN, halign="center", size_hint_y=None, height=dp(24))
        self.sel_ch_lbl = make_label("Łączenie z dekoderem...", 16.5, C_WHITE, halign="center", bold=True, size_hint_y=None, height=dp(24))
        left_box.add_widget(self.bouquet_lbl)
        left_box.add_widget(self.sel_ch_lbl)

        self.pig_frame = EnigmaFrame(bg=(0.01, 0.015, 0.025, 1), border=C_BORDER, radius=6,
                                     padding=dp(3), size_hint_y=0.40)
        self.pig_image = Image(allow_stretch=True, keep_ratio=True, opacity=0)
        self.pig_frame.add_widget(self.pig_image)
        self.pig_frame.bind(pos=self._sync_pig, size=self._sync_pig, on_touch_down=self._on_pig_touch)
        left_box.add_widget(self.pig_frame)

        self.epg_title_lbl = make_label("", 14.5, C_CYAN, halign="center", size_hint_y=None, height=dp(24))
        self.epg_desc_lbl = make_label("", 12.5, C_WHITE, valign="top", shorten=False, size_hint_y=0.26)
        left_box.add_widget(self.epg_title_lbl)
        left_box.add_widget(self.epg_desc_lbl)

        time_row = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(20), spacing=dp(8))
        self.epg_start_lbl = make_label("", 13.5, C_WHITE, bold=True, size_hint_x=None, width=dp(48), shorten=False)
        bar_holder = BoxLayout(padding=(0, dp(6)))
        self.main_progress = GradientProgressBar(framed=False)
        bar_holder.add_widget(self.main_progress)
        self.epg_end_lbl = make_label("", 13.5, C_WHITE, halign="right", bold=True, size_hint_x=None, width=dp(48), shorten=False)
        time_row.add_widget(self.epg_start_lbl)
        time_row.add_widget(bar_holder)
        time_row.add_widget(self.epg_end_lbl)
        left_box.add_widget(time_row)

        up_box = BoxLayout(orientation="vertical", size_hint_y=0.34, spacing=dp(1))
        self.upcoming_lbls = [make_label("", 13, markup=True) for _ in range(4)]
        for lbl in self.upcoming_lbls:
            up_box.add_widget(lbl)
        left_box.add_widget(up_box)
        left_col.add_widget(left_box)

        lbtns = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(10), padding=(dp(10), 0))
        for txt, fn in (
            ("EPG", lambda: App.get_running_app().open_epg_from("main")),
            ("MENU", self.open_settings),
            ("EXIT", lambda: App.get_running_app().exit_application()),
        ):
            b = PillButton(text=txt, accent=C_CYAN, font_size=13.5)
            b.bind(on_release=lambda *a, f=fn: f())
            lbtns.add_widget(b)
        left_col.add_widget(lbtns)
        root.add_widget(left_col)

        right_col = BoxLayout(orientation="vertical", size_hint_x=0.63, spacing=dp(5))
        top_bar = BoxLayout(orientation="horizontal", size_hint_y=None, height=dp(60), spacing=dp(8))

        badge1 = EnigmaFrame(bg=(0.03, 0.035, 0.05, 1), border=C_BORDER, radius=9,
                             padding=(dp(6), dp(4)), size_hint_x=None, width=dp(128))
        self.badge_lbl = make_label("TV HD\nStreaming", 12.5, C_WHITE, halign="center", bold=True, shorten=False)
        badge1.add_widget(self.badge_lbl)
        top_bar.add_widget(badge1)

        badge2 = PillButton(text="Bukiety\n& Menu", accent=(0, 0, 0, 0), font_size=12.5, size_hint_x=None, width=dp(100))
        badge2.bind(on_release=lambda *a: self.open_bouquet_modal())
        top_bar.add_widget(badge2)

        picon_box = EnigmaFrame(bg=(0.02, 0.025, 0.035, 1), border=C_BORDER, radius=9,
                                padding=dp(5), size_hint_x=None, width=dp(110))
        self.header_picon = Image(allow_stretch=True, keep_ratio=True, opacity=0)
        picon_box.add_widget(self.header_picon)
        top_bar.add_widget(picon_box)
        top_bar.add_widget(Widget())

        clk_box = BoxLayout(orientation="vertical", size_hint_x=None, width=dp(190))
        self.clock_lbl = make_label("00:00:00", 18, C_CYAN, halign="right", valign="bottom", shorten=False)
        self.day_lbl = make_label("", 13, C_CYAN, halign="right", shorten=False)
        self.date_lbl = make_label("", 13.5, C_CYAN, halign="right", valign="top", shorten=False)
        clk_box.add_widget(self.clock_lbl)
        clk_box.add_widget(self.day_lbl)
        clk_box.add_widget(self.date_lbl)
        top_bar.add_widget(clk_box)
        right_col.add_widget(top_bar)

        self.subinfo_lbl = make_label("", 12, C_MUTED, halign="center", size_hint_y=None, height=dp(18))
        right_col.add_widget(self.subinfo_lbl)

        self.list_wrap = EnigmaFrame(bg=(0.018, 0.022, 0.032, 1), border=C_BORDER_SUB, radius=8,
                                     orientation="horizontal", padding=(dp(4), dp(4)), spacing=dp(4))
        rows_box = BoxLayout(orientation="vertical", spacing=dp(1))
        self.row_widgets = [ChannelRowWidget(i, self._on_slot_clicked) for i in range(self.VISIBLE_ROWS)]
        for rw in self.row_widgets:
            rows_box.add_widget(rw)
        self.list_wrap.add_widget(rows_box)

        self.scrollbar = Widget(size_hint_x=None, width=dp(5))
        with self.scrollbar.canvas:
            Color(0.14, 0.17, 0.22, 0.7)
            self._sb_track = Rectangle(pos=self.scrollbar.pos, size=self.scrollbar.size)
            Color(0.50, 0.56, 0.64, 0.9)
            self._sb_thumb = RoundedRectangle(pos=self.scrollbar.pos, size=(dp(5), dp(40)), radius=[dp(2.5)])
        self.scrollbar.bind(pos=self._update_scrollbar, size=self._update_scrollbar)
        self.list_wrap.add_widget(self.scrollbar)
        self.list_wrap.bind(on_touch_down=self._on_list_scroll)
        right_col.add_widget(self.list_wrap)

        bbar = BoxLayout(size_hint_y=None, height=dp(34), spacing=dp(12), padding=(dp(8), 0))
        for txt, col, fn in (
            ("◄ Bukiety/Menu", (0.88, 0.24, 0.24, 1), self.open_bouquet_modal),
            ("Odśwież", (0.24, 0.78, 0.40, 1), self.load_bouquets),
            ("Podgląd PIP", (0.92, 0.78, 0.22, 1), self.preview_selected_in_pig),
            ("Ustawienia IP", C_CYAN, self.open_settings),
        ):
            btn = PillButton(text=txt, accent=col, underline=True, font_size=13)
            btn.bind(on_release=lambda *a, f=fn: f())
            bbar.add_widget(btn)
        right_col.add_widget(bbar)

        root.add_widget(right_col)
        self.add_widget(root)

        Clock.schedule_interval(self._update_clock, 1.0)
        Clock.schedule_interval(self._poll_vlc_status, 0.7)
        self._update_clock(0)

    def _get_pig_mode(self):
        app = App.get_running_app()
        return app.config_data.get("pig_mode", "follow") if app else "follow"

    def on_enter(self):
        Clock.schedule_once(self._on_enter_resume_pig, 0.08)

    def _on_enter_resume_pig(self, dt):
        app = App.get_running_app()
        if not app or not app.root or app.root.current != "main":
            return
        if self.video_backend.is_playing:
            self._sync_pig()
        elif platform == "android" and self.items:
            if self._get_pig_mode() == "enigma2":
                target = self.playing_item or self._find_last_or_first_channel()
                if target:
                    self._start_pig_for_item(target, save_last=False)
            else:
                it = self.get_selected_channel()
                if it:
                    self._start_pig_for_item(it, save_last=False)

    def _sync_pig(self, *args):
        app = App.get_running_app()
        if app and app.root and app.root.current == "main":
            self.video_backend.sync_pig_widget(self.pig_frame)

    def _update_clock(self, dt):
        now = datetime.datetime.now()
        self.clock_lbl.text = now.strftime("%H:%M:%S")
        self.day_lbl.text = PL_DAYS[now.weekday()]
        self.date_lbl.text = f"{now.day} {PL_MONTHS[now.month]} {now.year}"

    def open_settings(self):
        App.get_running_app().root.current = "settings"

    def toggle_pig_mode_quick(self):
        app = App.get_running_app()
        cur = self._get_pig_mode()
        nxt = "enigma2" if cur == "follow" else "follow"
        app.update_config_fields(pig_mode=nxt)
        self.subinfo_lbl.text = f"Tryb PIP zmieniony na: {pig_mode_label(nxt)}"
        if nxt == "enigma2":
            if self._pig_auto_ev:
                self._pig_auto_ev.cancel()
            if platform == "android" and not self.video_backend.is_playing:
                target = self.playing_item or self._find_last_or_first_channel()
                if target:
                    self._start_pig_for_item(target, save_last=False)
        else:
            it = self.get_selected_channel()
            if platform == "android" and it:
                self._start_pig_for_item(it, save_last=False)

    def open_bouquet_modal(self):
        if self.active_modal:
            return
        m = BouquetModal(
            bouquets=self.bouquets,
            current_idx=self.bouquet_idx,
            current_pig_mode=self._get_pig_mode(),
            on_select=self.select_bouquet,
            on_pig=self.preview_selected_in_pig,
            on_toggle_pig_mode=self.toggle_pig_mode_quick,
            on_epg=lambda: App.get_running_app().open_epg_from("main"),
            on_refresh=self.load_bouquets,
            on_settings=self.open_settings,
            on_exit=lambda: App.get_running_app().exit_application(),
        )
        m.bind(on_dismiss=lambda *a: setattr(self, "active_modal", None))
        self.active_modal = m
        m.open()

    def load_bouquets(self):
        cfg = App.get_running_app().config_data
        ip, wp, pdir = cfg.get("ip", ""), cfg.get("web_port", "80"), cfg.get("picon_dir", DEFAULT_PICON_DIR)
        self.picon_manager.configure(ip, wp, pdir)
        self.sel_ch_lbl.text = f"Łączenie z {ip}..."
        self.subinfo_lbl.text = f"OpenWebif: {ip}:{wp}  |  Pikony: {pdir}"
        threading.Thread(target=self._fetch_bouquets_worker, args=(ip, wp), daemon=True).start()

    def _fetch_bouquets_worker(self, ip, wp):
        try:
            req = urllib.request.Request(f"http://{ip}:{wp}/api/getallservices", headers={"User-Agent": "EnigmaTV-Python"})
            with urllib.request.urlopen(req, timeout=8) as r:
                services = json.loads(r.read().decode("utf-8")).get("services", [])
            Clock.schedule_once(lambda dt: self._on_bouquets_loaded(services, ip))
        except Exception as e:
            Clock.schedule_once(lambda dt, m=f"Błąd połączenia z {ip}: {e}": self._on_bouquets_error(m))

    def _on_bouquets_error(self, msg):
        self.sel_ch_lbl.text = "Brak połączenia"
        self.epg_title_lbl.text = "Wciśnij LEWO / WSTECZ -> Ustawienia IP"
        self.epg_desc_lbl.text = msg
        self.items = []
        self._render_viewport()

    def _on_bouquets_loaded(self, services, ip):
        self.bouquets = services
        self.badge_lbl.text = f"TV HD\n{ip}"
        if self.bouquets:
            cfg = App.get_running_app().config_data
            saved_bidx = cfg.get("last_bouquet_idx", self.bouquet_idx)
            try:
                saved_bidx = int(saved_bidx)
            except Exception:
                saved_bidx = 0
            if not (0 <= saved_bidx < len(self.bouquets)):
                saved_bidx = 0
            self.select_bouquet(saved_bidx, is_initial=True)
        else:
            self.sel_ch_lbl.text = "Brak bukietów"

    def _find_last_or_first_channel(self):
        cfg = App.get_running_app().config_data
        last_sref = cfg.get("last_sref", "")
        if last_sref:
            for it in self.items:
                if it["type"] == "channel" and it["sref"] == last_sref:
                    return it
        for it in self.items:
            if it["type"] == "channel":
                return it
        return None

    def select_bouquet(self, idx, is_initial=False):
        if not self.bouquets or not (0 <= idx < len(self.bouquets)):
            return
        self.bouquet_idx = idx
        b = self.bouquets[idx]
        b_name, b_ref = b.get("servicename", "Bukiet"), b.get("servicereference", "")
        self.bouquet_lbl.text = b_name

        built, ch_num = [], 1
        for sub in b.get("subservices", []):
            sref, sname = sub.get("servicereference", ""), sub.get("servicename", "")
            if sref.startswith("1:64:"):
                built.append({"type": "marker", "name": sname})
            else:
                built.append({"type": "channel", "num": ch_num, "name": sname, "sref": sref})
                ch_num += 1

        self.items, self.epg_now, self.epg_next, self.top_idx = built, {}, {}, 0
        self.selected_idx = self._find_next_channel(0, 1)

        # Przy starcie aplikacji w trybie Enigma2 ustaw kursor na ostatnio oglądanym kanale (jeśli jest na liście)
        if is_initial:
            last_sref = App.get_running_app().config_data.get("last_sref", "")
            if last_sref:
                for i, it in enumerate(self.items):
                    if it["type"] == "channel" and it["sref"] == last_sref:
                        self.selected_idx = i
                        break

        mode_tag = "PIP: Enigma2" if self._get_pig_mode() == "enigma2" else "PIP: Auto"
        self.subinfo_lbl.text = f"{b_name}  •  {mode_tag}  |  [OK] Pełny ekran  |  [◄] Menu & Bukiety"
        self._ensure_visible()
        self._render_viewport()
        self._on_selection_changed()
        self._fetch_epg_bouquet(b_ref)

        # W trybie Enigma2 uruchom w PIP ostatni oglądany (lub pierwszy na liście) jeśli nic jeszcze nie gra
        if platform == "android" and self._get_pig_mode() == "enigma2" and not self.video_backend.is_playing:
            target = self._find_last_or_first_channel()
            if target:
                Clock.schedule_once(lambda dt, t=target: self._start_pig_for_item(t, save_last=False), 0.35)

    def _find_next_channel(self, start, direction=1):
        if not self.items:
            return 0
        for d in (direction, -direction):
            i = max(0, min(start, len(self.items) - 1))
            while 0 <= i < len(self.items):
                if self.items[i]["type"] == "channel":
                    return i
                i += d
        return 0

    def _ensure_visible(self):
        if not self.items:
            self.top_idx = 0
            return
        if self.selected_idx < self.top_idx:
            self.top_idx = self.selected_idx
        elif self.selected_idx >= self.top_idx + self.VISIBLE_ROWS:
            self.top_idx = self.selected_idx - self.VISIBLE_ROWS + 1
        self.top_idx = max(0, min(self.top_idx, max(0, len(self.items) - self.VISIBLE_ROWS)))

    def _render_viewport(self):
        live_sref = self.video_backend.current_sref if self.video_backend.is_playing else ""
        for slot in range(self.VISIBLE_ROWS):
            idx = self.top_idx + slot
            rw = self.row_widgets[slot]
            if idx < len(self.items):
                it = self.items[idx]
                if it["type"] == "marker":
                    rw.bind_marker(it["name"])
                else:
                    rw.bind_channel(it, idx == self.selected_idx, self.epg_now.get(it["sref"]),
                                    self.picon_manager, is_live=(it["sref"] == live_sref))
            else:
                rw.bind_empty()
        self._update_scrollbar()

    def _update_scrollbar(self, *args):
        self._sb_track.pos, self._sb_track.size = self.scrollbar.pos, self.scrollbar.size
        total = len(self.items)
        if total <= self.VISIBLE_ROWS:
            self._sb_thumb.pos, self._sb_thumb.size = self.scrollbar.pos, self.scrollbar.size
            return
        th = max(dp(24), self.scrollbar.height * (self.VISIBLE_ROWS / total))
        frac = self.top_idx / max(1, total - self.VISIBLE_ROWS)
        ty = self.scrollbar.top - th - frac * (self.scrollbar.height - th)
        self._sb_thumb.pos, self._sb_thumb.size = (self.scrollbar.x, ty), (self.scrollbar.width, th)

    def _on_list_scroll(self, widget, touch):
        if not self.list_wrap.collide_point(*touch.pos) or not self.items or not touch.is_mouse_scrolling:
            return False
        step = -3 if touch.button == "scrollup" else 3 if touch.button == "scrolldown" else 0
        if step:
            self.top_idx = max(0, min(self.top_idx + step, max(0, len(self.items) - self.VISIBLE_ROWS)))
            self._render_viewport()
            return True
        return False

    def _on_slot_clicked(self, slot):
        idx = self.top_idx + slot
        if idx >= len(self.items) or self.items[idx]["type"] != "channel":
            return
        if idx == self.selected_idx:
            self.play_selected_fullscreen()
        else:
            self.selected_idx = idx
            self._render_viewport()
            self._on_selection_changed()

    def _on_pig_touch(self, widget, touch):
        if widget.collide_point(*touch.pos) and touch.button == "left":
            if self._get_pig_mode() == "enigma2" and self.playing_item:
                self.play_channel_fullscreen(self.playing_item)
            else:
                self.play_selected_fullscreen()
            return True
        return False

    def move_selection(self, delta):
        if not self.items:
            return
        direction = 1 if delta > 0 else -1
        target = self.selected_idx + delta
        if target < 0:
            target, direction = len(self.items) - 1, -1
        elif target >= len(self.items):
            target, direction = 0, 1
        ni = self._find_next_channel(target, direction)
        if ni != self.selected_idx:
            self.selected_idx = ni
            self._ensure_visible()
            self._render_viewport()
            self._on_selection_changed()

    def get_selected_channel(self):
        if not self.items or self.selected_idx >= len(self.items):
            return None
        it = self.items[self.selected_idx]
        return it if it["type"] == "channel" else None

    def _on_selection_changed(self):
        it = self.get_selected_channel()
        if not it:
            return
        sref, name = it["sref"], it["name"]
        self.sel_ch_lbl.text = name
        self.header_picon.opacity = 0
        self.picon_manager.get_picon(sref, name, self._on_header_picon)
        self._render_left_epg(self.epg_now.get(sref), self.epg_service_cache.get(sref, []))

        if self._detail_ev:
            self._detail_ev.cancel()
        self._detail_ev = Clock.schedule_once(lambda dt: self._fetch_epg_service(sref), 0.22)

        if platform == "android":
            if self._pig_auto_ev:
                self._pig_auto_ev.cancel()
            if self._get_pig_mode() == "follow":
                self._pig_auto_ev = Clock.schedule_once(lambda dt, sr=sref: self._auto_start_pig(sr), 1.0)

    def _on_header_picon(self, sref, tex):
        it = self.get_selected_channel()
        if not it or it.get("sref") != sref:
            return
        if tex:
            self.header_picon.texture, self.header_picon.opacity = tex, 1
            if not (self.video_backend.is_embedded and self.video_backend.is_playing):
                self.pig_image.texture, self.pig_image.opacity = tex, 0.85

    def _auto_start_pig(self, expected_sref):
        app = App.get_running_app()
        if not app or not app.root or app.root.current != "main":
            return
        if self._get_pig_mode() != "follow":
            return
        it = self.get_selected_channel()
        if it and it.get("sref") == expected_sref:
            self._start_pig_for_item(it, save_last=False)

    def show_exit_hint(self):
        self._toast_until = time.monotonic() + 2.0
        self.subinfo_lbl.color = (1.0, 0.80, 0.30, 1)
        self.subinfo_lbl.text = "Naciśnij WSTECZ ponownie, aby zamknąć aplikację"
        Clock.schedule_once(self._restore_subinfo_color, 2.05)

    def _restore_subinfo_color(self, dt):
        if time.monotonic() >= self._toast_until - 0.05:
            self.subinfo_lbl.color = C_MUTED

    def _poll_vlc_status(self, dt):
        if time.monotonic() < self._toast_until:
            return
        st = self.video_backend.get_status()
        if not st:
            return
        app = App.get_running_app()
        if app and app.root and app.root.current == "main" and self.video_backend.is_playing:
            ch_name = self.playing_item.get("name", "") if self.playing_item else ""
            if not ch_name:
                it = self.get_selected_channel()
                ch_name = it.get("name", "") if it else ""
            mode_tag = "PIP:E2" if self._get_pig_mode() == "enigma2" else "PIP:Auto"
            self.subinfo_lbl.color = C_MUTED
            self.subinfo_lbl.text = f"{st} • {ch_name} ({mode_tag})  |  [OK] Pełny ekran  |  [◄] Menu"

    def _stream_url_for(self, sref):
        parts = sref.split(":", 10)
        if len(parts) >= 11:
            embedded = parts[10].split(":")[0]
            dec = urllib.parse.unquote(embedded)
            if dec.startswith(("http://", "https://", "rtsp://", "rtmp://")):
                return dec
        cfg = App.get_running_app().config_data
        return f"http://{cfg.get('ip')}:{cfg.get('stream_port', '8001')}/{sref}"

    def _is_iptv_sref(self, sref):
        parts = sref.split(":", 10)
        if len(parts) >= 11:
            dec = urllib.parse.unquote(parts[10].split(":")[0])
            return dec.startswith(("http://", "https://", "rtsp://", "rtmp://"))
        return False

    def _start_pig_for_item(self, item, save_last=True):
        if not item:
            return
        self.playing_item = item
        if save_last:
            App.get_running_app().update_config_fields(last_sref=item["sref"], last_bouquet_idx=self.bouquet_idx)
        ok, msg = self.video_backend.play_pig(
            self._stream_url_for(item["sref"]), item["sref"], item["name"], self.pig_frame
        )
        self.subinfo_lbl.text = msg
        self._render_viewport()

    def preview_selected_in_pig(self):
        it = self.get_selected_channel()
        if it:
            self._start_pig_for_item(it, save_last=True)

    def _build_infobar_data(self, item):
        sref, name = item["sref"], item["name"]
        num = item.get("num", "")
        ch_title = f"{num}. {name}" if num else name

        now_ev = self.epg_now.get(sref)
        next_ev = self.epg_next.get(sref)
        svc_evs = self.epg_service_cache.get(sref, [])

        if not now_ev and svc_evs:
            now_ev = svc_evs[0]
        if not next_ev and svc_evs:
            cb = (now_ev.get("begin_timestamp") or 0) if now_ev else 0
            for e in svc_evs:
                if (e.get("begin_timestamp") or 0) > cb:
                    next_ev = e
                    break

        now_time = now_title = ""
        now_begin = now_dur = 0
        if now_ev and now_ev.get("title"):
            now_title = now_ev.get("title", "")
            now_begin = int(now_ev.get("begin_timestamp") or 0)
            now_dur = int(now_ev.get("duration_sec") or 0)
            if now_begin > 0 and now_dur > 0:
                now_time = f"{fmt_epg_time(now_begin)} - {fmt_epg_time(now_begin + now_dur)}"

        next_time = next_title = next_dur_text = ""
        if next_ev and next_ev.get("title"):
            next_title = next_ev.get("title", "")
            nb = int(next_ev.get("begin_timestamp") or 0)
            nd = int(next_ev.get("duration_sec") or 0)
            if nb > 0 and nd > 0:
                next_time = f"{fmt_epg_time(nb)} - {fmt_epg_time(nb + nd)}"
                next_dur_text = f"{int(nd // 60)} min"
            elif nb > 0:
                next_time = fmt_epg_time(nb)

        picon_path = self.picon_manager.get_cached_path(sref, name)
        if not picon_path:
            self.picon_manager.get_picon(sref, name, self._on_fullscreen_picon_ready)

        return {
            "ch_title": ch_title,
            "now_time": now_time,
            "now_title": now_title,
            "now_begin": now_begin,
            "now_dur": now_dur,
            "next_time": next_time,
            "next_title": next_title,
            "next_dur_text": next_dur_text,
            "picon_path": picon_path,
            "source_tag": "IPTV" if self._is_iptv_sref(sref) else "ENIGMA2",
        }

    def _on_fullscreen_picon_ready(self, sref, tex):
        app = App.get_running_app()
        if app and app.root and app.root.current == "player" and self.video_backend.current_sref == sref:
            path = self.picon_manager.get_cached_path(sref)
            if path:
                self.video_backend.update_infobar_picon(path)

    def play_selected_fullscreen(self):
        it = self.get_selected_channel()
        if it:
            self.play_channel_fullscreen(it)

    def play_channel_fullscreen(self, item):
        sref, name = item["sref"], item["name"]
        self.playing_item = item
        App.get_running_app().update_config_fields(last_sref=sref, last_bouquet_idx=self.bouquet_idx)

        for i, x in enumerate(self.items):
            if x.get("sref") == sref:
                self.selected_idx = i
                self._ensure_visible()
                break

        ib_data = self._build_infobar_data(item)
        embedded_fs, msg = self.video_backend.play_fullscreen(
            self._stream_url_for(sref), sref, name, infobar_data=ib_data
        )
        self.subinfo_lbl.text = msg
        self._render_viewport()
        if embedded_fs and self.video_backend.is_embedded:
            App.get_running_app().root.current = "player"

    def zap_in_fullscreen(self, delta):
        self.move_selection(delta)
        it = self.get_selected_channel()
        if it:
            self.play_channel_fullscreen(it)

    def _fetch_epg_bouquet(self, b_ref):
        if not b_ref:
            return
        self._epg_b_req += 1
        rid = self._epg_b_req
        cfg = App.get_running_app().config_data
        bref_q = urllib.parse.quote(b_ref, safe="")
        url_now = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgnow?bRef={bref_q}"
        url_next = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgnext?bRef={bref_q}"

        def worker():
            evs_now, evs_next = [], []
            for url, out in ((url_now, evs_now), (url_next, evs_next)):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        out.extend(json.loads(r.read().decode("utf-8")).get("events", []))
                except Exception:
                    pass
            Clock.schedule_once(lambda dt: self._apply_epg_bouquet(rid, evs_now, evs_next))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_epg_bouquet(self, rid, events_now, events_next):
        if rid != self._epg_b_req:
            return
        now_ts = time.time()
        for ev in events_now:
            sref = ev.get("sref", "")
            if not sref:
                continue
            b, d = ev.get("begin_timestamp") or 0, ev.get("duration_sec") or 0
            ev["_progress"] = max(0.0, min(1.0, (now_ts - b) / d)) if b > 0 and d > 0 else 0.0
            self.epg_now[sref] = ev
        for ev in events_next:
            sref = ev.get("sref", "")
            if sref:
                self.epg_next[sref] = ev
        self._render_viewport()
        it = self.get_selected_channel()
        if it:
            self._render_left_epg(self.epg_now.get(it["sref"]), self.epg_service_cache.get(it["sref"], []))

    def _fetch_epg_service(self, sref):
        if not sref:
            return
        if sref in self.epg_service_cache:
            return self._render_left_epg(self.epg_now.get(sref), self.epg_service_cache[sref])
        self._epg_s_req += 1
        rid = self._epg_s_req
        cfg = App.get_running_app().config_data
        url = f"http://{cfg.get('ip')}:{cfg.get('web_port', '80')}/api/epgservice?sRef={urllib.parse.quote(sref, safe='')}"

        def worker():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "EnigmaTV-Python"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    evs = json.loads(r.read().decode("utf-8")).get("events", [])
                Clock.schedule_once(lambda dt: self._apply_epg_service(rid, sref, evs))
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _apply_epg_service(self, rid, sref, events):
        self.epg_service_cache[sref] = events[:10]
        it = self.get_selected_channel()
        if rid == self._epg_s_req and it and it["sref"] == sref:
            self._render_left_epg(self.epg_now.get(sref), events[:10])

    def _render_left_epg(self, now_ev, service_events):
        now_ts = time.time()
        cur = now_ev or (service_events[0] if service_events else None)
        upcoming = []
        if cur and service_events:
            cb = cur.get("begin_timestamp") or 0
            upcoming = [e for e in service_events if (e.get("begin_timestamp") or 0) > cb][:len(self.upcoming_lbls)]

        if cur and cur.get("title"):
            self.epg_title_lbl.text = cur.get("title", "")
            desc = (cur.get("longdesc") or cur.get("shortdesc") or "").strip().replace("\n", " ")
            self.epg_desc_lbl.text = desc or "Brak szczegółowego opisu audycji."
            b, d = cur.get("begin_timestamp") or 0, cur.get("duration_sec") or 0
            if b > 0 and d > 0:
                self.epg_start_lbl.text, self.epg_end_lbl.text = fmt_epg_time(b), fmt_epg_time(b + d)
                self.main_progress.set_progress(max(0.0, min(1.0, (now_ts - b) / d)), visible=True)
            else:
                self.epg_start_lbl.text = self.epg_end_lbl.text = ""
                self.main_progress.set_progress(0.0, visible=False)
        else:
            self.epg_title_lbl.text = "Brak danych EPG"
            self.epg_desc_lbl.text = "Wciśnij OK dla pełnego ekranu lub LEWO dla menu (PIP / EPG / Bukiety)."
            self.epg_start_lbl.text = self.epg_end_lbl.text = ""
            self.main_progress.set_progress(0.0, visible=False)

        for i, lbl in enumerate(self.upcoming_lbls):
            if i < len(upcoming):
                ev = upcoming[i]
                lbl.text = f"[color=#D5DCE4]{fmt_epg_time(ev.get('begin_timestamp'))}[/color]  [color=#48BF84]{escape_markup(ev.get('title', ''))}[/color]"
            else:
                lbl.text = ""

    def handle_dpad(self, key):
        if self.active_modal:
            return self.active_modal.handle_key(key)
        if key == 273:
            self.move_selection(-1)
            return True
        if key == 274:
            self.move_selection(1)
            return True
        if key in (275, 281):
            self.move_selection(self.VISIBLE_ROWS)
            return True
        if key == 280:
            self.move_selection(-self.VISIBLE_ROWS)
            return True
        if key == 276:
            self.open_bouquet_modal()
            return True
        if key == 27:
            self.show_exit_hint()
            return True
        if key in (13, 271, 23):
            self.play_selected_fullscreen()
            return True
        if key == ord("y"):
            self.preview_selected_in_pig()
            return True
        if key == ord("e"):
            App.get_running_app().open_epg_from("main")
            return True
        if key == ord("m"):
            self.open_settings()
            return True
        return False


class EnigmaTVApp(App):
    def build(self):
        self.title = "Enigma2 TV"
        Window.clearcolor = C_BLACK
        self.config_path = os.path.join(self.user_data_dir, "enigma_config.json")
        self.config_data = self.load_enigma_config()
        self.picon_manager = PiconManager(os.path.join(self.user_data_dir, "picons_cache"))
        self.video_backend = AndroidLibVLCBackend() if platform == "android" else DesktopVLCBackend()

        sm = ScreenManager(transition=FadeTransition(duration=0.14))
        self.settings_screen = SettingsScreen(self.video_backend, name="settings")
        self.main_screen = EnigmaChannelSelectionScreen(self.video_backend, self.picon_manager, name="main")
        self.player_screen = FullscreenPlayerScreen(self.video_backend, name="player")
        self.epg_screen = EPGScreen(self.video_backend, self.picon_manager, name="epg")

        for sc in (self.settings_screen, self.main_screen, self.player_screen, self.epg_screen):
            sm.add_widget(sc)

        if self.config_data.get("ip"):
            sm.current = "main"
            Clock.schedule_once(lambda dt: self.main_screen.load_bouquets(), 0.15)
        else:
            sm.current = "settings"

        self._last_back_ts = 0.0
        Window.bind(on_keyboard=self.on_keyboard)
        self._android_activity_cls = None
        if platform == "android":
            try:
                from jnius import autoclass
                self._android_activity_cls = autoclass("org.kivy.android.PythonActivity")
                Clock.schedule_interval(self._poll_tv_keys, 1.0 / 30.0)
            except Exception:
                pass
        return sm

    def open_epg_from(self, source_screen):
        ch = self.main_screen.playing_item if (source_screen == "player" and self.main_screen.playing_item) else self.main_screen.get_selected_channel()
        if not ch:
            return
        b_ref = ""
        if self.main_screen.bouquets:
            b_ref = self.main_screen.bouquets[self.main_screen.bouquet_idx].get("servicereference", "")
        self.epg_screen.open_for_channel(
            channel_item=ch,
            bouquet_items=self.main_screen.items,
            bouquet_ref=b_ref,
            return_screen=source_screen,
            start_mode="single",
        )
        self.root.current = "epg"

    def on_stop(self):
        self.video_backend.stop()
        self.picon_manager.shutdown()

    def _poll_tv_keys(self, dt):
        if not self._android_activity_cls:
            return
        while True:
            kc = self._android_activity_cls.pollTvKey()
            if kc < 0:
                break
            self._dispatch_android_keycode(kc)

    def _dispatch_android_keycode(self, kc):
        codepoint = ""
        nkey = None
        if kc == 19:
            nkey = 273
        elif kc == 20:
            nkey = 274
        elif kc == 21:
            nkey = 276
        elif kc == 22:
            nkey = 275
        elif kc in (23, 66, 160):
            nkey = 13
        elif kc == 4:
            nkey = 27
        elif kc == 82:
            nkey = ord("m")
        elif kc == 166:
            nkey = 280
        elif kc == 167:
            nkey = 281
        elif kc == 172:
            nkey = ord("e")
        elif kc == 165:
            nkey = ord("1") if self.root.current == "epg" else ord("e")
        elif kc == 183:
            nkey = ord("1")
        elif kc == 184:
            nkey = ord("2")
        elif kc == 185:
            nkey = ord("3") if self.root.current == "epg" else ord("y")
        elif kc == 186:
            nkey = ord("4")
        elif kc == 222:
            nkey = ord("a")
        elif kc == 175:
            nkey = ord("s")
        elif kc == 67:
            nkey = 8
        elif 7 <= kc <= 16:
            nkey = 48 + (kc - 7)
            codepoint = str(kc - 7)

        if nkey is not None:
            handled = self._route_key(nkey, codepoint)
            if not handled and nkey == 27 and platform == "android":
                self.stop()

    def exit_application(self):
        try:
            self.picon_manager.shutdown()
        except Exception:
            pass
        if platform == "android" and hasattr(self.video_backend, "finish_app"):
            self.video_backend.finish_app()
        else:
            self.video_backend.stop()
            self.stop()

    def _route_key(self, nkey, codepoint=""):
        if nkey == 27:
            now = time.monotonic()
            if now - self._last_back_ts <= 1.8:
                self.exit_application()
                return True
            self._last_back_ts = now
        else:
            self._last_back_ts = 0.0

        cur = self.root.current
        if cur == "settings":
            return self.settings_screen.handle_dpad(nkey, codepoint)
        if cur == "main":
            return self.main_screen.handle_dpad(nkey)
        if cur == "player":
            return self.player_screen.handle_dpad(nkey)
        if cur == "epg":
            return self.epg_screen.handle_dpad(nkey)
        return False

    def on_keyboard(self, window, key, scancode, codepoint, modifier):
        return self._route_key(normalize_remote_key(key, scancode), codepoint or "")

    def load_enigma_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("picon_dir") == "/usr/share/enigma/picons":
                    data["picon_dir"] = DEFAULT_PICON_DIR
                return data
            except Exception:
                pass
        return {}

    def _write_config_file(self):
        os.makedirs(self.user_data_dir, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.config_data, f)

    def update_config_fields(self, **kwargs):
        self.config_data.update(kwargs)
        self._write_config_file()

    def save_config(self, ip, web_port, stream_port, picon_dir, pig_mode=None):
        self.config_data.update({
            "ip": ip,
            "web_port": web_port,
            "stream_port": stream_port,
            "picon_dir": picon_dir,
        })
        if pig_mode is not None:
            self.config_data["pig_mode"] = pig_mode
        elif "pig_mode" not in self.config_data:
            self.config_data["pig_mode"] = "follow"
        self._write_config_file()


if __name__ == "__main__":
    EnigmaTVApp().run()
