"""
xlsx_export.py
Genera, per ciascun paziente, un file Excel (.xlsx) con il layout
richiesto dall'utente (vedi template fornito paziente_01.xlsx):

    ELIGIBILITY | (vuoto) | RITHYM A | (vuoto) | RITHYM B
    - riga 3: "secondi, forza, stato motore"
    - un tap grezzo per riga: "secondi,forza,stato_motore" in un'unica cella
    - per Rhythm A: le due fasi sono separate da un marcatore "INIZIO FASE 2"
      (nel firmware, il riferimento temporale si azzera tra fase 1 e fase 2)
    - dopo i tap grezzi: blocco statistiche complessivo
      ("Final analysis of the recorded data" + "Recorded Tap: N" + metriche)
    - per Rhythm A / Rhythm B, seguono anche i blocchi per singola fase
      ("STATISTICHE FASE 1/2" oppure "STATISTICHE t0-t15" ecc.)

Escluse volutamente le colonne ETA e SESSO presenti nel template originale.

Un file per paziente (patient_reports/<paziente_id>.xlsx): ogni sessione
salvata aggiunge un NUOVO FOGLIO (sheet) con il timestamp della sessione,
cosi' nessuna sessione precedente viene mai persa/sovrascritta.
"""

import os

TEST_HEADERS = {"eligibility": "ELIGIBILITY", "rhythmA": "RITHYM A", "rhythmB": "RITHYM B"}
TEST_START_COL = {"eligibility": 1, "rhythmA": 8, "rhythmB": 15}  # A, H, O

RHYTHM_A_PHASE_LABELS = ["STATISTICHE FASE 1", "STATISTICHE FASE 2"]
RHYTHM_B_PHASE_LABELS = ["STATISTICHE t0-t15", "STATISTICHE t26-t105", "STATISTICHE t106-t120"]
RHYTHM_A_PHASE_KEYS = ["phase1", "phase2"]
RHYTHM_B_PHASE_KEYS = ["phase1", "phase2", "phase3"]


def _safe_sheet_name(session_timestamp: str) -> str:
    name = session_timestamp.replace(":", "-").replace("T", " ")
    return name[:31]  # Excel sheet name hard limit


def _safe_filename(paziente_id: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in paziente_id)


def _fmt_tap(event) -> str:
    t, force, motor = event
    return f"{int(round(t))},{int(round(force))},{int(round(motor))}"


