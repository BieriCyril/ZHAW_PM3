# Dateiname: TensileTestingMachineGUI.py

import sys
import csv
import time
import numpy as np
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QGroupBox, 
                             QFileDialog, QMessageBox, QLineEdit, QFormLayout)
from PyQt5.QtCore import QThread, pyqtSignal
import pyqtgraph as pg

# Die SerialStream-Klasse deiner Hochschule importieren
from SerialStream import SerialStream

class MeasurementWorker(QThread):
    connection_status = pyqtSignal(bool, str)
    state_update = pyqtSignal(str)
    result_ready = pyqtSignal(object)
    
    def __init__(self, port, baudrate):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.is_running = True
        self.serial_stream = None

    def run(self):
        try:
            self.connection_status.emit(True, f"Verbinde mit {self.port}...")
            self.serial_stream = SerialStream(self.port, self.baudrate)
            self.connection_status.emit(True, f"Verbunden ({self.baudrate} Baud)")
        except Exception as e:
            self.connection_status.emit(False, f"Fehler: {e}")
            return

        while self.is_running:
            time.sleep(0.1)

    def start_measurement(self):
        if not self.serial_stream:
            return

        self.state_update.emit("MESSUNG LÄUFT... (Warte auf STM32)")
        try:
            self.serial_stream.reset()
            self.serial_stream.start()

            while self.serial_stream.is_busy_flag():
                time.sleep(0.1)
                
            data = self.serial_stream.get_data()
            self.state_update.emit("MESSUNG BEENDET")
            self.result_ready.emit(data)

        except Exception as e:
            self.state_update.emit(f"FEHLER: {e}")

    def send_command(self, cmd_string):
        if self.serial_stream and self.serial_stream.SerialPort.is_open:
            self.serial_stream.SerialPort.write(f"{cmd_string}\n".encode('utf-8'))

    def stop(self):
        self.is_running = False

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tensile Testing Machine - PES Board")
        self.resize(1100, 750)

        self.port_name = "/dev/ttyUSB0" 
        self.baudrate = int(2e6)
        
        # Speichert die berechneten Daten für den Export
        self.messdaten = None

        self.init_ui()

        self.worker = MeasurementWorker(self.port_name, self.baudrate)
        self.worker.connection_status.connect(self.on_connection_status)
        self.worker.state_update.connect(self.on_state_update)
        self.worker.result_ready.connect(self.on_result_ready)
        self.worker.start()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        # --- Top Bar ---
        status_layout = QHBoxLayout()
        self.lbl_conn = QLabel("Suche Verbindung...")
        self.lbl_state = QLabel("Status: BEREIT")
        self.lbl_state.setStyleSheet("font-weight: bold; font-size: 14px;")
        status_layout.addWidget(self.lbl_conn)
        status_layout.addWidget(self.lbl_state)
        main_layout.addLayout(status_layout)

        # --- Mittlerer Bereich ---
        mid_layout = QHBoxLayout()

        left_col = QVBoxLayout()
        
        # Probenparameter (Jetzt mit Anfangslänge L0)
        param_group = QGroupBox("Prüfkörper Parameter")
        param_layout = QFormLayout()
        self.input_name = QLineEdit("Probe_01")
        self.input_length = QLineEdit("50.0")  # L0 für Dehnungsberechnung
        self.input_width = QLineEdit("10.0")
        self.input_thickness = QLineEdit("2.0")
        param_layout.addRow("Proben-Name:", self.input_name)
        param_layout.addRow("Anfangslänge L0 (mm):", self.input_length)
        param_layout.addRow("Breite (mm):", self.input_width)
        param_layout.addRow("Dicke (mm):", self.input_thickness)
        param_group.setLayout(param_layout)
        left_col.addWidget(param_group)

        # Manuelle Steuerung
        jog_group = QGroupBox("Einspannen & Setup")
        jog_layout = QVBoxLayout()
        btn_up = QPushButton("Motor AUFWÄRTS (Jog)")
        btn_up.clicked.connect(lambda: self.worker.send_command("JOG_UP"))
        btn_down = QPushButton("Motor ABWÄRTS (Jog)")
        btn_down.clicked.connect(lambda: self.worker.send_command("JOG_DOWN"))
        btn_tare = QPushButton("Kraft TARA (Nullabgleich)")
        btn_tare.clicked.connect(lambda: self.worker.send_command("TARE"))
        jog_layout.addWidget(btn_up)
        jog_layout.addWidget(btn_down)
        jog_layout.addWidget(btn_tare)
        jog_group.setLayout(jog_layout)
        left_col.addWidget(jog_group)
        
        left_col.addStretch()
        mid_layout.addLayout(left_col, 1)

        # Rechte Spalte: Graph (Spannung-Dehnung)
        self.graphWidget = pg.PlotWidget(title="Spannungs-Dehnungs-Diagramm")
        self.graphWidget.setLabel('left', 'Spannung (MPa)', units='N/mm²')
        self.graphWidget.setLabel('bottom', 'Dehnung', units='%')
        self.graphWidget.showGrid(x=True, y=True)
        self.plot_line = self.graphWidget.plot([], [], pen=pg.mkPen('b', width=2))
        mid_layout.addWidget(self.graphWidget, 3)

        main_layout.addLayout(mid_layout)

        # --- Bottom Bar ---
        btn_layout = QHBoxLayout()
        
        btn_start = QPushButton("START ZUGVERSUCH")
        btn_start.setStyleSheet("background-color: lightgreen; height: 50px; font-weight: bold;")
        btn_start.clicked.connect(self.start_measurement)
        
        btn_stop = QPushButton("NOT-HALT / STOP")
        btn_stop.setStyleSheet("background-color: salmon; height: 50px; font-weight: bold;")
        btn_stop.clicked.connect(lambda: self.worker.send_command("STOP"))
        
        btn_reset = QPushButton("RESET")
        btn_reset.setStyleSheet("height: 50px;")
        btn_reset.clicked.connect(lambda: self.worker.send_command("RESET"))
        
        btn_export = QPushButton("Daten Exportieren")
        btn_export.setStyleSheet("background-color: lightblue; height: 50px;")
        btn_export.clicked.connect(self.export_data)

        btn_layout.addWidget(btn_start)
        btn_layout.addWidget(btn_stop)
        btn_layout.addWidget(btn_reset)
        btn_layout.addWidget(btn_export)
        main_layout.addLayout(btn_layout)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

    def on_connection_status(self, connected, msg):
        self.lbl_conn.setText(msg)
        self.lbl_conn.setStyleSheet("color: green; font-weight:bold;" if connected else "color: red; font-weight:bold;")

    def on_state_update(self, state):
        self.lbl_state.setText(f"Status: {state}")

    def on_result_ready(self, data):
        time_array = data["time"]
        values_array = data["values"]

        if len(time_array) == 0:
            QMessageBox.warning(self, "Fehler", "Keine Daten empfangen!")
            return

        # Prüfen, ob der STM32 wirklich schon 2 Werte (Kraft, Weg) sendet
        if values_array.shape[1] < 2:
            QMessageBox.warning(self, "Warnung", "Der STM32 sendet noch keinen Weg! Bitte Firmware anpassen (braucht dtime, force, displacement).")
            return

        # 1. Rohdaten extrahieren
        force_N = values_array[:, 0] 
        displacement_mm = values_array[:, 1]

        # 2. Geometrie auslesen
        try:
            l0 = float(self.input_length.text().replace(',', '.'))
            width = float(self.input_width.text().replace(',', '.'))
            thickness = float(self.input_thickness.text().replace(',', '.'))
        except ValueError:
            QMessageBox.warning(self, "Fehler", "Bitte gültige Zahlen für die Probengeometrie eingeben.")
            return

        # 3. Spannung und Dehnung berechnen
        area_mm2 = width * thickness
        stress_MPa = force_N / area_mm2
        strain_pct = (displacement_mm / l0) * 100

        # 4. Daten speichern & plotten
        self.messdaten = {
            "time_s": time_array,
            "force_N": force_N,
            "disp_mm": displacement_mm,
            "stress_MPa": stress_MPa,
            "strain_pct": strain_pct
        }
        
        self.plot_line.setData(strain_pct, stress_MPa)
        QMessageBox.information(self, "Erfolg", f"Versuch abgeschlossen! {len(time_array)} Messpunkte berechnet.")

    def start_measurement(self):
        self.plot_line.setData([], [])
        self.messdaten = None
        self.worker.start_measurement()

    def export_data(self):
        if self.messdaten is None:
            QMessageBox.warning(self, "Fehler", "Keine berechneten Messdaten vorhanden!")
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        proben_name = self.input_name.text().replace(" ", "_")
        default_filename = f"{timestamp}_{proben_name}.csv"
            
        path, _ = QFileDialog.getSaveFileName(self, "Messdaten speichern", default_filename, "CSV Files (*.csv)")
        
        if path:
            with open(path, mode='w', newline='') as file:
                writer = csv.writer(file, delimiter=';')
                
                # Metadaten
                writer.writerow(["# Probenname", self.input_name.text()])
                writer.writerow(["# L0 (mm)", self.input_length.text()])
                writer.writerow(["# Breite (mm)", self.input_width.text()])
                writer.writerow(["# Dicke (mm)", self.input_thickness.text()])
                writer.writerow([])
                
                # Spaltenköpfe
                writer.writerow(['Zeit (s)', 'Kraft (N)', 'Weg (mm)', 'Spannung (MPa)', 'Dehnung (%)'])
                
                # Datenzeilen
                for i in range(len(self.messdaten["time_s"])):
                    writer.writerow([
                        round(self.messdaten["time_s"][i], 4),
                        round(self.messdaten["force_N"][i], 3),
                        round(self.messdaten["disp_mm"][i], 4),
                        round(self.messdaten["stress_MPa"][i], 3),
                        round(self.messdaten["strain_pct"][i], 4)
                    ])
                    
            QMessageBox.information(self, "Erfolg", "Daten erfolgreich exportiert!")

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())