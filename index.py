import curses
import sys
import argparse
import time
from datetime import datetime
from collections import deque
import glob
import serial


def add_user_text(win, text, attr=0):
    new_text = ""
    for i in range(len(text)):
        char = text[i]
        if char.isprintable() or char == '\n':
            new_text += char
        else:
            win.addstr(new_text, attr)
            new_text = ""
            win.addstr(f"\\x{ord(char):02x}", curses.color_pair(4))

    if len(new_text) > 0:
        win.addstr(new_text, attr)

    return new_text


class SimpleSplitTerminal:
    def __init__(self, stdscr, port, baudrate, split="vertical"):
        self.stdscr = stdscr
        self.port = port
        self.baudrate = baudrate
        self.ser = None

        # Buffers
        self.rx_lines = []
        self.tx_lines = []
        self.hex_history = deque(maxlen=100)
        self.current_input = ""
        self.connected = False

        self.rx_pos = 0
        self.tx_pos = 0

        # Initialize
        self.rx_pad_y = 0
        self.tx_pad_y = 0

        self.rx_max_y = 0
        self.tx_max_y = 0

        self.split = split
        self.command_pending = False
        self._dirty = False
        self.selected_panel = "tx"  # "tx" or "rx" — arrow keys scroll this panel

        # Setup colors and screen
        curses.use_default_colors()
        curses.curs_set(1)
        self.stdscr.timeout(20)

        curses.start_color()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Normal text
        curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)   # Prompt
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Success
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)    # Error

        # Border style — change this to tweak panel borders globally
        self.BORDER = curses.color_pair(1)

        # Init layout dimensions
        self.resize_windows()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def resize_windows(self):
        self.height, self.width = self.stdscr.getmaxyx()

        self.sidebar_width = 8
        self.main_width = self.width - self.sidebar_width - 1

        # Reserve row 0 for the top bar
        panel_top = 1
        panel_height = self.height - 1
        self.split_row = panel_top + panel_height // 2
        self.split_col = self.main_width // 2

        if self.height < 11 or self.width < 30:
            raise Exception("Terminal screen too small!")

        # Top bar
        self.top_bar = curses.newwin(1, self.width, 0, 0)

        # Panels
        if self.split == "vertical":
            self.rx_win = curses.newwin(panel_height, self.split_col, panel_top, 0)
            self.tx_win = curses.newwin(panel_height, self.main_width - self.split_col - 1,
                                        panel_top, self.split_col + 1)
        else:
            rx_height = self.split_row - panel_top
            self.rx_win = curses.newwin(rx_height, self.main_width, panel_top, 0)
            self.tx_win = curses.newwin(self.height - self.split_row - 1, self.main_width,
                                        self.split_row + 1, 0)

        max_history_lines = 1000

        # Pads (scrollable text areas inside the panel borders)
        _, rx_max_x = self.rx_win.getmaxyx()
        self.rx_pad = curses.newpad(max_history_lines, rx_max_x - 2)

        _, tx_max_x = self.tx_win.getmaxyx()
        self.tx_pad = curses.newpad(max_history_lines, tx_max_x - 2)

        self.hex_win = curses.newwin(panel_height, self.sidebar_width, panel_top, self.main_width + 1)

        self._refresh_all_panels()

    # ------------------------------------------------------------------
    # Shared drawing helpers
    # ------------------------------------------------------------------

    def _draw_panel_border(self, win, title, *, refresh=True):
        """Erase *win*, draw a box border, and place *title* on the top edge."""
        win.erase()
        win.attrset(self.BORDER)
        win.box()
        win.attrset(curses.A_NORMAL)
        win.addstr(0, 2, f" {title} ", curses.A_BOLD)
        if refresh:
            win.noutrefresh()

    def _stage_pad(self, pad, win, pad_row, *, top_margin=1, bottom_margin=2):
        """Stage *pad* so its viewport fills the interior of *win*."""
        beg_y, beg_x = win.getbegyx()
        max_y, max_x = win.getmaxyx()
        pad.noutrefresh(
            pad_row, 0,
            beg_y + top_margin, beg_x + 1,
            beg_y + max_y - bottom_margin, beg_x + max_x - 2,
        )

    def _draw_status_line(self, win, row, count, label):
        """Draw a status line showing line count above/below viewport.

        If *count* is 0, draws a horizontal-rule border instead.
        """
        _, max_x = win.getmaxyx()
        interior_width = max_x - 2
        if count > 0:
            text = f" {label} {count} line{'s' if count != 1 else ''} "
        else:
            text = "─" * interior_width
        text = text[:interior_width]
        win.addstr(row, 1, text, curses.A_DIM)

    def _draw_panel_body(self, win, pad, pos, *, top_margin):
        """Shared body for RX/TX panels: status lines + pad viewport.

        Calls win.noutrefresh() BEFORE staging the pad so the pad content
        overlays the (otherwise blank) window interior on the virtual screen.
        """
        max_y, _ = win.getmaxyx()
        current_y, _ = pad.getyx()

        # Rows consumed by border + extra content above the pad viewport:
        #   row 0           = top border
        #   rows 1..top_margin-1 = extra content (e.g. TX prompt)
        #   row top_margin-1 = ↑ status line
        #   rows top_margin..max_y-3 = pad viewport
        #   row max_y-2      = ↓ status line
        #   row max_y-1      = bottom border
        visible_height = max_y - top_margin - 2  # -2 for ↓ status + bottom border
        pad_row = max(0, current_y - pos - visible_height)

        above = pad_row
        below = max(0, current_y - pad_row - visible_height)

        self._draw_status_line(win, top_margin - 1, above, "↑")
        self._draw_status_line(win, max_y - 2, below, "↓")

        # Push window content (border + prompt + status lines) to virtual
        # screen first, THEN overlay the pad — pad.noutrefresh must be last.
        win.noutrefresh()
        self._stage_pad(pad, win, pad_row, top_margin=top_margin, bottom_margin=3)

    def _ts(self, text):
        """Timestamp-prefixed line for RX / TX buffers."""
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        return f"[{ts}] {text}"

    def _refresh_all_panels(self):
        """Stage every panel and push to the physical screen.

        (update_top_bar is called inside refresh_screen already.)
        """
        self.update_hex_sidebar()
        self.update_tx_display()
        self.update_rx_display()
        self.refresh_screen()

    def refresh_screen(self):
        """Push all staged noutrefresh changes to the physical screen at once.

        Always re-stages the top bar so it survives implicit refreshes
        triggered by getch() -> wrefresh(stdscr).
        """
        self.update_top_bar()
        curses.doupdate()

    # ------------------------------------------------------------------
    # Top bar
    # ------------------------------------------------------------------

    def update_top_bar(self):
        self.top_bar.erase()
        _, w = self.top_bar.getmaxyx()
        attr = 0

        self.top_bar.addstr(0, 0, " " * (w - 1), attr)

        title = " Simple Split Terminal (sst) "
        self.top_bar.addstr(0, 0, title, attr | curses.A_BOLD)

        # Debug: show scroll position
        rx_y, _ = self.rx_pad.getyx()
        tx_y, _ = self.tx_pad.getyx()
        _, rx_h = self.rx_win.getmaxyx()
        _, tx_h = self.tx_win.getmaxyx()
        sel = f"[{self.selected_panel.upper()}]"
        debug = f" {sel} rx_pos={self.rx_pos}/{rx_y}r/{rx_h}h  tx_pos={self.tx_pos}/{tx_y}w/{tx_h}h "
        self.top_bar.addstr(0, len(title) + 1, debug, attr)

        if self.command_pending:
            indicator = "[Cmd]"
            self.top_bar.addstr(0, w - len(indicator) - 1, indicator, attr | curses.A_BOLD)

        self.top_bar.noutrefresh()

    # ------------------------------------------------------------------
    # Serial
    # ------------------------------------------------------------------

    def connect_serial(self):
        while not self.connected:
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
                self.add_rx_line(f"SYSTEM: Connected to {self.port} at {self.baudrate} baud.")
                self.connected = True
            except Exception as e:
                self.add_rx_line(f"ERROR: Could not open {self.port}: {e}")
                time.sleep(0.5)

    # ------------------------------------------------------------------
    # Line-oriented output
    # ------------------------------------------------------------------

    def add_rx_line(self, text):
        attr = curses.color_pair(4) if "ERROR" in text else 0
        add_user_text(self.rx_pad, self._ts(text) + "\n", attr)
        self.update_rx_display()
        self.refresh_screen()

    def add_tx_line(self, text):
        attr = curses.color_pair(4) if "ERROR" in text else 0
        add_user_text(self.tx_pad, self._ts(text) + "\n", attr)
        self.update_tx_display()
        self.refresh_screen()

    # ------------------------------------------------------------------
    # Per-panel display updates
    # ------------------------------------------------------------------

    def update_tx_display(self):
        self._draw_panel_border(self.tx_win, "TX Buffer", refresh=False)

        # Prompt line (row 1)
        _, max_tx_w = self.tx_win.getmaxyx()
        prompt = "TX > "
        available_width = max_tx_w - len(prompt) - 1
        visible_input = self.current_input[-available_width:]
        self.tx_win.addstr(1, 1, prompt, curses.A_BOLD)
        self.tx_win.addstr(1, len(prompt) + 1, visible_input)

        # Status lines + pad viewport (also calls win.noutrefresh before
        # staging the pad so the pad overlays the window interior).
        self._draw_panel_body(self.tx_win, self.tx_pad, self.tx_pos, top_margin=3)
        self.tx_max_y, self.tx_max_x = self.tx_win.getmaxyx()
        self.tx_pad_y, self.tx_pad_x = self.tx_pad.getyx()

    def update_rx_display(self):
        self._draw_panel_border(self.rx_win, "RX Buffer", refresh=False)
        self._draw_panel_body(self.rx_win, self.rx_pad, self.rx_pos, top_margin=2)
        self.rx_max_y, self.rx_max_x = self.rx_win.getmaxyx()

        last_height = self.rx_pad_y 

        self.rx_pad_y, self.rx_pad_x = self.rx_pad.getyx()

        if last_height != self.rx_pad_y and self.rx_pos != 0:
            self.rx_pos = max(0, self.rx_pos + (self.rx_pad_y - last_height))
            self.update_rx_display()


    def update_hex_sidebar(self):
        self.hex_win.erase()
        h, _ = self.hex_win.getmaxyx()

        self.hex_win.addstr(0, 1, "HEX", curses.A_UNDERLINE)

        recent_hex = list(self.hex_history)[-(h - 2):]
        for idx, hex_val in enumerate(recent_hex):
            if idx + 1 < h:
                self.hex_win.addstr(idx + 1, 2, f"0x{hex_val}")

        self.hex_win.noutrefresh()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        # Flush display before any blocking operation
        self._refresh_all_panels()
        self.stdscr.timeout(0)
        self.stdscr.getch()
        self.stdscr.timeout(20)

        self.connect_serial()

        # connect_serial triggers add_rx_line which only stages RX —
        # re-stage everything so TX / hex are not left stale.
        self._refresh_all_panels()

        rx_buffer = bytearray()

        while True:
            # --- Incoming serial data ---
            try:
                if self.ser and self.ser.in_waiting > 0:
                    data = self.ser.read(self.ser.in_waiting)
                    for byte in data:
                        if byte == ord('\n'):
                            self.add_rx_line(rx_buffer.decode('utf-8', errors='replace').strip())
                            rx_buffer.clear()
                        elif byte != ord('\r'):
                            rx_buffer.append(byte)
            except Exception as e:
                self.add_rx_line(f"SERIAL ERROR: {e}")
                self.connected = False
                self.connect_serial()

            # --- Keyboard input ---
            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                break
            except curses.error:
                continue

            if ch == curses.KEY_RESIZE:
                self.resize_windows()
                continue

            if ch == 9:                          # Tab — switch active panel
                self.selected_panel = "rx" if self.selected_panel == "tx" else "tx"
                self.update_rx_display()
                self.update_tx_display()
                self.refresh_screen()
                continue

            if ch == curses.KEY_UP:
                if self.selected_panel == "rx":
                    rx_visible = max(1, self.rx_pad_y - self.rx_max_y+4)
                    self.rx_pos = min(self.rx_pos + 1, rx_visible)
                    self.update_rx_display()
                else:
                    tx_visible = max(1, self.tx_pad_y - self.tx_max_y+4)
                    self.tx_pos = min(self.tx_pos + 1, tx_visible)
                    self.update_tx_display()
                self._dirty = True    # defer refresh — batches rapid scroll events
                continue

            if ch == curses.KEY_DOWN:
                if self.selected_panel == "rx":
                    self.rx_pos -= 1
                    if self.rx_pos < 0:
                        self.rx_pos = 0
                    self.update_rx_display()
                else:
                    self.tx_pos -= 1
                    if self.tx_pos < 0:
                        self.tx_pos = 0
                    self.update_tx_display()
                self._dirty = True    # defer refresh — batches rapid scroll events
                continue

            if ch == -1:
                if self._dirty:
                    self._dirty = False
                    self.refresh_screen()
                continue

            # --- Command mode (Ctrl+T prefix) ---
            if self.command_pending:
                self.command_pending = False
                if ch == ord("h"):
                    self.split = "horizontal"
                    self.resize_windows()
                elif ch == ord("v"):
                    self.split = "vertical"
                    self.resize_windows()
                else:
                    self.refresh_screen()  # hide [Cmd] indicator
                continue

            if ch == 20:                     # Ctrl+T
                self.command_pending = True
                self.refresh_screen()        # show [Cmd] indicator
                continue

            # --- Standard key handlers ---
            if ch in [3, 27]:                # Ctrl+C / ESC → quit
                break

            if ch in [10, 13, curses.KEY_ENTER]:
                if self.current_input:
                    self.hex_history.append(f"{ch:02X}")
                    self.add_tx_line(self.current_input)
                    try:
                        self.ser.write((self.current_input + "\r\n").encode('utf-8'))
                    except Exception as e:
                        self.add_tx_line(f"TX ERROR: {e}")
                    self.current_input = ""
                    self.tx_pos = 0  # auto-scroll TX to latest
                    self.update_tx_display()
                    self.update_hex_sidebar()
                    self._dirty = False
                    self.refresh_screen()

            elif ch in [8, 127, curses.KEY_BACKSPACE]:
                if self.current_input:
                    self.current_input = self.current_input[:-1]
                    self.update_tx_display()
                    self._dirty = False
                    self.refresh_screen()

            elif 32 <= ch <= 126:
                self.current_input += chr(ch)
                self.hex_history.append(f"{ch:02X}")
                self.update_tx_display()
                self.update_hex_sidebar()
                self._dirty = True   # defer refresh — batches rapid input like paste


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def find_serial_ports():
    patterns = ['/dev/ttyUSB*', '/dev/ttyAMA*']
    found_ports = []
    for pattern in patterns:
        found_ports.extend(glob.glob(pattern))
    return found_ports


def main():
    parser = argparse.ArgumentParser(description="sst - Simple Split Terminal")
    parser.add_argument("port", nargs="?", default="none",
                        help="Serial port (e.g. /dev/ttyUSB0, COM3). Use 'sim' for demo.")
    parser.add_argument("-b", "--baud", type=int, default=115200,
                        help="Baud rate (default: 115200)")
    parser.add_argument("-s", "--split", choices=["vertical", "horizontal"],
                        default="vertical", help="Split direction (default: vertical)")
    args = parser.parse_args()

    if args.port == "none":
        print("Error: No serial port specified.\n")
        print("Available serial ports:")
        for port in find_serial_ports():
            print(f"  {port}")
        return

    curses.wrapper(lambda stdscr: SimpleSplitTerminal(stdscr, args.port, args.baud, args.split).run())


if __name__ == "__main__":
    main()
