// dichiaro le variabili 
const int ledRosso  = 4;
const int ledGiallo = 5;
const int ledVerde  = 6;
const int motore  = 3;   
const int piezo  = A0;  

// dichiaro i parametri
const unsigned long durata_fase_1   = 10000UL;  // UL mi dice che millis() restituisce un usigned long
const unsigned long durata_fase_2   = 15000UL;  
const unsigned long motore_ON       = 80UL;  // durata singola vibrazione
const unsigned long periodo_motore  = 1000UL;  // periodo isocrono (1 Hz)
const int           soglia_piezo    = 100;      
const unsigned long debounce_ms     = 300;  // elimina eventuali rimbalzi fisici

// variabili globali
unsigned long fase2Start  = 0;
unsigned long ultimaVibro = 0;  // timestamp ultimo impulso vibrazione
bool          vibroAttivo = false;
unsigned long vibroFine   = 0;
unsigned long ultimoTap   = 0;

// Prototipi, servono per definire cose che verranno definite successivamente ma Arduino necessità di sapere
void tuttoSpento();
void avviaTest();
void gestisciVibrazione(bool fase2);
void leggiPiezo();

void setup() {
  Serial.begin(9600);

  pinMode(ledRosso,  OUTPUT);
  pinMode(ledGiallo, OUTPUT);
  pinMode(ledVerde,  OUTPUT);
  pinMode(motore,    OUTPUT);

  tuttoSpento();  // mi serve per avere la sicurezza di aver eliminato tutto
  digitalWrite(ledRosso, HIGH);

  Serial.println(F("Eligibility test ready to go"));
  Serial.println(F("Send 's' to start"));  
}


void loop() {
  if (Serial.available() > 0) {
    char c = Serial.read();
    if (c == 'S' || c == 's') {
      avviaTest();
    }
  }
}


void avviaTest() {
  vibroAttivo = false;

  // Fase 1
  tuttoSpento();
  digitalWrite(ledGiallo, HIGH);
  Serial.println(F("Listening phase, do not tap"));  

  unsigned long fase1Start = millis();
  ultimaVibro = fase1Start - periodo_motore;  // in modo da far partire subito

  while (millis() - fase1Start < durata_fase_1) {
    gestisciVibrazione(false);  // false = non siamo in fase 2
  }
  analogWrite(motore, 0);  // devo essere sicuro che il motore sia spento alla fine della fase 1

  // Fase 2
  tuttoSpento();
  digitalWrite(ledVerde, HIGH);  
  Serial.println(F("Start recording, follow the vibration!"));

  fase2Start  = millis();
  ultimaVibro = fase2Start - periodo_motore;  // sincronizza il beat
  ultimoTap   = 0;
  vibroAttivo = false;

  while (millis() - fase2Start < durata_fase_2) {
    gestisciVibrazione(true);
    leggiPiezo();
  }
  analogWrite(motore, 0);

  // Fine
  tuttoSpento();
  digitalWrite(ledRosso, HIGH);
  Serial.println(F("Test completed"));
  Serial.println(F("Send 's' again for a new test"));  
}

// fase2: se true, registra anche il timestamp del beat

void gestisciVibrazione(bool fase2) {
  unsigned long ora = millis();

  if (!vibroAttivo && (ora - ultimaVibro >= periodo_motore)) {  // Avvia nuova vibrazione se è passato il periodo
    ultimaVibro = ultimaVibro + periodo_motore;  // mantieni isocronia
    vibroAttivo = true;
    vibroFine   = ora + motore_ON;
    analogWrite(motore, 150);  
  }

  if (vibroAttivo && ora >= vibroFine) {
    vibroAttivo = false;
    analogWrite(motore, 0);  
  }
}

// Legge il piezo e stampa timestamp, forza e stato motore

void leggiPiezo() {
  int val = analogRead(piezo);
  unsigned long ora = millis();

  if (val >= soglia_piezo && (ora - ultimoTap) > debounce_ms) {
    ultimoTap = ora;

    int statoMotore = vibroAttivo ? 1 : 0;

    Serial.print(ora - fase2Start);
    Serial.print(F(","));
    Serial.print(val);
    Serial.print(F(","));
    Serial.println(statoMotore);
  }
}


void tuttoSpento() {
  digitalWrite(ledRosso,  LOW);
  digitalWrite(ledGiallo, LOW);
  digitalWrite(ledVerde,  LOW);
  analogWrite(motore, 0);  
  vibroAttivo = false;
}
