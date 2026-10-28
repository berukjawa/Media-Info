import asyncio
import atexit
import re
import time
import unicodedata
from datetime import datetime, timezone

import psutil
import serial
from serial.tools import list_ports
import win32api
import win32gui
import win32process

from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

SERIAL_PORT = None
BAUD_RATE = 115200

HANDSHAKE_SEND = b"MEDIAINFO_HELLO\n"
HANDSHAKE_REPLY = b"MEDIAINFO_ACK"

KNOWN_VIDS = {0x10C4, 0x1A86, 0x0403, 0x303A}

RETRY_BAD_S = 30

SHOW_SOURCE = False

DEBUG = False

BROWSERS = {"brave": "Brave", "chrome": "Chrome", "msedge": "Edge"}
BROWSER_EXES = ["brave.exe", "chrome.exe", "msedge.exe"]

PLATFORM_RE = re.compile(
    r"youtube music|youtube|soundcloud|spotify|brave|google chrome|"
    r"microsoft edge|vlc media player|\bchrome\b|\bedge\b",
    re.IGNORECASE,
)

esp32 = None

_bad_ports = {}


def probe_port(device):
    ser = None
    try:
        ser = serial.Serial(device, BAUD_RATE, timeout=0.3, write_timeout=1)
        time.sleep(2)
        ser.reset_input_buffer()

        for _ in range(3):
            ser.write(HANDSHAKE_SEND)
            ser.flush()
            deadline = time.time() + 1.0
            buf = b""
            while time.time() < deadline:
                buf += ser.read(ser.in_waiting or 1)
                if HANDSHAKE_REPLY in buf:
                    ser.reset_input_buffer()
                    ser.timeout = 1
                    return ser
        ser.close()
    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] {device}: {e}")
        try:
            if ser and ser.is_open:
                ser.close()
        except Exception:
            pass
    return None


def find_esp32(preferred=None):
    ports = [p for p in list_ports.comports() if p.vid is not None]
    current = {p.device for p in ports}

    for dev in list(_bad_ports):
        if dev not in current:
            del _bad_ports[dev]

    ports.sort(key=lambda p: (p.device != preferred, p.vid not in KNOWN_VIDS, p.device))

    for p in ports:
        if time.time() - _bad_ports.get(p.device, 0) < RETRY_BAD_S:
            continue
        if DEBUG:
            print(f"[DEBUG] mencoba {p.device} ({p.description})")
        ser = probe_port(p.device)
        if ser:
            _bad_ports.pop(p.device, None)
            return ser
        _bad_ports[p.device] = time.time()
    return None


