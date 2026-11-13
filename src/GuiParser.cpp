#include "GuiParser.h"
#include <cstdlib>
#include <cstring>

// Nutze BufferedSerial auf denselben USB-Pins
static BufferedSerial pc(USBTX, USBRX, 115200);

GuiParser::GuiParser() {
    cmd_start = false;
    cmd_reset = false;
    jog_fwd = false;
    jog_rev = false;
    tare = true;
    ESTOP = false;
    pull_back_time_ms = 5000;
    rx_index = 0;
    jog_speed = 1.0f;
    pull_back_speed = -2.0f;
    
    // Setze den Port in den nicht-blockierenden Modus, damit die Schleife nicht hängen bleibt
    pc.set_blocking(false);
}

void GuiParser::parse_command() {
    size_t len = strlen(rx_buffer);
    while (len > 0 && (rx_buffer[len - 1] == '\r' || rx_buffer[len - 1] == '\n' || rx_buffer[len - 1] == ' ')) {
        rx_buffer[len - 1] = '\0';
        len--;
    }

    if (len == 0) return;

    if (strncmp(rx_buffer, "START", 5) == 0) {
        cmd_start = true;
        printf("Befehl erkannt: START\n");
    } 
    else if (strncmp(rx_buffer, "RESET", 5) == 0) {
        cmd_reset = true;
        printf("Befehl erkannt: RESET\n");
    } 
    else if (strncmp(rx_buffer, "JOG_FWD", 7) == 0) {
        jog_fwd = true;
        printf("Befehl erkannt: JOG_FWD\n");
    } 
    else if (strncmp(rx_buffer, "JOG_REV", 7) == 0) {
        jog_rev = true;
        printf("Befehl erkannt: JOG_REV\n");
    }
    else if (strncmp(rx_buffer, "JOG_STOP", 8) == 0) {
        jog_fwd = false;
        jog_rev = false;
        printf("Befehl erkannt: JOG_STOP\n");
    } 
    else if (strncmp(rx_buffer, "TARE", 4) == 0) {
        tare = true;
        printf("Befehl erkannt: TARE\n");
    }
    else if (strncmp(rx_buffer, "E-STOP", 6) == 0) {
        ESTOP = true;
        printf("Befehl erkannt: E-STOP\n");
    }
    else if (strncmp(rx_buffer, "TIME:", 5) == 0) {
        pull_back_time_ms = atoi(rx_buffer + 5);
        printf("Neue Zugzeit: %lu ms\n", pull_back_time_ms);
    }
    else if (strncmp(rx_buffer, "PULL_SPEED:", 11) == 0) {
        pull_back_speed = atof(rx_buffer + 11);
        printf("Neue Zuggeschwindigkeit: %.2f\n", pull_back_speed);
    }
    else if (strncmp(rx_buffer, "JOG_SPD:", 8) == 0) {
        jog_speed = atof(rx_buffer + 8);
        printf("Neue Jog-Geschwindigkeit: %.2f\n", jog_speed);
    }
    else if (strncmp(rx_buffer, "CALIB:", 6) == 0) {
        calib_factor = atof(rx_buffer + 6);
        printf("Neuer Kalibrierfaktor: %.2f\n", calib_factor);
    }
    else {
        printf("Unbekannter Befehl: %s\n", rx_buffer);
    }
}

void GuiParser::check_serial() {
    char c;
    // Bei BufferedSerial liest .read() direkt die Bytes aus dem Empfangspuffer
    while (pc.read(&c, 1) > 0) {
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