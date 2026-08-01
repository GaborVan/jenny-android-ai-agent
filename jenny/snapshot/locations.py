"""Layout su disco del sottosistema snapshot/backup.

Tutto ciò che deve SOPRAVVIVERE allo swap atomico del workspace (store degli
snapshot, staging, marker, copie di sicurezza) vive nella *runtime root*: la
directory che contiene il workspace (su Android è ``filesDir``, la stessa
``data_dir`` passata a ``run_gateway``).

    <runtime_root>/
      workspace/               ← l'albero versionato/scambiato al restore
      snapshots/               ← store content-addressed (storia)
      backup_staging/          ← file .jbk in export/import + albero staged
      restore_pending.json     ← marker del protocollo di restore al boot
      workspace_pre_restore_*  ← copie di sicurezza del workspace sostituito
"""

from __future__ import annotations

from pathlib import Path

STAGED_WORKSPACE_DIR_NAME = "workspace_staged"
STAGED_SNAPSHOTS_DIR_NAME = "snapshots_staged"
MARKER_FILE_NAME = "restore_pending.json"
SAFETY_DIR_PREFIX = "workspace_pre_restore_"
SANITY_FILE_NAME = ".jenny_restore_manifest.json"


def runtime_root_for(workspace_path: Path) -> Path:
    return Path(workspace_path).resolve().parent


def snapshots_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / "snapshots"


def backup_staging_dir_for(workspace_path: Path) -> Path:
    return runtime_root_for(workspace_path) / "backup_staging"
