"""Voice Studio : transcription integrale d'une video YouTube, puis synthese
vocale locale du texte choisi.

Module INDEPENDANT du reste du logiciel (Radar, Content Factory, Projets...),
mais qui ne reconstruit rien de ce qui existe deja :

- la transcription passe par transcription/whisper_engine.py, le seul moteur
  Whisper du projet ;
- l'audio est extrait par video/audio_extractor.py ;
- les exports .srt/.vtt reutilisent export/subtitles_export.py ;
- l'annulation utilise core/cancellation.py, comme le pipeline video.

Ce paquet n'importe aucun element de l'interface : il est utilisable et
testable sans Qt.
"""
