# @Test suite for source file discovery with gitignore support, TEST_DISC_1, test, [IMPL_DISC_1]
import json
from pathlib import Path
import subprocess

import pytest

from sphinx_codelinks.source_discover.config import (
    COMMENT_FILETYPE,
    TS_DEFAULT_EXCLUDE,
    SourceDiscoverConfig,
    SourceDiscoverConfigType,
)
from sphinx_codelinks.source_discover.source_discover import SourceDiscover

FIXTURES_PATH = Path(__file__).parent / "data" / "discover_fixtures.json"


@pytest.mark.parametrize(
    ("config", "msgs"),
    [
        (
            {
                "src_dir": 123,
                "exclude": ["exclude1", "exclude2"],
                "include": ["include1", "include2"],
                "gitignore": True,
                "comment_type": "cpp",
            },
            ["Schema validation error in field 'src_dir': 123 is not of type 'string'"],
        ),
        (
            {
                "src_dir": "/path/to/root",
                "exclude": ["exclude1", "exclude2"],
                "include": ["include1", "include2"],
                "gitignore": "TrueAsString",
                "comment_type": "cpp",
            },
            [
                "Schema validation error in field 'gitignore': 'TrueAsString' is not of type 'boolean'"
            ],
        ),
        (
            {
                "src_dir": "/path/to/root",
                "exclude": ["exclude1", "exclude2"],
                "include": ["include1", "include2"],
                "gitignore": True,
                "comment_type": "java",
            },
            [
                "Schema validation error in field 'comment_type': 'java' is not one of ['bash', 'cpp', 'cs', 'go', 'jsonc', 'python', 'rust', 'ts', 'yaml']"
            ],
        ),
        (
            {
                "src_dir": "/path/to/root",
                "exclude": ["exclude1", "exclude2"],
                "include": ["include1", "include2"],
                "gitignore": True,
                "comment_type": ["cpp", "hpp"],
            },
            [
                "Schema validation error in field 'comment_type': ['cpp', 'hpp'] is not of type 'string'"
            ],
        ),
        (
            {
                "src_dir": "/path/to/root",
                "follow_links": "not_a_bool",
            },
            [
                "Schema validation error in field 'follow_links': 'not_a_bool' is not of type 'boolean'"
            ],
        ),
    ],
)
def test_schema_negative(config, msgs):
    source_discover_config = SourceDiscoverConfig(**config)
    errors = source_discover_config.check_schema()
    assert sorted(errors) == sorted(msgs)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {
            "src_dir": "/path/to/root",
            "exclude": ["exclude1", "exclude2"],
            "include": ["include1", "include2"],
            "gitignore": True,
            "comment_type": "cpp",
        },
        {
            "src_dir": "/path/to/root",
            "exclude": ["exclude1", "exclude2"],
            "include": ["include1", "include2"],
            "gitignore": True,
            "comment_type": "python",
        },
        {
            "src_dir": "/path/to/root",
            "exclude": ["exclude1", "exclude2"],
            "include": ["include1", "include2"],
            "gitignore": True,
            "comment_type": "ts",
        },
        {
            "src_dir": "/path/to/root",
            "follow_links": True,
        },
    ],
)
def test_schema_positive(config):
    source_discover_config = SourceDiscoverConfig(**config)
    errors = source_discover_config.check_schema()
    assert len(errors) == 0


