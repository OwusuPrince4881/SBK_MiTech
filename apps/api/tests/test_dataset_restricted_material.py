"""The UMaT/MN273 prohibition and the restricted-location guard.

Only synthetic look-alike paths in temporary folders are used. The real
`data/evaluation`, `data/umat_practicals` and `data/uploads` are never touched.
"""

import json
import os
import pathlib
from pathlib import Path
from typing import Any

import pytest

from app.dataset_manifest import (
    RestrictedPathError,
    SplitConfig,
    SplitGenerationError,
    audit_manifests,
    generate_splits,
    load_manifest,
    validate_document,
)
from app.dataset_manifest import files as files_module
from app.dataset_manifest.restricted import ensure_not_restricted, lexical_restriction, restriction_of
from app.dataset_manifest.validation import ValidationResult
from dataset_builders import build_dataset, clone, link_directory
from scripts import audit_dataset_leakage, generate_dataset_splits, validate_dataset_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_1_0 = REPO_ROOT / "docs" / "examples" / "example_dataset_manifest.json"
RATIOS = {"train": 0.7, "validation": 0.15, "test": 0.15}


def error_codes(result: ValidationResult) -> set[str]:
    return {issue.code for issue in result.report.errors}


def make_umat(manifest: dict[str, Any], *, status: str = "approved", reference: str | None = "x") -> dict[str, Any]:
    result = clone(manifest)
    result["dataset"]["category"] = "umat_practical"
    permissions = result["dataset"]["permissions"]
    permissions["status"] = status
    permissions["permitted_uses"] = ["independent_evaluation"] if status == "approved" else []
    if reference is not None:
        permissions["authorisation_reference"] = reference
    for record in result["records"]:
        record["split"] = "independent_evaluation" if status == "approved" else "unassigned"
    return result


def lookalike(tmp_path: Path, *parts: str, samples: int = 3) -> tuple[Path, dict[str, Any]]:
    """A synthetic dataset in a folder that merely *looks like* a restricted location."""
    root = tmp_path.joinpath(*parts)
    return root, build_dataset(root, sample_count=samples, images_per_sample=2)


