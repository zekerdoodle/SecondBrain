#!/bin/bash
cd "$(dirname "$0")"

MODE="${1:-production}"

# Kill previous
fuser -k 8000/tcp 2>/dev/null

echo "Installing dependencies..."
cd client
npm install > /dev/null 2>&1

if [ "$MODE" = "dev" ]; then
    echo "Starting in DEVELOPMENT mode (two servers)..."
    fuser -k 5173/tcp 2>/dev/null

    cd ../server
    if [ ! -d "venv" ]; then python3 -m venv venv; fi
    source venv/bin/activate
    pip install fastapi uvicorn websockets > /dev/null 2>&1
    python3 main.py &
    BACKEND_PID=$!
    cd ..

    cd client
    npm run dev -- --host &
    FRONTEND_PID=$!
    cd ..

    echo ""
    echo "DEV MODE - Second Brain Interface"
    echo "  Frontend: http://localhost:5173"
    echo "  API:      http://localhost:8000"
    echo ""

    wait $BACKEND_PID $FRONTEND_PID
else
    echo "Building frontend for production..."
    npm run build

    if [ ! -d "dist" ]; then
        echo "ERROR: Build failed - dist/ not created"
        exit 1
    fi

    cd ../server
    if [ ! -d "venv" ]; then python3 -m venv venv; fi
    source venv/bin/activate
    pip install fastapi uvicorn websockets > /dev/null 2>&1

    echo ""
    echo "PRODUCTION MODE - Second Brain Interface"
    echo "  App: http://localhost:8000"
    echo ""

    python3 main.py
fi
