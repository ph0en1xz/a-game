#!/usr/bin/env bash
#
# verify_langfuse.sh — end-to-end health check for the Langfuse trace path.
#
#   ./scripts/verify_langfuse.sh              checks only, sends nothing
#   ./scripts/verify_langfuse.sh --trace      also publishes a match and proves a trace lands
#   ./scripts/verify_langfuse.sh --trace 560916   same, with a specific match id
#
# --trace calls the real Anthropic API. One Haiku call, a fraction of a cent.
#
# Exit 0 if every check passes, 1 otherwise.

set -uo pipefail

NS=a-game
CH=a-game-lang-clickhouse-0
FAIL=0
SEND=0
MATCH=""

for arg in "$@"; do
  case "$arg" in
    --trace) SEND=1 ;;
    [0-9]*)  MATCH="$arg" ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $arg"; exit 2 ;;
  esac
done

pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
info() { printf '       %s\n' "$1"; }
head() { printf '\n\033[1m%s\033[0m\n' "$1"; }

ch() { kubectl exec -n "$NS" "$CH" -- clickhouse-client --query "$1" 2>/dev/null; }

# ---------------------------------------------------------------- 1. pods
head "1. pods"
NOTREADY=$(kubectl get pods -n "$NS" --no-headers 2>/dev/null \
  | grep -vE 'Completed' | awk '$2!="1/1"{print "  " $1 " " $2 " " $3}')
if [ -z "$NOTREADY" ]; then
  pass "all pods 1/1 Running"
else
  fail "some pods are not ready:"; echo "$NOTREADY"
fi

# ------------------------------------------------------- 2. web is serving
# 127.0.0.1 on purpose: this is what proves HOSTNAME=0.0.0.0 is still set.
# Without it the app binds only the pod IP and port-forward breaks, while
# every readiness probe keeps passing because the kubelet probes the pod IP.
head "2. langfuse-web serving on loopback"
HEALTH=$(kubectl exec -n "$NS" deploy/a-game-lang-web -- \
  wget -qO- --timeout=5 http://127.0.0.1:3000/api/public/health 2>/dev/null)
case "$HEALTH" in
  *'"status":"OK"'*) pass "health $HEALTH" ;;
  "") fail "no response on 127.0.0.1:3000 — check HOSTNAME=0.0.0.0 in 93-langfuse-web.yaml" ;;
  *) fail "unexpected: $HEALTH" ;;
esac

BIND=$(kubectl exec -n "$NS" deploy/a-game-lang-web -- \
  sh -c "awk 'NR>1 && \$4==\"0A\"{print \$2}' /proc/net/tcp" 2>/dev/null | grep -i ':0BB8')
case "$BIND" in
  00000000:*) pass "bound 0.0.0.0:3000 — port-forward will work" ;;
  "")         fail "port 3000 not listening on ipv4" ;;
  *)          fail "bound $BIND (single address) — port-forward will be refused" ;;
esac

# ------------------------------------------------------- 3. callback wiring
head "3. litellm callback"
CB=$(kubectl logs -n "$NS" -l app.kubernetes.io/name=a-game-litellm --tail=300 2>/dev/null \
  | grep -i 'Success Callbacks' | tail -1)
case "$CB" in
  *langfuse_otel*) pass "langfuse_otel loaded" ;;
  *langfuse*)      fail "plain 'langfuse' is loaded — v4 rejects the legacy SDK, traces are dropped silently" ;;
  *)               fail "no success callback found in litellm logs" ;;
esac

# ----------------------------------------------------------- 4. send a trace
BEFORE=$(ch "select count() from events_full")
BEFORE=${BEFORE:-0}

if [ "$SEND" = "1" ]; then
  head "4. publishing a match (real Anthropic call)"
  BRAIN=$(kubectl get pod -n "$NS" -l app.kubernetes.io/name=a-game-brain \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -z "$MATCH" ]; then
    MATCH=$(kubectl exec -n "$NS" a-game-postgres-0 -- \
      psql -U postgres -d a_game_db -tAc \
      "select id from a_game.match where status='SCHEDULED' order by random() limit 1" 2>/dev/null | tr -d '[:space:]')
  fi
  if [ -z "$MATCH" ]; then
    fail "could not find a match id to publish"
  else
    info "match $MATCH via $BRAIN"
    kubectl exec -n "$NS" "$BRAIN" -- python -c "
import asyncio, json, aio_pika
from app.config import settings
async def main():
    conn = await aio_pika.connect_robust(settings.amqp_url)
    ch = await conn.channel()
    await ch.declare_queue(settings.rabbitmq_queue, durable=True)
    await ch.default_exchange.publish(
        aio_pika.Message(body=json.dumps($MATCH).encode()),
        routing_key=settings.rabbitmq_queue)
    await conn.close()
asyncio.run(main())" 2>/dev/null && pass "published" || fail "publish failed"
    info "waiting 45s for the call and the ClickHouse flush"
    sleep 45
  fi
fi

# ------------------------------------------------------ 5. rows in clickhouse
head "5. clickhouse"
AFTER=$(ch "select count() from events_full"); AFTER=${AFTER:-0}
info "events_full: $BEFORE -> $AFTER"

if [ "$SEND" = "1" ]; then
  if [ "$AFTER" -gt "$BEFORE" ]; then
    pass "trace landed (+$((AFTER-BEFORE)) rows; 2 per call is normal)"
  else
    fail "no new rows — the trace was lost"
  fi
elif [ "$AFTER" -gt 0 ]; then
  pass "$AFTER events stored"
else
  info "no events yet — run with --trace to send one"
fi

if [ "$AFTER" -gt 0 ]; then
  echo
  ch "select name, provided_model_name, usage_details, total_cost,
      dateDiff('millisecond', start_time, end_time) as latency_ms
      from events_full order by start_time desc limit 2 format Vertical"
fi

# ------------------------------------------------------- 6. silent rejections
# The failure that looks like success: the completion returns 200 and the
# callback fails soft, so the only evidence is rows that never arrive.
head "6. rejected events (last 15m)"
REJ=$(kubectl logs -n "$NS" -l app.kubernetes.io/name=a-game-lang-web --since=15m 2>/dev/null \
  | grep -ci 'Rejected')
if [ "${REJ:-0}" -eq 0 ]; then
  pass "none"
else
  fail "$REJ rejection log lines — traces are being dropped"
  kubectl logs -n "$NS" -l app.kubernetes.io/name=a-game-lang-web --since=15m 2>/dev/null \
    | grep -i 'Rejected' | tail -1 | cut -c1-200
fi

# ------------------------------------------------------------ 7. supporting
head "7. supporting stores"
QD=$(kubectl exec -n "$NS" a-game-redis-0 -- redis-cli -n 1 dbsize 2>/dev/null | tr -d '[:space:]')
info "redis db1 keys : ${QD:-?}"
OBJ=$(kubectl exec -n "$NS" a-game-lang-minio-0 -- \
  sh -c 'find /data/langfuse -type f 2>/dev/null | wc -l' 2>/dev/null | tr -d '[:space:]')
info "minio objects  : ${OBJ:-?}   (0 is expected — the OTLP path does not write blobs)"

# --------------------------------------------------------------- verdict
head "verdict"
if [ "$FAIL" -eq 0 ]; then
  printf '  \033[32mall checks passed\033[0m\n'
  info "UI: kubectl port-forward svc/a-game-lang-web 3001:3000 -n $NS  ->  http://localhost:3001"
  exit 0
else
  printf '  \033[31m%d check(s) failed\033[0m\n' "$FAIL"
  exit 1
fi
