"""
stats_calculator.py
Calcola le statistiche finali di un test (frequenza, variabilita',
asincronia, forza media) a partire dai soli eventi di tap grezzi
raccolti dalla seriale: (timestamp_ms, forza_adc, stato_motore).

Non si basa sulle statistiche che Arduino stampa a fine test: le
ricalcola interamente in Python, usando i tempi nominali dei beat
ricostruiti in beat_schedules.py.

Ogni test restituisce un dizionario di FASI:
    { "phase1": {"label": "...", ...metriche...}, "phase2": {...}, ... }

- Eligibility: una sola fase ("full", nessuna etichetta di fase).
- Rhythm A: due fasi separate e indipendenti - Fase 1 (paced) e
  Fase 2 (accelerata/perturbata) - cosi' da poter confrontare le due
  parti del test singolarmente.
- Rhythm B: tre fasce di beat separate e indipendenti - beat 1-15,
  beat 26-105, beat 106-120 - per analizzare meglio l'andamento nelle
  diverse parti del test. I beat 16-25 (zona di transizione) non
  rientrano in nessuna delle tre fasce e non vengono conteggiati.
"""

import statistics
import beat_schedules

EMPTY_METRICS = {
    "n_tap": 0,
    "frequenza_media_hz": 0.0,
    "iti_medio_ms": 0.0,
    "iti_sd_ms": 0.0,
    "asincronia_media_ms": 0.0,
    "asincronia_sd_ms": 0.0,
    "forza_media_adc": 0.0,
}

RHYTHM_B_PHASE_RANGES = [
    ("phase1", "Beats 1-15", 1, 15),
    ("phase2", "Beats 26-105", 26, 105),
    ("phase3", "Beats 106-120", 106, 120),
]


def _segment_events(events):
    """
    Divide gli eventi in segmenti separati ogni volta che il timestamp
    torna indietro: succede quando Arduino resetta il riferimento
    temporale passando a una nuova fase (es. Rhythm A, fase 2 -> 3).
    """
    segments = []
    current = []
    last_t = None
    for e in events:
        t = e[0]
        if last_t is not None and t < last_t:
            segments.append(current)
            current = []
        current.append(e)
        last_t = t
    if current:
        segments.append(current)
    return segments


def _compute_metrics(events: list, beats: list, duration_s: float) -> dict:
    if not events:
        return dict(EMPTY_METRICS)

    n_tap = len(events)
    iti_values = []
    async_values = []
    forces = []

    prev_t = None
    for (t, force, _motor) in events:
        forces.append(force)
        if prev_t is not None:
            iti_values.append(t - prev_t)
        prev_t = t
        async_values.append(beat_schedules.asynchrony_ms(beats, t))

    frequenza = n_tap / duration_s if duration_s > 0 else 0.0
    iti_mean = statistics.mean(iti_values) if iti_values else 0.0
    iti_sd = statistics.pstdev(iti_values) if len(iti_values) > 1 else 0.0
    async_mean = statistics.mean(async_values) if async_values else 0.0
    async_sd = statistics.pstdev(async_values) if len(async_values) > 1 else 0.0
    forza_media = statistics.mean(forces) if forces else 0.0

    return {
        "n_tap": n_tap,
        "frequenza_media_hz": round(frequenza, 3),
        "iti_medio_ms": round(iti_mean, 2),
        "iti_sd_ms": round(iti_sd, 2),
        "asincronia_media_ms": round(async_mean, 2),
        "asincronia_sd_ms": round(async_sd, 2),
        "forza_media_adc": round(forza_media, 1),
    }


def _eligibility_stats(events):
    beats = beat_schedules.eligibility_beats()
    duration_s = beat_schedules.ELIGIBILITY_DURATION / 1000.0
    return {"full": {"label": None, **_compute_metrics(events, beats, duration_s)}}


def _rhythm_a_stats(events):
    raw_segments = _segment_events(events)
    seg1 = raw_segments[0] if len(raw_segments) >= 1 else []
    seg2 = raw_segments[1] if len(raw_segments) >= 2 else []

    beats1 = beat_schedules.rhythm_a_phase2_beats()
    duration1_s = beat_schedules.RHYTHM_A_DURATION_PACED / 1000.0
    beats2 = beat_schedules.rhythm_a_phase3_beats()
    duration2_s = beat_schedules.RHYTHM_A_DURATION_PERTURBED / 1000.0

    return {
        "phase1": {"label": "Phase 1 - Paced", **_compute_metrics(seg1, beats1, duration1_s)},
        "phase2": {"label": "Phase 2 - Accelerated", **_compute_metrics(seg2, beats2, duration2_s)},
    }


def _rhythm_b_stats(events):
    beats = beat_schedules.rhythm_b_beats()

    buckets = {key: [] for key, _label, _lo, _hi in RHYTHM_B_PHASE_RANGES}
    for e in events:
        t = e[0]
        beat_num = beat_schedules.nearest_beat_index(beats, t) + 1  # 1-based
        for key, _label, lo, hi in RHYTHM_B_PHASE_RANGES:
            if lo <= beat_num <= hi:
                buckets[key].append(e)
                break

    result = {}
    for key, label, lo, hi in RHYTHM_B_PHASE_RANGES:
        seg_beats = beats[lo - 1:hi]  # slice of nominal beat times for this range
        if len(seg_beats) >= 2:
            duration_s = (seg_beats[-1] - seg_beats[0]) / 1000.0
        else:
            duration_s = beat_schedules.RHYTHM_B_ISI_SHORT / 1000.0
        result[key] = {"label": label, **_compute_metrics(buckets[key], seg_beats, duration_s)}
    return result


def compute_stats(test_key: str, events: list) -> dict:
    """
    events: lista di tuple (timestamp_ms, forza_adc, stato_motore),
    nell'ordine in cui sono arrivate dalla seriale.

    Ritorna sempre un dizionario {fase_key: {"label": ..., ...metriche}}.
    """
    if test_key == "eligibility":
        return _eligibility_stats(events)
    elif test_key == "rhythmA":
        return _rhythm_a_stats(events)
    elif test_key == "rhythmB":
        return _rhythm_b_stats(events)
    else:
        return {"full": {"label": None, **dict(EMPTY_METRICS)}}
