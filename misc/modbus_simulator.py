import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from pymodbus.server import StartTcpServer
from pymodbus.device import ModbusDeviceIdentification
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext, ModbusSequentialDataBlock
from misc.relay_data_block import RelayDataBlock  # new import
import logging

logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.DEBUG)

# Enable detailed pymodbus communication debug output.
logging.getLogger("pymodbus").setLevel(logging.DEBUG)

# Create a datastore for coils and holding registers.
slave_store = ModbusSlaveContext(
    di=ModbusSequentialDataBlock(0, [0]*100),
    co=RelayDataBlock(0, [False]*100),  # use our custom data block for coils
    hr=ModbusSequentialDataBlock(0, [0]*0x4000 + [21] + [0]*99),
    ir=ModbusSequentialDataBlock(0, [0]*100))
# Use single mode so all requests get the same datastore regardless of slave/unit id.
context = ModbusServerContext(slaves=slave_store, single=True)

# Device identity info
identity = ModbusDeviceIdentification()
identity.VendorName = 'Pymodbus'
identity.ProductCode = 'PM'
identity.VendorUrl = 'http://github.com/riptideio/pymodbus/'
identity.ProductName = 'Pymodbus Simulator'
identity.ModelName = 'Pymodbus Simulator'
identity.MajorMinorRevision = '1.0'

if __name__ == "__main__":
    print("DEBUG: Starting modbus simulator on localhost:5020")
    StartTcpServer(context, identity=identity, address=("localhost", 5020))
