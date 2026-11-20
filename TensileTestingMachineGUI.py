# Dateiname: TensileTestingMachineGUI.py

import sys
import csv
import re
import time
import threading
import numpy as np
from datetime import datetime
import serial
import serial.tools.list_ports
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QPushButton, QLabel, QGroupBox,
                             QFileDialog, QMessageBox, QLineEdit, QFormLayout,
                             QComboBox, QTextEdit)
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
import pyqtgraph as pg

# Exakter Zustandsvergleich ("State: 1" darf nicht auf "State: 10" matchen)
STATE_RE = re.compile(r"State:\s*(\d+)")
MAX_RX_BUFFER = 65536

# Zustandsmaschine der Firmware (enum MachineStep, Position = Enum-Wert)
MACHINE_STATES = [
    ("ST_IDLE", "Start state"),
    ("ST_REFERENCING", "Motor moves to initial position"),
    ("ST_WAIT_SPECIMEN", "Waiting for specimen insertion"),
    ("ST_PULL_BACK", "Pull back"),
    ("ST_FINISHED", "Finished"),
    ("ST_FAULT", "Fault"),
]



class MeasurementWorker(QThread):
    connection_status = pyqtSignal(bool, str)
    state_update = pyqtSignal(str)
    # (Messpunkte, Abbruch/Fault-Flag)
    measurement_finished = pyqtSignal(list, bool)

    def __init__(self, baudrate):
        super().__init__()
        self.baudrate = baudrate
        self.is_running = True
        self.ser = None
        self.rx_buffer = b""

        self.lock = threading.Lock()
        self.log_buffer = []
        self.plot_buffer = []
        self.recorded_data = []

        self.is_recording = False
        self.saw_pull_state = False

    @property
    def is_connected(self):
        ser = self.ser
        return ser is not None and ser.is_open

    def run(self):
        while self.is_running:
            ser = self.ser
            if ser is not None:
                try:
                    if ser.is_open:
                        waiting = ser.in_waiting
                        if waiting > 0:
                            # Rohbytes puffern, damit unvollstaendige Zeilen
                            # nicht in zwei Teile zerrissen werden
                            self.rx_buffer += ser.read(waiting)
                            if len(self.rx_buffer) > MAX_RX_BUFFER and b"\n" not in self.rx_buffer:
                                self.rx_buffer = b""
                            while b"\n" in self.rx_buffer:
                                raw, self.rx_buffer = self.rx_buffer.split(b"\n", 1)
                                line = raw.decode('utf-8', errors='ignore').strip()
                                if line:
                                    self._process_line(line)
                except (serial.SerialException, OSError) as e:
                    # Nur melden, wenn der Port nicht absichtlich geschlossen wurde
                    if self.ser is ser:
                        self._handle_connection_lost(ser, e)
                except Exception as e:
                    with self.lock:
                        self.log_buffer.append(f"[GUI] Internal error: {e}")
            time.sleep(0.005)

    def _process_line(self, line):
        finished_snapshot = None
        fault = False

        with self.lock:
            self.log_buffer.append(line)

            match = STATE_RE.search(line)
            if match:
                state = int(match.group(1))
                if state == 1:
                    self.saw_pull_state = True
                elif self.is_recording and self.saw_pull_state and state in (2, 3):
                    self.is_recording = False
                    finished_snapshot = list(self.recorded_data)
                    fault = (state == 3)
            elif ";" in line and "State" not in line:
                parts = line.split(";")
                if len(parts) == 3:
                    try:
                        t = float(parts[0])
                        f = float(parts[1])
                        d = float(parts[2])
                        self.plot_buffer.append((t, f, d))
                        # Aufzeichnung erst ab Zugbeginn (State: 1)
                        if self.is_recording and self.saw_pull_state:
                            self.recorded_data.append((t, f, d))
                    except ValueError:
                        pass

        if finished_snapshot is not None:
            self.state_update.emit("FAULT / E-STOP" if fault else "MEASUREMENT FINISHED")
            self.measurement_finished.emit(finished_snapshot, fault)

    def _handle_connection_lost(self, ser, error):
        self.ser = None
        self.rx_buffer = b""
        try:
            ser.close()
        except Exception:
            pass

        with self.lock:
            was_recording = self.is_recording
            self.is_recording = False
            self.log_buffer.append(f"[GUI] Connection lost: {error}")

        self.connection_status.emit(False, "Connection lost")
        if was_recording:
            self.state_update.emit("CONNECTION LOST - DATA INCOMPLETE")

    def pop_buffers(self):
        with self.lock:
            logs = self.log_buffer[:]
            points = self.plot_buffer[:]
            self.log_buffer.clear()
            self.plot_buffer.clear()
        return logs, points

    def start_recording(self):
        with self.lock:
            self.recorded_data.clear()
            self.is_recording = True
            self.saw_pull_state = False
        self.state_update.emit("MEASUREMENT RUNNING...")

    def stop_recording(self):
        with self.lock:
            self.is_recording = False
            return list(self.recorded_data)

    def snapshot_recorded(self):
        """Kopie der bisherigen Daten, ohne die Aufzeichnung zu beenden."""
        with self.lock:
            return list(self.recorded_data)

    def connect_port(self, port):
        try:
            self.connection_status.emit(True, f"Connecting to {port}...")
            self.rx_buffer = b""
            self.ser = serial.Serial(port, self.baudrate, timeout=0.05)
            self.connection_status.emit(True, f"Connected ({port} @ {self.baudrate} Baud)")
            return True
        except Exception as e:
            self.connection_status.emit(False, f"Error: {e}")
            self.ser = None
            return False

    def disconnect_port(self):
        ser = self.ser
        self.ser = None  # zuerst auf None setzen, damit run() keinen Fehler meldet
        self.rx_buffer = b""
        if ser:
            try:
                if ser.is_open:
                    ser.close()
                self.connection_status.emit(False, "Not connected")
            except Exception as e:
                self.connection_status.emit(False, f"Error disconnecting: {e}")

    def send_command(self, cmd_string):
        ser = self.ser
        if ser and ser.is_open:
            try:
                ser.write(f"{cmd_string}\n".encode('utf-8'))
            except Exception as e:
                with self.lock:
                    self.log_buffer.append(f"[GUI] Send error ({cmd_string}): {e}")

    def stop(self):
        self.is_running = False
        self.disconnect_port()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tensile Testing Machine - PES Board")
        self.resize(1150, 800)

        self.baudrate = 115200
        self.messdaten = None
        # Probenparameter, die beim Start einer Messung eingefroren werden
        self.meas_params = None

        self.live_strain = []
        self.live_stress = []

        self.worker = MeasurementWorker(self.baudrate)
        self.worker.connection_status.connect(self.on_connection_status)
        self.worker.state_update.connect(self.on_state_update)
        self.worker.measurement_finished.connect(self.on_measurement_finished)
        self.worker.start()

        self.init_ui()

        # UI-Refresh auf 25 FPS (alle 40 ms) drosseln für maximale Performance
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_ui_batch)
        self.ui_timer.start(40)

    # ------------------------------------------------------------------
    # Hilfsfunktionen
    # ------------------------------------------------------------------
    @staticmethod
    def _to_float(text):
        return float(text.replace(',', '.'))

    def _read_geometry_fields(self):
        """Liest L0, Breite, Dicke aus den Eingabefeldern. Wirft ValueError bei ungültigen Werten."""
        l0 = self._to_float(self.input_length.text())
        width = self._to_float(self.input_width.text())
        thickness = self._to_float(self.input_thickness.text())
        if l0 <= 0 or width <= 0 or thickness <= 0:
            raise ValueError("Geometry values must be greater than zero")
        return l0, width, thickness

    def _active_geometry(self):
        """Eingefrorene Geometrie der laufenden/letzten Messung, sonst die Eingabefelder."""
        if self.meas_params:
            p = self.meas_params
            return p["l0"], p["width"], p["thickness"]
        return self._read_geometry_fields()

    def _specimen_info(self):
        if self.meas_params:
            return self.meas_params
        return {
            "name": self.input_name.text(),
            "l0": self.input_length.text(),
            "width": self.input_width.text(),
            "thickness": self.input_thickness.text(),
        }

    def _require_connection(self):
        if not self.worker.is_connected:
            QMessageBox.warning(self, "Not connected", "Please connect to the STM32 first.")
            return False
        return True

    def send_machine_parameters(self):
        if not self._require_connection():
            return False
        try:
            pull_time_ms = int(self._to_float(self.input_pull_time.text()))
            pull_speed = self._to_float(self.input_pull_speed.text())
            jog_speed = self._to_float(self.input_jog_speed.text())
            calib_factor = self._to_float(self.input_calib.text())
        except ValueError:
            QMessageBox.warning(self, "Error", "Please enter valid numeric values for time and speeds.")
            return False

        self.worker.send_command(f"TIME:{pull_time_ms}")
        self.worker.send_command(f"PULL_SPEED:{pull_speed}")
        self.worker.send_command(f"JOG_SPD:{jog_speed}")
        self.worker.send_command(f"CALIB:{calib_factor}")
        return True

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
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
        self.text_log.document().setMaximumBlockCount(500)
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

                # State Legend (enum MachineStep der Firmware)
        legend_group = QGroupBox("State Legend (Firmware)")
        legend_layout = QVBoxLayout()
        rows = "".join(
            f"<tr><td align='right'><b>{i}</b>&nbsp;</td>"
            f"<td><code>{name}</code>&nbsp;&nbsp;</td><td>{desc}</td></tr>"
            for i, (name, desc) in enumerate(MACHINE_STATES)
        )
        lbl_legend = QLabel(f"<table cellspacing='2'>{rows}</table>")
        lbl_legend.setStyleSheet("font-size: 11px;")
        legend_layout.addWidget(lbl_legend)
        legend_group.setLayout(legend_layout)
        left_col.addWidget(legend_group)

        # Machine Parameters (TIME, PULL_SPEED, JOG_SPD)
        mach_param_group = QGroupBox("Test & Motor Parameters")
        mach_param_layout = QFormLayout()
        self.input_pull_time = QLineEdit("5000")
        self.input_pull_speed = QLineEdit("2.0")
        self.input_jog_speed = QLineEdit("1.0")
        self.input_calib = QLineEdit("-21630.5")
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

        btn_ref = QPushButton("1. Referencing (Reference Run)")
        btn_ref.setStyleSheet("background-color: #f0e68c;")
        btn_ref.clicked.connect(lambda: self.worker.send_command("REFERENCE_START"))

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
        self.graphWidget.setLabel('left', 'Stress', units='MPa')
        self.graphWidget.setLabel('bottom', 'Strain', units='%')
        # Keine automatischen SI-Präfixe (sonst z. B. "kMPa" oder "m%")
        self.graphWidget.getAxis('left').enableAutoSIPrefix(False)
        self.graphWidget.getAxis('bottom').enableAutoSIPrefix(False)
        self.graphWidget.showGrid(x=True, y=True)
        self.plot_line = self.graphWidget.plot([], [], pen=pg.mkPen('b', width=2))
        mid_layout.addWidget(self.graphWidget, 3)

        main_layout.addLayout(mid_layout)

        # --- Bottom Bar ---
        btn_layout = QHBoxLayout()

        btn_start = QPushButton("START TENSILE TEST")
        btn_start.setStyleSheet("background-color: lightgreen; height: 50px; font-weight: bold;")
        btn_start.clicked.connect(self.show_safety_popup)

        btn_stop = QPushButton("EMERGENCY STOP")
        btn_stop.setStyleSheet("background-color: salmon; height: 50px; font-weight: bold;")
        btn_stop.clicked.connect(self.trigger_estop)

        btn_reset = QPushButton("RESET Machine")
        btn_reset.setStyleSheet("height: 50px;")
        btn_reset.clicked.connect(self.reset_machine)

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

    # ------------------------------------------------------------------
    # Verbindung
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Live-Anzeige
    # ------------------------------------------------------------------
    def update_ui_batch(self):
        logs, points = self.worker.pop_buffers()

        if logs:
            self.text_log.append("\n".join(logs))
            self.text_log.verticalScrollBar().setValue(self.text_log.verticalScrollBar().maximum())

        if points:
            try:
                l0, width, thickness = self._active_geometry()
                area = width * thickness

                for _, f, d in points:
                    self.live_stress.append(f / area)
                    self.live_strain.append((d / l0) * 100.0)
                self.plot_line.setData(self.live_strain, self.live_stress)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Messung
    # ------------------------------------------------------------------
    def start_measurement(self):
        if not self._require_connection():
            return

        try:
            l0, width, thickness = self._read_geometry_fields()
        except ValueError:
            QMessageBox.warning(self, "Error", "Please enter valid specimen geometry values (greater than zero).")
            return

        if not self.send_machine_parameters():
            return

        # Probenparameter für diese Messung einfrieren
        self.meas_params = {
            "name": self.input_name.text(),
            "l0": l0,
            "width": width,
            "thickness": thickness,
        }

        self.live_strain.clear()
        self.live_stress.clear()
        self.plot_line.setData([], [])
        self.messdaten = None

        self.worker.start_recording()
        self.worker.send_command("START")

    def trigger_estop(self):
        self.worker.send_command("E-STOP")
        fallback_points = self.worker.stop_recording()
        if fallback_points and self.messdaten is None:
            try:
                self.messdaten = self._build_messdaten_dict(fallback_points)
            except ValueError:
                pass
        self.on_state_update("E-STOP TRIGGERED")

    def reset_machine(self):
        self.worker.send_command("RESET")
        self.on_state_update("READY")

    def _build_messdaten_dict(self, raw_points):
        if not raw_points:
            return None

        arr = np.array(raw_points, dtype=float)
        time_array = arr[:, 0]
        force_N = arr[:, 1]
        displacement_mm = arr[:, 2]

        l0, width, thickness = self._active_geometry()

        area_mm2 = width * thickness
        stress_MPa = force_N / area_mm2
        strain_pct = (displacement_mm / l0) * 100.0

        return {
            "time_s": time_array,
            "force_N": force_N,
            "disp_mm": displacement_mm,
            "stress_MPa": stress_MPa,
            "strain_pct": strain_pct
        }

    def on_measurement_finished(self, raw_points, fault):
        if not raw_points:
            QMessageBox.warning(self, "Error", "No data received during measurement!")
            return

        try:
            self.messdaten = self._build_messdaten_dict(raw_points)
            self.plot_line.setData(self.messdaten["strain_pct"], self.messdaten["stress_MPa"])
        except ValueError:
            QMessageBox.warning(self, "Error", "Invalid specimen geometry values.")
            return

        n = len(self.messdaten['time_s'])
        if fault:
            QMessageBox.warning(
                self, "Test aborted",
                f"The test was aborted by a fault / E-STOP.\n{n} data points were recorded until the abort."
            )
        else:
            QMessageBox.information(self, "Success", f"Test completed! {n} data points recorded.")
            self.show_remove_specimen_popup()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def export_data(self):
        if self.messdaten is None:
            # Aufzeichnung läuft weiter, es wird nur eine Kopie genommen
            fallback_points = self.worker.snapshot_recorded()
            if fallback_points:
                try:
                    self.messdaten = self._build_messdaten_dict(fallback_points)
                except ValueError:
                    pass

        if self.messdaten is None:
            QMessageBox.warning(self, "Error", "No calculated measurement data available!")
            return

        info = self._specimen_info()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        proben_name = str(info["name"]).replace(" ", "_")
        default_filename = f"{timestamp}_{proben_name}.csv"

        path, _ = QFileDialog.getSaveFileName(self, "Save Measurement Data", default_filename, "CSV Files (*.csv)")

        if path:
            try:
                with open(path, mode='w', newline='', encoding='utf-8') as file:
                    writer = csv.writer(file, delimiter=';')

                    writer.writerow(["# Specimen Name", info["name"]])
                    writer.writerow(["# L0 (mm)", info["l0"]])
                    writer.writerow(["# Width (mm)", info["width"]])
                    writer.writerow(["# Thickness (mm)", info["thickness"]])
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
            except Exception as e:
                QMessageBox.critical(self, "Export Error", f"Could not save file:\n{e}")

    def closeEvent(self, event):
        self.ui_timer.stop()
        self.worker.stop()
        self.worker.wait()
        event.accept()

    # ------------------------------------------------------------------
    # Ablauf-Popups
    # ------------------------------------------------------------------
    def show_safety_popup(self):
        popup = QMessageBox(self)
        popup.setWindowTitle("Safety Confirmation")
        popup.setIcon(QMessageBox.Warning)
        popup.setText("Safety Conditions:\n\n1. Make sure that the Testing System is unloaded and empty.\n2. Make sure the area is clear of personnel.\n3. Wear appropriate safety gear.\n\nDo you want to proceed with the tensile test?")

        btn_confirm = popup.addButton("Confirm", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        popup.exec_()
        if popup.clickedButton() == btn_confirm:
            self.show_reference_popup()

    def show_reference_popup(self):
        popup = QMessageBox(self)
        popup.setWindowTitle("Motor Referencing")
        popup.setIcon(QMessageBox.Information)
        popup.setText("Motor runs to the reference (zero) position.")

        btn_start = popup.addButton("Start Referencing", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        popup.exec_()
        if popup.clickedButton() == btn_start:
            if not self._require_connection():
                return
            self.worker.send_command("REFERENCE_START")
            self.show_insert_specimen_popup()

    def show_insert_specimen_popup(self):
        popup = QMessageBox(self)
        popup.setWindowTitle("Insert Specimen")
        popup.setIcon(QMessageBox.Information)
        popup.setText(
            "Wait until the motor has stopped at the reference position.\n\n"
            "Then insert the specimen into the testing system and ensure it is positioned correctly.\n\n"
        )
        btn_confirm = popup.addButton("Confirm", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        # 20.11.26
        popup.exec_()
        if popup.clickedButton() == btn_confirm:
            self.show_startup_popup()
            if not self._require_connection():
                return
            self.worker.send_command("ConfirmSpecimenInserted")
            self.show_startup_popup()

    def show_startup_popup(self):
        popup = QMessageBox(self)
        popup.setWindowTitle("Start Tensile Test")
        popup.setIcon(QMessageBox.Information)
        popup.setText(
            "Specimen is inserted\n\n"
            "Hands are out of the testing area\n\n"
            "Start Tensile Test\n\n"
        )
        btn_start = popup.addButton("Start Test", QMessageBox.AcceptRole)
        popup.addButton("Cancel", QMessageBox.RejectRole)

        popup.exec_()
        if popup.clickedButton() == btn_start:
            self.start_measurement()

    def show_remove_specimen_popup(self):
        QMessageBox.information(
            self, "Remove Specimen",
            "Wait until the motor has stopped.\n\n"
            "Then remove the specimen from the testing system."
        )


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())