@pytest.mark.parametrize(
    ("config", "num_files", "suffix"),
    [
        (
            {
                "gitignore": False,
            },
            4,
            "",
        ),
        (
            {
                "gitignore": True,
            },
            3,
            "",
        ),
        (
            {
                "gitignore": True,
                "exclude": ["charge/*.cpp"],
                "include": ["**/*.cpp"],
            },
            # With ignore-python, include patterns whitelist files (overriding
            # gitignore) and exclude patterns are applied after, so both
            # charge/*.cpp files are excluded resulting in 2 instead of 4.
            2,
            "",
        ),
        (
            {
                "gitignore": True,
                "exclude": ["charge/*.cpp"],
            },
            2,
            "",
        ),
        (
            {"gitignore": False, "comment_type": "cpp"},
            4,
            "cpp",
        ),
    ],
)
def test_source_discover(
    config: SourceDiscoverConfigType,
    num_files: int,
    suffix: str,
    source_directory: Path,
) -> None:
    config["src_dir"] = source_directory
    src_discover_config = SourceDiscoverConfig(**config)
    source_discover = SourceDiscover(src_discover_config)
    assert len(source_discover.source_paths) == num_files
    if suffix:
        assert all(path.suffix == ".cpp" for path in source_discover.source_paths)


@pytest.fixture(scope="function")
def create_source_files(tmp_path: Path) -> Path:
    for file_types in COMMENT_FILETYPE.values():
        for ext in file_types:
            (tmp_path / f"file.{ext}").touch()
    return tmp_path


@pytest.mark.parametrize(
    ("comment_type", "nums_files"),
    [
        ("cpp", len(COMMENT_FILETYPE["cpp"])),
        ("python", len(COMMENT_FILETYPE["python"])),
        ("ts", len(COMMENT_FILETYPE["ts"])),
        ("bash", len(COMMENT_FILETYPE["bash"])),
    ],
)
def test_comment_filetype(
    comment_type: str, nums_files: int, create_source_files: Path
) -> None:
    src_dir = create_source_files

    config = SourceDiscoverConfig(
        src_dir=src_dir, comment_type=comment_type, gitignore=False
    )
    source_discover = SourceDiscover(config)
    assert len(source_discover.source_paths) == nums_files


def test_jsonc_discover_gate() -> None:
    """`.jsonc` is always discovered; `.json` only when it opens with a comment."""
    jsonc_dir = Path(__file__).parent / "data" / "jsonc"
    config = SourceDiscoverConfig(
        src_dir=jsonc_dir, comment_type="jsonc", gitignore=False
    )
    discovered = {p.name for p in SourceDiscover(config).source_paths}
    assert "demo.jsonc" in discovered
    assert "with_modeline.json" in discovered
    assert "plain.json" not in discovered


