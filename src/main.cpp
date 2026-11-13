#include "mbed.h"

// pes board pin map
#include "PESBoardPinMap.h"

// drivers
#include "DebounceIn.h"
#include "Stepper.h"


//- ASW Driver
#include "HX711.h"
#include "GuiParser.h"



// --- ZUSTANDSMODELL (STATE MACHINE) ---
enum MachineStep { 
    ST_IDLE,
    ST_PULL_BACK,
    ST_FINISHED,
    ST_FAULT,
} machine_step = ST_IDLE; // Start-Zustand

int main()
{
    
    printf("Anfang\n");

    
    // --- Hardware Objekte ---
    Stepper stepper_M1(PB_9, PB_8);
    DigitalOut enableSTEPPER(PB_6); //- Enable Pin für Stepper (1 = deaktiviert, 0 = aktiviert)

    // Emergency Stop Button (z.B. an Pin xx) - PullUp (0 = gedrückt, 1 = in Ordnung)
    DigitalIn e_stop_button(PC_6); 

    // HX711 Pins
    DigitalOut hx711_sck(PB_4); 
    DigitalIn  hx711_dt(PC_5);  

    // Das GUI-Objekt ---
    GuiParser gui;

    // --- Globale Variablen für Kraftmessung ---
    volatile int32_t tare_offset = 95900; 
    volatile int32_t last_raw_force = 0;
    volatile float last_force_N = 0.0f;
    float calibration_factor = 358.0f; //- Default calibration factor, can be adjusted via GUI
    const float spindl_steigung_mm = 1.0f; //-m6
    float displacement_mm = 0.0f;


    // --- STEPPER SIMULATION ---
    bool STEPPER_SIM_WAY = true; // Auf 'false' stellen, wenn der echte Motor wieder läuft!

// --- Funktion zum Auslesen des HX711, construct object
    HX711 loadcell(PB_4, PC_5);

    printf("Obejkte initialisiert\n");

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

    const int printcycle = 200; // Ausgabe alle 200ms

    // Timer starten
    main_task_timer.start();
    total_timer.start(); 
    print_timer.start();

    printf("Maschine gestartet. Zustand: ST_IDLE\n");

    // --- HAUPTSCHLEIFE ---
    while (true) {
        main_task_timer.reset();

        // Serielle Befehle von der GUI einlesen!
        gui.check_serial();

        // Zeit & Kraft messen (unabhängig vom Zustand immer ausführen)
        float time_s = duration_cast<milliseconds>(pull_back_timer.elapsed_time()).count() / 1000.0f;
        int32_t raw_force = loadcell.read(); //- read loadcell
        

        calibration_factor = gui.calib_factor; 
        if (raw_force != 0) {
            last_raw_force = raw_force;
            last_force_N = (raw_force - tare_offset) / calibration_factor;
        }

        // NOT-AUS ÜBERWACHUNG (höchste Priorität)
        // Angenommen: Wenn Pin auf 0 gezogen wird (GND), ist Not-Aus gedrückt
        if (e_stop_button.read() == 0 && machine_step != MachineStep::ST_FAULT) {
            machine_step = MachineStep::ST_FAULT;
            stepper_M1.setVelocity(0.0f);
            printf("NOT-AUS GEDRUECKT! Wechsel in ST_FAULT\n");
        }

                // Angenommen: Wenn Pin auf 0 gezogen wird (GND), ist Not-Aus gedrückt
        if (gui.ESTOP && machine_step != MachineStep::ST_FAULT) {
            machine_step = MachineStep::ST_FAULT;
            stepper_M1.setVelocity(0.0f);
            printf("NOT-HALT VON GUI GEDRUECKT! Wechsel in ST_FAULT\n");
            gui.ESTOP = false; // Reset the command after handling
        }

        // STATE MACHINE LOGIK
        switch (machine_step) {
            
            case MachineStep::ST_IDLE: {
                // In IDLE warten wir auf GUI Befehle (Start oder Jogging)
                
                if (gui.cmd_start) {
                    // GUI hat Startbefehl gesendet
                    gui.cmd_start = false; // Befehl quittieren/zurücksetzen
                    
                    pull_back_timer.reset();
                    pull_back_timer.start();
                    machine_step = MachineStep::ST_PULL_BACK;
                    printf("Transition to Step: ST_PULL_BACK\n");
                } 
                else if (gui.jog_fwd) {
                    stepper_M1.setVelocity(gui.jog_speed); //- forward speed
                } 
                else if (gui.jog_rev) {
                    stepper_M1.setVelocity(- (gui.jog_speed)); //- inverse speed
                } 
                else {
                    stepper_M1.setVelocity(0.0f); // Stillstand wenn keine Tasten gedrückt
                }
                if (gui.tare && (last_raw_force != 0)) {
                    tare_offset = last_raw_force; //- bic: tare offset setzen
                    gui.tare = false; //- bic: reset tare command
                    printf("Tara gesetzt: %d\n", tare_offset);
                }
                break;
            }
            
            case MachineStep::ST_PULL_BACK: {
                // Motor zieht mit voreingestellter Geschwindigkeit
                stepper_M1.setVelocity(gui.pull_back_speed); //- pull back speed
                
                // Prüfen, ob die geforderte Zeit abgelaufen ist
                uint32_t elapsed_pull_time = duration_cast<milliseconds>(pull_back_timer.elapsed_time()).count();
                
                if (elapsed_pull_time >= gui.pull_back_time_ms) {
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
                
                if (gui.cmd_reset) {
                    gui.cmd_reset = false; // Befehl quittieren
                    
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
                if (e_stop_button.read() == 1 && gui.cmd_reset) {
                    gui.cmd_reset = false;
                    machine_step = MachineStep::ST_IDLE;
                    printf("Fehler quittiert. Transition to Step: ST_IDLE\n");
                }
                break;
            }
        }
        
        // Status LED
        user_led = !user_led;


        // --- Wegberechnung (Echt oder Simulation) ---

        if (STEPPER_SIM_WAY) {
            // Wenn die Simulation aktiv ist und wir im Zug-Modus sind
            if (machine_step == MachineStep::ST_PULL_BACK) {
                // Simuliere z. B. 1.5 mm Vorschub pro Sekunde (anpassbar über Faktor)
                uint32_t elapsed_pull_time = duration_cast<milliseconds>(pull_back_timer.elapsed_time()).count();
                displacement_mm = (elapsed_pull_time / 1000.0f) * 1.5f; 
            } else if (machine_step == MachineStep::ST_IDLE) {
                displacement_mm = 0.0f; // Im Idle zurücksetzen
            }
        } else {
            // Echte Hardware (Spindel & Stepper)
            displacement_mm = stepper_M1.getRotation() * spindl_steigung_mm;
        }


        // Nur im PULL_BACK-Zustand aktivieren (0 = Ein, 1 = Aus)
        if (machine_step == MachineStep::ST_PULL_BACK) {
            enableSTEPPER.write(0); // Aktiviert
            //printf("Stepper an\n");
        } else {
            enableSTEPPER.write(1); // Deaktiviert
            //printf("Stepper aus\n");
        }


        // --- Telemetrie-Daten via Serial ausgeben (für die GUI) ---
        if (print_timer.read_ms() >= printcycle) {
            printf("State: %d\n", machine_step);
            printf("%.3f;%.3f;%.3f\n", time_s, last_force_N, displacement_mm);
            print_timer.reset();
        }

        // Zykluszeit
        int main_task_elapsed_time_ms = duration_cast<milliseconds>(main_task_timer.elapsed_time()).count();
        if (main_task_period_ms - main_task_elapsed_time_ms < 0)
            printf("Warning: Main task took longer than main_task_period_ms\n");
        else
            thread_sleep_for(main_task_period_ms - main_task_elapsed_time_ms);
    }
}