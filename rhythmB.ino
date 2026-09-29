const int ledRosso  = 4;
const int ledGiallo = 5;
const int ledVerde  = 6;
const int motore    = 3;
const int piezo     = A0;

const unsigned long ISI_corto    = 667UL;
const unsigned long ISI_lungo    = 1333UL;
const unsigned long motore_ON    = 80UL;
const int           soglia_piezo = 120;
const int           beat_totali  = 120;
const unsigned long debounce_ms  = 300UL;

unsigned long ultimaVibro  = 0;
bool          vibroAttivo  = false;
unsigned long vibroFine    = 0;
int           beatCount    = 0;
unsigned long ultimoTap    = 0;
unsigned long ISI_corrente = ISI_corto;
unsigned long testStart    = 0;


void tuttoSpento();
void avviaTest();


void setup() {
  Serial.begin(9600);
  pinMode(ledRosso,  OUTPUT);
  pinMode(ledGiallo, OUTPUT);
  pinMode(ledVerde,  OUTPUT);
  pinMode(motore,    OUTPUT);

  tuttoSpento();
  digitalWrite(ledRosso, HIGH);
  Serial.println(F("Rhythm B ready"));
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
  ultimoTap    = 0;
  ISI_corrente = ISI_corto;

  tuttoSpento();
  digitalWrite(ledVerde, HIGH);
  Serial.println(F("Start tapping"));

  testStart   = millis();
  ultimaVibro = testStart - ISI_corto;  

  while (beatCount < beat_totali) {
    unsigned long ora = millis();

    
    if (!vibroAttivo && (ora - ultimaVibro >= ISI_corrente)) {  
      ultimaVibro += ISI_corrente;
      vibroAttivo  = true;
      vibroFine    = ora + motore_ON;
      analogWrite(motore, 150);
      beatCount++;

    
      ISI_corrente = (ISI_corrente == ISI_corto) ? ISI_lungo : ISI_corto;  
    }

  
    if (vibroAttivo && ora >= vibroFine) {
      vibroAttivo = false;
      analogWrite(motore, 0);
    }

    int val = analogRead(piezo);
    if (val >= soglia_piezo && (ora - ultimoTap) > debounce_ms) {
      ultimoTap = ora;

      
      int statoMotore = vibroAttivo ? 1 : 0;  

      unsigned long timestamp = ora - testStart;

      Serial.print(timestamp);
      Serial.print(F(","));
      Serial.print(val);
      Serial.print(F(","));
      Serial.println(statoMotore);
    }
  }

 
  analogWrite(motore, 0);
  tuttoSpento();
  digitalWrite(ledRosso, HIGH);
  Serial.println(F("Test completed"));
  Serial.println(F("Send 's' for a new test"));
}


void tuttoSpento() {
  digitalWrite(ledRosso,  LOW);
  digitalWrite(ledGiallo, LOW);
  digitalWrite(ledVerde,  LOW);
  analogWrite(motore, 0);
  vibroAttivo = false;
}
