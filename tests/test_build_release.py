"""scripts/build_release.py sinovlari: arxiv yig'ish va --publish (GitHub Release: gh CLI yoki REST)."""
import email.message
import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
import zipfile

import pytest

from test_flow import PROJECT

TOKEN = "test-token-qwerty-12345"  # soxta; maxfiy andozalarga tushmaydi
REPO = "owner/name"
RELEASE_HTML = f"https://github.com/{REPO}/releases/tag/vX"
UPLOAD_TEMPLATE = "https://uploads.github.com/repos/owner/name/releases/501/assets{?name,label}"


def version() -> str:
    return (PROJECT / "VERSION").read_text().strip()


def zip_name() -> str:
    return f"claude-telegram-bot-v{version()}.zip"


def load_module(name: str = "build_release_under_test"):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "scripts" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def release(tmp_path, monkeypatch):
    """Skriptni modul sifatida yuklaydi; muhitdagi token/repo va o'rnatilgan `gh` ta'sirini o'chiradi."""
    mod = load_module()
    for var in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPO"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda name: None)  # gh yo'q → REST
    monkeypatch.setattr(mod, "iter_files", lambda: [PROJECT / "VERSION"])  # tez, boshqa fayllardan mustaqil
    notes = tmp_path / "notes.md"
    notes.write_text("Sinov izohi\n- birinchi qator\n", encoding="utf-8")
    return mod, notes


def run_main(mod, monkeypatch, tmp_path, notes, *extra: str) -> int:
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--out", str(tmp_path / "dist"), "--notes", str(notes), *extra])
    return mod.main()


# ----------------------------------------------------------------------------- soxta GitHub REST

class FakeResponse:
    def __init__(self, status: int, payload):
        self.status = status
        self._raw = b"" if payload is None else json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(url: str, code: int, payload: dict) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "error", email.message.Message(), io.BytesIO(json.dumps(payload).encode()))


class FakeGitHub:
    """urllib.request.urlopen o'rnida: so'rovlarni yozib boradi, tayyor javob qaytaradi."""

    def __init__(self, *, exists: bool = False, create_error: tuple[int, str] | None = None):
        self.exists = exists
        self.create_error = create_error
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.release = {"id": 501, "upload_url": UPLOAD_TEMPLATE, "html_url": RELEASE_HTML, "assets": []}

    def __call__(self, req, timeout=None):
        method, url = req.get_method(), req.full_url
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((method, url, headers, req.data))
        if url.startswith("https://uploads.github.com/"):
            return FakeResponse(201, {"id": 9, "name": "asset"})
        if method == "POST" and url.endswith("/releases"):
            if self.create_error:
                raise http_error(url, self.create_error[0], {"message": self.create_error[1]})
            if self.exists:
                raise http_error(url, 422, {"message": "Validation Failed", "errors": [{"code": "already_exists"}]})
            return FakeResponse(201, self.release)
        if method == "GET" and "/releases/tags/" in url:
            return FakeResponse(200, {**self.release, "assets": [{"id": 77, "name": zip_name()}, {"id": 78, "name": "boshqa.txt"}]})
        if method == "PATCH" and url.endswith("/releases/501"):
            return FakeResponse(200, self.release)
        if method == "DELETE" and "/releases/assets/" in url:
            return FakeResponse(204, None)
        raise AssertionError(f"kutilmagan so'rov: {method} {url}")

    def find(self, method: str, part: str):
        return [c for c in self.calls if c[0] == method and part in c[1]]


# ----------------------------------------------------------------------------- arxiv

