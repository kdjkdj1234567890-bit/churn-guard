#!/usr/bin/env bash
# 단골 지키미 — 조용히 떠나는 단골을 붙잡으세요
# 사용법: bash run.sh   →   http://localhost:5008
set -euo pipefail

cd "$(dirname "$0")"

if ! python3 -c "import flask" >/dev/null 2>&1; then
  echo "Flask를 설치합니다..."
  pip3 install --quiet flask
fi

# 포트 5008이 사용 중이면 자동으로 다음 빈 포트로 전환
PORT=5008
PORT=$(python3 - "$PORT" <<'EOF'
import socket, sys
p = int(sys.argv[1])
while True:
    s = socket.socket()
    try:
        s.bind(("0.0.0.0", p))
        s.close()
        print(p); break
    except OSError:
        p += 1
EOF
)

echo "==============================================="
echo "  단골 지키미 — 조용히 떠나는 단골을 붙잡으세요"
echo "  접속: http://localhost:${PORT}"
echo "  단골 명단: http://localhost:${PORT}/customers"
echo "  떠나가는 단골: http://localhost:${PORT}/risk"
echo "  쿠폰 보내기: http://localhost:${PORT}/coupons/new"
echo "  효과 보기: http://localhost:${PORT}/effect"
echo "  오늘 브리핑: http://localhost:${PORT}/briefing"
echo "  (5008이 사용 중이면 자동으로 다음 빈 포트로 켜집니다)"
echo "  (종료: Ctrl+C)"
echo "==============================================="
PORT="$PORT" exec python3 app.py
