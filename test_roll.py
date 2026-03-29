import math
import time
import vgamepad as vg
import struct
import ctypes

def main():
    print("Emulando controlador PS4 - Probando eje Z del Giroscopio (supuesto Roll / Alabeo)")
    pad = vg.VDS4Gamepad()
    
    try:
        vigem_commons = vg.win.vigem_commons
        report_ex = vigem_commons.DS4_REPORT_EX()
        if hasattr(vigem_commons, "DS4_REPORT_INIT"):
            vigem_commons.DS4_REPORT_INIT(report_ex.Report)
    except Exception as e:
        print("Error: No se soporta reporte extendido.", e)
        return

    print("Enviando secuencias FIJAS a wGyroZ usando el OFFSET 12 CORRECTO.")
    try:
        valores = [1500, 0, -1500, 0]
        idx = 0
        while True:
            value = valores[idx]
            idx = (idx + 1) % len(valores)
            
            # wGyroZ está en bytes (16, 17) dentro de los 12 bytes del IMU en offset 12.
            packed_data = struct.pack("<hhhhhh", 0, 0, value, 0, 8192, 0)
            ctypes.memmove(ctypes.addressof(report_ex.Report) + 12, packed_data, 12)
            
            pad.update_extended_report(report_ex)
            pad.update()
            
            print(f"Enviando wGyroX = 0 | wGyroY = 0 | wGyroZ (Roll) = {value:>5}")
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nFinalizado.")

if __name__ == "__main__":
    main()
