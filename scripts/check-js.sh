#!/usr/bin/env bash
# Verificação de sintaxe dos módulos ES da dashboard (sem build de frontend).
# Uso: ./scripts/check-js.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JS_DIR="$ROOT/src/mcp_rag_api/static/js"

fail=0
while IFS= read -r f; do
  if ! node --input-type=module --check < "$f" 2>/tmp/check-js-error.txt; then
    echo "ERRO de sintaxe em $f:" >&2
    cat /tmp/check-js-error.txt >&2
    fail=1
  fi
done < <(find "$JS_DIR" -name '*.js' -type f | sort)

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "OK — sintaxe válida em todos os módulos de $JS_DIR"
