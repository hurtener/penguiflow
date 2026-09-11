from pathlib import Path

import learning_control_plane


def test_learning_control_plane_package_is_importable() -> None:
    assert learning_control_plane.__doc__


def test_milestone_zero_structure_is_present() -> None:
    package_root = Path(learning_control_plane.__file__).parent
    expected_paths = [
        "README.md",
        "FULL_LOOP_RUN.md",
        "architecture.md",
        "evidence.py",
    ]
    assert all((package_root / path).is_file() for path in expected_paths)
