# Movite

Bus, tram e metro di Roma in tempo reale: mappa live dei mezzi, fermate di ogni linea,
tabelloni con gli arrivi e calcolo del percorso più veloce usando i tempi di arrivo reali.

## Avvio

Doppio clic su **`Avvia Movite.bat`** (oppure `python server/server.py`).
Si apre il browser su <http://localhost:8080>.

Serve solo Python 3.10 o successivo (nessuna libreria da installare). Al primissimo avvio il server
scarica il GTFS di Roma Mobilità (~46 MB) e lo legge (~20 s). Da lì in poi l'avvio richiede
circa 1 secondo, con i bus GPS già sulla mappa:

- **cache a tre livelli** in `data/`: la "base" (orari di tutte le corse, letta dal CSV una
  volta per versione del GTFS), il "giorno" (ricavato dalla base in ~0,5 s) e le connessioni
  del calcolo percorsi (preparate in background in ~2 s e salvate per i riavvii);
- **nessuna attesa per gli aggiornamenti**: si parte con gli orari già scaricati; il controllo
  di un GTFS più recente e la sua lettura avvengono dopo, in un processo separato, e il nuovo
  orario sostituisce il vecchio senza interruzioni (lo stesso al cambio di giorno);
- **realtime subito**: i feed si scaricano in parallelo mentre i dati si caricano, i bus si
  pubblicano appena letti e, ogni 10 s, si riscarica un feed solo se è cambiato.

**Rete pedonale (solo la prima volta):** per calcolare i tratti a piedi sulle strade vere,
Movite scarica da OpenStreetMap (Overpass) le vie percorribili a piedi di Roma, a 67 riquadri.
Ci vogliono circa 30–60 minuti, a seconda di quanto è carico Overpass; l'app intanto si usa
normalmente e stima i tratti a piedi in linea d'aria (il pannello Percorso lo segnala). I dati
restano in `data/` e vengono riscaricati ogni 60 giorni.

Porta diversa: `set MOVITE_PORT=9000` prima dell'avvio.

## Sull'iPhone (web app su un server Docker)

Movite è una **web app installabile**: sull'iPhone si apre a schermo intero dalla sua icona,
come un'app, senza App Store. Il server gira in un container sul tuo server online.

**1. Avvia il container** (nella cartella del progetto, sul server):

```
docker compose up -d --build
```

L'app risponde su `http://<server>:8070` (la porta si cambia in `docker-compose.yml`). Il primo avvio scarica e legge gli orari (~30 s); i
dati restano nel volume `movite-data`, quindi riavvii e aggiornamenti dell'immagine ripartono
in ~1 s. Memoria: ~250 MB, di più con la rete pedonale (`MOVITE_WALKNET: "0"` la disattiva).

