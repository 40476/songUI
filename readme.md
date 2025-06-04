# SongUI

## A curses-based music player UI for Bluetooth or local (MPRIS2/playerctl) audio.

- Shows track info, progress bar, and basic controls.
- If --device/-D is not specified, uses currently playing local MPRIS2 player.
- For internal audio: If remaining time is negative, animates a <===> bar moving across.
- Low CPU usage
- Buttons stay highlighted for (autorefresh_interval*0.60) seconds after being pressed.
- If --device is specified, attempts to connect at startup.
- "Reconnect" is a keyboard key ([r]), available only when device not found.
- All command-line arguments have short forms.
- In internal mode, if no player is found: show "No Player" screen, auto-retry every autorefresh interval.