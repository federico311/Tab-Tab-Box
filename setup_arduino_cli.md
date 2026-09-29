# Setup di arduino-cli sul Raspberry Pi

La GUI carica automaticamente lo sketch scelto sull'Arduino usando
`arduino-cli` come comando di sistema. Va installato una sola volta sul Pi
(via SSH, stessi comandi di sempre).

## 1. Installazione di arduino-cli

```bash
cd ~
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
sudo mv bin/arduino-cli /usr/local/bin/
```

Verifica che sia installato:
```bash
arduino-cli version
```

## 2. Installazione del core AVR (necessario per Arduino Nano)

```bash
arduino-cli core update-index
arduino-cli core install arduino:avr
```

Questo comando scarica i tool di compilazione per i microcontrollori
Atmega (quello che c'è sull'Arduino Nano). Richiede qualche minuto la
prima volta.

## 3. Permessi porta seriale

Se non l'hai già fatto per il resto del progetto:
```bash
sudo usermod -a -G dialout $USER
```
(serve logout/login o riavvio per applicare)

## 4. Verifica che l'Arduino sia riconosciuto

Con l'Arduino Nano collegato via USB:
```bash
arduino-cli board list
```

Dovresti vedere una riga con la porta (es. `/dev/ttyUSB0`) e possibilmente
il nome della scheda rilevata.

## 5. Nota sul FQBN (tipo di scheda)

Nel file `arduino_flash.py` il FQBN di default è:
```
arduino:avr:nano:cpu=atmega328old
```
Questo è corretto per la maggior parte dei cloni economici di Arduino Nano
(quelli con chip USB CH340). Se il caricamento fallisce con un errore tipo
"programmer not responding" o simile, prova a cambiare in:
```
arduino:avr:nano:cpu=atmega328
```
(usato dagli Arduino Nano originali con bootloader più recente).

Puoi testare manualmente quale funziona con:
```bash
arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328old sketches/eligibility
arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328old sketches/eligibility
```
sostituendo la porta con quella reale mostrata da `arduino-cli board list`.

Una volta capito quale FQBN funziona per il tuo Nano, se è quello
alternativo, aggiorna la costante `DEFAULT_FQBN` in `arduino_flash.py`.
