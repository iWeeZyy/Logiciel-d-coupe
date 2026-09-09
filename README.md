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

### Telecharger l'application

Adresse permanente, toujours la derniere version :

**https://github.com/iWeeZyy/Logiciel-d-coupe/releases/latest**

- `ClipFarming-Setup.exe` -- l'installateur. Desinstalle la version precedente,
  puis lance-le.
- `ClipFarming-windows.zip` -- version portable, sans installation.

Chaque compilation sur `main` publie automatiquement une Release ; cette adresse
pointe toujours vers la plus recente et ne change jamais. Les artifacts de
GitHub Actions restent disponibles en parallele (historique build par build),
mais leur URL change a chaque compilation et ils expirent au bout de 30 jours.
Le depot etant prive, il faut etre connecte a GitHub pour telecharger.

### Radar YouTube et Twitch

Veille de contenu sur une liste personnalisable de createurs, YouTube et Twitch
depuis une seule page. Le Radar ne produit rien : il SELECTIONNE des
opportunites et les transmet au pipeline existant.

**Ce qui exige Internet** : resoudre une chaine, lancer un scan, lire des
statistiques. **Ce qui reste local** : scores, tendances, historique, favoris,
et evidemment tout le traitement video.

**Ce qu'affiche une fiche.** La duree du clip, son anciennete (« il y a 3 h »,
puis une date au-dela d'une semaine), son nombre de vues et le JEU. Twitch ne
renvoie qu'un identifiant numerique de jeu avec un clip : il est resolu en nom
par `GET /games`, en une requete pour toute la liste et avec un cache. Un nom
que Twitch ne rend pas laisse la categorie VIDE plutot que d'afficher le
nombre -- c'est ce que faisait l'application, et ce nombre partait meme en
hashtag.

**Trois ordres de lecture** : score radar, plus recents (par defaut), plus vus.
Aucun ne remplace les autres -- un clip qui vient de sortir n'a pas encore de
vues, un clip tres vu n'est plus une nouveaute. Le tri est fait par la base et
non apres coup : la liste est coupee a une limite, donc trier ensuite ne
trierait que ce que le tri precedent a laisse passer.

**Conformite.** Uniquement les APIs officielles -- YouTube Data API v3 et
Twitch Helix (flux applicatif "client credentials"). Aucun scraping, aucun
contournement, aucun telechargement automatique. Le Radar lit des metadonnees
publiques ; cela ne constitue jamais une cession de droits, et rien dans
l'interface ne le laisse entendre.

**Quota YouTube.** `search.list` coute 100 unites sur les 10 000 quotidiennes.
Le Radar passe par la playlist d'uploads (mise en cache) puis
`playlistItems.list` et `videos.list`, soit 2 unites par createur et par scan :
27 createurs reviennent a 54 unites, donc environ 185 scans par jour au lieu de
3. `search.list` n'est utilise que pour resoudre un nom approximatif, a l'ajout.

**Identifiants.** YouTube : la meme cle que la recherche (voir plus haut).
Twitch : creer une application sur https://dev.twitch.tv/console/apps, puis
definir `TWITCH_CLIENT_ID` et `TWITCH_CLIENT_SECRET`, ou renseigner
`twitch_credentials.txt` dans le dossier de donnees (Client ID en premiere
ligne, Client Secret en seconde). Sans identifiants, la plateforme se declare
indisponible avec la marche a suivre et l'autre continue de fonctionner.

**Le Radar ne cherche que des clips.** Un clip est deja le decoupage d'un
moment fort, fait par le public au moment ou il s'est produit. Les directs ont
ete retires du reglage par defaut apres usage reel : un direct tres suivi score
tres haut et occupait le haut de la liste a la place des clips. Ils restent
activables en ajoutant `"live"` a `twitch.content_kinds` dans
`config/radar.json` (`"vod"` egalement). Un type retire n'est plus cherche --
aucune requete n'est envoyee pour lui -- et les contenus de ce type deja
enregistres sont retires de la base au scan suivant : un direct d'hier ne
redeviendra jamais exact, et le garder fausserait les compteurs. Les favoris ne
sont pas touches, ils portent leur propre copie des statistiques du moment.

**Trois limites assumees, ecrites plutot que masquees :**

- Aucune API ne dit si une video est un **Short** : la detection se fait sur la
  duree et se presente comme une heuristique.