def _split_on_reset(events: list) -> list:
    """
    Divide una lista di tap grezzi in segmenti separati ogni volta che il
    timestamp torna indietro (succede quando il firmware azzera il
    riferimento temporale passando a una nuova fase, es. Rhythm A).
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


def _stat_lines(metrics: dict, header_label: str, include_recorded_tap: bool) -> list:
    lines = [header_label]
    if include_recorded_tap:
        lines.append(f"Recorded Tap: {metrics.get('n_tap', 0)}")
    lines.append(f"Mean Frequency (tap/s): {metrics.get('frequenza_media_hz', 0)}")
    lines.append(f"Variability (SD ITI ms): {metrics.get('iti_sd_ms', 0)}")
    lines.append(f"Mean Asynchrony (ms): {metrics.get('asincronia_media_ms', 0)}")
    lines.append(f"Asynchrony SD (ms): {metrics.get('asincronia_sd_ms', 0)}")
    lines.append(f"Avg Impact Force (ADC): {metrics.get('forza_media_adc', 0)}")
    return lines


def _pooled_metrics(phase_metrics_list: list) -> dict:
    """Statistica complessiva del test (tutte le fasi insieme), come media
    pesata sul numero di tap di ciascuna fase."""
    total_n = sum(m.get("n_tap", 0) for m in phase_metrics_list)
    if total_n == 0:
        return {"n_tap": 0, "frequenza_media_hz": 0, "iti_sd_ms": 0,
                "asincronia_media_ms": 0, "asincronia_sd_ms": 0, "forza_media_adc": 0}

    def wavg(key, ndigits):
        s = sum(m.get(key, 0) * m.get("n_tap", 0) for m in phase_metrics_list)
        return round(s / total_n, ndigits)

    return {
        "n_tap": total_n,
        "frequenza_media_hz": wavg("frequenza_media_hz", 3),
        "iti_sd_ms": wavg("iti_sd_ms", 2),
        "asincronia_media_ms": wavg("asincronia_media_ms", 2),
        "asincronia_sd_ms": wavg("asincronia_sd_ms", 2),
        "forza_media_adc": wavg("forza_media_adc", 1),
    }


def _write_test_column(ws, test_key: str, raw_events: list, phases_metrics: dict):
    from openpyxl.styles import Font
    header_font = Font(bold=True, size=13)
    label_font = Font(bold=True)

    col = TEST_START_COL[test_key]

    ws.cell(row=1, column=col, value=TEST_HEADERS[test_key]).font = header_font
    ws.cell(row=3, column=col, value="secondi, forza, stato motore").font = label_font

    if test_key == "rhythmA":
        segments = _split_on_reset(raw_events)
        marker = "INIZIO FASE 2"
    else:
        segments = [raw_events]
        marker = None

    row = 4
    for i, seg in enumerate(segments):
        for event in seg:
            ws.cell(row=row, column=col, value=_fmt_tap(event))
            row += 1
        if marker and i < len(segments) - 1:
            row += 1
            ws.cell(row=row, column=col, value=marker)
            row += 2

    # Blocco statistiche complessivo (tutte le fasi insieme)
    row += 1
    if test_key == "eligibility":
        combined = phases_metrics.get("full", {})
    else:
        phase_keys = RHYTHM_A_PHASE_KEYS if test_key == "rhythmA" else RHYTHM_B_PHASE_KEYS
        combined = _pooled_metrics([phases_metrics.get(k, {}) for k in phase_keys])
    for line in _stat_lines(combined, "Final analysis of the recorded data", include_recorded_tap=True):
        ws.cell(row=row, column=col, value=line)
        row += 1

    # Blocchi per singola fase (solo Rhythm A / Rhythm B)
    if test_key == "rhythmA":
        phase_keys, phase_labels = RHYTHM_A_PHASE_KEYS, RHYTHM_A_PHASE_LABELS
    elif test_key == "rhythmB":
        phase_keys, phase_labels = RHYTHM_B_PHASE_KEYS, RHYTHM_B_PHASE_LABELS
    else:
        phase_keys, phase_labels = [], []

    for key, label in zip(phase_keys, phase_labels):
        row += 1
        metrics = phases_metrics.get(key, {})
        for line in _stat_lines(metrics, label, include_recorded_tap=False):
            ws.cell(row=row, column=col, value=line)
            row += 1

    ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = 26


def patient_report_path(paziente_id: str, base_dir: str) -> str:
    return os.path.join(base_dir, "patient_reports", f"{_safe_filename(paziente_id)}.xlsx")


def export_patient_report(paziente_id: str, session_timestamp: str,
                           raw_events: dict, session_results: dict, base_dir: str) -> str:
    """
    Crea/aggiorna patient_reports/<paziente_id>.xlsx aggiungendo un nuovo
    foglio per questa sessione (le sessioni precedenti restano intatte).
    Ritorna il path del file scritto.
    """
    from openpyxl import Workbook, load_workbook

    path = patient_report_path(paziente_id, base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if os.path.isfile(path):
        wb = load_workbook(path)
    else:
        wb = Workbook()
        wb.remove(wb.active)  # rimuove il foglio vuoto di default

    sheet_name = _safe_sheet_name(session_timestamp)
    if sheet_name in wb.sheetnames:
        del wb[sheet_name]
    ws = wb.create_sheet(title=sheet_name)

    for test_key in ("eligibility", "rhythmA", "rhythmB"):
        _write_test_column(ws, test_key, raw_events.get(test_key, []), session_results.get(test_key, {}))

    wb.save(path)
    return path


def delete_patient_report(paziente_id: str, base_dir: str):
    """Rimuove il file report di un paziente (usato quando il paziente viene eliminato)."""
    path = patient_report_path(paziente_id, base_dir)
    if os.path.isfile(path):
        os.remove(path)


def rename_patient_report(old_id: str, new_id: str, base_dir: str):
    """Rinomina il file report di un paziente (usato quando si rinomina un paziente)."""
    old_path = patient_report_path(old_id, base_dir)
    new_path = patient_report_path(new_id, base_dir)
    if os.path.isfile(old_path):
        os.makedirs(os.path.dirname(new_path), exist_ok=True)
        os.replace(old_path, new_path)
