#!/usr/bin/env bash
set -euo pipefail

# --- Simple Split Terminal (sst) — Linux/macOS installer ---

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REQUIREMENTS="$SCRIPT_DIR/requirements.txt"

echo "=== sst installer ==="
echo ""

# 1. Check for Python 3
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Install Python 3.8+ and try again."
    exit 1
fi
echo "Found: $(python3 --version)"

# 2. Install dependencies
echo ""
echo "Installing dependencies..."
python3 -m pip install -r "$REQUIREMENTS"

# 3. Create launcher script
LAUNCHER="$SCRIPT_DIR/sst"
cat > "$LAUNCHER" << 'PYEOF'
#!/usr/bin/env bash
exec python3 "{{SCRIPT_DIR}}/index.py" "$@"
PYEOF
# Replace the placeholder with the actual script directory
sed -i "s|{{SCRIPT_DIR}}|$SCRIPT_DIR|g" "$LAUNCHER"
chmod +x "$LAUNCHER"

# 4. Symlink into PATH
if [ -w /usr/local/bin ]; then
    ln -sf "$LAUNCHER" /usr/local/bin/sst
    echo ""
    echo "Installed: /usr/local/bin/sst -> $LAUNCHER"
elif [ -d "$HOME/.local/bin" ] || mkdir -p "$HOME/.local/bin" 2>/dev/null; then
    ln -sf "$LAUNCHER" "$HOME/.local/bin/sst"
    echo ""
    echo "Installed: $HOME/.local/bin/sst -> $LAUNCHER"
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        echo "NOTE: Add ~/.local/bin to your PATH:"
        echo '  export PATH="$HOME/.local/bin:$PATH"'
    fi
else
    echo ""
    echo "Launcher created at: $LAUNCHER"
    echo "Run it directly or symlink it into your PATH manually."
fi

echo ""
echo "Done. Run: sst <port> [-b BAUD] [-s vertical|horizontal]"
