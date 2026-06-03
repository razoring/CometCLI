import colorama
import argparse
import subprocess
import os
import json
import urllib.request
import urllib.error
import importlib.metadata
import re
import random

COMMIT_RESPONSE_SCHEMA = {
    "properties": {
        "commit_message": {
            "description": "The concise commit message. DO NOT output diffs. STRICTLY limit to 150 characters.",
            "maxLength": 150,
            "title": "Commit Message",
            "type": "string"
        }
    },
    "required": ["commit_message"],
    "title": "CommitResponse",
    "type": "object"
}

def extract_json_message(buffer: str) -> str:
    match = re.search(r'"commit_message"\s*:\s*"(.*)', buffer, re.DOTALL)
    if match:
        text = match.group(1)
        text = text.rstrip(" \n\r\t}")
        if text.endswith('"'): text = text[:-1]
        text = text.replace('\\n', '\n').replace('\\"', '"')
        if '\n' in text:
            #discard everything after newline
            text = text.split('\n')[0]
        return text

    if buffer.lstrip().startswith("{"): return ""
    if '\n' in buffer:
        #discard everything after newline
        buffer = buffer.split('\n')[0]
    return buffer

def get_settings_path():
    return os.path.join(os.path.dirname(__file__), "settings.json")

def load_settings():
    path = get_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                if "quickStartup" not in settings:
                    settings["quickStartup"] = True
                return settings
        except Exception:
            pass
    return {"quickStartup": True}

def save_settings(provider, model):
    path = get_settings_path()
    try:
        settings = load_settings()
        settings["provider"] = provider
        settings["model"] = model
        if "openrouter_api_key" not in settings:
            settings["openrouter_api_key"] = ""
            
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
    except Exception:
        pass

def check_endpoint(url):
    try:
        urllib.request.urlopen(url, timeout=1.0)
        return True
    except (urllib.error.URLError, ValueError):
        return False

