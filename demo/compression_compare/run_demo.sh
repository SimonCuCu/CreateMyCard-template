#!/bin/zsh
set -euo pipefail

demo_root="${0:A:h}"
compressed_repo="${demo_root:h:h}"
original_repo="/Users/simonhcb/Desktop/huawei/CreateMyCard 9.34.06 AM"
api_key="${WIDGET_SERVICE_DEEPSEEK_API_KEY:-${DEEPSEEK_API_KEY:-}}"

if [[ -z "${api_key}" ]]; then
  print -u2 "请先设置 WIDGET_SERVICE_DEEPSEEK_API_KEY（或 DEEPSEEK_API_KEY）。"
  exit 1
fi

if [[ ! -f "${original_repo}/widget_service/cloud/start_websocket_server.py" ]]; then
  print -u2 "找不到原组件库：${original_repo}"
  exit 1
fi

for port in 8855 8856 8870; do
  if lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    print -u2 "端口 ${port} 已被占用，请先停止对应的本地服务。"
    exit 1
  fi
done

runtime_dir="$(mktemp -d /tmp/create-my-card-comparison.XXXXXX)"
service_pids=()

cleanup() {
  for process_id in "${service_pids[@]}"; do
    kill "${process_id}" 2>/dev/null || true
  done
  rm -rf "${runtime_dir}"
}

trap cleanup EXIT INT TERM

start_service() {
  local repository="$1"
  local port="$2"
  local log_file="$3"
  (
    cd "${repository}/widget_service"
    env \
      WIDGET_SERVICE_SERVER_HOST=127.0.0.1 \
      WIDGET_SERVICE_SERVER_PORT="${port}" \
      WIDGET_SERVICE_ARTIFACT_BASE_URL="http://127.0.0.1:${port}/api/v1/artifacts" \
      WIDGET_SERVICE_ENABLE_A2UI_MODEL_MOCK=false \
      WIDGET_SERVICE_DESIGN_COMPACT_MODEL_BACKEND=openai \
      WIDGET_SERVICE_OPENAI_MASTER_CLIENT=deepseek_http \
      WIDGET_SERVICE_ENABLE_OPENAI_FALLBACK=false \
      WIDGET_SERVICE_DEEPSEEK_API_URL=https://api.deepseek.com \
      WIDGET_SERVICE_DEEPSEEK_HTTP_MODEL=deepseek-chat \
      WIDGET_SERVICE_DEEPSEEK_API_KEY="${api_key}" \
      python3 cloud/start_websocket_server.py
  ) >"${log_file}" 2>&1 &
  service_pids+=("$!")
}

wait_for_health() {
  local port="$1"
  for _ in {1..50}; do
    if curl --silent --fail "http://127.0.0.1:${port}/health" >/dev/null; then
      return 0
    fi
    sleep 0.2
  done
  print -u2 "服务 ${port} 未就绪，请查看 ${runtime_dir}。"
  exit 1
}

start_service "${original_repo}" 8855 "${runtime_dir}/original.log"
start_service "${compressed_repo}" 8856 "${runtime_dir}/compressed.log"
wait_for_health 8855
wait_for_health 8856

print "原组件库：http://127.0.0.1:8855"
print "压缩组件库：http://127.0.0.1:8856"
print "比较页面：http://127.0.0.1:8870"
python3 "${demo_root}/server.py"