class OpenSpy:
    """Records every file opened through pathlib while active."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.names: list[str] = []
        real = pathlib.Path.open

        def spy(path: Path, *args: Any, **kwargs: Any) -> Any:
            self.names.append(path.name)
            return real(path, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "open", spy)



# --- the guard itself --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "location"),
    [
        ("data/evaluation", "data/evaluation"),
        ("data/evaluation/umat_practicals/set-1", "umat_practicals"),
        ("data/umat_practicals", "umat_practicals"),
        ("data/uploads", "data/uploads"),
        ("data/uploads/abc/def.jpg", "data/uploads"),
        ("DATA/Evaluation/x", "data/evaluation"),
        ("backup/data/uploads", "data/uploads"),
        ("elsewhere/umat_practicals", "umat_practicals"),
        ("x/UMAT_PRACTICALS/y", "umat_practicals"),
        ("data/./evaluation", "data/evaluation"),
        ("fine/../data/uploads", "data/uploads"),
    ],
)
def test_restricted_locations_are_recognised_from_the_path_alone(
    tmp_path: Path, relative: str, location: str
) -> None:
    # The paths do not exist: recognition must not need the file system.
    assert lexical_restriction(tmp_path / relative) == location


@pytest.mark.parametrize(
    "relative",
    [
        "data/datasets/raw/public-set",
        "data/datasets/manifests",
        "docs",
        "evaluation",
        "uploads",
        "data/evaluations",
        "data/upload",
        "mydata/uploads",
        "umat_practicals_not",
    ],
)
def test_ordinary_locations_are_not_restricted(tmp_path: Path, relative: str) -> None:
    assert lexical_restriction(tmp_path / relative) is None


def test_the_lexical_check_touches_nothing_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the file system was touched")

    monkeypatch.setattr(os, "scandir", forbidden)
    monkeypatch.setattr(os.path, "realpath", forbidden)
    monkeypatch.setattr(pathlib.Path, "resolve", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(os, "stat", forbidden)

    assert restriction_of(tmp_path / "data" / "evaluation" / "umat_practicals") == "umat_practicals"
    with pytest.raises(RestrictedPathError, match="restricted location"):
        ensure_not_restricted(tmp_path / "data" / "uploads", "Dataset directory")


def test_the_refusal_message_names_the_role_the_location_and_the_rule(tmp_path: Path) -> None:
    with pytest.raises(RestrictedPathError) as caught:
        ensure_not_restricted(tmp_path / "data" / "uploads", "Dataset directory")

    message = str(caught.value)
    assert "Dataset directory" in message and "data/uploads" in message
    assert "never read, hash, scan or write" in message
    assert "no manifest field can change that" in message


def test_a_link_into_a_restricted_location_is_refused_after_resolution(tmp_path: Path) -> None:
    target, _ = lookalike(tmp_path, "data", "uploads")
    alias = tmp_path / "harmless-name"
    if not link_directory(alias, target):
        pytest.skip("this environment cannot create a symbolic link or junction")

    location = restriction_of(alias)

    assert location is not None and "reached through a link" in location


# --- the validator, splitter and audit all refuse restricted directories -------------------------


@pytest.mark.parametrize(
    "parts",
    [("data", "evaluation"), ("data", "evaluation", "umat_practicals"), ("data", "umat_practicals"), ("data", "uploads")],
)
def test_the_validator_never_opens_anything_in_a_restricted_directory(
    tmp_path: Path, parts: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest = lookalike(tmp_path, *parts)
    spy = OpenSpy(monkeypatch)
    listed: list[str] = []
    real_scandir = os.scandir
    monkeypatch.setattr(files_module.os, "scandir", lambda path: listed.append(str(path)) or real_scandir(path))

    result = validate_document(manifest, dataset_dir=root)

    assert not result.ok
    assert error_codes(result) == {"restricted-directory"}
    assert result.files_verified is False
    assert not any(name.endswith(".jpg") for name in spy.names), "an image was opened"
    assert listed == [], "the restricted directory was listed"


def test_a_restricted_directory_is_refused_even_when_it_does_not_exist(tmp_path: Path) -> None:
    manifest = build_dataset(tmp_path / "ok", sample_count=3, images_per_sample=2)

    result = validate_document(manifest, dataset_dir=tmp_path / "data" / "uploads" / "nowhere")

    assert error_codes(result) == {"restricted-directory"}  # not "dataset-dir-invalid"


def test_the_splitter_refuses_a_restricted_directory(tmp_path: Path) -> None:
    root, manifest = lookalike(tmp_path, "data", "evaluation", "set", samples=30)

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)

    assert caught.value.report is not None and "restricted-directory" in caught.value.report.codes()


def test_the_audit_refuses_a_restricted_directory(tmp_path: Path) -> None:
    root, manifest = lookalike(tmp_path, "data", "umat_practicals")

    result = audit_manifests([("m.json", manifest)], dataset_dirs={"m.json": root})

    assert not result.passed()
    assert "restricted-directory" in {issue.code for issue in result.report.errors}


def test_a_restricted_folder_inside_a_dataset_directory_is_not_entered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, manifest = lookalike(tmp_path, "dataset")
    hidden = root / "data" / "evaluation"
    hidden.mkdir(parents=True)
    (hidden / "inside.jpg").write_bytes(b"\xff\xd8\xff synthetic")
    spy = OpenSpy(monkeypatch)

    result = validate_document(manifest, dataset_dir=root)

    assert "restricted-directory" in error_codes(result)
    assert "inside.jpg" not in spy.names
    assert result.unmanifested_images == []  # the folder was never entered, so nothing in it is listed


def test_the_cli_refuses_a_restricted_dataset_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest = lookalike(tmp_path, "data", "uploads")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    validate = validate_dataset_manifest.main([str(manifest_path), "--dataset-dir", str(root)])
    err = capsys.readouterr().err
    audit = audit_dataset_leakage.main([str(manifest_path), "--dataset-dir", str(root)])
    audit_out = capsys.readouterr().out

    assert validate == 2 and "restricted location" in err
    assert audit == 2 and "restricted-directory" in audit_out and "RESULT: FAIL" in audit_out


@pytest.mark.parametrize("parts", [("data", "evaluation"), ("data", "uploads"), ("data", "umat_practicals")])
def test_a_manifest_in_a_restricted_location_is_never_read(
    tmp_path: Path, parts: tuple[str, ...], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = tmp_path.joinpath(*parts)
    folder.mkdir(parents=True)
    manifest_path = folder / "restricted-manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    spy = OpenSpy(monkeypatch)

    with pytest.raises(RestrictedPathError):
        load_manifest(manifest_path)
    codes = [
        validate_dataset_manifest.main([str(manifest_path)]),
        audit_dataset_leakage.main([str(manifest_path)]),
    ]

    assert codes == [2, 2]
    assert "restricted-manifest.json" not in spy.names
    assert "restricted location" in capsys.readouterr().err


def test_the_splitter_never_writes_into_a_restricted_location(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest = lookalike(tmp_path, "ok", samples=30)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    target = tmp_path / "data" / "uploads"
    target.mkdir(parents=True)

    code = generate_dataset_splits.main(
        [str(manifest_path), "--dataset-dir", str(root), "--seed", "1", "--output", str(target / "s.json")]
    )

    assert code == 2
    assert "restricted location" in capsys.readouterr().err
    assert not (target / "s.json").exists()


def test_a_source_manifest_in_a_restricted_location_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, manifest = lookalike(tmp_path, "ok")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    folder = tmp_path / "data" / "evaluation"
    folder.mkdir(parents=True)
    (folder / "source.json").write_text("{}", encoding="utf-8")
    spy = OpenSpy(monkeypatch)

    code = validate_dataset_manifest.main(
        [str(manifest_path), "--source-manifest", str(folder / "source.json")]
    )

    assert code == 2
    assert "source.json" not in spy.names


# --- UMaT/MN273 material is refused for every use, in every tool ----------------------------------


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    root = tmp_path / "dataset"
    return build_dataset(root, sample_count=4, images_per_sample=2), root


@pytest.mark.parametrize("status", ["approved", "pending_review", "unknown", "denied"])
@pytest.mark.parametrize("reference", [None, "x", "Letter of 2026-03-02, signed."])
def test_the_validator_refuses_umat_data_whatever_is_recorded(
    dataset: tuple[dict[str, Any], Path], status: str, reference: str | None
) -> None:
    manifest = make_umat(dataset[0], status=status, reference=reference)

    result = validate_document(manifest, dataset_dir=dataset[1])

    assert "restricted-category" in error_codes(result)
    assert result.cleared_uses == ()


@pytest.mark.parametrize("use", ["training", "validation", "testing", "independent_evaluation"])
def test_the_audit_refuses_umat_data_whatever_is_recorded(
    dataset: tuple[dict[str, Any], Path], use: str
) -> None:
    manifest = make_umat(dataset[0], reference="Letter of 2026-03-02, signed.")

    result = audit_manifests([("umat.json", manifest)], dataset_dirs={"umat.json": dataset[1]})

    assert not result.passed()
    assert "restricted-category" in {issue.code for issue in result.report.errors}
    assert not result.passed(strict=False)


def test_the_splitter_refuses_umat_data_with_a_clear_message(
    dataset: tuple[dict[str, Any], Path],
) -> None:
    manifest = make_umat(dataset[0], reference="Letter of 2026-03-02, signed.")
    manifest["dataset"]["permissions"]["permitted_uses"] = ["training", "validation", "testing"]
    for record in manifest["records"]:
        record["split"] = "unassigned"

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=dataset[1])

    assert "restricted UMaT/MN273 material" in str(caught.value)
    assert "no manifest field changes that" in str(caught.value)
    assert caught.value.report is not None and "restricted-category" in caught.value.report.codes()


def test_the_cli_tools_refuse_umat_data(
    tmp_path: Path, dataset: tuple[dict[str, Any], Path], capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = make_umat(dataset[0], reference="Letter of 2026-03-02, signed.")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    codes = [
        validate_dataset_manifest.main([str(manifest_path)]),
        validate_dataset_manifest.main(
            [str(manifest_path), "--dataset-dir", str(dataset[1]), "--use", "independent_evaluation"]
        ),
        audit_dataset_leakage.main([str(manifest_path), "--dataset-dir", str(dataset[1])]),
        generate_dataset_splits.main(
            [str(manifest_path), "--dataset-dir", str(dataset[1]), "--seed", "1", "--output", str(tmp_path / "s.json")]
        ),
    ]

    captured = capsys.readouterr()
    assert codes == [2, 2, 2, 2]
    assert "restricted-category" in captured.err + captured.out
    assert not (tmp_path / "s.json").exists()


# --- the prohibition does not rely on the declared category -----------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        lambda m: m["dataset"].update(dataset_id="umat-practical-set-1"),
        lambda m: m["dataset"].update(name="UMaT practical samples"),
        lambda m: m["dataset"]["source"].update(name="MN273 team"),
        lambda m: m["dataset"]["source"].update(url="https://example.invalid/mn-273/photos"),
        lambda m: m["dataset"]["source"].update(custodian="MN 273 project group"),
        lambda m: m["records"][0]["file"].update(relative_path="images/UMAT_practicals/x.jpg", filename="x.jpg"),
        lambda m: m["dataset"].update(name="mn_273 images"),
    ],
)
def test_restricted_names_are_caught_even_when_the_category_says_otherwise(
    dataset: tuple[dict[str, Any], Path], change: Any
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["category"] = "ghana_field"
    change(manifest)

    result = validate_document(manifest)

    assert not result.ok
    assert "restricted-material-suspected" in error_codes(result)


@pytest.mark.parametrize("name", ["Formatted field photographs", "Automatic classification set", "Kumasi field set"])
def test_ordinary_names_are_not_mistaken_for_restricted_material(
    dataset: tuple[dict[str, Any], Path], name: str
) -> None:
    manifest = clone(dataset[0])
    manifest["dataset"]["name"] = name

    assert validate_document(manifest).ok


def test_the_name_tripwire_also_covers_schema_1_0() -> None:
    manifest = json.loads(EXAMPLE_1_0.read_text(encoding="utf-8"))
    manifest["dataset"]["dataset_id"] = "umat-practicals-copy"

    result = validate_document(manifest)

    assert "restricted-material-suspected" in error_codes(result)


def test_a_restricted_name_in_a_manifest_stops_the_splitter(tmp_path: Path) -> None:
    root, manifest = lookalike(tmp_path, "ok", samples=30)
    manifest["dataset"]["category"] = "ghana_field"
    manifest["dataset"]["name"] = "UMaT practical photographs"

    with pytest.raises(SplitGenerationError) as caught:
        generate_splits(manifest, SplitConfig(seed=1, ratios=RATIOS), dataset_dir=root)

    assert caught.value.report is not None
    assert "restricted-material-suspected" in caught.value.report.codes()


# --- no tool says that anything is "confirmed" ---------------------------------------------------------


def test_no_message_in_the_tools_claims_a_permission_is_confirmed() -> None:
    sources = list((REPO_ROOT / "apps" / "api" / "scripts").glob("*.py")) + list(
        (REPO_ROOT / "apps" / "api" / "app" / "dataset_manifest").glob("*.py")
    )
    offenders = [
        path.name
        for path in sources
        if "permission confirmed" in path.read_text(encoding="utf-8").lower()
    ]

    assert offenders == []