- Aucune API ne renvoie une **vitesse de progression** : elles donnent un
  compteur a l'instant present. Les tendances sont calculees sur les releves
  successifs du Radar, donc le premier scan d'un contenu ne peut rien dire --
  et l'affiche ainsi, au lieu d'annoncer 0 %.
- Le **nombre de followers Twitch** demande une autorisation utilisateur que la
  surveillance ne justifie pas : il reste inconnu plutot qu'affiche a zero.

**Twitch et le telechargement.** Un CLIP est telecharge par l'application :
Twitch propose lui-meme ce telechargement (menu Partager d'un clip). Les
versions precedentes le refusaient, sur une affirmation fausse -- elles
interdisaient ce que la plateforme autorise. Une VOD ou un direct, en revanche,
n'ont aucun bouton de telechargement chez Twitch et restent refuses : pour
ceux-la, selectionnez un fichier dont vous disposez legalement, il sera traite
comme n'importe quelle autre video.

Disposer du fichier n'est pas disposer des droits : Twitch fournit un fichier,
pas une licence. Le clip reste la propriete de son createur et peut contenir des
tiers, de la musique ou du jeu soumis a leurs propres regles. L'avertissement
s'affiche avant publication, il ne conditionne plus le telechargement.

Les clips telecharges sont gardes dans `%LOCALAPPDATA%\ClipFarming\clips` : un
clip deja recupere n'est jamais retelecharge, ni pour une reanalyse, ni pour un
envoi au Content Factory.

### Analyse de contenu d'un clip

Depuis une carte du Radar, "🔊 Analyser le contenu" ecoute le clip localement et
en rend un descriptif pret a publier : moment cle date, resume, description
editoriale, description courte, version reseaux sociaux, hashtags et trois
titres, chacun copiable en un clic. Plusieurs clips peuvent etre coches puis
analyses a la suite ; rien ne demarre sans un clic explicite.

Trois niveaux. Le descriptif complet -- moment cle, resume, les trois
descriptions, hashtags et titres -- est produit dans les TROIS : le niveau ne
change que ce qui s'ajoute autour.

| Niveau | Ce qu'il change |
| --- | --- |
| Rapide | Sans les emotions ni les mots horodates. Prend un modele Whisper plus leger s'il est deja telecharge ; sinon le gain de temps est faible, la transcription representant l'essentiel du travail. |
| Standard | Emotions et mots horodates. C'est le defaut. |
| Approfondie | Jusqu'a trois autres passages candidats pour le moment cle, avec leur note. |

- **Tout est local.** La transcription est Faster-Whisper, deja utilise par le
  pipeline. La redaction est EXTRACTIVE : les phrases proposees sont des phrases
  reellement prononcees, jamais un texte genere. Aucun modele de langue, aucune
  API distante, rien n'est envoye nulle part.
- **Rien n'est invente.** Un clip sans parole ne produit aucun descriptif et le
  dit. Un clip qui commence au milieu d'une idee signale qu'il manque du
  contexte au lieu de le combler. Une emotion supposee est affichee avec la
  phrase qui l'a declenchee et un niveau de confiance -- eleve, moyen ou faible,
  jamais un pourcentage, que rien ici ne permettrait de calculer honnetement.
- **Le media est recupere tout seul.** Le clip est telecharge depuis Twitch a la
  meilleure qualite disponible, puis garde. Un fichier de votre ordinateur reste
  utilisable a la place, et une VOD ou un direct en demande un (voir plus haut).
- Une analyse deja faite est reaffichee immediatement ; "Réanalyser" la refait
  et remplace l'ancienne. Le meme media, le meme modele et le meme niveau ne
  sont jamais retraites.

### Content Factory et apprentissage par les performances

**Content Factory** produit plusieurs clips d'une video longue en une passe. La
chaine de production est celle du pipeline habituel ; ce qui change est la
FACON de choisir les passages : au lieu du seul classement par potentiel viral,
un Content Factory Priority Score (potentiel, qualite du contexte, qualite
audio, publiabilite) assorti d'une penalite de redondance, temporelle et
thematique. Sans elle, les dix meilleurs scores d'une video de deux heures
viennent souvent du meme quart d'heure.

**Apprentissage.** Le logiciel enregistre les caracteristiques de chaque clip
produit (valeurs deja calculees, photographiees a la production), et
l'utilisateur peut saisir a la main les statistiques obtenues apres publication.
La page « Ce que le logiciel apprend » confronte alors le potentiel ESTIME a la
performance CONSTATEE.

Ce qui est volontairement absent :

- aucun modele d'apprentissage automatique. C'est un ajustement de ponderations
  guide par des correlations observees, et le code ne pretend pas autre chose ;
- aucune affirmation de causalite : « correlation », jamais « cause » ;
- aucune modification silencieuse d'un reglage. Toute proposition est affichee,
  acceptable, refusable, et annulable ;
- rien n'est affiche sous 10 clips saisis, et la confiance affichee ne depasse
  jamais 85 % -- aucune quantite de donnees saisies a la main ne justifie
  d'annoncer une certitude ;
- l'historique personnel pese au maximum 25 % du classement : il module
  l'analyse generale, il ne la remplace pas.

Toutes ces donnees restent sur la machine (`%LOCALAPPDATA%\ClipFarming\
performance`), exportables et effacables depuis l'application. Rien n'est
envoye nulle part.

### Ou l'application range ses fichiers

Installee en .exe, ClipFarming ne range AUCUN de vos fichiers dans son dossier
d'installation (`Program Files`) : projets, clips, caches et reglages
d'interface vivent ailleurs. Seuls les fichiers `config/*.json` y sont
reecrits, par la page Parametres. Trois emplacements distincts :

| Quoi | Ou | Survit a une desinstallation |
|---|---|---|
| Projets et clips generes | `Documents\ClipFarming` (modifiable dans Parametres) | oui |
| Caches, reglages, cle YouTube | `%LOCALAPPDATA%\ClipFarming` | oui |
| Modele Whisper | `%HF_HOME%` si defini, sinon `~/.cache/huggingface` | oui |
| Application elle-meme et `config/` | `Program Files\ClipFarming` | non, supprimee |

Les versions anterieures ecrivaient tout dans le dossier d'installation : au
premier lancement, ce qui s'y trouve encore est recupere automatiquement vers
les emplacements ci-dessus, sans jamais ecraser un fichier deja present.

**La desinstallation supprime tout le dossier d'installation**, et pas
seulement ce que l'installateur y avait pose. Le desinstalleur d'Inno ne
connait que les fichiers qu'il a ecrits et ne retire un dossier que s'il est
vide : le moindre fichier apparu apres l'installation -- cache Python ou numba
ecrit a cote d'une bibliotheque, journal, reglage reecrit -- bloquait son
dossier parent, et le dossier d'installation restait debout au complet. Rangez
donc vos propres fichiers ailleurs : ce dossier est fait pour disparaitre
entierement.

En mode script (depot clone), rien ne change : tout reste dans le depot.

### 4. Premiere execution : telechargement des modeles

Au premier lancement, deux telechargements automatiques ont lieu (uniquement s'ils ne sont pas deja en cache) :

- Le **modele Whisper** choisi (`--model`, defaut `small`, ~484 Mo ; `medium` ~1,5 Go, `large-v3` ~3 Go) -- mis en cache dans `~/.cache/huggingface/`. Si ce disque manque de place, deplacez le cache avec la variable d'environnement `HF_HOME` (par exemple `HF_HOME=E:\huggingface`). L'espace libre est verifie **avant** le telechargement, et un telechargement interrompu (dossier de modele present mais sans son fichier de poids) est detecte et retelecharge automatiquement au lancement suivant. L'avancement du telechargement s'affiche dans l'etape "Transcription" (par exemple `Telechargement du modele small : 210 / 484 Mo`) : l'application n'est pas figee, et "Annuler" reste utilisable pendant toute la duree.
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

### Choix du modèle Whisper

Sans GPU, le modèle est le premier facteur de temps de traitement, loin devant tout le reste :

| Modèle | Téléchargement | Sur processeur |
|---|---|---|
| `tiny` / `base` | ~75 / 145 Mo | très rapide, qualité limitée |
| `small` | ~480 Mo | **recommandé sans GPU** -- bonne qualité, temps raisonnable |
| `medium` | ~1,5 Go | lent |
| `large-v3` | ~3 Go | souvent plus lent que la durée de la vidéo elle-même |

L'interface demande confirmation avant de lancer `medium` ou `large` quand aucun GPU n'est détecté : sur une vidéo d'une demi-heure, la différence se compte en heures.

**Téléchargement interrompu** : un modèle coupé en cours de téléchargement laisse un dossier de cache sans son fichier de poids, que Hugging Face considère ensuite comme déjà présent -- il ne retélécharge plus rien et l'application échoue à chaque lancement. Le logiciel détecte ce cas, supprime lui-même le dossier fautif et relance le téléchargement une fois. En cas de second échec, le message nomme le dossier exact à supprimer.

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

### Cadrage intelligent 9:16 (`framing`)

Le recadrage suit le sujet au lieu de rester fige. La trajectoire est lissee sur trois niveaux, dans cet ordre : **zone morte** (sous un certain deplacement, le cadrage ne bouge pas du tout -- c'est ce qui elimine le tremblement de la detection), **moyenne exponentielle** (inertie), puis **limite de vitesse** (meme sur un saut brutal de detection, le cadrage ne peut pas se deplacer plus vite qu'une fraction d'image par seconde).

Le visage est place aux deux cinquiemes de la hauteur (`vertical_bias`) plutot qu'au centre : un visage exactement centre en 9:16 laisse un vide au-dessus de la tete et coupe le buste.

**Deux personnes** : le locuteur actif est estime en correlant le mouvement de la bouche de chaque visage avec l'energie audio, fenetre par fenetre, avec hysteresis pour ne pas sauter de l'un a l'autre. C'est une heuristique, pas un modele dedie -- sous le seuil de confiance, **les deux visages sont cadres ensemble** plutot que de parier sur le mauvais.

**Replis** : si le visage n'est detecte que sur une minorite des images analysees, ou si le sujet ne bouge pas, le suivi est abandonne au profit d'un cadrage fixe. Aucune detection du tout -> crop centre, comme avant.

### Montage automatique (`montage`)

Produit une **EditList** (la liste des segments conserves) sur laquelle sous-titres, trajectoire de cadrage et zooms se recalent automatiquement -- c'est ce qui garantit qu'une coupe ne desynchronise jamais les sous-titres.

- **Silences** : un silence n'est coupe que s'il est a la fois sans parole ET reellement silencieux cote audio (un rire ou une reaction n'est pas un silence). Les pauses qui suivent une question ou precedent un mot important sont protegees. Les silences de tete et de queue du clip ne sont jamais touches : c'est la marge posee volontairement par la detection de contexte.
- **Hesitations** : liste configurable ("euh", "hmm"...) et faux departs (repetition immediate d'un mot court). Une repetition volontaire espacee est conservee.
- **Plafond** : si le montage retirait plus de `max_removed_ratio` du clip, **rien n'est applique** -- a ce niveau ce n'est plus un nettoyage, c'est une reecriture du rythme du locuteur.
- **Zooms dynamiques** : legers, sur les moments forts deja identifies par les sous-titres intelligents, bornes en amplitude, en nombre par clip et en ecart minimal. Aucun mot marquant -> aucun zoom.
- **Audio** : normalisation du volume (`loudnorm`) et limitation des pics. La reduction de bruit reste desactivee par defaut : trop agressive, elle degrade la voix plus que le bruit qu'elle retire. Le son fait partie du module : decocher « Montage auto » rend aussi l'audio d'origine, intact.

Tout est assemble en **un seul encodage ffmpeg** par clip (montage, cadrage, zoom, mise a l'echelle, sous-titres, audio) : aucune perte de qualite due a des passes successives.

### Titres et description (`metadata`)

**Extraction, jamais generation.** Sans modele de langue -- le cahier des charges impose un traitement 100 % local sans LLM -- la seule facon honnete de proposer un titre est de reprendre des mots **reellement prononces**. Le module choisit la meilleure phrase, retire les amorces de discours ("et donc du coup..."), et tronque a une longueur de titre. Aucun fait, chiffre, citation ou nom n'est ajoute.

Trois propositions : **direct** (la phrase la plus claire), **curiosite** (une vraie question du clip si elle existe, sinon la meilleure phrase tronquee avant sa chute), **punchy** (la phrase courte la plus forte, en majuscules). Une variante impossible a construire honnetement est **absente** de la liste plutot que remplie.

Description : une a deux phrases reellement prononcees. Hashtags : uniquement des mots-cles presents dans le clip.

Consequence assumee : un titre est bon quand la personne dit une phrase percutante, et quelconque sinon. C'est le prix du 100 % local -- et c'est preferable a un titre invente qui promettrait ce que le clip ne montre pas.

### Miniatures (`thumbnails`)

Trois variantes 1080x1920 par clip : **a** (visage), **b** (plan de contexte), **c** (meilleure expression).

Les images candidates sont mesurees (nettete par variance du laplacien, luminosite, visage, yeux ouverts, ecart avec l'image precedente). Le flou et les transitions sont **eliminatoires**. Si aucune image ne passe, la moins mauvaise est proposee telle quelle -- jamais "reparee" a coups de filtres.

Le texte vient des titres extraits (jamais d'une phrase fabriquee), se pose dans la bande la plus eloignee du visage, et sa couleur est choisie d'apres la luminosite reelle de cette bande. La taille diminue jusqu'a tenir en deux lignes dans les marges de securite.

### Filigrane (`watermark`)

Pose le logo livre avec l'application (`assets/branding/watermark.png`) en
semi-transparence sur chaque clip, dans le meme encodage que le reste -- pas de
passe supplementaire.

- **La taille est un pourcentage de la largeur de sortie** (14 % par defaut),
  jamais un nombre de pixels : le logo pese pareil a l'oeil en 1080x1920 et en
  1920x1080. Une taille fixe serait deux fois trop grosse sur l'un des deux.
- **En bas au centre par defaut**, sous les sous-titres. La colonne d'icones de
  TikTok et d'Instagram est a droite, la legende a gauche : le centre bas est la
  seule zone basse que leur interface laisse libre. `position` accepte
  `haut-gauche`, `haut-centre`, `haut-droite`, `bas-gauche`, `bas-centre`,
  `bas-droite` ; le centrage est calcule par ffmpeg (`(W-w)/2`) et reste donc
  juste dans les deux formats.
- **Les sous-titres ne peuvent pas retomber dessus.** Le placement intelligent
  les rapproche du bas quand un visage occupe le cadre, et il n'a aucune raison
  de savoir qu'un logo est pose la : la bande occupee par le filigrane (marge +
  hauteur reelle de l'image + un ecart) devient son plancher.
- **L'opacite multiplie l'alpha existant** (`colorchannelmixer=aa=`) au lieu de
  le remplacer : le bord adouci du disque est conserve, sinon le logo
  ressortirait dans un carre net.
- `image` permet d'en designer un autre. Une image absente **desactive le
  filigrane** au lieu de faire echouer le rendu.

### Interface

**Accueil** : dépose une vidéo, choisis la durée (« Automatique » ajuste chaque clip sur la structure du discours) et le nombre de clips, coche les modules voulus, puis **✨ CRÉER MES MEILLEURS CLIPS**. Les modules cochés ici ne valent que pour cette analyse ; leurs valeurs par défaut se règlent dans Paramètres.

**Résultats** : pour chaque clip, l'aperçu, les trois scores (Viral / Hook / Rewatch), sa catégorie narrative, son titre et sa description, et quatre actions -- **Lire**, **Modifier** (réécrire les titres et la description, avec copie en un clic), **Miniatures** (choisir parmi les trois variantes), **Exporter**. **Exporter tout** copie les clips, leurs miniatures, leurs sous-titres et un fichier texte contenant titres, description et hashtags.

Les titres modifiés à la main sont réécrits dans `metadata/clip_XX.json` **et** dans `results.json` : rouvrir le projet réaffiche bien le texte modifié.

**Paramètres** : les dix modules d’édition automatique s'activent ou se désactivent indépendamment, et les états sont enregistrés avec le projet.

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

**Qualite de telechargement.** La meilleure piste reellement disponible est
prise, y compris les flux VP9 et AV1 que YouTube ne sert pas en MP4 -- c'est ce
que l'ancien reglage refusait sans le dire, plafonnant au mieux a du 1080p
H.264. L'audio reste en AAC, qui se remuxe proprement en MP4 la ou l'Opus des
pistes WebM fait echouer la fusion. Pour plafonner la definition (une source 4K
en AV1 se decode lentement, et le clip produit sort au mieux en 1080 vertical),
renseigner `download.max_height` dans `config/youtube.json` ; `0` signifie
aucune limite.

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

## Options de production

Ces choix se font AVANT de lancer un traitement -- sur la page Accueil, ou en
ligne de commande.

Elles se choisissent a deux endroits : sur la page **Accueil** pour une video de
votre ordinateur, et dans la fenetre du **Radar** avant de produire un clip
trouve. Le meme composant sert aux deux, donc les choix y sont identiques.

| Option | Accueil et Radar | Ligne de commande |
| --- | --- | --- |
| Sous-titres | case "Sous-titres" | `--no-subtitles` |
| Format | menu "Format" | `--aspect 9:16` / `--aspect 16:9` |
| Cadrage intelligent | case "Cadrage intelligent" | `--no-smart-framing` |
| Montage auto | case "Montage auto" | `--no-auto-montage` |
| Filigrane | case "Filigrane" | `--no-watermark` |

- **Sous-titres decoches** : aucun fichier de sous-titres n'est produit, aucun
  n'est incruste. Jusqu'a la version qui introduit ce tableau, la case coupait
  l'export du fichier `.srt` mais les sous-titres restaient incrustes dans
  l'image -- c'etait un defaut, pas un choix.
- **16:9** : l'image d'origine est conservee, sans aucun recadrage. Une source
  qui n'est pas deja au format est completee par des bandes plutot que
  deformee ou rognee. Le cadrage intelligent n'a alors plus d'objet et la case
  se grise : il sert a choisir QUOI garder dans un cadre plus etroit que la
  source, ce qui ne se pose pas ici.
- **Depuis le Radar**, "Produire les clips" lance le decoupage complet sur le
  clip choisi, avec ces options. Le clip est recupere d'abord si besoin. Une
  seule sortie est produite : un clip Twitch est deja court, le decouper en
  cinq morceaux de quelques secondes n'aurait pas de sens. L'analyse du
  contenu, elle, ne produit que du texte -- ce sont deux actions distinctes
  dans la meme fenetre.
- **Le clip du Radar est traite EN ENTIER** (`--whole-source` en ligne de
  commande). Il a deja ete decoupe par quelqu'un : y chercher un "meilleur
  passage" revient a defaire ce choix. Trois etapes disparaissent donc dans ce
  cas -- analyse des hooks, selection des meilleurs passages, detection du
  contexte -- et l'ecran d'avancement ne les affiche plus, puisqu'elles ne
  decideraient rien. Le passage reste NOTE (les notes Viral / Hook / Rewatch
  de la fiche en viennent), mais il n'est plus compare a un autre. Deux
  defauts reels que cela corrige, mesures sur un clip de 24 s : le moteur
  ouvrait une seconde fenetre au tiers du clip et en retenait une qui perdait
  les six premieres secondes ; et un clip de reaction, ou personne ne parle
  vraiment, echouait avec "aucun passage exploitable" parce qu'une fenetre de
  moins de huit mots est ecartee -- filtre utile pour choisir un passage,
  absurde quand il n'y a rien a choisir.
- Ces interrupteurs ne peuvent que DESACTIVER. Un module coupe dans
  `config/editing.json` (page Parametres) ne se rallume pas en cochant une
  case : la configuration reste la source, la case est un interrupteur
  par-dessus, le temps d'un traitement.

## Sortie

```
output/
├── clips/         clip_01.mp4, clip_02.mp4...
├── thumbnails/   clip_01_a.jpg, _b, _c
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

Les tests sont des tests unitaires purs (analyse texte, scoring, selection, analyse audio sur un signal synthetique, decoupage en phrases, recalage du contexte, timeline de montage, structure de sortie, analyse de contenu d'un clip) -- ils ne necessitent ni ffmpeg, ni modele Whisper, ni GPU.

## Limites connues (volontaires)

- La detection de hook est un **score signal**, pas une comprehension semantique -- elle ne remplace pas un montage humain, elle propose des candidats.
- Une video sans piste audio ne peut pas etre traitee (transcription impossible) -- erreur explicite plutot qu'un plantage.
- Le detecteur de visage est un detecteur DNN classique (pas de suivi entre frames) -- suffisant pour centrer un cadrage, pas pour un suivi fluide type camera operator.
- Voir la section 17 du cahier des charges du projet pour les evolutions prevues mais volontairement non implementees en V1 (suivi de visage, detection de scene/rire, interface graphique, apprentissage sur les clips gardes/supprimes...).

## Notes de compatibilite

- **numpy** est fige `<2` : `librosa`/`numba` ont un historique de friction avec numpy 2.x via des dependances transitives. A revisiter si une version future de librosa confirme une compatibilite numpy 2 totale.
- **Pas de dependance PyTorch** dans tout le projet : `faster-whisper` (CTranslate2) et la detection de visage (OpenCV DNN) n'en ont pas besoin -- empreinte disque et memoire reduite.
- **ffmpeg systeme reste necessaire** meme si `faster-whisper` embarque son propre decodeur (paquet `av`/PyAV) : ce dernier ne sert qu'a la transcription. La decoupe, le recadrage et l'incrustation des sous-titres passent par le binaire `ffmpeg` du systeme.
