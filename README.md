# JARVIS-LOCAL

JARVIS-LOCAL is a local-first desktop assistant that uses Ollama for offline planning, Gradio for its interface, a Fernet-encrypted connection vault, local ChromaDB memory, and explicit user approval before tool execution.

## What is implemented

- **Chat / agent loop:** Qwen 2.5 Coder plans in JSON; the UI shows the proposed tool calls; **Do it** runs that queued plan; desktop actions capture a verification screenshot.
- **Connections Hub:** create, test, save encrypted, enable/disable, list, and delete Zimbra/Gmail, MySQL, FTP/FTPS, WhatsApp Cloud API, and GitHub credentials.
- **Skills:** successful multi-step plans produce local reusable recipes under `skills/`; only tool names and general steps are retained, not action arguments.
- **Computer control:** screen capture, OCR, optional OmniParser endpoint, mouse, keyboard shortcuts, Windows UI Automation, workspace file management, Playwright page inspection, faster-whisper transcription, Piper speech output.
- **Replit MCP:** OAuth sign-in, create/list/search/update hosted Replit apps, ask questions, publish, and check publish status. OAuth registration and tokens are stored inside the Fernet-encrypted vault.
- **Local project tools:** create a Python/static workspace, run a loopback preview, package a Python program into an executable, install a named PyPI package, and build/run constrained Docker containers.
- **Connections tools:** test connections; send email/WhatsApp; read-only MySQL queries; workspace-limited FTP/FTPS uploads; list GitHub issues.

## Install on Windows

### Download the private GitHub project without Git

The repository is private, so sign in with the GitHub account that has access. GitHub CLI downloads a ZIP archive; this does not use `git clone` or `git pull`.

If GitHub CLI is not installed, run this once, close PowerShell, and open a new PowerShell window:

```powershell
winget install --id GitHub.cli --exact --source winget
```

Sign in through the browser:

```powershell
gh auth login --hostname github.com --web --git-protocol https
```

Download and install the project. This stops rather than overwriting an existing `JARVIS-LOCAL` folder:

```powershell
$ErrorActionPreference = "Stop"
$zip = Join-Path $env:TEMP "jarvis-local.zip"
$stage = Join-Path $env:TEMP "jarvis-local-extract"
$installDir = Join-Path $HOME "JARVIS-LOCAL"

if (Test-Path $installDir) {
    throw "The folder $installDir already exists. Rename it or choose another install location first."
}

gh api repos/yasirkamran555-hue/jarvis-local/zipball --output $zip
if ($LASTEXITCODE -ne 0) { throw "Download failed. Check gh auth status and confirm this GitHub account can access the private repository." }

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $stage -Force
$source = Get-ChildItem -Path $stage -Directory | Select-Object -First 1
if (-not $source) { throw "The downloaded archive did not contain the project folder." }
Move-Item -LiteralPath $source.FullName -Destination $installDir
Remove-Item $stage -Recurse -Force

Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location $installDir
.\install.ps1
```

The installer sets up Python **3.11**, the virtual environment, Python packages, and Playwright. It offers to install Ollama and download the two local models; model downloads need internet and several GB of disk space. Git is not required. Tesseract OCR and Docker Desktop are optional.

### If the project folder is already on your PC

Open PowerShell in the `JARVIS-LOCAL` folder and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1
```

After installation, launch JARVIS with:

```powershell
Set-Location (Join-Path $HOME "JARVIS-LOCAL")
.\.venv\Scripts\python.exe main.py
```

The interface binds to `127.0.0.1` only. It opens in your browser at the local Gradio address shown in the terminal. Do not expose it to a network without setting `JARVIS_UI_PASSWORD` and configuring a trusted host.

## Models and offline use

Ollama runs at `http://127.0.0.1:11434` by default. Set `OLLAMA_BASE_URL` in `.env` only if your Ollama service uses a different local URL. Model files must be downloaded before disconnecting from the internet. Chroma uses deterministic local embeddings and does not download an embedding model. faster-whisper downloads its selected speech model on its first use; run that once while online before using it offline.

OmniParser is an optional local service, not a pip dependency. Set `OMNIPARSER_URL` to its local HTTP parse endpoint. Piper speech requires a local `.onnx` voice model and `PIPER_MODEL` set to that file. OCR requires the Tesseract executable; set `TESSERACT_CMD` if it is not on `PATH`.

## Credentials and data

- On first use, JARVIS generates `JARVIS_FERNET_KEY` in `.env`; `connections.json` contains only the Fernet-encrypted vault.
- Back up `.env` and `connections.json` together. Losing the key makes the vault unreadable. Never commit either file.
- ChromaDB memory and generated skill recipes remain local but are not encrypted; only general task summaries and tool sequences are saved automatically.
- `.gitignore` excludes the local key, vault, memory database, screenshots, audio, project workspaces, and virtual environment.
- Connection passwords are hidden in the UI and omitted from connection lists. MySQL is restricted to read-only `SELECT` queries with a 200-row cap.
- FTP uploads are limited to files within `workspace/`; file operations are contained within the configured `JARVIS_WORKSPACE`. Deleted files go to the recycle bin.

## Safety boundaries

- Planning does not execute tools. The user must review a plan and click **Do it**.
- Desktop control requires a real interactive desktop and is unavailable in headless hosted sessions. Windows UI Automation is Windows-only.
- Screen files are kept under `workspace/screenshots`; OCR and OmniParser cannot read arbitrary filesystem paths.
- The public Gradio share link is disabled. The app defaults to loopback and refuses a non-loopback bind unless an access password is configured.
- Docker runs are isolated with no network, read-only root filesystems, dropped Linux capabilities, resource limits, and no mounted host paths.
- `create_repl`, `update_repl`, and `publish_repl` call Replit's online MCP service after OAuth. The app description or change request in a reviewed plan is sent to Replit only when **Do it** is selected.
- `create_local_project` creates a **local project workspace**. Replit OAuth credentials are encrypted with the same Fernet key as the connection vault. Use **Forget Replit credentials** to remove the local copy; revoke the authorization separately in your Replit account.
- `live_preview` binds to loopback. It is for the same local machine, not an externally published web site.

## Connections

Use the Connections Hub form. Host values support `host` or `host:port` for mail, MySQL, and FTP. For WhatsApp, use the Meta WhatsApp Cloud API phone-number ID as Host, the Cloud API bearer token as Pass, and a recipient phone number when sending. For GitHub, use `owner/repo` as Host and a fine-grained token as Pass. Gmail accounts generally require an app password and IMAP access enabled.

## Replit preview limitations

This application controls the computer on which it runs. A Replit-hosted Linux preview cannot capture or control a user's Windows desktop, and it cannot reach Ollama running on that separate PC via `localhost`. Install and run JARVIS locally for those features. Normal planning uses the local Ollama model; Replit MCP calls require an internet connection and send only the specific approved Replit operation. JARVIS does not send local memory, screenshots, or saved connection records to Replit.

## Development checks

```powershell
python -m compileall -q .
python -m unittest discover -s tests -v
```