def headless_auto_commit(provider, model, diff, file_status, commits, do_commit=True, do_push=True):
    print(f"{colorama.Fore.CYAN}Generating auto-commit with {provider}...{colorama.Style.RESET_ALL}")
    
    client = None
    if provider == "auto":
        lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
        ollamaUp = check_endpoint("http://localhost:11434/api/tags")
        if lmstudioUp and not ollamaUp:
            provider = "lmstudio"
        else:
            provider = "ollama"

    if provider == "ollama":
        from ollama import Client as OllamaClient
        client = OllamaClient()
        try:
            allModelsData = sorted(client.list().models, key=lambda m:m.size, reverse=False)
            allModels = [m.model for m in allModelsData]
            loadedModels = client.ps().models
            defaultModel = loadedModels[0].model if loadedModels else (allModels[0] if allModels else "unknown")
        except Exception:
            allModels = ["unknown"]
            defaultModel = "unknown"
    elif provider == "lmstudio":
        from openai import OpenAI
        client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
        try:
            modelsData = client.models.list().data
            allModels = [m.id for m in modelsData]
            defaultModel = allModels[0] if allModels else "unknown"
        except Exception:
            allModels = ["unknown"]
            defaultModel = "unknown"
    elif provider == "openrouter":
        settings = load_settings()
        api_key = os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", "")
        from openai import OpenAI
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key or "missing_key")
        try:
            modelsData = client.models.list().data
            allModels = [m.id for m in modelsData]
            defaultModel = "openai/gpt-4o-mini" if "openai/gpt-4o-mini" in allModels else (allModels[0] if allModels else "unknown")
        except Exception:
            allModels = ["unknown"]
            defaultModel = "unknown"
    else:
        allModels = []
        defaultModel = "unknown"

    if model not in allModels or model == "":
        model = defaultModel
        
    save_settings(provider, model)
    print(f"{colorama.Fore.YELLOW}Model active: {model}{colorama.Style.RESET_ALL}")
    
    systemPath = os.path.join(os.path.dirname(__file__), "system.md")
    systemPrompt = open(systemPath, "r", encoding="utf-8").read()
    systemPrompt += f"\n\nRecent Commits (For Context Only. DO NOT SUMMARIZE THESE. They are just for tone/style reference):\n{commits}"
    
    promptContent = f"Files changed:\n{file_status}\n\nDiff to summarize (may exclude minified/auto-generated files):\n```diff\n{diff}\n```"
    messages = [{"role": "system", "content": systemPrompt}, {"role": "user", "content": promptContent}]
    
    buffer = ""
    try:
        if provider == "ollama":
            from ollama import chat as ollama_chat
            response = ollama_chat(
                model=model, 
                messages=messages, 
                options={"temperature": 0.9, "seed": random.randint(0, 1000000)}, 
                think=False, 
                stream=True,
                format=COMMIT_RESPONSE_SCHEMA
            )
            for chunk in response:
                buffer += chunk['message']['content']
        elif provider in ["lmstudio", "openrouter"]:
            try:
                response = client.chat.completions.create(
                    model=model, 
                    messages=messages, 
                    temperature=0.9, 
                    stream=True,
                    response_format={
                        "type": "json_schema", 
                        "json_schema": {"name": "CommitResponse", "schema": COMMIT_RESPONSE_SCHEMA, "strict": True}
                    }
                )
            except Exception:
                messages[0]["content"] += "\nRespond with JSON: {\"commit_message\": \"...\"}"
                response = client.chat.completions.create(
                    model=model, 
                    messages=messages, 
                    temperature=0.9, 
                    stream=True,
                    response_format={"type": "json_object"}
                )
            for chunk in response:
                if chunk.choices[0].delta.content:
                    buffer += chunk.choices[0].delta.content
    except Exception as e:
        print(f"{colorama.Fore.RED}API Error: {e}{colorama.Style.RESET_ALL}")
        return

    message = extract_json_message(buffer)
    if not message:
        print(f"{colorama.Fore.RED}Failed to generate a valid commit message. Output was: {buffer}{colorama.Style.RESET_ALL}")
        return
        
    print(f"\n{colorama.Fore.GREEN}Generated Message:{colorama.Style.RESET_ALL}\n{message}\n")
    
    if not do_commit:
        return
        
    print(f"{colorama.Fore.CYAN}Committing...{colorama.Style.RESET_ALL}")
    commit_res = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
    if commit_res.returncode != 0:
        print(f"{colorama.Fore.RED}Commit failed:\n{commit_res.stderr}{colorama.Style.RESET_ALL}")
        return
        
    if not do_push:
        return
        
    print(f"{colorama.Fore.CYAN}Pushing to remote...{colorama.Style.RESET_ALL}")
    push_res = subprocess.run(["git", "push"], capture_output=True, text=True)
    if push_res.returncode != 0:
        print(f"{colorama.Fore.RED}Push failed:\n{push_res.stderr}{colorama.Style.RESET_ALL}")
        return
        
    print(f"{colorama.Fore.GREEN}Comet committed and synced successfully!{colorama.Style.RESET_ALL}")
    check_for_updates_cli()

def check_for_updates_cli():
    try:
        current_version = importlib.metadata.version("cli-comet")
        req = urllib.request.Request("https://pypi.org/pypi/cli-comet/json")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            data = json.loads(response.read().decode())
            latest_version = data["info"]["version"]
            curr_tuple = tuple(map(int, current_version.split(".")))
            latest_tuple = tuple(map(int, latest_version.split(".")))
            if latest_tuple > curr_tuple:
                print(f"\n{colorama.Fore.YELLOW}Update available: v{latest_version}! Run `pipx upgrade cli-comet` to install.{colorama.Style.RESET_ALL}")
    except Exception:
        pass

