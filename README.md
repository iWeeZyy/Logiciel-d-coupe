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

### Les compilations Windows se demandent, elles ne partent plus toutes seules

Les deux workflows GitHub Actions ne se declenchent plus a chaque poussee :
ils s'executent a la demande (onglet Actions, bouton « Run workflow »).

La raison est mesurable. Ce depot est prive, donc les minutes d'execution sont
comptees, et un runner Windows est facture DEUX fois le temps reel. Un build
prend environ 22 minutes, soit 44 minutes facturees -- et DEUX workflows
partaient ensemble a chaque commit, celui de la version graphique et celui de
la version en ligne de commande dont personne ne se sert. Trois jours de
travail ont ainsi consomme une centaine de builds et epuise le quota mensuel.

Un installateur ne sert que quand quelqu'un veut l'installer. Pour revenir au
comportement precedent, il suffit de remettre le bloc `push:` retire dans
`.github/workflows/*.yml`.

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

### Voice Studio

Onglet independant : une adresse YouTube en entree, la transcription INTEGRALE
de ce qui est dit en sortie, puis une voix locale lisant le texte choisi.

**Verbatim, jamais un resume.** Le texte affiche est celui qui a ete prononce :
rien n'est resume, reformule, corrige, ni filtre. Les hesitations, les
repetitions et les tics de langage restent. Deux consequences techniques :

- la reconnaissance tourne **sans detecteur de voix** (`vad_filter=False`).
  Le detecteur ecarte des zones jugees muettes AVANT la reconnaissance : utile
  pour chercher un passage a clipper, inacceptable ici, ou un mot mal juge
  disparaitrait sans que rien ne le signale. Le pipeline video, lui, garde le
  detecteur -- son comportement ne change pas ;
- la vue **« version nettoyee »** ne fait que de la MISE EN PAGE (espaces,
  majuscule de debut de paragraphe, paragraphes sur les silences). Elle ne
  remplace jamais la version brute, accessible d'un clic, et un test verifie
  que les deux disent exactement les memes mots dans le meme ordre.

**Trois sources, dans cet ordre** : les sous-titres publies par la chaine, les
sous-titres generes automatiquement par YouTube, puis la transcription locale
par Faster-Whisper. Les deux premieres ne recuperent que du texte deja publie
avec la video. La troisieme demande la piste audio, donc une case a cocher
explicite : sans elle, Voice Studio s'arrete et explique pourquoi.

**La couverture est mesuree et affichee** : nombre de segments, duree de parole
transcrite, instant du dernier mot, duree de la video. Si le texte s'arrete
nettement avant la fin, l'ecran le dit au lieu de laisser croire a une
transcription complete.

**Deux moteurs de voix, tous deux locaux.**

| Moteur | Voix | Installation | Qualite |
| --- | --- | --- | --- |
| Voix du systeme | celles de Windows (Hortense...) | aucune, deja la | correcte |
| Piper | voix neuronales francaises | telechargement a la demande | nettement plus naturelle |

Les voix du systeme passent par `pyttsx3` (SAPI5 sous Windows, espeak-ng sous
Linux) : rien a telecharger, disponibles immediatement. Piper execute
localement un modele `.onnx` que l'utilisateur installe depuis **Gerer les
voix** ; la vitesse et le volume sont geres par le moteur lui-meme
(`length_scale`), sans reechantillonnage ni passage par ffmpeg, donc sans
artefact, et la normalisation evite la saturation.

Dans les deux cas : aucun compte, aucune cle d'API, aucun abonnement, et ni le
texte ni l'audio ne quittent l'ordinateur. Le reseau ne sert qu'a recuperer un
modele, une seule fois, sur demande explicite -- jamais automatiquement.

**Une voix affichee est une voix utilisable.** La liste ne montre que les
modeles reellement installes ; un telechargement interrompu (modele sans sa
configuration) n'y apparait pas. Aucune voix du tout -> le bouton est desactive
et le dit, plutot que d'echouer au moment de generer.

**Le cache evite de resynthetiser.** Meme texte, meme moteur, meme voix, meme
vitesse, meme volume, memes pauses : le fichier deja produit est repris.

**L'apercu ne genere qu'un extrait** (une phrase ou deux), avec le moteur, la
voix et la vitesse reellement choisis -- verifier une voix ne doit pas couter
plusieurs minutes de synthese.

Le clonage de voix n'existe pas ici : reproduire la voix d'une personne demande
son autorisation, que rien dans ce logiciel ne peut verifier.

