# Dateiname: TensileTestingMachineGUI.py

import sys
import csv
import time
import numpy as np
from datetime import datetime
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QGroupBox, 
                             QFileDialog, QMessageBox, QLineEdit, QFormLayout, QComboBox)
from PyQt5.QtCore import QThread, pyqtSignal
import pyqtgraph as pg

# Import the SerialStream class
from SerialStream import SerialStream

class MeasurementWorker(QThread):
    connection_status = pyqtSignal(bool, str)
    state_update = pyqtSignal(str)
    result_ready = pyqtSignal(object)
    
    def __init__(self, baudrate):
        super().__init__()
        self.baudrate = baudrate
        self.is_running = True
        self.serial_stream = None

    def run(self):
        while self.is_running:
            time.sleep(0.1)

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

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tensile Testing Machine - PES Board")
        self.resize(1150, 800)

        self.baudrate = int(2e6)
        self.messdaten = None

        self.worker = MeasurementWorker(self.baudrate)
        self.worker.connection_status.connect(self.on_connection_status)
        self.worker.state_update.connect(self.on_state_update)
        self.worker.result_ready.connect(self.on_result_ready)
        self.worker.start()

        self.init_ui()

    def init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout()

        # --- Top Bar (COM Port Connection) ---
        connection_group = QGroupBox("Connection (STM32)")
        conn_layout = QHBoxLayout()
        
        self.cb_ports = QComboBox()
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

        # --- Middle Area ---
        mid_layout = QHBoxLayout()

        left_col = QVBoxLayout()
        
        # Specimen Parameters
        param_group = QGroupBox("Specimen Parameters")
        param_layout = QFormLayout()
        self.input_name = QLineEdit("Specimen_01")
        self.input_length = QLineEdit("50.0")
        self.input_width = QLineEdit("10.0")
        self.input_thickness = QLineEdit("2.0")
        param_layout.addRow("Specimen Name:", self.input_name)
        param_layout.addRow("Initial Length L0 (mm):", self.input_length)
        param_layout.addRow("Width (mm):", self.input_width)
        param_layout.addRow("Thickness (mm):", self.input_thickness)
        param_group.setLayout(param_layout)
        left_col.addWidget(param_group)

        # Machine Control
        ctrl_group = QGroupBox("Machine Control")
        ctrl_layout = QVBoxLayout()
        
        btn_ref = QPushButton("1. Homing (Reference Run)")
        btn_ref.setStyleSheet("background-color: #f0e68c;")
        btn_ref.clicked.connect(lambda: self.worker.send_command("HOMING"))
        
        btn_up = QPushButton("Motor UP (Jog)")
        btn_up.clicked.connect(lambda: self.worker.send_command("JOG_UP"))
        
        btn_down = QPushButton("Motor DOWN (Jog)")
        btn_down.clicked.connect(lambda: self.worker.send_command("JOG_DOWN"))
        
        btn_tare = QPushButton("2. TARE (Zero Force/Displacement)")
        btn_tare.clicked.connect(lambda: self.worker.send_command("TARE"))
        
        ctrl_layout.addWidget(btn_ref)
        ctrl_layout.addWidget(btn_up)
        ctrl_layout.addWidget(btn_down)
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
        
        btn_start = QPushButton("3. START TENSILE TEST")
        btn_start.setStyleSheet("background-color: lightgreen; height: 50px; font-weight: bold;")
        btn_start.clicked.connect(self.start_measurement)
        
        btn_stop = QPushButton("EMERGENCY STOP")
        btn_stop.setStyleSheet("background-color: salmon; height: 50px; font-weight: bold;")
        btn_stop.clicked.connect(lambda: self.worker.send_command("STOP"))
        
        btn_reset = QPushButton("RESET")
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

    def start_measurement(self):
        self.plot_line.setData([], [])
        self.messdaten = None
        self.worker.start_measurement()

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

    def closeEvent(self, event):
        self.worker.stop()
        self.worker.wait()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())