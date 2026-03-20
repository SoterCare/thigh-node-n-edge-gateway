const { io } = require("socket.io-client");
const socket = io("https://backend.sotercare.com/realtime", { transports: ["websocket"] });

let count = 0;
let simulationInterval;

socket.on("connect", () => {
  socket.emit("device_auth", {
    device_id: "pi-ffc585939c23",
    device_key: "TW3DNioQHndfvbcIyVex2KqBCJFwjZuOpPsEYa15",
    timestamp: Date.now(),
  }, (authAck) => {
    console.log("authAck", JSON.stringify(authAck));
    
    simulationInterval = setInterval(() => {
      if (count >= 10) {
        console.log("Finished 10 seconds simulation");
        clearInterval(simulationInterval);
        socket.close();
        process.exit(0);
        return;
      }
      
      const gaitLabels = ["sitting", "walking", "standing"];
      const randomGait = gaitLabels[Math.floor(Math.random() * gaitLabels.length)];
      const randomTemp = parseFloat((36.0 + Math.random() * 2).toFixed(1));
      const randomAmbient = parseFloat((24.0 + Math.random() * 2).toFixed(1));
      const randomMoisture = 25 + Math.floor(Math.random() * 75); // 25 to 99
      
      const logEntry = {
        temp: randomTemp,
        ambientTemp: randomAmbient,
        moisture: randomMoisture,
        gait_label: randomGait,
        sos_trigger: Math.random() > 0.95,
        fall_alert: Math.random() > 0.95,
        unix_timestamp: Math.floor(Date.now() / 1000)
      };

      socket.emit("device_data", { logs: [logEntry] }, (dataAck) => {
        console.log(`[Second ${count}] Sent random log:`, JSON.stringify(logEntry));
        console.log("dataAck:", JSON.stringify(dataAck));
      });
      
      count++;
    }, 1000);
  });
});

socket.on("exception", (e) => console.error("exception", JSON.stringify(e)));
socket.on("connect_error", (e) => console.error("connect_error", e.message));
