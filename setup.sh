#!/usr/bin/env bash
# Run once inside the sbx sandbox to install tmux and open the 5-agent layout.
# Usage: bash setup.sh

set -e

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SVC_DIR="$REPO_ROOT/a_comp_hcp_communication"
VENV_DIR="$REPO_ROOT/.venv"

echo "Installing tmux..."
sudo apt-get update -qq && sudo apt-get install -y tmux

echo "Installing Python deps..."
# Python 3.12+ is externally managed (PEP 668), so install into a project venv.
# Prefer uv when available; fall back to the stdlib venv + pip.
if [ ! -x "$VENV_DIR/bin/python" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV_DIR"
  else
    python3 -m venv "$VENV_DIR"
  fi
fi
if command -v uv >/dev/null 2>&1; then
  uv pip install --python "$VENV_DIR/bin/python" -r "$REPO_ROOT/requirements.txt"
else
  "$VENV_DIR/bin/pip" install -r "$REPO_ROOT/requirements.txt" -q
fi

echo "Setting up tmux session for a_comp_hcp_communication..."

# Create session with 5 named windows — one per agent
tmux new-session -d -s agents -n "stage-01-competitors"
tmux new-window  -t agents -n "stage-02-corpus"
tmux new-window  -t agents -n "stage-03-wiki"
tmux new-window  -t agents -n "stage-04-sentiment"
tmux new-window  -t agents -n "stage-05-reviewer"

# Send initial cd + venv activation to each window
for window in stage-01-competitors stage-02-corpus stage-03-wiki stage-04-sentiment stage-05-reviewer; do
  tmux send-keys -t "agents:$window" "cd $SVC_DIR && source $VENV_DIR/bin/activate" Enter
done

echo ""
echo "Done. Attach with:  tmux attach -t agents"
echo "Switch windows:     Ctrl+b then w  (window list)"
echo "                    Ctrl+b then n  (next window)"
echo ""
echo "In each window, run:  claude"
echo "Then paste the agent prompt from a_comp_hcp_communication/CLAUDE.md"
