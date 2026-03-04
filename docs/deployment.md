# Deployment Guide

## Overview

Two services are deployed as Docker containers on target hosts (linux/arm64):

| Service | Image | Port |
|---------|-------|------|
| ldoce5-api | `ghcr.io/blue126/ldoce5-api:latest` | 5050 |
| anki-writer | `ghcr.io/blue126/anki-writer:latest` | 5051 |

Images are built automatically by GitHub Actions on every push to `main` and published to ghcr.io. Ansible playbooks for deployment live in the separate IaC repository.

---

## Update Workflow

```
git push origin main
  └─▶ GitHub Actions builds linux/amd64 + linux/arm64 images
        └─▶ pushes to ghcr.io/blue126/ldoce5-api:latest
                       ghcr.io/blue126/anki-writer:latest

cd <iac-repo>
ansible-playbook deploy.yml   # pulls latest images, restarts containers
```

Only the service whose files changed triggers a rebuild (path filters are set per service in `.github/workflows/docker-build.yml`).

---

## Environment Variables

Each service reads its configuration from environment variables (injected via env file or docker run). These are managed in the IaC repo.

### ldoce5-api (port 5050)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LDOCE5_MDX_PATH` | yes | — | Absolute path to `.mdx` file on host |
| `LDOCE5_MDD_PATH` | no | — | Absolute path to `.mdd` audio file |
| `API_HOST` | no | `0.0.0.0` | Bind address |
| `API_PORT` | no | `5050` | Listen port |
| `LLM_API_KEY` | no | — | Leave empty to disable AI sense disambiguation |
| `LLM_BASE_URL` | no | `https://integrate.api.nvidia.com/v1` | OpenAI-compatible endpoint |
| `LLM_MODEL` | no | `nvidia/llama-3.1-nemotron-70b-instruct` | Model ID |

### anki-writer (port 5051)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `COLLECTION_PATH` | yes | — | Absolute path to `collection.anki2` on host |
| `ANKI_SYNC_URL` | no | `http://localhost:8080/` | Anki sync server URL |
| `ANKI_SYNC_USER` | no | `anki` | Sync server username |
| `ANKI_SYNC_PASSWORD` | no | `anki` | Sync server password (**use vault**) |
| `LDOCE5_API_URL` | no | `http://localhost:5050` | ldoce5-api base URL |
| `DECK_NAME` | no | `ODH` | Target Anki deck |
| `NOTE_TYPE_NAME` | no | `ODH` | Anki note type name |
| `API_HOST` | no | `0.0.0.0` | Bind address |
| `API_PORT` | no | `5051` | Listen port |

---

## Data Files (not in image)

The following files must be present on the target host and mounted as volumes:

| File | Mount path | Notes |
|------|------------|-------|
| LDOCE5 `.mdx` | `/opt/anki/dict/...mdx` | Large, copyrighted — copy manually |
| LDOCE5 `.mdd` | `/opt/anki/dict/...mdd` | Audio data |
| `collection.anki2` | `/opt/anki/collection/collection.anki2` | Anki collection, read/write |

---

## Health Checks

```bash
curl http://152.67.113.23:5050/health   # ldoce5-api
curl http://152.67.113.23:5051/health   # anki-writer
```

## Logs

```bash
docker logs ldoce5-api  -f
docker logs anki-writer -f
```