**2. HTTPS (obbligatorio per l'iPhone).** Safari concede la posizione GPS e l'installazione
come app solo in HTTPS. Se il tuo server ha già un reverse proxy con certificato (Traefik,
Nginx Proxy Manager, Coolify, Caddy…) basta puntarlo sulla porta 8070 del server (o su `movite:8080` se il proxy è nella stessa rete Docker). Altrimenti
c'è Caddy già pronto, che ottiene da solo il certificato Let's Encrypt (serve un dominio che
punti al server e le porte 80/443 libere):

```
MOVITE_DOMAIN=movite.tuodominio.it docker compose --profile https up -d --build
```

**3. Installa sull'iPhone:** apri `https://movite.tuodominio.it` in **Safari** → pulsante
**Condividi** → **Aggiungi alla schermata Home**. Da lì Movite si apre a schermo intero, con
la sua icona; l'interfaccia resta salvata sul telefono e si apre subito anche con rete lenta.

**Mappe.** Le tile Stadia (scure e leggere) sono gratuite senza registrazione solo su
`localhost`: online l'app usa OpenStreetMap. Per avere Stadia anche online crea un account
gratuito su stadiamaps.com e imposta `MOVITE_STADIA_KEY` (oppure registra lì il dominio e
imposta `MOVITE_TILES: "stadia"`) nel `docker-compose.yml`.

Variabili utili: `MOVITE_HOST` (indirizzo di ascolto, `0.0.0.0` nel container), `MOVITE_PORT`,
`MOVITE_DATA` (cartella dati, `/data` nel container), `MOVITE_WALKNET`, `MOVITE_TILES`,
`MOVITE_STADIA_KEY`.

### Spazio su disco

Movite usa nel volume circa **60 MB** (orari 45 MB + cache compressa 15 MB), più circa 40 MB
per la rete pedonale; durante l'aggiornamento degli orari servono altri ~50 MB per qualche
secondo. L'immagine Docker occupa ~200 MB. Se il disco è pieno Movite funziona lo stesso
(senza cache: ogni avvio richiede ~10 s invece di 1), ma deve almeno poter scaricare gli orari:
in quel caso la schermata iniziale dice "disco del server pieno" e Movite riprova ogni minuto.

Per vedere e liberare spazio sul server:

```
df -h                      # spazio libero sui dischi
docker system df           # quanto occupano immagini, container, volumi e cache di build
docker image prune         # immagini senza nome lasciate dai redeploy
docker builder prune       # cache delle build (le build di Portainer da Git la fanno crescere)
```

In Portainer: **Images → seleziona quelle "Unused" → Remove**. Evita `docker volume prune`:
cancellerebbe anche i dati degli stack fermi.

### Aggiornare da GitHub con Portainer

1. Crea su GitHub un repository vuoto (anche privato) e caricaci il progetto:
   ```
   git remote add origin https://github.com/<tuo-utente>/movite.git
   git push -u origin main
   ```
2. In Portainer: **Stacks → Add stack → Repository**. Indirizzo del repository, riferimento
   `refs/heads/main`, file `docker-compose.yml`. Se il repository è privato attiva
   l'autenticazione con il tuo utente GitHub e un token di sola lettura (GitHub → Settings →
   Developer settings → Fine-grained tokens, permesso *Contents: read-only*). Le variabili
   (`MOVITE_STADIA_KEY`, `MOVITE_WALKNET`…) si impostano nella sezione *Environment variables*.
3. Per aggiornare: carica le modifiche su GitHub (`git push`) e in Portainer premi **Pull and
   redeploy** sullo stack, oppure attiva *GitOps updates* perché lo faccia da solo. I dati nel
   volume `movite-data` restano. Il `docker-compose.yml` ha `pull_policy: build`, così a ogni
   redeploy l'immagine viene ricostruita con il codice nuovo (pochi secondi).

   Per controllare che il server abbia davvero l'ultima versione:
   `curl -s https://<tuo-dominio>/app.js | wc -c` deve dare la stessa dimensione di `web/app.js`.

**Esposto su internet:** chiunque conosca l'indirizzo può usare l'app (le API sono in sola
lettura; i calcoli percorso contemporanei sono limitati a 2 per non rallentare il resto).

## Cosa fa

- **Mappa live**: tutti i mezzi con GPS (aggiornati ogni 10–15 s, con movimento animato),
  filtri Metro / Tram / Bus. Ogni mezzo è un puntatore a goccia orientato nella direzione di
  marcia, con il pittogramma (bus / tram / lettera della metro), l'etichetta della linea e un
  alone se ha il GPS; le fermate sono piccoli anelli grigi (gialli per i tram), visibili da
  zoom 15. Mezzi e fermate sono disegnati su canvas, per uno zoom fluido anche con ~900 mezzi.
- **Metro e tram senza GPS**: il feed realtime di Roma Mobilità non include la metropolitana,
  quindi i treni vengono mostrati in posizione **stimata dall'orario** (bordo tratteggiato).
  Il pulsante con il cerchio tratteggiato mostra anche i bus senza GPS.
- **Linee**: elenco per tipo, tracciato sulla mappa, tutte le fermate per ciascuna direzione
  e i mezzi in linea posizionati lungo la sequenza di fermate.
- **Fermate**: prossimi passaggi nelle 2 ore, con orario previsto in tempo reale, ritardo
  o anticipo, e i passaggi successivi della stessa linea.
