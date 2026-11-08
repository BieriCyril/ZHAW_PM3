#include "mbed.h"

// pes board pin map
#include "PESBoardPinMap.h"

// drivers
#include "DebounceIn.h"
#include "Stepper.h"


//- ASW Driver
#include "HX711.h"

// --- GUI Mock Variables (Werden später durch Serial-Befehle überschrieben) ---
volatile bool gui_cmd_start = false;    // Startet den Pull-Back Prozess
volatile bool gui_cmd_reset = false;    // Setzt die Maschine aus FINISHED oder FAULT zurück
volatile bool gui_jog_fwd = false;      // Jog Vorwärts gedrückt halten
volatile bool gui_jog_rev = false;      // Jog Rückwärts gedrückt halten

volatile float gui_jog_speed = 1.0f;         // Geschwindigkeit beim manuellen Fahren (U/s)
volatile float gui_pull_back_speed = -2.0f;  // Geschwindigkeit beim automatischen Ziehen (U/s)
volatile uint32_t gui_pull_back_time_ms = 5000; // Dauer des Zug-Prozesses in Millisekunden

// --- Hardware Objekte ---
Stepper stepper_M1(PB_14, PC_4);

// Emergency Stop Button (z.B. an Pin PC_6) - PullUp (0 = gedrückt, 1 = in Ordnung)
DigitalIn e_stop_button(PC_6); 

// HX711 Pins
DigitalOut hx711_sck(PB_4); 
DigitalIn  hx711_dt(PC_5);  

// --- Globale Variablen für Kraftmessung ---
volatile int32_t tare_offset = 95900; 
volatile int32_t last_raw_force = 0;
volatile float last_force_N = 0.0f;

// --- Funktion zum Auslesen des HX711 (MUSS VOR MAIN STEHEN) ---
HX711 loadcell(PB_4, PC_5);

// --- ZUSTANDSMODELL (STATE MACHINE) ---
enum MachineStep { 
    ST_IDLE,
    ST_PULL_BACK,
    ST_FINISHED,
    ST_FAULT,
} machine_step = MachineStep::ST_IDLE; // Start-Zustand

int main()
{
    // Hardware initialisieren
    hx711_sck.write(0);
    e_stop_button.mode(PullUp); // Not-Aus Schalter nutzt internen PullUp-Widerstand

    // Timer einrichten
    const int main_task_period_ms = 20; // 50 Hz Loop
    Timer main_task_timer;              
    Timer print_timer;                  
    Timer total_timer;                  
    Timer pull_back_timer;              // Misst die Zeit im PULL_BACK State

    DigitalOut user_led(LED1);

    // Konstanten
    const float calibration_factor = 358.0f; 
    const int printcycle = 200; // Ausgabe alle 200ms

    // Timer starten
    main_task_timer.start();
    total_timer.start(); 
    print_timer.start();

    printf("Maschine gestartet. Zustand: ST_IDLE\n");

    // --- HAUPTSCHLEIFE ---
    while (true) {
        main_task_timer.reset();

        // 1. Zeit & Kraft messen (unabhängig vom Zustand immer ausführen)
        float time_s = duration_cast<milliseconds>(total_timer.elapsed_time()).count() / 1000.0f;
        int32_t raw_force = loadcell.read();
        
        if (raw_force != 0) {
            last_raw_force = raw_force;
            last_force_N = (raw_force - tare_offset) / calibration_factor;
        }

        // 2. NOT-AUS ÜBERWACHUNG (höchste Priorität)
        // Angenommen: Wenn Pin auf 0 gezogen wird (GND), ist Not-Aus gedrückt
        if (e_stop_button.read() == 0 && machine_step != MachineStep::ST_FAULT) {
            machine_step = MachineStep::ST_FAULT;
            stepper_M1.setVelocity(0.0f);
            printf("NOT-AUS GEDRUECKT! Wechsel in ST_FAULT\n");
        }

        // 3. STATE MACHINE LOGIK
        switch (machine_step) {
            
            case MachineStep::ST_IDLE: {
                // In IDLE warten wir auf GUI Befehle (Start oder Jogging)
                
                if (gui_cmd_start) {
                    // GUI hat Startbefehl gesendet
                    gui_cmd_start = false; // Befehl quittieren/zurücksetzen
                    
                    pull_back_timer.reset();
                    pull_back_timer.start();
                    machine_step = MachineStep::ST_PULL_BACK;
                    printf("Transition to Step: ST_PULL_BACK\n");
                } 
                else if (gui_jog_fwd) {
                    stepper_M1.setVelocity(gui_jog_speed);
                } 
                else if (gui_jog_rev) {
                    stepper_M1.setVelocity(-gui_jog_speed);
                } 
                else {
                    stepper_M1.setVelocity(0.0f); // Stillstand wenn keine Tasten gedrückt
                }
                break;
            }
            
            case MachineStep::ST_PULL_BACK: {
                // Motor zieht mit voreingestellter Geschwindigkeit
                stepper_M1.setVelocity(gui_pull_back_speed);
                
                // Prüfen, ob die geforderte Zeit abgelaufen ist
                uint32_t elapsed_pull_time = duration_cast<milliseconds>(pull_back_timer.elapsed_time()).count();
                
                if (elapsed_pull_time >= gui_pull_back_time_ms) {
                    stepper_M1.setVelocity(0.0f); // Motor (Servo) stoppen
                    pull_back_timer.stop();
                    
                    machine_step = MachineStep::ST_FINISHED;
                    printf("Transition to Step: ST_FINISHED\n");
                }
                break;
            }

            case MachineStep::ST_FINISHED: {
                // Motor bleibt gestoppt, bis Reset kommt
                stepper_M1.setVelocity(0.0f);
                
                if (gui_cmd_reset) {
                    gui_cmd_reset = false; // Befehl quittieren
                    
                    // Tara neu setzen vor dem nächsten Lauf (optional)
                    if (last_raw_force != 0) tare_offset = last_raw_force;
                    
                    machine_step = MachineStep::ST_IDLE;
                    printf("Transition to Step: ST_IDLE\n");
                }
                break;
            }

            case MachineStep::ST_FAULT: {
                // Fehlerzustand blockiert alle Bewegungen
                stepper_M1.setVelocity(0.0f);
                
                // Maschine darf nur zurückgesetzt werden, wenn Not-Aus gelöst ist (== 1) UND GUI Reset gesendet wurde
                if (e_stop_button.read() == 1 && gui_cmd_reset) {
                    gui_cmd_reset = false;
                    machine_step = MachineStep::ST_IDLE;
                    printf("Fehler quittiert. Transition to Step: ST_IDLE\n");
                }
                break;
            }
        }
        
        // 4. Status LED blinken lassen (hilft bei Diagnose)
        user_led = !user_led;

        // 5. Telemetrie-Daten via Serial ausgeben (für die GUI)
        if (print_timer.read_ms() >= printcycle) {
            printf("State: %d | Zeit: %.3f s | Kraft: %.3f N | Rohwert: %d\n", 
                   machine_step, time_s, last_force_N, (int)last_raw_force);
            print_timer.reset();
        }

        // 6. Zykluszeit einhalten (20ms)
        int main_task_elapsed_time_ms = duration_cast<milliseconds>(main_task_timer.elapsed_time()).count();
        if (main_task_period_ms - main_task_elapsed_time_ms < 0)
            printf("Warning: Main task took longer than main_task_period_ms\n");
        else
            thread_sleep_for(main_task_period_ms - main_task_elapsed_time_ms);
    }
}