**Licence, et pourquoi l'application compilee n'embarque pas Piper.** La
bibliotheque Python `piper-tts` est publiee sous GPL-3.0 : l'inclure dans un
executable distribue imposerait ses obligations a toute l'application. Elle
n'est donc PAS dans `requirements.txt`. En developpement, `pip install
piper-tts` suffit et Voice Studio l'utilise directement ; dans l'application
compilee, le gestionnaire de voix propose d'installer le programme `piper`
officiel, appele comme processus separe. Le detail est dans
`docs/voix-piper.md`.

**Ce qui est enregistre**, sous `%LOCALAPPDATA%\ClipFarming\voice_studio_data`
(un fichier JSON par video) : l'adresse, le titre, la chaine, la duree, la
langue, la source du texte, le modele utilise, la transcription avec ses
minutages, les reglages de voix. S'y ajoutent, pour la creation video, le
chemin de la video source, le script de narration, les reglages de rendu et la
transcription de la VOIX GENEREE -- gardee separement de celle de la video, les
confondre ferait afficher les sous-titres de l'une sur l'audio de l'autre.
Rouvrir la meme video propose la transcription deja faite plutot que de la
refaire. Les voix de narration et les apercus vivent dans le sous-dossier
`video_work`, les videos telechargees depuis Recherche dans
`%LOCALAPPDATA%\ClipFarming\videos` (modifiable dans Parametres).

**Réécriture originale.** Une fois la transcription obtenue, Voice Studio peut
en tirer un NOUVEAU script : mêmes informations, formulation, transitions et
souvent structure différentes. Ce n'est pas un outil "anti-plagiat" et rien
dans l'interface ne le presente comme tel -- une reformulation reste une oeuvre
derivee, et l'ecran le dit.

Deux moities, separees a dessein :

- **ce qui se calcule sans modele** : accroche, resultat final, niche, chiffres,
  dates et noms propres avec leur phrase d'origine, repetitions, duree estimee.
  Toujours disponible, meme sans modele installe, et c'est ce qui s'affiche
  alors pendant que la generation reste desactivee ;
- **la generation**, confiee a un modele de langue local execute par
  **llama.cpp**. Aucun appel reseau pendant la generation : le modele est un
  fichier sur le disque. Le telechargement est une etape separee et volontaire.

**Ce qui protege les faits.** La consigne interdit d'inventer, et donne au
modele la liste des chiffres et des noms a conserver -- tiree du texte, jamais
d'ailleurs. Surtout, le resultat est VERIFIE avant d'etre montre : chiffres
perdus, chiffres, dates ou noms qui n'existent pas dans la source, longueur
hors cible. Une information qui semble ajoutee est signalee avec la phrase
concernee, la confiance baisse, et un bouton propose de regenerer avec une
consigne renforcee. Cette detection n'est pas parfaite (une affirmation fausse
sans chiffre ni nom passera) et l'interface ne pretend pas le contraire.

La "difference de formulation estimee" mesure la part des groupes de quatre
mots du script absents de la source. Ce n'est pas une preuve d'originalite
juridique, et elle n'est jamais presentee comme telle.

**Modeles** (catalogue dans `config/rewrite_models.json`, corrigeable sans
reconstruire) : un modele leger 7B et un modele qualite 12B, tous deux en
quantification Q4_K_M et sous licence Apache 2.0. Ils sont ranges sous
`%LOCALAPPDATA%\ClipFarming\voice_studio_data\llm`, jamais dans l'executable.
Avant de proposer un telechargement, l'application lit la memoire disponible et
l'espace disque quand le systeme les donne, et affiche une recommandation ; sans
mesure possible, elle affiche "ressources non mesurables" plutot qu'une
estimation inventee. Un seul modele reste charge en memoire a la fois.

**Le script choisi part vers le systeme de voix DEJA present** : il n'y a pas de
second moteur de synthese, seulement un champ qui se remplit.

**Exports** : TXT (avec ou sans minutages), SRT et VTT. Les deux derniers
passent par `export/subtitles_export.py`, deja utilise par le pipeline video --
un seul formateur de minutage dans tout le projet.

### Montage delire

Des effets francs poses sur les moments forts, avec une case et un cran
d'intensite dans l'accueil. **Desactive par defaut** : c'est un parti pris
esthetique, pas une amelioration, et il n'a rien a faire sur un clip sobre.

Meme partage que les zooms dynamiques : `editing/delire.py` PLANIFIE (module
pur, aucun encodage pour le tester), `video/delire_filters.py` TRADUIT en
filtres ffmpeg.

**LA DUREE NE CHANGE JAMAIS, et c'est la contrainte qui a dessine le reste.**
Les sous-titres sont cales en temps absolu et l'audio aussi : un seul effet qui
allongerait la video les desynchroniserait tous les deux. D'ou l'usage de
l'option `enable='between(t,a,b)'` de ffmpeg, qui active un filtre sur un
intervalle et le laisse transparent ailleurs, plutot qu'un decoupage-recollage.
Verifie par un rendu reel : hors intervalle, l'image produite est
BYTE-IDENTIQUE a un rendu sans effet, et la duree sort a 10.000 s pour 10 s
demandees.

**Ce qui est volontairement absent, et pourquoi :**

- Pas de ralenti, d'arret sur image ni de retour arriere. Ce sont ceux qui
  deplacent la suite. Les ajouter demande une carte de correspondance temps
  source vers temps sortie appliquee aux sous-titres, a l'audio et a l'image a
  la fois. C'est une autre etape, pas un oubli.
- Pas de texte a l'ecran. Le texte lisible de cette application passe par un
  fichier ASS, qui gere les polices correctement sur Windows ; `drawtext` de
  ffmpeg exige un chemin de police en dur. Les punchlines viendront par la voie
  des sous-titres.
- Pas de secousse. Elle demande d'agrandir l'image de quelques pour cent sur
  TOUT le clip pour avoir de la marge, donc de l'adoucir partout pour trois
  dixiemes de seconde d'effet.

**Six effets, tous verifies comme acceptant `enable`** (indicateur « T » dans
`ffmpeg -filters`) : glitch (separation des canaux), deepfry (saturation et
contraste pousses), VHS (grain et chrominance decalee), pixelisation, eclair
blanc, inversion breve. `crop`, `scale` et `zoompan` ne l'acceptent PAS, ce qui
explique l'absence de zoom ici -- il est deja assure par `dynamic_zoom`.

**Le glitch est decoupe en tranches, et ce detail a une histoire.** Premiere
tentative : faire trembler le decalage avec le temps, `rh='18*sin(t*61)'`.
ffmpeg l'a refuse -- `rh` de `rgbashift` est un ENTIER, pas une expression,
contrairement aux parametres de `crop`. L'intervalle est donc coupe en trois
tranches adjacentes a decalages fixes et inegaux : le resultat saute d'une
valeur a l'autre, ce qui est exactement l'effet cherche.

**Trois bornes, comme pour les zooms**, parce qu'un effet toutes les deux
secondes donne une video que personne ne regarde : un nombre d'evenements par
minute, un ecart minimal entre deux, et une part maximale du clip sous effet.
Sur un clip de 45 secondes cela donne 3, 6 ou 10 effets selon le cran, couvrant
de 1,7 % a 6,3 % de la duree. Le budget est la contrainte qui mord ; le
plafond de couverture est un garde-fou.

**Ni les instants ni les effets ne sont tires au hasard.**

Les instants sont ceux que `editing/captions.py` a deja retenus comme
marquants, exactement la source qu'utilise `editing/zoom.py`. Aucun detecteur
n'est ajoute, sinon deux modules pourraient designer des moments differents sur
le meme clip. Aucun moment marquant, aucun effet.

**L'EFFET DECOULE DE CE QUI EST DIT OU FAIT.** A chaque signal correspond une
famille d'effets, et le meme signal donne toujours la meme famille : c'est ce
qui rend le montage lisible plutot que decoratif.

| Signal | D'ou il vient | Famille d'effets |
|---|---|---|
| colere | lexique | glitch, deepfry |
| rire | lexique | deepfry, pixel |
| surprise | lexique | inversion, eclair |
| question | `editing/sentences.py` | glitch, pixel |
| crie | niveau audio nettement au-dessus du seuil du clip | eclair, inversion |
| chiffre | un chiffre dans le mot | pixel, glitch |
| motcle | `config/hooks_keywords.json` | glitch, VHS |
| defaut | rien de reconnu | glitch, VHS, deepfry |

**L'ordre de priorite est explicite, et ce qui est DIT passe avant ce qui est
ENTENDU.** Un « putain ! » hurle est classe en colere, pas en cri. Dans l'autre
ordre le lexique ne servirait jamais, une montee de volume accompagnant presque
toujours un mot fort. Le signal retenu est conserve dans le plan avec le mot qui
l'a declenche : un montage qu'on ne sait pas expliquer ne se corrige pas.

**Comment deux clips evitent malgre tout de se ressembler.** Le sens choisit la
FAMILLE, la graine du clip choisit LE MEMBRE. Et un effet n'est jamais repete
deux fois de suite quand sa famille en propose plusieurs, ce qui evite qu'un
clip entier de rires soit une seule texture. Deux clips dont l'un est fait de
jurons et l'autre de rires ne se ressemblent donc pas du tout ; le meme clip
rejoue donne exactement le meme montage.

**Le cran d'intensite a le dernier mot sur la famille.** Une surprise appelle
un eclair, mais « doux » ne l'autorise pas : on reste alors dans ce que le cran
permet, sinon le cran ne voudrait plus rien dire. Le lexique est surchargeable
par signal dans `config/editing.json` -- redefinir « colere » n'efface pas
« rire ». Ces listes sont des propositions de depart, pas des verites :
personne n'a mesure que le rire appelle la saturation plutot que la
pixelisation.

**Le plan est reproductible.** Une graine derivee du debut du clip par defaut :
deux clips differents n'ont pas les memes effets, mais rejouer le meme clip
redonne le meme resultat -- sans quoi comparer deux reglages serait impossible.
Le plan complet est enregistre dans le manifeste du projet.

**Les effets passent AVANT les sous-titres** dans la chaine : un texte qui
glitche n'est plus lisible, et l'interet d'un sous-titre est qu'on le lise. Le
filigrane est une incrustation posee plus loin encore, donc il reste net aussi.

### Filigrane : deux chaines, un choix dans l'accueil

Le filigrane existait deja, avec sa position, sa taille en pourcentage de la
largeur de sortie et son opacite. Ce qui s'ajoute est un CATALOGUE, lu dans
`config/editing.json`, et un menu dans l'accueil a cote de la case
« Filigrane ».

| Choix | Fichier |
|---|---|
| ClipsOfStreams | `assets/branding/watermark.png` |
| LandsCapesFR | `assets/branding/watermark-landscapesfr-badge.png` |

**Les deux logos sont detoures au meme diametre (512 px), fond transparent,
bord adouci.** Ce n'est pas cosmetique : `size_percent` est un pourcentage de
la largeur de sortie, donc deux logos de diametres differents ne peseraient pas
pareil a l'oeil ; un bord net laisserait un escalier de pixels visible a
l'encodage, et `prepare_filter()` MULTIPLIE l'alpha existant precisement pour
preserver ce degrade au lieu de le carrer. Des tests verifient le diametre,
la transparence des quatre coins et le caractere carre du cadre.

**Une cle inconnue ne donne JAMAIS le logo d'une autre chaine.**
`image_for_choice()` rend une chaine vide plutot que le premier du catalogue :
retomber silencieusement sur le voisin signerait une video du mauvais nom sans
que personne s'en apercoive. C'est `from_config()` qui decide alors de revenir
au logo historique -- un filigrane a bien ete demande, mieux vaut celui-la que
pas de filigrane.

**Trois sources, dans l'ordre :** `image` designee a la main (elle
court-circuite tout, c'est ce qui permet un logo hors catalogue), puis
`choice`, puis le defaut historique. Un utilisateur qui n'a rien choisi
retrouve donc exactement ce qu'il avait avant.

**Deux badges LandsCapes coexistent, volontairement.** Le menu propose le badge
COMPLET (le disque bleu nuit avec le nom et « voyage · nature · france »).
`watermark-landscapesfr.png`, le medaillon interieur seul sans texte, reste
livre et sert a Voice Studio : basculer l'un sur l'autre ne demande qu'un
changement de chemin dans `config/editing.json`.

**Le filigrane est le seul module a porter plus que son interrupteur.** Les
surcharges venues de l'accueil etaient un booleen par module ; elles acceptent
desormais AUSSI un dictionnaire, fusionne dans le bloc du module. Les reglages
non mentionnes survivent, et un booleen continue de ne toucher que `enabled` --
sinon les autres cases auraient cesse de fonctionner.

### Voice Studio ZeroGPU — banc d'essai (optionnel, isole)

**CE QUI EST SUR, ET CE QUI NE L'EST PAS.** Cette section melange des faits de
nature differente ; les confondre serait la faute la plus couteuse ici. Chaque
affirmation ci-dessous porte donc une marque :

| Marque | Sens |
|---|---|
| **[VERIFIE]** | Lu dans le code source officiel ou la documentation officielle, a la date indiquee. |
| **[TESTE]** | Execute et verifie par la suite de tests de ce depot, sans reseau. |
| **[ESTIME]** | Un calcul a partir de chiffres publies. Ce n'est PAS une mesure. |
| **[NON TESTE]** | Jamais execute : `huggingface.co` est injoignable depuis l'environnement de developpement. Seul le PC de l'utilisateur peut le confirmer. |

Sont **[NON TESTE]**, en bloc : la connexion reelle a un Space, la vitesse, la
duree de la file d'attente, le temps de reveil d'un Space endormi, la
consommation reelle du quota, la qualite de la voix produite, et la
comparaison chiffree avec le Chatterbox local et Piper. C'est precisement ce
que le journal de banc d'essai existe pour recueillir.


Une page separee, « ⚡ Voice Studio ZeroGPU », qui execute LE MEME Chatterbox
Multilingual V3 sur un GPU distant (Hugging Face ZeroGPU) au lieu du
processeur, pour repondre a une seule question chiffree : est-ce que cela vaut
mieux que le local sur cette machine.

**Ce n'est pas un quatrieme moteur. [TESTE]** Il n'apparait pas dans la liste des voix,
n'ecrit dans aucun projet, n'utilise pas le cache de `tts.py`. Le Voice Studio
normal (SAPI, Piper, Chatterbox local, transcription, exports, video) ne le
connait pas : `tests/test_zerogpu.py::TestIsolation` verifie qu'aucun module
existant ne l'importe, et que son empreinte dans `gui/main_window.py` tient en
trois lignes. Le supprimer, c'est effacer les fichiers `zerogpu_*` et ces trois
lignes ; rien n'est a restaurer.

**Le jeton Hugging Face suit le mecanisme de la cle YouTube [TESTE]**, pas un second
systeme de secrets : variable d'environnement `HF_TOKEN` d'abord, sinon un
fichier `huggingface_token.txt` dans le dossier de donnees de l'utilisateur.
Jamais dans le code, jamais dans le depot, jamais dans l'executable. Sans
jeton, l'appel reste possible sous le quota anonyme, ce que l'ecran annonce.

**Le decoupage est a 300 caracteres, et ce chiffre n'est pas prudentiel. [VERIFIE]**
`multilingual_app.py`, dans le depot officiel de Chatterbox, fait
`text_input[:300]` SANS RIEN DIRE : un morceau plus long perdrait sa fin en
silence. C'est pourquoi la limite est propre a ce fichier de configuration et
non partagee avec le Chatterbox local, qui coupe a 320. Les regles de coupe,
elles, sont empruntees telles quelles a `chatterbox_catalogue.chunks()` : entre
phrases d'abord, apres une virgule ensuite, entre deux mots en dernier recours,
jamais a l'interieur d'un mot. Les morceaux sont recolles en UN fichier, avec
un court silence aux jointures, avant toute transcription.

**L'API du Space est decouverte, pas devinee. [VERIFIE] + [TESTE]** Le depot officiel n'attache
aucun `api_name` a son bouton et le fichier reellement deploye sur le Space
n'est pas lisible depuis un depot public : ecrire un endpoint en dur serait une
supposition, et une supposition fausse enverrait le script dans le champ
« temperature ». L'application lit donc `view_api()` a la connexion et associe
ses valeurs aux parametres par leur nom, avec repli sur le libelle affiche
puis sur l'ordre declare dans `config/zerogpu.json`. **Si le champ du texte
ne peut etre reconnu, AUCUN appel n'est envoye** : `check_arguments()` leve
une erreur nommant les parametres reellement publies. Sans ce garde-fou,
l'appel serait parti avec un texte vide, aurait consomme du quota GPU et
serait revenu avec la voix par defaut du Space lisant son propre exemple --
le plus couteux des echecs silencieux. [TESTE]

**Attente et generation sont mesurees separement [NON TESTE en reel]**, parce qu'elles ne disent
pas la meme chose : la file d'attente depend de la charge de Hugging Face et du
niveau de compte, la generation depend du modele. Les confondre rendrait toute
comparaison avec le local trompeuse, le local n'ayant pas de file. Chaque essai
est ajoute au journal `zerogpu_benchmark.jsonl` (mots, duree d'audio, attente,
GPU, total, RTF), en JSON Lines pour qu'une ligne s'ajoute sans relire le
fichier et qu'une troncature ne coute que la derniere ligne.

**Quotas annonces par Hugging Face [VERIFIE au 2026-09-10]**, lus dans `huggingface/hub-docs` le
2026-09-10 et repris dans `config/zerogpu.json` : 2 minutes de GPU par jour
sans compte, 5 minutes avec un compte gratuit, 40 minutes avec PRO, sur une
moitie de NVIDIA RTX Pro 6000 Blackwell. Un compte gratuit peut heberger deux
Spaces ZeroGPU. Ces chiffres bougent : ils sont AFFICHES comme un rappel date,
jamais utilises pour calculer ou bloquer quoi que ce soit. La duree maximale
d'un appel GPU n'est pas chiffree par la documentation officielle -- seule la
duree par defaut (60 s) l'est -- donc l'application ne la teste pas et se
contente de traduire un refus du serveur.

**Un ordre de grandeur, et rien de plus. [ESTIME]** Un script de 500 mots
represente environ 3 min 20 d'audio. Si Chatterbox tenait sur ce materiel le
facteur temps reel annonce par Resemble pour un H100, cela couterait de
l'ordre de 40 s de quota, soit trois a cinq scripts par jour sur un compte
gratuit. C'est un calcul enchaine sur deux hypotheses, pas un resultat : le
materiel n'est pas le meme, et rien n'a ete mesure. Le journal de banc d'essai
remplacera ce paragraphe par des chiffres.

**De la narration a la video, sans que la video connaisse Hugging Face.
[TESTE]** Le bouton « Utiliser dans la création vidéo » envoie le WAV produit
vers le panneau de creation video. La couture existait deja :
`VideoRequest.narration_wav` sert depuis toujours a reutiliser une voix quand
seul le cadrage ou le style a change, et une narration venue d'ailleurs entre
par la meme porte -- `voice_studio/video_service.py` n'a donc pas change d'une
ligne. Le panneau expose `use_external_narration(wav, script, langue)`,
volontairement generique : il recoit un fichier, un texte et une langue, et ne
nomme ni Hugging Face ni ZeroGPU, ce qu'un test verifie ligne a ligne. La page
ZeroGPU, elle, se contente d'emettre un signal ; c'est la fenetre principale
qui aiguille, comme elle le fait deja pour Recherche vers Voice Studio.

Deux pieges gardes par des tests, parce qu'ils ne se voient pas : la SIGNATURE
qui decide de jeter une narration obsolete devait cesser de dependre de la voix
(sinon le premier rendu jetait la narration qu'on venait d'adopter, et changer
de voix aurait detruit un fichier que la voix ne produit pas), et la LANGUE
devait etre imposee explicitement -- une narration externe n'a pas de voix d'ou
la deduire, et sans elle Whisper traduirait, la faute exacte qui avait deja
donne des sous-titres anglais sur un script francais.

**Rien n'est payant sans action explicite.** Le depassement de quota chez PRO
se paie en credits prepayes ; l'application n'a aucun mecanisme de paiement et
n'en aura pas.

#### Chatterbox — la voix expressive (optionnelle)

Troisieme moteur de synthese, a cote des voix du systeme et de Piper. Il est
propose dans la meme liste, il produit le meme WAV, et il traverse le meme
enchainement ensuite (Faster-Whisper, sous-titres, video) : ce n'est pas un
second systeme de voix.

**Ce qui est utilise, exactement.** `ChatterboxMultilingualTTS` du depot
officiel `resemble-ai/chatterbox`, avec `t3_model="v3"` (poids
`t3_mtl23ls_v3.safetensors`) et `language_id="fr"`. Le paquet publie sur PyPI
(0.1.7) n'expose PAS le choix du modele et retomberait silencieusement sur la
V2 : l'installation vise donc le depot lui-meme, epingle sur un commit precis
ecrit dans `config/chatterbox.json`. Licence du code : MIT.

**Il vit dans un environnement Python separe**, installe a la demande, jamais
embarque dans l'executable. Chatterbox epingle `torch==2.6.0`,
`transformers==5.2.0` et `diffusers==0.29.0` -- des versions exactes qui
figeraient tout le projet, et pres de 195 Mo pour la seule roue PyTorch de
Windows. L'application, elle, n'a aucune dependance PyTorch : la transcription
passe par CTranslate2, et cela ne change pas. Consequence directe : elle
demarre et fonctionne sans Chatterbox, et une casse de son cote ne peut pas
empecher Piper ou Faster-Whisper de fonctionner.

**Deux telechargements distincts et INDEPENDANTS, tous deux declenches par un
bouton** dans « Installer / gerer Chatterbox » : l'environnement (Python +
PyTorch + Chatterbox) et les poids du modele. L'ordre n'a aucune importance --
les poids sont recuperes par le telechargeur de l'application, qui n'a pas
besoin de l'environnement -- mais les deux sont necessaires pour generer.

**Git n'est PAS necessaire, et il a fallu s'y reprendre a deux fois.** Le
catalogue propose d'abord deux sources pour le meme commit, essayees dans
l'ordre : une archive du depot, qui s'installe avec pip seul, puis le depot
git. Cela reglait un echec reel -- PyTorch installe, puis « Cannot find command
'git' » a la derniere etape -- mais pas tout : pip lisait ensuite les
dependances declarees par le depot, dont
`resemble-perth @ git+https://github.com/resemble-ai/Perth.git@master`. Une
adresse git dans une dependance redemande git, meme quand la source principale
n'en demande plus. D'ou le fonctionnement actuel : les dependances sont
installees DEPUIS PyPI (`library_packages` dans `config/chatterbox.json`,
`resemble-perth` y existe sous le numero 1.0.1), puis la bibliotheque est posee
avec `--no-deps`. `gradio` est volontairement absent de cette liste : le depot
le declare, mais c'est l'interface web de ses demos, jamais importee par la
bibliotheque.

