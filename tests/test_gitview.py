from pr_swipe import gitview as GV
from tests.gitrepo import make_origin, git


def mirror_of(tmp, o):
    m = tmp / "mirror.git"
    git(tmp, "clone", "-q", "--mirror", str(o["origin"]), str(m))
    return m


def test_commits_files_diff_and_show(tmp_path):
    o = make_origin(tmp_path, files={"app.py": "print('hi')\n", "lib/util.py": "x = 1\n"})
    m = mirror_of(tmp_path, o)
    v = GV.GitView(m)
    assert [c["subject"] for c in v.commits(o["base"], o["head"])] == ["feat: change"]
    files = {f["path"]: f for f in v.files(o["base"], o["head"])}
    assert set(files) == {"app.py", "lib/util.py"}
    assert files["app.py"]["additions"] == 1 and files["app.py"]["status"] == "A"
    assert "+print('hi')" in v.file_diff(o["base"], o["head"], "app.py")
    assert v.show(o["head"], "lib/util.py") == "x = 1\n"
    assert v.merge_base(o["base"], o["head"]) == o["base"]


def test_attributes_in_the_pr_cannot_run_a_diff_driver(tmp_path):
    marker = tmp_path / "pwned"
    o = make_origin(tmp_path, files={".gitattributes": "*.py diff=evil\n", "a.py": "1\n"})
    m = mirror_of(tmp_path, o)
    # even if the mirror's own config defined the driver, --no-ext-diff/--no-textconv must ignore it
    # a bare mirror ignores the tree's .gitattributes; info/attributes is the one place it would apply
    (m / "info").mkdir(exist_ok=True)
    (m / "info" / "attributes").write_text("*.py diff=evil\n")
    git(m, "config", "diff.evil.command", f"touch {marker}")
    git(m, "config", "diff.evil.textconv", f"sh -c 'touch {marker}; cat'")
    v = GV.GitView(m)
    v.file_diff(o["base"], o["head"], "a.py")
    v.files(o["base"], o["head"])
    assert not marker.exists()


def test_rejects_non_sha_revisions_and_odd_paths(tmp_path):
    o = make_origin(tmp_path)
    v = GV.GitView(mirror_of(tmp_path, o))
    import pytest
    with pytest.raises(ValueError):
        v.commits("--output=/tmp/x", o["head"])
    with pytest.raises(ValueError):
        v.file_diff(o["base"], o["head"], "-c")
