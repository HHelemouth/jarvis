# Jarvis : double clap, le bureau se réveille

Inspiré de [hectorg2211/jarvis](https://github.com/hectorg2211/jarvis), entièrement reconfiguré.

Au double clap :

1. La voix dit « Bon retour ! », puis annonce le nombre de mails non lus et de conversations Teams en attente.
2. **Claude** (claude.ai) s'ouvre dans Edge, en plein écran sur l'**écran du milieu**.
3. **Teams** (moitié gauche) et **Outlook** (moitié droite) s'ouvrent sur l'**écran de droite**.
4. Une **vidéo YouTube** s'ouvre sans le son, en plein écran sur l'**écran de gauche**.
5. **FIP Groove** démarre dans une fenêtre Edge réduite.

## Les fichiers à double-cliquer

| Fichier | Rôle |
| ------- | ---- |
| `Installer.bat` | Première installation : environnement Python, composants, puis réglage de la voix. |
| `Jarvis.bat` | Lance Jarvis : il écoute, puis réveille le bureau au double clap. |
| `Tester-les-claps.bat` | Mode test : affiche le niveau de chaque bruit, n'ouvre rien. Pour régler la détection. |
| `Jarvis-debug.bat` | Comme `Jarvis.bat`, avec l'affichage des niveaux. |
| `Connexion-Microsoft.bat` | Connexion unique à Microsoft pour compter les mails et messages Teams. |
| `Micros.bat` | Liste les micros du PC. |
| `Demarrage-auto-ON.bat` | Jarvis se lance tout seul avec Windows, sans fenêtre. Après un réveil, il coupe le micro et se réarme après 4 h d'absence (nuit, veille). Journal : `.cache/jarvis.log`. |
| `Demarrage-auto-OFF.bat` | Désactive le démarrage automatique et arrête Jarvis en arrière-plan. |

## Réglages possibles dans `.env`

`configurer.py` remplit ce fichier pour toi. Réglages facultatifs :

| Variable | Rôle |
| -------- | ---- |
| `JARVIS_MIN_RMS` | Niveau minimum d'un clap (0.28 par défaut). À baisser si tes claps sont « trop faibles ». |
| `JARVIS_DECAY_RATIO` | Le son doit retomber sous cette part du pic en 100 ms (0.35). |
| `JARVIS_MIN_GAP_S` / `JARVIS_MAX_GAP_S` | Écart autorisé entre les deux claps (0.12 à 0.35 s). |
| `JARVIS_INPUT_DEVICE` | Numéro ou morceau du nom du micro à utiliser (voir `Micros.bat`). |
| `JARVIS_SAMPLE_RATE` | Force la fréquence du micro (par défaut : celle du micro). |
| `ELEVENLABS_MODEL_ID` | Modèle de voix (`eleven_multilingual_v2` par défaut). |

## Comment la détection évite les faux déclenchements

Le volume ne suffit pas : une voix atteint le même niveau qu'un clap. Jarvis regarde donc la **forme** du son.
Quand un pic dépasse le seuil, il attend 100 ms : si le niveau est retombé sous 35 % du pic, c'est un clap.
Sinon c'est de la voix ou de la musique, et c'est ignoré.
