#!/usr/bin/env python3

import sys
import argparse
import logging
from time import sleep
from typing import Dict, Any, Tuple, List, Optional

from modbus_relay import ModbusRelayClient, ModbusRelayError, validate_config
import custom_windows_shutter_constants as constants

# Configure logging and create a module logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Define action constants
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
        except Exception as e:
            logger.error(f"Error loading YAML file {path}: {e}")
            raise


    def validate_relay_seq(self, seq: Any, action_name: str, shutter_name: str) -> bool:
        """Validate a single relay_seq list."""
        if not isinstance(seq, list):
            logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}': 'relay_seq' must be a list.")
            return False
        if not seq:
            logger.warning(f"Config for shutter '{shutter_name}', action '{action_name}': 'relay_seq' is empty.")
            # Allow empty sequence for actions that do nothing
            return True
        for idx, step in enumerate(seq):
            if not isinstance(step, dict):
                logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}', step {idx+1}: Each step must be a dictionary.")
                return False
            if 'relay_num' not in step or not isinstance(step['relay_num'], int):
                logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}', step {idx+1}: Missing or invalid 'relay_num' (must be an integer).")
                return False
            if 'delay' not in step or not isinstance(step['delay'], (int, float)):
                logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}', step {idx+1}: Missing or invalid 'delay' (must be a number).")
                return False
            if step['relay_num'] < 1 or step['relay_num'] > 32:
                 logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}', step {idx+1}: 'relay_num' must be between 1 and 32.")
                 return False
            if step['delay'] < 0:
                 logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}', step {idx+1}: 'delay' cannot be negative.")
                 return False
        return True


    def validate_shutter_config(self, full_config: Dict[str, Any]) -> bool:
        """Validate the structure and version (v1.x.x) of the shutter configuration."""
        if not isinstance(full_config, dict):
            logger.error("Shutter configuration must be a dictionary.")
            return False

        required_keys = ['config_version', 'shutters']
        for key in required_keys:
            if key not in full_config:
                logger.error(f"Shutter configuration must contain '{key}'.")
                return False

        # Validate config version (must start with v1.)
        version = full_config.get('config_version', '')
        if not isinstance(version, str) or not version.startswith('v1.'):
            logger.error(f"Invalid or unsupported 'config_version': '{version}'. Major version must be '1' (e.g., 'v1.0.0').")
            return False
        logger.info(f"Configuration version '{version}' loaded successfully.")

        # Validate shutters structure
        shutters = full_config['shutters']
        if not isinstance(shutters, dict):
             logger.error("'shutters' must be a dictionary.")
             return False

        for shutter_name, actions in shutters.items():
            if not isinstance(actions, dict):
                logger.error(f"Invalid config for shutter '{shutter_name}': value must be a dictionary of actions.")
                return False
            if not actions:
                 logger.warning(f"Shutter '{shutter_name}' has no actions defined.")
                 continue # Allow shutters with no actions
            for action_name, action_config in actions.items():
                 if not isinstance(action_config, dict):
                      logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}': value must be a dictionary.")
                      return False
                 if 'relay_seq' not in action_config:
                      logger.error(f"Invalid config for shutter '{shutter_name}', action '{action_name}': missing 'relay_seq'.")
                      return False
                 if not self.validate_relay_seq(action_config['relay_seq'], action_name, shutter_name):
                     return False # Stop validation on first error in sequence

        # Validate optional shutter_groups structure
        if 'shutter_groups' in full_config and not isinstance(full_config['shutter_groups'], dict):
            logger.error("Shutter configuration 'shutter_groups' must be a dictionary.")
            return False

        return True

    def validate_group_config(self, groups: Dict[str, Any], shutters: Dict[str, Any]) -> None:
        """Validate the structure of the group configuration."""
        for group_name, shutter_list in groups.items():
            if not isinstance(shutter_list, list):
                raise ValueError(f"Invalid group '{group_name}': expected a list of shutter names.")
            if not shutter_list:
                 logger.warning(f"Group '{group_name}' is empty.")
                 continue # Allow empty groups
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
            if not self.validate_shutter_config(full_config): # Validates v1.x.x format
                raise ValueError("Invalid or unsupported shutter configuration")

            shutters = full_config['shutters']
            groups = full_config.get('shutter_groups', {})

            self.validate_group_config(groups, shutters)
            return modbus_config, shutters, groups
        except FileNotFoundError as e:
            # Error already logged in load_yaml_file
            raise
        except yaml.YAMLError as e:
            # Error already logged in load_yaml_file
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
        self.client: Optional[ModbusRelayClient] = None # Initialize client as None
        try:
            self.client = ModbusRelayClient(modbus_config)
            # Connection is attempted within __enter__ or methods needing it
        except Exception as e:
            logger.error(f"Failed to initialize Modbus client structure: {e}")
            raise # Re-raise critical initialization error

    def _ensure_connected(self) -> bool:
        """Ensure the client is connected, attempting to connect if necessary."""
        if not self.client:
             logger.error("Modbus client not initialized.")
             return False
        # Check if client exists and socket is open
        if not self.client.client or not self.client.client.is_socket_open():
            logger.info("Modbus client not connected. Attempting to connect...")
            if not self.client.connect():
                logger.error("Failed to connect to Modbus device.")
                return False
            logger.info("Modbus connection successful.")
        return True

    def __enter__(self):
        """Enter method for context management."""
        # Connection is handled lazily by methods needing it
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit method for context management; ensures the Modbus client is closed."""
        if self.client:
            self.client.close()

    def execute_relay_sequence(self, shutter_name: str, action_name: str, relay_seq: List[Dict[str, Any]]) -> bool:
        """Execute a sequence of relay activations and delays."""
        if not self._ensure_connected(): return False
        assert self.client is not None # Ensure client is not None after _ensure_connected

        logger.info(f"Executing action '{action_name}' for shutter '{shutter_name}'...")
        success = True
        try:
            # Initial reset before starting sequence
            if not self.client.reset_relays():
                raise ModbusRelayError("Failed initial reset before sequence")
            sleep(0.1) # Small delay after reset

            for i, step in enumerate(relay_seq):
                relay_num = step['relay_num']
                delay = step['delay']
                logger.info(f"  Step {i+1}: Activating relay {relay_num} for {delay}s")

                # Activate the specific relay for this step
                if not self.client.write_relay(relay_num, True):
                     # Attempt safety reset on failure
                    self.client.reset_relays()
                    raise ModbusRelayError(f"Failed to activate relay {relay_num} in step {i+1}")

                # Optional: Display current state after activation
                states = self.client.read_relay_states()
                if states:
                    self.client.display_relay_states(states)
                else:
                    logger.warning("Could not read relay states after activation.")

                # Wait for the specified delay
                sleep(delay)

                # Reset all relays before next step or after last step
                if i < len(relay_seq) - 1:
                    logger.debug(f"  Step {i+1}: Resetting relays before next step.")
                else:
                    logger.info(f"  Sequence finished. Performing final reset.")

                if not self.client.reset_relays():
                    # Log error but try to continue if possible? Or abort? Abort seems safer.
                    raise ModbusRelayError(f"Failed to reset relays after step {i+1}")
                sleep(0.1) # Small delay after reset

            logger.info(f"Action '{action_name}' for shutter '{shutter_name}' completed successfully.")

        except ModbusRelayError as e:
            logger.error(f"Modbus error during action '{action_name}' for shutter '{shutter_name}': {e}")
            success = False
            # Ensure relays are reset in case of error
            if self.client:
                self.client.reset_relays()
        except Exception as e:
            logger.exception(f"Unexpected error during action '{action_name}' for shutter '{shutter_name}': {e}")
            success = False
            # Ensure relays are reset in case of error
            if self.client:
                self.client.reset_relays()

        return success


    def control_shutter(self, shutter_name: str, action: str) -> bool:
        """Control a specific shutter by executing the relay sequence for the given action."""
        if shutter_name not in self.shutters:
            logger.error(f"Shutter '{shutter_name}' not found in configuration.")
            return False

        shutter_config = self.shutters[shutter_name]
        if action not in shutter_config:
            logger.error(f"Action '{action}' not defined for shutter '{shutter_name}'.")
            return False

        action_config = shutter_config[action]
        relay_seq = action_config.get('relay_seq')

        if not relay_seq:
             logger.info(f"Action '{action}' for shutter '{shutter_name}' has an empty relay sequence. Doing nothing.")
             return True # Empty sequence is considered successful completion

        return self.execute_relay_sequence(shutter_name, action, relay_seq)

    def control_group(self, group_name: str, action: str) -> bool:
        """Control a group of shutters sequentially."""
        if group_name not in self.groups:
            logger.error(f"Group '{group_name}' not found in configuration.")
            return False

        group_shutters = self.groups[group_name]
        if not group_shutters:
            logger.warning(f"Group '{group_name}' is empty. Nothing to do.")
            return True

        logger.info(f"Controlling group '{group_name}' sequentially for action '{action}'...")
        overall_success = True
        for shutter_name in group_shutters:
            logger.info(f"--- Group '{group_name}': Controlling shutter '{shutter_name}' for action '{action}' ---")
            # Check if the specific action exists for this shutter in the group
            if shutter_name not in self.shutters or action not in self.shutters[shutter_name]:
                 logger.error(f"Action '{action}' not defined for shutter '{shutter_name}' in group '{group_name}'. Skipping.")
                 overall_success = False
                 continue # Skip this shutter and continue with the group

            if not self.control_shutter(shutter_name, action):
                logger.error(f"Failed to control shutter '{shutter_name}' in group '{group_name}' for action '{action}'.")
                overall_success = False
                # Decide whether to continue with the rest of the group or stop
                # Let's continue for now, but log the failure.
            sleep(0.5) # Add a small delay between shutters in a group

        if overall_success:
             logger.info(f"Group '{group_name}' action '{action}' completed.")
        else:
             logger.warning(f"Group '{group_name}' action '{action}' completed with one or more failures.")

        return overall_success


    def check_device_address(self) -> bool:
        """Check and log the device address."""
        if not self._ensure_connected(): return False
        assert self.client is not None

        try:
            resp = self.client.read_device_address()
            if not resp:
                # Error logged by decorator/client method
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
                # Error logged by decorator/client method
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
        # Check device address once before performing actions
        if not self.check_device_address():
            return False # Stop if we can't confirm device ID

        if target in self.shutters:
            logger.info(f"Controlling single shutter '{target}' for action '{action}'")
            return self.control_shutter(target, action)
        elif target in self.groups:
            logger.info(f"Controlling shutter group '{target}' for action '{action}'")
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

    # Setup logging level
    log_level = logging.DEBUG if args.debug else logging.INFO
    # Use force=True to override the default basicConfig if it was already called (e.g., by pymodbus)
    logging.basicConfig(level=log_level, format="%(asctime)s [%(levelname)s] %(message)s", force=True)

    # Validate arguments
    if args.action != ACTION_STOP and not args.target:
        parser.error(f"Target shutter or group name required for action '{args.action}'")
    if args.action == ACTION_STOP and args.target:
         logger.warning(f"Target '{args.target}' ignored for '{ACTION_STOP}' action.")
         args.target = None # Ignore target for stop action


    try:
        config_loader = ConfigLoader()
        modbus_config, shutters, groups = config_loader.load_and_validate_configs(args.modbus_config, args.shutter_config)
        # Add debug flag from args to modbus_config if not already set or if --debug is used
        if 'DEBUG_MODBUS' not in modbus_config:
             modbus_config['DEBUG_MODBUS'] = args.debug
        elif args.debug:
             modbus_config['DEBUG_MODBUS'] = True # Override if --debug is set

    except Exception:
        # Errors logged within loader/validator
        sys.exit(1)

    exit_code = 0
    try:
        # Use context manager for ShutterController
        with ShutterController(modbus_config, shutters, groups) as controller:
            success = False
            if args.action == ACTION_STOP:
                success = controller.handle_stop_action()
            else:
                # Target is guaranteed to be non-None here if action is not STOP
                success = controller.handle_action(args.action, args.target) # type: ignore

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