def normalize(text):
    text = "".join(c for c in (text or "") if unicodedata.category(c) != "Cf")
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def browser_window_titles():
    titles = []

    def cb(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            t = win32gui.GetWindowText(hwnd).strip()
            if t:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    if psutil.Process(pid).name().lower() in BROWSER_EXES:
                        titles.append(t)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

    win32gui.EnumWindows(cb, None)
    return titles


def detect_source(app_id):
    a = app_id.lower()
    if "spotify" in a:
        return "Spotify"
    if "vlc" in a:
        return "VLC Media Player"
    for key, name in BROWSERS.items():
        if key in a:
            titles = " ".join(browser_window_titles())
            if "YouTube Music" in titles:
                return "YouTube Music"
            if "SoundCloud" in titles:
                return "SoundCloud"
            if "YouTube" in titles:
                return "YouTube"
            return name
    return app_id.split(".")[0] or "Media"


def clean(text):
    return normalize(text).replace("|", "-")


def clean_meta(text, source):
    text = clean(text)
    if not text:
        return ""
    if text.lower() == source.lower() or PLATFORM_RE.search(text):
        return ""
    return text


def read_timeline(session):
    try:
        tl = session.get_timeline_properties()
        start = tl.start_time.total_seconds()
        pos = tl.position.total_seconds() - start
        dur = (tl.end_time - tl.start_time).total_seconds()

        last = tl.last_updated_time
        if last.year > 2000:
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            rate = session.get_playback_info().playback_rate or 1.0
            if elapsed > 0:
                pos += elapsed * rate

        dur = max(dur, 0)
        pos = max(pos, 0)
        if dur > 0:
            pos = min(pos, dur)
        return int(pos * 1000), int(dur * 1000)
    except Exception as e:
        if DEBUG:
            print(f"[DEBUG] timeline error: {e}")
        return 0, 0


async def read_media_session():
    manager = await MediaManager.request_async()
    sessions = list(manager.get_sessions())

    playing = []
    for s in sessions:
        status = s.get_playback_info().playback_status
        if status == PlaybackStatus.PLAYING:
            playing.append(s)

    for s in playing:
        props = await s.try_get_media_properties_async()
        title = clean(props.title)
        if not title:
            continue
        source = detect_source(s.source_app_user_model_id)

        if DEBUG:
            print(f"[DEBUG] artist={props.artist!r} album={props.album_title!r}")

        artist = clean_meta(props.artist, source)
        album = clean_meta(props.album_title, source)
        pos_ms, dur_ms = read_timeline(s)
        return title, album, artist, source, pos_ms, dur_ms

    return None, None, None, None, 0, 0


def get_media_info():
    try:
        return asyncio.run(read_media_session())
    except Exception as e:
        print(f"[ERROR] Media Session: {e}")
        return None, None, None, None, 0, 0


def fmt_time(ms):
    t = int(ms // 1000)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def send_disconnect():
    global esp32
    try:
        if esp32 and esp32.is_open:
            esp32.write(b"DISCONNECT\n")
            esp32.flush()
            time.sleep(0.2)
            esp32.close()
    except Exception:
        pass
    esp32 = None


def on_console_event(ctrl_type):
    send_disconnect()
    return False


def main():
    global esp32

    last_payload = ""
    last_port = None
    searching = False

    saved_title = ""
    saved_album = ""
    saved_artist = ""
    saved_source = ""
    saved_pos = 0
    saved_dur = 0

    atexit.register(send_disconnect)
    win32api.SetConsoleCtrlHandler(on_console_event, True)

    print("=== PROYEK: now_playing.py (Media Session) ===")

    while True:
        try:
            if esp32 is None or not esp32.is_open:
                if not searching:
                    print("[MENCARI] ESP32 di port USB...")
                    searching = True
                esp32 = find_esp32(last_port or SERIAL_PORT)
                if esp32 is None:
                    time.sleep(2)
                    continue
                searching = False
                last_port = esp32.port
                last_payload = ""
                print(f">> TERHUBUNG KE ESP32 ({esp32.port}) <<")

            title, album, artist, source, pos_ms, dur_ms = get_media_info()

            if title:
                saved_title = title
                saved_album = album
                saved_artist = artist
                saved_source = source
                saved_pos = pos_ms
                saved_dur = dur_ms
                is_playing = 1
            else:
                is_playing = 0

            out_source = saved_source if SHOW_SOURCE else ""

            if saved_title:
                payload = (
                    f"{saved_title}|{saved_album}|{saved_artist}|{out_source}"
                    f"|{saved_pos}|{saved_dur}|{is_playing}"
                )
            else:
                payload = f"CLEAR|||{out_source}|0|0|0"

            esp32.write(f"{payload}\n".encode('utf-8', errors='ignore'))

            key = f"{saved_title}|{saved_album}|{saved_artist}|{is_playing}"
            if key != last_payload:
                last_payload = key
                if not saved_title:
                    print("[STANDBY]")
                else:
                    by = f" | By: {saved_artist}" if saved_artist else ""
                    alb = f" | Album: {saved_album}" if saved_album else ""
                    src = f" ({saved_source})" if SHOW_SOURCE else ""
                    dur = f" | {fmt_time(saved_dur)}" if saved_dur else ""
                    state = "PLAYING" if is_playing else "PAUSED"
                    print(f"[{state}] {saved_title}{by}{alb}{dur}{src}")

            time.sleep(1.5)

        except (serial.SerialException, serial.SerialTimeoutException, OSError):
            print("[TERPUTUS] ESP32 tidak terdeteksi, mencari ulang...")
            try:
                if esp32 and esp32.is_open:
                    esp32.close()
            except Exception:
                pass
            esp32 = None
            searching = True
            time.sleep(2)

        except KeyboardInterrupt:
            print("\nProgram dihentikan.")
            send_disconnect()
            break

        except Exception:
            time.sleep(2)


if __name__ == '__main__':
    main()