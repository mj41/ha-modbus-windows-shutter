import logging
from typing import Dict, Any, Tuple, List
import yaml

# Import validate_config from modbus_relay
from modbus_relay import validate_config

# Configure logging for this module
logger = logging.getLogger(__name__)

class ConfigLoader:
    """Loads and validates configurations."""

    def load_yaml_file(self, path: str) -> Any:
        """Load a YAML file and return its contents."""
        try:
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
        """Load and validate the Modbus and shutter configurations, converting delays to milliseconds."""
        try:
            modbus_config = self.load_yaml_file(modbus_config_path)
            if not validate_config(modbus_config): # Use imported validate_config
                raise ValueError("Invalid modbus configuration")

            full_config = self.load_yaml_file(shutter_config_path)
            if not self.validate_shutter_config(full_config): # Validates v1.x.x format
                raise ValueError("Invalid or unsupported shutter configuration")

            shutters = full_config['shutters']
            groups = full_config.get('shutter_groups', {})

            # Convert delays to milliseconds (int) after validation
            for shutter_name, actions in shutters.items():
                for action_name, action_config in actions.items():
                    if 'relay_seq' in action_config and action_config['relay_seq']:
                        for step in action_config['relay_seq']:
                            # Convert float seconds to int milliseconds
                            step['delay_ms'] = int(step['delay'] * 1000)
                            # Optionally remove the old key, or keep it for reference
                            # del step['delay']
                            logger.debug(f"Converted delay for {shutter_name}/{action_name}: {step['delay']}s -> {step['delay_ms']}ms")


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
