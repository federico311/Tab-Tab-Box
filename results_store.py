"""
results_store.py
Salva e legge le sessioni di test (eligibility + rhythm A + rhythm B)
in un unico file CSV, una riga per sessione/paziente. Ogni test puo'
avere una o piu' FASI (es. Rhythm A: paced/accelerata, Rhythm B: 3
fasce di beat), quindi le colonne sono organizzate come
"<test>_<fase>_<metrica>".

Gestisce inoltre i METADATI dei pazienti (preferiti, cartelle) in un
file JSON separato (patients_meta.json).
"""

import csv
import json
import os
from datetime import datetime

import xlsx_export

TEST_PREFIXES = {"eligibility": "elig", "rhythmA": "rhA", "rhythmB": "rhB"}

# Fasi previste per ciascun test: (chiave_fase, etichetta_o_None)
TEST_PHASES = {
    "eligibility": [("full", None)],
    "rhythmA": [("phase1", "Phase 1 - Paced"), ("phase2", "Phase 2 - Accelerated")],
    "rhythmB": [("phase1", "Beats 1-15"), ("phase2", "Beats 26-105"), ("phase3", "Beats 106-120")],
}

METRIC_KEYS = [
    "n_tap", "frequenza_media_hz", "iti_medio_ms", "iti_sd_ms",
    "asincronia_media_ms", "asincronia_sd_ms", "forza_media_adc",
]

FIELDNAMES = ["timestamp", "paziente_id"]
for _test_key, _prefix in TEST_PREFIXES.items():
    for _phase_key, _label in TEST_PHASES[_test_key]:
        for _mk in METRIC_KEYS:
            FIELDNAMES.append(f"{_prefix}_{_phase_key}_{_mk}")


