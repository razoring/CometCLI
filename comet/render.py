import os
import sys
import time
import queue
import threading

if os.name == 'nt':
    import msvcrt
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)
    mode = ctypes.c_ulong()
    kernel32.GetConsoleMode(handle, ctypes.byref(mode))
    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
else:
    import tty
    import termios
    import select

class Event:
    def __init__(self, type, **kwargs):
        self.type = type
        self.data = kwargs

class Widget:
    def __init__(self, id=None):
        self.id = id
        self.focused = False
        self.disabled = False
        self.x = 1
        self.y = 1
        self.width = 10
        self.height = 1

    def render(self):
        return []

    def handle_event(self, event):
        pass

class Label(Widget):
    def __init__(self, text, id=None, style="\033[38;5;250m"):
        super().__init__(id)
        self.text = text
        self.style = style

    def render(self):
        lines = self.text.split("\n")
        out = []
        for i, line in enumerate(lines):
            out.append((self.x, self.y + i, f"{self.style}{line}\033[0m"))
        return out

class Button(Widget):
    def __init__(self, label, id=None, action=None, style_normal="\033[38;5;250m", style_focus="\033[48;5;25m\033[37m"):
        super().__init__(id)
        self.label = label
        self.action = action
        self.style_normal = style_normal
        self.style_focus = style_focus
        self.width = len(label)

    def render(self):
        st = self.style_focus if self.focused else self.style_normal
        if self.disabled:
            st = "\033[38;5;238m" # dark grey
        return [(self.x, self.y, f"{st}{self.label}\033[0m")]

    def handle_event(self, event):
        if event.type == "keypress" and event.data["key"] == "enter" and not self.disabled:
            if self.action:
                self.action()

class TextArea(Widget):
    def __init__(self, text="", id=None):
        super().__init__(id)
        self.lines = text.split("\n")
        if not self.lines:
            self.lines = [""]
        self.cursor_y = 0
        self.cursor_x = len(self.lines[0])
        self.scroll_y = 0

    @property
    def text(self):
        return "\n".join(self.lines)

    @text.setter
    def text(self, val):
        self.lines = val.split("\n") if val else [""]
        self.cursor_y = max(0, min(self.cursor_y, len(self.lines) - 1))
        self.cursor_x = max(0, min(self.cursor_x, len(self.lines[self.cursor_y])))
        self.scroll_y = max(0, self.cursor_y - self.height + 1)

    def insert(self, char):
        line = self.lines[self.cursor_y]
        self.lines[self.cursor_y] = line[:self.cursor_x] + char + line[self.cursor_x:]
        self.cursor_x += len(char)

    def newline(self):
        line = self.lines[self.cursor_y]
        self.lines[self.cursor_y] = line[:self.cursor_x]
        self.lines.insert(self.cursor_y + 1, line[self.cursor_x:])
        self.cursor_y += 1
        self.cursor_x = 0

    def backspace(self):
        if self.cursor_x > 0:
            line = self.lines[self.cursor_y]
            self.lines[self.cursor_y] = line[:self.cursor_x-1] + line[self.cursor_x:]
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            line = self.lines[self.cursor_y]
            prev_len = len(self.lines[self.cursor_y - 1])
            self.lines[self.cursor_y - 1] += line
            self.lines.pop(self.cursor_y)
            self.cursor_y -= 1
            self.cursor_x = prev_len

    def delete(self):
        line = self.lines[self.cursor_y]
        if self.cursor_x < len(line):
            self.lines[self.cursor_y] = line[:self.cursor_x] + line[self.cursor_x+1:]
        elif self.cursor_y < len(self.lines) - 1:
            next_line = self.lines[self.cursor_y + 1]
            self.lines[self.cursor_y] += next_line
            self.lines.pop(self.cursor_y + 1)

    def handle_event(self, event):
        if event.type != "keypress": return
        key = event.data["key"]
        
        if key == "up":
            if self.cursor_y > 0:
                self.cursor_y -= 1
                self.cursor_x = min(self.cursor_x, len(self.lines[self.cursor_y]))
        elif key == "down":
            if self.cursor_y < len(self.lines) - 1:
                self.cursor_y += 1
                self.cursor_x = min(self.cursor_x, len(self.lines[self.cursor_y]))
            else:
                self.cursor_x = len(self.lines[self.cursor_y])
                self.newline()
        elif key == "left" and self.cursor_x > 0:
            self.cursor_x -= 1
        elif key == "right" and self.cursor_x < len(self.lines[self.cursor_y]):
            self.cursor_x += 1
        elif key == "backspace":
            self.backspace()
        elif key == "delete":
            self.delete()
        elif key == "enter":
            self.newline()
        elif len(key) == 1:
            self.insert(key)
        
        if self.cursor_y < self.scroll_y:
            self.scroll_y = self.cursor_y
        elif self.cursor_y >= self.scroll_y + self.height:
            self.scroll_y = self.cursor_y - self.height + 1

    def render(self):
        output = []
        for i in range(self.height):
            line_idx = self.scroll_y + i
            if line_idx < len(self.lines):
                text = self.lines[line_idx]
                if len(text) > self.width:
                    text = text[:self.width]
                output.append((self.x, self.y + i, f"\033[38;5;250m{text}\033[0m\033[K"))
            else:
                output.append((self.x, self.y + i, "\033[K"))
        return output

