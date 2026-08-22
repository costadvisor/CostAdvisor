#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

cleanup() {
    echo -e "\n${RED}Shutting down...${NC}"
    kill $BACKEND_PID $FRONTEND_PID $LANDING_PID 2>/dev/null
    wait $BACKEND_PID $FRONTEND_PID $LANDING_PID 2>/dev/null
    echo "Done."
}
trap cleanup EXIT INT TERM

# Check services
if ! pg_isready -q 2>/dev/null; then
    echo -e "${RED}PostgreSQL is not running. Start it first.${NC}"
    exit 1
fi

if ! redis-cli ping &>/dev/null; then
    echo -e "${RED}Redis is not running. Start it first.${NC}"
    exit 1
fi

# Start backend
echo -e "${GREEN}Starting backend on http://localhost:8000${NC}"
cd "$DIR/backend"
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

# Start frontend
echo -e "${GREEN}Starting frontend on http://localhost:5173${NC}"
cd "$DIR/frontend"
npx vite --host &
FRONTEND_PID=$!

# Start landing page with live reload via browser-sync
echo -e "${GREEN}Starting landing page on http://localhost:3333 (live reload on save)${NC}"
cd "$DIR"
npx browser-sync start \
    --server "landing" \
    --files "landing" \
    --port 3333 \
    --no-open \
    --no-notify &
LANDING_PID=$!

echo -e "\n${GREEN}All services running — press Ctrl+C to stop all.${NC}"
echo -e "  ${YELLOW}Backend${NC}   http://localhost:8000"
echo -e "  ${YELLOW}App${NC}       http://localhost:5173"
echo -e "  ${YELLOW}Landing${NC}   http://localhost:3333\n"
wait
#triigered
