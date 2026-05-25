import colorama
from ollama import chat as ollama_chat, Client as OllamaClient
from openai import OpenAI
import argparse
import subprocess
import os
import json
import urllib.request
import urllib.error
import importlib.metadata
import re
import random
from pydantic import BaseModel, Field

class CommitResponse(BaseModel):
    commit_message: str = Field(..., max_length=150, description="The concise commit message. DO NOT output diffs. STRICTLY limit to 150 characters.")

def extract_json_message(buffer: str) -> str:
    match = re.search(r'"commit_message"\s*:\s*"(.*)', buffer, re.DOTALL)
    if match:
        text = match.group(1)
        text = text.rstrip(" \n\r\t}")
        if text.endswith('"'): text = text[:-1]
        return text.replace('\\n', '\n').replace('\\"', '"')

    if buffer.lstrip().startswith("{"): return ""
    return buffer

def get_settings_path():
    return os.path.join(os.path.dirname(__file__), "settings.json")

def load_settings():
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_settings(provider, model):
    path = get_settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"provider": provider, "model": model}, f)
    except Exception:
        pass

def check_endpoint(url):
    try:
        urllib.request.urlopen(url, timeout=1.0)
        return True
    except (urllib.error.URLError, ValueError):
        return False

def main():
    parser = argparse.ArgumentParser(description="Comet - AI commit message generator")
    parser.add_argument("--provider", choices=["auto", "ollama", "lmstudio"], default="auto", help="Choose AI provider")
    args = parser.parse_args()

    provider = args.provider
    model = ""
    if provider == "auto":
        settings = load_settings()
        if "provider" in settings:
            provider = settings["provider"]
        if "model" in settings:
            model = settings["model"]

    diff_args = [
        "git", "diff", "HEAD", "-U5", "--", ".",
        ":(exclude)package-lock.json",
        ":(exclude)yarn.lock",
        ":(exclude)pnpm-lock.yaml",
        ":(exclude)poetry.lock",
        ":(exclude)Cargo.lock",
        ":(exclude)Gemfile.lock",
        ":(exclude)uv.lock",
        ":(exclude)*.min.js",
        ":(exclude)*.min.css",
        ":(exclude)*.svg"
    ]
    diff = subprocess.run(diff_args, cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    status = subprocess.run(["git", "diff", "--name-status", "HEAD"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    commits = subprocess.run(["git", "log", "-n", "5", "--oneline"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    
    app = CometTUI(commit="Generating...", model=model, diff=diff, file_status=status, commits=commits, allModels=[], provider=provider, client=None)
    result = app.run()
    if result: print(result)

from textual.app import App, ComposeResult
from textual.widgets import TextArea, Button, Label
from textual.containers import Horizontal, Vertical
from textual.binding import Binding
from textual import work, events
import subprocess, colorama

class CustomTextArea(TextArea):
    BINDINGS = [
        Binding("ctrl+r", "regenerate_action", "Regenerate", priority=True),
        Binding("escape", "exit_action", "Terminate", priority=True)
    ]

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
        border: solid $panel;
        border-title-color: $text-muted;
    }

    #input_row:focus-within {
        border: solid $primary;
        border-title-color: $primary;
    }

    #input_row.committed {
        border: solid $panel;
        border-title-color: $text-muted;
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

    #undoBtn {
        width: 1fr;
        margin-left: 1;
        height: 3;
        background: $secondary;
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
        color: $text-muted;
        margin-top: 1;
    }

    #bottom_row {
        width: 100%;
        height: 1;
        margin-top: 1;
    }

    #cwd_label {
        width: 1fr;
        height: 1;
        color: $text-muted;
        padding-left: 1;
        overflow: hidden;
    }

    #settingsBtn {
        width: auto;
        height: 1;
        border: none;
        background: transparent;
        color: $text-muted;
        margin-right: 1;
    }

    #settingsBtn:hover {
        color: $text;
    }

    #logo {
        width: 100%;
        text-align: center;
        color: $primary;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+r", "regenerate_action", "Regenerate", priority=True),
        Binding("escape", "exit_action", "Quit", priority=True),
        Binding("ctrl+z", "undo_commit", "Undo Commit", priority=True),
        Binding("tab", "swap_model", "Swap Model", priority=True),
        Binding("enter", "commit_action", "Continue", priority=True)
    ]

    def __init__(self, commit: str, model: str, diff: str, file_status: str, commits: str, allModels: list[str], provider: str = "ollama", client = None):
        super().__init__()
        self.commit = commit
        self.model = model or ""
        self.diff = diff
        self.file_status = file_status
        self.commits = commits
        self.allModels = allModels
        self.provider = provider
        self.client = client
        self.is_generating = False
        self.needs_poll = True
        self._ollama_expires_at = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="main_container"):
            #ascii logo
            yield Label(""" ▄▄▄▄  ▄▄▄  ▄▄   ▄▄ ▄▄▄▄▄ ▄▄▄▄▄▄   ┌─┐┬  ┬\n██▀▀▀ ██▀██ ██▀▄▀██ ██▄▄    ██     │  │  │\n▀████ ▀███▀ ██   ██ ██▄▄▄   ██     └─┘┴─┘┴""", id="logo")
            with Horizontal(id="input_row"):
                yield CustomTextArea(self.commit, id="input", show_line_numbers=False)
                yield Button(" ₊✦  Regenerate  ", id="regenBtn")
            with Horizontal(id="action_row"):
                undo = Button(" ↺   Undo ", id="undoBtn")
                yield Button(" ✔   Commit ", id="commitBtn")
                undo.display = False
                yield undo
                yield Button(" ⛌   Quit ", id="cancelBtn")
            yield Label("[$text][b]ctrl+r[/b][/] regenerate    [$text][b]enter[/b][/] continue    [$text][b]tab[/b][/] swap model    [$text][b]ctrl+z[/b][/] undo    [$text][b]↓/↑[/b][/] move lines    [$text][b]esc[/b][/] quit", id="shortcuts")
            
            home = os.path.expanduser("~")
            cwd_raw = os.getcwd()
            cwd_path = "~" + cwd_raw[len(home):] if cwd_raw.startswith(home) else cwd_raw
            cwd_path = cwd_path.replace(os.sep, "/") + "/"
            
            with Horizontal(id="bottom_row"):
                yield Label(f" {cwd_path}", id="cwd_label")
                yield Button(" ⚙ Settings ", id="settingsBtn")

    def action_swap_model(self) -> None:
        if self.query_one("#input_row").has_class("committed"):
            return
        if not self.allModels: return
        try:
            currentIndex = self.allModels.index(self.model)
        except ValueError:
            currentIndex = -1
        nextIndex = (currentIndex + 1) % len(self.allModels)
        self.model = self.allModels[nextIndex]
        self.query_one("#input_row").border_title = f"{self.model}"
        save_settings(self.provider, self.model)

    def action_regenerate_action(self) -> None:
        regenBtn = self.query_one("#regenBtn", Button)
        if not regenBtn.disabled:
            regenBtn.press()

    def action_exit_action(self) -> None:
        self.exit(f"{colorama.Fore.RED}User cancelled the operation. {colorama.Style.RESET_ALL}")

    def action_commit_action(self) -> None:
        commitBtn = self.query_one("#commitBtn", Button)
        if not commitBtn.disabled:
            commitBtn.press()

    def action_undo_commit(self) -> None:
        commitBtn = self.query_one("#commitBtn", Button)
        if str(commitBtn.label).strip() == "Sync  ➤":
            subprocess.run(["git", "reset", "HEAD~1"], capture_output=True)
            commitBtn.label = " ✔   Commit "
            self.query_one("#undoBtn").display = False
            self.query_one("#cancelBtn").display = True
            
            textArea = self.query_one("#input", TextArea)
            textArea.disabled = False
            input_row = self.query_one("#input_row")
            input_row.remove_class("committed")
            self.query_one("#regenBtn").disabled = False

    def on_mount(self) -> None:
        if self.provider == "auto" or self.model == "":
            self.notify("Welcome! This is the longest it'll take to load. :D", title="Scanning Providers", severity="information", timeout=3.0)
        self.query_one("#input_row").border_title = f"{self.model}"
        self.query_one("#regenBtn").disabled = True
        self.query_one("#commitBtn").disabled = True
        self.set_interval(2.0, self.update_status_loop)
        
        try:
            from textual.color import Color
            v = self.get_css_variables()
            
            def resolve_color(val, default_hex):
                if val.startswith("auto") or not val: return default_hex
                return val
                
            # Default to dark mode colors (#1e1e1e surface, #ffffff text) if unresolved
            surface_str = resolve_color(v.get("surface", "#1E1E1E"), "#1E1E1E")
            text_str = resolve_color(v.get("text", "#ffffff"), "#ffffff")
            
            surface = Color.parse(surface_str)
            text = Color.parse(text_str)
            blended = surface.blend(text, 0.6)
            
            css = f"""
            #input_row {{ border: solid {blended.hex}; }}
            #input_row.committed {{ border: solid {blended.hex}; }}
            #input_row:focus-within {{ border: solid $primary; border-title-color: $primary; }}
            """
            self.stylesheet.add_source(css)
            self.stylesheet.update(self)
        except Exception:
            pass

        self.initialize_llm()
        self.check_for_updates()

    @work(thread=True)
    def check_for_updates(self) -> None:
        try:
            current_version = importlib.metadata.version("cli-comet")
            req = urllib.request.Request("https://pypi.org/pypi/cli-comet/json")
            with urllib.request.urlopen(req, timeout=2.0) as response:
                data = json.loads(response.read().decode())
                latest_version = data["info"]["version"]
                
                curr_tuple = tuple(map(int, current_version.split(".")))
                latest_tuple = tuple(map(int, latest_version.split(".")))
                
                if latest_tuple > curr_tuple:
                    self.call_from_thread(self.notify, f"Update available: v{latest_version}! Run `pipx upgrade cli-comet` to install.", title="Update Available", severity="warning", timeout=15.0)
        except Exception:
            pass

    @work(thread=True)
    def initialize_llm(self) -> None:
        if self.provider == "auto":
            lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
            ollamaUp = check_endpoint("http://localhost:11434/api/tags")
            if lmstudioUp and not ollamaUp:
                self.provider = "lmstudio"
            else:
                self.provider = "ollama"

        if self.provider == "ollama":
            self.client = OllamaClient()
            try:
                allModelsData = sorted(self.client.list().models, key=lambda m:m.size, reverse=False)
                self.allModels = [m.model for m in allModelsData]
                loadedModels = self.client.ps().models
                defaultModel = loadedModels[0].model if loadedModels else (self.allModels[0] if self.allModels else "unknown")
            except Exception:
                self.allModels = ["unknown"]
                defaultModel = "unknown"
        elif self.provider == "lmstudio":
            self.client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
            try:
                modelsData = self.client.models.list().data
                self.allModels = [m.id for m in modelsData]
                defaultModel = self.allModels[0] if self.allModels else "unknown"
            except Exception:
                self.allModels = ["unknown"]
                defaultModel = "unknown"

        if getattr(self, "model", "") in self.allModels and self.model != "Loading...":
            pass
        else:
            self.model = defaultModel

        save_settings(self.provider, self.model)
        
        self.call_from_thread(self.post_initialize_llm)

    def post_initialize_llm(self) -> None:
        self.query_one("#input_row").border_title = f"{self.model}"
        self.query_one("#commitBtn").disabled = False
        self.regenerate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "commitBtn":
            if str(event.button.label).strip() == "Sync  ➤":
                event.button.label = "Syncing..."
                event.button.disabled = True
                subprocess.run(["git", "push"], capture_output=True)
                self.exit(f"{colorama.Fore.GREEN}Comet committed and synced successfully! {colorama.Style.RESET_ALL}")
                return

            textArea = self.query_one("#input", TextArea)
            finalMessage = textArea.text.strip()
            
            subprocess.run(["git", "commit", "-a", "-m", finalMessage], capture_output=True)
            event.button.label = "Sync  ➤"
            self.query_one("#undoBtn").display = True
            self.query_one("#cancelBtn").display = False
            
            textArea.disabled = True
            input_row = self.query_one("#input_row")
            input_row.add_class("committed")
            self.query_one("#regenBtn").disabled = True
            
        elif event.button.id == "cancelBtn":
            self.exit(f"{colorama.Fore.RED}User cancelled the operation. {colorama.Style.RESET_ALL}")

        elif event.button.id == "undoBtn":
            self.action_undo_commit()
            
        elif event.button.id == "regenBtn":
            event.button.disabled = True
            textArea = self.query_one("#input", TextArea)
            textArea.text = ""
            self.regenerate()

    @work(thread=True)
    def regenerate(self) -> None:
        self.is_generating = True
        self.needs_poll = True
        if not hasattr(self, "pastResponses"):
            self.pastResponses = set()
            
        systemPath = os.path.join(os.path.dirname(__file__), "system.md")
        systemPrompt = open(systemPath, "r", encoding="utf-8").read()
        systemPrompt += f"\n\nRecent Commits (For Context Only. DO NOT SUMMARIZE THESE. They are just for tone/style reference):\n{self.commits}"
        
        promptContent = f"Files changed:\n{self.file_status}\n\nDiff to summarize (may exclude minified/auto-generated files):\n```diff\n{self.diff}\n```"
        
        while True:
            messages = [{"role": "system", "content": systemPrompt}]
            messages.append({"role": "user", "content": promptContent})
            
            for pastResponse in self.pastResponses:
                messages.append({"role": "assistant", "content": pastResponse})
                messages.append({"role": "user", "content": "Please provide a DIFFERENT summary. Do not repeat the previous ones."})
                
            if self.provider == "ollama":
                response = ollama_chat(
                    model=self.model, 
                    messages=messages, 
                    options={"temperature": 0.9, "seed": random.randint(0, 1000000)}, 
                    think=False, 
                    keep_alive="60m", 
                    stream=True,
                    format=CommitResponse.model_json_schema()
                )
                buffer = ""
                for chunk in response:
                    buffer += chunk['message']['content']
                    self.call_from_thread(self.update_textarea, extract_json_message(buffer), False)
                message = extract_json_message(buffer)
            elif self.provider == "lmstudio":
                try:
                    response = self.client.chat.completions.create(
                        model=self.model, 
                        messages=messages, 
                        temperature=0.9, 
                        stream=True,
                        response_format={
                            "type": "json_schema", 
                            "json_schema": {"name": "CommitResponse", "schema": CommitResponse.model_json_schema(), "strict": True}
                        }
                    )
                except Exception:
                    # Fallback to json_object if json_schema is not supported by LMStudio version
                    messages[0]["content"] += "\nRespond with JSON: {\"commit_message\": \"...\"}"
                    response = self.client.chat.completions.create(
                        model=self.model, 
                        messages=messages, 
                        temperature=0.9, 
                        stream=True,
                        response_format={"type": "json_object"}
                    )
                
                buffer = ""
                for chunk in response:
                    if chunk.choices[0].delta.content is not None:
                        buffer += chunk.choices[0].delta.content
                        self.call_from_thread(self.update_textarea, extract_json_message(buffer), False)
                message = extract_json_message(buffer)
            
            if message not in self.pastResponses:
                self.pastResponses.add(message)
                self.call_from_thread(self.update_textarea, message, True)
                break

    def update_textarea(self, message: str, finished: bool) -> None:
        textArea = self.query_one("#input", TextArea)
        textArea.text = message
        if finished:
            self.query_one("#regenBtn").disabled = False
            self.is_generating = False
            if self.provider == "ollama":
                from datetime import datetime, timedelta
                self._ollama_expires_at[self.model] = datetime.now() + timedelta(minutes=60)
            self.needs_poll = False

    @work(thread=True)
    def update_status_loop(self) -> None:
        from datetime import datetime
        if not getattr(self, "model", ""):
            self.call_from_thread(self.update_border_title, "")
            return

        if self.provider != "ollama":
            self.call_from_thread(self.update_border_title, f"{self.model}")
            return

        status = ""
        expires_at = self._ollama_expires_at.get(self.model)
        now = datetime.now(expires_at.tzinfo) if (expires_at and expires_at.tzinfo) else datetime.now()
        secs = (expires_at - now).total_seconds() if expires_at else -1
        
        if expires_at and secs <= 0:
            self.needs_poll = True
            expires_at = None
            secs = -1

        if getattr(self, "needs_poll", False) and not self.is_generating:
            try:
                import ollama
                ps = ollama.ps()
                models = getattr(ps, 'models', []) if hasattr(ps, 'models') else ps.get('models', [])
                self._ollama_expires_at.clear()
                for m in models:
                    m_name = getattr(m, 'model', m.get('model', '')) if hasattr(m, 'model') else m.get('model', '')
                    m_expires = getattr(m, 'expires_at', None) if hasattr(m, 'expires_at') else m.get('expires_at')
                    self._ollama_expires_at[m_name] = m_expires
                    if getattr(m, 'name', ''):
                        self._ollama_expires_at[getattr(m, 'name', '')] = m_expires
                self.needs_poll = False
                
                expires_at = self._ollama_expires_at.get(self.model)
                now = datetime.now(expires_at.tzinfo) if (expires_at and expires_at.tzinfo) else datetime.now()
                secs = (expires_at - now).total_seconds() if expires_at else -1
            except Exception:
                pass

        if self.is_generating and (expires_at is None or secs <= 0):
            status = "Loading..."
        elif expires_at and secs > 0:
            mins = int(secs // 60)
            status = f"TTL: {mins}m" if mins > 0 else "TTL: <1m"
        else:
            status = ""

        if status:
            self.call_from_thread(self.update_border_title, f"{self.model} ㆍ {status}")
        else:
            self.call_from_thread(self.update_border_title, f"{self.model}")

    def update_border_title(self, title: str) -> None:
        try:
            self.query_one("#input_row").border_title = title
        except Exception:
            pass

if __name__ == "__main__": main()