**Si Python n'est pas detecte alors qu'il est installe**, deux causes connues,
toutes deux traitees : une variable PATH mise a jour n'atteint pas un programme
deja lance (redemarrer l'application suffit), et Windows pose dans
`WindowsApps` des fichiers de zero octet qui ouvrent le Microsoft Store au lieu
de lancer Python (ils sont ignores). En dernier recours, « Choisir
python.exe… » designe l'interpreteur directement, et le choix est conserve. La
fenetre affiche toujours ou elle a cherche. Les poids passent par le telechargeur
deja utilise pour les voix Piper -- reprise apres coupure, annulation,
verification de l'espace disque. Rien ne part au demarrage de l'application.

**Reglages exposes** (les noms techniques sont ceux du modele) :

| Dans l'interface | Parametre reel | Defaut |
|---|---|---|
| Style | prereglage (Naturel, Storytelling, Dynamique, Calme, Shorts) | Naturel |
| Expressivite | `exaggeration` | 0,5 |
| Rythme | `cfg_weight` | 0,5 |
| Temperature (avance) | `temperature` | 0,8 |
| Graine (avance) | `torch.manual_seed` | aleatoire |
| Voix | integree, ou fichier de reference | integree |

Les prereglages sont des points de depart documentes dans
`config/chatterbox.json`, pas des optima : ils n'ont pas ete compares a
l'oreille. Tout reste modifiable a la main.

**Ce que ce moteur ne sait pas faire, et qui est dit plutot que masque** : il
n'expose aucun reglage de debit, donc le curseur de vitesse est grise quand il
est choisi. Il ne propose qu'UNE voix integree par langue -- il n'existe pas de
catalogue de voix dans le modele ; pour en changer, il faut fournir son propre
fichier audio de reference, choisi a la main, jamais recupere automatiquement.

**Textes longs.** La bibliotheque genere au plus 1000 jetons de parole par
appel (environ 40 s) : au-dela, la fin du texte ne serait tout simplement pas
prononcee. Un script est donc decoupe entre les phrases -- a defaut apres une
virgule, jamais a l'interieur d'un mot -- puis les morceaux sont concatenes en
une seule narration continue.

**Filigrane.** Chaque audio genere porte un filigrane inaudible
(`resemble-perth`), applique sans condition par la bibliotheque officielle. Il
n'impose aucune restriction d'usage et n'est pas contourne.

#### Creer une video narree

Dernier bloc de la page : une video, un script de narration, et un MP4 en
sortie. La video vient de l'onglet **Recherche** (bouton « Telecharger », puis
« Ouvrir dans Voice Studio ») ou d'un fichier deja present sur le disque.

