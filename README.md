# Logiciel-d-coupe (clip_farming)

Transforme automatiquement une video longue en plusieurs clips courts (9:16, sous-titres incrustes) optimises pour Instagram Reels.

**100 % local pour le traitement.** Aucune API externe, aucune cle API, aucun compte, aucun abonnement pour transcrire/analyser/decouper/exporter. Une connexion internet n'est necessaire qu'une seule fois, pour installer les dependances et telecharger les modeles (Whisper + detecteur de visage). Ensuite, le traitement fonctionne entierement hors ligne.

La **recherche YouTube** (optionnelle, section dediee plus bas) est la seule fonctionnalite qui a besoin d'internet a chaque utilisation -- c'est inherent a la recherche sur une plateforme distante, pas un choix de conception.

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

L'installation tire `faster-whisper`, `librosa`/`numba` (analyse audio), `opencv-python-headless` (detection de visage) et `PySide6` (interface graphique, `gui_main.py`) -- prevoir quelques minutes et ~400-500 Mo.

### 4. Premiere execution : telechargement des modeles

Au premier lancement, deux telechargements automatiques ont lieu (uniquement s'ils ne sont pas deja en cache) :

- Le **modele Whisper** choisi (`--model`, defaut `small`, ~484 Mo) -- mis en cache dans `~/.cache/huggingface/`.
- Le **detecteur de visage** OpenCV DNN (~28 Ko + ~10,7 Mo) -- mis en cache dans `.cache/models/` du projet.

Si le detecteur de visage ne peut pas etre telecharge (pas de connexion), le programme **continue normalement** : il utilise un recadrage centre au lieu d'un recadrage centre sur le visage. Ce n'est jamais bloquant.

Une fois ces telechargements faits, **le programme fonctionne entierement hors ligne**.

### Alternative : version .exe (Windows, sans installer Python)

Deux `.exe` autonomes (Python + toutes les dependances + ffmpeg deja inclus) sont construits automatiquement par GitHub Actions a chaque evolution du code -- onglet **Actions** du depot :

- **CLI** -- workflow "Build Windows .exe", artifact `clip_farming-windows` (zip). Dezipper puis, depuis une invite de commandes dans le dossier extrait :
  ```
  clip_farming.exe --input video.mp4 --clip-duration 45 --nb-clips 5
  ```
- **Interface graphique (ClipFarming)** -- workflow "Build Windows GUI (.exe + installateur)", deux artifacts : `ClipFarming-windows` (dossier zippe, meme principe que la CLI : dezipper puis lancer `ClipFarming.exe`) et **`ClipFarming-Setup`** (`ClipFarming-Setup.exe`, un vrai installateur Windows -- installe dans `Program Files\ClipFarming`, cree les raccourcis Bureau et Menu Demarrer, fournit un desinstalleur). C'est la version a donner a quelqu'un qui ne connait pas la ligne de commande.

Important a savoir : packager en `.exe` ne change rien aux besoins materiels (CPU/RAM) pour faire tourner Whisper et encoder la video -- c'est exactement le meme code, ca evite seulement d'installer Python separement. Le modele Whisper doit toujours etre telecharge au premier lancement (connexion internet necessaire une fois, comme en Python) -- y compris pour la version GUI/installateur : le modele n'est pas embarque dans l'installateur (le garder leger l'a emporte sur un fonctionnement 100% hors ligne des l'installation).

`config/`, `assets/` et `ffmpeg.exe` sont deja a cote de l'executable dans les deux cas -- rien d'autre a installer.

## Interface graphique (ClipFarming)

C'est l'interface recommandee pour un usage quotidien -- la CLI (`main.py`) reste disponible et pleinement fonctionnelle pour un usage scriptable/automatise, mais **aucune des deux n'est requise pour utiliser l'autre** : ce sont deux points d'entree independants vers le meme moteur.

Lancement (une fois les dependances installees, voir Installation ci-dessus) :
```bash
python gui_main.py
```

Navigation laterale : **Accueil** (glisser-deposer une video ou la selectionner, regler duree/nombre de clips/modele, lancer), **Recherche** (recherche YouTube, section dediee plus bas), **Projets** (historique des analyses passees, reouvrables a tout moment), **Parametres** (modele par defaut, GPU/CPU, dossier des projets, style de sous-titres, pondération du score et mots-cles -- ces deux derniers modifient directement `config/settings.json`/`config/hooks_keywords.json`, les memes fichiers que lit la CLI).

Pendant une analyse, l'interface **ne se fige jamais** (le traitement tourne dans un thread separe) : progression reelle par etape, temps ecoule/estimation restante, nombre de clips deja trouves, et un bouton **Annuler** qui interrompt reellement le traitement (y compris un encodage ffmpeg en cours) et nettoie les fichiers temporaires.

Les resultats s'affichent en grille (score, miniature, timestamps, transcription, detail du score) avec un lecteur video integre (QtMultimedia -- jamais besoin d'ouvrir VLC) permettant de naviguer d'un clip a l'autre, et des boutons d'export (un clip, tous les clips, ou ouvrir le dossier du projet).

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
| `--input` | — | Chemin de la video source locale (un de `--input`/`--youtube`/`--search` est requis) |
| `--youtube` | — | ID ou URL YouTube a telecharger puis traiter (voir section recherche YouTube) |
| `--search` | — | Recherche YouTube seule, n'affiche que des resultats |
| `--confirm-rights` | off | Confirme disposer des droits necessaires -- obligatoire pour `--youtube` |
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

