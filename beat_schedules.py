"""
beat_schedules.py
Ricostruisce in Python i tempi nominali dei "beat" (impulsi del motore)
generati da ciascuno sketch Arduino, replicando gli stessi parametri
(ISI, durate di fase) usati nel firmware (vedi sketches/*.ino).

Serve per calcolare l'asincronia tap-stimolo lato Python, a partire dai
soli dati grezzi (timestamp dei tap) che Arduino invia via seriale,
invece di fidarsi delle statistiche che Arduino stesso calcola.

I tempi sono relativi all'inizio della fase corrispondente, stesso
riferimento (millis() - faseStart) usato da Arduino nei timestamp.
"""

import bisect

# --- Parametri copiati identici dagli sketch .ino ---

ELIGIBILITY_ISI = 1000
ELIGIBILITY_DURATION = 15000  # durata_fase_2

RHYTHM_A_ISI_PACED = 1000            # ISI_normale
RHYTHM_A_DURATION_PACED = 66000      # durata_paced
RHYTHM_A_ISI_PERTURBED = 833         # ISI_perturbato
RHYTHM_A_DURATION_PERTURBED = 54000  # durata_perturbato

RHYTHM_B_ISI_SHORT = 667    # ISI_corto
RHYTHM_B_ISI_LONG = 1333    # ISI_lungo
RHYTHM_B_BEAT_TOTALI = 120


def _isochronous_beats(isi: int, duration: int) -> list:
    return list(range(0, duration, isi))


def eligibility_beats() -> list:
    return _isochronous_beats(ELIGIBILITY_ISI, ELIGIBILITY_DURATION)


def rhythm_a_phase2_beats() -> list:
    return _isochronous_beats(RHYTHM_A_ISI_PACED, RHYTHM_A_DURATION_PACED)


def rhythm_a_phase3_beats() -> list:
    return _isochronous_beats(RHYTHM_A_ISI_PERTURBED, RHYTHM_A_DURATION_PERTURBED)


def rhythm_b_beats() -> list:
    """
    Replica esattamente il ciclo del firmware: il primo beat parte a t=0,
    poi l'ISI alterna lungo/corto ad ogni beat successivo.
    """
    beats = [0]
    isi = RHYTHM_B_ISI_SHORT
    cur = 0
    for _ in range(1, RHYTHM_B_BEAT_TOTALI):
        isi = RHYTHM_B_ISI_LONG if isi == RHYTHM_B_ISI_SHORT else RHYTHM_B_ISI_SHORT
        cur += isi
        beats.append(cur)
    return beats


def nearest_beat_index(beats: list, t: float) -> int:
    """
    Ritorna l'indice (0-based) del beat nominale più vicino nel tempo al
    tap avvenuto al tempo t. Usato per assegnare ogni tap al proprio
    "numero di beat" (index + 1), a sua volta usato per raggruppare i
    tap di Rhythm B nelle 3 fasce richieste (1-15 / 26-105 / 106-120).
    """
    if not beats:
        return 0
    i = bisect.bisect_left(beats, t)
    if i == 0:
        return 0
    if i >= len(beats):
        return len(beats) - 1
    before = beats[i - 1]
    after = beats[i]
    return i - 1 if (t - before) <= (after - t) else i


def asynchrony_ms(beats: list, t: float) -> float:
    """
    Replica la logica dell'Arduino (distDalPrecedente / distDalProssimo):
    trova il beat precedente e quello successivo rispetto al tap al
    tempo t, e ritorna l'asincronia (positiva se il tap arriva dopo il
    beat precedente, negativa se anticipa il beat successivo).
    """
    if not beats:
        return 0.0

    i = bisect.bisect_right(beats, t) - 1
    i = max(0, min(i, len(beats) - 1))
    prev_beat = beats[i]

    if i + 1 < len(beats):
        next_beat = beats[i + 1]
    else:
        step = beats[i] - beats[i - 1] if i > 0 else 1000
        next_beat = prev_beat + step

    dist_precedente = t - prev_beat
    dist_prossimo = next_beat - t

    return -dist_prossimo if dist_prossimo < dist_precedente else dist_precedente
