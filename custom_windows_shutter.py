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
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Define action constants
ACTION_STOP = 'stop'

# Type alias for timeline events: (command, data)
# command: "on" -> data: List[int] of active relays
# command: "delay" -> data: int milliseconds to wait
TimelineEvent = Tuple[str, Union[List[int], int]]

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
        """Generate a sorted timeline based on states at unique time points (milliseconds)."""
        if not group_shutters:
            logger.info("Input group is empty. Returning empty timeline.")
            return []

        # 1. Gather Relay Events & Max Duration
        relay_events: List[Tuple[int, int, int]] = []
        max_shutter_duration_ms = 0
        logger.info(f"Generating merged timeline for action '{action}' (using milliseconds)...")
        for shutter_name in group_shutters:
            logger.debug(f"Processing shutter '{shutter_name}' for merged timeline")
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
            shutter_local_time_ms = 0
            for step in relay_seq:
                relay_num = step['relay_num']
                delay_ms = step['delay_ms']
                start_time_ms = shutter_local_time_ms
                end_time_ms = start_time_ms + delay_ms
                relay_events.append((start_time_ms, end_time_ms, relay_num))
                logger.debug(f"  Relay {relay_num}: ON at {start_time_ms}ms, OFF at {end_time_ms}ms (relative to shutter start)")
                shutter_local_time_ms += delay_ms
            max_shutter_duration_ms = max(max_shutter_duration_ms, shutter_local_time_ms)

        if not relay_events and max_shutter_duration_ms == 0:
            logger.info("No relay events generated and max duration is 0. Returning empty timeline.")
            return []

        # 2. Identify Key Time Points
        time_points_ms = set([0, max_shutter_duration_ms])
        for start, end, _ in relay_events:
            time_points_ms.add(start)
            time_points_ms.add(end)
        sorted_times_ms = sorted([t for t in time_points_ms if t >= 0])
        sorted_times_ms = sorted(list(set(sorted_times_ms)))

        logger.debug(f"Unique time points for merging (ms): {sorted_times_ms}")

        if not sorted_times_ms:
            if max_shutter_duration_ms == 0:
                logger.info("Max duration is 0 and no time points identified. Returning empty timeline.")
                return []
            else:
                sorted_times_ms = [0, max_shutter_duration_ms]

        # 3. Calculate State at Each Time Point
        states_at_time: Dict[int, List[int]] = {}
        for t_ms in sorted_times_ms:
            active_relays = set()
            for start, end, r_num in relay_events:
                if start <= t_ms < end:
                    active_relays.add(r_num)
            states_at_time[t_ms] = sorted(list(active_relays))
        logger.debug(f"States at time points (ms): {states_at_time}")

        # 4. Build Timeline from States and Durations
        timeline: List[TimelineEvent] = []
        last_added_on_state: Optional[List[int]] = None

        for i in range(len(sorted_times_ms) - 1):
            t1_ms = sorted_times_ms[i]
            t2_ms = sorted_times_ms[i+1]
            state_during_interval = states_at_time[t1_ms]
            duration_ms = t2_ms - t1_ms

            if duration_ms > 0:
                state_changed = (last_added_on_state is None or state_during_interval != last_added_on_state)

                if state_changed:
                    timeline.append(("on", state_during_interval))
                    last_added_on_state = state_during_interval
                    logger.debug(f"  Timeline Add: ('on', {state_during_interval}) for interval starting at {t1_ms}ms")
                    timeline.append(("delay", duration_ms))
                    logger.debug(f"  Timeline Add: ('delay', {duration_ms})")
                else:
                    if timeline and timeline[-1][0] == 'delay':
                        timeline[-1] = ('delay', timeline[-1][1] + duration_ms)
                        logger.debug(f"  Timeline Merge Delay: Updated to {timeline[-1][1]}ms")
                    else:
                        timeline.append(("delay", duration_ms))
                        logger.debug(f"  Timeline Add (unexpected): ('delay', {duration_ms})")

        # 5. Final State & Cleanup
        final_state = states_at_time[sorted_times_ms[-1]]
        if last_added_on_state is None or final_state != last_added_on_state:
            if not timeline and final_state != []:
                timeline.append(("on", final_state))
                logger.debug(f"  Timeline Add: Initial and final state ('on', {final_state}) at 0ms")
            elif timeline:
                timeline.append(("on", final_state))
                last_added_on_state = final_state
                logger.debug(f"  Timeline Add: Final state ('on', {final_state}) at {sorted_times_ms[-1]}ms")

        clean_timeline = [evt for evt in timeline if not (evt[0] == 'delay' and evt[1] == 0)]

        if max_shutter_duration_ms > 0:
            if not clean_timeline or clean_timeline[-1] != ('on', []):
                if clean_timeline and clean_timeline[-1][0] == 'on':
                    if clean_timeline[-1][1] != []:
                        logger.debug("Timeline cleanup: Replacing last 'on' state with ('on', [])")
                        clean_timeline[-1] = ('on', [])
                elif clean_timeline and clean_timeline[-1][0] == 'delay':
                    logger.debug("Timeline cleanup: Appending final ('on', []) after delay")
                    clean_timeline.append(('on', []))
                elif not clean_timeline:
                    logger.debug("Timeline cleanup: Adding ('on', []) to empty timeline (max_duration > 0)")
                    clean_timeline.append(('on', []))

        if clean_timeline and clean_timeline[0][0] == 'delay':
            logger.warning("Timeline cleanup: Removing unexpected leading delay.")
            clean_timeline.pop(0)

        if clean_timeline and clean_timeline[0][0] != 'on':
            logger.warning("Timeline cleanup: First event is not 'on'. Prepending initial state.")
            initial_state_at_zero = states_at_time.get(0, [])
            clean_timeline.insert(0, ('on', initial_state_at_zero))

        logger.info(f"Merged timeline generation complete. Events: {len(clean_timeline)}")
        logger.debug(f"Final Timeline (ms): {clean_timeline}")
        return clean_timeline

    def _execute_timeline(self, timeline: List[TimelineEvent]) -> bool:
        """Execute a pre-generated state-based timeline (delays in ms)."""
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
                    if isinstance(data, int) and data > 0:
                        sleep_seconds = data / 1000.0
                        logger.debug(f"  Sleeping for {sleep_seconds:.3f} seconds ({data} ms)...")
                        sleep(sleep_seconds)
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
