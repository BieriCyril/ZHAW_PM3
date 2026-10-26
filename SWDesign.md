# Software Architektur: Zugprüfmaschine (PES Board)

Dieses Dokument visualisiert den Aufbau und den Datenfluss der Python-basierten Steuerungs- und Auswertungssoftware.
## 1. System-Architektur
Dieses Diagramm zeigt die physische und logische Trennung zwischen Laptop und STM32.

```mermaid
graph TD
    subgraph Laptop ["Laptop (Python)"]
        GUI["Haupt-GUI (PyQt5)<br>- Parameter-Eingabe<br>- PyQtGraph Plot<br>- Spannungs-Berechnung"]
        Worker["MeasurementWorker<br>QThread (Hintergrund)"]
        StreamPY["SerialStream.py<br>Binär-Parser & Puffer"]
    end

    subgraph Nucleo ["STM32 Nucleo Board (C++)"]
        StreamCPP["SerialStream C++<br>2 Mbaud UART"]
        StateMachine["State Machine<br>Motor & Sensoren"]
    end

    GUI -- "Benutzereingaben" --> Worker
    Worker -- "Instanziert & Steuert" --> StreamPY
    StreamPY <-->|"UART via USB-TTL<br>(RX/TX)"| StreamCPP
    StreamCPP <-->|"Float Array Puffer"| StateMachine
```


## 2. Datenfluss & Ablauf (Sequenz-Diagramm)

Dieses Diagramm zeigt den genauen zeitlichen Ablauf, wenn der Benutzer einen Zugversuch startet. Es visualisiert das "Handshake"-Protokoll mit dem Start-Byte und die asynchrone Datenverarbeitung.

```mermaid
sequenceDiagram
    actor User
    participant GUI as MainWindow (PyQt)
    participant Thread as Worker (QThread)
    participant Serial as SerialStream
    participant STM32 as PES Board (C++)

    User->>GUI: Klick "START ZUGVERSUCH"
    GUI->>GUI: Leert alten Graphen
    GUI->>Thread: start_measurement()
    
    Note over Thread, STM32: Handshake Phase
    Thread->>Serial: serial_stream.start()
    Serial->>STM32: Sendet Start-Byte (255)
    STM32->>Serial: Sendet Header (Anzahl Floats)
    
    Note over Serial, STM32: Mess Phase (Hochgeschwindigkeit)
    loop Datenerfassung (z.B. 1 kHz)
        STM32->>Serial: Sendet Binär-Floats (dTime, Kraft, Weg)
    end
    
    Note over Serial, STM32: STM32 State-Machine beendet Messung
    
    Serial-->>Thread: get_data() liefert fertige Arrays
    Thread-->>GUI: result_ready(Daten) Signal
    
    Note over GUI: Auswertungs Phase
    GUI->>GUI: Liest L0, Breite, Dicke aus
    GUI->>GUI: Berechnet Spannung (MPa) & Dehnung (%)
    GUI->>User: Zeichnet fertiges Diagramm
    
    User->>GUI: Klick "Exportieren"
    GUI->>User: Speichert CSV-Datei
```

## 3. Zuständigkeiten der Schichten

*   **STM32 (C++):** Reine Echtzeit-Datenerfassung und Motorsteuerung. Berechnet keine Spannungen, sondern liefert nur Rohwerte (`Delta-Zeit`, `Kraft in N`, `Weg in mm`).
*   **SerialStream (Python):** Übersetzer. Nimmt den kontinuierlichen 2 Mbaud Datenstrom auf und gießt ihn in strukturierte Numpy-Matrizen.
*   **MeasurementWorker (Python):** Der Dirigent. Hält die Verbindung offen, wartet auf das Ende der Messung und schützt die GUI vor Abstürzen.
*   **MainWindow (Python):** Die Präsentation und Logik. Übernimmt die gesamte Mathematik (Geometrie zu Spannung/Dehnung) und das Zeichnen der Graphen.