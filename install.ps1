$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Launcher = Get-Command py -ErrorAction SilentlyContinue
$HasPython311 = $false
if ($Launcher) {
    & py -3.11 --version 2>$null
    $HasPython311 = ($LASTEXITCODE -eq 0)
}
if ($HasPython311) {
    & py -3.11 -m venv .venv
} else {
    $PythonExe = $null
    $Candidates = @(
        (Join-Path $env:LocalAppData "Programs\Python\Python311\python.exe"),
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($Candidate in $Candidates) {
        if (Test-Path $Candidate) { $PythonExe = $Candidate; break }
    }
    if (-not $PythonExe -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "Installing Python 3.11 with Windows Package Manager..."
        winget install --id Python.Python.3.11 -e --scope user --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Python 3.11 installation failed." }
        foreach ($Candidate in $Candidates) {
            if (Test-Path $Candidate) { $PythonExe = $Candidate; break }
        }
    }
    if (-not $PythonExe) {
        throw "Could not locate Python 3.11. Install it from python.org or enable winget, then rerun this installer."
    }
    & $PythonExe --version
    if ($LASTEXITCODE -ne 0) { throw "Could not run Python 3.11." }
    & $PythonExe -m venv .venv
}
if ($LASTEXITCODE -ne 0) { throw "Python virtual environment creation failed." }

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r requirements.txt
& $VenvPython -m playwright install chromium

New-Item -ItemType Directory -Force -Path (Join-Path $Root "workspace") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "skills") | Out-Null

$Ollama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $Ollama -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    $InstallOllama = Read-Host "Ollama is missing. Install it now with winget? (Y/N)"
    if ($InstallOllama -match "^(Y|y)$") {
        winget install --id Ollama.Ollama -e --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Ollama installation failed." }
        $Ollama = Get-Command ollama -ErrorAction SilentlyContinue
        if (-not $Ollama) {
            $KnownOllama = Join-Path $env:LocalAppData "Programs\Ollama\ollama.exe"
            if (Test-Path $KnownOllama) { $Ollama = Get-Item $KnownOllama }
        }
    }
}
if (-not $Ollama) {
    Write-Host "Ollama is not available yet. Install Ollama, start it, and rerun this installer to pull a model."
} else {
    if ($Ollama -is [System.IO.FileInfo]) { $OllamaCommand = $Ollama.FullName }
    else { $OllamaCommand = $Ollama.Source }
    $Model = Read-Host "Pull qwen2.5-coder:14b now? It needs several GB of disk space (Y/N)"
    if ($Model -match "^(Y|y)$") {
        & $OllamaCommand pull qwen2.5-coder:14b
        if ($LASTEXITCODE -ne 0) { throw "Could not download qwen2.5-coder:14b." }
    }
    $SmallModel = Read-Host "Also pull llama3.2:3b as a smaller fallback? (Y/N)"
    if ($SmallModel -match "^(Y|y)$") {
        & $OllamaCommand pull llama3.2:3b
        if ($LASTEXITCODE -ne 0) { throw "Could not download llama3.2:3b." }
    }
}

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    Write-Host "Optional OCR: install Tesseract OCR and set TESSERACT_CMD if it is not on PATH."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Optional containers: install Docker Desktop and start it to enable Docker tools."
}

Write-Host ""
Write-Host "Installation complete. Start JARVIS with:"
Write-Host "  .\.venv\Scripts\python.exe main.py"
Write-Host "The first launch creates a local .env encryption key. Keep .env backed up and private."
