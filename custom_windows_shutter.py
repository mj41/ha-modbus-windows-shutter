#!/usr/bin/env python3

import sys
import argparse
import logging
from time import sleep
from typing import Dict, Any, Tuple, List

from modbus_relay import ModbusRelayClient, ModbusRelayError, validate_config
import custom_windows_shutter_constants as constants

# Configure logging and create a module logger
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Define action constants
ACTION_UP = 'up'
ACTION_DOWN = 'down'
ACTION_STOP = 'stop'

class ConfigLoader:
    """Loads and validates configurations."""

    def load_yaml_file(self, path: str) -> Any:
        """Load a YAML file and return its contents."""
        try:
            import yaml
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Configuration file not found: {path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file: {path} - {e}")
            raise

    def validate_shutter_config(self, full_config: Dict[str, Any]) -> bool:
        """Validate the structure of the shutter configuration."""
        if not isinstance(full_config, dict):
            logger.error("Shutter configuration must be a dictionary.")
            return False

        required_keys = ['shutters']
        for key in required_keys:
            if key not in full_config:
                logger.error(f"Shutter configuration must contain '{key}'.")
                return False
            if not isinstance(full_config[key], dict):
                 logger.error(f"'{key}' must be a dictionary.")
                 return False

        if 'shutter_groups' in full_config and not isinstance(full_config['shutter_groups'], dict):
            logger.error("Shutter configuration 'shutter_groups' must be a dictionary.")
            return False

        return True

    def validate_group_config(self, groups: Dict[str, Any], shutters: Dict[str, Any]) -> None:
        """Validate the structure of the group configuration."""
        for group_name, shutter_list in groups.items():
            if not isinstance(shutter_list, list):
                raise ValueError(f"Invalid group '{group_name}': expected a list of shutter names.")
            for shutter in shutter_list:
                if shutter not in shutters:
                    raise ValueError(f"Invalid group '{group_name}': shutter '{shutter}' not defined.")

    def load_and_validate_configs(self, modbus_config_path: str, shutter_config_path: str) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """Load and validate the Modbus and shutter configurations."""
        try:
            modbus_config = self.load_yaml_file(modbus_config_path)
            if not validate_config(modbus_config):
                raise ValueError("Invalid modbus configuration")

            full_config = self.load_yaml_file(shutter_config_path)
            if not self.validate_shutter_config(full_config) :
                raise ValueError("Invalid shutter configuration")

            shutters = full_config['shutters']
            groups = full_config.get('shutter_groups', {})

            self.validate_group_config(groups, shutters)
            return modbus_config, shutters, groups
        except FileNotFoundError as e:
            logger.error(f"Configuration file not found: {e}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML file: {e}")
            raise
        except ValueError as e:
            logger.error(f"Invalid configuration: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load and validate configurations: {e}")
            raise

class ShutterController:
    """Encapsulates the logic for controlling shutters and groups."""

    def __init__(self, modbus_config: Dict[str, Any], shutters: Dict[str, Any], groups: Dict[str, Any]) -> None:
        """Initialize with Modbus configuration, shutters, and groups."""
        self.modbus_config = modbus_config
        self.shutters = shutters
        self.groups = groups
        try:
            self.client = ModbusRelayClient(modbus_config)
            if not self.client.connect():
                raise ModbusRelayError("Failed to connect to Modbus device")
        except Exception as e:
            logger.error(f"Failed to initialize Modbus client: {e}")
            raise

    def __enter__(self):
        """Enter method for context management."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit method for context management; ensures the Modbus client is closed."""
        self.client.close()

    def perform_relay_action(self, relays: List[int], duration: float, action: str) -> bool:
        """Perform the relay action for the specified duration and reset the relays."""
        logger.info(f"Activating {action.upper()} relays {sorted(relays)} for {duration}s")
        try:
            if not self.client.reset_relays():
                raise ModbusRelayError("Failed to reset relays")
            for relay in relays:
                if not self.client.write_relay(relay, True):
                    raise ModbusRelayError(f"Failed to activate relay {relay}")
            states = self.client.read_relay_states()
            if states:
                self.client.display_relay_states(states)
            sleep(duration)
            return self.client.reset_relays()
        except ModbusRelayError as e:
            logger.error(f"Modbus error during {action} action: {e}")
            self.client.reset_relays()  # Safety reset
            return False
        except Exception as e:
            logger.exception(f"Unexpected error during {action} action: {e}")
            self.client.reset_relays()  # Safety reset
            return False

    def control_shutter(self, shutter_name: str, action: str) -> bool:
        """Control a specific shutter."""
        if shutter_name not in self.shutters:
            logger.error(f"Shutter '{shutter_name}' not found in configuration.")
            return False

        config = self.shutters[shutter_name]
        relay_up, relay_down = config['relays']
        up_time, down_time = config['timing']
        active_time = up_time if action == ACTION_UP else down_time
        active_relay = relay_up if action == ACTION_UP else relay_down

        logger.info(f"Controlling shutter '{shutter_name}' -> {action} for {active_time}s using relay {active_relay}")
        return self.perform_relay_action([active_relay], active_time, action)

    def control_group(self, group_name: str, action: str) -> bool:
        """Control a group of shutters by union of their relays and maximum required time."""
        if group_name not in self.groups:
            logger.error(f"Group '{group_name}' not found in configuration.")
            return False

        group = self.groups[group_name]
        union_relays = set()
        union_time = 0
        for shutter in group:
            if shutter not in self.shutters:
                logger.error(f"Shutter '{shutter}' not found in configuration.")
                return False
            cfg = self.shutters[shutter]
            if action == ACTION_UP:
                relay = cfg['relays'][0]
                time_val = cfg['timing'][0]
            else:
                relay = cfg['relays'][1]
                time_val = cfg['timing'][1]
            union_relays.add(relay)
            union_time = max(union_time, time_val)

        logger.info(f"Controlling group '{group_name}' with relays {union_relays} for {union_time}s")
        return self.perform_relay_action(list(union_relays), union_time, action)

    def check_device_address(self) -> None:
        """Check and log the device address."""
        try:
            resp = self.client.read_device_address()
            if not resp:
                raise ModbusRelayError("Failed to read device address")
            logger.info(f"Slave ID confirmed: {resp.registers}")
        except ModbusRelayError as e:
            logger.error(f"Failed to read device address: {e}")
            raise

    def handle_stop_action(self) -> None:
        """Handle the stop action by resetting all relays."""
        logger.info("Global STOP command received")
        try:
            if not self.client.reset_relays():
                raise ModbusRelayError("Failed to reset relays")
        except ModbusRelayError as e:
            logger.error(f"Failed to reset relays: {e}")
            raise

    def handle_up_down_action(self, action: str, target: str) -> None:
        """Handle the up or down action for a specific shutter or group."""
        try:
            self.check_device_address()
            if target in self.shutters:
                logger.info(f"Controlling single shutter '{target}' to go '{action}'")
                if not self.control_shutter(target, action):
                    sys.exit(1)
            elif target in self.groups:
                logger.info(f"Controlling shutter group '{target}' to go '{action}'")
                if not self.control_group(target, action):
                    sys.exit(1)
            else:
                logger.error(f"Error: Unknown shutter or group '{target}'")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to handle {action} action for target {target}: {e}")
            sys.exit(1)

def main() -> None:
    """Main function to parse arguments and control shutters or groups."""
    parser = argparse.ArgumentParser(description="Window Shutter Control")
    parser.add_argument("--modbus_config", type=str, default=constants.MODBUS_CONFIG_PATH, help="Path to Modbus configuration file")
    parser.add_argument("--shutter_config", type=str, default=constants.SHUTTER_CONFIG_PATH, help="Path to Shutter configuration file")
    parser.add_argument("action", choices=[ACTION_UP, ACTION_DOWN, ACTION_STOP], help="Action to perform")
    parser.add_argument("target", nargs='?', help="Shutter or group to control (required for up/down)")

    args = parser.parse_args()

    if args.action in [ACTION_UP, ACTION_DOWN] and not args.target:
        parser.error(f"Shutter or group name required for {args.action} action")

    try:
        config_loader = ConfigLoader()
        modbus_config, shutters, groups = config_loader.load_and_validate_configs(args.modbus_config, args.shutter_config)
    except Exception:
        sys.exit(1)

    try:
        with ShutterController(modbus_config, shutters, groups) as controller:
            if args.action == ACTION_STOP:
                controller.handle_stop_action()
            elif args.action in [ACTION_UP, ACTION_DOWN]:
                controller.handle_up_down_action(args.action, args.target)
            else:
                parser.print_help()
                sys.exit(1)
    except Exception as e:
        logger.exception(f"Fatal error in main: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
