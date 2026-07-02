# Exaris Services Repo

This repo contains Exaris pharma pipeline services, one per folder.

## Services

| Folder | Service | Status |
|--------|---------|--------|
| `a_comp_hcp_communication/` | 1.2 Competitive HCP Communication Monitoring | active |

## How to work on a service

1. `sbx run claude .` from the repo root on your machine
2. Inside the sandbox: `bash setup.sh`
3. `tmux attach -t agents`
4. In each window: `claude` → paste the agent prompt from the service's `CLAUDE.md`

## Repo conventions

- Each service is self-contained — no cross-service imports
- JSON checkpoints go in `<service>/data/` (gitignored)
- HTML/Excel outputs go in `<service>/results/` (gitignored)
- Agent coordination via `TASKS.md` at repo root
