"""En-tete des bandes-annonces ActuaFilms (news_story/trailer_header.py) :
badge neon « BANDE-ANNONCE », « NOUVEAU » seulement pour un film pas encore
sorti, date mise en valeur, animation incrustee sous l'intro."""
from datetime import date

import pytest

from news_story import trailer_header as th

TODAY = date(2026, 11, 1)


@pytest.mark.parametrize("line, expected", [
    ("Au cinéma depuis le 21 octobre 2026", ("Au cinéma depuis le", "21 octobre 2026")),
    ("Au cinéma le 12 novembre 2026", ("Au cinéma le", "12 novembre 2026")),
    ("Sur Netflix le 3 décembre", ("Sur Netflix le", "3 décembre")),
    ("Sortie le 1er janvier 2027", ("Sortie le", "1er janvier 2027")),
    ("", None),
    ("Un film de Christopher Nolan", None),
])
def test_split_date(line, expected):
    assert th.split_date(line) == expected


@pytest.mark.parametrize("line, label", [
    ("Au cinéma le 12 novembre 2026", "NOUVEAU"),
    ("Au cinéma le 1 novembre 2026", "NOUVEAU"),          # le jour de la sortie
    ("Au cinéma depuis le 21 octobre 2026", ""),          # deja sorti : jamais « nouveau »
    ("Sorti le 2 octobre 2026", ""),
    ("Au cinéma le 21 octobre 2026", ""),                 # date passee, meme sans « depuis »
    ("", ""),                                             # date inconnue : rien d'invente
    ("Un film de Christopher Nolan", ""),
])
def test_nouveau_seulement_avant_la_sortie(line, label):
    assert th.badge_label(line, TODAY) == label


def test_badge_sans_libelle_plus_etroit():
    from video import neon_badge

    alone = neon_badge.frames("", word=th.LABEL, label="", icon="play")
    new = neon_badge.frames("", word=th.LABEL, label="NOUVEAU", icon="play")
    assert alone[0].width < new[0].width
    assert len(alone) == round(neon_badge.PERIOD_S * neon_badge.FPS)


def test_reflet_sur_la_date_puis_repos():
    frames = th.date_frames("Au cinéma le 12 novembre 2026", 940)
    assert len(frames) == round(th.DATE_PERIOD_S * th.DATE_FPS)
    still = th.date_block("Au cinéma le 12 novembre 2026", 940)
    assert frames[-1].tobytes() == still.tobytes()
    assert frames[round(th.SHEEN_S * th.DATE_FPS / 2)].tobytes() != still.tobytes()
    assert th.date_frames("", 940) == []


def test_en_tete_tient_dans_la_place_et_se_reduit():
    short = th.build("Karma", "Au cinéma le 12 novembre 2026", 1080, 800, 430, today=TODAY)
    long = th.build("Mission: Impossible – The Final Reckoning", "Au cinéma le 12 novembre 2026",
                    1080, 800, 430, today=TODAY)
    assert short and long and short.height <= 430 and long.height <= 430
    assert len(short.sprites) == 2
    assert th.build("Karma", "", 1080, 800, 20) is None


class TestMontage:
    def test_bande_annonce_animee_et_autres_inchangees(self):
        from news_story.video_composer import build_layers

        _, _, sprites = build_layers(title="Karma", label="BANDE-ANNONCE",
                                     subtitle="Au cinéma le 12 novembre 2026",
                                     source_size=(1920, 1080), cta="Suivez-moi")
        assert len(sprites) == 2
        _, _, none = build_layers(title="Karma", label="ACTUALITÉ", source_size=(1920, 1080))
        assert none == []

    def test_animation_sous_l_intro(self):
        from news_story.video_composer import IntroSpec, ffmpeg_args

        spec = IntroSpec(video="i.mp4", mask="m.mp4", text_png="t.png", duration=5.0,
                         trailer_has_audio=True, trailer_duration=30.0)
        sprites = [("a0_%03d.png", 30, 46, 200), ("a1_%03d.png", 30, 70, 450)]
        args = ffmpeg_args("in.mp4", "ov.png", "out.mp4", (0, 622, 1080, 608), intro=spec,
                           sprites=sprites)
        graph = args[args.index("-filter_complex") + 1]
        assert args.count("-stream_loop") == 2
        # Entrees 0-1 (video, calque), 2-4 (intro), 5-6 (animations).
        assert "[5:v]overlay=46:200:shortest=1" in graph
        assert "[6:v]overlay=70:450:shortest=1" in graph
        assert graph.index("[6:v]") < graph.index("[texted][intro]")

    def test_sans_intro(self):
        from news_story.video_composer import ffmpeg_args

        args = ffmpeg_args("in.mp4", "ov.png", "out.mp4", (0, 622, 1080, 608),
                           sprites=[("a0_%03d.png", 30, 46, 200)])
        graph = args[args.index("-filter_complex") + 1]
        assert "[2:v]overlay=46:200:shortest=1,format=yuv420p[v]" in graph
