# Logiciel-d-coupe (clip_farming)

Transforme automatiquement une video longue en plusieurs clips courts (9:16, sous-titres incrustes) optimises pour Instagram Reels.

**100 % local.** Aucune API externe, aucune cle API, aucun compte, aucun abonnement. Une connexion internet n'est necessaire qu'une seule fois, pour installer les dependances et telecharger les modeles (Whisper + detecteur de visage). Ensuite, tout fonctionne hors ligne.

## Comment ca marche

1. Extraction de l'audio de la video.
2. Transcription automatique (faster-whisper), avec timestamps mot-par-mot.
3. Analyse audio (volume, pics, silences, hauteur) et texte (questions, mots-cles, densite) par fenetre glissante.
4. Score composite transparent pour chaque passage candidat.
5. Selection des meilleurs passages, sans chevauchement, avec du contexte (pre-roll/post-roll).
6. Decoupe + recadrage automatique en 1080x1920 (centre sur le visage si detecte, sinon crop centre).
7. Incrustation des sous-titres (style mot-par-mot dynamique par defaut).
8. Export dans `output/` + `output/results.json` (score detaille par critere).

Le detecteur de hooks n'utilise **aucun LLM** : c'est un score signal (audio + texte), pas une comprehension du contenu. Chaque score reste explicable (voir `output/results.json` -> `reasons`, ou `--debug-scores`).

## Installation

### 1. Python

Python **3.11 ou 3.12** (verifie sur 3.11 dans cet environnement). `python3 --version` pour vérifier.

### 2. ffmpeg

Doit etre installe **au niveau systeme** et present dans le PATH, avec le support `libass` (necessaire pour incruster les sous-titres) et `libx264`.

- **Ubuntu/Debian** :
  ```bash
  sudo apt-get update
  sudo apt-get install --no-install-recommends ffmpeg
  ```
- **macOS** (Homebrew) :
  ```bash
  brew install ffmpeg
  ```
