#!/usr/bin/env python3

import signal
import sys
from typing import List, Optional, Dict, Any

import pymodbus.client as ModbusClient
from pymodbus import (
    ExceptionResponse,
    FramerType,
    ModbusException,
    pymodbus_apply_logging_config,
)


def validate_config(config: Dict[str, Any]) -> bool:
    """Validate configuration dictionary."""
    required_keys = ['CONNECTION_TYPE', 'DEVICE_PORT', 'SLAVE_ID']
    return all(key in config for key in required_keys)


class ModbusRelayError(Exception):
    """Custom exception for ModbusRelay errors."""
    pass


def handle_modbus_exception(func):
    """Decorator to handle ModbusException and check responses."""
    def wrapper(*args, **kwargs) -> Optional[Any]:  # type: ignore
        try:
            resp = func(*args, **kwargs)
            if hasattr(resp, 'isError') and resp.isError():
                raise ModbusRelayError(f"Modbus library error: {resp}")
            if isinstance(resp, ExceptionResponse):
                raise ModbusRelayError(f"Modbus exception: {resp}")
            return resp
        except ModbusException as exc:
            raise ModbusRelayError(f"ModbusException: {exc}")
        except Exception as e:
            raise ModbusRelayError(f"Error in {func.__name__}: {e}")
    return wrapper


class ModbusRelayClient:
    """Modbus client for controlling relay board."""
    DeviceAddress = 0x4000  # Device address

    def __init__(self, config: Dict[str, Any]):
        """Initialize client with config dictionary."""
        if not validate_config(config):
            raise ValueError("Invalid configuration")
        
        self.port = config['DEVICE_PORT']
        self.slave_id = config['SLAVE_ID']
        self.connection_type = config.get("CONNECTION_TYPE", "serial")
        self.framer = FramerType.RTU
        self.client: Optional[Any] = None
        if config.get('DEBUG_MODBUS', False):
            pymodbus_apply_logging_config("DEBUG")

    @staticmethod
    def relay_to_coil(relay_num: int) -> int:
        """Convert relay number to coil number.
        Maps relay numbers 0-31 to coil addresses by reversing byte order:
        relays 0-7   -> coils 24-31
        relays 8-15  -> coils 16-23
        relays 16-23 -> coils 8-15
        relays 24-31 -> coils 0-7
        """
        relay_idx = relay_num - 1
        return 24 - (relay_idx // 8) * 8 + (relay_idx % 8)

    def display_relay_states(self, relay_states: Optional[List[bool]]) -> None:
        """Display relay states showing both logical and physical numbers."""
        if relay_states is None:
            print("Error: Could not read relay states")
            return
        s1 = "Relay: "
        s2 = "State: "
        for relay_state_idx in range(32):
            s1 += f" {(relay_state_idx + 1):2d}"
            s2 += f"  {1 if relay_states[relay_state_idx] else 0}"
        print(s1)
        print(s2)

    def connect(self) -> bool:
        """Connect to the modbus device."""
        try:
            print(f"Connecting to Modbus with settings: {self.__dict__}")  # Debugging
            if self.connection_type == "tcp":
                from pymodbus.client import ModbusTcpClient  # updated import
                # For simulator, use fixed port 5020
                self.client = ModbusTcpClient(self.port, port=5020)
            else:
                self.client = ModbusClient.ModbusSerialClient(
                    self.port,
                    framer=self.framer,
                    baudrate=9600,
                    bytesize=8,
                    parity="N",
                    stopbits=1,
                )
            return self.client.connect()
        except Exception as e:
            print(f"Connection error: {e}")
            return False

    def close(self) -> None:
        """Close the connection and reset relays."""
        if self.client:
            try:
                self.reset_relays()
                self.client.close()
            except Exception as e:
                print(f"Error closing connection: {e}")

    @handle_modbus_exception
    def reset_relays(self) -> Optional[Any]:
        """Reset all coils to False."""
        if self.client:
            return self.client.write_coils(address=0, values=[False] * 32, slave=self.slave_id)
        return None

    @handle_modbus_exception
    def write_relay(self, relay_num: int, value: bool) -> Optional[Any]:
        """Write value to a single relay."""
        coil = relay_num - 1  # Convert 1-based relay number to 0-based coil
        if self.client:
            return self.client.write_coil(address=coil, value=value, slave=self.slave_id)
        return None

    @handle_modbus_exception
    def write_relays(self, values: List[bool]) -> Optional[Any]:
        """Write values to all relays.
        Args:
            values: List of 32 boolean values where index 0 is relay 1, index 1 is relay 2, etc.
        """
        coil_values = [False] * 32
        for relay_state_idx, value in enumerate(values):
            coil = self.relay_to_coil(relay_state_idx + 1)
            coil_values[coil] = value
        if self.client:
            return self.client.write_coils(address=0, values=coil_values, slave=self.slave_id)
        return None

    @handle_modbus_exception
    def read_relay_states(self) -> Optional[List[bool]]:
        """Read all coil states."""
        if self.client:
            resp = self.client.read_coils(address=0, count=32, slave=self.slave_id)
            if resp:
                bits = resp.bits
                return [bits[self.relay_to_coil(relay_num)] for relay_num in range(1, 33)]
        return None

    @handle_modbus_exception
    def read_device_address(self) -> Optional[Any]:
        """Read device address register."""
        if self.client:
            # When using simulator (TCP), use address 0.
            addr = 0 if self.connection_type == "tcp" else self.DeviceAddress
            return self.client.read_holding_registers(
                address=addr,
                slave=self.slave_id,
                count=1
            )
        return None

    def register_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        def handler(signum, frame):
            print("\nCtrl+C pressed. Resetting relays...")
            self.close()
            sys.exit(0)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def __enter__(self) -> 'ModbusRelayClient':
        """Context manager entry."""
        self.connect()
        self.register_signal_handlers()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()