**Les sous-titres disent le SCRIPT, aux instants de la VOIX.** Le fichier audio
produit par la synthese est repasse dans Faster-Whisper -- le meme moteur que le
reste de l'application -- pour savoir QUAND chaque mot est prononce. Rien n'est
estime a partir du nombre de mots ou de caracteres : une voix qui marque une
pause, allonge un chiffre ou avale une liaison reste synchrone. La duree de la
narration est mesuree sur le fichier (ffprobe), jamais calculee.

Le TEXTE, lui, n'est pas celui que Whisper a entendu : `voice_studio/align.py`
apparie les mots reconnus avec ceux du script et garde ceux du script. Whisper
ecrit ce qu'il entend -- un mot approche, un nom propre defigure -- alors que le
texte est ici connu d'avance. Un mot que la reconnaissance n'a pas retrouve voit
son instant interpole entre les deux mots surs qui l'encadrent, au prorata de sa
longueur, et l'ecran affiche combien de mots ont ete reellement retrouves : une
precision qu'on n'a pas n'est jamais presentee comme acquise.

**La langue de la narration vient de la voix, jamais de la video source.** Defaut
reel rencontre : la langue choisie en haut de la page (celle de la video a
transcrire, anglaise) etait aussi annoncee a Whisper pour la narration
francaise. Forcer une langue que l'audio ne parle pas ne produit pas une
transcription dans cette langue, cela fait TRADUIRE Whisper -- et les
sous-titres sortaient en anglais sur un script francais. La langue est
maintenant deduite de la voix choisie, et laissee en detection automatique
quand la voix ne la declare pas.

