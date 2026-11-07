#include "mbed.h"

// pes board pin map
#include "PESBoardPinMap.h"

// drivers
#include "DebounceIn.h"
#include "Stepper.h"

bool do_execute_main_task = false; 
bool do_reset_all_once = false;    

// objects for user button (blue button) handling on nucleo board
DebounceIn user_button(BUTTON1);   
void toggle_do_execute_main_fcn(); 
  




// HX711 Pins
DigitalOut hx711_sck(PB_4); 
DigitalIn  hx711_dt(PC_5);  

// Merkt sich den letzten Zustand des Motors
bool last_stepper_state = false;


volatile int32_t tare_offset = 95900;         // Hier trägst du später den Rohwert ohne Gewicht ein
volatile int32_t last_raw_force = 0;
volatile float last_force_N = 0.0f;

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
    Stepper stepper_M1(PB_14, PC_4);
    // Button Interrupt verknüpfen
    user_button.fall(&toggle_do_execute_main_fcn);

    // Timer einrichten
    const int main_task_period_ms = 20; // 20 ms Zykluszeit (50 Hz)
    Timer main_task_timer;              
    Timer total_timer;                  // Timer für die Ausgabe der Sekunden

    DigitalOut user_led(LED1);

    // --- Kalibrierung der Wägezelle ---
    float calibration_factor = 358.0f; // Hier trägst du später deinen berechneten Faktor ein
    int print_counter = 0;              // Zählt die Durchläufe für den 1-Sekunden-Print


    // Timer starten
    main_task_timer.start();
    total_timer.start(); 

    // --- HAUPTSCHLEIFE ---
    while (true) {
        main_task_timer.reset();

        
        // 1. Stepper-Steuerung NUR BEI ZUSTANDSÄNDERUNG aufrufen
        if (do_execute_main_task != last_stepper_state) {
            last_stepper_state = do_execute_main_task;
            
            if (do_execute_main_task) {
                stepper_M1.setVelocity(2.0f); // Einmalig starten
            } else {
                stepper_M1.setVelocity(2.0f); // Einmalig stoppen
            }
        }

        // 1. Zeitstempel berechnen (in Sekunden)
        float time_s = duration_cast<milliseconds>(total_timer.elapsed_time()).count() / 1000.0f;

        // 2. Wägezelle auslesen
        int32_t raw_force = read_hx711();
        
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

        } else {
            if (do_reset_all_once) {
                do_reset_all_once = false;

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

// Nulle auf den LETZTEN gültigen Rohwert (sicher gegen 0-Rückgaben!)
    if (last_raw_force != 0) {
        tare_offset = last_raw_force;
    }
}