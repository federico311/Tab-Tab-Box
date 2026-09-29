"""
serial_comm.py
Gestisce la connessione seriale con il dispositivo (Arduino/microcontrollore)
in un thread dedicato, per non bloccare mai la GUI mentre si aspettano dati.

I dati letti vengono messi in una queue.Queue thread-safe, che la GUI
legge periodicamente con root.after().
"""

import threading
import queue
import time

import serial
import serial.tools.list_ports


class SerialReader:
    def __init__(self, data_queue: queue.Queue, baudrate: int = 9600):
        self.data_queue = data_queue
        self.baudrate = baudrate
        self.ser = None
        self._stop_event = threading.Event()
        self._thread = None

    @staticmethod
    def list_ports():
        """Ritorna la lista delle porte seriali disponibili (es. COM3, /dev/ttyUSB0)."""
        return [p.device for p in serial.tools.list_ports.comports()]

    @staticmethod
    def hard_reset(port: str, baudrate: int = 9600):
        """
        Forces an immediate hardware reset of the Arduino by briefly
        opening and closing the serial port. Opening the port toggles
        DTR, which on an Arduino Nano pulses the reset line - the same
        mechanism the board's own auto-reset-on-upload relies on. This
        stops whatever the Arduino is doing (mid-test motor pulses,
        etc.) right away, instead of waiting for a full reflash later.
        """
        try:
            s = serial.Serial(port, baudrate)
            s.close()
        except serial.SerialException:
            pass

    def connect(self, port: str) -> bool:
        try:
            self.ser = serial.Serial(port, self.baudrate, timeout=0.2)
            time.sleep(2)  # tempo di reset tipico dell'Arduino dopo l'apertura seriale
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._read_loop, daemon=True)
            self._thread.start()
            return True
        except serial.SerialException as e:
            self.data_queue.put(("error", f"Errore connessione: {e}"))
            return False

    def _read_loop(self):
        while not self._stop_event.is_set():
            try:
                if self.ser and self.ser.in_waiting > 0:
                    line = self.ser.readline().decode("utf-8", errors="ignore").strip()
                    if line:
                        self.data_queue.put(("data", line))
            except serial.SerialException as e:
                self.data_queue.put(("error", f"Connessione persa: {e}"))
                break
            time.sleep(0.01)

    def send(self, message: str):
        """Invia un comando al dispositivo (es. 'START', 'STOP')."""
        if self.ser and self.ser.is_open:
            self.ser.write((message + "\n").encode("utf-8"))

    def disconnect(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=1)
        if self.ser and self.ser.is_open:
            self.ser.close()