def test_plain_build_still_works(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setattr(mod, "iter_files", load_module("build_release_real").iter_files)  # haqiqiy fayl ro'yxati
    assert run_main(mod, monkeypatch, tmp_path, notes) == 0
    target = tmp_path / "dist" / zip_name()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
    assert "claude-telegram-bot/VERSION" in names and "claude-telegram-bot/bot/app.py" in names
    assert "claude-telegram-bot/UPDATE_NOTES.md" in names
    assert not any("tests/" in n or "scripts/" in n for n in names)
    out = capsys.readouterr().out
    assert "✅" in out and "GitHub" not in out


def test_missing_notes_fails(release, tmp_path, monkeypatch, capsys):
    mod, _ = release
    assert run_main(mod, monkeypatch, tmp_path, tmp_path / "yoq.md") == 1
    assert "topilmadi" in capsys.readouterr().out


# ----------------------------------------------------------------------------- REST

def test_publish_rest_creates_release(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_REPO", REPO)  # repo muhitdan
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    gh = FakeGitHub()
    monkeypatch.setattr(mod.urllib.request, "urlopen", gh)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--branch", "main") == 0
    tag = f"v{version()}"

    (create,) = gh.find("POST", "api.github.com")
    assert create[1] == f"https://api.github.com/repos/{REPO}/releases"
    payload = json.loads(create[3])
    assert payload == {"tag_name": tag, "name": tag, "body": notes.read_text(encoding="utf-8"), "target_commitish": "main"}
    assert create[2]["authorization"] == f"Bearer {TOKEN}"
    assert create[2]["accept"] == "application/vnd.github+json"
    assert create[2]["x-github-api-version"] == "2022-11-28"

    (upload,) = gh.find("POST", "uploads.github.com")
    assert upload[1] == f"https://uploads.github.com/repos/owner/name/releases/501/assets?name={zip_name()}"
    assert upload[2]["content-type"] == "application/zip"
    assert upload[3] == (tmp_path / "dist" / zip_name()).read_bytes()
    assert len(gh.calls) == 2

    out = capsys.readouterr()
    assert f"✅ GitHub Release: {RELEASE_HTML}" in out.out
    assert TOKEN not in out.out + out.err


def test_publish_rest_updates_existing_release(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    gh = FakeGitHub(exists=True)
    monkeypatch.setattr(mod.urllib.request, "urlopen", gh)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 0
    tag = f"v{version()}"

    assert [c[0] for c in gh.calls] == ["POST", "GET", "PATCH", "DELETE", "POST"]
    (lookup,) = gh.find("GET", "/releases/tags/")
    assert lookup[1].endswith(f"/repos/{REPO}/releases/tags/{tag}")
    (patch,) = gh.find("PATCH", "/releases/501")
    assert json.loads(patch[3]) == {"name": tag, "body": notes.read_text(encoding="utf-8")}
    (delete,) = gh.find("DELETE", "/releases/assets/")
    assert delete[1].endswith("/releases/assets/77")  # faqat bir xil nomli aktiv o'chiriladi
    (upload,) = gh.find("POST", "uploads.github.com")
    assert upload[1].endswith(f"?name={zip_name()}") and upload[2]["content-type"] == "application/zip"
    out = capsys.readouterr().out
    assert RELEASE_HTML in out and TOKEN not in out


def test_publish_rest_reports_api_error(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    gh = FakeGitHub(create_error=(401, "Bad credentials"))
    monkeypatch.setattr(mod.urllib.request, "urlopen", gh)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 1
    out = capsys.readouterr().out
    assert "401" in out and "Bad credentials" in out and TOKEN not in out
    assert len(gh.calls) == 1


def test_publish_rest_network_error(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)

    def down(req, timeout=None):
        raise urllib.error.URLError("ulanish yo'q")

    monkeypatch.setattr(mod.urllib.request, "urlopen", down)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 1
    out = capsys.readouterr().out
    assert "ulanib bo'lmadi" in out and TOKEN not in out


def test_publish_without_token_fails(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    calls = []
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: calls.append(a))
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 1
    out = capsys.readouterr().out
    assert "GITHUB_TOKEN" in out and "gh auth login" in out
    assert calls == []


# ----------------------------------------------------------------------------- gh CLI

class FakeGh:
    """subprocess.run o'rnida: gh buyruqlarini yozib boradi."""

    def __init__(self, *, exists: bool = False, fail: str | None = None):
        self.exists = exists
        self.fail = fail  # shu kichik buyruq (create/upload/edit) xato bilan tugaydi
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        assert kwargs.get("check") is False and kwargs.get("capture_output") and kwargs.get("text")
        self.calls.append(list(argv))
        assert argv[0] == "gh"
        sub = argv[2] if len(argv) > 2 else argv[1]
        if argv[1] == "auth":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if sub == "view":
            return subprocess.CompletedProcess(argv, 0 if self.exists else 1, "", "release not found")
        if sub == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "HTTP 403: Resource not accessible")
        return subprocess.CompletedProcess(argv, 0, RELEASE_HTML + "\n", "")

    def sub(self, name: str) -> list[list[str]]:
        return [c for c in self.calls if c[1:3] == ["release", name]]


@pytest.fixture
def gh_cli(release, monkeypatch):
    """`gh` o'rnatilgan deb hisoblanadi (subprocess.run esa har testda FakeGh bilan almashtiriladi)."""
    mod, _ = release
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/gh" if name == "gh" else None)
    return mod


def test_publish_gh_creates_release(release, gh_cli, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)  # gh bo'lsa REST ishlatilmaydi
    fake = FakeGh()
    monkeypatch.setattr(mod.subprocess, "run", fake)
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("REST chaqirilmasligi kerak"))
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO, "--branch", "main") == 0
    tag = f"v{version()}"
    assert fake.calls[0] == ["gh", "auth", "status"]
    assert fake.calls[1] == ["gh", "release", "view", tag, "--repo", REPO]
    (create,) = fake.sub("create")
    assert create == ["gh", "release", "create", tag, str(tmp_path / "dist" / zip_name()), "--repo", REPO,
                      "--title", tag, "--notes-file", str(notes), "--target", "main"]
    assert not fake.sub("upload") and not fake.sub("edit")
    out = capsys.readouterr().out
    assert f"✅ GitHub Release: https://github.com/{REPO}/releases/tag/{tag}" in out and TOKEN not in out


def test_publish_gh_updates_existing_release(release, gh_cli, tmp_path, monkeypatch):
    mod, notes = release
    fake = FakeGh(exists=True)
    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 0
    tag = f"v{version()}"
    assert not fake.sub("create")
    (upload,) = fake.sub("upload")
    assert upload == ["gh", "release", "upload", tag, str(tmp_path / "dist" / zip_name()), "--repo", REPO, "--clobber"]
    (edit,) = fake.sub("edit")
    assert edit[:5] == ["gh", "release", "edit", tag, "--repo"] and edit[-2:] == ["--notes-file", str(notes)]


def test_publish_gh_failure_shows_stderr(release, gh_cli, tmp_path, monkeypatch, capsys):
    mod, notes = release
    fake = FakeGh(fail="create")
    monkeypatch.setattr(mod.subprocess, "run", fake)
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish", "--repo", REPO) == 1
    out = capsys.readouterr().out
    assert "❌" in out and "HTTP 403" in out


# ----------------------------------------------------------------------------- repo aniqlash

@pytest.mark.parametrize("remote", [
    "https://github.com/owner/name.git",
    "https://github.com/owner/name",
    "git@github.com:owner/name.git",
    "git@github.com:owner/name",
    "ssh://git@github.com/owner/name.git",
])
def test_resolve_repo_from_git_remote(release, monkeypatch, remote):
    mod, _ = release

    def fake_git(argv, **kwargs):
        assert argv[:2] == ["git", "-C"] and argv[3:] == ["remote", "get-url", "origin"]
        return subprocess.CompletedProcess(argv, 0, remote + "\n", "")

    monkeypatch.setattr(mod.subprocess, "run", fake_git)
    assert mod._resolve_repo(None) == "owner/name"


def test_resolve_repo_precedence(release, monkeypatch):
    mod, _ = release
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: pytest.fail("git chaqirilmasligi kerak"))
    monkeypatch.setenv("GITHUB_REPO", "env-owner/env-name")
    assert mod._resolve_repo(None) == "env-owner/env-name"
    assert mod._resolve_repo("arg-owner/arg-name") == "arg-owner/arg-name"  # --repo ustun
    assert mod._resolve_repo("https://github.com/u/r.git") == "u/r"  # URL ham qabul qilinadi


def test_resolve_repo_missing(release, monkeypatch):
    mod, _ = release
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 128, "", "fatal: No such remote"))
    assert mod._resolve_repo(None) is None
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "https://gitlab.com/o/n.git\n", ""))
    assert mod._resolve_repo(None) is None  # GitHub bo'lmagan remote


def test_publish_without_repo_fails(release, tmp_path, monkeypatch, capsys):
    mod, notes = release
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda argv, **kw: subprocess.CompletedProcess(argv, 128, "", "fatal: No such remote"))
    monkeypatch.setattr(mod.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("REST chaqirilmasligi kerak"))
    assert run_main(mod, monkeypatch, tmp_path, notes, "--publish") == 1
    out = capsys.readouterr().out
    assert "GITHUB_REPO" in out and "--repo" in out and TOKEN not in out
    assert (tmp_path / "dist" / zip_name()).exists()  # arxiv baribir yig'ilgan
