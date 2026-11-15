"""Dataset manifest tooling: validation, reproducible splits and a leakage audit.

Pure Python (standard library plus jsonschema). Nothing here imports FastAPI,
SQLAlchemy or the request/response schemas, and nothing reads or writes a
database, so these tools can run offline over a dataset directory.
"""

from app.dataset_manifest.audit import AuditResult, audit_manifests
from app.dataset_manifest.issues import Issue, Report
from app.dataset_manifest.loading import (
    LoadedManifest,
    ManifestLoadError,
    dumps_canonical,
    load_manifest,
)
from app.dataset_manifest.splits import (
    SplitConfig,
    SplitGenerationError,
    SplitResult,
    apply_splits,
    generate_splits,
)
from app.dataset_manifest.restricted import RestrictedPathError, ensure_not_restricted
from app.dataset_manifest.validation import ValidationResult, source_view, validate_document

__all__ = [
    "AuditResult",
    "Issue",
    "LoadedManifest",
    "ManifestLoadError",
    "Report",
    "RestrictedPathError",
    "SplitConfig",
    "SplitGenerationError",
    "SplitResult",
    "ValidationResult",
    "apply_splits",
    "audit_manifests",
    "dumps_canonical",
    "ensure_not_restricted",
    "generate_splits",
    "load_manifest",
    "source_view",
    "validate_document",
]
