"""Sous-titres francais des bandes-annonces en VO (news_story/vost.py)."""
import json
from types import SimpleNamespace

import pytest

from news_story import vost
from news_story.vost import Cue


def _message(payload, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop,
                           content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class TestLangue:
    def test_une_vf_n_est_pas_sous_titree(self):
        assert not vost.needs_subtitles("fr", [Cue(0, 1, "Bonjour")])
        assert vost.needs_subtitles("en", [Cue(0, 1, "Hello")])

    def test_sans_paroles_rien(self):
        assert not vost.needs_subtitles("en", [])


class TestTraduction:
    def test_requete(self):
        request = vost.build_translation_request(["Hello.", "Run!"], "claude-haiku-5-5")
        assert "tool_choice" not in request and "tools" not in request
        schema = request["output_config"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert "1. Hello.\n2. Run!" in request["messages"][0]["content"]

    def test_nombre_de_repliques_garde(self):
        assert vost.parse_translation(_message({"repliques": ["Bonjour.", "Cours !"]}), 2) == \
            ["Bonjour.", "Cours !"]
        with pytest.raises(vost.VostError):
            vost.parse_translation(_message({"repliques": ["Bonjour."]}), 2)

    def test_refus_et_troncature(self):
        for stop in ("refusal", "max_tokens"):
            with pytest.raises(vost.VostError):
                vost.parse_translation(_message({"repliques": ["x"]}, stop=stop), 1)

    def test_sans_cle_message_clair(self):
        with pytest.raises(vost.VostError, match="clé API Claude"):
            vost.translate(["Hello"])


class TestIncrustation:
    def test_replique_trop_longue_coupee_et_temps_partage(self):
        cue = Cue(10.0, 14.0, "Nous devons partir avant que la tempête n'arrive sur la ville "
                              "et que les routes soient coupées par la neige")
        parts = vost.split_cue(cue)
        assert len(parts) == 2
        assert parts[0].start == 10.0 and parts[-1].end == 14.0
        assert parts[0].end == parts[1].start
        assert " ".join(p.text for p in parts) == cue.text

    def test_ass_en_bas_de_la_video(self, tmp_path):
        path = vost.build_ass([Cue(0.5, 2.0, "Cours, {vite} !")], (0, 622, 1080, 608),
                              tmp_path / "s.ass")
        text = path.read_text(encoding="utf-8")
        assert "PlayResX: 1080" in text and "PlayResY: 1920" in text
        margin_v = 1920 - (622 + 608) + 22
        assert f",2,140,140,{margin_v},1" in text          # centre, en bas de la video
        assert "0:00:00.50,0:00:02.00" in text
        assert "Cours, \\{vite\\} !" in text                 # accolades echappees (balises ASS)

    def test_ffmpeg_brule_les_sous_titres_en_dernier(self):
        from news_story.video_composer import ffmpeg_args

        args = ffmpeg_args("in.mp4", "ov.png", "out.mp4", (0, 600, 1080, 608), "s.ass")
        graph = args[args.index("-filter_complex") + 1]
        assert graph.endswith("overlay=0:0,subtitles='s.ass',format=yuv420p[v]")
        plain = ffmpeg_args("in.mp4", "ov.png", "out.mp4", (0, 600, 1080, 608))
        assert "subtitles" not in plain[plain.index("-filter_complex") + 1]


class TestEnchainement:
    def test_vo_transcrite_traduite_coupee(self, monkeypatch):
        monkeypatch.setattr(vost, "transcribe_cues", lambda *a, **k: (
            "en", [Cue(0, 2, "Hello."), Cue(2, 4, "We need to go now.")]))
        monkeypatch.setattr(vost, "translate", lambda texts: ["Bonjour.", "Il faut partir."])
        statuses = []
        cues = vost.french_cues("v.mp4", on_status=statuses.append)
        assert [c.text for c in cues] == ["Bonjour.", "Il faut partir."]
        assert any("Traduction" in s for s in statuses)

    def test_vf_laissee_telle_quelle(self, monkeypatch):
        monkeypatch.setattr(vost, "transcribe_cues", lambda *a, **k: ("fr", [Cue(0, 2, "Salut.")]))
        monkeypatch.setattr(vost, "translate", lambda texts: pytest.fail("pas de traduction"))
        assert vost.french_cues("v.mp4") is None
