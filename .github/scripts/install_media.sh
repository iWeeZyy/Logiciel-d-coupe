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

sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg fontconfig cabextract "$@" > /dev/null

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
