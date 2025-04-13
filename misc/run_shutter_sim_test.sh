#!/bin/bash

# Create temp directory if it doesn't exist
mkdir -p temp

LOGFILE="temp/shutter-sim.log"
echo "Starting shutter simulator tests" > "$LOGFILE"

# Start simulator in background so that $! is the simulator PID
echo "Starting modbus simulator..." | tee -a "$LOGFILE"
./misc/simulator_start.sh &
SIMULATOR_PID=$!
echo "Simulator started with PID $SIMULATOR_PID" | tee -a "$LOGFILE"
sleep 2

echo "Running UP test for kitchen shutter..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml up kitchen 2>&1 | tee -a "$LOGFILE"

echo "Running DOWN test for kitchen shutter..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml down kitchen 2>&1 | tee -a "$LOGFILE"

echo "Running sunA test for kitchen shutter..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml sunA kitchen 2>&1 | tee -a "$LOGFILE"

# Added tests for living_room_east_right
echo "Running UP test for living_room_east_right shutter..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml up living_room_east_right 2>&1 | tee -a "$LOGFILE"

echo "Running DOWN test for living_room_east_right shutter..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml down living_room_east_right 2>&1 | tee -a "$LOGFILE"

# Added tests for group "east"
echo "Running UP test for group 'east'..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml up east 2>&1 | tee -a "$LOGFILE"

echo "Running DOWN test for group 'east'..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml down east 2>&1 | tee -a "$LOGFILE"

echo "Running sunA test for group 'east'..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml sunA east 2>&1 | tee -a "$LOGFILE"

echo "Running STOP test (global)..." | tee -a "$LOGFILE"
python3 custom_windows_shutter.py --shutter_config custom_windows_shutter.yaml.example --modbus_config misc/simulator-modbus-config.yaml stop 2>&1 | tee -a "$LOGFILE"

echo "Killing simulator with PID $SIMULATOR_PID" | tee -a "$LOGFILE"
pkill -P $SIMULATOR_PID
kill -9 $SIMULATOR_PID 2>/dev/null

echo "Waiting for simulator PID $SIMULATOR_PID to stop..." | tee -a "$LOGFILE"
wait $SIMULATOR_PID 2>/dev/null

echo "Test completed. Check $LOGFILE for test outputs." | tee -a "$LOGFILE"