def _make_generated_output_tree(tmp_path: Path) -> Path:
    """Lay out a source file alongside checked-in generated output.

    Mirrors a ``tsc``/bundler output tree: ``src/app.ts`` is the real source,
    while ``lib/app.js``, ``dist/app.js`` and ``node_modules/pkg/index.js``
    stand in for generated or vendored output that carries the same marker.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(
        "// @Feature A, IMPL_1, impl\n", encoding="utf-8"
    )
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "app.js").write_text(
        "// @Feature A, IMPL_1, impl\n", encoding="utf-8"
    )
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app.js").write_text(
        "// @Feature A, IMPL_1, impl\n", encoding="utf-8"
    )
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text(
        "// vendored\n", encoding="utf-8"
    )
    return tmp_path


def test_default_exclude_skips_generated_output(tmp_path: Path) -> None:
    """The ``ts``-derived default ``exclude`` keeps generated/vendored JS out
    of discovery, while ``lib/`` — deliberately not in ``TS_DEFAULT_EXCLUDE``
    — is still discovered (see the constant's docstring for why)."""
    src_dir = _make_generated_output_tree(tmp_path)
    config = SourceDiscoverConfig(src_dir=src_dir, comment_type="ts", gitignore=False)
    assert config.exclude == TS_DEFAULT_EXCLUDE

    discover = SourceDiscover(config)
    discovered = sorted(str(p.relative_to(src_dir)) for p in discover.source_paths)
    assert discovered == [
        str(Path("lib") / "app.js"),
        str(Path("src") / "app.ts"),
    ]


def test_cpp_project_default_exclude_is_empty_and_finds_lib_marker(
    tmp_path: Path,
) -> None:
    """Regression guard for the D1 defect: the ``ts``-family default exclude
    must not leak to other ``comment_type`` values. ``cpp`` projects very
    commonly keep hand-written library source under ``lib/`` — unlike a
    ``tsc``/bundler ``lib/`` output dir, it must still be discovered."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir()
    (lib_dir / "widget.cpp").write_text(
        "// @Feature A, IMPL_1, impl\n", encoding="utf-8"
    )

    config = SourceDiscoverConfig(src_dir=tmp_path, comment_type="cpp", gitignore=False)
    assert config.exclude == []

    discover = SourceDiscover(config)
    discovered = sorted(str(p.relative_to(tmp_path)) for p in discover.source_paths)
    assert discovered == [str(Path("lib") / "widget.cpp")]


def test_explicit_exclude_replaces_default(tmp_path: Path) -> None:
    """An explicit ``exclude`` (even ``[]``) fully replaces the default list."""
    src_dir = _make_generated_output_tree(tmp_path)
    config = SourceDiscoverConfig(
        src_dir=src_dir, comment_type="ts", gitignore=False, exclude=[]
    )
    assert config.exclude == []

    discover = SourceDiscover(config)
    discovered = sorted(str(p.relative_to(src_dir)) for p in discover.source_paths)
    assert discovered == [
        str(Path("dist") / "app.js"),
        str(Path("lib") / "app.js"),
        str(Path("node_modules") / "pkg" / "index.js"),
        str(Path("src") / "app.ts"),
    ]


def test_follow_links(tmp_path: Path) -> None:
    """Test that follow_links controls whether symbolic links are followed."""
    # Create a real directory with a source file
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "source.cpp").write_text("// test")

    # Create a project directory with a symlink to the real directory
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "direct.cpp").write_text("// direct")
    link = project_dir / "linked"
    link.symlink_to(real_dir)

    # Without follow_links, symlinked files should not be discovered
    config_no_follow = SourceDiscoverConfig(
        src_dir=project_dir, gitignore=False, follow_links=False
    )
    discover_no_follow = SourceDiscover(config_no_follow)
    discovered_names = {p.name for p in discover_no_follow.source_paths}
    assert "direct.cpp" in discovered_names
    assert "source.cpp" not in discovered_names

    # With follow_links, symlinked files should be discovered
    config_follow = SourceDiscoverConfig(
        src_dir=project_dir, gitignore=False, follow_links=True
    )
    discover_follow = SourceDiscover(config_follow)
    discovered_names = {p.name for p in discover_follow.source_paths}
    assert "direct.cpp" in discovered_names
    assert "source.cpp" in discovered_names


def _load_discover_fixtures() -> list[dict]:
    with FIXTURES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize(
    "case",
    _load_discover_fixtures(),
    ids=lambda c: c["name"],
)
def test_discover_fixture(case: dict, tmp_path: Path) -> None:
    """Run portable discovery test cases from the shared JSON fixture."""
    # Create files
    for rel_path, content in case["files"].items():
        file_path = tmp_path / rel_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")

    # Optionally initialise a git repo (required for .gitignore support)
    if case.get("git_init", False):
        subprocess.run(
            ["git", "init"],  # noqa: S607
            cwd=str(tmp_path),
            check=True,
            capture_output=True,
        )

    cfg = case["config"]
    src_dir = tmp_path / cfg["src_dir"]

    config = SourceDiscoverConfig(
        src_dir=src_dir,
        include=cfg.get("include", []),
        exclude=cfg.get("exclude", []),
        gitignore=cfg.get("gitignore", True),
        comment_type=cfg.get("comment_type", "cpp"),
    )

    discover = SourceDiscover(config)

    # Convert discovered paths to paths relative to tmp_path for comparison
    discovered_relative = sorted(
        str(p.relative_to(tmp_path)) for p in discover.source_paths
    )

    # Normalise expected paths to use the OS path separator
    expected = sorted(str(Path(p)) for p in case["expected"])

    assert discovered_relative == expected, (
        f"Case '{case['name']}': expected {expected}, got {discovered_relative}"
    )
