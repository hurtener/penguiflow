from pathlib import Path

import learning_control_plane


def test_learning_control_plane_package_is_importable() -> None:
    assert learning_control_plane.__doc__


def test_learning_control_plane_source_is_grouped_by_responsibility() -> None:
    package_root = Path(learning_control_plane.__file__).parent
    expected_paths = [
        "README.md",
        "FULL_LOOP_RUN.md",
        "contracts/evidence.py",
        "contracts/investigation.py",
        "control_plane/control_plane.py",
        "evaluation/evaluation.py",
        "mining/mining.py",
        "providers/investigation_publisher.py",
        "integrations/penguiflow/projector.py",
        "docs/architecture.md",
    ]
    assert all((package_root / path).is_file() for path in expected_paths)


def test_learning_control_plane_root_has_no_compatibility_shims() -> None:
    package_root = Path(learning_control_plane.__file__).parent
    root_modules = {path.name for path in package_root.glob("*.py")}
    assert root_modules == {"__init__.py"}
