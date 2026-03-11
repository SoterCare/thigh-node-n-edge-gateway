import live_inference
import random
import time

print("Testing Model Inference...")

# Generate fake buffer
mock_window = []
for _ in range(live_inference.SAMPLES_PER_WINDOW):
    # AccX, AccY, AccZ, GyroX, GyroY, GyroZ
    mock_window.append([random.uniform(-2, 2) for _ in range(6)])

# Run directly
live_inference.run_inference(mock_window)
print("Test completed.")
