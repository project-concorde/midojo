#!/usr/bin/env bash
set -euo pipefail

set -a
source .env
set +a

CONTROL_PORT=8099
MCP_PORT=8098
CONTROL_URL="http://localhost:${CONTROL_PORT}"
MCP_URL="http://localhost:${MCP_PORT}/mcp"

cleanup() {
    for pid in "${SERVER_PID:-}" "${MCP_PID:-}"; do
        [[ -n "$pid" ]] && kill "$pid" 2>/dev/null && wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

echo "=== Starting control plane on port ${CONTROL_PORT} ==="
uv run midojo-serve --port "$CONTROL_PORT" &
SERVER_PID=$!

echo "Waiting for control plane..."
for i in $(seq 1 20); do
    curl -sf "${CONTROL_URL}/suite" >/dev/null 2>&1 && break
    kill -0 "$SERVER_PID" 2>/dev/null || { echo "FAIL: Control plane died"; exit 1; }
    sleep 0.5
done
curl -sf "${CONTROL_URL}/suite" >/dev/null 2>&1 || { echo "FAIL: Control plane not ready"; exit 1; }
echo "Control plane ready"

echo "=== Creating run ==="
RUN_ID=$(curl -sf -X POST "${CONTROL_URL}/runs" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Run ID: ${RUN_ID}"

echo "=== Creating evaluation ==="
EVAL_RESP=$(curl -sf -X POST "${CONTROL_URL}/runs/${RUN_ID}/evaluations" \
    -H "Content-Type: application/json" \
    -d '{"user_task_id": "user_task_0"}')
EVAL_ID=$(echo "$EVAL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
PROMPT=$(echo "$EVAL_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['prompt'])")
echo "Eval ID: ${EVAL_ID}"
echo "Prompt: ${PROMPT}"

echo ""
echo "=== Starting MCP SDK server on port ${MCP_PORT} ==="
MIDOJO_URL="$CONTROL_URL" MIDOJO_RUN_ID="$RUN_ID" MIDOJO_EVAL_ID="$EVAL_ID" \
    uv run weather-mcp-serve --port "$MCP_PORT" &
MCP_PID=$!

echo "Waiting for MCP server..."
for i in $(seq 1 20); do
    kill -0 "$MCP_PID" 2>/dev/null || { echo "FAIL: MCP server died"; exit 1; }
    # MCP streamable HTTP needs proper headers; just check the process is up
    sleep 0.5
    # Try an initialize call to confirm readiness
    INIT_RESP=$(curl -s -X POST "http://localhost:${MCP_PORT}/mcp" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' \
        2>/dev/null) && break
done
echo "MCP server ready"

# Extract session ID from the initialize response headers
SESSION_URL=$(curl -s -D- -o/dev/null -X POST "http://localhost:${MCP_PORT}/mcp" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"test","version":"0.1"}}}' \
    2>/dev/null | grep -i 'mcp-session-id' | tr -d '\r' | awk '{print $2}')
echo "Session ID: ${SESSION_URL}"

echo ""
echo "=== Calling get_weather via MCP ==="
TOOL_RESP=$(curl -s -X POST "http://localhost:${MCP_PORT}/mcp" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    ${SESSION_URL:+-H "Mcp-Session-Id: ${SESSION_URL}"} \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"get_weather","arguments":{"city":"New York"}}}')
echo "Tool response: ${TOOL_RESP}"

echo ""
echo "=== Checking function calls recorded ==="
FC_RESP=$(curl -sf "${CONTROL_URL}/runs/${RUN_ID}/evaluations/${EVAL_ID}/function-calls")
echo "$FC_RESP" | python3 -m json.tool

FC_COUNT=$(echo "$FC_RESP" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
echo ""
if [[ "$FC_COUNT" -gt 0 ]]; then
    echo "PASS: ${FC_COUNT} function call(s) recorded"
else
    echo "FAIL: No function calls recorded"
    exit 1
fi

echo ""
echo "=== Done ==="
