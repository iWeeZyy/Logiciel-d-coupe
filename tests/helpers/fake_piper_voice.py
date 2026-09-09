"""Fabrique une voix Piper SYNTHETIQUE, utilisable par le vrai moteur.

Pourquoi : aucune vraie voix Piper ne peut etre telechargee depuis
l'environnement de developpement (huggingface.co y est bloque). Un modele
factice ne remplace pas une vraie voix -- il ne dit rien de la QUALITE du son
-- mais il a la meme interface ONNX, ce qui permet d'executer pour de vrai
toute la chaine : phonemisation espeak, table des phonemes, session ONNX,
ecriture du WAV, vitesse, volume, pauses, cache et conversion MP3.

Ce que ce modele ne peut PAS montrer : l'effet de la vitesse. Un vrai modele
etire la parole selon `length_scale` ; celui-ci rend un signal de longueur fixe
par phoneme. La transmission de la vitesse est donc verifiee autrement, en
regardant la valeur que le moteur passe reellement a Piper (voir les tests).
"""
from __future__ import annotations

import json
from pathlib import Path

SAMPLES_PER_PHONEME = 256
SAMPLE_RATE = 22050

PANGRAM = (
    "Bonjour, ceci est un essai de synthèse vocale française. "
    "Le vif zéphyr jubile sur les quais où soufflent quinze zéphyrs. "
    "Portez ce vieux whisky au juge blond qui fume, cinq, six, sept, huit ! "
    "Où est passé ce garçon exquis, joyeux, un peu naïf ?"
)


def build(directory, key: str = "fr_FR-test-medium") -> Path:
    """Ecrit <key>.onnx et <key>.onnx.json. Renvoie le chemin du modele."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from piper.phonemize_espeak import EspeakPhonemizer

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    phonemizer = EspeakPhonemizer()
    seen = {p for sentence in phonemizer.phonemize("fr", PANGRAM) for p in sentence}
    symbols = ["_", "^", "$", " "] + sorted(p for p in seen if p != " ")
    phoneme_id_map = {symbol: [index] for index, symbol in enumerate(symbols)}

    n = SAMPLES_PER_PHONEME
    initializers = [
        numpy_helper.from_array(np.ones((1, 1, n), np.float32), "ones"),
        numpy_helper.from_array(np.arange(n, dtype=np.float32).reshape(1, 1, n), "ramp"),
        numpy_helper.from_array(np.array([0.05], np.float32), "freq"),
        numpy_helper.from_array(np.array([1, 1, -1], np.int64), "shape"),
        numpy_helper.from_array(np.array([2], np.int64), "axes"),
    ]
    nodes = [
        helper.make_node("Cast", ["input"], ["f"], to=TensorProto.FLOAT),
        helper.make_node("Unsqueeze", ["f", "axes"], ["u"]),
        helper.make_node("Mul", ["u", "ones"], ["m"]),
        helper.make_node("Add", ["m", "ramp"], ["s"]),
        helper.make_node("Mul", ["s", "freq"], ["sf"]),
        helper.make_node("Sin", ["sf"], ["wave"]),
        helper.make_node("Reshape", ["wave", "shape"], ["output"]),
    ]
    graph = helper.make_graph(
        nodes, "fake_piper_voice",
        [helper.make_tensor_value_info("input", TensorProto.INT64, [1, None]),
         helper.make_tensor_value_info("input_lengths", TensorProto.INT64, [1]),
         helper.make_tensor_value_info("scales", TensorProto.FLOAT, [3])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 1, None])],
        initializer=initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 9
    onnx.checker.check_model(model)

    model_path = directory / f"{key}.onnx"
    onnx.save(model, str(model_path))
    (directory / f"{key}.onnx.json").write_text(json.dumps({
        "audio": {"sample_rate": SAMPLE_RATE},
        # "fr-fr" A DESSEIN : c'est ce qu'ecrivent les vraies voix, et c'est le
        # code que la donnee espeak livree avec piper refuse.
        "espeak": {"voice": "fr-fr"},
        "inference": {"noise_scale": 0.667, "length_scale": 1.0, "noise_w": 0.8},
        "phoneme_id_map": phoneme_id_map,
        "phoneme_type": "espeak",
        "num_symbols": len(symbols),
        "num_speakers": 1,
        "language": {"code": "fr_FR"},
    }, ensure_ascii=False), encoding="utf-8")
    return model_path
