"""Dataset experimental de actividad de PET hidrolasas -> tablas numéricas para los algoritmos.

Fuente: Norton-Baker, Komp, Gado et al., "Activity across temperature and pH of PET hydrolase candidates",
Zenodo 10.5281/zenodo.15417757 (CC-BY-4.0). 213 secuencias, actividad medida en 11 condiciones
(pH 4.5-8.5, 40/60 °C, PET cristalino en polvo o film amorfo), con splits de validación cruzada.

Genera en workspace.lab:
  enzyme_features   una fila por enzima: propiedades fisicoquímicas calculadas de la secuencia (Biopython)
  pet_activity      una fila por (enzima, condición) MEDIDA: pH, temperatura, sustrato, actividad
  pet_activity_ml   vista lista para modelar: pet_activity + enzyme_features

Uso: uv run python -m data_pipeline.datasets.pet_activity [--sink databricks]
"""

import argparse
import csv
import io
import re
import tarfile
from pathlib import Path

from Bio.SeqUtils.ProtParam import ProteinAnalysis

from data_pipeline.connectors.base import Http

RECORD = "15417757"
TAR_URL = f"https://zenodo.org/api/records/{RECORD}/files/p740.tar.gz/content"
ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "zenodo" / RECORD
OUT = ROOT / "data" / "datasets" / "pet_activity"
VOLUME_DIR = "/Volumes/workspace/lab/raw/datasets/pet_activity"
CONDITION = re.compile(r"activity_at_(?P<ph>[\d.]+)_(?P<temp>\d+)_(?P<substrate>\w+)")
SUBSTRATES = {"cryPow": "crystalline_powder", "aFilm": "amorphous_film"}
AMINO_ACIDS = "ACDEFGHIKLMNPQRSTVWY"
HYDROPHOBIC = set("AILMFWVY")


def download() -> Path:
    tar_path = CACHE / "p740.tar.gz"
    if not tar_path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        tar_path.write_bytes(Http(timeout=600).request("GET", TAR_URL).content)
    return tar_path


def read_labels(tar_path: Path) -> list[dict]:
    with tarfile.open(tar_path) as tar:
        f = tar.extractfile("./p740/label_data.csv")
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))


def features(enzyme_id: str, sequence: str) -> dict:
    clean = "".join(c for c in sequence.upper() if c in AMINO_ACIDS)
    pa = ProteinAnalysis(clean)
    helix, turn, sheet = pa.secondary_structure_fraction()
    row = {
        "enzyme_id": enzyme_id,
        "seq_length": len(clean),
        "nonstandard_residues": len(sequence) - len(clean),
        "molecular_weight": round(pa.molecular_weight(), 2),
        "isoelectric_point": round(pa.isoelectric_point(), 3),
        "charge_ph7": round(pa.charge_at_pH(7.0), 3),
        "aromaticity": round(pa.aromaticity(), 4),
        "instability_index": round(pa.instability_index(), 3),
        "gravy": round(pa.gravy(), 4),
        "hydrophobic_fraction": round(sum(c in HYDROPHOBIC for c in clean) / len(clean), 4),
        "helix_fraction": round(helix, 4),
        "turn_fraction": round(turn, 4),
        "sheet_fraction": round(sheet, 4),
    }
    composition = pa.amino_acids_percent
    row.update({f"aa_{aa}": round(composition.get(aa, 0.0), 4) for aa in AMINO_ACIDS})
    return row


def build(labels: list[dict]) -> tuple[list[dict], list[dict]]:
    feature_rows, activity_rows = [], []
    for r in labels:
        enzyme_id = r[""]
        feature_rows.append({
            **features(enzyme_id, r["sequence"]),
            "sequence": r["sequence"],
            "design_round": int(r["round"]),
            "cv_split": int(r["cross_val_split"]),
            "temporal_split": int(r["temporal_split"]),
            "has_nonzero_activity": r["has_nonzero_activity_anywhere"] == "True",
            "max_observed_activity": float(r["max_observed_activity"]),
        })
        for col, value in r.items():
            m = CONDITION.fullmatch(col)
            if not m or value.strip() == "":
                continue  # vacío = condición no medida para esa enzima, NO actividad cero
            activity_rows.append({
                "enzyme_id": enzyme_id,
                "ph": float(m["ph"]),
                "temperature_c": int(m["temp"]),
                "substrate": SUBSTRATES.get(m["substrate"], m["substrate"]),
                "activity": float(value),
                "is_active": float(value) > 0,
                "cv_split": int(r["cross_val_split"]),
                "source_doc_id": f"zenodo:{RECORD}",
            })
    return feature_rows, activity_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def publish(tar_path: Path) -> None:
    from data_pipeline.databricks_io import Databricks

    db = Databricks()
    db.upload_dir(OUT, VOLUME_DIR)
    with tar_path.open("rb") as fh:  # incluye las 1679 estructuras AF2 para el visor y features 3D
        db.w.files.upload(f"/Volumes/workspace/lab/raw/zenodo/{RECORD}/p740.tar.gz", fh, overwrite=True)
    for table in ("enzyme_features", "pet_activity"):
        db.sql(f"""
            CREATE OR REPLACE TABLE workspace.lab.{table}
            COMMENT 'Derivada de Zenodo {RECORD} (CC-BY-4.0) por data_pipeline/datasets/pet_activity.py'
            AS SELECT * FROM read_files('{VOLUME_DIR}/{table}.csv', format => 'csv', header => true, inferSchema => true)
        """)
    db.sql("""
        CREATE OR REPLACE VIEW workspace.lab.pet_activity_ml
        COMMENT 'Una fila por (enzima, condición medida) con features de secuencia; objetivo: activity / is_active'
        AS SELECT a.enzyme_id, a.ph, a.temperature_c, a.substrate, a.cv_split,
                  f.* EXCEPT (enzyme_id, sequence, cv_split, has_nonzero_activity, max_observed_activity),
                  a.activity, a.is_active
           FROM workspace.lab.pet_activity a JOIN workspace.lab.enzyme_features f USING (enzyme_id)
    """)
    counts = db.sql("SELECT (SELECT count(*) FROM workspace.lab.enzyme_features), (SELECT count(*) FROM workspace.lab.pet_activity)")
    print(f"databricks: enzyme_features={counts[0][0]} pet_activity={counts[0][1]} + vista pet_activity_ml")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sink", choices=("local", "databricks"), default="local")
    args = p.parse_args()
    tar_path = download()
    feature_rows, activity_rows = build(read_labels(tar_path))
    write_csv(OUT / "enzyme_features.csv", feature_rows)
    write_csv(OUT / "pet_activity.csv", activity_rows)
    active = sum(r["is_active"] for r in activity_rows)
    print(f"local: {len(feature_rows)} enzimas, {len(activity_rows)} mediciones ({active} con actividad > 0) en {OUT}")
    if args.sink == "databricks":
        publish(tar_path)


if __name__ == "__main__":
    main()
