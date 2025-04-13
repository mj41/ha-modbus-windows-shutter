#!/usr/bin/env python3

import sys
import argparse
import logging
import time
from time import sleep
from typing import Dict, Any, Tuple, List, Optional, Union
from collections import defaultdict
import math

from modbus_relay import ModbusRelayClient, ModbusRelayError, validate_config
import custom_windows_shutter_constants as constants
from config_loader import ConfigLoader

# Configure logging and create a module logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Define action constants
ACTION_STOP = 'stop'

# Type alias for timeline events: (command, data)
# command: "on" -> data: List[int] of active relays
# command: "delay" -> data: float seconds to wait
TimelineEvent = Tuple[str, Union[List[int], float]]

# Small tolerance for float comparisons
FLOAT_TOLERANCE = 1e-6

class ShutterController:
    """Encapsulates the logic for controlling shutters and groups."""

    def __init__(self, modbus_config: Dict[str, Any], shutters: Dict[str, Any], groups: Dict[str, Any]) -> None:
        """Initialize with Modbus configuration, shutters, and groups."""
        self.modbus_config = modbus_config
        self.shutters = shutters
        self.groups = groups
        self.client: Optional[ModbusRelayClient] = None
        try:
            self.client = ModbusRelayClient(modbus_config)
        except Exception as e:
            logger.error(f"Failed to initialize Modbus client structure: {e}")
            raise

    def _ensure_connected(self) -> bool:
        """Ensure the client is connected, attempting to connect if necessary."""
        if not self.client:
            logger.error("Modbus client not initialized.")
            return False
        if not self.client.client or not self.client.client.is_socket_open():
            logger.info("Modbus client not connected. Attempting to connect...")
            if not self.client.connect():
                logger.error("Failed to connect to Modbus device.")
                return False
            logger.info("Modbus connection successful.")
        return True

    def __enter__(self):
        """Enter method for context management."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit method for context management; ensures the Modbus client is closed."""
        if self.client:
            self.client.close()

    def _generate_group_timeline(self, group_shutters: List[str], action: str) -> List[TimelineEvent]:
        """Generate a sorted timeline of state changes for a group action."""
        # Check for empty input group immediately
        if not group_shutters:
            logger.info("Input group is empty. Returning empty timeline.")
            return []

        relay_events: List[Tuple[float, float, int]] = []
        current_shutter_offset = 0.0

        logger.info(f"Generating state-based timeline for action '{action}'...")

        for shutter_name in group_shutters:
            logger.debug(f"Processing shutter '{shutter_name}' for timeline at offset {current_shutter_offset:.3f}s")
            if shutter_name not in self.shutters:
                logger.warning(f"Shutter '{shutter_name}' not found. Skipping.")
                continue
            shutter_config = self.shutters[shutter_name]
            if action not in shutter_config:
                logger.warning(f"Action '{action}' not defined for '{shutter_name}'. Skipping.")
                continue

            action_config = shutter_config[action]
            relay_seq = action_config.get('relay_seq')
            if not relay_seq:
                logger.info(f"Action '{action}' for '{shutter_name}' is empty. Skipping.")
                continue

            shutter_local_time = 0.0
            for step in relay_seq:
                relay_num = step['relay_num']
                delay = step['delay']
                start_time = current_shutter_offset + shutter_local_time
                end_time = start_time + delay
                relay_events.append((start_time, end_time, relay_num))
                logger.debug(f"  Relay {relay_num}: ON at {start_time:.3f}s, OFF at {end_time:.3f}s")
                shutter_local_time += delay

            current_shutter_offset += shutter_local_time

        # Check if any events were actually generated
        if not relay_events:
            logger.info("No relay events generated (shutters missing/actions empty?). Returning empty timeline.")
            return []

        time_points = set([0.0])
        for start, end, _ in relay_events:
            time_points.add(start)
            time_points.add(end)

        sorted_times = sorted(list(time_points))
        timeline: List[TimelineEvent] = []
        last_time = 0.0

        for t in sorted_times:
            if abs(t - last_time) < FLOAT_TOLERANCE and timeline:
                continue

            active_relays = set()
            for start, end, r_num in relay_events:
                if (start <= t + FLOAT_TOLERANCE) and (t < end - FLOAT_TOLERANCE):
                    active_relays.add(r_num)

            current_active_list = sorted(list(active_relays))
            delay_duration = t - last_time

            if delay_duration > FLOAT_TOLERANCE:
                last_state = timeline[-1][1] if timeline and timeline[-1][0] == 'on' else []
                if not timeline or current_active_list != last_state:
                    logger.debug(f"  Timeline Add: ('delay', {delay_duration:.3f})")
                    timeline.append(("delay", round(delay_duration, 3)))

            if not timeline or timeline[-1] != ("on", current_active_list):
                logger.debug(f"  Timeline Add: ('on', {current_active_list}) at {t:.3f}s")
                timeline.append(("on", current_active_list))

            last_time = t

        final_state = timeline[-1][1] if timeline and timeline[-1][0] == 'on' else []
        if final_state:
            max_end_time = max([end for _, end, _ in relay_events] + [0.0])
            if max_end_time > last_time + FLOAT_TOLERANCE:
                final_delay = round(max_end_time - last_time, 3)
                if final_delay > FLOAT_TOLERANCE:
                    logger.debug(f"  Timeline Add: ('delay', {final_delay:.3f}) (final)")
                    timeline.append(("delay", final_delay))
            logger.debug(f"  Timeline Add: ('on', []) (final state at {max_end_time:.3f}s)")
            timeline.append(("on", []))

        clean_timeline = []
        last_on_state = None
        for i, event in enumerate(timeline):
            cmd, data = event
            if cmd == "delay":
                if data > FLOAT_TOLERANCE:
                    clean_timeline.append(event)
            elif cmd == "on":
                if data != last_on_state:
                    clean_timeline.append(event)
                    last_on_state = data

        if clean_timeline and clean_timeline[-1][0] == 'delay':
            clean_timeline.pop()

        logger.info(f"State-based timeline generation complete. Events: {len(clean_timeline)}")
        return clean_timeline

    def _execute_timeline(self, timeline: List[TimelineEvent]) -> bool:
        """Execute a pre-generated state-based timeline."""
        if not self._ensure_connected(): return False
        assert self.client is not None

        logger.info("Executing state-based timeline...")
        success = True
        try:
            logger.debug("Timeline: Performing initial reset.")
            if not self.client.reset_relays():
                raise ModbusRelayError("Failed initial reset before timeline execution")

            for i, event in enumerate(timeline):
                command, data = event
                logger.info(f"Timeline Step {i+1}: Executing {command} with data {data}")

                if command == "delay":
                    if isinstance(data, (int, float)) and data > FLOAT_TOLERANCE:
                        logger.debug(f"  Sleeping for {data:.3f} seconds...")
                        sleep(data)
                elif command == "on":
                    if isinstance(data, list):
                        relay_state_list = [False] * 32
                        active_relays_str = []
                        for relay_num in data:
                            if 1 <= relay_num <= 32:
                                relay_state_list[relay_num - 1] = True
                                active_relays_str.append(str(relay_num))
                            else:
                                logger.warning(f"  Invalid relay number {relay_num} in 'on' command, skipping.")
                        logger.debug(f"  Setting relays ON: {', '.join(active_relays_str) if active_relays_str else 'None'}")
                        if not self.client.write_relays(relay_state_list):
                            raise ModbusRelayError(f"Timeline: Failed to set relay state {data}")
                    else:
                        logger.error(f"  Invalid data type for 'on' command: {type(data)}")
                        raise ValueError("Invalid timeline event data for 'on' command")
                else:
                    logger.error(f"Timeline: Unknown command '{command}'")
                    raise ValueError(f"Unknown timeline command: {command}")

            logger.info("Timeline execution completed successfully.")

        except ModbusRelayError as e:
            logger.error(f"Timeline: Modbus error during execution: {e}")
            success = False
        except Exception as e:
            logger.exception(f"Timeline: Unexpected error during execution: {e}")
            success = False
        finally:
            logger.info("Timeline: Performing final safety reset.")
            if self.client and self.client.client and self.client.client.is_socket_open():
                if not self.client.reset_relays():
                    logger.error("Timeline: Failed to perform final safety reset!")
            else:
                logger.warning("Timeline: Client not connected or available for final safety reset.")

        return success

    def control_group(self, group_name: str, action: str) -> bool:
        """Control a group of shutters using a generated state-based timeline."""
        if group_name not in self.groups:
            logger.error(f"Group '{group_name}' not found in configuration.")
            return False

        group_shutters = self.groups[group_name]
        if not group_shutters:
            logger.warning(f"Group '{group_name}' is empty. Nothing to do.")
            return True

        logger.info(f"Controlling group '{group_name}' via state-based timeline for action '{action}'...")

        try:
            timeline = self._generate_group_timeline(group_shutters, action)
        except Exception as e:
            logger.exception(f"Failed to generate timeline for group '{group_name}', action '{action}': {e}")
            return False

        if not timeline:
            logger.info(f"Generated timeline for group '{group_name}', action '{action}' is empty. Nothing to execute.")
            return True

        overall_success = self._execute_timeline(timeline)

        if overall_success:
            logger.info(f"Group '{group_name}' action '{action}' completed via timeline.")
        else:
            logger.warning(f"Group '{group_name}' action '{action}' failed during timeline execution.")

        return overall_success

    def check_device_address(self) -> bool:
        """Check and log the device address."""
        if not self._ensure_connected(): return False
        assert self.client is not None

        try:
            resp = self.client.read_device_address()
            if not resp:
                return False
            logger.info(f"Slave ID confirmed: {resp.registers}")
            return True
        except ModbusRelayError as e:
            logger.error(f"Failed to read device address: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error reading device address: {e}")
            return False

    def handle_stop_action(self) -> bool:
        """Handle the stop action by resetting all relays."""
        if not self._ensure_connected(): return False
        assert self.client is not None

        logger.info("Global STOP command received. Resetting all relays.")
        try:
            if not self.client.reset_relays():
                return False
            logger.info("All relays reset successfully.")
            return True
        except ModbusRelayError as e:
            logger.error(f"Failed to reset relays during STOP action: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during STOP action: {e}")
            return False

    def handle_action(self, action: str, target: str) -> bool:
        """Handle a specific action for a shutter or group."""
        if not self.check_device_address():
            return False

        if target in self.shutters:
            logger.info(f"Controlling single shutter '{target}' via timeline for action '{action}'")
            timeline = self._generate_group_timeline([target], action)
            if not timeline:
                logger.info(f"Generated timeline for single shutter '{target}', action '{action}' is empty.")
                return True
            return self._execute_timeline(timeline)

        elif target in self.groups:
            logger.info(f"Controlling shutter group '{target}' via timeline for action '{action}'")
            return self.control_group(target, action)
        else:
            logger.error(f"Error: Unknown shutter or group '{target}'")
            return False