class ResultsStore:
    def __init__(self, filepath: str):
        self.filepath = filepath
        self.meta_filepath = os.path.join(os.path.dirname(filepath), "patients_meta.json")
        self._ensure_header()
        self._ensure_meta()

    # ---------------- SESSIONS (CSV) ----------------
    def _ensure_header(self):
        if not os.path.isfile(self.filepath):
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
            return
        self._migrate_header_if_needed()

    def _migrate_header_if_needed(self):
        """
        csv.DictWriter always appends new rows positioned according to
        today's FIELDNAMES, but it never rewrites the header line already
        on disk. If sessions.csv was created by an older version of this
        file (different/renamed columns), the header on disk goes stale:
        every row saved afterwards still has correct data, but
        csv.DictReader (used everywhere data is read back) maps values
        using that stale header, so values land under the wrong column
        names - e.g. "elig_full_n_tap" is no longer found, and rows
        silently disappear from the per-test data views even though they
        were saved correctly for the patient. This detects that mismatch
        and rewrites the file with today's header, remapping every column
        that still matches by name and leaving genuinely new columns
        blank. Existing sessions are preserved, not lost.
        """
        with open(self.filepath, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            on_disk_header = reader.fieldnames or []
            if on_disk_header == FIELDNAMES:
                return  # already up to date
            rows = list(reader)

        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for old_row in rows:
                writer.writerow({fn: old_row.get(fn, "") for fn in FIELDNAMES})

    def save_session(self, paziente_id: str, results: dict) -> str:
        """
        results: { "eligibility": {fase: {label, ...metriche}}, "rhythmA": {...}, "rhythmB": {...} }
        Ritorna il timestamp usato per la riga, cosi' i tap grezzi (vedi
        save_raw_taps) possono essere collegati alla stessa sessione.
        """
        timestamp = datetime.now().isoformat(timespec="seconds")
        row = {
            "timestamp": timestamp,
            "paziente_id": paziente_id,
        }
        for test_key, prefix in TEST_PREFIXES.items():
            phases = results.get(test_key, {})
            for phase_key, _label in TEST_PHASES[test_key]:
                metrics = phases.get(phase_key, {})
                for mk in METRIC_KEYS:
                    row[f"{prefix}_{phase_key}_{mk}"] = metrics.get(mk, "")

        with open(self.filepath, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writerow(row)

        # Registra il paziente nei metadati (senza toccare preferiti/cartella se già esiste)
        self._ensure_patient_meta(paziente_id)
        return timestamp

    # ---------------- PATIENT REPORT (.xlsx: every single tap + stats) ----------------
    def save_patient_report(self, paziente_id: str, session_timestamp: str, raw_events: dict, results: dict) -> str:
        """
        Genera/aggiorna il file Excel completo del paziente (ogni singolo
        tap grezzo, diviso per test, piu' le statistiche finali) nel
        formato richiesto. Vedi xlsx_export.py per il layout esatto.
        """
        return xlsx_export.export_patient_report(
            paziente_id, session_timestamp, raw_events, results,
            base_dir=os.path.dirname(self.filepath),
        )

    def read_all(self) -> list:
        if not os.path.isfile(self.filepath):
            return []
        with open(self.filepath, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def read_for_test_phase(self, test_key: str, phase_key: str) -> list:
        """Righe filtrate per un singolo test + fase (usato dalle sezioni 'Dati X')."""
        prefix = TEST_PREFIXES[test_key]
        result = []
        for row in self.read_all():
            if row.get(f"{prefix}_{phase_key}_n_tap"):
                entry = {"timestamp": row["timestamp"], "paziente_id": row["paziente_id"]}
                for mk in METRIC_KEYS:
                    entry[mk] = row.get(f"{prefix}_{phase_key}_{mk}", "")
                result.append(entry)
        return result

    def read_for_patient(self, paziente_id: str) -> list:
        """Tutte le sessioni di un paziente, con i dati di tutti e 3 i test insieme."""
        return [r for r in self.read_all() if r.get("paziente_id") == paziente_id]

    def delete_patient(self, paziente_id: str):
        """Rimuove completamente un paziente: tutte le sue sessioni + i suoi metadati + i tap grezzi."""
        rows = [r for r in self.read_all() if r.get("paziente_id") != paziente_id]
        with open(self.filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)
        meta = self._load_meta()
        meta["patients"].pop(paziente_id, None)
        self._save_meta(meta)
        xlsx_export.delete_patient_report(paziente_id, base_dir=os.path.dirname(self.filepath))

    # ---------------- PATIENT METADATA (JSON: favorites, folders) ----------------
    def _ensure_meta(self):
        if not os.path.isfile(self.meta_filepath):
            self._save_meta({"patients": {}, "folders": []})

    def _load_meta(self) -> dict:
        try:
            with open(self.meta_filepath, encoding="utf-8") as f:
                data = json.load(f)
                data.setdefault("patients", {})
                data.setdefault("folders", [])
                return data
        except (FileNotFoundError, json.JSONDecodeError):
            return {"patients": {}, "folders": []}

    def _save_meta(self, data: dict):
        with open(self.meta_filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _ensure_patient_meta(self, paziente_id: str):
        meta = self._load_meta()
        if paziente_id not in meta["patients"]:
            meta["patients"][paziente_id] = {"favorite": False, "folder": None}
            self._save_meta(meta)

    def get_folders(self) -> list:
        return list(self._load_meta()["folders"])

    def create_folder(self, name: str):
        meta = self._load_meta()
        if name and name not in meta["folders"]:
            meta["folders"].append(name)
            self._save_meta(meta)

    def delete_folder(self, name: str):
        meta = self._load_meta()
        if name in meta["folders"]:
            meta["folders"].remove(name)
            for pdata in meta["patients"].values():
                if pdata.get("folder") == name:
                    pdata["folder"] = None
            self._save_meta(meta)

    def rename_folder(self, old_name: str, new_name: str):
        """Rinomina una cartella. Se new_name coincide con una cartella gia'
        esistente, le due cartelle vengono unite (i pazienti di old_name
        vengono spostati in new_name) invece di creare un duplicato."""
        new_name = (new_name or "").strip()
        if not new_name or old_name == new_name:
            return
        meta = self._load_meta()
        if old_name not in meta["folders"]:
            return
        if new_name in meta["folders"]:
            meta["folders"].remove(old_name)
        else:
            idx = meta["folders"].index(old_name)
            meta["folders"][idx] = new_name
        for pdata in meta["patients"].values():
            if pdata.get("folder") == old_name:
                pdata["folder"] = new_name
        self._save_meta(meta)

    def set_patient_folder(self, paziente_id: str, folder: str):
        meta = self._load_meta()
        self._ensure_patient_meta_inline(meta, paziente_id)
        meta["patients"][paziente_id]["folder"] = folder
        self._save_meta(meta)

    def toggle_favorite(self, paziente_id: str) -> bool:
        """Inverte lo stato preferito del paziente e ritorna il nuovo stato."""
        meta = self._load_meta()
        self._ensure_patient_meta_inline(meta, paziente_id)
        new_state = not meta["patients"][paziente_id].get("favorite", False)
        meta["patients"][paziente_id]["favorite"] = new_state
        self._save_meta(meta)
        return new_state

    def is_favorite(self, paziente_id: str) -> bool:
        meta = self._load_meta()
        return meta["patients"].get(paziente_id, {}).get("favorite", False)

    def get_patient_folder(self, paziente_id: str):
        meta = self._load_meta()
        return meta["patients"].get(paziente_id, {}).get("folder")

    def _ensure_patient_meta_inline(self, meta: dict, paziente_id: str):
        if paziente_id not in meta["patients"]:
            meta["patients"][paziente_id] = {"favorite": False, "folder": None}

    def get_all_patient_ids(self) -> list:
        """Elenco di tutti i pazienti distinti (con almeno una sessione)."""
        ids = []
        for r in self.read_all():
            pid = r.get("paziente_id")
            if pid and pid not in ids:
                ids.append(pid)
        return ids

    def rename_patient(self, old_id: str, new_id: str) -> bool:
        """
        Rinomina un paziente ovunque (sessioni salvate, preferiti/cartella,
        file Excel del referto). Ritorna False se new_id e' vuoto, uguale
        al vecchio, o gia' usato da un altro paziente (per evitare di unire
        per errore i dati di due pazienti diversi); True se la rinomina e'
        andata a buon fine.
        """
        new_id = (new_id or "").strip()
        if not new_id or new_id == old_id:
            return False
        if new_id in self.get_all_patient_ids():
            return False

        rows = self.read_all()
        changed = False
        for r in rows:
            if r.get("paziente_id") == old_id:
                r["paziente_id"] = new_id
                changed = True
        if changed:
            with open(self.filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
                writer.writeheader()
                for r in rows:
                    writer.writerow({fn: r.get(fn, "") for fn in FIELDNAMES})

        meta = self._load_meta()
        if old_id in meta["patients"]:
            meta["patients"][new_id] = meta["patients"].pop(old_id)
            self._save_meta(meta)

        xlsx_export.rename_patient_report(old_id, new_id, base_dir=os.path.dirname(self.filepath))
        return True
