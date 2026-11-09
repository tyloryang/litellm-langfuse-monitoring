#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose --env-file .env config -q
docker compose ps
curl -fsS "http://$(grep '^HOST_IP=' .env | cut -d= -f2):4000/health/readiness" || true
curl -fsS "http://$(grep '^HOST_IP=' .env | cut -d= -f2):3000/api/public/health" || true
