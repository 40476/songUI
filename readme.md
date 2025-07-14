# SongUI

## Demo recordings

### `songui -AVD <BT ADDRESS> -b 16 -v 0.01 -B 60`
![`songui -AVD <BT ADDRESS> -b 16 -v 0.01 -B 60`](readme/demo1.gif)

### `songui -AVD <BT ADDRESS> -b 11 -c 16 -v 0.01 -B 60`
![`songui -AVD <BT ADDRESS> -b 11 -c 16 -v 0.01 -B 60`](readme/demo2.gif)

### `songui -AVD <BT ADDRESS> -b 24 -v 0.01 -B 60`
![`songui -AVD <BT ADDRESS> -b 24 -v 0.01 -B 60`](readme/demo3.gif)

## Usage

```plaintext
usage: songui [-h] [-D DEVICE] [-c COLOR] [-b BGCOLOR] [-a AUTOREFRESH] [-A] [-V] [-v VISU_REFRESH] [-g] [-B VISU_BARS]

Bluetooth Music Player UI with Mouse and Keyboard Control

options:
  -h, --help            show this help message and exit
  -D, --device DEVICE   Bluetooth MAC address of the device
  -c, --color COLOR     Text color theme (0-255 or terminal default)
  -b, --bgcolor BGCOLOR
                        Background color (0-255 or terminal default)
  -a, --autorefresh AUTOREFRESH
                        Autorefresh interval in seconds (default: 1.0)
  -A, --announce        Announce songs using espeak (optional dependency - requires installation via a package manager)
  -V, --visualizer      Show CAVA visualizer at the bottom of the UI (optional dependency - requires installation via a package manager)
  -v, --visu-refresh VISU_REFRESH
                        Refresh rate in seconds when visualizer is active (default: 0.1)
  -g, --visu-autogain   Enable autogain for the visualizer (default: enabled)
  -B, --visu-bars VISU_BARS
                        Number of visualizer bars (min: 1, max: terminal width)
```

## A curses-based music player UI for Bluetooth or local (MPRIS2/playerctl) audio

- Shows track info, progress bar, and basic controls
- If --device/-D is not specified, uses currently playing local MPRIS2 player
- For internal audio: If remaining time is negative, animates a <===> bar moving across
- Low CPU usage
- Buttons stay highlighted for (autorefresh_interval*0.60) seconds after being pressed
- If --device is specified, attempts to connect at startup
- "Reconnect" is a keyboard key ([r]), available only when device not found
- All command-line arguments have short forms
- In internal mode, if no player is found: show "No Player" screen, auto-retry every autorefresh interval
- If --announce/-A is set, announce new song/artist changes using espeak
