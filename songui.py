#!/bin/python3.13

import curses
import subprocess
import argparse
import time
import shutil
import sys
import os
import signal
import struct
import tempfile
import threading
import hashlib
import urllib.request

# Update time! Yes honey...
BUILD_TIMESTAMP = "1752498602"

BUILD_TIMESTAMP = "1752498602"
# Global variable to remember the currently running espeak pid
ESPEAK_PID = None
# Announcement thread and event for async announcement
ANNOUNCE_THREAD = None
ANNOUNCE_EVENT = threading.Event()
ANNOUNCE_LOCK = threading.Lock()
ANNOUNCE_PENDING = {'title': None, 'artist': None, 'enabled': False, 'prev_id': None}

# Dependency free checker (is playerctl/espeak/qdbus6/figlet in $PATH? or is the user a dumb*ss?)
def check_deps(espeak_optional=False):
    # If you don't have these, this script is as useful as a chocolate teapot.
    missing = []
    for dep in ["playerctl", "qdbus6", "figlet"]:
        if shutil.which(dep) is None:
            missing.append(dep)
    if not espeak_optional and shutil.which("espeak") is None:
        missing.append("espeak")
    return missing

def run_qdbus6(args):
    # Used for querying Bluetooth device state/info. Be nice to D-Bus, it has feelings.
    try:
        return subprocess.check_output(["qdbus6", "--system"] + args, text=True)
    except Exception:
        return ""

def parse_status(output: str) -> dict:
    # Turns "Key: Value" lines into a dict. Still easier than parsing HTML.
    info = {}
    for line in output.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info

