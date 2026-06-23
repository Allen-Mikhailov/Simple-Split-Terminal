import curses
import sys
import argparse
import time
from datetime import datetime
from collections import deque
import glob
import serial

class SimpleSplitTerminal:
    def __init__(self, stdscr, port, baudrate):
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

        self.split = "vertical"
        
        # Setup colors and screen
        curses.use_default_colors()
        curses.curs_set(1) # Show cursor
        self.stdscr.timeout(20) # Non-blocking input delay (ms)
        
        # Init layout dimensions
        self.resize_windows()

    def resize_windows(self):
        self.height, self.width = self.stdscr.getmaxyx()
        
        # Geometry definitions
        self.sidebar_width = 8
        self.main_width = self.width - self.sidebar_width - 1
        self.split_row = self.height // 2
        self.split_col = self.main_width // 2
        
        # Sub-window bounds checking to prevent crashes on tiny screens
        if self.height < 10 or self.width < 30:
            raise Exception("Terminal screen too small!")

        # Clear main screen
        self.stdscr.clear()
        
        # Create windows: newwin(height, width, begin_y, begin_x)
        if self.split == "vertical":
            self.rx_win = curses.newwin(self.height, self.split_col, 0, 0)
            self.tx_win = curses.newwin(self.height, self.main_width - self.split_col - 1, 0, self.split_col + 1)
        else:
            self.rx_win = curses.newwin(self.split_row, self.main_width, 0, 0)
            self.tx_win = curses.newwin(self.height - self.split_row - 1, self.main_width, self.split_row + 1, 0)



        self.hex_win = curses.newwin(self.height, self.sidebar_width, 0, self.main_width + 1)
        
        # Enable scrolling for output windows
        # self.rx_win.scrollok(True)
        
    def draw_borders(self):
        
        try:
            if self.split == "vertical":
                # Draw vertical split line
                self.stdscr.vline(0, self.split_col, curses.ACS_VLINE, self.height)
            else:
                # Draw horizontal split line
                self.stdscr.hline(self.split_row, 0, curses.ACS_HLINE, self.main_width)

            # Draw vertical sidebar line
            self.stdscr.vline(0, self.main_width, curses.ACS_VLINE, self.height)
            # Intersection joint
            self.stdscr.addch(self.split_row, self.main_width, curses.ACS_PLUS)
            self.stdscr.refresh()
        except curses.error:
            pass

    def connect_serial(self):
        while not self.connected:
            try:
                self.ser = serial.Serial(self.port, self.baudrate, timeout=0.05)
                self.add_rx_line(f"SYSTEM: Connected to {self.port} at {self.baudrate} baud.")
                self.connected = True
            except Exception as e:
                self.add_rx_line(f"ERROR: Could not open {self.port}: {e}")
                time.sleep(500)  # Wait before retrying

    def add_rx_line(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        formatted_line = f"[{timestamp}] {text}"
        self.rx_lines.append(formatted_line)

        self.update_rx_display()

    def add_tx_line(self, text):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        formatted_line = f"[{timestamp}] {text}"
        self.tx_lines.append(formatted_line)
        
        # Write to window & scroll
        self.tx_win.addstr(formatted_line + "\n")
        self.tx_win.refresh()

    def update_rx_display(self):
        self.rx_win.clear()
        max_rx_h, max_rx_w = self.rx_win.getmaxyx()

        line_count = len(self.rx_lines)

        for i in self.rx_lines[line_count - max_rx_h + 1-self.rx_pos:line_count-self.rx_pos]:
            self.rx_win.addstr(i + "\n")

        self.rx_win.refresh()

    def update_tx_display(self):
        self.tx_win.clear()
        max_tx_h, max_tx_w = self.tx_win.getmaxyx()
        
        # Display Prompt
        prompt = "TX > "
        self.tx_win.addstr(0, 0, prompt, curses.A_BOLD)
        
        # Display typed text (truncated if it exceeds window width)
        available_width = max_tx_w - len(prompt) - 1
        visible_input = self.current_input[-available_width:]
        self.tx_win.addstr(0, len(prompt), visible_input)
        
        # Move cursor to end of text
        self.tx_win.move(0, len(prompt) + len(visible_input))
        self.tx_win.refresh()

    def update_hex_sidebar(self):
        self.hex_win.clear()
        h, w = self.hex_win.getmaxyx()
        
        # Title
        self.hex_win.addstr(0, 1, "HEX", curses.A_UNDERLINE)
        
        # Show the most recent typed characters starting from the bottom or filling down
        recent_hex = list(self.hex_history)[-(h - 2):]
        for idx, hex_val in enumerate(recent_hex):
            if idx + 1 < h:
                self.hex_win.addstr(idx + 1, 2, f"0x{hex_val}")
        
        self.hex_win.refresh()

    def run(self):
        self.connect_serial()
        self.draw_borders()
        self.update_tx_display()
        self.update_hex_sidebar()
        
        rx_buffer = bytearray()
        
        while True:
            # 1. Handle incoming Data (Rx)
            if self.ser and self.ser.in_waiting > 0:
                try:
                    data = self.ser.read(self.ser.in_waiting)
                    for byte in data:
                        if byte == ord('\n'):
                            self.add_rx_line(rx_buffer.decode('utf-8', errors='replace').strip())
                            rx_buffer.clear()
                        elif byte != ord('\r'):
                            rx_buffer.append(byte)
                except Exception as e:
                    self.add_rx_line(f"SERIAL ERROR: {e}")

            # 2. Handle Key Input (Tx)
            ch = self.stdscr.getch()
            
            if ch == curses.KEY_RESIZE:
                self.resize_windows()
                self.draw_borders()
                self.update_tx_display()
                self.update_hex_sidebar()
                continue

            if ch == curses.KEY_UP:
                self.rx_pos += 1
                self.stdscr.addstr(2, 0, f"Scrolling up. Position: {self.rx_pos}")
                self.stdscr.refresh()
                self.update_rx_display()

            if ch == curses.KEY_DOWN:
                self.rx_pos -= 1
                if self.rx_pos < 0:
                    self.rx_pos = 0

                self.update_rx_display()

            if ch == curses.KEY_MOUSE:
                try:
                    # 3. Retrieve the mouse event tuple
                    # _, x, y, _, bstate = curses.getmouse()
                    mouse_id, x, y, z, bstate = curses.getmouse()
                    
                    # 4. Check bitmasks for scroll up/down
                    # Note: Exact button states can vary slightly by terminal emulator
                    if bstate & curses.BUTTON4_PRESSED:
                        self.rx_win.scrl(1)
                    elif bstate & curses.BUTTON5_PRESSED: # Often button 5 or 4-shifted
                        self.rx_win.scrl(-1)
                    else:
                        # Catch-all for other terminal-specific scroll masks
                        self.stdscr.addstr(2, 0, f"Mouse action detected. Mask: {hex(bstate)}")
                        
                    self.stdscr.refresh()
                except curses.error:
                    pass
                
            if ch != -1:
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

                # Backspace Key
                elif ch in [8, 127, curses.KEY_BACKSPACE]:
                    if len(self.current_input) > 0:
                        self.current_input = self.current_input[:-1]
                        self.update_tx_display()

                # Printable characters
                elif 32 <= ch <= 126:
                    char_str = chr(ch)
                    self.current_input += char_str
                    # Log the character's hex value to the sidebar
                    self.hex_history.append(f"{ch:02X}")
                    
                    self.update_tx_display()
                    self.update_hex_sidebar()
                else:
                    # self.stdscr.addstr(2, 0, f"Unhandled key: {ch}")
                    # self.stdscr.refresh()
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
    args = parser.parse_args()

    if args.port == "none":
        print("Error: No serial port specified.")

        print("Available serial ports:")
        for port in find_serial_ports():
            print(f"  {port}")

        return

    # Wrapper handles proper terminal setup/teardown automatically
    curses.wrapper(lambda stdscr: SimpleSplitTerminal(stdscr, args.port, args.baud).run())

if __name__ == "__main__":
    main()

