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
