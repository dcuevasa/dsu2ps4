import ctypes
import struct
import vgamepad as vg

# Let's see if writing to byte 13 effectively populates what driver sees, and if it bypasses the buggy ctypes mapping.
ex = vg.win.vigem_commons.DS4_REPORT_EX()

data = struct.pack("<hhhhhh", 1111, 2222, 3333, 4444, 5555, 6666)
ctypes.memmove(ctypes.addressof(ex) + 13, data, 12)

# Check the raw buffer:
buf = bytes(ex.ReportBuffer)
print("Buffer bytes 13..25:")
print(buf[13:25])
print("Unpacked from buffer directly:")
print(struct.unpack("<hhhhhh", buf[13:25]))

# Compare with the python struct properties to see how offset it is:
print("ex.Report properties (will be wrong because of python alignment!):")
print("wGyroX:", ex.Report.wGyroX)
print("wGyroY:", ex.Report.wGyroY)
