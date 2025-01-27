__all__ = ["RelayDataBlock"]

from pymodbus.datastore import ModbusSequentialDataBlock

class RelayDataBlock(ModbusSequentialDataBlock):
    def relay_to_coil(self, relay_num: int) -> int:
        relay_idx = relay_num - 1
        return 24 - (relay_idx // 8) * 8 + (relay_idx % 8)

    def setValues(self, address, values):
        # If writing a single coil within 0-31, apply mapping
        if len(values) == 1 and 0 <= address < 32:
            phys = self.relay_to_coil(address + 1)
            self.values[phys] = values[0]
        # If writing 32 values starting at 0, use mapping for block update
        elif address == 0 and len(values) == 32:
            for i, val in enumerate(values):
                phys = self.relay_to_coil(i + 1)
                self.values[phys] = val
        else:
            # ...existing behavior...
            super().setValues(address, values)

    def getValues(self, address, count=1):
        # If reading a single coil within 0-31, apply mapping
        if count == 1 and 0 <= address < 32:
            phys = self.relay_to_coil(address + 1)
            return [self.values[phys]]
        # If reading 32 values starting at 0, apply mapping in reverse
        elif address == 0 and count == 32:
            result = [False] * 32
            for i in range(32):
                phys = self.relay_to_coil(i + 1)
                result[i] = self.values[phys]
            return result
        else:
            return super().getValues(address, count)
