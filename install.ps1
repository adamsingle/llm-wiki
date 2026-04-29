# LLM Wiki Agent - Windows Setup Script
# =======================================
# Installs all dependencies for the LLM Wiki Agent using Chocolatey.
#
# Usage:
#   1. Open PowerShell as Administrator
#   2. Run: Set-ExecutionPolicy Bypass -Scope Process -Force
#   3. Run: .\install.ps1
#
# Optional flags:
#   .\install.ps1 -SkipOllama       # Skip Ollama (if using Gemini/Claude/OpenAI only)
#   .\install.ps1 -SkipGit          # Skip Git install
#   .\install.ps1 -PythonVersion 3.11  # Install a specific Python version

param(
    [switch]$SkipOllama,
    [switch]$SkipGit,
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$msg)
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$msg)
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Warn {
    param([string]$msg)
    Write-Host "  [WARN] $msg" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$msg)
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
}

# --- Check running as Administrator ---

Write-Step "Checking administrator privileges"
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Fail "This script must be run as Administrator."
    Write-Host "  Right-click PowerShell and choose 'Run as administrator', then try again."
    exit 1
}
Write-Success "Running as Administrator"

# --- Install Chocolatey if not present ---

Write-Step "Checking for Chocolatey"
if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
    Write-Host "  Installing Chocolatey..."
    Set-ExecutionPolicy Bypass -Scope Process -Force
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
    $chocoInstall = (New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1')
    & ([scriptblock]::Create($chocoInstall))
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command choco -ErrorAction SilentlyContinue)) {
        Write-Fail "Chocolatey installation failed. Please install manually from https://chocolatey.org"
        exit 1
    }
    Write-Success "Chocolatey installed"
} else {
    Write-Success "Chocolatey already installed ($(choco --version))"
}

# --- Install Python ---

Write-Step "Checking for Python $PythonVersion+"
$pythonOk = $false
if (Get-Command python -ErrorAction SilentlyContinue) {
    $ver = python --version 2>&1
    if ($ver -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -ge 3 -and $minor -ge 8) {
            Write-Success "Python already installed: $ver"
            $pythonOk = $true
        }
    }
}

if (-not $pythonOk) {
    Write-Host "  Installing Python $PythonVersion via Chocolatey..."
    choco install python --version=$PythonVersion -y --no-progress
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    if (Get-Command python -ErrorAction SilentlyContinue) {
        Write-Success "Python installed: $(python --version)"
    } else {
        Write-Fail "Python installation failed."
        exit 1
    }
}

# --- Install Git ---

if (-not $SkipGit) {
    Write-Step "Checking for Git"
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Write-Success "Git already installed: $(git --version)"
    } else {
        Write-Host "  Installing Git..."
        choco install git -y --no-progress
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        Write-Success "Git installed"
    }
}

# --- Install Ollama ---

if (-not $SkipOllama) {
    Write-Step "Checking for Ollama"
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Write-Success "Ollama already installed: $(ollama --version 2>&1)"
    } else {
        Write-Host "  Installing Ollama..."
        choco install ollama -y --no-progress
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        if (Get-Command ollama -ErrorAction SilentlyContinue) {
            Write-Success "Ollama installed"
        } else {
            Write-Warn "Ollama installation may have failed. Install manually from https://ollama.com if needed."
        }
    }
} else {
    Write-Warn "Skipping Ollama (using cloud provider only)"
}

# --- Install Python packages ---

Write-Step "Installing Python packages from requirements.txt"

$reqFile = Join-Path $PSScriptRoot "requirements.txt"
if (-not (Test-Path $reqFile)) {
    Write-Warn "requirements.txt not found - installing packages directly"
    $packages = @("requests>=2.31.0", "pyyaml>=6.0", "pypdf>=3.0.0", "python-docx>=1.0.0")
    foreach ($pkg in $packages) {
        Write-Host "  Installing $pkg..."
        python -m pip install $pkg --quiet
    }
} else {
    Write-Host "  Running: pip install -r requirements.txt"
    python -m pip install -r $reqFile --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pip install failed. Check your requirements.txt and internet connection."
        exit 1
    }
}

Write-Success "Python packages installed"

# --- Verify Python imports ---

Write-Step "Verifying Python package imports"
$checks = @(
    @{ import = "requests"; name = "requests" },
    @{ import = "yaml";     name = "pyyaml" },
    @{ import = "pypdf";    name = "pypdf" },
    @{ import = "docx";     name = "python-docx" }
)

$allOk = $true
foreach ($check in $checks) {
    $result = python -c "import $($check.import); print('ok')" 2>&1
    if ($result -eq "ok") {
        Write-Success "$($check.name)"
    } else {
        Write-Fail "$($check.name) failed to import: $result"
        $allOk = $false
    }
}

if (-not $allOk) {
    Write-Warn "Some packages failed to import. Try: python -m pip install -r requirements.txt"
}

# --- Verify agent files ---

Write-Step "Checking agent files"
$requiredFiles = @("agent.py", "providers.py", "tools.py", "schema.py")
$missingFiles = @()

foreach ($file in $requiredFiles) {
    $filePath = Join-Path $PSScriptRoot $file
    if (Test-Path $filePath) {
        Write-Success "$file"
    } else {
        Write-Fail "$file - NOT FOUND"
        $missingFiles += $file
    }
}

if ($missingFiles.Count -gt 0) {
    Write-Warn "Missing files: $($missingFiles -join ', ')"
    Write-Host "  Make sure all agent files are in the same directory as install.ps1"
}

# --- Check Ollama models ---

if (-not $SkipOllama) {
    Write-Step "Ollama model setup"
    Write-Host "  Checking if Ollama server is running..."
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -TimeoutSec 3 -ErrorAction Stop
        $models = ($response.Content | ConvertFrom-Json).models
        if ($models.Count -gt 0) {
            Write-Success "Ollama running with models: $(($models | Select-Object -First 3 -ExpandProperty name) -join ', ')"
        } else {
            Write-Warn "Ollama is running but no models are installed."
            Write-Host "  To install the recommended model, run:"
            Write-Host "    ollama pull qwen2.5:14b"
        }
    } catch {
        Write-Warn "Ollama server not currently running."
        Write-Host "  Start it with: ollama serve"
        Write-Host "  Then pull a model: ollama pull qwen2.5:14b"
    }
}

# --- Done ---

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  LLM Wiki Agent - Installation Complete" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:"
Write-Host ""
Write-Host "  1. Create a wiki folder and run init:"
Write-Host "       mkdir C:\my-wiki"
Write-Host "       cd C:\my-wiki"
Write-Host "       python C:\path\to\agent.py init"
Write-Host ""
Write-Host "  2. Edit config.yaml to set your provider:"
Write-Host "       - Ollama (local):  uncomment the ollama: block"
Write-Host "       - Gemini:          uncomment gemini: block, add your API key"
Write-Host "       - Claude:          uncomment anthropic: block, add your API key"
Write-Host "       - OpenAI:          uncomment openai: block, add your API key"
Write-Host ""
Write-Host "  3. Run init again to apply the config:"
Write-Host "       python agent.py init"
Write-Host ""
Write-Host "  4. Drop source files into raw/ and ingest them:"
Write-Host "       python agent.py ingest raw\myfile.pdf"
Write-Host ""
Write-Host "  5. Start chatting:"
Write-Host "       python agent.py chat"
Write-Host ""
Write-Host "  See README.md for full usage guide."
Write-Host ""