def run_init():
    import sys
    print(f"{colorama.Fore.CYAN}Initializing Comet CLI...{colorama.Style.RESET_ALL}")
    
    provider = "auto"
    lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
    ollamaUp = check_endpoint("http://localhost:11434/api/tags")
    if lmstudioUp and not ollamaUp:
        provider = "lmstudio"
    else:
        provider = "ollama"
        
    print(f"{colorama.Fore.GREEN}Auto-detected provider: {provider}{colorama.Style.RESET_ALL}")
    
    settings = load_settings()
    settings["provider"] = provider
    settings["quickStartup"] = True
    
    model = ""
    if provider == "ollama":
        try:
            from ollama import Client as OllamaClient
            client = OllamaClient()
            modelsData = sorted(client.list().models, key=lambda m:m.size, reverse=False)
            allModels = [m.model for m in modelsData]
            model = allModels[0] if allModels else "unknown"
        except Exception:
            model = "unknown"
    elif provider == "lmstudio":
        try:
            from openai import OpenAI
            client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
            modelsData = client.models.list().data
            allModels = [m.id for m in modelsData]
            model = allModels[0] if allModels else "unknown"
        except Exception:
            model = "unknown"
            
    settings["model"] = model
    
    path = get_settings_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4)
    except Exception:
        pass
        
    print(f"{colorama.Fore.GREEN}Saved settings. Model: {model}{colorama.Style.RESET_ALL}")

    if provider == "ollama" and model and model != "unknown":
        try:
            print(f"{colorama.Fore.CYAN}Preloading model {model} with a 3 hour TTL...{colorama.Style.RESET_ALL}")
            client.generate(model=model, prompt="", keep_alive="3h")
            print(f"{colorama.Fore.GREEN}Model preloaded successfully!{colorama.Style.RESET_ALL}")
        except Exception as e:
            print(f"{colorama.Fore.RED}Failed to preload model: {e}{colorama.Style.RESET_ALL}")
    
    
    if os.name == 'nt':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "CometCLI", 0, winreg.REG_SZ, "comet --warmup")
            winreg.CloseKey(key)
            print(f"{colorama.Fore.GREEN}Added Comet to Windows Startup for quick initialization.{colorama.Style.RESET_ALL}")
        except Exception as e:
            print(f"{colorama.Fore.RED}Failed to add startup entry: {e}{colorama.Style.RESET_ALL}")
            
    print(f"{colorama.Fore.GREEN}Initialization complete!{colorama.Style.RESET_ALL}")

def run_warmup():
    settings = load_settings()
    if not settings.get("quickStartup", True):
        return
        
    provider = settings.get("provider", "ollama")
    if provider == "ollama":
        try:
            from ollama import Client as OllamaClient
            client = OllamaClient()
            model = settings.get("model", "")
            if model and model != "unknown":
                client.generate(model=model, prompt="", keep_alive="3h")
            else:
                client.list()
        except Exception:
            pass
    elif provider == "lmstudio":
        try:
            from openai import OpenAI
            client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
            client.models.list()
        except Exception:
            pass

