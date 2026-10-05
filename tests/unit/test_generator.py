"""Pruebas del generador de configuracion (determinismo y escritura atomica)."""

from __future__ import annotations

from dog_matrix.capabilities import Capabilities, find_profile
from installer.generator import ConfigGenerator


def _profile(name="box_turtle"):
    capabilities = Capabilities(str(find_profile(name)))
    return capabilities.load()


def test_bundle_contains_expected_files():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile())
    assert "dog_matrix_generated.cfg" in bundle.files
    assert "dog_matrix_macros.cfg" in bundle.files
    assert "dog_matrix_profile.json" in bundle.files
    assert bundle.config_hash.startswith("sha256:")


def test_generation_is_deterministic():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle_a = generator.build_bundle(_profile())
    bundle_b = generator.build_bundle(_profile())
    assert bundle_a.config_hash == bundle_b.config_hash
    assert bundle_a.files == bundle_b.files


def test_hash_embedded_matches():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile())
    content = bundle.files["dog_matrix_generated.cfg"]
    assert "__CONFIG_HASH__" not in content
    assert "sha256:" in content


def test_write_atomic_bundle(tmp_path):
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile())
    assert generator.write_atomic_config_bundle(bundle, str(tmp_path))
    for name in bundle.files:
        assert (tmp_path / name).exists()
    assert (tmp_path / "dog_matrix_manifest.json").exists()


def test_no_unresolved_placeholders():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile())
    for name, content in bundle.files.items():
        assert "${" not in content, f"placeholder sin resolver en {name}"


def test_macros_contain_core_commands():
    generator = ConfigGenerator(generated_at="2026-01-01T00:00:00Z")
    bundle = generator.build_bundle(_profile())
    macros = bundle.files["dog_matrix_macros.cfg"]
    for command in ("DM_STATUS", "DM_CHANGE", "DM_RECOVER"):
        assert command in macros


def test_moonraker_extension_snippet():
    generator = ConfigGenerator()
    snippet = generator.render_moonraker_extension({"update_spoolman_location": False})
    assert "[dog_matrix]" in snippet
    assert "update_spoolman_location: false" in snippet