**Le filigrane n'est pas celui des clips.** Une video narree n'est pas publiee
sous le meme nom qu'un clip : Voice Studio pose
`assets/branding/watermark-landscapesfr.png`, le rendu des clips garde
`watermark.png`. Seule l'image change -- position, marge, taille et opacite
viennent du meme bloc de configuration, un seul jeu de reglages a tenir a jour.
`voice_studio_image`, `voice_studio_size_percent` et `voice_studio_opacity`
dans `config/editing.json` permettent de regler le logo de Voice Studio
separement si besoin, sans toucher a celui des clips. La bande basse qu'il
occupe reste le plancher des sous-titres, qui ne peuvent donc pas lui passer
dessus.

**Aucun nouveau systeme.** La voix vient du bloc « Generer une voix » juste
au-dessus (meme moteur, meme voix, meme vitesse, meme cache) ; les styles de
sous-titres sont ceux de `config/subtitles.json`, decoupes par
`editing/captions.py` et rendus par `video/subtitle_renderer.py` ; le recadrage
9:16, le remplissage flou du 16:9 et le filigrane sont ceux du rendu des clips.
Un seul encodage ffmpeg.

**Les quatre decisions, toutes explicites** :

| Choix | Ce qui se passe |
|---|---|
| Format | `16:9` garde l'image entiere et remplit les bords (flou ou noir) ; `9:16` recadre (au centre ou en suivant le visage detecte), ou garde l'image entiere et remplit le haut et le bas. |
| Son | remplacer par la narration, garder celui de la video, ou melanger (le son d'origine est *baisse*, pas supprime). |
| Duree | couper au plus court, garder toute la video (silence apres la narration), ou figer la derniere image si la narration est plus longue. |
| Sous-titres | incrustes ou non, dans le style choisi, avec le filigrane par-dessus ou non. |

Rien n'est jamais accelere ni ralenti pour faire coincider deux durees : etirer
une voix ou une image s'entend et se voit. Une video muette melangee a la
narration n'est pas presentee comme un melange -- le mode reellement applique
est annonce.

**Ce qui n'est pas recalcule** : changer le style de sous-titres, le cadrage, le
son ou la duree ne re-synthetise pas la voix et ne relance pas son analyse. La
voix et ses minutages sont gardes tant que le script, la voix ou ses reglages
n'ont pas change. L'apercu (15 s) est le MEME rendu, simplement plus court : ce
qu'on voit est ce qui sortira.

**Sous-titres en 16:9** : les styles sont calibres pour un cadre 1080x1920. En
paysage, la toile ASS vaut la taille de sortie et les valeurs en pixels
(police, contour, marge) sont mises a l'echelle de la hauteur reelle -- sans
cela le texte serait etire horizontalement et passerait derriere le logo, ce
qu'un premier rendu a effectivement montre.

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

**Suspendre une chaine la retire de la liste**, pas seulement des scans. Le
scan sautait deja les chaines suspendues, mais leurs contenus deja collectes
restaient affiches et comptes : suspendre une surveillance et continuer a voir
ses clips, c'est ne pas l'avoir suspendue. Rien n'est supprime pour autant --
reactiver la chaine fait tout revenir. A ne pas confondre avec RETIRER un
createur de la liste, qui laisse volontairement son historique consultable.

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
| Videos completes telechargees | `%LOCALAPPDATA%\ClipFarming\videos` (modifiable dans Parametres) | oui |
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

**Quatorze styles** dans `config/subtitles.json`, tous personnalisables : police, taille, couleurs, contour, ombre, position, animation, mots par groupe, longueur de ligne.

| Style | Ce qu'il fait |
|---|---|
| `dynamic` (defaut) | deux mots tres grands, leger effet d'apparition sur le mot important |
| `bold` | trois mots, contour epais, mot important en ambre -- le passe-partout |
| `minimal` | discret, minuscules, contour fin, aucune animation |
| `podcast` | quatre mots lisibles longtemps, places plus haut |
| `gaming` | tres contraste, accent jaune, contour tres epais |
| `progressive` / `big_text` | deux mots / un seul mot a la fois, styles historiques |
| `classic` | sous-titres par phrase, remplissage karaoke |
| `karaoke` | idem en gros, gras et majuscules, remplissage dore |
| `marqueur` | le mot-cle surligne au marqueur, sur une pastille pleine |
| `neon` | lueur floutee autour du texte |
| `ressort` | le mot important depasse sa taille puis se pose |
| `secousse` | le mot important tremble une demi-seconde |
| `machine` | le texte s'ecrit lettre par lettre, au rythme reel de la parole |

Les styles historiques (`progressive`, `big_text`, `classic`, `bold`, `dynamic`, `minimal`, `podcast`, `gaming`) produisent **exactement** le meme fichier `.ass` qu'avant ces ajouts, verifie par comparaison d'empreintes.

**Six animations** : `none`, `fade`, `pop`, `bounce`, `shake`, `glow`. Elles portent sur LE MOT mis en evidence, jamais sur la ligne entiere -- faire trembler tout un bloc rend la lecture penible. `fade` est la seule exception, par nature : elle habille la ligne.

**Deux pieges verifies au rendu**, contre ce qu'on lit souvent :

- `border_style: 3` (rectangle opaque) est peint par libass avec la couleur de **contour**, pas avec `back_color`, et couvre la **ligne entiere**. Le surlignage au marqueur d'un seul mot passe donc par un contour epais pose sur ce mot (`emphasis_marker`), pas par ce reglage.
- le tag ASS `\k` fait passer le texte de la couleur **secondaire** a la couleur **primaire**. `highlight_color` habille donc le texte **pas encore dit**, et `primary_color` le texte **deja dit** : pour un karaoke qui se remplit d'une couleur d'accent, c'est l'accent qui va dans `primary_color`.

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

**Telecharger une video complete (interface graphique).** La page Recherche a un
bouton « ⬇ Telecharger » sur chaque resultat : il recupere la video ENTIERE,
sans transcription, sans scoring et sans decoupage -- une video complete sert a
autre chose qu'a produire des clips, et lui faire traverser l'analyse couterait
plusieurs minutes de calcul pour un resultat dont on ne veut pas. La qualite
(meilleure disponible par defaut) et le dossier de destination sont demandes
avant, la progression est affichee en megaoctets et le transfert s'annule. Le
verrou de consentement est le meme que pour l'analyse (voir ci-dessous). A la
fin, la video peut partir directement dans Voice Studio pour y poser une
narration.

Le tri `--sort potential` utilise le **Video Potential Score** (`youtube/ranking.py`) : pertinence + popularite (vues, echelle log) + duree exploitable (combien de clips tiennent dedans) + un proxy de qualite tres faible base uniquement sur la duree. **Ce n'est pas le Hook Score** (`config/settings.json`) : le premier note une video entiere avant tout telechargement a partir de simples metadonnees, le second note un passage precis apres transcription reelle. Les deux ne sont jamais additionnes.

### ⚠️ Droits d'utilisation -- a lire avant d'utiliser `--youtube`

**Trouver une video ne donne aucun droit de la reutiliser.** Deux points distincts, a ne pas confondre :

- Telecharger une video par un autre moyen que le bouton de telechargement officiel de YouTube **viole les conditions d'utilisation de YouTube** -- y compris pour une video marquee Creative Commons. Une licence Creative Commons porte sur le *contenu* (droit d'auteur), elle ne donne aucun droit vis-a-vis de la *plateforme* YouTube elle-meme.
- Republier ou monetiser un extrait sans les droits necessaires (accord du createur, licence explicite hors YouTube, contenu dont tu es l'auteur...) est une question de droit d'auteur, separee de ce qui precede.

Le filtre `--yt-creative-commons` est **indicatif**, pas une garantie juridique -- l'information vient de YouTube telle quelle. `--youtube` refuse d'agir sans `--confirm-rights`, qui n'est qu'une confirmation de ta part : le logiciel ne verifie ni ne peut verifier tes droits reels.

### Garder toute la video, au lieu d'en tirer des clips

Une case sur la page d'accueil : « Garder toute la video (une seule sortie,
sans decoupage) ». La video est alors traitee en entier, en UNE sortie, avec les
memes sous-titres, le meme cadrage, le meme montage et le meme filigrane qu'un
clip -- seule la recherche d'un passage disparait.