class EventLoop:
    def __init__(self):
        self.queue = queue.Queue()
        self.running = False
        self._input_thread = None

    def _windows_input(self):
        while self.running:
            if msvcrt.kbhit():
                c = msvcrt.getwch()
                if c in ('\x00', '\xe0'):
                    code = msvcrt.getwch()
                    key_map = {'H': 'up', 'P': 'down', 'K': 'left', 'M': 'right', 'S': 'delete'}
                    if code in key_map:
                        self.queue.put(Event("keypress", key=key_map[code]))
                elif c == '\r': self.queue.put(Event("keypress", key="enter"))
                elif c == '\x08': self.queue.put(Event("keypress", key="backspace"))
                elif c == '\t': self.queue.put(Event("keypress", key="tab"))
                elif c == '\x1b': self.queue.put(Event("keypress", key="escape"))
                elif c == '\x12': self.queue.put(Event("keypress", key="ctrl+r"))
                elif c == '\x1a': self.queue.put(Event("keypress", key="ctrl+z"))
                elif c == '\x03': self.queue.put(Event("keypress", key="escape"))
                else: self.queue.put(Event("keypress", key=c))
            else:
                time.sleep(0.01)

    def _unix_input(self):
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(sys.stdin.fileno())
            while self.running:
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    c = sys.stdin.read(1)
                    if c == '\x1b':
                        r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                        if r2:
                            c2 = sys.stdin.read(1)
                            if c2 == '[':
                                c3 = sys.stdin.read(1)
                                key_map = {'A': 'up', 'B': 'down', 'C': 'right', 'D': 'left'}
                                if c3 == '3':
                                    sys.stdin.read(1)
                                    self.queue.put(Event("keypress", key="delete"))
                                elif c3 in key_map:
                                    self.queue.put(Event("keypress", key=key_map[c3]))
                        else:
                            self.queue.put(Event("keypress", key="escape"))
                    elif c == '\n' or c == '\r': self.queue.put(Event("keypress", key="enter"))
                    elif c == '\x7f' or c == '\x08': self.queue.put(Event("keypress", key="backspace"))
                    elif c == '\t': self.queue.put(Event("keypress", key="tab"))
                    elif c == '\x12': self.queue.put(Event("keypress", key="ctrl+r"))
                    elif c == '\x1a': self.queue.put(Event("keypress", key="ctrl+z"))
                    elif c == '\x03': self.queue.put(Event("keypress", key="escape"))
                    else: self.queue.put(Event("keypress", key=c))
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def start(self):
        self.running = True
        target = self._windows_input if os.name == 'nt' else self._unix_input
        self._input_thread = threading.Thread(target=target, daemon=True)
        self._input_thread.start()

    def stop(self):
        self.running = False

