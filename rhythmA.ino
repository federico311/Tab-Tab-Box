
const int ledRosso  = 4;
const int ledGiallo = 5;
const int ledVerde  = 6;
const int motore    = 3;
const int piezo     = A0;

// parametri
const unsigned long durata_ascolto     = 10000UL;  
const unsigned long durata_paced       = 66000UL;  
const unsigned long durata_perturbato  = 54000UL;  
const unsigned long motore_ON          = 80UL;     
const unsigned long ISI_normale        = 1000UL;  // 60 BPM
const unsigned long ISI_perturbato     = 833UL;  // 72 BPM
const int           soglia_piezo       = 100;
const unsigned long debounce_ms        = 300UL;

unsigned long faseStart   = 0;
unsigned long ultimaVibro = 0;
bool          vibroAttivo = false;
unsigned long vibroFine   = 0;
unsigned long ultimoTap   = 0;
int           beatCount   = 0;
unsigned long ISI_corrente = ISI_normale;

// prototipi
void tuttoSpento();
void avviaTest();
void gestisciVibrazione();
void leggiPiezo();

void setup() {
  Serial.begin(9600);
  pinMode(ledRosso,  OUTPUT);
  pinMode(ledGiallo, OUTPUT);
  pinMode(ledVerde,  OUTPUT);
  pinMode(motore,    OUTPUT);

  tuttoSpento();
  digitalWrite(ledRosso, HIGH);
  Serial.println(F("Rhythm A ready"));
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
  beatCount    = 0;
  vibroAttivo  = false;
  ISI_corrente = ISI_normale;

  // Fase 1
  tuttoSpento();
  digitalWrite(ledGiallo, HIGH);
  Serial.println(F("Listening phase, do not tap"));

  faseStart   = millis();
  ultimaVibro = faseStart - ISI_normale;   

  while (millis() - faseStart < durata_ascolto) {
    gestisciVibrazione();
  }
  analogWrite(motore, 0);
  vibroAttivo = false;

  // Fase 2
  tuttoSpento();
  digitalWrite(ledVerde, HIGH);
  Serial.println(F("Start tapping"));

  faseStart    = millis();
  ultimaVibro  = faseStart - ISI_normale;
  ultimoTap    = 0;
  ISI_corrente = ISI_normale;  
  beatCount    = 0;

  while (millis() - faseStart < durata_paced) {
    gestisciVibrazione();
    leggiPiezo();
  }
  analogWrite(motore, 0);
  vibroAttivo = false;

  // Fase 3
  ISI_corrente = ISI_perturbato;
  faseStart    = millis();
  ultimaVibro  = faseStart - ISI_perturbato;

  while (millis() - faseStart < durata_perturbato) {
    gestisciVibrazione();
    leggiPiezo();
  }
  analogWrite(motore, 0);

  tuttoSpento();
  digitalWrite(ledRosso, HIGH);
  Serial.println(F("Test completed"));
  Serial.println(F("Send 's' for a new test"));
}

void gestisciVibrazione() {
  unsigned long ora = millis();

  if (!vibroAttivo && (ora - ultimaVibro >= ISI_corrente)) {
    ultimaVibro += ISI_corrente; 
    vibroAttivo  = true;
    vibroFine    = ora + motore_ON;
    analogWrite(motore, 150);

   
    if (beatCount >= 0) beatCount++;
  }

  if (vibroAttivo && ora >= vibroFine) {
    vibroAttivo = false;
    analogWrite(motore, 0);
  }
}

void leggiPiezo() {
  int val = analogRead(piezo);
  unsigned long ora = millis();

  if (val >= soglia_piezo && (ora - ultimoTap) > debounce_ms) {
    ultimoTap = ora;

    int statoMotore = vibroAttivo ? 1 : 0;

    Serial.print(ora - faseStart);
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
