# Dateiname: TensileTestingMachineGUI.py

import sys
import csv
import time
import numpy as np
from datetime import datetime
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QGroupBox, 
                             QFileDialog, QMessageBox, QLineEdit, QFormLayout, QComboBox, QTextEdit)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
import pyqtgraph as pg

# Import the SerialStream class
from SerialStream import SerialStream

import threading

class MeasurementWorker(QThread):
    connection_status = pyqtSignal(bool, str)
    state_update = pyqtSignal(str)
    result_ready = pyqtSignal(object)
    log_message = pyqtSignal(str)
    live_data = pyqtSignal(float, float, float)
    
    def __init__(self, baudrate):
        super().__init__()
        self.baudrate = baudrate
        self.is_running = True
        self.serial_stream = None

    def run(self):
        while self.is_running:
            if self.serial_stream and self.serial_stream.SerialPort.is_open:
                try:
                    if self.serial_stream.SerialPort.in_waiting > 0:
                        line = self.serial_stream.SerialPort.readline().decode('utf-8', errors='ignore').strip()
                        if line:
                            self.log_message.emit(line) # Immer im Monitor anzeigen
                            
                            # Wenn es eine Datenzeile ist (enthält Semikolon, kein Text)
                            if ";" in line and "State" not in line:
                                parts = line.split(";")
                                if len(parts) == 3:
                                    t = float(parts[0])
                                    f = float(parts[1])
                                    d = float(parts[2])
                                    self.live_data.emit(t, f, d) # An den Graphen senden
                except Exception:
                    pass
            time.sleep(0.01)

    def connect_port(self, port):
        try:
            self.connection_status.emit(True, f"Connecting to {port}...")
            self.serial_stream = SerialStream(port, self.baudrate)
            self.connection_status.emit(True, f"Connected ({port} @ {self.baudrate} Baud)")
            return True
        except Exception as e:
            self.connection_status.emit(False, f"Error: {e}")
            self.serial_stream = None
            return False

    def disconnect_port(self):
        if self.serial_stream:
            try:
                if self.serial_stream.SerialPort.is_open:
                    self.serial_stream.SerialPort.close()
                self.serial_stream = None
                self.connection_status.emit(False, "Not connected")
            except Exception as e:
                self.connection_status.emit(False, f"Error disconnecting: {e}")

    def start_measurement(self):
        if not self.serial_stream:
            self.state_update.emit("ERROR: Not connected")
            return

        self.state_update.emit("MEASUREMENT RUNNING... (Waiting for STM32)")
        try:
            self.serial_stream.reset()
            self.serial_stream.start()

            while self.serial_stream.is_busy_flag():
                time.sleep(0.1)
                
            data = self.serial_stream.get_data()
            self.state_update.emit("MEASUREMENT FINISHED")
            self.result_ready.emit(data)

        except Exception as e:
            self.state_update.emit(f"ERROR: {e}")


    def send_command(self, cmd_string):
        if self.serial_stream and self.serial_stream.SerialPort.is_open:
            self.serial_stream.SerialPort.write(f"{cmd_string}\n".encode('utf-8'))

    def stop(self):
        self.is_running = False
        self.disconnect_port()

    def pop_buffers(self):
        with self.lock:
            logs = self.log_buffer[:]
            points = self.plot_buffer[:]
            self.log_buffer.clear()
            self.plot_buffer.clear()
        return logs, points

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tensile Testing Machine - PES Board")
        self.resize(1150, 800)

        self.baudrate = int(115200)
        self.messdaten = None


        self.live_strain = []
        self.live_stress = []

        self.worker = MeasurementWorker(self.baudrate)
        self.worker.connection_status.connect(self.on_connection_status)
        self.worker.log_message.connect(self.append_log)
        self.worker.state_update.connect(self.on_state_update)
        self.worker.result_ready.connect(self.on_result_ready)
        self.worker.live_data.connect(self.update_live_plot)
        self.worker.start()

        self.init_ui()


