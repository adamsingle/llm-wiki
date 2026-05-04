#!/bin/bash
# LLM Wiki Agent - Linux Setup Script

set -e

echo ""
echo "==> Checking for Python 3.8+"
if command -v python3 &>/dev/null; then
    ver=$(python3 --version)
    echo "  [OK] $ver already installed"
else
    echo "  Installing Python..."
    sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip
fi

echo ""
echo "==> Checking for Git"
if command -v git &>/dev/null; then
    echo "  [OK] $(git --version) already installed"
else
    sudo apt-get install -y git
fi

echo ""
echo "==> Checking for Ollama"
if command -v ollama &>/dev/null; then
    echo "  [OK] Ollama already installed"
else
    echo "  Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

echo ""
echo "==> Checking for python3-venv"
if ! dpkg -s python3-venv &>/dev/null; then
    echo "  Installing python3-venv..."
    sudo apt-get install -y python3-venv
fi

echo ""
echo "==> Setting up Python virtual environment"
if [ ! -f "venv/bin/activate" ]; then
    echo "  Creating virtual environment..."
    rm -rf venv
    python3 -m venv venv
    echo "  [OK] Virtual environment created in ./venv"
else
    echo "  [OK] Virtual environment already exists"
fi

# Activate the venv
source venv/bin/activate

echo ""
echo "==> Installing Python packages"
pip install -r requirements.txt

echo ""
echo "==> Verifying installs"
python3 -c "import requests, yaml, pypdf, docx; print('  [OK] All packages imported successfully')"

deactivate

echo "  IMPORTANT: Activate the virtual environment before running the agent:"
echo "       source venv/bin/activate"
echo "       python3 agent.py init"
echo "  Or run directly without activating:"
echo "       venv/bin/python3 agent.py init"

echo ""
echo "============================================================"
echo "  LLM Wiki Agent - Installation Complete"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. source venv/bin/activate"
echo "  2. python3 agent.py init"
echo "  3. Edit config.yaml to set your provider"
echo "  4. python3 agent.py init  (run again to apply config)"
echo "  5. python3 agent.py ingest raw/myfile.pdf"
echo "  6. python3 agent.py chat"
echo ""