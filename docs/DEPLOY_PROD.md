# Production-ish Deployment Blueprint (GCP + Hetzner)

This guide provides a pragmatic deployment baseline for serious OpenClaw + OpenPill usage:

- 24/7 scientific batch/agent workloads
- sales/ops contexts with sensitive customer data

It is intentionally conservative on safety and cost.

## Target topology

- One VM per environment (GCP or Hetzner).
- `openclaw`, `openpill-api`, and `mongodb` in one private Docker network.
- Only one public entrypoint (reverse proxy / ingress).
- `mongodb` is never exposed publicly.

Use `docker-compose.prod.yml` as a starting point.

## Shared preparation

1. Copy production env file:

```bash
cp .env.example .env.production
```

2. Set at least:

- `OPENPILL_API_KEY`
- `MONGO_URI` if you do not use the compose default
- model policy vars (`EXTRACTOR_MODEL_POLICY`, `JANITOR_MODEL_POLICY`)
- A/B guard vars (`AB_GUARDS_ENABLED`, `AB_ALLOW_EXTERNAL`, `AB_MAX_EXTERNAL_CALLS`)

3. Set baseline runtime defaults:

- `EXTRACTOR_MODEL_POLICY=local_first`
- `JANITOR_MODEL_POLICY=local_first`
- `AB_GUARDS_ENABLED=true`
- `AB_ALLOW_EXTERNAL=false` (enable only intentionally)
- `AB_MAX_EXTERNAL_CALLS=20` (tune per workload)
- `HYBRID_RETRIEVAL_ENABLED=false` until measured benefit

## Preset A: GCP with existing OpenClaw ingress (openclaw-go style)

Use this if OpenClaw is already reachable via OAuth/HTTPS and you only want to add OpenPill cleanly.

1. Keep your existing GCP ingress/auth stack as-is.
2. Start OpenPill core services only (no extra edge proxy):

```bash
docker compose -f docker-compose.prod.yml up -d mongodb openpill-api
```

3. Enable MCP SSE only if your host requires remote MCP transport:

```bash
# enable MCP SSE service
docker compose -f docker-compose.prod.yml --profile mcp up -d
```

4. Verify health:

```bash
curl -s http://127.0.0.1:8080/health
```

5. Route existing ingress to OpenPill internally (same VM/VPC preferred).

Reference setup style: [tjcrone/openclaw-go](https://github.com/tjcrone/openclaw-go)

### GCP-specific notes

- Add OpenPill as sibling services on the same VM as OpenClaw where possible.
- Keep OpenPill behind the same OAuth/identity boundary as OpenClaw dashboards.
- Prefer same-region placement for OpenClaw + OpenPill + MongoDB to reduce latency and failure modes.

## Preset B: Hetzner single-VM Compose-only

Use this if you run everything on one Hetzner VM and manage ingress in this repository.

1. Start core services:

```bash
docker compose -f docker-compose.prod.yml up -d mongodb openpill-api
```

2. Enable built-in edge proxy profile:

```bash
docker compose -f docker-compose.prod.yml --profile edge up -d
```

3. Update `deploy/Caddyfile` with your real domain (replace `openpill.example.com`).

4. Optional MCP SSE:

```bash
docker compose -f docker-compose.prod.yml --profile mcp up -d
```

5. Verify health through host/proxy and locally.

### Hetzner-specific notes

- Single VM + Docker Compose is fine initially for serious usage if backups and access controls are in place.
- Add TLS + access control before exposing endpoints.
- Keep database private and persist volumes on durable storage.

## Decision guide

- Pick **Preset A (GCP)** if OpenClaw ingress and auth already exist and are stable.
- Pick **Preset B (Hetzner)** if you want one self-managed Compose stack with local control.

## Day-2 operations (required for serious usage)

Use these as baseline runbook steps for both presets.

### Hermes-Agent bridge quick demo

If you run Hermes and want OpenPill as a durable external memory backend, you can use the included bridge template in `integrations/hermes/openpill/`.

Quick local check (against a running OpenPill API):

```bash
python3 integrations/hermes/openpill/openpill_client.py semantic "idempotency key" --limit 5
python3 integrations/hermes/openpill/openpill_client.py neighbors "<pill_id>"
```

### 1) Daily Mongo backup

Create compressed dump (inside docker network, written on host):

```bash
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T mongodb \
  mongodump --archive --gzip > "backups/mongo-$(date +%F-%H%M).archive.gz"
```

Retention example (keep last 14 days):

```bash
find backups -type f -name 'mongo-*.archive.gz' -mtime +14 -delete
```

### 2) Restore drill (weekly/monthly)

Do this in a non-production environment first:

```bash
gunzip -c backups/mongo-YYYY-MM-DD-HHMM.archive.gz | \
docker compose -f docker-compose.prod.yml exec -T mongodb \
  mongorestore --archive --drop
```

After restore, validate:

```bash
curl -s http://127.0.0.1:8080/health
```

Then run a semantic query against your known test entry.

### 3) Update procedure (small safe steps)

```bash
# 1) snapshot before update
mkdir -p backups
docker compose -f docker-compose.prod.yml exec -T mongodb \
  mongodump --archive --gzip > "backups/preupdate-$(date +%F-%H%M).archive.gz"

# 2) pull latest code/images
git pull
docker compose -f docker-compose.prod.yml pull

# 3) rolling restart core services
docker compose -f docker-compose.prod.yml up -d mongodb openpill-api
```

Post-update checks:

```bash
curl -s http://127.0.0.1:8080/health
docker compose -f docker-compose.prod.yml ps
```

### 4) Rollback minimum

If update fails:

```bash
# 1) return to previous known-good commit
git checkout <known-good-commit>

# 2) restart services from that revision
docker compose -f docker-compose.prod.yml up -d mongodb openpill-api
```

If schema/data corruption is suspected, restore last pre-update backup.

### 5) Monitoring minimum

- Track container restarts (`docker compose ps` / host monitoring).
- Alert on API health failures (`/health` probe).
- Alert on disk usage (Mongo volume and backup directory).
- Keep Docker log rotation enabled to avoid disk exhaustion.

## Security baseline checklist

- API key auth enabled (`OPENPILL_API_KEY`).
- TLS enabled at ingress.
- No public MongoDB port.
- Secrets not committed; use env files + host secret management.
- Log rotation enabled for Docker and host.

## Reliability checklist

- `restart: unless-stopped` on all long-running services.
- Health checks configured.
- Daily Mongo backup job.
- Periodic restore test (backup without restore test is not enough).

## OpenClaw wiring

- OpenClaw should call OpenPill over internal DNS (for example `http://openpill-api:8080`) when in the same compose project.
- Avoid `localhost` assumptions inside containers.
- For Mongo replica set discovery edge cases, use an explicit URI with `directConnection=true` when needed.

## Cost control in production

- Keep local-first as default.
- External providers only via explicit opt-in.
- Use A/B guard caps to prevent silent budget drift.
- Track external-call volume per run or day.
