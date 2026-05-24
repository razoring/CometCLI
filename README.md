# Comet CLI

Comet helps you save tokens! Comet is a terminal user interface application that automatically generates descriptive git commit messages using local language models via Ollama or LMStudio. It analyzes your staged git diffs and provides a clean interface to review, edit, regenerate, and commit your changes instantly.

## Features

- **Instant Boot**: The UI renders instantly with zero latency, utilizing background threading for network auto-detection and initialization.
- **Provider Auto-Detection**: Automatically detects whether you are running Ollama or LMStudio locally without requiring configuration, while allowing overrides via the `--provider` flag.
- **Persistent Settings**: Comet remembers your last successfully used provider and model in a `settings.json` file inside the installation directory, skipping auto-detection on future boot-ups.
- **Local Model Generation**: Connects to your local AI servers to generate commit messages entirely privately.
- **Model Swapping**: Press `tab` to cycle through available local models directly from the interface. 
- **Context Aware**: Automatically pulls in the last 5 commits to understand the tone and style of your project.
- **Terminal User Interface**: Built with Textual. It provides a dedicated text area to edit the generated message.
- **Dynamic Newlines**: Use the down arrow key on the last line to seamlessly add new lines to your commit message, and the up arrow key to remove them.
- **Quick Undo**: Press `ctrl+z` to instantly undo the last local commit if you need to make changes.
- **Direct Syncing**: Commit your changes and immediately push them to your remote repository with a second press of the commit button.
- **Customizable Prompts**: The instructions provided to the language model are stored in `comet/system.md` and can be edited to fit your specific workflow.

## Requirements

- Python 3.10 or higher
- Git installed and accessible in your system path
- Ollama or LMStudio installed and running locally

## Installation

The recommended way to install Comet is globally using `pipx`:

```bash
pipx install cli-comet
```

### Installation from Source

Clone the repository and install the required dependencies:

```bash
git clone https://github.com/razoring/CometCLI.git
cd CometCLI
python -m venv .venv

# On Windows
.venv\Scripts\activate

# On Unix/macOS
source .venv/bin/activate

pip install -e .
```

## Usage

Ensure you have staged your changes using `git add` and that Ollama or LMStudio is running in the background.

Run the Comet application:

```bash
comet
```

You can optionally force a specific provider:
```bash
comet --provider ollama
comet --provider lmstudio
```

### Keyboard Shortcuts

- `enter`: Commit the current text. If pressed again, it will push the commit to the remote repository.
- `tab`: Swap the active language model.
- `ctrl+r`: Regenerate the commit message with the current model.
- `ctrl+z`: Undo the most recent local commit.
- `ctrl+t`: Terminate the application.
- `down arrow`: Add a new line when at the bottom of the text area.
- `up arrow`: Remove the previous empty line.

## Architecture

Comet uses the `subprocess` module to run git commands and capture diffs. It reads the local `comet/system.md` file for system instructions and sends the diff to the selected provider's API. The interface is built using Textual, providing responsive keyboard bindings and a resilient terminal layout. All blocking network logic runs in an asynchronous `@work` thread to ensure 0ms UI start latency.

## License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0). See the `LICENSE` file for details.
