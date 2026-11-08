#include "GuiParser.h"
#include <cstdlib>
#include <cstring>

// Konstruktor: Setzt die Standardwerte beim Start
GuiParser::GuiParser() {
    cmd_start = false;
    cmd_reset = false;
    jog_fwd = false;
    jog_rev = false;
    tare = true; //-bic: tarieren beim start
    pull_back_time_ms = 5000;
    rx_index = 0;
    jog_speed = 1.0f;
    pull_back_speed = -2.0f;
}

void GuiParser::parse_command() {
    if (strncmp(rx_buffer, "START", 5) == 0) {
        cmd_start = true;
    } 
    else if (strncmp(rx_buffer, "RESET", 5) == 0) {
        cmd_reset = true;
    } 
    else if (strncmp(rx_buffer, "JOG_FWD", 7) == 0) {
        jog_fwd = true;
    } 
    else if (strncmp(rx_buffer, "JOG_REV", 7) == 0) {
        jog_rev = true;
    }
    else if (strncmp(rx_buffer, "JOG_STOP", 8) == 0) {
        jog_fwd = false;
        jog_rev = false;
    } 
    else if (strncmp(rx_buffer, "TARE", 4) == 0) {
        tare = true;
    }
    else if (strncmp(rx_buffer, "TIME:", 5) == 0) {
        pull_back_time_ms = atoi(rx_buffer + 5);
        printf("Neue Zugzeit: %lu ms\n", pull_back_time_ms);
    }
    else if (strncmp(rx_buffer, "JOG_SPD:", 8) == 0) {
    jog_speed = atof(rx_buffer + 8);
    printf("Neue Jog-Geschwindigkeit: %.2f\n", jog_speed);
    }
    else {
        printf("Unbekannter Befehl: %s\n", rx_buffer);
    }
}

void GuiParser::check_serial() {
    FileHandle *console = mbed_file_handle(STDIN_FILENO);
    char c;
    
    while (console->readable()) {
        console->read(&c, 1);
        
        if (c == '\n' || c == '\r') {
            if (rx_index > 0) {
                rx_buffer[rx_index] = '\0';
                parse_command();
                rx_index = 0;
            }
        } else {
            if (rx_index < sizeof(rx_buffer) - 1) {
                rx_buffer[rx_index] = c;
                rx_index++;
            }
        }
    }
}