Fichiers modifiables sans toucher au code :

- `config/settings.json` -- valeurs par defaut des options CLI, poids du score composite (`weights`, doivent sommer a 1.0), parametres internes de scoring.
- `config/hooks_keywords.json` -- liste des mots/expressions accrocheurs et amorces de question.
- `config/subtitles.json` -- styles de sous-titres (police, taille, couleur, position, mode `progressive`/`classic`).
- `config/youtube.json` -- poids du Video Potential Score et limite de quota (recherche YouTube, section dediee plus bas).
- `config/editing.json` -- modules d'edition automatique (section suivante). Chaque module a son propre `enabled` : le passer a `false` retablit exactement le comportement d'avant son ajout.

## Edition automatique

Modules optionnels appliques apres la selection des passages. Ils reutilisent les donnees deja calculees (mots horodates de Whisper, analyse audio, scores) sans declencher de traitement supplementaire de la video.

Regle commune : **mieux vaut ne rien modifier que mal modifier**. Chaque module produit une confiance interne ; sous le seuil configure, sa proposition est ignoree et l'etat d'origine conserve.

### Scores d'un clip

| Score | Ce qu'il mesure |
|---|---|
| **Hook Score** (`total`) | force de l'accroche : audio, mots-cles, questions, densite, silence de mise en tension, intensite |
| **Content Score** | densite et structure du propos (mots-cles, debit, longueur des phrases, ponctuation forte) |
| **Rewatch Score** | envie de revoir : part reelle de parole, clip qui se termine sur une phrase finie, variation d'intensite, phrases courtes |
| **Viral Potential** | combinaison ponderee des trois (`clip_scores.weights` dans `config/editing.json`) -- c'est le score de classement |

Ce sont des heuristiques explicables construites sur les memes mesures que le Hook Score, **pas** une prediction de viralite reelle : aucune donnee de performance ne permettrait de les calibrer.

### Detection du contexte (`context_detection`)

Recale les bornes de chaque clip sur la structure reelle du discours plutot que sur un decoupage arbitraire :

- un debut au milieu d'une phrase remonte au debut de cette phrase ;
- une fin au milieu d'une phrase va jusqu'a sa fin ;
- une question suivie de sa reponse inclut la reponse (payoff) ;
- une marge configurable est ajoutee, sans jamais mordre sur la phrase voisine ;
- aucune borne ne tombe au milieu d'un mot ;
- la **duree maximale** (`clip_duration` x `1 + max_overshoot_ratio`) reste prioritaire sur tout le reste.

Quand ce module est actif, `--pre-roll`/`--post-roll` ne sont plus appliques : c'est lui qui fixe les bornes (deux extensions superposees se marcheraient dessus). Le clip est ensuite re-note sur ses bornes definitives, pour que les scores affiches soient ceux du clip reellement exporte.

