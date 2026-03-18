#!/bin/bash
# Start the full CorridorKey dev stack (Supabase + Web Server)
# Run from the deploy/ directory.
#
# Usage:
#   ./dev-up.sh              # rebuild and restart everything
#   ./dev-up.sh --no-build   # restart without rebuilding

set -e
cd "$(dirname "$0")"

COMPOSE="docker compose -f docker-compose.dev.yml --env-file .env --env-file .env.supabase"

# Bring down existing containers first
$COMPOSE down --remove-orphans 2>/dev/null || true

# Start fresh
$COMPOSE up -d --build "$@"

echo ""
echo "CorridorKey:      http://localhost:3000"
echo "Supabase Studio:  http://localhost:54323"
