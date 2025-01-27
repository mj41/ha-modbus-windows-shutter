#!/usr/bin/env python3

import sys
from time import sleep
from typing import Dict, Any, Optional

import argparse
import yaml
from modbus_relay import ModbusRelayClient, ModbusRelayError, validate_config


def load_config(config_path: str) -> Optional[Dict[str, Any]]:
    """Load and validate configuration from the given path."""
    try:
        with open(config_path, 'r', encoding='utf-8') as file:
            local_config = yaml.safe_load(file)
            if not validate_config(local_config):
                print("Invalid configuration format")
                return None
            return local_config
    except FileNotFoundError:
        print(f"Error: Config file not found: {config_path}")
        return None
    except yaml.YAMLError as e:
        print(f"Error parsing YAML config: {e}")
        return None
    except Exception as e:  # Catch other potential exceptions
        print(f"Error loading config from {config_path}: {e}")
        return None


def run_sync_simple_client(config: Dict[str, Any]) -> None:
    """Run sync client."""
    try:
        with ModbusRelayClient(config) as client:
            resp = client.read_device_address()
            if not resp:
                return
            print(f"Slave ID confirmed {resp.registers}\n")

            sleep_time = 0.15

            print("Write relays On one by one ...")
            client.reset_relays()
            for relay_num in range(1, 3):
                client.write_relay(relay_num, True)
                print(f"Write relay {relay_num} True done")
                sleep(0.1)
                if relay_num == 17:
                    sleep(sleep_time * 2)
                client.display_relay_states(client.read_relay_states())
                print("")
                sleep(sleep_time)

                client.reset_relays()
                sleep(sleep_time)

            print("Write all odds relays to On ...")
            all_odds_on = [True if i % 2 else False for i in range(32)]
            client.write_relays(all_odds_on)
            client.display_relay_states(client.read_relay_states())
            print("")
            sleep(sleep_time * 20)

            print("Write coils False ...\n")
            client.reset_relays()

    except ModbusRelayError as e:
        print(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Modbus Relay Client")
    parser.add_argument("--config", type=str, required=True, help="Path to configuration file")
    args = parser.parse_args()

    config = load_config(args.config)
    if not config:
        sys.exit(1)

    run_sync_simple_client(config)