- **Windows** : telecharger un build sur [ffmpeg.org](https://ffmpeg.org/download.html) (ou `winget install ffmpeg`) et ajouter le dossier `bin/` au PATH.

Verifier que le filtre sous-titres est disponible :
```bash
ffmpeg -filters | grep subtitles
```

### 3. Dependances Python

```bash
python3 -m venv .venv
source .venv/bin/activate        # .venv\Scripts\activate sous Windows
pip install -r requirements.txt
```

L'installation tire `faster-whisper`, `librosa`/`numba` (analyse audio) et `opencv-python-headless` (detection de visage) -- prevoir quelques minutes et ~200-300 Mo.

### 4. Premiere execution : telechargement des modeles

Au premier lancement, deux telechargements automatiques ont lieu (uniquement s'ils ne sont pas deja en cache) :

- Le **modele Whisper** choisi (`--model`, defaut `small`, ~484 Mo) -- mis en cache dans `~/.cache/huggingface/`.
- Le **detecteur de visage** OpenCV DNN (~28 Ko + ~10,7 Mo) -- mis en cache dans `.cache/models/` du projet.

Si le detecteur de visage ne peut pas etre telecharge (pas de connexion), le programme **continue normalement** : il utilise un recadrage centre au lieu d'un recadrage centre sur le visage. Ce n'est jamais bloquant.

Une fois ces telechargements faits, **le programme fonctionne entierement hors ligne**.

## Utilisation

```bash
python main.py --input video.mp4
```

```bash
python main.py \
  --input video.mp4 \
  --clip-duration 45 \
  --nb-clips 5
```

```bash
python main.py --help
```

### Options principales

| Option | Defaut | Description |
|---|---|---|
| `--input` | *(obligatoire)* | Chemin de la video source |
| `--output` | `output` | Dossier de sortie |
| `--clip-duration` | `45` | Duree cible d'un clip (secondes) |
| `--nb-clips` | `5` | Nombre de clips a generer |
| `--language` | auto | Code langue ISO (`fr`, `en`...) -- omis = detection automatique |
| `--pre-roll` | `5` | Secondes de contexte avant le hook |
| `--post-roll` | `3` | Secondes de contexte apres le hook |
| `--min-gap` | `20` | Distance minimale (s) entre deux clips |
| `--subtitle-style` | `progressive` | Style de sous-titres (voir `config/subtitles.json`) |
| `--model` | `small` | Modele Whisper : `tiny`, `base`, `small`, `medium`, `large-v3` |
| `--device` | `auto` | `cpu`, `cuda`, ou `auto` (detection automatique du GPU) |
| `--overwrite` | off | Ecrase les clips existants dans le dossier de sortie |
| `--no-cache` | off | Ignore/n'ecrit pas le cache de transcription (`.cache/`) |
| `--debug-scores` | off | Affiche le detail des scores de chaque clip retenu |

### CPU vs GPU

**CPU** (par defaut, aucune configuration requise) : `--device cpu` ou laisser `auto` sans GPU disponible. Le modele `small` en `int8` est un bon compromis vitesse/qualite sur CPU.

**GPU NVIDIA** (accelere la transcription) : `--device cuda`. Necessite CUDA + cuDNN compatibles avec CTranslate2 (utilise par faster-whisper) :

- ctranslate2 recent (>= 4.5.0) demande **CUDA >= 12.3 + cuDNN 9**.
- Pour CUDA 12.x + cuDNN 8 : `pip install "ctranslate2==4.4.0"`.
- Pour CUDA 11 : `pip install "ctranslate2==3.24.0"` (version ancienne, verifier la compatibilite avec la version de faster-whisper installee).
- Alternative sans CUDA systeme : `pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` puis positionner `LD_LIBRARY_PATH` **avant** de lancer `python` (CTranslate2 resout le chemin des libs a l'import, pas a l'appel) :
  ```bash
  export LD_LIBRARY_PATH=$(python3 -c "import os,nvidia.cublas.lib,nvidia.cudnn.lib as c; print(os.path.dirname(nvidia.cublas.lib.__file__)+':'+os.path.dirname(c.__file__))")
  ```

Le programme indique toujours dans ses logs quel peripherique (`cpu`/`cuda`) est reellement utilise -- un GPU mal configure ne plante pas, il retombe silencieusement sur CPU, donc verifier ce log si un GPU est attendu.

## Configuration

Trois fichiers, modifiables sans toucher au code :

- `config/settings.json` -- valeurs par defaut des options CLI, poids du score composite (`weights`, doivent sommer a 1.0), parametres internes de scoring.
- `config/hooks_keywords.json` -- liste des mots/expressions accrocheurs et amorces de question.
- `config/subtitles.json` -- styles de sous-titres (police, taille, couleur, position, mode `progressive`/`classic`).

## Sortie

```
output/
├── clip_01_score_92.mp4
├── clip_02_score_88.mp4
├── ...
└── results.json
```

`results.json` contient, pour chaque clip : nom de fichier, timestamps, duree, score total, detail par critere (audio/keywords/questions/densite/silence/intensite), transcription, langue detectee, et les principales raisons du score (`reasons`).

## Tests

```bash
pip install -r requirements.txt   # inclut pytest
pytest tests/
```

Les tests sont des tests unitaires purs (analyse texte, scoring, selection, analyse audio sur un signal synthetique) -- ils ne necessitent ni ffmpeg, ni modele Whisper, ni GPU.

## Limites connues (volontaires)

- La detection de hook est un **score signal**, pas une comprehension semantique -- elle ne remplace pas un montage humain, elle propose des candidats.
- Une video sans piste audio ne peut pas etre traitee (transcription impossible) -- erreur explicite plutot qu'un plantage.
- Le detecteur de visage est un detecteur DNN classique (pas de suivi entre frames) -- suffisant pour centrer un cadrage, pas pour un suivi fluide type camera operator.
- Voir la section 17 du cahier des charges du projet pour les evolutions prevues mais volontairement non implementees en V1 (suivi de visage, detection de scene/rire, interface graphique, apprentissage sur les clips gardes/supprimes...).

## Notes de compatibilite

- **numpy** est fige `<2` : `librosa`/`numba` ont un historique de friction avec numpy 2.x via des dependances transitives. A revisiter si une version future de librosa confirme une compatibilite numpy 2 totale.
- **Pas de dependance PyTorch** dans tout le projet : `faster-whisper` (CTranslate2) et la detection de visage (OpenCV DNN) n'en ont pas besoin -- empreinte disque et memoire reduite.
- **ffmpeg systeme reste necessaire** meme si `faster-whisper` embarque son propre decodeur (paquet `av`/PyAV) : ce dernier ne sert qu'a la transcription. La decoupe, le recadrage et l'incrustation des sous-titres passent par le binaire `ffmpeg` du systeme.
