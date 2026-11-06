#include "mbed.h"

// pes board pin map
#include "PESBoardPinMap.h"

// drivers
#include "DebounceIn.h"

bool do_execute_main_task = false; 
bool do_reset_all_once = false;    

// objects for user button (blue button) handling on nucleo board
DebounceIn user_button(BUTTON1);   
void toggle_do_execute_main_fcn(); 

// ir distance sensor
float AI_digits = 0.0f; 
AnalogIn ir_analog_in(PC_2); 

// HX711 Pins
DigitalOut hx711_sck(PB_4); 
DigitalIn  hx711_dt(PC_5);  


// --- Funktion zum Auslesen des HX711 (MUSS VOR MAIN STEHEN) ---
int32_t read_hx711() {
    if (hx711_dt.read() == 1) {
        return 0; // Keine neuen Daten verfügbar
    }

    int32_t raw_value = 0;

    for (int i = 0; i < 24; i++) {
        hx711_sck.write(1);
        wait_us(1); 
        
        raw_value = raw_value << 1; 
        
        hx711_sck.write(0);
        if (hx711_dt.read() == 1) {
            raw_value++; 
        }
        wait_us(1);
    }

    // 25. Takt-Impuls (Gain 128)
    hx711_sck.write(1);
    wait_us(1);
    hx711_sck.write(0);
    wait_us(1);

    if (raw_value & 0x800000) {
        raw_value |= 0xFF000000;
    }

    return raw_value;
}
int main()
{
    // HX711 Clock initial auf 0 setzen
    hx711_sck.write(0);

    // Button Interrupt verknüpfen
    user_button.fall(&toggle_do_execute_main_fcn);

    // Timer einrichten
    const int main_task_period_ms = 20; // 20 ms Zykluszeit (50 Hz)
    Timer main_task_timer;              
    Timer total_timer;                  // Timer für die Ausgabe der Sekunden

    DigitalOut user_led(LED1);
    DigitalOut led1(PB_9);

    // --- Kalibrierung der Wägezelle ---
    float tare_offset = 95900.0f;           // Hier trägst du später den Rohwert ohne Gewicht ein
    float calibration_factor = 2150.0f; // Hier trägst du später deinen berechneten Faktor ein
    int print_counter = 0;              // Zählt die Durchläufe für den 1-Sekunden-Print
    int32_t last_raw_force = 0;
    float last_force_N = 0.0f;

    // Timer starten
    main_task_timer.start();
    total_timer.start(); 

    // --- HAUPTSCHLEIFE ---
    while (true) {
        main_task_timer.reset();

        // 1. Zeitstempel berechnen (in Sekunden)
        float time_s = duration_cast<milliseconds>(total_timer.elapsed_time()).count() / 1000.0f;

        // 2. Wägezelle auslesen
        int32_t raw_force = read_hx711();
        float force_N = 0.0f;
        
if (raw_force != 0) {
            last_raw_force = raw_force;
            last_force_N = (raw_force - tare_offset) / calibration_factor;
        }

        // 3. Datenausgabe (nur alle 50 Durchläufe = 1x pro Sekunde)
        print_counter++;
        if (print_counter >= 10) {
            printf("Zeit: %.3f s | Kraft: %.3f N | Rohwert: %d\n", time_s, last_force_N, (int)last_raw_force);
            print_counter = 0; // Zähler zurücksetzen
        }

        // --- Button und LED Logik ---
        if (do_execute_main_task) {
            led1 = 1;
        } else {
            if (do_reset_all_once) {
                do_reset_all_once = false;
                led1 = 0;
            }
        }
        user_led = !user_led;

        // --- Zeitverzögerung für stabile 20ms ---
        int main_task_elapsed_time_ms = duration_cast<milliseconds>(main_task_timer.elapsed_time()).count();
        if (main_task_period_ms - main_task_elapsed_time_ms >= 0) {
            thread_sleep_for(main_task_period_ms - main_task_elapsed_time_ms);
        }
    }
}

// --- Button Funktion ---
void toggle_do_execute_main_fcn()
{
    do_execute_main_task = !do_execute_main_task;
    if (do_execute_main_task)
        do_reset_all_once = true;
}