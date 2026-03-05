#!/usr/bin/env bash
# scripts/tune_redis.sh
# Optimises Redis for high-frequency in-memory writes.
# Disables all disk persistence and sets an LRU memory cap.
# Run once after Redis is installed:  bash scripts/tune_redis.sh

set -e
echo "[redis] Applying in-memory optimisation settings..."

redis-cli CONFIG SET save ""
redis-cli CONFIG SET appendonly no
redis-cli CONFIG SET maxmemory 128mb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG SET hz 100              # Higher event loop frequency
redis-cli CONFIG SET tcp-keepalive 60

echo "[redis] Done. Verifying..."
redis-cli CONFIG GET save
redis-cli CONFIG GET maxmemory
redis-cli CONFIG GET maxmemory-policy
echo "[redis] Redis is tuned for SoterCare edge workloads."
