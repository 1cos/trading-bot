# TRADE CASE — AMD — 2026-08-07

## Scopo del documento

Questo documento conserva la trade AMD del 7 agosto 2026 come caso studio per MaxBot.

Non è una specifica esecutiva definitiva e non autorizza modifiche al codice. Serve a:

- ricostruire la logica della trade;
- distinguere la qualità del setup dal risultato economico finale;
- documentare le nuove regole strategiche emerse;
- identificare requisiti futuri per Strategy Engine, Risk Manager ed Execution Manager;
- fornire a Claude un caso reale da confrontare con l'implementazione futura.

---

## 1. Sintesi

AMD ha mostrato forte debolezza relativa fin dall'apertura, mentre SPY e QQQ non erano convergenti nella stessa direzione. Dopo un displacement netto sotto l'ORB Low, AMD è tornata a ritestare una zona di confluenza formata da ORB Low e Pre-Market Low.

Max è entrato short sulla rejection della zona. Poiché la candela d'ingresso era troppo vicina al livello e uno stop immediatamente sopra la sua wick avrebbe lasciato poco margine al normale rumore del retest, lo stop è stato collocato sopra il pivot high precedente, con un piccolo buffer.

Il prezzo è effettivamente tornato a testare quel pivot senza invalidare lo short, poi è ripartito al ribasso. Il target originario a 2R è stato raggiunto. La struttura ha inoltre lasciato spazio per un'estensione verso il minimo di giornata.

Il risultato finale è stato però di circa **+$88**, contro un profitto disponibile stimato vicino a **+$450**, perché l'ordine manuale usato per alleggerire circa il 70% della posizione non è stato eseguito immediatamente. Il fill è arrivato circa 30 secondi dopo, quando gran parte del profitto era già rientrata.

Conclusione: **trade strategicamente valida e target raggiunto; risultato economico ridotto da gestione ed execution dell'uscita, non da errore nella tesi iniziale.**

---

## 2. Contesto di mercato

### SPY

- Mostrava maggiore forza rispetto a QQQ.
- Aveva iniziato a rompere verso l'alto ORB High e la zona dei massimi precedenti/pre-market.
- Costituiva quindi vento contrario a una posizione short su AMD.

### QQQ

- Era ancora choppy e poco direzionale.
- Alternava movimenti dentro e attorno all'ORB.
- Non forniva una conferma short pulita, ma neppure una convergenza long completa e stabile con SPY.

### AMD

- Opening drive ribassista molto più forte degli indici.
- Uscita netta sotto l'ORB.
- Displacement ribassista visibile.
- Pullback verso la confluenza ORB Low + Pre-Market Low.
- Incapacità di seguire la forza di SPY: segnale di forte debolezza relativa.

### Lettura intermarket

La mancata convergenza di SPY e QQQ abbassava la qualità complessiva del trade, ma non annullava il setup autonomo di AMD.

Regola emersa:

> L'allineamento intermarket è un modificatore di qualità e di timing, non un gate binario. Una forte divergenza relativa del singolo strumento può sostenere un trade anche con indici incerti o parzialmente contrari.

Il setup AMD può essere classificato indicativamente come **B/B+**, non A+, per il conflitto con il contesto degli indici.

---

## 3. Struttura tecnica della trade

### Direzione

`SHORT`

### Sequenza osservata

1. Forte impulso ribassista dopo l'apertura.
2. Break e displacement sotto ORB Low.
3. Creazione di spazio tra il prezzo e il livello rotto.
4. Pullback verso ORB Low e Pre-Market Low.
5. Rejection della zona durante il retest.
6. Entrata short.
7. Secondo test verso il pivot high precedente.
8. Mancato superamento del pivot e nuova rejection.
9. Ripartenza ribassista.
10. Raggiungimento del target teorico a 2R.
11. Estensione successiva verso il minimo di giornata.

### Zona di retest

La zona rilevante era una confluenza composta da:

- `ORB_LOW`;
- `PRE_MARKET_LOW`;
- struttura/pivot creatasi durante il pullback.

Valori mostrati durante l'analisi, da considerare approssimativi finché non saranno verificati sui dati grezzi:

- ORB Low: circa **489.65**;
- area pivot/PML superiore: circa **491.0–491.2**.

### Entrata

L'entrata short mostrata nelle schermate era approssimativamente nell'area **488.95–488.99**.

La logica d'ingresso non era semplicemente "prezzo sotto ORB". Era:

- break già avvenuto;
- displacement confermato;
- ritorno al livello;
- wick/penetrazione nella zona;
- chiusura nuovamente dalla parte short;
- prezzo di entrata non inseguito dopo l'estensione.

---

## 4. Stop loss strutturale

Lo stop non è stato collocato sopra la sola candela d'ingresso.

È stato collocato:

- sopra il pivot high precedente;
- leggermente oltre il livello, usando il piccolo buffer abituale di Max;
- in un punto che avrebbe rappresentato una vera invalidazione della struttura short.