Chaque clip recoit aussi une **categorie narrative** (revelation, histoire, conclusion, explication, liste, conseil) deduite des marqueurs de `config/editing.json`. Si aucun marqueur ne ressort, aucune categorie n'est attribuee -- jamais une categorie inventee.

### Sous-titres intelligents (`captions`)

Decoupage adapte au contenu plutot qu'a un simple compteur de mots : la coupe suit la ponctuation, les pauses reelles du locuteur et la longueur de ligne (`max_chars_per_group`), pour des blocs de 2 a 5 mots lisibles d'un coup d'oeil.

**Mise en evidence** des mots importants, a partir de signaux deja disponibles : mots-cles de `config/hooks_keywords.json`, chiffres reellement prononces, et mots nettement plus forts que le reste du clip (niveau audio deja mesure). Au plus un mot par bloc et au plus `max_ratio` du clip. **Si aucun mot ne ressort clairement, aucun n'est mis en avant** -- jamais un mot choisi par defaut.

**Placement vertical** : quand un visage occupe le bas du cadre, les sous-titres remontent au-dessus de lui. Sans visage detecte de facon fiable, la marge du style est conservee telle quelle.

**Six presets** dans `config/subtitles.json` -- `classic` (karaoke par phrase), `bold`, `dynamic` (defaut), `minimal`, `podcast`, `gaming` -- tous personnalisables : police, taille, couleurs, contour, ombre, position, animation (`none`/`fade`/`pop`), mots par groupe, longueur de ligne. Les styles historiques `progressive` et `big_text` sont conserves et produisent exactement le meme decoupage qu'avant.

**Export** : les sous-titres sont toujours incrustes dans le MP4 ; `export_srt`/`export_vtt` ecrivent en plus `subtitles/clip_XX.srt` / `.vtt`, construits a partir des **memes blocs** que la video -- un fichier exporte ne peut donc pas afficher un decoupage ou des timings differents de ce qu'on voit a l'ecran.

## Recherche YouTube (optionnelle)

