
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
        return text

    if buffer.lstrip().startswith("{"): return ""
    return buffer

def fetch_json(url, headers=None, data=None, timeout=2.0):
    import urllib.request, json
    req = urllib.request.Request(url, headers=headers or {})
    if data:
        req.data = json.dumps(data).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None

def stream_json(url, headers=None, data=None, timeout=2.0):
    import urllib.request, json
    req = urllib.request.Request(url, headers=headers or {})
    if data:
        req.data = json.dumps(data).encode('utf-8')
        req.add_header('Content-Type', 'application/json')
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception:
        return None

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
    print(f"\033[36mGenerating auto-commit with {provider}...\033[0m")
    
    client = None
    if provider == "auto":
        lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
        ollamaUp = check_endpoint("http://localhost:11434/api/tags")
        if lmstudioUp and not ollamaUp:
            provider = "lmstudio"
        else:
            provider = "ollama"

    if provider == "ollama":
        try:
            tags = fetch_json("http://localhost:11434/api/tags") or {}
            allModelsData = sorted(tags.get("models", []), key=lambda m: m.get("size", 0))
            allModels = [m["name"] for m in allModelsData if "name" in m]
            ps = fetch_json("http://localhost:11434/api/ps") or {}
            loadedModels = ps.get("models", [])
            defaultModel = loadedModels[0]["name"] if loadedModels else (allModels[0] if allModels else "unknown")
        except Exception:
            allModels = ["unknown"]
            defaultModel = "unknown"
    elif provider == "lmstudio":
        try:
            data = fetch_json("http://localhost:1234/v1/models") or {}
            allModels = [m["id"] for m in data.get("data", []) if "id" in m]
            defaultModel = allModels[0] if allModels else "unknown"
        except Exception:
            allModels = ["unknown"]
            defaultModel = "unknown"
    elif provider == "openrouter":
        settings = load_settings()
        api_key = os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", "")
        try:
            data = fetch_json("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {api_key}"}) or {}
            allModels = [m["id"] for m in data.get("data", []) if "id" in m]
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
    print(f"\033[33mModel active: {model}\033[0m")
    
    systemPath = os.path.join(os.path.dirname(__file__), "system.md")
    systemPrompt = open(systemPath, "r", encoding="utf-8").read()
    systemPrompt += f"\n\nRecent Commits (For Context Only. DO NOT SUMMARIZE THESE. They are just for tone/style reference):\n{commits}"
    
    promptContent = f"Files changed:\n{file_status}\n\nDiff to summarize (may exclude minified/auto-generated files):\n```diff\n{diff}\n```"
    messages = [{"role": "system", "content": systemPrompt}, {"role": "user", "content": promptContent}]
    
    buffer = ""
    try:
        if provider == "ollama":
            payload = {
                "model": model, "messages": messages, "stream": True,
                "options": {"temperature": 0.9, "seed": random.randint(0, 1000000)},
                "format": COMMIT_RESPONSE_SCHEMA
            }
            resp = stream_json("http://localhost:11434/api/chat", data=payload, timeout=60.0)
            if resp:
                for line in resp:
                    if line:
                        chunk = json.loads(line.decode('utf-8'))
                        if "message" in chunk and "content" in chunk["message"]:
                            buffer += chunk["message"]["content"]
                            msg = extract_json_message(buffer)
                            if msg:
                                sys.stdout.write('\r\033[K' + '\033[32m' + msg.replace('\n', ' ') + '\033[0m')
                                sys.stdout.flush()
                print()
                message = extract_json_message(buffer)
                if not message and buffer:
                    message = buffer
            else:
                print(f"\033[31mError: Failed to fetch response.\033[0m")
                return None
        elif provider in ["lmstudio", "openrouter"]:
            settings = load_settings()
            base_url = "http://localhost:1234/v1" if provider == "lmstudio" else "https://openrouter.ai/api/v1"
            api_key = "lm-studio" if provider == "lmstudio" else (os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", ""))
            payload = {
                "model": model, "messages": messages, "temperature": 0.9, "stream": True,
                "response_format": {"type": "json_schema", "json_schema": {"name": "CommitResponse", "schema": COMMIT_RESPONSE_SCHEMA, "strict": True}}
            }
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = stream_json(f"{base_url}/chat/completions", headers=headers, data=payload, timeout=60.0)
            if not resp:
                messages[0]["content"] += "\nRespond with JSON: {\"commit_message\": \"...\"}"
                payload["response_format"] = {"type": "json_object"}
                payload["messages"] = messages
                resp = stream_json(f"{base_url}/chat/completions", headers=headers, data=payload, timeout=60.0)
            if resp:
                for line in resp:
                    line_text = line.decode('utf-8').strip()
                    if line_text.startswith("data: ") and line_text != "data: [DONE]":
                        chunk = json.loads(line_text[6:])
                        if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                            buffer += chunk["choices"][0]["delta"]["content"]
                            msg = extract_json_message(buffer)
                            if msg:
                                sys.stdout.write('\r\033[K' + '\033[32m' + msg.replace('\n', ' ') + '\033[0m')
                                sys.stdout.flush()
                print()
                message = extract_json_message(buffer)
                if not message and buffer:
                    message = buffer
            else:
                print(f"\033[31mError: Failed to fetch response.\033[0m")
                return None
    except Exception as e:
        print(f"\033[31mAPI Error: {e}\033[0m")
        return

    message = extract_json_message(buffer)
    if not message:
        print(f"\033[31mFailed to generate a valid commit message. Output was: {buffer}\033[0m")
        return
        
    print(f"\n\033[32mGenerated Message:\033[0m\n{message}\n")
    
    if not do_commit:
        return
        
    print(f"\033[36mCommitting...\033[0m")
    commit_res = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
    if commit_res.returncode != 0:
        print(f"\033[31mCommit failed:\n{commit_res.stderr}\033[0m")
        return
        
    if not do_push:
        return
        
    print(f"\033[36mPushing to remote...\033[0m")
    push_res = subprocess.run(["git", "push"], capture_output=True, text=True)
    if push_res.returncode != 0:
        print(f"\033[31mPush failed:\n{push_res.stderr}\033[0m")
        return
        
    print(f"\033[32mComet committed and synced successfully!\033[0m")
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
                print(f"\n\033[33mUpdate available: v{latest_version}! Run `pipx upgrade cli-comet` to install.\033[0m")
    except Exception:
        pass

def run_init():
    import sys
    print(f"\033[36mInitializing Comet CLI...\033[0m")
    
    provider = "auto"
    lmstudioUp = check_endpoint("http://localhost:1234/v1/models")
    ollamaUp = check_endpoint("http://localhost:11434/api/tags")
    if lmstudioUp and not ollamaUp:
        provider = "lmstudio"
    else:
        provider = "ollama"
        
    print(f"\033[32mAuto-detected provider: {provider}\033[0m")
    
    settings = load_settings()
    settings["provider"] = provider
    settings["quickStartup"] = True
    
    model = ""
    if provider == "ollama":
        try:
            tags = fetch_json("http://localhost:11434/api/tags") or {}
            modelsData = sorted(tags.get("models", []), key=lambda m: m.get("size", 0))
            allModels = [m["name"] for m in modelsData if "name" in m]
            model = allModels[0] if allModels else "unknown"
        except Exception:
            model = "unknown"
    elif provider == "lmstudio":
        try:
            data = fetch_json("http://localhost:1234/v1/models") or {}
            allModels = [m["id"] for m in data.get("data", []) if "id" in m]
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
        
    print(f"\033[32mSaved settings. Model: {model}\033[0m")

    if provider == "ollama" and model and model != "unknown":
        try:
            print(f"\033[36mPreloading model {model} with a 3 hour TTL...\033[0m")
            import urllib.request, json
            req = urllib.request.Request("http://localhost:11434/api/generate", data=json.dumps({"model": model, "keep_alive": "3h"}).encode('utf-8'), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=5.0) as _:
                pass
            print(f"\033[32mModel preloaded successfully!\033[0m")
        except Exception as e:
            print(f"\033[31mFailed to preload model: {e}\033[0m")
    
    
    if os.name == 'nt':
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, "CometCLI", 0, winreg.REG_SZ, "comet --warmup")
            winreg.CloseKey(key)
            print(f"\033[32mAdded Comet to Windows Startup for quick initialization.\033[0m")
        except Exception as e:
            print(f"\033[31mFailed to add startup entry: {e}\033[0m")
            
    print(f"\033[32mInitialization complete!\033[0m")