Valore mostrato nell'ultima ricostruzione grafica: circa **491.17**. In schermate/commenti precedenti erano comparsi valori leggermente diversi; prima di usare questo caso come fixture quantitativa occorre verificare il valore esatto dal broker o dai dati del grafico.

### Motivazione

La candela d'ingresso era troppo vicina alla zona. Uno stop sopra la sua wick avrebbe potuto essere colpito da un normale secondo test, senza che la tesi short fosse realmente invalidata.

Il mercato ha poi confermato questa lettura:

- AMD è risalita fino al pivot precedente;
- non lo ha superato materialmente;
- ha respinto nuovamente la zona;
- è tornata sotto ORB Low;
- ha ripreso la direzione short.

### Regola candidata

Serve una terza modalità di stop oltre a quelle già considerate:

`PREVIOUS_PIVOT`

Principio:

> Quando la candela d'ingresso è troppo vicina alla zona e non lascia spazio al normale retest, lo stop può essere posto oltre il pivot strutturale precedente, con un piccolo buffer e con size adattata alla maggiore distanza.

Questa modalità non deve essere scelta automaticamente senza una definizione meccanica del pivot valido e del buffer.

---

## 5. Target e rapporto rischio/rendimento

La ricostruzione finale mostrava approssimativamente:

- entrata: **488.95–488.99**;
- stop: **491.17**;
- rischio sul sottostante: circa **2.18–2.22 punti**;
- target 2R mostrato sul grafico: circa **484.56–484.63**.

Il prezzo ha attraversato la zona del target 2R e ha poi continuato verso il minimo di giornata, mostrato successivamente attorno a **478.91**.

### Lettura sul timeframe 5 minuti

Sul 5m la struttura appariva pulita:

- displacement ribassista iniziale;
- pullback incapace di recuperare stabilmente ORB Low/PML;
- lower high;
- ripresa della pressione short;
- spazio verso il today low.

La fascia **485–486** rappresentava un supporto intermedio. Il target 2R cadeva proprio in quell'area, rendendo razionale monetizzare almeno parte della posizione lì.

### Conclusione sul target

Per questa trade, il piano semplice a **2R** avrebbe funzionato meglio della gestione manuale improvvisata durante il movimento.

---

## 6. Gestione reale dell'uscita

Durante il movimento favorevole, Max ha tentato di chiudere circa il **70%** dei contratti.

L'ordine non è stato eseguito immediatamente. Il fill è arrivato circa **30 secondi dopo**, quando il prezzo dell'opzione era già cambiato e gran parte del profitto disponibile era rientrata.

Risultato indicativo:

- profitto disponibile osservato: circa **$400–$450**;
- profitto finale realizzato: circa **$88**;
- quota catturata del profitto disponibile: circa **20%**.

Non è ancora documentato con certezza:

- tipo esatto dell'ordine usato;
- limit price iniziale;
- bid/ask al momento dell'invio;
- eventuali partial fill;
- prezzo e timestamp esatti di ogni fill;
- slippage effettivo rispetto al bid/ask.

Questi dati devono essere recuperati dall'order history prima di trasformare l'episodio in un test quantitativo di execution.

---

## 7. Valutazione corretta della trade

### Cosa ha funzionato

- Lettura della debolezza relativa di AMD.
- Identificazione del displacement sotto ORB.
- Identificazione della confluenza ORB Low + PML.
- Attesa del retest invece di inseguire l'impulso.
- Entrata sulla rejection.
- Stop sopra una struttura reale con buffer.
- Size teoricamente adattabile allo stop più ampio.
- Target 2R coerente con la struttura.
- Lettura 5m dello spazio verso il minimo di giornata.

### Cosa non ha funzionato

- Cambio della gestione durante il trade.
- Presunzione pratica che l'ordine di alleggerimento fosse già eseguito.
- Mancanza di conferma immediata del fill.
- Mancanza di una procedura automatica di cancel/replace.
- Perdita di gran parte del profitto disponibile durante il ritardo.

### Classificazione journal

> Trade vincente. Setup valido e target 2R raggiunto. Risultato economico fortemente ridotto da fill ritardato sull'uscita parziale e dalla mancanza di conferma dell'esecuzione.

Il risultato di **+$88** non misura correttamente la qualità della lettura strategica. Misura l'esito combinato di strategia, gestione discrezionale ed execution manuale.

---

## 8. Requisiti candidati per MaxBot

### 8.1 Strategy Engine

1. L'allineamento SPY/QQQ non deve essere un veto binario.
2. Deve poter classificare forza/debolezza relativa del simbolo rispetto agli indici.
3. Un setup autonomo forte contro indici incerti può restare valido con punteggio inferiore.
4. Se entrambi gli indici hanno displacement confermato nella direzione opposta, la penalità deve essere maggiore.
5. Break, displacement e retest devono restare eventi distinti.
6. Il retest può avvenire su una zona composita, per esempio ORB Low + PML.

### 8.2 Stop Manager

Aggiungere come candidato:

`PREVIOUS_PIVOT`

Requisiti ancora da definire:

- quale pivot è eleggibile;
- distanza massima temporale dall'entry;
- conferma richiesta per il pivot;
- buffer in tick oltre il pivot;
- comportamento se il pivot è troppo distante;
- riduzione della size in base al rischio monetario massimo.