Permet de trouver des videos candidates avant de les analyser, plutot que de partir d'un fichier deja en main. **A besoin d'internet a chaque recherche** (voir l'avertissement en tete de ce README) -- contrairement au reste du logiciel.

### Methode

Utilise exclusivement la **YouTube Data API v3 officielle** (REST direct, pas de scraping des pages de resultats, pas de contournement des protections de YouTube). Deux appels : `search.list` (la recherche) puis `videos.list` groupe (vues/duree/licence pour les resultats affiches).

### Obtenir une cle API (gratuite)

1. [Google Cloud Console](https://console.cloud.google.com/) -> creer un projet (ou en reutiliser un).
2. "APIs & Services" -> "Library" -> chercher "YouTube Data API v3" -> l'activer.
3. "APIs & Services" -> "Credentials" -> "Create credentials" -> "API key".
4. Aucune carte bancaire requise pour le quota gratuit.

**La cle n'est jamais ecrite dans le code ni committee.** Deux facons de la fournir :
- variable d'environnement `YOUTUBE_API_KEY` ;
- ou un fichier texte `youtube_api_key.txt` (contenant uniquement la cle) place a cote de `main.py` (ou de `clip_farming.exe` pour la version .exe) -- deja dans `.gitignore`.

### Quotas

Le quota gratuit est de **10 000 unites/jour**, remis a zero a minuit heure du Pacifique. Une recherche (`search.list`) coute 100 unites (~100 recherches/jour max) ; recuperer les details (vues/duree) coute 1 unite pour tout le lot. Le logiciel garde un compteur local (`.cache/youtube_quota.json`) et previent avant de depasser plutot que de laisser l'API renvoyer une erreur brute.

### Utilisation

```bash
# Recherche seule -- affiche les resultats, ne telecharge rien
python main.py --search "podcast entrepreneuriat francais" --max-results 10 --sort potential

# Filtres disponibles : --yt-language, --yt-duration {short,medium,long},
# --yt-min-duration-s, --yt-max-duration-s, --yt-published-after/--yt-published-before
# (YYYY-MM-DD), --yt-channel, --yt-category, --yt-creative-commons

# Analyser une video trouvee (telecharge puis lance le pipeline normal)
python main.py --youtube <id_ou_url> --confirm-rights --clip-duration 45 --nb-clips 5
```

Le tri `--sort potential` utilise le **Video Potential Score** (`youtube/ranking.py`) : pertinence + popularite (vues, echelle log) + duree exploitable (combien de clips tiennent dedans) + un proxy de qualite tres faible base uniquement sur la duree. **Ce n'est pas le Hook Score** (`config/settings.json`) : le premier note une video entiere avant tout telechargement a partir de simples metadonnees, le second note un passage precis apres transcription reelle. Les deux ne sont jamais additionnes.

### ⚠️ Droits d'utilisation -- a lire avant d'utiliser `--youtube`

**Trouver une video ne donne aucun droit de la reutiliser.** Deux points distincts, a ne pas confondre :

- Telecharger une video par un autre moyen que le bouton de telechargement officiel de YouTube **viole les conditions d'utilisation de YouTube** -- y compris pour une video marquee Creative Commons. Une licence Creative Commons porte sur le *contenu* (droit d'auteur), elle ne donne aucun droit vis-a-vis de la *plateforme* YouTube elle-meme.
- Republier ou monetiser un extrait sans les droits necessaires (accord du createur, licence explicite hors YouTube, contenu dont tu es l'auteur...) est une question de droit d'auteur, separee de ce qui precede.

Le filtre `--yt-creative-commons` est **indicatif**, pas une garantie juridique -- l'information vient de YouTube telle quelle. `--youtube` refuse d'agir sans `--confirm-rights`, qui n'est qu'une confirmation de ta part : le logiciel ne verifie ni ne peut verifier tes droits reels.

## Sortie

```
output/
├── clips/         clip_01.mp4, clip_02.mp4...
├── thumbnails/
├── subtitles/    clip_01.srt (optionnel)
├── metadata/      clip_01.json -- detail complet d'un clip
├── project.json   manifeste du projet (interface graphique)
└── results.json   index de tous les clips
```

`results.json` contient, pour chaque clip : chemin relatif du fichier, timestamps, duree, score de classement, detail de tous les scores, transcription, langue detectee, principales raisons du score (`reasons`) et trace de la detection de contexte (`context`). Les fichiers de `metadata/` reprennent la meme information clip par clip.

Le score n'apparait plus dans le nom du fichier : il change des que les poids changent, alors que le nom est reference par `results.json`, les sous-titres et les miniatures. Les projets produits avant cette structure (clips a plat, `clip_01_score_92.mp4`) restent lisibles tels quels.

## Tests

```bash
pip install -r requirements.txt   # inclut pytest
pytest tests/
```

Les tests sont des tests unitaires purs (analyse texte, scoring, selection, analyse audio sur un signal synthetique, decoupage en phrases, recalage du contexte, timeline de montage, structure de sortie) -- ils ne necessitent ni ffmpeg, ni modele Whisper, ni GPU.

## Limites connues (volontaires)

- La detection de hook est un **score signal**, pas une comprehension semantique -- elle ne remplace pas un montage humain, elle propose des candidats.
- Une video sans piste audio ne peut pas etre traitee (transcription impossible) -- erreur explicite plutot qu'un plantage.
- Le detecteur de visage est un detecteur DNN classique (pas de suivi entre frames) -- suffisant pour centrer un cadrage, pas pour un suivi fluide type camera operator.
- Voir la section 17 du cahier des charges du projet pour les evolutions prevues mais volontairement non implementees en V1 (suivi de visage, detection de scene/rire, interface graphique, apprentissage sur les clips gardes/supprimes...).

## Notes de compatibilite

- **numpy** est fige `<2` : `librosa`/`numba` ont un historique de friction avec numpy 2.x via des dependances transitives. A revisiter si une version future de librosa confirme une compatibilite numpy 2 totale.
- **Pas de dependance PyTorch** dans tout le projet : `faster-whisper` (CTranslate2) et la detection de visage (OpenCV DNN) n'en ont pas besoin -- empreinte disque et memoire reduite.
- **ffmpeg systeme reste necessaire** meme si `faster-whisper` embarque son propre decodeur (paquet `av`/PyAV) : ce dernier ne sert qu'a la transcription. La decoupe, le recadrage et l'incrustation des sous-titres passent par le binaire `ffmpeg` du systeme.