def ms_to_mins_secs(ms):
    # Converts ms to hh:mm:ss, for people who actually listen to albums.
    seconds = int(ms) // 1000
    mins = (seconds // 60) % 60
    hours = (seconds // 60) // 60
    secs = seconds % 60
    return f"{hours:02}:{mins:02}:{secs:02}"

def check_device_connected(mac_addr):
    # Checks if your Bluetooth device is connected or if it's ghosting you again.
    dev_path = f"/org/bluez/hci0/dev_{mac_to_bluez(mac_addr)}"
    try:
        output = subprocess.check_output(
            ["qdbus6", "--system", "org.bluez", dev_path, "org.freedesktop.DBus.Properties.Get", "org.bluez.Device1", "Connected"],
            text=True,
            stderr=subprocess.DEVNULL
        )
        return "true" in output.lower()
    except Exception:
        return False

def attempt_bluetooth_connect(mac_addr):
    # Attempts to connect to your Bluetooth device like a desperate AirPods user in Starbucks. (JBL FOREVER GET REKT)
    try:
        subprocess.run(['bluetoothctl', 'connect', bluez_to_mac(mac_addr)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

def figlet_centered(stdscr, text, color_pair=0):
    # Centers your figlet message, because it deserves to be the center of attention. (prob ur gf if u have on tho)
    max_y, max_x = stdscr.getmaxyx()
    try:
        figlet_out = subprocess.check_output(
            ["figlet", "-f", "slant", "-l", "-w", str(max_x), text],
            text=True,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        figlet_out = text  # If you don't have figlet, you get boring text. (LMAO imagine)
    lines = figlet_out.splitlines()
    y0 = max((max_y - len(lines)) // 2, 0)
    for i, line in enumerate(lines):
        x0 = max((max_x - len(line)) // 2, 0)
        try:
            stdscr.addstr(y0 + i, x0, line, curses.color_pair(color_pair))
        except curses.error:
            pass

def which_button(mx, my, button_boxes):
    # Returns which button (if any) your greasy finger is over. (not your mouse pointer, you filthy casual)
    for idx, (y1, x1, y2, x2) in enumerate(button_boxes):
        if y1 <= my < y2 and x1 <= mx < x2:
            return idx
    return None

def mac_to_bluez(mac):
    # MAC address but bluezified, because BlueZ can't handle colons like a normal person.
    return mac.replace(":", "_").upper()

def bluez_to_mac(bluez_mac):
    # Turns BlueZ's weird MAC back into something you can copy and paste.
    return bluez_mac.replace("_", ":").upper()

def draw_progress_bar(stdscr, y, elapsed, bar_length, prog_pos, prog_dur, remaining, color_pair, internal_audio=False, remaining_time=0, status="", anim_state=None):
    # Draws the progress bar, or a little <===> animation if the track is over and the UI wants to look alive.
    max_x = stdscr.getmaxyx()[1]
    bar = ""
    if internal_audio and remaining_time < 0:
        # Animation: <===> bar sliding back and forth
        anim_len = 5  # "<===>"
        anim_str = "<===>"
        bar_inside = bar_length - 2
        if anim_state is None or "frame" not in anim_state or "direction" not in anim_state:
            anim_state = {"frame": 0, "direction": 1}
        frame = anim_state["frame"]
        direction = anim_state["direction"]
        if frame < 0:
            frame = 0
            direction = 1
        if frame > bar_inside - anim_len:
            frame = bar_inside - anim_len
            direction = -1
        bar = "[" + " " * frame + anim_str + " " * (bar_inside - frame - anim_len) + "]"
        anim_state["frame"] = frame + direction
        anim_state["direction"] = direction
    else:
        filled_length = int(bar_length * prog_pos // prog_dur) if prog_dur else 0
        bar = "[" + "=" * filled_length + ">" + " " * (bar_length - filled_length - 1) + "]"
    line = f"{elapsed.rjust(5)} {bar} -{remaining}"
    stdscr.addstr(y, 0, line[:max_x], curses.color_pair(color_pair))

def draw_control_buttons(stdscr, labels, highlight_idx, button_boxes, color_pair, highlight_timer=None, autorefresh_interval=1.0, mouse_ignore_idx=None):
    # Draws the play/pause/next/prev buttons with instant highlight, because you need that dopamine NOW.
    max_y, max_x = stdscr.getmaxyx()
    button_boxes.clear()
    
    # get rekt (rect)
    btn_y = 9
    btn_h = 5
    btn_w = 13

    gap = 6
    num_btns = len(labels)
    total_btns = num_btns * btn_w + (num_btns - 1) * gap
    start_x = (max_x - total_btns) // 2

    now = time.time()
    highlight_active = lambda i: (
        highlight_idx is not None and highlight_timer is not None and
        now - highlight_timer < (autorefresh_interval * 0.60) and i == highlight_idx
    )
    for i, label in enumerate(labels):
        x = start_x + i * (btn_w + gap)
        y = btn_y
        box = (y, x, y + btn_h, x + btn_w)
        button_boxes.append(box)
        for by in range(y, y+btn_h):
            try:
                stdscr.addstr(by, x, " " * btn_w, curses.color_pair(color_pair) | (curses.A_REVERSE if highlight_active(i) else curses.A_NORMAL))
            except curses.error:
                pass
        for bx in range(x, x+btn_w):
            try:
                stdscr.addch(y, bx, curses.ACS_HLINE, curses.color_pair(color_pair))
                stdscr.addch(y+btn_h-1, bx, curses.ACS_HLINE, curses.color_pair(color_pair))
            except curses.error:
                pass
        for by in range(y, y+btn_h):
            try:
                stdscr.addch(by, x, curses.ACS_VLINE, curses.color_pair(color_pair))
                stdscr.addch(by, x+btn_w-1, curses.ACS_VLINE, curses.color_pair(color_pair))
            except curses.error:
                pass
        try:
            stdscr.addch(y, x, curses.ACS_ULCORNER, curses.color_pair(color_pair))
            stdscr.addch(y, x+btn_w-1, curses.ACS_URCORNER, curses.color_pair(color_pair))
            stdscr.addch(y+btn_h-1, x, curses.ACS_LLCORNER, curses.color_pair(color_pair))
            stdscr.addch(y+btn_h-1, x+btn_w-1, curses.ACS_LRCORNER, curses.color_pair(color_pair))
        except curses.error:
            pass
        try:
            stdscr.addstr(
                y + btn_h//2, x + (btn_w-len(label))//2, label,
                curses.A_BOLD | curses.color_pair(color_pair) | (curses.A_REVERSE if highlight_active(i) else 0)
            )
        except curses.error:
            pass

def fill_background(stdscr, color_pair):
    # Fill the whole screen with spaces using the specified color pair, because transparency is for cowards
    max_y, max_x = stdscr.getmaxyx()
    for y in range(max_y):
        try:
            stdscr.addstr(y, 0, " " * max_x, curses.color_pair(color_pair))
        except curses.error:
            pass

def draw_ui(stdscr, info, highlight_idx=None, button_boxes=None, color_pair=0, internal_audio=False, anim_state=None, highlight_timer=None, autorefresh_interval=1.0, bluetooth_mode=False, mouse_ignore_idx=None, visualizer_data=None, fg_color=7, bg_color=0):
    # The main UI. If you see this break, blame curses, not me.
    try:
        fill_background(stdscr, color_pair)
        max_y, max_x = stdscr.getmaxyx()
        album = info.get("Track", info.get("Album", 'Unknown Album'))
        if not album or not any(c.isprintable() and not c.isspace() for c in str(album)) or album.endswith("Album:"):
            album = 'Unknown Album'
        elif album.startswith("Album: ") or album.startswith("Track: "):
            album = album[7:]
        title = info.get("Title", "Unknown Title")
        if not title or not any(c.isprintable() and not c.isspace() for c in str(title)):
            title = "Unknown Title"
        artist = info.get("Artist", "Unknown Artist")
        if not artist or not any(c.isprintable() and not c.isspace() for c in str(artist)):
            artist = "Unknown Artist"
        status = info.get("Status", "paused").capitalize()
        try:
            position = int(info.get("Position", "0"))
        except Exception:
            position = 0
        try:
            duration = int(info.get("Duration", "1"))
        except Exception:
            duration = 1
        elapsed = ms_to_mins_secs(position)
        remaining_time = duration - position
        remaining = ms_to_mins_secs(max(remaining_time, 0))
        prog_pos = position // 1000
        prog_dur = max(duration // 1000, 1)

        stdscr.addstr(0, 0, "=" * max_x, curses.color_pair(color_pair))
        stdscr.addstr(1, 0, title.center(max_x), curses.color_pair(color_pair) | curses.A_BOLD)
        stdscr.addstr(2, 0, artist.center(max_x), curses.color_pair(color_pair))
        stdscr.addstr(3, 0, album.center(max_x), curses.color_pair(color_pair))
        stdscr.addstr(4, 0, "=" * max_x, curses.color_pair(color_pair))
        stdscr.addstr(5, 0, f"Status: {status}", curses.color_pair(color_pair))
        bar_length = max(max_x - 23, 10)
        draw_progress_bar(
            stdscr, 7, elapsed, bar_length, prog_pos, prog_dur, remaining, color_pair,
            internal_audio=internal_audio, remaining_time=remaining_time, status=status, anim_state=anim_state
        )
        labels = ["⏮", "⏯" if status.lower() != "playing" else "⏸", "⏭"]
        draw_control_buttons(
            stdscr, labels, highlight_idx, button_boxes, color_pair,
            highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
            mouse_ignore_idx=mouse_ignore_idx
        )
        btn_y = 9
        btn_h = 5
        help_line = "Controls: [p] Play/Pause  [n] Next  [b] Previous  [q] Quit"
        stdscr.addstr(btn_y+btn_h+1, 0, help_line.center(max_x), curses.color_pair(color_pair))
        stdscr.addstr(btn_y+btn_h+2, 0, "=" * max_x, curses.color_pair(color_pair))
        # Draw visualizer if present
        if visualizer_data is not None:
            # Make the visualizer fill all available space below the UI
            y_start = btn_y + btn_h + 3
            if y_start < max_y:
                draw_visualizer(stdscr, visualizer_data, y_start, color_pair, fg_color, bg_color)
        stdscr.refresh()
    except curses.error:
        stdscr.clear()
        msg = "Curses clocked out. Try resizing the window.\n(Maybe don't ratio it so hard next time.)"
        try:
            stdscr.addstr(0, 0, msg)
            stdscr.refresh()
            time.sleep(2)
        except Exception:
            pass

def no_player_screen(stdscr, color_pair, autoretry_interval=1.0):
    # Shows a "No Player" screen. Waits, then tries again, like a loyal dog.
    while True:
        fill_background(stdscr, color_pair)
        figlet_centered(stdscr, "No Player", color_pair=color_pair)
        max_y, max_x = stdscr.getmaxyx()
        msg = f"No audio player running. Auto-retry every {autoretry_interval:.1f}s, press [q] to quit.".center(max_x)
        stdscr.addstr(max_y-2, 0, msg, curses.color_pair(color_pair))
        stdscr.refresh()
        wait_start = time.time()
        while True:
            key = stdscr.getch()
            if key in [ord('q'), ord('Q')]:
                return False
            if time.time() - wait_start > autoretry_interval:
                break
            time.sleep(0.05)
        player = find_active_player()
        if player:
            return player

def device_not_found_screen(stdscr, bluez_mac, color_pair, autoretry_interval=1.0, reconnect_callback=None):
    # If your Bluetooth device is in another room, this will keep looking until you bring it back.
    shown_mac = bluez_to_mac(bluez_mac)
    while True:
        fill_background(stdscr, color_pair)
        figlet_centered(stdscr, shown_mac, color_pair=color_pair)
        max_y, max_x = stdscr.getmaxyx()
        msg = f"Device not connected. Waiting for device... (auto-retry every {autoretry_interval:.1f}s, press [q] to quit, [r] to reconnect)".center(max_x)
        stdscr.addstr(max_y-2, 0, msg, curses.color_pair(color_pair))
        stdscr.refresh()
        wait_start = time.time()
        while True:
            key = stdscr.getch()
            if key in [ord('q'), ord('Q')]:
                return False
            if key in [ord('r'), ord('R')]:
                if reconnect_callback:
                    reconnect_callback()
                break
            if time.time() - wait_start > autoretry_interval:
                break
            time.sleep(0.05)
        if reconnect_callback:
            time.sleep(0.05)
        if check_device_connected(shown_mac):
            return True

def find_active_player():
    # Looks for a running MPRIS2 player. If you have five YouTube tabs open, good luck.
    try:
        players = subprocess.check_output(["playerctl", "-l"], text=True, stderr=subprocess.DEVNULL).splitlines()
        if not players:
            return None
        for player in players:
            status = subprocess.check_output(["playerctl", "-p", player, "status"], text=True, stderr=subprocess.DEVNULL).strip()
            if status == "Playing":
                return player
        return players[0]
    except Exception:
        return None

def refresh_internal_audio_info(player):
    # Gets all the info about the track, using playerctl, which is totally not just a wrapper for dbus-send.
    info = {}
    def get(cmd):
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""
    info["Status"] = get(["playerctl", "-p", player, "status"])
    info["Title"] = get(["playerctl", "-p", player, "metadata", "title"])
    info["Artist"] = get(["playerctl", "-p", player, "metadata", "artist"])
    info["Album"] = get(["playerctl", "-p", player, "metadata", "album"])
    info["Track"] = get(["playerctl", "-p", player, "metadata", "album"])
    pos = get(["playerctl", "-p", player, "position"])
    try:
        info["Position"] = str(int(float(pos) * 1000))
    except Exception:
        info["Position"] = "0"
    # Find all keys ending with :length and use the smallest value
    try:
        metadata = subprocess.check_output(["playerctl", "-p", player, "metadata"], text=True, stderr=subprocess.DEVNULL)
        length_values = []
        for line in metadata.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2:
                key, value = parts
                if key.endswith(":length"):
                    try:
                        length_values.append(int(value))
                    except Exception:
                        pass
        if length_values:
            info["Duration"] = str(min(length_values))
        else:
            dur = get(["playerctl", "-p", player, "metadata", "mpris:length"])
            info["Duration"] = dur if dur else "1"
    except Exception:
        dur = get(["playerctl", "-p", player, "metadata", "mpris:length"])
        info["Duration"] = dur if dur else "1"
    return info

def refresh_bluetooth_info(PLAYER_PATH):
    # If you're using Bluetooth, ask BlueZ very very nicely for the info.
    status_out = run_qdbus6([
        "org.bluez",
        PLAYER_PATH,
        "org.freedesktop.DBus.Properties.GetAll",
        "org.bluez.MediaPlayer1"
    ])
    info = parse_status(status_out)
    return info

def announce_song(title, artist, prev_id, announce_enabled, skip_delay=0):
    """
    Schedule an announcement for the song, but only announce after skipping has stopped for a short period.
    If a new song is requested before the delay, the previous announcement is cancelled.
    """
    global ESPEAK_PID, ANNOUNCE_THREAD, ANNOUNCE_EVENT, ANNOUNCE_PENDING
    curr_id = (title, artist)
    if not announce_enabled or shutil.which("espeak") is None:
        return curr_id
    if curr_id == prev_id:
        return curr_id
    with ANNOUNCE_LOCK:
        ANNOUNCE_PENDING['title'] = title
        ANNOUNCE_PENDING['artist'] = artist
        ANNOUNCE_PENDING['enabled'] = announce_enabled
        ANNOUNCE_PENDING['prev_id'] = prev_id
        # Cancel any pending announcement
        ANNOUNCE_EVENT.set()
        ANNOUNCE_EVENT.clear()
        if ANNOUNCE_THREAD is None or not ANNOUNCE_THREAD.is_alive():
            ANNOUNCE_THREAD = threading.Thread(target=_announce_worker, daemon=True)
            ANNOUNCE_THREAD.start()
    return curr_id

def _announce_worker():
    global ESPEAK_PID, ANNOUNCE_EVENT, ANNOUNCE_PENDING
    while True:
        # Wait for a short period; if event is set, restart wait
        interrupted = ANNOUNCE_EVENT.wait(timeout=1.5)
        if interrupted:
            ANNOUNCE_EVENT.clear()
            continue
        with ANNOUNCE_LOCK:
            title = ANNOUNCE_PENDING['title']
            artist = ANNOUNCE_PENDING['artist']
            enabled = ANNOUNCE_PENDING['enabled']
            prev_id = ANNOUNCE_PENDING['prev_id']
            curr_id = (title, artist)
            # Only announce if enabled and not same as prev_id
            if enabled and curr_id != prev_id and shutil.which("espeak"):
                text = f"Now playing: {title} by {artist}"
                try:
                    if ESPEAK_PID is not None and int(ESPEAK_PID) > 0:
                        os.kill(int(ESPEAK_PID), signal.SIGKILL)
                except Exception:
                    pass
                try:
                    import subprocess
                    proc = subprocess.Popen(['espeak', text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    ESPEAK_PID = proc.pid
                except Exception:
                    ESPEAK_PID = None
            # After announcing, clear prev_id to prevent repeats
            ANNOUNCE_PENDING['prev_id'] = curr_id
        # Wait for next event or exit if not needed
        ANNOUNCE_EVENT.clear()
        # If no new announcement is pending, exit thread
        with ANNOUNCE_LOCK:
            if ANNOUNCE_PENDING['title'] is None:
                break

def handle_keypress_internal_audio(key, player, info, button_boxes, stdscr, color_pair, highlight_idx, highlight_timer, autorefresh_interval):
    # Handles all keypresses, including mouse clicks (for those who hate the keyboard).
    # highlight_idx and highlight_timer are passed in so we can update highlight instantly & ignore mouse on highlighted button
    if key == ord('q'):
        return info, None, True, None
    elif key in [ord('p'), ord(' ')]:
        status = info.get("Status", "paused").lower()
        if status == "playing":
            subprocess.run(["playerctl", "-p", player, "pause"])
        else:
            subprocess.run(["playerctl", "-p", player, "play"])
        return refresh_internal_audio_info(player), 1, False, time.time()
    elif key in [ord('n'), curses.KEY_RIGHT]:
        subprocess.run(["playerctl", "-p", player, "next"])
        return refresh_internal_audio_info(player), 2, False, time.time()
    elif key in [ord('b'), curses.KEY_LEFT]:
        subprocess.run(["playerctl", "-p", player, "previous"])
        return refresh_internal_audio_info(player), 0, False, time.time()
    elif key == curses.KEY_MOUSE:
        _, mx, my, _, _ = curses.getmouse()
        btn_idx = which_button(mx, my, button_boxes)
        if btn_idx is not None and highlight_idx == btn_idx:
            return info, highlight_idx, False, highlight_timer
        if btn_idx == 0:
            subprocess.run(["playerctl", "-p", player, "previous"])
        elif btn_idx == 1:
            status = info.get("Status", "paused").lower()
            if status == "playing":
                subprocess.run(["playerctl", "-p", player, "pause"])
            else:
                subprocess.run(["playerctl", "-p", player, "play"])
        elif btn_idx == 2:
            subprocess.run(["playerctl", "-p", player, "next"])
        return refresh_internal_audio_info(player), btn_idx, False, time.time()
    return refresh_internal_audio_info(player), highlight_idx, False, highlight_timer

def handle_keypress_bluetooth(key, PLAYER_PATH, info, button_boxes, highlight_idx, highlight_timer, autorefresh_interval):
    # Handles keypresses for Bluetooth mode, for when you want to feel pain.
    if key == ord('q'):
        return info, None, True, None
    elif key in [ord('p'), ord(' ')]:
        status = info.get("Status", "paused").lower()
        if status == "playing":
            run_qdbus6([
                "org.bluez",
                PLAYER_PATH,
                "org.bluez.MediaPlayer1.Pause"
            ])
        else:
            run_qdbus6([
                "org.bluez",
                PLAYER_PATH,
                "org.bluez.MediaPlayer1.Play"
            ])
        return refresh_bluetooth_info(PLAYER_PATH), 1, False, time.time()
    elif key in [ord('n'), curses.KEY_RIGHT]:
        run_qdbus6([
            "org.bluez",
            PLAYER_PATH,
            "org.bluez.MediaPlayer1.Next"
        ])
        return refresh_bluetooth_info(PLAYER_PATH), 2, False, time.time()
    elif key in [ord('b'), curses.KEY_LEFT]:
        run_qdbus6([
            "org.bluez",
            PLAYER_PATH,
            "org.bluez.MediaPlayer1.Previous"
        ])
        return refresh_bluetooth_info(PLAYER_PATH), 0, False, time.time()
    elif key == curses.KEY_MOUSE:
        _, mx, my, _, _ = curses.getmouse()
        btn_idx = which_button(mx, my, button_boxes)
        if btn_idx is not None and highlight_idx == btn_idx:
            return info, highlight_idx, False, highlight_timer
        if btn_idx == 0:
            run_qdbus6([
                "org.bluez",
                PLAYER_PATH,
                "org.bluez.MediaPlayer1.Previous"
            ])
        elif btn_idx == 1:
            status = info.get("Status", "paused").lower()
            if status == "playing":
                run_qdbus6([
                    "org.bluez",
                    PLAYER_PATH,
                    "org.bluez.MediaPlayer1.Pause"
                ])
            else:
                run_qdbus6([
                    "org.bluez",
                    PLAYER_PATH,
                    "org.bluez.MediaPlayer1.Play"
                ])
        elif btn_idx == 2:
            run_qdbus6([
                "org.bluez",
                PLAYER_PATH,
                "org.bluez.MediaPlayer1.Next"
            ])
        return refresh_bluetooth_info(PLAYER_PATH), btn_idx, False, time.time()
    return refresh_bluetooth_info(PLAYER_PATH), highlight_idx, False, highlight_timer

def show_figlet_error_screen(stdscr, message, color_pair):
    # Shows an error message you can't ignore, even if you want to.
    fill_background(stdscr, color_pair)
    figlet_centered(stdscr, "Error", color_pair=color_pair)
    max_y, max_x = stdscr.getmaxyx()
    for idx, line in enumerate(message.splitlines()):
        y = max_y // 2 + idx + 3
        x = (max_x - len(line)) // 2
        try:
            stdscr.addstr(y, x, line, curses.color_pair(color_pair))
        except curses.error:
            pass
    stdscr.addstr(max_y-2, 0, "Press [q] to quit.".center(max_x), curses.color_pair(color_pair))
    stdscr.refresh()
    while True:
        key = stdscr.getch()
        if key in [ord('q'), ord('Q')]:
            break
        time.sleep(0.1)

def update_highlight_timer(now, highlight_timer, highlight_idx, autorefresh_interval):
    # Handles highlight timing for buttons, because visual feedback is important for dopamine.
    if highlight_timer is not None and now - highlight_timer >= (autorefresh_interval * 0.60):
        return None, None
    return highlight_idx, highlight_timer

def parse_color_arg(color_str, is_fg=True):
    # Accepts an integer color value as string, returns int in [0,255]. If None or "" or "default", returns -1 for transparent.
    if color_str is None or color_str.strip() == "" or color_str.strip().lower() == "default":
        return -1
    try:
        if color_str.startswith("#"):
            return 7 if is_fg else 0
        return max(-1, min(255, int(color_str)))
    except Exception:
        return -1

def launch_cava_visualizer(bars=30, bit_format="16bit", fg_color=7, bg_color=0, autogain=True):
    # Launch cava as a subprocess and yield normalized bar values
    conpat = """
[general]
bars = %d
autogain = %s
[output]
method = raw
raw_target = %s
bit_format = %s
"""
    RAW_TARGET = "/dev/stdout"
    config = conpat % (bars, "1" if autogain else "0", RAW_TARGET, bit_format)
    bytetype, bytesize, bytenorm = ("H", 2, 65535) if bit_format == "16bit" else ("B", 1, 255)
    def cava_reader(pipe, chunk, fmt, bytenorm, outlist, stop_event):
        gain = 1.0
        decay = 0.98
        min_gain = 1.0
        max_gain = 100.0
        target = 0.98  # Target normalized height for the tallest bar
        while not stop_event.is_set():
            data = pipe.read(chunk)
            if not data or len(data) < chunk:
                break
            sample = [i / bytenorm for i in struct.unpack(fmt, data)]
            if autogain and sample:
                peak = max(sample)
                if peak > 0:
                    new_gain = min(max_gain, max(min_gain, target / peak))
                    gain = gain * decay + new_gain * (1 - decay)
                else:
                    gain = gain * decay
                sample = [min(1.0, max(0.0, v * gain)) for v in sample]
            outlist[:] = sample
    config_file = tempfile.NamedTemporaryFile(delete=False)
    config_file.write(config.encode())
    config_file.flush()
    process = subprocess.Popen(["cava", "-p", config_file.name], stdout=subprocess.PIPE)
    chunk = bytesize * bars
    fmt = bytetype * bars
    vis_data = [0.0] * bars
    stop_event = threading.Event()
    thread = threading.Thread(target=cava_reader, args=(process.stdout, chunk, fmt, bytenorm, vis_data, stop_event), daemon=True)
    thread.start()
    return process, vis_data, stop_event, config_file.name

def draw_visualizer(stdscr, vis_data, y_start, color_pair, fg_color, bg_color):
    # Draws the visualizer bars at the bottom of the screen
    max_y, max_x = stdscr.getmaxyx()
    term_width = max_x
    bars = len(vis_data)
    max_height = max_y - y_start
    display_data = []
    if bars == term_width:
        display_data = vis_data
    elif bars > term_width:
        # Downsample: average groups of bars
        factor = bars / term_width
        display_data = [
            sum(vis_data[int(i*factor):int((i+1)*factor)]) / max(1, int((i+1)*factor)-int(i*factor))
            for i in range(term_width)
        ]
    else:
        # Repeat each bar to fill the width (no smoothing), distribute extra columns symmetrically
        repeats = term_width // bars
        remainder = term_width % bars
        # Calculate how many times each bar should be repeated
        repeat_counts = [repeats] * bars
        # Distribute the remainder symmetrically from the center
        center = bars // 2
        left = center - (remainder // 2)
        for i in range(remainder):
            idx = left + i
            if idx < bars:
                repeat_counts[idx] += 1
        for i, val in enumerate(vis_data):
            display_data.extend([val] * repeat_counts[i])
        # If rounding error, trim or pad
        if len(display_data) > term_width:
            display_data = display_data[:term_width]
        elif len(display_data) < term_width:
            display_data.extend([vis_data[-1]] * (term_width - len(display_data)))
    for i, val in enumerate(display_data):
        height = int(val * max_height)
        for h in range(max_height):
            y = y_start + max_height - h - 1
            char = '█' if h < height else ' '
            try:
                stdscr.addstr(y, i, char, curses.color_pair(color_pair))
            except curses.error:
                pass

def main(stdscr, mac_addr, color_pair, bg_color_pair, autorefresh_interval=1.0, visu_refresh=0.1, scan_mode=None, announce=False):
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    stdscr.nodelay(True)
    button_boxes = []

    internal_audio = not mac_addr

    anim_state = {"frame": 0, "direction": 1, "last_update": time.time()}
    highlight_idx = None
    highlight_timer = None
    prev_song_id = None
    last_announce_time = 0

    fill_background(stdscr, color_pair)

    # Visualizer setup
    args = parse_args()
    visualizer_enabled = getattr(args, 'visualizer', False)
    fg_color = parse_color_arg(args.color, is_fg=True)
    bg_color = parse_color_arg(args.bgcolor, is_fg=False)
    term_width = stdscr.getmaxyx()[1]
    visu_bars = max(1, min(getattr(args, 'visu_bars', 30), term_width))
    visu_autogain = getattr(args, 'visu_autogain', True)
    cava_process = None
    vis_data = None
    stop_event = None
    cava_config_file = None
    if visualizer_enabled and shutil.which("cava"):
        cava_process, vis_data, stop_event, cava_config_file = launch_cava_visualizer(bars=visu_bars, fg_color=fg_color, bg_color=bg_color, autogain=visu_autogain)

    try:
        if internal_audio:
            if shutil.which("playerctl") is None:
                show_figlet_error_screen(
                    stdscr,
                    "playerctl is not installed.\nInstall it to use this program without --device.",
                    color_pair
                )
                return

            player = find_active_player()
            while not player:
                player = no_player_screen(stdscr, color_pair, autoretry_interval=autorefresh_interval)
                if player is False:
                    return
            info = refresh_internal_audio_info(player)
            prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), None, announce)
            draw_ui(
                stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
            )
            last_ui_refresh = time.time()
            last_visu_refresh = time.time()
            while True:
                try:
                    current_player = find_active_player()
                    if not current_player:
                        player = no_player_screen(stdscr, color_pair, autoretry_interval=autorefresh_interval)
                        if player is False:
                            return
                        info = refresh_internal_audio_info(player)
                        prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), None, announce)
                        draw_ui(
                            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                            internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                            autorefresh_interval=autorefresh_interval,
                            mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                        )
                        last_ui_refresh = time.time()
                        last_visu_refresh = time.time()
                        continue
                    else:
                        player = current_player
                    key = stdscr.getch()
                    now = time.time()
                    anim_update_needed = False
                    if info:
                        try:
                            pos = int(info.get("Position", "0"))
                            dur = int(info.get("Duration", "1"))
                            rem = dur - pos
                        except:
                            rem = 0
                        if rem < 0:
                            if now - anim_state.get("last_update", 0) > 0.07:
                                anim_update_needed = True

                    highlight_idx, highlight_timer = update_highlight_timer(now, highlight_timer, highlight_idx, autorefresh_interval)

                    if now - last_announce_time < 3:
                        skip_delay = 3
                    else:
                        skip_delay = 0

                    ui_should_refresh = (now - last_ui_refresh > autorefresh_interval) or anim_update_needed
                    visu_should_refresh = visualizer_enabled and (now - last_visu_refresh > visu_refresh)

                    if ui_should_refresh or visu_should_refresh:
                        # Only refresh info if UI refresh is due
                        if ui_should_refresh:
                            if anim_update_needed:
                                bar_length = max(stdscr.getmaxyx()[1] - 23, 10)
                                anim_len = 5
                                bar_inside = bar_length - 2
                                if anim_state["frame"] < 0:
                                    anim_state["frame"] = 0
                                    anim_state["direction"] = 1
                                if anim_state["frame"] > bar_inside - anim_len:
                                    anim_state["frame"] = bar_inside - anim_len
                                    anim_state["direction"] = -1
                                anim_state["frame"] += anim_state["direction"]
                                anim_state["last_update"] = now
                            else:
                                info = refresh_internal_audio_info(player)
                                last_ui_refresh = now
                            prev_song_id = announce_song(
                                info.get("Title", ""), info.get("Artist", ""), prev_song_id, announce, skip_delay=skip_delay
                            )
                            last_announce_time = now
                        # Always redraw UI if either refresh is due
                        draw_ui(
                            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                            internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                            autorefresh_interval=autorefresh_interval,
                            mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                        )
                        if ui_should_refresh:
                            last_ui_refresh = now
                        if visu_should_refresh:
                            last_visu_refresh = now
                    if key == -1:
                        time.sleep(0.02)
                        continue
                    info_new, idx, quit_app, new_timer = handle_keypress_internal_audio(
                        key, player, info, button_boxes, stdscr, color_pair, highlight_idx, highlight_timer, autorefresh_interval
                    )
                    if idx is not None:
                        highlight_idx = idx
                        highlight_timer = new_timer
                    info = info_new
                    prev_song_id = announce_song(
                        info.get("Title", ""), info.get("Artist", ""), prev_song_id, announce, skip_delay=skip_delay
                    )
                    last_announce_time = now
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                        autorefresh_interval=autorefresh_interval,
                        mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                    )
                    last_ui_refresh = time.time()
                    if quit_app:
                        break
                except KeyboardInterrupt:
                    break
                except curses.error:
                    stdscr.clear()
                    msg = "Screen too small. Try resizing your terminal or accept your fate.\n(You lost to a terminal window, congrats.)"
                    try:
                        stdscr.addstr(0, 0, msg)
                        stdscr.refresh()
                        time.sleep(2)
                    except Exception:
                        pass
        else:
            bluez_mac = mac_to_bluez(mac_addr)
            PLAYER_PATH = f"/org/bluez/hci0/dev_{bluez_mac}/player0"

            def do_reconnect():
                attempt_bluetooth_connect(mac_addr)

            attempt_bluetooth_connect(mac_addr)
            highlight_idx = None
            highlight_timer = None

            def bluetooth_info():
                return refresh_bluetooth_info(PLAYER_PATH)

            while not check_device_connected(mac_addr):
                found = device_not_found_screen(
                    stdscr, bluez_mac, color_pair, autoretry_interval=autorefresh_interval,
                    reconnect_callback=do_reconnect
                )
                if not found:
                    return
            info = bluetooth_info()
            prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), None, announce)
            draw_ui(
                stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                bluetooth_mode=True,
                mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
            )
            last_ui_refresh = time.time()
            last_visu_refresh = time.time()
            while True:
                try:
                    key = stdscr.getch()
                    now = time.time()
                    highlight_idx, highlight_timer = update_highlight_timer(now, highlight_timer, highlight_idx, autorefresh_interval)

                    if not check_device_connected(mac_addr):
                        found = device_not_found_screen(
                            stdscr, bluez_mac, color_pair, autoretry_interval=autorefresh_interval,
                            reconnect_callback=do_reconnect
                        )
                        if not found:
                            return
                        info = bluetooth_info()
                        prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), prev_song_id, announce)
                        draw_ui(
                            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                            highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                            bluetooth_mode=True,
                            mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                        )
                        last_ui_refresh = time.time()
                        last_visu_refresh = time.time()
                        continue
                    if now - last_announce_time < 3:
                        skip_delay = 3
                    else:
                        skip_delay = 0
                    ui_should_refresh = (now - last_ui_refresh > autorefresh_interval)
                    visu_should_refresh = visualizer_enabled and (now - last_visu_refresh > visu_refresh)
                    if ui_should_refresh or visu_should_refresh:
                        if ui_should_refresh:
                            info = bluetooth_info()
                            prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), prev_song_id, announce, skip_delay=skip_delay)
                            last_announce_time = now
                        draw_ui(
                            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                            highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                            bluetooth_mode=True,
                            mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                        )
                        if ui_should_refresh:
                            last_ui_refresh = now
                        if visu_should_refresh:
                            last_visu_refresh = now
                    if key == -1:
                        time.sleep(0.05)
                        continue
                    info_new, idx, quit_app, new_timer = handle_keypress_bluetooth(
                        key, PLAYER_PATH, info, button_boxes, highlight_idx, highlight_timer, autorefresh_interval
                    )
                    if idx is not None:
                        highlight_idx = idx
                        highlight_timer = new_timer
                    info = bluetooth_info()
                    prev_song_id = announce_song(info.get("Title", ""), info.get("Artist", ""), prev_song_id, announce, skip_delay=skip_delay)
                    last_announce_time = now
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                        bluetooth_mode=True,
                        mouse_ignore_idx=highlight_idx, visualizer_data=vis_data if visualizer_enabled else None, fg_color=fg_color, bg_color=bg_color
                    )
                    last_ui_refresh = time.time()
                    if quit_app:
                        break
                except KeyboardInterrupt:
                    break
                except curses.error:
                    stdscr.clear()
                    msg = "Screen too small. Try resizing your terminal or accept your fate.\n(You lost to a terminal window, congrats.)"
                    try:
                        stdscr.addstr(0, 0, msg)
                        stdscr.refresh()
                        time.sleep(2)
                    except Exception:
                        pass
    finally:
        # Cleanup CAVA process and config file
        if stop_event:
            stop_event.set()
        if cava_process:
            cava_process.terminate()
            cava_process.wait()
        if cava_config_file:
            try:
                os.unlink(cava_config_file)
            except Exception:
                pass

