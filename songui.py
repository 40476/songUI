#!/bin/python3.13
"""
- Shows track info, progress bar, and basic controls.
- If --device/-D is not specified, uses currently playing local MPRIS2 player.
- For internal audio: If remaining time is negative, animates a <===> bar moving across.
- Low CPU usage
- Buttons stay highlighted for (autorefresh_interval*0.60) seconds after being pressed.
- If --device is specified, attempts to connect at startup.
- "Reconnect" is a keyboard key ([r]), available only when device not found.
- All command-line arguments have short forms.
- In internal mode, if no player is found: show "No Player" screen, auto-retry every autorefresh interval.
"""
import curses
import subprocess
import argparse
import time
import shutil

def run_qdbus6(args):
    try:
        return subprocess.check_output(["qdbus6", "--system"] + args, text=True)
    except Exception:
        return ""

def parse_status(output: str) -> dict:
    info = {}
    for line in output.splitlines():
        if ':' in line:
            k, v = line.split(':', 1)
            info[k.strip()] = v.strip()
    return info

def ms_to_mins_secs(ms):
    seconds = int(ms) // 1000
    mins = seconds // 60
    hours = mins // 60
    secs = seconds % 60
    return f"{hours:02}:{mins:02}:{secs:02}"

def check_device_connected(mac_addr):
    dev_path = f"/org/bluez/hci0/dev_{mac_addr.replace(':', '_')}"
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
    try:
        subprocess.run(['bluetoothctl', 'connect', mac_addr.replace('_', ':')], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False

# def run_bluetoothctl_player_scan(mode):
#     try:
#         subprocess.run(['bluetoothctl', 'player.scan', mode], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#     except Exception:
#         pass

def figlet_centered(stdscr, text, color_pair=0):
    max_y, max_x = stdscr.getmaxyx()
    try:
        figlet_out = subprocess.check_output(
            ["figlet", "-f", "slant", "-l", "-w", str(max_x), text],
            text=True,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        figlet_out = text
    lines = figlet_out.splitlines()
    y0 = max((max_y - len(lines)) // 2, 0)
    for i, line in enumerate(lines):
        x0 = max((max_x - len(line)) // 2, 0)
        try:
            stdscr.addstr(y0 + i, x0, line, curses.color_pair(color_pair))
        except curses.error:
            pass

def which_button(mx, my, button_boxes):
    for idx, (y1, x1, y2, x2) in enumerate(button_boxes):
        if y1 <= my < y2 and x1 <= mx < x2:
            return idx
    return None

def mac_to_bluez(mac):
    return mac.replace(":", "_").upper()

def bluez_to_mac(bluez_mac):
    return bluez_mac.replace("_", ":").upper()

def draw_progress_bar(stdscr, y, elapsed, bar_length, prog_pos, prog_dur, remaining, color_pair, internal_audio=False, remaining_time=0, status="", anim_state=None):
    max_x = stdscr.getmaxyx()[1]
    if internal_audio and remaining_time < 0:
        anim_len = 5  # "<===>"
        anim_str = "<===>"
        bar_inside = bar_length - 2
        if anim_state is None or "frame" not in anim_state or "direction" not in anim_state:
            anim_state = {"frame": 0, "direction": 1}
        frame = anim_state["frame"]
        if frame < 0:
            frame = 0
            anim_state["direction"] = 1
        if frame > bar_inside - anim_len:
            frame = bar_inside - anim_len
            anim_state["direction"] = -1
        bar = "[" + " " * frame + anim_str + " " * (bar_inside - frame - anim_len) + "]"
    else:
        filled_length = int(bar_length * prog_pos // prog_dur) if prog_dur else 0
        bar = "[" + "=" * filled_length + ">" + " " * (bar_length - filled_length - 1) + "]"
    line = f"{elapsed.rjust(5)} {bar} -{remaining}"
    stdscr.addstr(y, 0, line[:max_x], curses.color_pair(color_pair))

def draw_control_buttons(stdscr, labels, highlight_idx, button_boxes, color_pair, highlight_timer=None, autorefresh_interval=1.0):
    max_y, max_x = stdscr.getmaxyx()
    button_boxes.clear()
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
        for bx in range(x, x+btn_w):
            stdscr.addch(y, bx, curses.ACS_HLINE, curses.color_pair(color_pair))
            stdscr.addch(y+btn_h-1, bx, curses.ACS_HLINE, curses.color_pair(color_pair))
        for by in range(y, y+btn_h):
            stdscr.addch(by, x, curses.ACS_VLINE, curses.color_pair(color_pair))
            stdscr.addch(by, x+btn_w-1, curses.ACS_VLINE, curses.color_pair(color_pair))
        stdscr.addch(y, x, curses.ACS_ULCORNER, curses.color_pair(color_pair))
        stdscr.addch(y, x+btn_w-1, curses.ACS_URCORNER, curses.color_pair(color_pair))
        stdscr.addch(y+btn_h-1, x, curses.ACS_LLCORNER, curses.color_pair(color_pair))
        stdscr.addch(y+btn_h-1, x+btn_w-1, curses.ACS_LRCORNER, curses.color_pair(color_pair))
        for by in range(y+1, y+btn_h-1):
            stdscr.addstr(
                by, x+1, " " * (btn_w-2),
                curses.color_pair(color_pair) | (curses.A_REVERSE if highlight_active(i) else curses.A_NORMAL)
            )
        stdscr.addstr(
            y + btn_h//2, x + (btn_w-len(label))//2, label,
            curses.A_BOLD | curses.color_pair(color_pair) | (curses.A_REVERSE if highlight_active(i) else 0)
        )

def draw_ui(stdscr, info, highlight_idx=None, button_boxes=None, color_pair=0, internal_audio=False, anim_state=None, highlight_timer=None, autorefresh_interval=1.0, bluetooth_mode=False):
    stdscr.clear()
    max_y, max_x = stdscr.getmaxyx()
    album = info.get("Track", info.get("Album", 'Stop posting about baller'))
    title = info.get("Title", "Unknown Title")
    artist = info.get("Artist", "Unknown Artist")
    status = info.get("Status", "paused").capitalize()
    
    # Sometimes info.get does not work return fallback values so this is a failsafe
    if artist=="":
        artist="Unknown Artist"
    
    if album.endswith("Album:"):
        album='Unknown Album'
    elif album.startswith("Album: ") or album.startswith("Track: "):
        album = album[7:]
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
    stdscr.addstr(1, 0, title.center(max_x), curses.color_pair(color_pair))
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
        highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval
    )
    btn_y = 9
    btn_h = 5
    help_line = "Controls: [p] Play/Pause  [n] Next  [b] Previous  [q] Quit"
    stdscr.addstr(btn_y+btn_h+1, 0, help_line.center(max_x), curses.color_pair(color_pair))
    stdscr.addstr(btn_y+btn_h+2, 0, "=" * max_x, curses.color_pair(color_pair))
    stdscr.refresh()

def no_player_screen(stdscr, color_pair, autoretry_interval=1.0):
    while True:
        stdscr.clear()
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
        # retry after interval
        player = find_active_player()
        if player:
            return player

def device_not_found_screen(stdscr, bluez_mac, color_pair, autoretry_interval=1.0, reconnect_callback=None):
    shown_mac = bluez_to_mac(bluez_mac)
    while True:
        stdscr.clear()
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
    info["Track"] = get(["playerctl", "-p", player, "metadata", "album"]) # fallback: for consistency
    pos = get(["playerctl", "-p", player, "position"])
    try:
        info["Position"] = str(int(float(pos) * 1000))
    except Exception:
        info["Position"] = "0"
    dur = get(["playerctl", "-p", player, "metadata", "mpris:length"])
    info["Duration"] = dur if dur else "1"
    return info

def refresh_bluetooth_info(PLAYER_PATH):
    status_out = run_qdbus6([
        "org.bluez",
        PLAYER_PATH,
        "org.freedesktop.DBus.Properties.GetAll",
        "org.bluez.MediaPlayer1"
    ])
    info = parse_status(status_out)
    return info

def handle_keypress_internal_audio(key, player, info, button_boxes, stdscr, color_pair):
    highlight_idx = None
    if key == ord('q'):
        return info, highlight_idx, True
    elif key in [ord('p'), ord(' ')]:
        status = info.get("Status", "paused").lower()
        if status == "playing":
            subprocess.run(["playerctl", "-p", player, "pause"])
        else:
            subprocess.run(["playerctl", "-p", player, "play"])
        highlight_idx = 1
    elif key in [ord('n'), curses.KEY_RIGHT]:
        subprocess.run(["playerctl", "-p", player, "next"])
        highlight_idx = 2
    elif key in [ord('b'), curses.KEY_LEFT]:
        subprocess.run(["playerctl", "-p", player, "previous"])
        highlight_idx = 0
    elif key == curses.KEY_MOUSE:
        _, mx, my, _, _ = curses.getmouse()
        btn_idx = which_button(mx, my, button_boxes)
        highlight_idx = btn_idx
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
    return refresh_internal_audio_info(player), highlight_idx, False

def handle_keypress_bluetooth(key, PLAYER_PATH, info, button_boxes):
    highlight_idx = None
    if key == ord('q'):
        return info, highlight_idx, True
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
        highlight_idx = 1
    elif key in [ord('n'), curses.KEY_RIGHT]:
        run_qdbus6([
            "org.bluez",
            PLAYER_PATH,
            "org.bluez.MediaPlayer1.Next"
        ])
        highlight_idx = 2
    elif key in [ord('b'), curses.KEY_LEFT]:
        run_qdbus6([
            "org.bluez",
            PLAYER_PATH,
            "org.bluez.MediaPlayer1.Previous"
        ])
        highlight_idx = 0
    elif key == curses.KEY_MOUSE:
        _, mx, my, _, _ = curses.getmouse()
        btn_idx = which_button(mx, my, button_boxes)
        highlight_idx = btn_idx
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
    return info, highlight_idx, False

def show_figlet_error_screen(stdscr, message, color_pair):
    stdscr.clear()
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
    if highlight_timer is not None and now - highlight_timer >= (autorefresh_interval * 0.60):
        return None, None
    return highlight_idx, highlight_timer

def alternate_scan_state(scan_idx, scan_states):
    scan_idx = 1 - scan_idx
    return scan_idx, scan_states[scan_idx]

def refresh_and_draw_ui(stdscr, info_func, *ui_args, **ui_kwargs):
    info = info_func()
    draw_ui(stdscr, info, *ui_args, **ui_kwargs)
    return info

def main(stdscr, mac_addr, color_pair, autorefresh_interval=1.0, scan_mode=None):
    curses.curs_set(0)
    curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
    stdscr.nodelay(True)
    button_boxes = []

    internal_audio = not mac_addr

    anim_state = {"frame": 0, "direction": 1, "last_update": time.time()}
    highlight_idx = None
    highlight_timer = None

    if internal_audio:
        if shutil.which("playerctl") is None:
            show_figlet_error_screen(
                stdscr,
                "playerctl is not installed.\nInstall it to use this program without --device.",
                color_pair
            )
            return

        player = find_active_player()
        # New: If no player, show no-player screen and autoretry
        while not player:
            player = no_player_screen(stdscr, color_pair, autoretry_interval=autorefresh_interval)
            if player is False:
                return  # user quit
        info = refresh_internal_audio_info(player)
        draw_ui(
            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
            internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval
        )
        last_refresh = time.time()
        while True:
            try:
                # check if player still exists
                current_player = find_active_player()
                if not current_player:
                    # lost player, go to no-player screen and autoretry
                    player = no_player_screen(stdscr, color_pair, autoretry_interval=autorefresh_interval)
                    if player is False:
                        return
                    info = refresh_internal_audio_info(player)
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                        autorefresh_interval=autorefresh_interval
                    )
                    last_refresh = time.time()
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
                        if now - anim_state.get("last_update", 0) > 0.2:
                            anim_update_needed = True

                highlight_idx, highlight_timer = update_highlight_timer(now, highlight_timer, highlight_idx, autorefresh_interval)

                if now - last_refresh > autorefresh_interval or anim_update_needed:
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
                        last_refresh = now
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                        autorefresh_interval=autorefresh_interval
                    )

                if key == -1:
                    time.sleep(0.05)
                    continue
                info_new, idx, quit_app = handle_keypress_internal_audio(key, player, info, button_boxes, stdscr, color_pair)
                if idx is not None:
                    highlight_idx = idx
                    highlight_timer = time.time()
                info = info_new
                draw_ui(
                    stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                    internal_audio=True, anim_state=anim_state, highlight_timer=highlight_timer,
                    autorefresh_interval=autorefresh_interval
                )
                last_refresh = time.time()
                if quit_app:
                    break
            except KeyboardInterrupt:
                break
    else:
        bluez_mac = mac_to_bluez(mac_addr)
        PLAYER_PATH = f"/org/bluez/hci0/dev_{bluez_mac}/player0"

        def do_reconnect():
            attempt_bluetooth_connect(mac_addr)

        attempt_bluetooth_connect(mac_addr)

        highlight_idx = None
        highlight_timer = None

        # scan_states = ["alltracks", "off"]
        # scan_idx = 0 if scan_mode == "alltracks" else 1
        # scan_last_switch_time = None
        # scan_last_mode_sent = None

        # run_bluetoothctl_player_scan(scan_states[scan_idx])
        # scan_last_mode_sent = scan_states[scan_idx]
        # scan_last_switch_time = time.time()

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
        draw_ui(
            stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
            highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
            bluetooth_mode=True
        )
        last_refresh = time.time()
        while True:
            try:
                key = stdscr.getch()
                now = time.time()
                highlight_idx, highlight_timer = update_highlight_timer(now, highlight_timer, highlight_idx, autorefresh_interval)

                # Bluetooth scan alternation (disabled as per your comment)
                # if scan_last_switch_time is None:
                #     scan_last_switch_time = now
                # if now - scan_last_switch_time >= 5.0:
                #     scan_idx, new_mode = alternate_scan_state(scan_idx, scan_states)
                #     run_bluetoothctl_player_scan(new_mode)
                #     scan_last_mode_sent = new_mode
                #     scan_last_switch_time = now

                if not check_device_connected(mac_addr):
                    found = device_not_found_screen(
                        stdscr, bluez_mac, color_pair, autoretry_interval=autorefresh_interval,
                        reconnect_callback=do_reconnect
                    )
                    if not found:
                        return
                    info = bluetooth_info()
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                        bluetooth_mode=True
                    )
                    last_refresh = time.time()
                    continue
                if now - last_refresh > autorefresh_interval:
                    info = bluetooth_info()
                    draw_ui(
                        stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                        highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                        bluetooth_mode=True
                    )
                    last_refresh = now
                if key == -1:
                    time.sleep(0.05)
                    continue
                info_new, idx, quit_app = handle_keypress_bluetooth(
                    key, PLAYER_PATH, info, button_boxes
                )
                if idx is not None:
                    highlight_idx = idx
                    highlight_timer = time.time()
                info = bluetooth_info()
                draw_ui(
                    stdscr, info, highlight_idx, button_boxes, color_pair=color_pair,
                    highlight_timer=highlight_timer, autorefresh_interval=autorefresh_interval,
                    bluetooth_mode=True
                )
                last_refresh = time.time()
                if quit_app:
                    break
            except KeyboardInterrupt:
                break

def parse_args():
    parser = argparse.ArgumentParser(description="Bluetooth Music Player UI with Mouse and Keyboard Control")
    parser.add_argument('-D', '--device', type=str, required=False, help="Bluetooth MAC address of the device")
    parser.add_argument('-c', '--color', type=str, default="white", help="Color theme (default: white)")
    parser.add_argument('-a', '--autorefresh', type=float, default=1.0, help="Autorefresh interval in seconds (default: 1.0)")
    # parser.add_argument('-s', '--scan', type=str, required=False, help="player.scan mode for bluetooth: alltracks or off (required if --device is specified) NOT USED DUE TO INSTABILITY")
    return parser.parse_args()

def color_theme(theme):
    colors = dict(
        black=curses.COLOR_BLACK, red=curses.COLOR_RED, green=curses.COLOR_GREEN, yellow=curses.COLOR_YELLOW,
        blue=curses.COLOR_BLUE, magenta=curses.COLOR_MAGENTA, cyan=curses.COLOR_CYAN, white=curses.COLOR_WHITE
    )
    base = colors.get(theme.lower(), curses.COLOR_WHITE)
    curses.init_pair(1, base, curses.COLOR_BLACK)
    return 1

def run():
    args = parse_args()
    mac_addr = args.device
    # scan_mode = args.scan.lower() if args.scan else None
    def wrapped(stdscr):
        curses.start_color()
        color_pair = color_theme(args.color)
        main(stdscr, mac_addr, color_pair, autorefresh_interval=args.autorefresh)#scan_mode=scan_mode)
    curses.wrapper(wrapped)

if __name__ == "__main__":
    run()

# "You cant park there sir"
# _/==\_
# o----o