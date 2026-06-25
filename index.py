import curses
import sys
import argparse
import time
from datetime import datetime
from collections import deque
import glob
import serial

def text_length(text):
    l = len(text)
    for i in range(len(text)):
        if not text[i].isprintable() and text[i] != '\n':
            l += 2 # Werid characters take up more space
    return l

def add_user_text(win, text):
    new_text = ""
    for i in range(len(text)):
        char = text[i]
        if char.isprintable() or char == '\n':
            new_text += char
        else:
            win.addstr(new_text)
            new_text = ""
            win.addstr(f"\\x{ord(char):02x}", curses.color_pair(4))

    if len(new_text) > 0:
        win.addstr(new_text)

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
        self.hex_history = deque(maxlen=100) # Tracks typed character hex codes
        self.current_input = ""
        self.connected = False

        self.rx_pos = 0
        self.tx_pos = 0

        self.split = split
        self.command_pending = False
        
        # Setup colors and screen
        curses.use_default_colors()
        curses.curs_set(1) # Show cursor
        self.stdscr.timeout(20) # Non-blocking input delay (ms)

        curses.start_color()
        curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Normal text
        curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)   # Prompt
        curses.init_pair(3, curses.COLOR_GREEN, curses.COLOR_BLACK)  # Success
        curses.init_pair(4, curses.COLOR_RED, curses.COLOR_BLACK)    # Error

        # Init layout dimensions
        self.resize_windows()

    def resize_windows(self):
        self.height, self.width = self.stdscr.getmaxyx()

        # Geometry definitions
        self.sidebar_width = 8
        self.main_width = self.width - self.sidebar_width - 1

        # Reserve row 0 for the top bar
        panel_top = 1
        panel_height = self.height - 1
        self.split_row = panel_top + panel_height // 2
        self.split_col = self.main_width // 2

        # Sub-window bounds checking to prevent crashes on tiny screens
        if self.height < 11 or self.width < 30:
            raise Exception("Terminal screen too small!")

        # Top bar
        self.top_bar = curses.newwin(1, self.width, 0, 0)

        # Create windows: newwin(height, width, begin_y, begin_x)
        if self.split == "vertical":
            self.rx_win = curses.newwin(panel_height, self.split_col, panel_top, 0)
            self.tx_win = curses.newwin(panel_height, self.main_width - self.split_col - 1, panel_top, self.split_col + 1)
        else:
            rx_height = self.split_row - panel_top
            self.rx_win = curses.newwin(rx_height, self.main_width, panel_top, 0)
            self.tx_win = curses.newwin(self.height - self.split_row - 1, self.main_width, self.split_row + 1, 0)

        max_history_lines = 1000

        # --- Create the Pads ---

        # For the RX (Receive) Pad:
        # It needs to be wide enough to fit inside the rx_win border (width - 2)
        rx_max_y, rx_max_x = self.rx_win.getmaxyx()
        self.rx_pad = curses.newpad(max_history_lines, rx_max_x - 2)

        # For the TX (Transmit) Pad:
        # It needs to be wide enough to fit inside the tx_win border (width - 2)
        tx_max_y, tx_max_x = self.tx_win.getmaxyx()
        self.tx_pad = curses.newpad(max_history_lines, tx_max_x - 2)

        self.hex_win = curses.newwin(panel_height, self.sidebar_width, panel_top, self.main_width + 1)

        self.update_top_bar()
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

    def update_top_bar(self):
        """Draw the top status bar with title and command indicator."""
        self.top_bar.erase()
        _, w = self.top_bar.getmaxyx()

        # Fill the entire bar with reversed-video spaces
        self.top_bar.addstr(0, 0, " " * (w-1))

        # Title on the left
        title = " Simple Split Terminal (sst) "
        self.top_bar.addstr(0, 0, title, curses.A_BOLD)

        # Command-mode indicator on the right
        if self.command_pending:
            indicator = "[Cmd]"
            self.top_bar.addstr(0, w - len(indicator) - 1, indicator,
                                curses.A_BOLD)

        self.top_bar.noutrefresh()

    def connect_serial(self):
        while not self.connected:
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
                self.add_rx_line(f"SYSTEM: Connected to {self.port} at {self.baudrate} baud.")
                self.connected = True
            except Exception as e:
                self.add_rx_line(f"ERROR: Could not open {self.port}: {e}")
                time.sleep(0.5)  # Wait before retrying

    def add_rx_line(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        formatted_line = f"[{timestamp}] {text}"
        
        add_user_text(self.rx_pad, formatted_line + "\n")

        self.update_rx_display()
        self.refresh_screen()

    def add_tx_line(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        formatted_line = f"[{timestamp}] {text}"

        add_user_text(self.tx_pad, formatted_line + "\n")

        self.update_tx_display()
        self.refresh_screen()

    def update_tx_display(self):
        self.tx_win.erase()
        _, max_tx_w = self.tx_win.getmaxyx()

        # Display Prompt
        prompt = "TX > "


        # Display typed text (truncated if it exceeds window width)
        available_width = max_tx_w - len(prompt) - 1
        visible_input = self.current_input[-available_width:]


        # 1. Clear and draw borders on the container windows
        self.tx_win.box()
        self.tx_win.addstr(0, 2, " TX Buffer ", curses.A_BOLD)
        self.tx_win.addstr(1, 1, prompt, curses.A_BOLD)
        self.tx_win.addstr(1, len(prompt)+1, visible_input)

        # 2. Stage the window refreshes in memory
        self.tx_win.noutrefresh()

        # 3. Calculate absolute screen coordinates for the PAD inner viewports
        # RX inner boundaries
        tx_beg_y, tx_beg_x = self.tx_win.getbegyx()
        tx_max_y, tx_max_x = self.tx_win.getmaxyx()

        # 4. Stage the pad updates over the window interiors
        # Arguments: pad, pad_top_line, pad_left_col, screen_top_y, screen_left_x, screen_bottom_y, screen_right_x
        self.tx_pad.noutrefresh(
            self.tx_pos, 0,
            tx_beg_y + 2, tx_beg_x + 1,        # Top-left interior
            tx_beg_y + tx_max_y - 2, tx_beg_x + tx_max_x - 2 # Bottom-right interior
        )

        # Move cursor to end of text
        self.tx_win.move(0, len(prompt) + len(visible_input))
        


    def update_rx_display(self):
        # 1. Clear and draw borders on the container windows
        self.rx_win.erase()
        self.rx_win.box()
        self.rx_win.addstr(0, 2, " RX Buffer ", curses.A_BOLD)

        # 2. Stage the window refreshes in memory
        self.rx_win.noutrefresh()

        # 3. Calculate absolute screen coordinates for the PAD inner viewports
        # RX inner boundaries
        rx_beg_y, rx_beg_x = self.rx_win.getbegyx()
        rx_max_y, rx_max_x = self.rx_win.getmaxyx()

        current_y, current_x = self.rx_pad.getyx()

        # 4. Stage the pad updates over the window interiors
        # Arguments: pad, pad_top_line, pad_left_col, screen_top_y, screen_left_x, screen_bottom_y, screen_right_x
        self.rx_pad.noutrefresh(
            max(0, current_y - rx_max_y - self.rx_pos), 0,
            rx_beg_y + 1, rx_beg_x + 1,        # Top-left interior
            rx_beg_y + rx_max_y - 2, rx_beg_x + rx_max_x - 2 # Bottom-right interior
        )

    def update_hex_sidebar(self):
        self.hex_win.erase()
        h, w = self.hex_win.getmaxyx()

        # Title
        self.hex_win.addstr(0, 1, "HEX", curses.A_UNDERLINE)

        # Show the most recent typed characters starting from the bottom or filling down
        recent_hex = list(self.hex_history)[-(h - 2):]
        for idx, hex_val in enumerate(recent_hex):
            if idx + 1 < h:
                self.hex_win.addstr(idx + 1, 2, f"0x{hex_val}")

        self.hex_win.noutrefresh()

    def run(self):
        # Stage and flush all panels to the physical screen before any
        # blocking operation. The getch() with zero timeout forces a
        # terminal-I/O cycle so the output is actually displayed.
        self.update_tx_display()
        self.update_rx_display()
        self.update_hex_sidebar()
        self.refresh_screen()
        self.stdscr.timeout(0)
        self.stdscr.getch()
        self.stdscr.timeout(20)

        self.connect_serial()

        # connect_serial triggers add_rx_line, which only stages RX.
        # Re-stage all panels so TX and hex are not left stale.
        self.update_tx_display()
        self.update_rx_display()
        self.update_hex_sidebar()
        self.refresh_screen()

        rx_buffer = bytearray()


        while True:
            # 1. Handle incoming Data (Rx)

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

            # 2. Handle Key Input (Tx)
            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                break
            except curses.error:
                continue

            if ch == curses.KEY_RESIZE:
                self.resize_windows()

                continue

            if ch == curses.KEY_UP:
                self.rx_pos += 1


                self.update_rx_display()
                self.refresh_screen()

            if ch == curses.KEY_DOWN:
                self.rx_pos -= 1
                if self.rx_pos < 0:
                    self.rx_pos = 0

                self.update_rx_display()
                self.refresh_screen()

            if ch != -1:
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
                        # Unrecognized command — hide the [Cmd] indicator
                        self.refresh_screen()
                    continue

                # Ctrl+T enters command mode
                if ch == 20:
                    self.command_pending = True
                    self.refresh_screen()
                    continue

                # Check for exit (Ctrl+C or ESC)
                if ch in [3, 27]:
                    break

                # Enter Key -> Send Line
                elif ch in [10, 13, curses.KEY_ENTER]:
                    if self.current_input:
                        # Append the hex code for the Enter key itself
                        self.hex_history.append(f"{ch:02X}")

                        self.add_tx_line(self.current_input)
                        try:
                            self.ser.write((self.current_input + "\r\n").encode('utf-8'))
                        except Exception as e:
                            self.add_tx_line(f"TX ERROR: {e}")

                        self.current_input = ""
                        self.update_tx_display()
                        self.update_hex_sidebar()
                        self.refresh_screen()

                # Backspace Key
                elif ch in [8, 127, curses.KEY_BACKSPACE]:
                    if len(self.current_input) > 0:
                        self.current_input = self.current_input[:-1]
                        self.update_tx_display()
                        self.refresh_screen()

                # Printable characters
                elif 32 <= ch <= 126:
                    char_str = chr(ch)
                    self.current_input += char_str
                    # Log the character's hex value to the sidebar
                    self.hex_history.append(f"{ch:02X}")

                    self.update_tx_display()
                    self.update_hex_sidebar()
                    self.refresh_screen()
                else:
                    pass

def find_serial_ports():
    # Search patterns for USB and ARM/AMBA serial ports in /dev
    patterns = ['/dev/ttyUSB*', '/dev/ttyAMA*']
    
    found_ports = []
    for pattern in patterns:
        # glob.glob finds all pathnames matching a specified pattern
        found_ports.extend(glob.glob(pattern))
        
    return found_ports


def main():
    parser = argparse.ArgumentParser(description="sst - Simple Split Terminal")
    parser.add_argument("port", nargs="?", default="none", help="Serial port (e.g. /dev/ttyUSB0, COM3). Use 'sim' for demo.")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("-s", "--split", choices=["vertical", "horizontal"], default="vertical",
                        help="Split direction (default: vertical)")
    args = parser.parse_args()

    if args.port == "none":
        print("Error: No serial port specified.")

        print("Available serial ports:")
        for port in find_serial_ports():
            print(f"  {port}")

        return

    # Wrapper handles proper terminal setup/teardown automatically
    curses.wrapper(lambda stdscr: SimpleSplitTerminal(stdscr, args.port, args.baud, args.split).run())

if __name__ == "__main__":
    main()