def parse_args():
    parser = argparse.ArgumentParser(description="Bluetooth Music Player UI with Mouse and Keyboard Control")
    parser.add_argument('-D', '--device', type=str, required=False, help="Bluetooth MAC address of the device")
    parser.add_argument('-c', '--color', type=str, default="default", help="Text color theme (0-255 or terminal default)")
    parser.add_argument('-b', '--bgcolor', type=str, default="default", help="Background color (0-255 or terminal default)")
    parser.add_argument('-a', '--autorefresh', type=float, default=1.0, help="Autorefresh interval in seconds (default: 1.0)")
    parser.add_argument('-A', '--announce', action='store_true', help="Announce songs using espeak (optional dependency - requires installation via a package manager)")
    parser.add_argument('-V', '--visualizer', action='store_true', help="Show CAVA visualizer at the bottom of the UI (optional dependency - requires installation via a package manager)")
    parser.add_argument('-v', '--visu-refresh', type=float, default=0.1, help="Refresh rate in seconds when visualizer is active (default: 0.1)")
    parser.add_argument('-g', '--visu-autogain', action='store_true', default=False, help="Enable autogain for the visualizer (default: enabled)")
    parser.add_argument('-B', '--visu-bars', type=int, default=30, help="Number of visualizer bars (min: 1, max: terminal width)")
    return parser.parse_args()

