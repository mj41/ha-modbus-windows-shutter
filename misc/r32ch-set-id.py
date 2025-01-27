#!/usr/bin/env python3


# inspiration
# https://github.com/thegridelectric/modbus-experiments/blob/dfba611165d4cc541c24e6088db11e555b7984f2/mbe/waveshare_relays.py


from enum import IntEnum
import time 

import pymodbus.client as ModbusClient
from pymodbus import (
    ExceptionResponse,
    FramerType,
    ModbusException,
    pymodbus_apply_logging_config,
)


class WaveShareRegisters(IntEnum):
    DeviceAddress = 0x4000  # Device address

class Temperature8x(IntEnum):
    DeviceAddress = 0x00FE  # Device address


def run_sync_simple_client(comm, host, port, framer=FramerType.SOCKET):
    """Run sync client."""
    # activate debugging
    pymodbus_apply_logging_config("DEBUG")

    if comm == "serial":
        client = ModbusClient.ModbusSerialClient(
            port,
            framer=framer,
            # timeout=10,
            # retries=3,
            baudrate=9600,
            bytesize=8,
            parity="N",
            stopbits=1,
            # handle_local_echo=False,
        )
    else:
        print(f"Unknown client {comm} selected")
        return

    print("connect to server")
    client.connect()

    # address_holding_register = WaveShareRegisters.DeviceAddress
    # current_slave_id = 1
    # new_slave_id = 21

    # address_holding_register = Temperature8x.DeviceAddress
    # current_slave_id = 1
    # new_slave_id = 22

    try:
        resp = client.read_holding_registers(address=address_holding_register, slave=current_slave_id, count=1)
        print(f"curretn address {resp.registers}")
        if new_slave_id != resp.registers[0]:
            print(f"Setting slave_id from {resp.registers[0]} to {new_slave_id}")
            time.sleep(0.2)
            resp = client.write_register(
                address=address_holding_register, value=new_slave_id & 0xFFFF, slave=current_slave_id
            )
            time.sleep(0.2)
            resp = client.read_holding_registers(
                address=address_holding_register, slave=new_slave_id, count=1
            )
            print(f"NEW ADDRESS {resp.registers}")

    except ModbusException as exc:
        print(f"Received ModbusException({exc}) from library")
        client.close()
        return
    if resp.isError():
        print(f"Received Modbus library error({resp})")
        client.close()
        return
    if isinstance(resp, ExceptionResponse):
        print(f"Received Modbus library exception ({resp})")
        # THIS IS NOT A PYTHON EXCEPTION, but a valid modbus message
        client.close()

    print("close connection")
    client.close()


if __name__ == "__main__":
    run_sync_simple_client("serial", None, "/dev/ttyACM0", framer=FramerType.RTU)