Principio costante:

> Lo stop deve stare leggermente oltre il livello che invalida realmente la tesi, non esattamente sul livello.

### 8.3 Target Manager

Configurazione iniziale candidata:

- target automatico armato a **2R**;
- prima versione semplice: uscita totale a 2R;
- variante da backtestare: **70% a 2R + 30% runner** verso un target strutturale, per esempio today low;
- nessuna modifica discrezionale non registrata durante il trade.

Le due modalità devono essere testate separatamente. Questo singolo caso non dimostra che 70/30 sia universalmente migliore dell'uscita totale.

### 8.4 Execution Manager per opzioni

Requisiti indispensabili:

1. Un ordine inviato non equivale a una posizione chiusa.
2. La posizione viene aggiornata soltanto sulla conferma del broker.
3. Il sistema deve tracciare quantità richiesta, quantità eseguita e quantità residua.
4. Deve rilevare partial fill e ordini ancora pendenti.
5. Se un'uscita deve essere immediatamente eseguita, deve usare un limit marketable coerente con il bid/ask corrente.
6. Se l'ordine non viene eseguito entro la finestra configurata, deve eseguire cancel/replace al nuovo prezzo ammesso.
7. Deve impedire ordini duplicati durante cancel/replace.
8. Deve mantenere separata la gestione del runner residuo.
9. Deve registrare:
   - timestamp invio;
   - tipo ordine;
   - prezzo richiesto;
   - bid/ask al momento dell'invio;
   - timestamp e prezzo di ogni fill;
   - quantità di ogni fill;
   - latenza;
   - slippage;
   - motivazione dell'uscita.
10. Stop e target sul sottostante devono essere tradotti in ordini sull'opzione senza presumere un rapporto di prezzo stabile.

### 8.5 Modalità operativa iniziale

Per ridurre la complessità della prima versione paper/live:

- la decisione di chiudere il 70% può inizialmente restare manuale;
- MaxBot deve però gestire automaticamente l'esecuzione affidabile dell'ordine;
- solo dopo aver validato l'Execution Manager si potrà automatizzare anche la decisione di alleggerimento.

---

## 9. Decisioni proposte, ma non ancora approvate come contratto

1. `PREVIOUS_PIVOT` come nuova modalità di stop.
2. Piccolo buffer oltre il pivot/livello di invalidazione.
3. Intermarket alignment come quality modifier e non gate assoluto.
4. Default iniziale a 2R.
5. Variante 70% a 2R + 30% runner.
6. Conferma broker obbligatoria prima di aggiornare la posizione.
7. Cancel/replace automatico per uscite non eseguite entro una finestra configurata.

Queste decisioni devono essere formalizzate in task separati. Non vanno inserite incidentalmente dentro B7.

---

## 10. Dati ancora necessari

Prima di usare questa trade come golden case quantitativo servono:

- simbolo/contratto esatto dell'opzione;
- strike e scadenza;
- lato put/call;
- quantità iniziale di contratti;
- timestamp esatto dell'entry;
- prezzo dell'opzione all'entry;
- prezzo esatto del sottostante all'entry;
- stop esatto sul sottostante;
- target 2R esatto;
- timestamp dell'ordine di uscita del 70%;
- order type e limit price;
- bid/ask al momento dell'invio;
- fill report completo;
- profitto massimo aperto verificato;
- profitto finale netto verificato, incluse commissioni.

Fino ad allora, i prezzi riportati in questo documento sono ricostruzioni visive approssimative.

---

## 11. Separazione dal flusso B7

Questo caso studio non deve interrompere o allargare il task B7 relativo alle zone composite.

Le eventuali dipendenze sono soltanto concettuali:

- il retest AMD coinvolgeva una zona composita ORB Low + PML;
- B7 deve costruire/rappresentare la confluenza;
- la selezione del trade, lo stop `PREVIOUS_PIVOT`, il target 2R e l'execution delle opzioni appartengono a task successivi e separati.

---

## 12. Handoff consigliato a Claude

Claude dovrà usare questo documento prima in modalità audit, senza implementare tutto insieme.

Ordine consigliato dei lavori futuri:

1. chiudere B7 secondo il task già in corso;
2. verificare i dati esatti della trade AMD;
3. confrontare il caso con i contratti attuali di strategy/risk/execution;
4. identificare quali requisiti sono già rappresentabili;
5. aprire task separati per:
   - intermarket quality modifier;
   - stop `PREVIOUS_PIVOT`;
   - target policy 2R vs 70/30;
   - Execution Manager e conferma dei fill;
6. aggiungere test soltanto dopo l'approvazione delle rispettive regole.

---

## Stato

`CASE DOCUMENTED — NOT YET FORMALIZED AS EXECUTABLE SPEC`

Data della trade: **2026-08-07**
Timezone operativo: **America/Chicago**
Strumento: **AMD**
Direzione: **SHORT**
Risultato reale indicativo: **+$88**
Target teorico: **2R, raggiunto**