def color_theme(theme, bgtheme="default"):
    # Use 256-color mode, default -1 means terminal default/transparent.
    fg = parse_color_arg(theme, is_fg=True)
    bg = parse_color_arg(bgtheme, is_fg=False)
    curses.init_pair(1, fg, bg)
    curses.init_pair(2, bg, fg)
    return 1, 2

def check_for_update():
    """Check for updates and return a message if an update is available, else return None."""
    github_url = "https://github.com/40476/songUI/raw/refs/heads/main/version.txt"
    try:
        remote_version = None
        with urllib.request.urlopen(github_url, timeout=5) as resp:
            remote_version = resp.read().decode().strip()
        local_version = BUILD_TIMESTAMP.strip('"') if isinstance(BUILD_TIMESTAMP, str) else str(BUILD_TIMESTAMP)
        if remote_version:
            if local_version != remote_version:
                if local_version > remote_version:
                    return f"\n\033[96m[REMINDER]\033[0m Your BUILD_TIMESTAMP ({local_version}) is newer than the repo's ({remote_version}).\nDon't forget to push your changes, or the time police will come for you!\n"
                else:
                    return f"\n\033[93m[UPDATE AVAILABLE]\033[0m Your BUILD_TIMESTAMP is {local_version}, but the latest is {remote_version}.\nGet the latest: https://github.com/40476/songUI\n"
    except Exception:
        pass
    return None

