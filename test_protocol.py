"""
test_protocol.py
Riceve le righe seriali dagli sketch Arduino e:
- raccoglie gli eventi di tap grezzi (timestamp_ms, forza_adc, stato_motore)
- tiene traccia dei messaggi di stato (per mostrare la fase corrente in GUI)
- rileva la fine del test ("Send 's' ...")

Le statistiche vere e proprie vengono calcolate da stats_calculator.py
usando solo i dati grezzi qui raccolti; il blocco di statistiche che
Arduino stampa a fine test viene ignorato di proposito.
"""

from enum import Enum, auto


class TestState(Enum):
    IDLE = auto()
    RUNNING = auto()
    FINISHED = auto()


class TapTestProtocol:
    def __init__(self):
        self.state = TestState.IDLE
        self.events = []            # (timestamp_ms, forza_adc, stato_motore)
        self.status_messages = []

    def start(self):
        self.state = TestState.RUNNING
        self.events = []
        self.status_messages = []

    def reset(self):
        self.state = TestState.IDLE
        self.events = []
        self.status_messages = []

    def process_line(self, raw_line: str):
        line = raw_line.strip()
        if not line or self.state != TestState.RUNNING:
            return

        # Riga dati: timestamp,forza,stato_motore (3 campi numerici)
        parts = line.split(",")
        if len(parts) == 3 and all(self._is_number(p) for p in parts):
            t, force, motor = parts
            self.events.append((float(t), float(force), int(float(motor))))
            return

        # Segnale di fine sessione: Arduino pronto per un nuovo test
        if line.startswith("Send 's'"):
            self.state = TestState.FINISHED
            return

        # Ignora il blocco statistiche di Arduino ("Etichetta: valore"):
        # lo calcoliamo noi in Python dai dati grezzi.
        if ":" in line:
            return

        # Qualunque altra riga è un messaggio di stato (es. "Start tapping")
        self.status_messages.append(line)

    @staticmethod
    def _is_number(s: str) -> bool:
        try:
            float(s)
            return True
        except ValueError:
            return False