- **Singolo mezzo**: velocità (GPS se disponibile, altrimenti calcolata da odometro/posizioni
  successive), grafico della velocità, puntualità, affollamento, traccia percorsa, fermate
  della corsa con orari previsti. “Segui sulla mappa” tiene il mezzo al centro.
- **Percorso**: partenza/arrivo da indirizzo, fermata, posizione GPS o clic destro sulla
  mappa; "Parti ora", "Parti alle…" o "Arriva entro…". Le soluzioni sono etichettate per
  profilo: **Più veloce**, **Meno cambi**, **Meno a piedi**, più una partenza successiva (o
  precedente, in "Arriva entro") di riserva. Opzioni: "Preferisci metro e tram" ed "Evita bus
  senza GPS" (quelli già in viaggio senza posizione nota); con le preferenze attive la
  soluzione migliore per te è segnata **Consigliato**. Vedi sotto come funziona il calcolo.
- **Avvisi** di servizio di Roma Mobilità.

## Come si calcola un percorso

1. **Connessioni**: gli orari del giorno diventano ~1,2 milioni di tratti fermata→fermata
   consecutiva. Dove il feed realtime dà gli orari previsti (circa 3 corse di bus su 4 già in
   viaggio) li uso al posto di quelli programmati; la metro va sempre a orario.
2. **Ricerca a giri** (Connection Scan Algorithm): per ogni fermata tengo l'orario migliore
   raggiungibile con 1, 2, 3 o 4 mezzi. In una sola passata escono le soluzioni ottimali per
   numero di cambi, da cui "Più veloce" e "Meno cambi". "Arriva entro" fa la stessa ricerca
   all'indietro dalla destinazione, per trovare la partenza più tardi possibile.
3. **Altre ricerche**: una con al massimo ~450 m a piedi ("Meno a piedi"); con le preferenze,
   una che esclude i bus in viaggio senza GPS e una in cui i bus "costano" 6 minuti in più.
   Il confronto finale aggiunge 3 min per tratto in bus (metro e tram) e 8 min per bus senza
   GPS (evita bus senza GPS).
4. **Margini prudenti**: in fermata 1 minuto prima del passaggio previsto (1,5 se il bus non
   ha GPS e può passare in anticipo); in metro 2 minuti per scendere in banchina e 2,5 per
   risalire; cambio bus→bus alla stessa fermata almeno 2 minuti; +1 minuto sulle coincidenze
   dopo un bus senza GPS. Per ogni mezzo viene indicato "sii in fermata entro le…".
5. **A piedi**: distanze e percorsi sulla rete pedonale reale (1,2 m/s), fino a ~1,1 km da e
   verso le fermate e ~450 m per i cambi tra fermate vicine.

## Struttura

```
server/server.py       server HTTP + API JSON
server/gtfs_static.py  lettura e indicizzazione del GTFS statico
server/realtime.py     polling feed GTFS-Realtime, velocità, ritardi
server/gtfsrt.py       decoder protobuf GTFS-RT senza dipendenze
server/planner.py      calcolo percorsi (CSA a giri, profili, "arriva entro")
server/walknet.py      rete pedonale da OpenStreetMap (download, grafo, cammini minimi)
web/                   interfaccia (HTML/CSS/JS + Leaflet), manifest, service worker e icone
Dockerfile, docker-compose.yml, Caddyfile   container e HTTPS per l'uso online / iPhone
data/                  GTFS scaricato e cache (rigenerabili)
```

## Fonti dati

- Orari e realtime: [Roma Mobilità – Open Data](https://romamobilita.it/it/dati-aperti) (GTFS e GTFS-Realtime)
- Mappa: tile © Stadia Maps / OpenMapTiles / OpenStreetMap contributors (gratuite in uso
  locale su localhost; se non rispondono si passa da soli a OpenStreetMap)
- Ricerca indirizzi: Nominatim (OpenStreetMap)
- Rete pedonale: © OpenStreetMap contributors, scaricata tramite Overpass API
