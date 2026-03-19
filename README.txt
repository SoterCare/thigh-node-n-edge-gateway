Terminal 1: Start the Redis Database (via WSL/Ubuntu)

sudo service redis-server start


Terminal 2: Start the Gateway Master

cd edge-gateway
.\.venv\Scripts\activate
python gateway_master.py


Terminal 3: Start the WebSocket Server

cd edge-gateway
.\.venv\Scripts\activate
python server.py


Terminal 4: Start the Dashboard UI

cd edge-gateway\dashboard-ui
npm run dev




SERVER_BASE_URL="https://backend.sotercare.com"
DEVICE_KEY="TW3DNioQHndfvbcIyVex2KqBCJFwjZuOpPsEYa15"
RASPBERRY_API_KEY="wSX83KInQJ1WiEZvDksPpVhfdxq02Ug7atmMHujLcrG6b9AT5NzYeCoR"
INGEST_MODE="ws"
SAMPLE_INTERVAL_MS="1000"
BATCH_SIZE="10"
RETRY_BASE_MS="1000"
RETRY_MAX_MS="60000"
DEVICE_ID="pi-ffc585939c23"