# UI-Refresh auf 25 FPS (alle 40 ms) drosseln für maximale Performance
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_ui_batch)
        self.ui_timer.start(40)

    def send_machine_parameters(self):
        try:
            pull_time_ms = int(float(self.input_pull_time.text().replace(',', '.')))
            pull_speed = float(self.input_pull_speed.text().replace(',', '.'))
            jog_speed = float(self.input_jog_speed.text().replace(',', '.'))
            calib_factor = float(self.input_calib.text().replace(',', '.'))
        except ValueError:
            QMessageBox.warning(self, "Error", "Please enter valid numeric values for time and speeds.")
            return False

        self.worker.send_command(f"TIME:{pull_time_ms}")
        self.worker.send_command(f"PULL_SPEED:{pull_speed}")
        self.worker.send_command(f"JOG_SPD:{jog_speed}")
        self.worker.send_command(f"CALIB:{calib_factor}")
        return True

    def append_log(self, text):
        self.text_log.append(text)
        self.text_log.verticalScrollBar().setValue(self.text_log.verticalScrollBar().maximum())

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        # --- Top Bar (COM Port Connection) ---
        connection_group = QGroupBox("Connection (STM32)")
        conn_layout = QHBoxLayout()
        
        self.cb_ports = QComboBox()
        self.cb_ports.view().parentWidget().setMinimumWidth(200)
        self.refresh_ports()
        
        btn_refresh = QPushButton("↻ Scan Ports")
        btn_refresh.clicked.connect(self.refresh_ports)
        
        self.btn_connect = QPushButton("Connect")
        self.btn_connect.clicked.connect(self.connect_serial)
        
        self.btn_disconnect = QPushButton("Disconnect")
        self.btn_disconnect.clicked.connect(self.disconnect_serial)
        self.btn_disconnect.setEnabled(False)

        self.lbl_conn = QLabel("Not connected")
        self.lbl_conn.setStyleSheet("color: red; font-weight: bold;")
        
        self.lbl_state = QLabel("Status: READY")
        self.lbl_state.setStyleSheet("font-weight: bold; font-size: 14px; margin-left: 20px;")

        conn_layout.addWidget(QLabel("COM Port:"))
        conn_layout.addWidget(self.cb_ports)
        conn_layout.addWidget(btn_refresh)
        conn_layout.addWidget(self.btn_connect)
        conn_layout.addWidget(self.btn_disconnect)
        conn_layout.addWidget(self.lbl_conn)
        conn_layout.addStretch()
        conn_layout.addWidget(self.lbl_state)
        connection_group.setLayout(conn_layout)
        main_layout.addWidget(connection_group)



        # --- STM32 Empfangs-Monitor ---
        log_group = QGroupBox("STM32 Received Data (Serial Monitor)")
        log_layout = QVBoxLayout()
        
        self.text_log = QTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setMaximumHeight(150)
        self.text_log.document().setMaximumBlockCount(500)  # Begrenzt Verlauf auf 500 Zeilen
        self.text_log.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas; font-size: 11px;")
        
        btn_clear_log = QPushButton("Clear Monitor")
        btn_clear_log.clicked.connect(self.text_log.clear)
        
        log_layout.addWidget(self.text_log)
        log_layout.addWidget(btn_clear_log)
        log_group.setLayout(log_layout)
        
        main_layout.addWidget(log_group)

        # --- Middle Area ---
        mid_layout = QHBoxLayout()

        left_col = QVBoxLayout()
        
        # Specimen Parameters
        param_group = QGroupBox("Specimen Parameters")
        param_layout = QFormLayout()
        self.input_name = QLineEdit("Specimen_01")
        self.input_length = QLineEdit("60.0")
        self.input_width = QLineEdit("3.0")
        self.input_thickness = QLineEdit("2.0")
        param_layout.addRow("Specimen Name:", self.input_name)
        param_layout.addRow("Initial Length L0 (mm):", self.input_length)
        param_layout.addRow("Width (mm):", self.input_width)
        param_layout.addRow("Thickness (mm):", self.input_thickness)
        param_group.setLayout(param_layout)
        left_col.addWidget(param_group)

        # Machine Parameters (TIME, PULL_SPEED, JOG_SPD)
        mach_param_group = QGroupBox("Test & Motor Parameters")
        mach_param_layout = QFormLayout()
        self.input_pull_time = QLineEdit("5000")
        self.input_pull_speed = QLineEdit("2.0")
        self.input_jog_speed = QLineEdit("1.0")
        self.input_calib = QLineEdit("358.0")
        btn_send_params = QPushButton("Apply Parameters")
        btn_send_params.clicked.connect(self.send_machine_parameters)
        mach_param_layout.addRow("Pull Time (ms):", self.input_pull_time)
        mach_param_layout.addRow("Pull Speed:", self.input_pull_speed)
        mach_param_layout.addRow("Jog Speed:", self.input_jog_speed)
        mach_param_layout.addRow("Calibration Factor:", self.input_calib)
        mach_param_layout.addRow(btn_send_params)
        mach_param_group.setLayout(mach_param_layout)
        left_col.addWidget(mach_param_group)

        # Machine Control
        ctrl_group = QGroupBox("Machine Control")
        ctrl_layout = QVBoxLayout()
        
        btn_ref = QPushButton("1. Homing (Reference Run (future...))")
        btn_ref.setStyleSheet("background-color: #f0e68c;")
        btn_ref.clicked.connect(lambda: self.worker.send_command("HOMING"))
        
        btn_fwd = QPushButton("Motor FWD (Jog)")
        btn_fwd.pressed.connect(lambda: self.worker.send_command("JOG_FWD"))
        btn_fwd.released.connect(lambda: self.worker.send_command("JOG_STOP"))
        
        btn_rev = QPushButton("Motor REV (Jog)")
        btn_rev.pressed.connect(lambda: self.worker.send_command("JOG_REV"))
        btn_rev.released.connect(lambda: self.worker.send_command("JOG_STOP"))
        
        btn_tare = QPushButton("2. TARE (Zero Force/Displacement)")
        btn_tare.clicked.connect(lambda: self.worker.send_command("TARE"))
        
        ctrl_layout.addWidget(btn_ref)
        ctrl_layout.addWidget(btn_fwd)
        ctrl_layout.addWidget(btn_rev)
        ctrl_layout.addWidget(btn_tare)
        ctrl_group.setLayout(ctrl_layout)
        left_col.addWidget(ctrl_group)
        
        left_col.addStretch()
        mid_layout.addLayout(left_col, 1)

        # Right Column: Graph
        self.graphWidget = pg.PlotWidget(title="Stress-Strain Diagram")
        self.graphWidget.setLabel('left', 'Stress (MPa)', units='N/mm²')
        self.graphWidget.setLabel('bottom', 'Strain', units='%')
        self.graphWidget.showGrid(x=True, y=True)
        self.plot_line = self.graphWidget.plot([], [], pen=pg.mkPen('b', width=2))
        mid_layout.addWidget(self.graphWidget, 3)

        main_layout.addLayout(mid_layout)

        # --- Bottom Bar ---
        btn_layout = QHBoxLayout()
        
        btn_start = QPushButton("START TENSILE TEST")
        btn_start.setStyleSheet("background-color: lightgreen; height: 50px; font-weight: bold;")
        btn_start.clicked.connect(self.show_safety_popup)
        
        btn_stop = QPushButton("Schnellstop (NOT HALT!)")
        btn_stop.setStyleSheet("background-color: salmon; height: 50px; font-weight: bold;")
        btn_stop.clicked.connect(lambda: self.worker.send_command("E-STOP"))
        
        btn_reset = QPushButton("Measurement Data / RESET")
        btn_reset.setStyleSheet("height: 50px;")
        btn_reset.clicked.connect(lambda: self.worker.send_command("RESET"))
        
        btn_export = QPushButton("Export Data")
        btn_export.setStyleSheet("background-color: lightblue; height: 50px;")
        btn_export.clicked.connect(self.export_data)

        btn_layout.addWidget(btn_start)
        btn_layout.addWidget(btn_stop)
        btn_layout.addWidget(btn_reset)
        btn_layout.addWidget(btn_export)
        main_layout.addLayout(btn_layout)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)


    def refresh_ports(self):
        self.cb_ports.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.cb_ports.addItem(port.device)
        
        if not ports:
            self.cb_ports.addItem("No ports found")

    def connect_serial(self):
        port = self.cb_ports.currentText()
        if port and "No ports" not in port:
            if self.worker.connect_port(port):
                self.btn_connect.setEnabled(False)
                self.btn_disconnect.setEnabled(True)
                self.cb_ports.setEnabled(False)

    def disconnect_serial(self):
        self.worker.disconnect_port()
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.cb_ports.setEnabled(True)

    def on_connection_status(self, connected, msg):
        self.lbl_conn.setText(msg)
        self.lbl_conn.setStyleSheet("color: green; font-weight:bold;" if connected else "color: red; font-weight:bold;")
        if not connected:
            self.btn_connect.setEnabled(True)
            self.btn_disconnect.setEnabled(False)
            self.cb_ports.setEnabled(True)

    def on_state_update(self, state):
        self.lbl_state.setText(f"Status: {state}")

    def on_result_ready(self, data):
        time_array = data["time"]
        values_array = data["values"]

        if len(time_array) == 0:
            QMessageBox.warning(self, "Error", "No data received!")
            return

        if values_array.shape[1] < 2:
            QMessageBox.warning(self, "Warning", "The STM32 is not sending displacement yet! Please adjust the firmware.")
            return

        force_N = values_array[:, 0] 
        displacement_mm = values_array[:, 1]

        try:
            l0 = float(self.input_length.text().replace(',', '.'))
            width = float(self.input_width.text().replace(',', '.'))
            thickness = float(self.input_thickness.text().replace(',', '.'))
        except ValueError:
            QMessageBox.warning(self, "Error", "Please enter valid numbers for the specimen geometry.")
            return

        area_mm2 = width * thickness
        stress_MPa = force_N / area_mm2
        strain_pct = (displacement_mm / l0) * 100

        self.messdaten = {
            "time_s": time_array,
            "force_N": force_N,
            "disp_mm": displacement_mm,
            "stress_MPa": stress_MPa,
            "strain_pct": strain_pct
        }
        
        self.plot_line.setData(strain_pct, stress_MPa)
        QMessageBox.information(self, "Success", f"Test completed! {len(time_array)} data points calculated.")

    def update_ui_batch(self):  
        logs, points = self.worker.pop_buffers()

        if logs:
            self.text_log.append("\n".join(logs))
            self.text_log.verticalScrollBar().setValue(self.text_log.verticalScrollBar().maximum())

        if points:
            try:
                l0 = float(self.input_length.text().replace(',', '.'))
                width = float(self.input_width.text().replace(',', '.'))
                thickness = float(self.input_thickness.text().replace(',', '.'))
                area = width * thickness

                if area > 0 and l0 > 0:
                    for _, f, d in points:
                        self.live_stress.append(f / area)
                        self.live_strain.append((d / l0) * 100.0)
                    self.plot_line.setData(self.live_strain, self.live_stress)
            except ValueError:
                pass


    def start_measurement(self):
        if not self.send_machine_parameters():
            return
        
        # Listen für den neuen Test leeren
        self.live_strain.clear()
        self.live_stress.clear()
        self.plot_line.setData([], [])
        
        self.messdaten = None
        self.worker.send_command("START") # Start-Befehl an STM32
        threading.Thread(target=self.worker.start_measurement, daemon=True).start()


    def export_data(self):
        if self.messdaten is None:
            QMessageBox.warning(self, "Error", "No calculated measurement data available!")
            return
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        proben_name = self.input_name.text().replace(" ", "_")
        default_filename = f"{timestamp}_{proben_name}.csv"
            
        path, _ = QFileDialog.getSaveFileName(self, "Save Measurement Data", default_filename, "CSV Files (*.csv)")
        
        if path:
            with open(path, mode='w', newline='') as file:
                writer = csv.writer(file, delimiter=';')
                
                writer.writerow(["# Specimen Name", self.input_name.text()])
                writer.writerow(["# L0 (mm)", self.input_length.text()])
                writer.writerow(["# Width (mm)", self.input_width.text()])
                writer.writerow(["# Thickness (mm)", self.input_thickness.text()])
                writer.writerow([])
                
                writer.writerow(['Time (s)', 'Force (N)', 'Displacement (mm)', 'Stress (MPa)', 'Strain (%)'])
                
                for i in range(len(self.messdaten["time_s"])):
                    writer.writerow([
                        round(self.messdaten["time_s"][i], 4),
                        round(self.messdaten["force_N"][i], 3),
                        round(self.messdaten["disp_mm"][i], 4),
                        round(self.messdaten["stress_MPa"][i], 3),
                        round(self.messdaten["strain_pct"][i], 4)
                    ])
                    
            QMessageBox.information(self, "Success", "Data successfully exported!")

    def update_live_plot(self, t, f, d):
        try:
            # Geometrie auslesen
            l0 = float(self.input_length.text().replace(',', '.'))
            width = float(self.input_width.text().replace(',', '.'))
            thickness = float(self.input_thickness.text().replace(',', '.'))
            
            # Spannung und Dehnung berechnen
            area = width * thickness
            stress = f / area
            strain = (d / l0) * 100
            
            # An Listen anhängen
            self.live_strain.append(strain)
            self.live_stress.append(stress)
            
            # Graphen aktualisieren
            self.plot_line.setData(self.live_strain, self.live_stress)
        except ValueError:
            pass # Ignorieren, falls Benutzereingaben ungültig sind

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait()
        event.accept()

    def show_safety_popup(self):
        # Create a safety confirmation popup
        popup = QMessageBox(self)
        popup.setWindowTitle("Safety Confirmation")
        popup.setIcon(QMessageBox.Warning)
        popup.setText("Safety Conditions:\n\n1. Make sure that the Testing System is unloaded and empty.\n2. Make sure the area is clear of personnel.\n3. Wear appropriate safety gear.\n\nDo you want to proceed with the tensile test?")

        # Add Confirm and Cancel buttons
        btn_confirm = popup.addButton("Confirm", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        # Show the popup and wait for user response
        popup.exec_()
        if popup.clickedButton() == btn_confirm:
            self.show_reference_popup()

    def show_reference_popup(self):
        # Create referencing popup
        popup = QMessageBox(self)
        popup.setWindowTitle("Motor Referencing")
        popup.setIcon(QMessageBox.Information)

        # Display message
        popup.setText("Motor run in reference (Zero) position.")

        # Add buttons
        btn_start = popup.addButton("Start Referencing", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        # Show popup
        popup.exec_()
        
        if popup.clickedButton() == btn_start:
            # self.worker.send_command("HOMING") # Falls du später homing nutzt
            self.show_insert_specimen_popup()    # <-- DAS ÖFFNET NUN DAS "INSERT SPECIMEN" FENSTER!

    def show_insert_specimen_popup(self):
        # Create information popup
        popup = QMessageBox(self)
        popup.setWindowTitle("Insert Specimen")
        popup.setIcon(QMessageBox.Information)

        popup.setText(
            "Reference position reached!\n\n"
            "Please insert the specimen into the testing system and ensure it is positioned correctly.\n\n"
        )
        # Add buttons
        btn_confirm = popup.addButton("Confirm", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        # Show the popup and wait for user response
        popup.exec_()
        if popup.clickedButton() == btn_confirm:
            self.show_startup_popup()  # <-- Hier stand noch fälschlicherweise show_reference_popup!

    def show_startup_popup(self):
        # Create information popup
        popup = QMessageBox(self)
        popup.setWindowTitle("Start Tensile Test")
        popup.setIcon(QMessageBox.Information)

        popup.setText(
            "Specimen is inserted\n\n"
            "Hands are out of the testing area\n\n"
            "Start Tensile Test\n\n"
        )
        # Add buttons
        btn_start= popup.addButton("Start Test", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        # Show the popup and wait for user response
        popup.exec_()
        if popup.clickedButton() == btn_start:
            self.start_measurement() # Schnittstelle zu Micocontroller

    def show_remvoe_specimen_popup(self):
        # Create information popup
        popup = QMessageBox(self)
        popup.setWindowTitle("Remove Specimen")
        popup.setIcon(QMessageBox.Information)
    
        popup.setText(
            "Motor reached initional position again\n\n"
            "Please remove the specimen out of the testing system\n\n"
        )
        # Add buttons
        btn_confirm = popup.addButton("Confirm", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)
    
        # Show the popup and wait for user response
        #popup.exec_()
        #if popup.clickedButton() == btn_confirm:
        #   self.




        
if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())