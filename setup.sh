#!/usr/bin/env bash
# Run once inside the sbx sandbox to install tmux and open the 5-agent layout.
# Usage: bash setup.sh

set -e

echo "Installing tmux..."
sudo apt-get update -qq && sudo apt-get install -y tmux

echo "Installing Python deps..."
pip install -r requirements.txt -q

echo "Setting up tmux session for a_comp_hcp_communication..."

# Create session with 5 named windows — one per agent
tmux new-session -d -s agents -n "stage-01-competitors"
tmux new-window  -t agents -n "stage-02-corpus"
tmux new-window  -t agents -n "stage-03-wiki"
tmux new-window  -t agents -n "stage-04-sentiment"
tmux new-window  -t agents -n "stage-05-reviewer"

# Send initial cd to each window
for window in stage-01-competitors stage-02-corpus stage-03-wiki stage-04-sentiment stage-05-reviewer; do
  tmux send-keys -t "agents:$window" "cd /workspace/a_comp_hcp_communication" Enter
done

echo ""
echo "Done. Attach with:  tmux attach -t agents"
echo "Switch windows:     Ctrl+b then w  (window list)"
echo "                    Ctrl+b then n  (next window)"
echo ""
echo "In each window, run:  claude"
echo "Then paste the agent prompt from a_comp_hcp_communication/CLAUDE.md"