Ce n'est pas un deuxieme mode de traitement : c'est le chemin `whole_source`
que le Radar utilise deja pour un clip Twitch, qui est deja un clip decoupe.
Une seule fenetre d'analyse, aucun filtre sur le nombre de mots, aucune
detection de contexte -- elle deplacerait des bornes qu'on ne veut pas
deplacer. En ligne de commande, c'est `--whole-source`.

Quand la case est cochee, la duree des clips et leur nombre sont grises : ces
deux decisions n'ont plus d'objet, et les laisser actives laisserait croire
qu'elles comptent encore.

### Qualite d'image : ou elle se perd vraiment

La question posee etait : peut-on gagner en qualite quand un zoom est fait ?
La reponse mesuree est oui, mais pas la ou on l'attend.

**Le plus gros facteur n'est pas le zoom, c'est l'agrandissement.** La fenetre
9:16 d'une source 1280x720 ne fait que 404x720 : il faut l'agrandir 2,67 fois
pour atteindre 1080x1920. Une source 1080p donne 606x1080, soit 1,78 fois. Le
telechargement prend deja la meilleure qualite disponible, sans plafond de
hauteur -- c'est le premier levier, et il est deja tire.

**Le levier trouve : l'algorithme de redimensionnement.** ffmpeg utilise
bicubique par defaut ; la chaine demande maintenant lanczos, qui conserve mieux
les hautes frequences.

| | bicubique | lanczos |
|---|---|---|
| nettete, source 720p | 4,94 | **5,28** |
| nettete, source 1080p | 4,96 | **5,33** |
| encodage d'un clip de 10 s | 12,4 s | 12,4 s |
| taille du fichier | 5,62 Mo | 5,80 Mo |