def run():
    # "You cant park there sir"
    # _/==\_
    # o----o
    # Start update check in the background, store result in a shared variable (balls)
    import threading
    update_message = {'msg': None}  # type: dict[str, str | None]
    update_done = threading.Event()
    def update_check_worker():
        msg = check_for_update()
        update_message['msg'] = msg if msg is not None else ''
        update_done.set()
    threading.Thread(target=update_check_worker, daemon=True).start()
    args = parse_args()
    autorefresh_interval = args.autorefresh
    visu_refresh = getattr(args, 'visu_refresh', 0.1)
    missing = check_deps(espeak_optional=True)
    if missing:
        print("Missing dependencies:", ", ".join(missing))
        print("Please install them or this program will be about as useful as a screen door on a submarine.")
        sys.exit(1)
    mac_addr = args.device
    fgpair, bgpair = 1, 2
    def wrapped(stdscr):
        curses.start_color()
        curses.use_default_colors()
        nonlocal fgpair, bgpair
        fgpair, bgpair = color_theme(args.color, args.bgcolor)
        main(stdscr, mac_addr, fgpair, bgpair, autorefresh_interval=autorefresh_interval, visu_refresh=visu_refresh, announce=args.announce)
    try:
        curses.wrapper(wrapped)
    except curses.error:
        print("Curses crashed or smth idk i didnt write curses")
    update_done.wait(timeout=0.2)
    if update_message['msg']:
        print(update_message['msg'])

if __name__ == "__main__":
    run()