def run_warmup():
    settings = load_settings()
    if not settings.get("quickStartup", True):
        return
        
    provider = settings.get("provider", "ollama")
    if provider == "ollama":
        try:
            model = settings.get("model", "")
            if model and model != "unknown":
                import urllib.request, json
                req = urllib.request.Request("http://localhost:11434/api/generate", data=json.dumps({"model": model, "keep_alive": "3h"}).encode('utf-8'), headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(req, timeout=5.0) as _: pass
            else:
                fetch_json("http://localhost:11434/api/tags")
        except Exception:
            pass
    elif provider == "lmstudio":
        try:
            fetch_json("http://localhost:1234/v1/models")
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
        print(f"\033[36mUndoing last commit...\033[0m")
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
        print(f"\033[33mNo changes detected. Exiting.\033[0m")
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
        print(f"\033[33mNo changes detected. Exiting.\033[0m")
        return
        
    status = subprocess.run(["git", "diff", "--name-status", "HEAD"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    commits = subprocess.run(["git", "log", "-n", "5", "--oneline"], cwd=os.getcwd(), capture_output=True, text=True, check=True, encoding="utf-8").stdout
    
    headless_auto_commit(provider, model, diff, status, commits, do_commit=False, do_push=False)

from .render import CustomTUI, TextArea, Button, Label
import subprocess

class CometTUI(CustomTUI):
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

    def _compose_layout(self):
        self.widgets.clear()
        
        path = "~" + os.getcwd()[len(os.path.expanduser("~")):] if os.getcwd().startswith(os.path.expanduser("~")) else os.getcwd().replace(os.sep, "/") + "/"
        self.widgets["cwd_label"] = Label(f" {path}", id="cwd_label")
        self.widgets["cwd_label"].x = 1
        self.widgets["cwd_label"].y = self.term_height - 2
        
        self.widgets["settingsBtn"] = Button(" ⚙  Settings ", id="settingsBtn", action=self.action_settings)
        self.widgets["settingsBtn"].x = self.term_width - 15
        self.widgets["settingsBtn"].y = self.term_height - 2

        logo = " ▄▄▄▄  ▄▄▄  ▄▄   ▄▄ ▄▄▄▄▄ ▄▄▄▄▄▄   ┌─┐┬  ┬\n██▀▀▀ ██▀██ ██▀▄▀██ ██▄▄    ██     │  │  │\n▀████ ▀███▀ ██   ██ ██▄▄▄   ██     └─┘┴─┘┴"
        logo_lines = logo.split('\n')
        logo_y = 2
        logo_x = max(1, (self.term_width - len(logo_lines[0])) // 2)
        self.widgets["logo"] = Label(logo, id="logo", style="\033[38;5;33m")
        self.widgets["logo"].x = logo_x
        self.widgets["logo"].y = logo_y

        input_y = logo_y + 4
        self.widgets["input"] = TextArea(text=self.commit, id="input")
        self.widgets["input"].x = 3
        self.widgets["input"].y = input_y
        self.widgets["input"].width = self.term_width - 6
        self.widgets["input"].height = 5
        
        btn_y = input_y + 6
        self.widgets["regenBtn"] = Button(" ₊✦  Regenerate  ", id="regenBtn", action=self.action_regenerate_action)
        self.widgets["regenBtn"].x = self.term_width - 20
        self.widgets["regenBtn"].y = btn_y
        
        self.widgets["commitBtn"] = Button(" ✔  Commit ", id="commitBtn", action=self.action_commit_action)
        self.widgets["commitBtn"].x = 3
        self.widgets["commitBtn"].y = btn_y
        
        self.widgets["undoBtn"] = Button(" ↺  Undo ", id="undoBtn", action=self.action_undo_commit)
        self.widgets["undoBtn"].x = 18
        self.widgets["undoBtn"].y = btn_y
        self.widgets["undoBtn"].disabled = True

        self.widgets["cancelBtn"] = Button(" ⛌  Quit ", id="cancelBtn", action=self.action_exit_action)
        self.widgets["cancelBtn"].x = 30
        self.widgets["cancelBtn"].y = btn_y

        self.widgets["shortcuts"] = Label("ctrl+r regenerate    enter continue    tab swap model    ctrl+z undo    esc quit", id="shortcuts", style="\033[38;5;238m")
        self.widgets["shortcuts"].x = max(1, (self.term_width - len(self.widgets["shortcuts"].text)) // 2)
        self.widgets["shortcuts"].y = self.term_height - 1

        self.tab_order = ["input", "commitBtn", "cancelBtn", "regenBtn", "settingsBtn", "undoBtn"]
        if "input" in self.widgets:
            self.widgets["input"].focused = True

    def handle_global_event(self, event):
        if event.type == "keypress":
            key = event.data["key"]
            if key == "ctrl+r":
                self.action_regenerate_action()
            elif key == "ctrl+z":
                self.action_undo_commit()
            elif key == "tab" and getattr(self, "_swapping", False) == False:
                self._swapping = True
                self.action_swap_model()
                self._swapping = False

    def action_swap_model(self) -> None:
        if self.widgets["undoBtn"].disabled == False:
            return
        if not self.allModels: return
        try:
            currentIndex = self.allModels.index(self.model)
        except ValueError:
            currentIndex = -1
        nextIndex = (currentIndex + 1) % len(self.allModels)
        self.model = self.allModels[nextIndex]
        self.border_title = f"{self.model}"
        save_settings(self.provider, self.model)
        self.draw()

    def action_regenerate_action(self) -> None:
        if self.widgets["regenBtn"].disabled:
            return
        self.widgets["regenBtn"].disabled = True
        self.widgets["input"].text = ""
        self.regenerate()

    def action_exit_action(self) -> None:
        self.exit(f"\033[31mUser cancelled the operation. \033[0m")

    def action_commit_action(self) -> None:
        commitBtn = self.widgets["commitBtn"]
        if commitBtn.disabled:
            return
        if str(commitBtn.label).strip() == "Sync  ➤":
            commitBtn.label = "Syncing..."
            commitBtn.disabled = True
            self.draw()
            subprocess.run(["git", "push"], capture_output=True)
            self.exit(f"\033[32mComet committed and synced successfully! \033[0m")
            return

        finalMessage = self.widgets["input"].text.strip()
        subprocess.run(["git", "commit", "-m", finalMessage], capture_output=True)
        commitBtn.label = "Sync  ➤"
        self.widgets["undoBtn"].disabled = False
        self.widgets["cancelBtn"].disabled = True
        
        self.widgets["input"].disabled = True
        self.widgets["regenBtn"].disabled = True
        self.draw()

    def action_undo_commit(self) -> None:
        commitBtn = self.widgets["commitBtn"]
        if str(commitBtn.label).strip() == "Sync  ➤":
            subprocess.run(["git", "reset", "HEAD~1"], capture_output=True)
            commitBtn.label = " ✔  Commit "
            self.widgets["undoBtn"].disabled = True
            self.widgets["cancelBtn"].disabled = False
            self.widgets["input"].disabled = False
            self.widgets["regenBtn"].disabled = False
            self.draw()

    def action_settings(self) -> None:
        import platform
        settings_path = get_settings_path()
        if platform.system() == 'Windows':
            os.startfile(settings_path)
        elif platform.system() == 'Darwin':
            subprocess.run(['open', settings_path])
        else:
            subprocess.run(['xdg-open', settings_path])

    def on_mount(self) -> None:
        if self.provider == "auto" or self.model == "":
            self.notify("Welcome! This is the longest it'll take to load. :D", title="Scanning Providers", severity="information", timeout=3.0)
        self.border_title = f"{self.model}"
        self.widgets["regenBtn"].disabled = True
        self.widgets["commitBtn"].disabled = True
        self.set_interval(2.0, self.update_status_loop)
        
        self.initialize_llm()
        self.check_for_updates()

    def check_for_updates(self) -> None:
        def run():
            try:
                import importlib.metadata
                current_version = importlib.metadata.version("cli-comet")
                import urllib.request, json
                req = urllib.request.Request("https://pypi.org/pypi/cli-comet/json")
                with urllib.request.urlopen(req, timeout=2.0) as response:
                    data = json.loads(response.read().decode())
                    latest_version = data["info"]["version"]
                    curr_tuple = tuple(map(int, current_version.split(".")))
                    latest_tuple = tuple(map(int, latest_version.split(".")))
                    if latest_tuple > curr_tuple:
                        self.notify(f"Update available: v{latest_version}! Run `pipx upgrade cli-comet` to install.", title="Update Available", severity="warning", timeout=15.0)
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
                try:
                    tags = fetch_json("http://localhost:11434/api/tags") or {}
                    allModelsData = sorted(tags.get("models", []), key=lambda m: m.get("size", 0))
                    self.allModels = [m["name"] for m in allModelsData if "name" in m]
                    ps = fetch_json("http://localhost:11434/api/ps") or {}
                    loadedModels = ps.get("models", [])
                    defaultModel = loadedModels[0]["name"] if loadedModels else (self.allModels[0] if self.allModels else "unknown")
                except Exception:
                    self.allModels = ["unknown"]
                    defaultModel = "unknown"
            elif self.provider == "lmstudio":
                try:
                    data = fetch_json("http://localhost:1234/v1/models") or {}
                    self.allModels = [m["id"] for m in data.get("data", []) if "id" in m]
                    defaultModel = self.allModels[0] if self.allModels else "unknown"
                except Exception:
                    self.allModels = ["unknown"]
                    defaultModel = "unknown"
            elif self.provider == "openrouter":
                settings = load_settings()
                api_key = os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", "")
                try:
                    data = fetch_json("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {api_key}"}) or {}
                    self.allModels = [m["id"] for m in data.get("data", []) if "id" in m]
                    defaultModel = "openai/gpt-4o-mini" if "openai/gpt-4o-mini" in self.allModels else (self.allModels[0] if self.allModels else "unknown")
                except Exception:
                    self.allModels = ["unknown"]
                    defaultModel = "unknown"

            if getattr(self, "model", "") in self.allModels and self.model != "Loading...":
                pass
            else:
                self.model = defaultModel

            save_settings(self.provider, self.model)
            self.post_initialize_llm()
        import threading
        threading.Thread(target=run, daemon=True).start()

    def post_initialize_llm(self) -> None:
        self.border_title = f"{self.model}"
        self.widgets["commitBtn"].disabled = False
        self.regenerate()

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
                    try:
                        payload = {
                            "model": self.model, "messages": messages, "stream": True,
                            "options": {"temperature": 0.9, "seed": random.randint(0, 1000000)},
                            "keep_alive": "60m", "format": COMMIT_RESPONSE_SCHEMA
                        }
                        resp = stream_json("http://localhost:11434/api/chat", data=payload, timeout=60.0)
                        buffer = ""
                        if resp:
                            for line in resp:
                                if line:
                                    chunk = json.loads(line.decode('utf-8'))
                                    if "message" in chunk and "content" in chunk["message"]:
                                        buffer += chunk["message"]["content"]
                                        msg = extract_json_message(buffer)
                                        if msg:
                                            self.call_from_thread(self.update_textarea, msg, False)
                            message = extract_json_message(buffer)
                            if not message and buffer:
                                message = buffer
                        else:
                            message = "Error: Failed to fetch response."
                    except Exception as e:
                        message = f"Error: {e}"
                        self.call_from_thread(self.update_textarea, message, True)
                        break
                elif self.provider in ["lmstudio", "openrouter"]:
                    try:
                        settings = load_settings()
                        base_url = "http://localhost:1234/v1" if self.provider == "lmstudio" else "https://openrouter.ai/api/v1"
                        api_key = "lm-studio" if self.provider == "lmstudio" else (os.getenv("OPENROUTER_API_KEY") or settings.get("openrouter_api_key", ""))
                        payload = {
                            "model": self.model, "messages": messages, "temperature": 0.9, "stream": True,
                            "response_format": {"type": "json_schema", "json_schema": {"name": "CommitResponse", "schema": COMMIT_RESPONSE_SCHEMA, "strict": True}}
                        }
                        headers = {"Authorization": f"Bearer {api_key}"}
                        resp = stream_json(f"{base_url}/chat/completions", headers=headers, data=payload, timeout=60.0)
                        if not resp:
                            messages[0]["content"] += "\nRespond with JSON: {\"commit_message\": \"...\"}"
                            payload["response_format"] = {"type": "json_object"}
                            payload["messages"] = messages
                            resp = stream_json(f"{base_url}/chat/completions", headers=headers, data=payload, timeout=60.0)
                        
                        buffer = ""
                        if resp:
                            for line in resp:
                                line_text = line.decode('utf-8').strip()
                                if line_text.startswith("data: ") and line_text != "data: [DONE]":
                                    chunk = json.loads(line_text[6:])
                                    if chunk.get("choices") and chunk["choices"][0].get("delta", {}).get("content"):
                                        buffer += chunk["choices"][0]["delta"]["content"]
                                        msg = extract_json_message(buffer)
                                        if msg:
                                            self.call_from_thread(self.update_textarea, msg, False)
                            message = extract_json_message(buffer)
                            if not message and buffer:
                                message = buffer
                        else:
                            message = "Error: Failed to fetch response."
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
        if "input" in self.widgets:
            if message or (finished and not self.widgets["input"].text):
                self.widgets["input"].text = message
        if finished:
            if "regenBtn" in self.widgets:
                self.widgets["regenBtn"].disabled = False
            self.is_generating = False
            if self.provider == "ollama":
                from datetime import datetime, timedelta
                self._ollama_expires_at[self.model] = datetime.now() + timedelta(minutes=60)
            self.needs_poll = False
            self.draw()

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
                        ps = fetch_json("http://localhost:11434/api/ps") or {}
                        models = ps.get("models", [])
                        self._ollama_expires_at.clear()
                        for m in models:
                            m_name = m.get("name", "") or m.get("model", "")
                            m_expires_str = m.get("expires_at")
                            if m_expires_str:
                                try:
                                    m_expires = datetime.fromisoformat(m_expires_str.replace("Z", "+00:00"))
                                except Exception:
                                    m_expires = None
                            else:
                                m_expires = None
                            self._ollama_expires_at[m_name] = m_expires
                            if m.get("model", ""):
                                self._ollama_expires_at[m.get("model", "")] = m_expires
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
        self.border_title = title
        self.draw()

if __name__ == "__main__": main()