La nettete est l'energie des hautes frequences de la sortie (moyenne du
gradient absolu), mesuree contre un maitre 3840x2160 a detail fin. Le gain est
le meme a zoom 1.0, donc il porte sur TOUT le clip et pas seulement sur les
zooms. Le temps d'encodage ne bouge pas : le redimensionnement est negligeable
devant x264. Le SSIM baisse legerement (0,882 -> 0,872), signature connue de
lanczos qui garde le detail au prix d'un leger rebond sur les contours ; la
nettete mesuree et l'inspection visuelle vont dans l'autre sens, et c'est ce
qui a decide.

**Le second levier : compenser l'agrandissement.** Aucun interpolateur ne cree
le detail qui manque, donc l'image ressort adoucie. Un masque flou leger
(`unsharp`) en recupere une partie -- et ce n'est pas du maquillage, parce que
la mesure ne compare pas la sortie a elle-meme mais a la VERITE : le maitre
3840x2160 dont la source a ete tiree. Si le filtre rapproche la sortie de
l'image vraie, il recupere du detail reel.

| agrandissement | force optimale | gain en PSNR |
|---|---|---|
| x1,78 (source 1080p) | 0,20 a 0,35 | +0,17 / +0,22 dB |
| x2,67 (source 720p) | 0,50 a 0,80 | +0,30 / +0,20 dB |
| x3,58 (source 540p) | 0,80 a 1,00 | +0,26 / +0,19 dB |
| x4,46 (source 432p) | 0,80 a 1,00 | +0,14 / +0,08 dB |

Les deux chiffres sont les deux maitres de test. La force optimale **croit**
avec l'agrandissement, d'ou une loi proportionnelle (`0,35 x (agrandissement -
1)`, plafonnee a 0,9) plutot qu'une valeur fixe : une force forte sur un faible
agrandissement coute -0,54 dB. **Sans agrandissement, aucun filtre n'est
pose** -- sur une source deja a la taille de sortie le masque flou mesure
-65 dB, l'image n'est plus elle-meme. La chrominance reste intacte, et le fond
flou du cadrage « image entiere » n'est jamais accentue. Cout mesure sur un
clip de 10 s aux reglages de production : encodage 8,0 -> 8,9 s (+11 %),
fichier 0,96 -> 1,05 Mo (+9 %).

Le risque connu de ce filtre est d'amplifier le bruit autant que le detail, et
un stream sombre est granuleux la ou un maitre de synthese est propre. Du bruit
a donc ete ajoute a la source, en mesurant toujours contre la verite propre :
le gain reste positif et diminue seulement -- +0,29 dB sans bruit, +0,28 avec
un bruit leger, +0,23 moyen, +0,11 fort. Le reglage n'a pas eu a etre abaisse.

#### Ce qui a ete essaye et ecarte, avec la mesure qui l'a decide

Le zoom fait subir a l'image **deux** redimensionnements au lieu d'un :
l'etage, puis la fenetre choisie par `zoompan`. Quatre pistes ont ete testees
pour n'en faire qu'un.

- **Un seul passage, en pilotant les dimensions du recadrage par `sendcmd`.**
  `crop` accepte des commandes sur sa largeur et sa hauteur (drapeau `T`), donc
  on peut recadrer a la resolution source puis ne redimensionner qu'une fois.
  **Ca fonctionne et ca atteint exactement le plafond theorique** (25,9 dB
  contre 23,0). Ecarte pour son cout : changer une dimension en cours de flux
  force `ffmpeg` a reconfigurer le graphe a chaque image, ce qui rend le rendu
  **au moins 70 fois plus lent** (un clip de 10 s n'avait pas fini apres
  10 minutes, contre 8 s) et finit par se bloquer.
- **Surechantillonner l'etage du zoom** (toile 2x ou 3x, retour en lanczos a la
  fin). Nettete inchangee, cout **x4 et x8**. Ecarte.
- **Reduire l'etage a la taille de sortie**, pour que `zoompan` n'agrandisse
  plus que du facteur de zoom : -3 dB. Ecarte.
- **Faire sortir `zoompan` a la taille de son etage** puis reduire en lanczos :
  aucun changement mesurable.

Et surtout, **la perte due au double passage est bien plus petite que ce
qu'elle semblait** : les 2,9 dB d'ecart mesures au depart etaient un decalage
d'UN pixel introduit par `zoompan`, pas de la nettete perdue. Les deux rendus
concordent a 36,6 dB une fois recales, et l'energie des hautes frequences est
identique a 0,5 % pres. La perte reelle est d'environ 0,6 dB, invisible.

Le tremblement du zoom a aussi ete mesure, avec un repere suivi image par
image. Sur une montee lente et artificielle de 4 secondes, 23 images sur 99
reculaient au lieu d'avancer -- `zoompan` tronque en pixels entiers. Mais avec
le **vrai** profil (attaque 0,25 s, tenue 0,5 s, relachement 0,4 s), le
deplacement est de 5 px par image et il n'y a **aucun recul**. Rien a corriger,
donc, et surtout rien qui justifie de payer le surechantillonnage.

L'encodage, lui, etait deja regle par la mesure : CRF 18, preset medium (voir
le commentaire de `config/settings.json`).

#### Ce qui resterait a gagner, et ce que ca couterait

Le seul levier vraiment plus puissant est un **agrandissement par reseau de
neurones** (Real-ESRGAN et equivalents), que les services en ligne utilisent
sur leurs serveurs. C'est ce qui explique l'essentiel de l'ecart quand on
compare a un outil du marche sur une source basse definition. Il n'est pas
utilisable ici en l'etat, pour trois raisons concretes :

- l'`ffmpeg` livre avec l'application (build « essentials » de gyan.dev) n'a
  pas les greffons DNN compiles ; ses filtres `sr` et `dnn_processing` existent
  dans le catalogue mais sans moteur derriere ;
- `nnedi`, l'agrandisseur de bonne qualite qui ne demande pas de moteur DNN,
  a besoin d'un fichier de poids (~13 Mo, qui pourrait etre livre) mais coute
  plusieurs secondes PAR IMAGE sur processeur ;
- un vrai modele de super-resolution demande un telechargement de modele et,
  en pratique, une carte graphique -- soit exactement l'installation
  supplementaire que ce projet s'interdit.

C'est donc un choix a faire, pas un oubli : tant que la contrainte « rien a
installer, tout en local » tient, la compensation de l'agrandissement decrite
au-dessus est le meilleur rapport qualite/cout disponible.

### Remplir le cadre : recadrer ou garder l'image entiere

Une image qui n'a pas la forme du cadre demande une decision, et les deux
reponses ont un usage reel. Le choix est dans les options de production, a cote
du format, sur la page Accueil comme dans le Radar (et en ligne de commande avec
`--fit`).

