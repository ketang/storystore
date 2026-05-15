import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-codex-plugin"


def create_source_tree(root: Path) -> None:
    codex_manifest_dir = root / ".codex-plugin"
    codex_manifest_dir.mkdir(parents=True)
    (codex_manifest_dir / "plugin.json").write_text(
        json.dumps(
            {
                "name": "storystore",
                "version": "1.0.0",
                "description": "Test plugin",
                "skills": "./.codex-plugin/skills",
            }
        )
        + "\n"
    )
    codex_skill_dir = codex_manifest_dir / "skills" / "stories-init"
    codex_skill_dir.mkdir(parents=True)
    (codex_skill_dir / "SKILL.md").write_text("---\nname: stories-init\n---\nCodex skill\n")

    (root / "plugin-version.json").write_text('{"version": "1.0.0"}\n')
    (root / "README.md").write_text("# storystore\n")


def run_installer(*args: str) -> subprocess.CompletedProcess[str]:
    command = ["bash", str(SCRIPT), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def test_help_flag_succeeds() -> None:
    result = run_installer("--help")

    assert result.returncode == 0
    assert SCRIPT.stat().st_mode & 0o111, "scripts/install-codex-plugin must be executable"
    assert "Install the Storystore Codex plugin" in result.stdout
    assert "--marketplace-root" in result.stdout


def test_dry_run_does_not_write_marketplace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    create_source_tree(source)
    marketplace = tmp_path / "marketplace"

    result = run_installer(
        "--source",
        str(source),
        "--marketplace-root",
        str(marketplace),
        "--skip-build",
        "--skip-register",
        "--dry-run",
    )

    assert result.returncode == 0, result.stderr
    assert "Would install Storystore Codex plugin" in result.stdout
    assert not marketplace.exists()


def test_policy_flags_validate_their_own_enums(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    create_source_tree(source)

    result = run_installer(
        "--source",
        str(source),
        "--skip-register",
        "--skip-build",
        "--install-policy",
        "ON_INSTALL",
    )

    assert result.returncode != 0
    assert "invalid --install-policy" in result.stderr


def test_installer_writes_local_marketplace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    create_source_tree(source)
    marketplace = tmp_path / "marketplace"

    result = run_installer(
        "--source",
        str(source),
        "--marketplace-root",
        str(marketplace),
        "--skip-build",
        "--skip-register",
        "--verbose",
    )

    assert result.returncode == 0, result.stderr

    plugin_root = marketplace / "plugins" / "storystore"
    assert (plugin_root / ".codex-plugin" / "plugin.json").is_file()
    assert (plugin_root / ".codex-plugin" / "skills" / "stories-init" / "SKILL.md").is_file()
    assert (plugin_root / "plugin-version.json").is_file()
    assert (plugin_root / "README.md").is_file()

    manifest_path = marketplace / ".agents" / "plugins" / "marketplace.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["name"] == "storystore"
    assert manifest["interface"]["displayName"] == "Storystore"

    plugin_entry = manifest["plugins"][0]
    assert plugin_entry["name"] == "storystore"
    assert plugin_entry["source"] == {
        "source": "local",
        "path": "./plugins/storystore",
    }
    assert plugin_entry["policy"] == {
        "installation": "INSTALLED_BY_DEFAULT",
        "authentication": "ON_INSTALL",
    }
    assert plugin_entry["category"] == "Documentation"


def test_installer_enables_plugin_when_registered(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    create_source_tree(source)
    marketplace = tmp_path / "marketplace"
    codex_home = tmp_path / "codex-home"

    result = run_installer(
        "--source",
        str(source),
        "--codex-home",
        str(codex_home),
        "--marketplace-root",
        str(marketplace),
        "--skip-build",
        "--codex",
        "true",
    )

    assert result.returncode == 0, result.stderr
    config = (codex_home / "config.toml").read_text()
    assert '[plugins."storystore@storystore"]' in config
    assert "enabled = true" in config