class CustomTUI:
    def __init__(self):
        self.widgets = {}
        self.tab_order = []
        self.focused_idx = 0
        self.loop = EventLoop()
        self.term_width, self.term_height = 80, 24
        self.running = False
        self.border_title = ""

    def on_mount(self):
        pass

    def update_layout(self):
        try:
            self.term_width, self.term_height = os.get_terminal_size()
        except OSError:
            pass
        self._compose_layout()

    def _compose_layout(self):
        pass

    def draw(self):
        out = ["\033[?25l"]
        
        # Draw border title for input row if any
        if self.border_title and "input" in self.widgets:
            input_w = self.widgets["input"]
            border_y = max(1, input_w.y - 1)
            # draw border
            out.append(f"\033[{border_y};{input_w.x}H\033[38;5;238m┌─ {self.border_title} {'─'*(self.term_width-len(self.border_title)-6)}┐\033[0m")
            for i in range(input_w.height):
                out.append(f"\033[{input_w.y+i};{input_w.x-2}H\033[38;5;238m│\033[0m")
                out.append(f"\033[{input_w.y+i};{self.term_width}H\033[38;5;238m│\033[0m")
            out.append(f"\033[{input_w.y+input_w.height};{input_w.x-2}H\033[38;5;238m└{'─'*(self.term_width-4)}┘\033[0m")

        for w in self.widgets.values():
            for x, y, text in w.render():
                if y <= self.term_height:
                    out.append(f"\033[{y};{x}H{text}")
        
        if self.tab_order and self.focused_idx < len(self.tab_order):
            focused_w = self.widgets[self.tab_order[self.focused_idx]]
            if isinstance(focused_w, TextArea) and not focused_w.disabled:
                cy = focused_w.y + focused_w.cursor_y - focused_w.scroll_y
                cx = focused_w.x + focused_w.cursor_x
                if focused_w.y <= cy < focused_w.y + focused_w.height:
                    out.append(f"\033[{cy};{cx}H\033[?25h")
        
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def exit(self, message=""):
        self.running = False
        self.loop.stop()
        sys.stdout.write("\033[2J\033[H\033[?25h")
        sys.stdout.flush()
        if message:
            print(message)

    def run(self):
        self.update_layout()
        sys.stdout.write("\033[2J\033[H")
        self.loop.start()
        self.running = True
        self.on_mount()

        while self.running:
            try:
                ev = self.loop.queue.get(timeout=0.05)
                if ev.type == "keypress":
                    if ev.data["key"] == "tab":
                        if self.tab_order:
                            self.widgets[self.tab_order[self.focused_idx]].focused = False
                            # find next non-disabled widget
                            original_idx = self.focused_idx
                            while True:
                                self.focused_idx = (self.focused_idx + 1) % len(self.tab_order)
                                if not self.widgets[self.tab_order[self.focused_idx]].disabled:
                                    break
                                if self.focused_idx == original_idx: break
                            self.widgets[self.tab_order[self.focused_idx]].focused = True
                    elif ev.data["key"] == "escape":
                        self.exit("\033[31mUser cancelled the operation. \033[0m")
                        break
                    else:
                        if self.tab_order:
                            self.widgets[self.tab_order[self.focused_idx]].handle_event(ev)
                        self.handle_global_event(ev)
                elif ev.type == "call_from_thread":
                    func, args, kwargs = ev.data["func"], ev.data["args"], ev.data["kwargs"]
                    func(*args, **kwargs)
            except queue.Empty:
                pass
            
            if self.running:
                self.update_layout()
                self.draw()

    def call_from_thread(self, func, *args, **kwargs):
        self.loop.queue.put(Event("call_from_thread", func=func, args=args, kwargs=kwargs))

    def set_interval(self, interval, func):
        def _loop():
            while self.running:
                time.sleep(interval)
                if self.running:
                    self.call_from_thread(func)
        threading.Thread(target=_loop, daemon=True).start()

    def handle_global_event(self, event):
        pass
    
    def notify(self, message, title="", severity="information", timeout=3.0):
        # We will render it at the bottom above the shortcuts
        self.border_title = f"{title}: {message}"