def main() -> None:
    """Main function to parse arguments and control shutters or groups."""
    parser = argparse.ArgumentParser(description="Window Shutter Control (Config v1.x.x)")
    parser.add_argument("--modbus_config", type=str, default=constants.MODBUS_CONFIG_PATH, help="Path to Modbus configuration file")
    parser.add_argument("--shutter_config", type=str, default=constants.SHUTTER_CONFIG_PATH, help="Path to Shutter configuration file (v1.x.x)")
    parser.add_argument("action", type=str, help=f"Action to perform (e.g., 'up', 'down', 'sunA', '{ACTION_STOP}')")
    parser.add_argument("target", nargs='?', help="Shutter or group name (required for all actions except 'stop')")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s", force=True)

    if args.action != ACTION_STOP and not args.target:
        parser.error(f"Target shutter or group name required for action '{args.action}'")
    if args.action == ACTION_STOP and args.target:
        logger.warning(f"Target '{args.target}' ignored for '{ACTION_STOP}' action.")
        args.target = None

    try:
        config_loader = ConfigLoader()
        modbus_config, shutters, groups = config_loader.load_and_validate_configs(args.modbus_config, args.shutter_config)
        if 'DEBUG_MODBUS' not in modbus_config:
            modbus_config['DEBUG_MODBUS'] = args.debug
        elif args.debug:
            modbus_config['DEBUG_MODBUS'] = True

    except Exception:
        sys.exit(1)

    exit_code = 0
    try:
        with ShutterController(modbus_config, shutters, groups) as controller:
            success = False
            if args.action == ACTION_STOP:
                success = controller.handle_stop_action()
            else:
                success = controller.handle_action(args.action, args.target)

            if not success:
                logger.error(f"Action '{args.action}' failed for target '{args.target}'.")
                exit_code = 1

    except ModbusRelayError as e:
        logger.error(f"Modbus communication error: {e}")
        exit_code = 1
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
