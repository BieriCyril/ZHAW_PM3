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
    ST_REFERENCING, // 20.11.26 Motor fährt zu Intialposition
    ST_WAIT_SPECIMEN, // 20.11.26Warten auf Einlegen der Probe
    ST_PULL_BACK,
    ST_FINISHED,
    ST_RETURN,
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

    // Endschalter für Initialposition
    // 20.11.26
    DigitalIn limit_switch(PC_9);
    limit_switch.mode(PullUp); // 0 = betätigt, 1 = nicht betätigt
    const float reference_speed = -1.0f; // Geschwindigkeit für Referenzfahrt

    // Das GUI-Objekt ---
    GuiParser gui;

    // --- Globale Variablen für Kraftmessung ---
    volatile int32_t tare_offset = 95900; 
    volatile int32_t last_raw_force = 0;
    volatile float last_force_N = 0.0f;
    float calibration_factor = -21630.5f; //- Default calibration factor, can be adjusted via GUI
    const float spindel_steigung_mm = 1.0f; //-m6
    float displacement_mm = 0.0f;
    float rotation_offset = 0.0f;


// --- Funktion zum Auslesen des HX711, construct object
    HX711 loadcell(PB_4, PC_5);

    printf("Obejkte initialisiert\n");

    // Hardware initialisieren
    e_stop_button.mode(PullUp); // Not-Aus Schalter nutzt internen PullUp-Widerstand

    // Timer einrichten
    const int main_task_period_ms = 20; // 50 Hz Loop
    Timer main_task_timer;              
    Timer print_timer;                  
    Timer total_timer;                  
    Timer pull_back_timer;              // Misst die Zeit im PULL_BACK State
    Timer return_timer;

    DigitalOut user_led(LED1);


    // Konstanten

    const int printcycle = 100; // limited by loadcell update rate (10 Hz) - 100 ms

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

        int32_t raw_force = loadcell.read(); //- read loadcell
        

        calibration_factor = gui.calib_factor; 
        if (raw_force != 0) {
            last_raw_force = raw_force;
            last_force_N = (raw_force - tare_offset) / calibration_factor;
        }

        // NOT-AUS ÜBERWACHUNG (höchste Priorität)
        // Angenommen: Wenn Pin auf 0 gezogen wird (GND), ist Not-Aus gedrückt
        if (e_stop_button.read() == 0 && machine_step != MachineStep::ST_FAULT) {
            stepper_M1.setVelocity(0.0f);
            pull_back_timer.stop();
            machine_step = MachineStep::ST_FAULT;
            printf("NOT-AUS GEDRUECKT! Wechsel in ST_FAULT\n");
        }

                // Angenommen: Wenn Pin auf 0 gezogen wird (GND), ist Not-Aus gedrückt
        if (gui.ESTOP && machine_step != MachineStep::ST_FAULT) {
            gui.ESTOP = false; // Reset the command after handling
            stepper_M1.setVelocity(0.0f);
            pull_back_timer.stop();
            machine_step = MachineStep::ST_FAULT;
            printf("NOT-HALT VON GUI GEDRUECKT! Wechsel in ST_FAULT\n");
        }

        // STATE MACHINE LOGIK
        switch (machine_step) {
            
            case MachineStep::ST_IDLE: {
                // In IDLE warten wir auf GUI Befehle (Start oder Jogging)
                gui.cmd_reset = false; // Alte Reset-Befehle verwerfen
                if (gui.cmd_reference_start) { // 20.11.26
                    // GUI hat Startbefehl gesendet
                    gui.cmd_reference_start = false; // Befehl quittieren/zurücksetzen
                    machine_step = MachineStep::ST_REFERENCING;
                    printf("Transition to Step: ST_REFERENCING\n");
                } 
                else if (gui.jog_fwd) {
                    stepper_M1.setVelocity(gui.jog_speed); //- forward speed
                } 
                else if (gui.jog_rev && limit_switch.read() == 1) {
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
            
            // 20.11.26
            case MachineStep::ST_REFERENCING: {
                // prüfen, ob Intialposition erreicht ist
               
                if ((limit_switch.read() == 0) || (gui.cmd_reference_complete)) { // Limit Switch betätigt oder GUI hat Referenzfahrt abgeschlossen
                    // Motor stoppen
                    stepper_M1.setVelocity(0.0f);
                    gui.cmd_reference_complete = false; // Befehl quittieren
                    // Referenzposition erreicht
                    machine_step = MachineStep::ST_WAIT_SPECIMEN;
                    printf("Transition to Step: ST_WAIT_SPECIMEN\n");
                } else {
                    // Motor fährt zur Initialposition
                    stepper_M1.setVelocity(reference_speed);
                }
                break;
            }

            
            // 20.11.26
            case MachineStep::ST_WAIT_SPECIMEN: {
                // Motor bleibt gestoppt
                stepper_M1.setVelocity(0.0f);
                rotation_offset = stepper_M1.getRotation(); // Weg beim Teststart sicher auf 0 setzen
                gui.cmd_reset = false; // Alte Reset-Befehle verwerfen
                // Warten auf Bestätigung vom GUI-Pop-UP "Specimen inserted"
                if (gui.cmd_specimen_confirmed) {
                    gui.cmd_specimen_confirmed = false;
                    if (last_raw_force != 0) tare_offset = last_raw_force;
                    machine_step = MachineStep::ST_PULL_BACK;
                    printf("Transition to Step: ST_PULL_BACK\n");
                    pull_back_timer.reset();
                    pull_back_timer.start();

                }
                break;
            }

            case MachineStep::ST_PULL_BACK: {
                // Motor zieht mit voreingestellter Geschwindigkeit
                stepper_M1.setVelocity(gui.pull_back_speed); //- pull back speed
                gui.cmd_reset = false; // Alte Reset-Befehle verwerfen
                
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
                    gui.cmd_reset = false;
                    gui.cmd_reference_start = false;
                    gui.cmd_specimen_confirmed = false;
                    return_timer.reset();
                    return_timer.start();
                    machine_step = MachineStep::ST_RETURN;
                    printf("Transition to Step: ST_RETURN\n");
                }
                break;
            }


                case MachineStep::ST_RETURN: {
                // Motor fährt mit Zuggeschwindigkeit zurück
                stepper_M1.setVelocity(-(gui.pull_back_speed));

                // Prüfen, ob die geforderte Rückfahrzeit (Testzeit - 5 s) abgelaufen ist
                int64_t elapsed_return_time = duration_cast<milliseconds>(return_timer.elapsed_time()).count();
                int64_t return_time_ms = (int64_t)gui.pull_back_time_ms - 5000;

                if (elapsed_return_time >= return_time_ms || limit_switch.read() == 0) {
                    stepper_M1.setVelocity(0.0f); // Motor stoppen
                    return_timer.stop();
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
                    gui.cmd_reference_start = false;
                    gui.cmd_specimen_confirmed = false;
                    machine_step = MachineStep::ST_IDLE;
                    printf("Fehler quittiert. Transition to Step: ST_IDLE\n");
                }
                break;
            }
        }
        
        // Status LED
        user_led = !user_led;


        // --- Wegberechnung 
        // Echte Hardware (Spindel & Stepper)
        displacement_mm = (stepper_M1.getRotation() - rotation_offset) * spindel_steigung_mm;

 // --- Stepper Enable Logik (0 = aktiviert, 1 = deaktiviert) ---
        bool is_jogging = (machine_step == MachineStep::ST_IDLE) && (gui.jog_fwd || (gui.jog_rev && limit_switch.read() == 1));
        if (machine_step == MachineStep::ST_PULL_BACK || is_jogging  ||  machine_step == MachineStep::ST_REFERENCING   ||  machine_step == MachineStep::ST_RETURN)

        
        { 
            enableSTEPPER.write(0); // Aktiviert
            //printf("Stepper an\n");
        } else {
            enableSTEPPER.write(1); // Deaktiviert
            //printf("Stepper aus\n");
        }


        // --- Telemetrie-Daten via Serial ausgeben (für die GUI) ---
        float time_s = duration_cast<milliseconds>(pull_back_timer.elapsed_time()).count() / 1000.0f;
        if (print_timer.read_ms() >= printcycle) {
            printf("State: %d\n", machine_step);
            printf("%.3f;%.3f;%.3f\n", time_s, last_force_N, displacement_mm);
            //printf("Rawforce :%d\n", last_raw_force);
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