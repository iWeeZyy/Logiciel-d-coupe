# Sa presence fait que pytest ajoute la racine du repo a sys.path, ce qui
# permet aux tests d'importer les paquets top-level (core, analysis, video,
# transcription...) sans installer le projet.

import pytest


@pytest.fixture(autouse=True)
def _pas_de_cle_claude(monkeypatch, tmp_path_factory):
    """Aucun test n'appelle l'API Claude pour de vrai : ni la cle du
    developpeur (variable d'environnement ou fichier a la racine du depot),
    ni son cache de resumes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        from news_story import ai_summary
    except Exception:  # noqa: BLE001
        return
    # Coffre de mots de passe en memoire : jamais le vrai Gestionnaire
    # d'identifiants du developpeur.
    from publishing import tokens

    class _Ring:
        def __init__(self):
            self.store = {}

        def get_password(self, service, name):
            return self.store.get((service, name))

        def set_password(self, service, name, value):
            self.store[(service, name)] = value

        def delete_password(self, service, name):
            self.store.pop((service, name), None)

    ring = _Ring()
    monkeypatch.setattr(tokens, "_keyring", lambda: ring)
    base = tmp_path_factory.mktemp("claude")
    monkeypatch.setattr(ai_summary, "KEY_FILE", base / "anthropic_api_key.txt")
    monkeypatch.setattr(ai_summary, "MODEL_FILE", base / "anthropic_model.txt")
    monkeypatch.setattr(ai_summary, "CACHE_FILE", base / "ai_summaries.json")
    # Ni la memoire des news publiees du developpeur.
    from news_story import published

    monkeypatch.setattr(published, "FILE", base / "news_publiees.json")
