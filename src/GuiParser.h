#ifndef GUI_PARSER_H
#define GUI_PARSER_H

#include "mbed.h"

class GuiParser {
public:
    // Konstruktor
    GuiParser();

    // Die Variablen, die vom GUI gesteuert werden
    bool cmd_start;
    bool cmd_reset;
    bool jog_fwd;
    bool jog_rev;
    bool tare;
    uint32_t pull_back_time_ms;
    float jog_speed;
    float pull_back_speed;
    float calib_factor;

    // Hauptfunktion, die in der Schleife aufgerufen wird
    void check_serial();

private:
    char rx_buffer[32];
    int rx_index;
    
    void parse_command();
};

#endif