from textual.widgets._label import Label
import colorama
from httpx import __name
from ollama import chat, ChatResponse, Client
import argparse
import subprocess
import os

def main():
    client = Client()
    models = sorted(client.list().models, key=lambda m:m.size, reverse=False)
    diff = subprocess.run(["git", "diff", "HEAD", "-U0"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    commits = subprocess.run(["git", "log", "-n", "5", "--oneline"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    
    prompt_content = f"Recent Commits (For Context Only):\n{commits}\n\nDiff to summarize:\n```diff\n{diff}\n```"
    app = CometTUI(commit="Generating...", model=models[0].model, diff=diff, commits=commits)
    result = app.run()
    if result: print(result)

from textual.app import App, ComposeResult
from textual.widgets import TextArea, Button, Label
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import work, events
import subprocess, colorama

class CustomTextArea(TextArea):
    Binding("ctrl+r", "regenerate_action", "Regenerate", priority=True),
    Binding("ctrl+t", "exit_action", "Terminate", priority=True)

    def on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            self.app.action_commit_action()
            event.prevent_default()

    def action_cursor_down(self, select: bool = False) -> None:
        if self.cursor_location[0] == self.document.line_count - 1:
            loc = self.cursor_location
            self.cursor_location = (self.document.line_count - 1, len(self.document.get_line(self.document.line_count - 1)))
            self.insert("\n")
            self.cursor_location = (loc[0] + 1, min(loc[1], len(self.document.get_line(loc[0] + 1))))
        else:
            super().action_cursor_down(select=select)

    def action_cursor_up(self, select: bool = False) -> None:
        row = self.cursor_location[0]
        if row > 0 and row == self.document.line_count - 1 and len(self.document.get_line(row)) == 0:
            self.action_delete_left()
        else:
            super().action_cursor_up(select=select)

class CometTUI(App):
    DEFAULT_CSS = """
    Screen {
        width: 100%;
        height: 100%;
        align: center middle;
        background: $surface;
    }

    Static {
        background: $surface;
    }

    Button {
        border: none;
    }
    
    Button:focus {
        border: none;
    }
    
    Button:hover {
        border: none;
    }

    #main_container {
        width: 100%;
        height: 100%;
        padding: 1 2;
        background: $surface;
    }

    #input_row {
        height: auto;
        padding: 1;
        background: $surface;
        border: round $primary;
    }

    #input {
        width: 1fr;
        height: 3;
        border: none;
        background: transparent;
    }

    #input:focus {
        border: none;
    }

    #regenBtn {
        height: 3;
        margin-left: 1;
        background: $primary;
        border: none;
    }

    #action_row {
        height: auto;
        margin-top: 1;
    }

    #commitBtn {
        width: 1fr;
        height: 3;
        background: $primary;
        border: none;
    }

    #cancelBtn {
        margin-left: 1;
        height: 3;
        background: $secondary;
        border: none;
    }

    #shortcuts {
        width: 100%;
        text-align: center;
        color: white;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+r", "regenerate_action", "Regenerate", priority=True),
        Binding("ctrl+t", "exit_action", "Terminate", priority=True),
        Binding("ctrl+z", "undo_commit", "Undo Commit", priority=True)
    ]

    def __init__(self, commit: str, model: str, diff: str, commits: str):
        super().__init__()
        self.commit = commit
        self.model = model
        self.diff = diff
        self.commits = commits

    def compose(self) -> ComposeResult:
        with Vertical(id="main_container"):
            with Horizontal(id="input_row"):
                yield CustomTextArea(self.commit, id="input", show_line_numbers=False)
                yield Button(" ₊✦  Regenerate  ", id="regenBtn")
            with Horizontal(id="action_row"):
                yield Button(" ✔   Commit", id="commitBtn")
                yield Button(" 🗙   Terminate", id="cancelBtn")
            yield Label("[white][b]ctrl+r[/b][/white] [gray]regenerate[/gray]    [white][b]enter[/b][/white] [gray]continue[/gray]    [white][b]ctrl+z[/b][/white] [gray]undo[/gray]    [white][b]↓/↑[/b][/white] [gray]move lines[/gray]    [white][b]ctrl+t[/b][/white] [gray]terminate[/gray]", id="shortcuts")

    def action_regenerate_action(self) -> None:
        regen_btn = self.query_one("#regenBtn", Button)
        if not regen_btn.disabled:
            regen_btn.press()

    def action_exit_action(self) -> None:
        self.query_one("#cancelBtn", Button).press()

    def action_commit_action(self) -> None:
        commit_btn = self.query_one("#commitBtn", Button)
        if not commit_btn.disabled:
            commit_btn.press()

    def action_undo_commit(self) -> None:
        commit_btn = self.query_one("#commitBtn", Button)
        if str(commit_btn.label).strip() == "Sync  ➤":
            subprocess.run(["git", "reset", "HEAD~1"], capture_output=True)
            commit_btn.label = " ✔   Commit"

    def on_mount(self) -> None:
        self.query_one("#input_row").border_title = "Comet CLI"
        self.query_one("#regenBtn").disabled = True
        self.regenerate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "commitBtn":
            if str(event.button.label).strip() == "Sync  ➤":
                event.button.label = "Syncing..."
                event.button.disabled = True
                subprocess.run(["git", "push"], capture_output=True)
                self.exit(f"{colorama.Fore.GREEN}Comet committed and synced successfully! {colorama.Style.RESET_ALL}")
                return

            text_area = self.query_one("#input", TextArea)
            final_message = text_area.text.strip()
            
            subprocess.run(["git", "commit", "-a", "-m", final_message], capture_output=True)
            event.button.label = "Sync  ➤"
            
        elif event.button.id == "cancelBtn":
            self.exit(f"{colorama.Fore.RED}User cancelled the operation. {colorama.Style.RESET_ALL}")
            
        elif event.button.id == "regenBtn":
            event.button.disabled = True
            text_area = self.query_one("#input", TextArea)
            text_area.text = ""
            self.regenerate()

    @work(thread=True)
    def regenerate(self) -> None:
        if not hasattr(self, "past_responses"):
            self.past_responses = set()
            
        prompt_content = f"Diff to summarize:\n```diff\n{self.diff}\n```\n\nRecent Commits (For Context Only. DO NOT SUMMARIZE THESE):\n{self.commits}"
        
        while True:
            messages = [{"role": "system", "content": open("comet\system.md","r", encoding="utf-8").read()}]
            messages.append({"role": "user", "content": prompt_content})
            
            for past_response in self.past_responses:
                messages.append({"role": "assistant", "content": past_response})
                messages.append({"role": "user", "content": "Please provide a DIFFERENT summary. Do not repeat the previous ones."})
                
            response = chat(model=self.model, messages=messages, options={"temperature": 0.9}, think=False, keep_alive=-1, stream=True)
            message = ""
            for chunk in response:
                message += chunk['message']['content']
                self.call_from_thread(self.update_textarea, message, False)
            
            if message not in self.past_responses:
                self.past_responses.add(message)
                self.call_from_thread(self.update_textarea, message, True)
                break

    def update_textarea(self, message: str, finished: bool) -> None:
        text_area = self.query_one("#input", TextArea)
        text_area.text = message
        if finished:
            self.query_one("#regenBtn").disabled = False

if __name__ == "__main__": main()