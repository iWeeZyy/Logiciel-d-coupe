#!/usr/bin/env bash
# ffmpeg (libass) et polices Arial / Arial Black des sous-titres, pour les
# workflows. Les polices ne passent plus par ttf-mscorefonts-installer : son
# telechargement depuis SourceForge pendait sans fin (40 min le 2026-11-06,
# puis la limite de 15 min atteinte le 2026-11-11). Ici : telechargement
# direct borne dans le temps, plusieurs essais, et cache (~/.fonts) entre les
# runs (grep sans -q : avec pipefail, -q coupe fc-list et fausse le test).
# Sans elles, avertissement et police de repli -- jamais de run bloque.
# Usage : install_media.sh [paquets apt supplementaires...]
set -euo pipefail

# Miroirs Ubuntu : un jour Azure ne se resout plus (2026-11-11), un autre le
# miroir officiel expire (2026-11-19) -- chaque run les essaie et met en
# premier celui qui repond le plus vite (InRelease en moins de 10 s).
if [ -f /etc/apt/apt-mirrors.txt ]; then
  codename=$(. /etc/os-release && echo "$VERSION_CODENAME")
  best=""
  for m in http://azure.archive.ubuntu.com/ubuntu/ http://archive.ubuntu.com/ubuntu/ \
           http://fr.archive.ubuntu.com/ubuntu/ http://mirrors.edge.kernel.org/ubuntu/; do
    t=$(curl -s -o /dev/null -w '%{time_total}' --max-time 10 "${m}dists/${codename}/InRelease" || echo fail)
    echo "Miroir ${m} : ${t}"
    if [ -z "$best" ] && [ "$t" != "fail" ]; then best="$m"; fi
  done
  if [ -n "$best" ]; then
    { printf '%s\tpriority:1\n' "$best"
      { grep -v -F "$best" /etc/apt/apt-mirrors.txt || true; } \
        | awk -F'priority:' 'NF > 1 {printf "%spriority:%d\n", $1, $2 + 1; next} {print}'
    } | sudo tee /etc/apt/apt-mirrors.txt.new > /dev/null
    sudo mv /etc/apt/apt-mirrors.txt.new /etc/apt/apt-mirrors.txt
    echo "Miroir retenu : $best"
  else
    echo "::warning::aucun miroir Ubuntu ne repond en 10 s"
  fi
fi
apt_opts=(-o Acquire::Retries=3 -o Acquire::http::Timeout=20)
# Deja present (ffmpeg avec libass, cabextract, fontconfig) et rien d'autre
# demande : apt n'est pas touche. Sinon, installation avec les listes de
# l'image d'abord ; mise a jour des listes seulement si elle echoue -- c'est
# le « apt-get update » qui expirait quand les miroirs repondaient mal
# (code 124, 2026-11-19, jour de sortie de GTA VI).
have_media() {
  command -v cabextract > /dev/null && command -v fc-cache > /dev/null \
    && ffmpeg -hide_banner -filters 2> /dev/null | grep -E " (subtitles|ass) " > /dev/null
}
if [ "$#" -eq 0 ] && have_media; then
  echo "ffmpeg, cabextract, fontconfig : deja installes"
else
  echo "apt : installation avec les listes de l'image..."
  if ! timeout 300 sudo apt-get "${apt_opts[@]}" install -y -qq ffmpeg fontconfig cabextract "$@" > /dev/null; then
    echo "apt : mise a jour des listes puis nouvel essai..."
    timeout 300 sudo apt-get "${apt_opts[@]}" update -qq
    timeout 420 sudo apt-get "${apt_opts[@]}" install -y -qq ffmpeg fontconfig cabextract "$@" > /dev/null
  fi
fi

dest="$HOME/.fonts/msttcorefonts"
mkdir -p "$dest"
if ls "$dest"/arial.ttf "$dest"/ariblk.ttf > /dev/null 2>&1; then
  echo "Polices Arial : cache"
else
  tmp=$(mktemp -d)
  for archive in arial32.exe arialb32.exe; do
    for attempt in 1 2 3; do
      if curl -sSL --connect-timeout 15 --max-time 90 -o "$tmp/$archive" \
           "https://downloads.sourceforge.net/corefonts/$archive" \
         && cabextract -q -L -F '*.ttf' -d "$dest" "$tmp/$archive"; then
        break
      fi
      echo "::warning::$archive : essai $attempt echoue"
      sleep $((attempt * 5))
    done
  done
  rm -rf "$tmp"
fi
fc-cache -f > /dev/null

ffmpeg -hide_banner -filters | grep -E " (subtitles|ass) " \
  || { echo "::error::ffmpeg sans libass"; exit 1; }
fc-list : family | grep -i "Arial Black" > /dev/null \
  || echo "::warning::Police Arial Black absente : sous-titres en police de repli"
