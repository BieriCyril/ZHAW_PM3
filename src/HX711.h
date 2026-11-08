#ifndef HX711_H
#define HX711_H

#include "mbed.h"

class HX711 {
public:
    // Konstruktor: Übergabe der Pins
    HX711(PinName sck_pin, PinName dt_pin);
    
    // Lesefunktion
    int32_t read();

private:
    DigitalOut sck;
    DigitalIn  dt;
};

#endif