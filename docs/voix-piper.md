# Ajouter une voix Piper à Voice Studio

Note pour développeur. Côté utilisateur, tout se fait dans
**Voice Studio → Gérer les voix** ; ce document explique ce qu'il y a derrière.

## Ce qu'est une voix

Une voix Piper est une **paire de fichiers** :

| Fichier | Contenu | Taille |
| --- | --- | --- |
| `<clé>.onnx` | le modèle neuronal | quelques dizaines de Mo |
| `<clé>.onnx.json` | phonèmes, fréquence d'échantillonnage, langue espeak | quelques Ko |

Les deux doivent être présents. `piper_models.is_installed()` ne répond « oui »
que dans ce cas : un téléchargement interrompu ne doit jamais ressembler à une
voix utilisable.

Emplacement : `%LOCALAPPDATA%\ClipFarming\voice_studio_data\piper`, modifiable
dans Paramètres. Jamais dans le dossier d'installation, que Windows protège en
écriture et qu'une désinstallation vide entièrement.

## Ajouter une voix au catalogue

`config/piper_voices.json`, un fichier de **configuration** et non de code :
une adresse qui change se corrige sans reconstruire l'application.

```json
{
  "key": "fr_FR-siwis-medium",
  "label": "Français — Femme (Siwis, qualité moyenne)",
  "language": "fr_FR",
  "quality": "medium",
  "gender": "female",
  "path": "fr/fr_FR/siwis/medium/fr_FR-siwis-medium"
}
```

- `key` : le nom des fichiers sur le disque, sans extension.
- `path` : le chemin sous `base_url`, sans extension non plus. Le
  téléchargement ajoute `.onnx` et `.onnx.json`.
- `gender` : laissé vide quand il n'est pas connu ; l'interface n'affiche alors
  rien plutôt que de deviner.
- **Aucune taille n'est écrite ici** : elle est lue sur le serveur au moment du
  téléchargement. Une taille recopiée à la main serait fausse un jour ou
  l'autre.

Une voix déposée à la main dans le dossier fonctionne aussi : le catalogue
propose, il ne décide pas de ce qui existe.

## Deux façons d'exécuter Piper

`PiperEngine.runtime()` répond `"library"`, `"binary"` ou `""` :

1. **`library`** — la bibliothèque Python `piper-tts`, si elle est installée.
   Génération dans le processus, sans fichier temporaire. C'est le cas en
   développement (`pip install piper-tts`).
2. **`binary`** — le programme `piper` officiel, cherché dans `PIPER_BIN`, dans
   le `PATH`, puis **récursivement** dans `voice_studio_data/piper-bin`. C'est le
   cas de l'application compilée, qui **n'embarque pas** la bibliothèque Python.

   La récursivité n'est pas un détail : l'archive officielle range son contenu
   dans un sous-dossier `piper/`. Une recherche limitée à la racine ne trouvait
   rien, et l'application proposait d'installer un moteur déjà installé pendant
   que les voix téléchargées restaient inutilisables. Une seule fonction fait
   cette recherche désormais, `piper_models.engine_binary()`, partagée par le
   moteur et par la fenêtre de gestion : c'est le désaccord entre deux
   recherches différentes qui avait produit le défaut.

Pourquoi cette séparation : `piper-tts` est publié sous **GPL-3.0**. L'inclure
dans un exécutable distribué imposerait ses obligations à toute l'application.
Le programme `piper`, lui, est appelé comme un processus séparé — une frontière
qui ne pose pas cette question. Le `.spec` PyInstaller ne collecte donc pas
`piper-tts`, et le gestionnaire de voix propose d'installer le programme.

## Le piège du code de langue espeak

Les voix officielles déclarent `"espeak": {"voice": "fr-fr"}`. La donnée
espeak livrée avec `piper-tts` **refuse** ce code (`Failed to set voice: fr-fr`)
et attend `"fr"`. Sans rattrapage, aucune voix française officielle ne
fonctionnerait.

`PiperEngine._espeak_voice()` essaie le code du modèle, puis sa langue de base,
et garde le premier que le phonémiseur accepte. Vérifié sur machine ; un test
le fige (`test_the_espeak_language_code_of_real_voices_is_repaired`).

## Ajouter une langue

Rien à coder : ajouter les entrées au catalogue avec leur `language`
(`en_US`, `es_ES`…). `CatalogueVoice.language_label` porte les drapeaux connus,
et l'interface n'affiche que les voix réellement installées.

## Tester sans réseau

`tests/helpers/fake_piper_voice.py` fabrique un modèle ONNX **synthétique** de
même interface. Il ne dit rien de la qualité d'une vraie voix, mais il permet
d'exécuter pour de vrai la phonémisation, la session ONNX, l'écriture du WAV,
la vitesse, les pauses, le cache et la conversion MP3. Les téléchargements sont
testés contre un petit serveur HTTP local, pas contre un double.
