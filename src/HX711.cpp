#include "HX711.h"

// Konstruktor initialisiert die Pins und setzt Clock auf 0
HX711::HX711(PinName sck_pin, PinName dt_pin) : sck(sck_pin), dt(dt_pin) {
    sck.write(0);
}

int32_t HX711::read() {
    if (dt.read() == 1) {
        return 0; // Keine neuen Daten verfügbar
    }

    int32_t raw_value = 0;

    for (int i = 0; i < 24; i++) {
        sck.write(1);
        wait_us(1); 
        
        raw_value = raw_value << 1; 
        
        sck.write(0);
        if (dt.read() == 1) {
            raw_value++; 
        }
        wait_us(1);
    }

    // 25. Takt-Impuls (Gain 128)
    sck.write(1);
    wait_us(1);
    sck.write(0);
    wait_us(1);

    // Vorzeichenerweiterung für negative Werte
    if (raw_value & 0x800000) {
        raw_value |= 0xFF000000;
    }

    return raw_value;
}