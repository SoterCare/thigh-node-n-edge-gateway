@echo off
echo ============================================
echo    SoterCare Edge Gateway  -  Starting
echo ============================================

echo [1/5] Starting Redis via WSL...
start "Redis" wsl -e bash -c "sudo service redis-server start && echo Redis started. && sleep infinity"

echo Waiting 3s for Redis to be ready...
timeout /t 3 /nobreak >nul

echo [2/5] Starting Gateway Master (UDP + BLE receiver)...
start "Gateway Master" cmd /k "..\\.venv\\Scripts\\python.exe gateway_master.py"

echo [3/5] Starting WebSocket Server (local dashboard bridge)...
start "WebSocket Server" cmd /k "..\\.venv\\Scripts\\python.exe server.py"

echo [4/5] Starting Backend Sync (cloud uplink)...
start "Backend Sync" cmd /k "..\\.venv\\Scripts\\python.exe backend_sync.py"

echo [5/5] Starting Dashboard UI...
start "Dashboard UI" cmd /k "cd dashboard-ui && npm run dev"

echo.
echo All services started! Dashboard: http://localhost:5173
