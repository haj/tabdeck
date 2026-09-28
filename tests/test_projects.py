import json
import subprocess

from tabdeck.projects import all_projects, clone, load_remotes, project_remotes, valid_name


def test_valid_name():
    assert valid_name("Atlas") and valid_name("learning_app-kids.v2")
    for bad in ["", "../etc", "a/b", ".hidden", "my app", "x;rm"]:
        assert not valid_name(bad), bad


def test_all_projects_merges_local_and_remote(tmp_path):
    (tmp_path / "Atlas").mkdir()
    assert all_projects(tmp_path, {"Beacon": "git@x:k.git", "Atlas": "git@x:f.git"}) == ["Atlas", "Beacon"]


def test_load_remotes_ignores_bad_entries(tmp_path):
    f = tmp_path / "projects.json"
    f.write_text(json.dumps({"Atlas": "git@github.com:h/Atlas.git", "../x": "git@a:b.git", "Bad": 3}))
    assert load_remotes(f) == {"Atlas": "git@github.com:h/Atlas.git"}
    assert load_remotes(tmp_path / "missing.json") == {}


def test_project_remotes_reads_origin(tmp_path):
    repo = tmp_path / "Atlas"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:h/Atlas.git"], check=True)
    (tmp_path / "notes").mkdir()
    assert project_remotes(tmp_path) == {"Atlas": "git@github.com:h/Atlas.git"}


async def test_clone_reports_failure(tmp_path):
    async def fail(*args):
        return 128, "Permission denied (publickey)."
    assert await clone("git@github.com:h/F.git", tmp_path / "F", run=fail) == "Permission denied (publickey)."
    async def ok(*args):
        return 0, ""
    assert await clone("git@github.com:h/F.git", tmp_path / "F", run=ok) is None


async def test_clone_timeout_removes_partial_folder(tmp_path):
    import asyncio
    dest = tmp_path / "F"

    async def slow(*args):
        dest.mkdir()
        (dest / "partial").write_text("x")
        await asyncio.sleep(5)
        return 0, ""
    assert await clone("git@github.com:h/F.git", dest, run=slow, timeout=0.2) == "the clone took too long"
    assert not dest.exists()


def test_project_remotes_strip_credentials(tmp_path):
    repo = tmp_path / "Secret"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://user:tok123@gitlab.com/h/s.git"], check=True)
    assert project_remotes(tmp_path) == {"Secret": "https://gitlab.com/h/s.git"}


def test_git_runs_non_interactively_and_accepts_new_host_keys():
    from tabdeck.projects import GIT_ENV
    assert GIT_ENV["GIT_TERMINAL_PROMPT"] == "0"
    assert "StrictHostKeyChecking=accept-new" in GIT_ENV["GIT_SSH_COMMAND"] and "BatchMode=yes" in GIT_ENV["GIT_SSH_COMMAND"]
