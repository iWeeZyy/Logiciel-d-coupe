"""Memoire des clips publies ecrite par deux workflows a la fois (2026-11-11)."""
import json

from radar.memory_merge import main, merge


def test_les_deux_ajouts_sont_gardes():
    base = [{"id": "a", "date": "2026-11-10"}]
    remote = {"published": base + [{"id": "midi", "date": "2026-11-11"}]}
    local = {"published": base + [{"id": "top1", "date": "2026-11-11"},
                                  {"id": "top2", "date": "2026-11-11"}]}
    ids = [e["id"] for e in merge(remote, local)["published"]]
    assert ids == ["a", "midi", "top1", "top2"]


def test_rien_n_est_double():
    same = {"published": [{"id": "a", "date": "2026-11-10"}]}
    assert merge(same, same) == same


def test_commande(tmp_path):
    r, l, o = tmp_path / "r.json", tmp_path / "l.json", tmp_path / "o.json"
    r.write_text(json.dumps({"published": [{"id": "x", "date": "d"}]}))
    l.write_text(json.dumps({"published": [{"id": "y", "date": "d"}]}))
    assert main([str(r), str(l), str(o)]) == 0
    assert [e["id"] for e in json.loads(o.read_text())["published"]] == ["x", "y"]