def main():
    parser = argparse.ArgumentParser(description="Comet - AI commit message generator")
    parser.add_argument("-a", "--auto", action="store_true", help="Skip the UI and automatically commit and sync")
    parser.add_argument("-i", "--init", action="store_true", help="Initialize Comet and configure QuickStartup")
    parser.add_argument("--warmup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--undo", action="store_true", help="Undo the last commit")
    args = parser.parse_args()

    if args.init:
        run_init()
        return

    if args.warmup:
        run_warmup()
        return

    if args.undo:
        print(f"{colorama.Fore.CYAN}Undoing last commit...{colorama.Style.RESET_ALL}")
        subprocess.run(["git", "reset", "HEAD~1"])
        return

    settings = load_settings()
    provider = settings.get("provider", "auto")
    model = settings.get("model", "")

    subprocess.run(["git", "add", "."], cwd=os.getcwd(), capture_output=True)

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
    
    if not diff.strip():
        print(f"{colorama.Fore.YELLOW}No changes detected. Exiting.{colorama.Style.RESET_ALL}")
        return
        
    status = subprocess.run(["git", "diff", "--name-status", "HEAD"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    commits = subprocess.run(["git", "log", "-n", "5", "--oneline"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    
    if args.auto:
        headless_auto_commit(provider, model, diff, status, commits)
    else:
        app = CometTUI(commit="Generating...", model=model, diff=diff, file_status=status, commits=commits, allModels=[], provider=provider, client=None)
        result = app.run()
        if result: print(result)

def main_git_comet():
    settings = load_settings()
    provider = settings.get("provider", "auto")
    model = settings.get("model", "")

    subprocess.run(["git", "add", "."], cwd=os.getcwd(), capture_output=True)

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
    
    if not diff.strip():
        print(f"{colorama.Fore.YELLOW}No changes detected. Exiting.{colorama.Style.RESET_ALL}")
        return
        
    status = subprocess.run(["git", "diff", "--name-status", "HEAD"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    commits = subprocess.run(["git", "log", "-n", "5", "--oneline"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    
    headless_auto_commit(provider, model, diff, status, commits, do_commit=False, do_push=False)

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
    CLOSE_TIMEOUT = 0.1
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
        layers: under default;
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
        dock: bottom;
        layer: under;
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
            path = "~" + os.getcwd()[len(os.path.expanduser("~")):] if os.getcwd().startswith(os.path.expanduser("~")) else os.getcwd().replace(os.sep, "/") + "/"
            with Horizontal(id="bottom_row"):
                yield Label(f" {path}", id="cwd_label")
                yield Button(" ⚙  Settings ", id="settingsBtn")

            #ascii logo
            yield Label(""" ▄▄▄▄  ▄▄▄  ▄▄   ▄▄ ▄▄▄▄▄ ▄▄▄▄▄▄   ┌─┐┬  ┬\n██▀▀▀ ██▀██ ██▀▄▀██ ██▄▄    ██     │  │  │\n▀████ ▀███▀ ██   ██ ██▄▄▄   ██     └─┘┴─┘┴""", id="logo")
            with Horizontal(id="input_row"):
                yield CustomTextArea(self.commit, id="input", show_line_numbers=False)
                yield Button(" ₊✦  Regenerate  ", id="regenBtn")
            with Horizontal(id="action_row"):
                undo = Button(" ↺  Undo ", id="undoBtn")
                yield Button(" ✔  Commit ", id="commitBtn")
                undo.display = False
                yield undo
                yield Button(" ⛌  Quit ", id="cancelBtn")
            yield Label("[$text][b]ctrl+r[/b][/] regenerate    [$text][b]enter[/b][/] continue    [$text][b]tab[/b][/] swap model    [$text][b]ctrl+z[/b][/] undo    [$text][b]↓/↑[/b][/] move lines    [$text][b]esc[/b][/] quit", id="shortcuts")

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

    def check_for_updates(self) -> None:
        def run():
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
        import threading
        threading.Thread(target=run, daemon=True).start()

    def initialize_llm(self) -> None:
        def run():
            if self.provider == "auto":
                lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
                ollamaUp = check_endpoint("http://localhost:11434/api/tags")
                if lmstudioUp and not ollamaUp:
                    self.provider = "lmstudio"
                else:
                    self.provider = "ollama"

            if self.provider == "ollama":
                from ollama import Client as OllamaClient
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
                from openai import OpenAI
                self.client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
                try:
                    modelsData = self.client.models.list().data
                    self.allModels = [m.id for m in modelsData]
                    defaultModel = self.allModels[0] if self.allModels else "unknown"
                except Exception:
                    self.allModels = ["unknown"]
                    defaultModel = "unknown"
            elif self.provider == "openrouter":
                settings = load_settings()
                api_key = os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", "")
                from openai import OpenAI
                self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key or "missing_key")
                try:
                    modelsData = self.client.models.list().data
                    self.allModels = [m.id for m in modelsData]
                    defaultModel = "openai/gpt-4o-mini" if "openai/gpt-4o-mini" in self.allModels else (self.allModels[0] if self.allModels else "unknown")
                except Exception:
                    self.allModels = ["unknown"]
                    defaultModel = "unknown"

            if getattr(self, "model", "") in self.allModels and self.model != "Loading...":
                pass
            else:
                self.model = defaultModel

            save_settings(self.provider, self.model)
            self.call_from_thread(self.post_initialize_llm)
        import threading
        threading.Thread(target=run, daemon=True).start()

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
            
            subprocess.run(["git", "commit", "-m", finalMessage], capture_output=True)
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

        elif event.button.id == "settingsBtn":
            import platform
            settings_path = get_settings_path()
            if platform.system() == 'Windows':
                os.startfile(settings_path)
            elif platform.system() == 'Darwin':
                subprocess.run(['open', settings_path])
            else:
                subprocess.run(['xdg-open', settings_path])

    def regenerate(self) -> None:
        def run():
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
                    from ollama import chat as ollama_chat
                    try:
                        response = ollama_chat(
                            model=self.model, 
                            messages=messages, 
                            options={"temperature": 0.9, "seed": random.randint(0, 1000000)}, 
                            think=False, 
                            keep_alive="60m", 
                            stream=True,
                            format=COMMIT_RESPONSE_SCHEMA
                        )
                        buffer = ""
                        for chunk in response:
                            buffer += chunk['message']['content']
                            self.call_from_thread(self.update_textarea, extract_json_message(buffer), False)
                        message = extract_json_message(buffer)
                    except Exception as e:
                        message = f"Error: {e}"
                        self.call_from_thread(self.update_textarea, message, True)
                        break
                elif self.provider in ["lmstudio", "openrouter"]:
                    try:
                        try:
                            response = self.client.chat.completions.create(
                                model=self.model, 
                                messages=messages, 
                                temperature=0.9, 
                                stream=True,
                                response_format={
                                    "type": "json_schema", 
                                    "json_schema": {"name": "CommitResponse", "schema": COMMIT_RESPONSE_SCHEMA, "strict": True}
                                }
                            )
                        except Exception:
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
                    except Exception as e:
                        message = f"Error: {e}"
                        self.call_from_thread(self.update_textarea, message, True)
                        break

                if message not in self.pastResponses:
                    self.pastResponses.add(message)
                self.call_from_thread(self.update_textarea, message, True)
                break
        import threading
        threading.Thread(target=run, daemon=True).start()

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

    def update_status_loop(self) -> None:
        if getattr(self, "_checkingStatus", False):
            return
        self._checkingStatus = True
        def run():
            try:
                try:
                    settings_path = get_settings_path()
                    if os.path.exists(settings_path):
                        current_mtime = os.path.getmtime(settings_path)
                        if not hasattr(self, "_last_settings_mtime"):
                            self._last_settings_mtime = current_mtime
                        elif current_mtime > self._last_settings_mtime:
                            self._last_settings_mtime = current_mtime
                            new_settings = load_settings()
                            new_provider = new_settings.get("provider", self.provider)
                            new_model = new_settings.get("model", self.model)
                            changed = False
                            if new_provider != self.provider:
                                self.provider = new_provider
                                changed = True
                            if new_model != self.model:
                                self.model = new_model
                                changed = True
                            if changed:
                                self.call_from_thread(self.initialize_llm)
                except Exception:
                    pass

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
            finally:
                self._checkingStatus = False
        import threading
        threading.Thread(target=run, daemon=True).start()

    def update_border_title(self, title: str) -> None:
        try:
            self.query_one("#input_row").border_title = title
        except Exception:
            pass

if __name__ == "__main__": main()