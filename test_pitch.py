import math
import time
import vgamepad as vg
import struct
import ctypes

def main():
    print("Emulando controlador PS4 - Probando eje X del Giroscopio (supuesto Pitch / Cabeceo)")
    pad = vg.VDS4Gamepad()
    
    try:
        vigem_commons = vg.win.vigem_commons
        report_ex = vigem_commons.DS4_REPORT_EX()
        if hasattr(vigem_commons, "DS4_REPORT_INIT"):
            vigem_commons.DS4_REPORT_INIT(report_ex.Report)
    except Exception as e:
        print("Error: No se soporta reporte extendido.", e)
        return

    print("Enviando secuencias FIJAS a wGyroX usando el OFFSET 12 CORRECTO.")
    try:
        valores = [1500, 0, -1500, 0]
        idx = 0
        while True:
            value = valores[idx]
            idx = (idx + 1) % len(valores)
            
            # EL BUG DE MEMORIA EXPLICADO:
            # En la estructura real en C de ViGEm, los datos deben estar empacados.
            # bThumbLX(0), bThumbLY(1), bThumbRX(2), bThumbRY(3)
            # wButtons(4-5), bSpecial(6), bTriggerL(7), bTriggerR(8)
            # wTimestamp(9-10), bBatteryLvl(11)
            # ==> wGyroX ENPIEZA EN EL OFFSET 12 (NO 13 como usamos antes por error)!
            # Escribir en 13 desfasaba los bytes, convirtiendo el byte bajo en byte alto (por eso los números enormes).
            
            packed_data = struct.pack("<hhhhhh", value, 0, 0, 0, 8192, 0)
            ctypes.memmove(ctypes.addressof(report_ex.Report) + 12, packed_data, 12)
            
            pad.update_extended_report(report_ex)
            pad.update()
            
            print(f"Enviando wGyroX (Pitch) = {value:>5} | wGyroY = 0 | wGyroZ = 0")
            time.sleep(1.0) # Cambia cada 1 segundo para verlo claramente
    except KeyboardInterrupt:
        print("\nFinalizado.")

if __name__ == "__main__":
    main()
