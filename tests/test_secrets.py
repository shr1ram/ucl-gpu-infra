"""Tests for the shared secret loader. No real secrets — uses tmp files."""
import os

import pytest

from ucl_gpu_infra import secrets as S


def test_parse_env_file_handles_comments_quotes_export():
    text = (
        "# a comment\n"
        "\n"
        "DEFAULT_API_BASE_URL=https://x/v1\n"
        "export CUSTOM_API_KEY='sk-123'\n"
        'DEFAULT_LLM_MODEL="my-model"\n'
        "EMPTY=\n"
        "no_equals_line\n"
    )
    kv = S.parse_env_file(text)
    assert kv["DEFAULT_API_BASE_URL"] == "https://x/v1"
    assert kv["CUSTOM_API_KEY"] == "sk-123"          # quotes + export stripped
    assert kv["DEFAULT_LLM_MODEL"] == "my-model"
    assert kv["EMPTY"] == ""
    assert "no_equals_line" not in kv


def test_secret_path_resolution(monkeypatch, tmp_path):
    # explicit arg wins
    assert S.secret_path(str(tmp_path / "a.env")) == tmp_path / "a.env"
    # then env var
    monkeypatch.setenv("UCL_GPU_INFRA_SECRETS", str(tmp_path / "b.env"))
    assert S.secret_path() == tmp_path / "b.env"
    # then default
    monkeypatch.delenv("UCL_GPU_INFRA_SECRETS", raising=False)
    assert str(S.secret_path()).endswith("ucl-gpu-infra/secrets.env")


def test_load_sets_unset_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("DEFAULT_API_BASE_URL", raising=False)
    f = tmp_path / "s.env"
    f.write_text("DEFAULT_API_BASE_URL=https://proxy/v1\nCUSTOM_API_KEY=k1\n")
    rep = S.load_secrets(str(f))
    assert rep["exists"] is True
    assert set(rep["set"]) == {"DEFAULT_API_BASE_URL", "CUSTOM_API_KEY"}
    assert os.environ["DEFAULT_API_BASE_URL"] == "https://proxy/v1"


def test_load_does_not_override_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_API_KEY", "already-set")
    f = tmp_path / "s.env"
    f.write_text("CUSTOM_API_KEY=from-file\n")
    rep = S.load_secrets(str(f))
    assert "CUSTOM_API_KEY" in rep["skipped"]
    assert os.environ["CUSTOM_API_KEY"] == "already-set"  # env wins


def test_load_override_true(monkeypatch, tmp_path):
    monkeypatch.setenv("CUSTOM_API_KEY", "old")
    f = tmp_path / "s.env"
    f.write_text("CUSTOM_API_KEY=new\n")
    S.load_secrets(str(f), override=True)
    assert os.environ["CUSTOM_API_KEY"] == "new"


def test_empty_values_reported_not_set(monkeypatch, tmp_path):
    monkeypatch.delenv("INFRA_SESSION_KEY", raising=False)
    f = tmp_path / "s.env"
    f.write_text("INFRA_SESSION_KEY=\n")
    rep = S.load_secrets(str(f))
    assert "INFRA_SESSION_KEY" in rep["empty"]
    assert os.environ.get("INFRA_SESSION_KEY", "") == ""


def test_missing_file_is_not_an_error(tmp_path):
    rep = S.load_secrets(str(tmp_path / "nope.env"))
    assert rep["exists"] is False
    assert rep["set"] == []


def test_load_secrets_exported_from_package():
    import ucl_gpu_infra
    assert hasattr(ucl_gpu_infra, "load_secrets")
    assert hasattr(ucl_gpu_infra, "secret_path")