| Choix | Ce qui se passe | Quand |
|---|---|---|
| `recadrer` (defaut en 9:16) | Une fenetre a la forme du cadre est gardee, le reste est jete. Le cadrage intelligent peut la deplacer pour suivre le sujet. | Un visage dans un plan large : on veut le voir en grand. |
| `entier` | Toute l'image est gardee, mise a l'echelle et centree ; ce qui reste du cadre est rempli par une copie floutee de l'image (ou par des bandes noires). | Un plan large, un paysage, un tableau de jeu : rien ne doit sortir du champ. |

**Le remplissage n'a d'effet que s'il reste quelque chose a remplir.** C'est un
piege reel, rencontre en usage : une source deja en 16:9 rendue en 16:9 occupe
exactement le cadre, il n'y a aucune bande, donc aucun flou visible -- l'option
avait l'air cassee alors qu'elle n'avait rien a faire. Sur un clip Twitch
(1920x1080), le flou ne devient visible qu'en vertical avec `--fit entier` :
c'est la que les bandes existent, et c'est ce que les plateformes remplissent
sinon avec du noir. La case est donc grisee quand elle ne sert a rien, plutot
que laissee cochee sans effet.

**Et le fond n'est plus calcule quand il ne sert a rien.** Le graphe passait
malgre tout par le fond flou : un flou gaussien sur chaque image, puis
integralement recouvert par l'image nette. Un clip DEJA vertical (1080x1920)
produit desormais une simple mise a l'echelle -- 100 images comparees, ecart de
luminance exactement 0, et l'encodage de 2,11 s a 0,65 s sur la mesure faite ici.
La condition est une egalite ENTIERE des formats, volontairement stricte : c'est
ce qui garantit qu'aucune bande d'un pixel n'apparait par arrondi. Elle couvre
toutes les resolutions verticales reelles (1080x1920, 720x1280, 540x960...) ; un
format seulement proche (1080x1918) garde l'ancien chemin. Voice Studio partage
cette geometrie, donc l'economie aussi.

Rien n'est jamais deforme : dans les deux cas la proportion d'origine est
preservee. On choisit seulement ce qu'on perd (recadrer) ou ce qu'on ajoute
(remplir). Quand l'image entiere est gardee, le cadrage intelligent est coupe
automatiquement -- il n'y a plus de fenetre a deplacer -- et la detection de
visages n'est meme pas lancee.

## Options de production

Ces choix se font AVANT de lancer un traitement -- sur la page Accueil, ou en
ligne de commande.

Elles se choisissent a deux endroits : sur la page **Accueil** pour une video de
votre ordinateur, et dans la fenetre du **Radar** avant de produire un clip
trouve. Le meme composant sert aux deux, donc les choix y sont identiques.

| Option | Accueil et Radar | Ligne de commande |
| --- | --- | --- |
| Style de montage | menu "Style de montage" | -- (il compose les options ci-dessous) |
| Sous-titres | case "Sous-titres" | `--no-subtitles` |
| Format | menu "Format" | `--aspect 9:16` / `--aspect 16:9` |
| Fond flou en 16:9 | case "Fond flou" | `--black-bars` pour l'inverse |
| Cadrage intelligent | case "Cadrage intelligent" | `--no-smart-framing` |
| Montage auto | case "Montage auto" | `--no-auto-montage` |
| Filigrane | case "Filigrane" | `--no-watermark` |

### Styles de montage

Un clip reussi n'est pas la somme de reglages independants : des sous-titres de
deux mots tres grands appellent un cadrage serre et des zooms francs, un clip
narratif appelle l'inverse. Le menu **Style de montage** pose une combinaison
coherente d'un seul coup -- format, cadrage, style de sous-titres, ampleur et
nombre des zooms, intensite du delire.

| Style | Sous-titres | Format | Zooms | Delire |
| --- | --- | --- | --- | --- |
| Personnalise | ceux des Parametres | inchange | inchanges | inchange |
| Punchline | `dynamic` | 9:16 recadre | francs (x1.14, 6 max) | doux |
| Recit | `podcast` | 9:16 image entiere | a peine perceptibles (x1.05, 3 max) | aucun |
| Surligne | `marqueur` | 9:16 recadre | mesures | aucun |
| Karaoke | `karaoke` | 9:16 recadre | mesures | aucun |
| Neon | `neon` | 9:16 recadre | appuyes (x1.10, 5 max) | doux |
| Chaos | `secousse` | 9:16 recadre | nombreux (x1.18, 8 max) | maximum |
| Sobre | `minimal` | **inchange** | aucun | aucun |

Trois choix de conception :

- **Rien n'est verrouille.** Le style regle les commandes POUR DE VRAI, et
  elles restent visibles et modifiables : on voit ce qui va se passer. Toucher
  l'une d'elles fait repasser le menu sur « Personnalise » plutot que de
  laisser afficher le nom d'un style qui ne decrit plus la sortie.
- **Un style ne coupe aucun mecanisme.** Il ne touche qu'au delire et aux
  zooms. Eteindre les sous-titres, le cadrage intelligent ou les miniatures
  n'est pas un parti pris esthetique, et « Sobre » coupe les zooms sans couper
  le montage automatique -- qui porte aussi la coupe des silences et la
  normalisation du son.
- **« Sobre » ne dit rien du format**, volontairement : un clip sobre se
  justifie aussi bien en vertical qu'en horizontal, et trancher a la place de
  l'utilisateur serait une perte d'information plutot qu'un service.

**Disponible aux deux endroits** : sur la page Accueil et dans la fenetre du
Radar, apres avoir selectionne un clip -- c'est le meme composant d'options,
donc les deux chemins proposent exactement les memes styles.

**Sur une source deja verticale**, les huit styles sortent en 1080x1920 sans
recadrage inutile ni bande : verifie par rendu reel des huit, taille de sortie,
duree exacte et sous-titres incrustes. Un clip PLUS haut que le 9:16
(1080x2400, par exemple) est le seul cas ou « recadrer » rogne le haut et le
bas ; « Recit » le garde alors entier.

Le menu n'a pas d'equivalent en ligne de commande : il ne fait que composer des
reglages qui y sont deja accessibles un par un (`--subtitle-style`, `--aspect`,
`--fit-mode`), a l'exception de l'ampleur des zooms et du cran de delire, qui se
reglent dans `config/editing.json`.

- **Sous-titres decoches** : aucun fichier de sous-titres n'est produit, aucun
  n'est incruste. Jusqu'a la version qui introduit ce tableau, la case coupait
  l'export du fichier `.srt` mais les sous-titres restaient incrustes dans
  l'image -- c'etait un defaut, pas un choix.
- **Fond flou (16:9)** : une source verticale dans un cadre horizontal laisse
  des bandes. Le fond flou les remplit avec une COPIE de l'image elle-meme,
  agrandie pour couvrir le cadre puis floutee ; l'image nette reste entiere et
  centree par-dessus. Rien n'est rogne, rien n'est deforme, et rien n'est
  invente -- ce qui remplit les bords est l'image, pas une image d'ailleurs.
  Actif par defaut ; `--black-bars` (ou la case decochee) rend les bandes
  noires. Sans objet en 9:16, ou le recadrage remplit deja le cadre : la case y
  est grisee.
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
