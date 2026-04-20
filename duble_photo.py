#!/usr/bin/env python3
"""
Программа для поиска дублей фотографий в указанной папке и всех вложенных папках.
Использует PyQt5 для GUI и SQLite для хранения базы данных хэшей изображений.
"""

import os
import sys
import hashlib
import sqlite3
from pathlib import Path
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QProgressBar, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QSplitter
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap


class ImageHasher:
    """Класс для вычисления хэша изображения (pHash упрощенный)."""
    
    @staticmethod
    def compute_hash(image_path):
        """Вычисляет perceptual hash изображения."""
        try:
            from PIL import Image
        except ImportError:
            # Если PIL не установлен, используем простой MD5 хэш файла
            return ImageHasher._compute_md5(image_path)
        
        try:
            with Image.open(image_path) as img:
                # Преобразуем в оттенки серого и уменьшаем до 8x8
                img = img.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
                pixels = list(img.getdata())
                
                # Вычисляем среднее значение
                avg = sum(pixels) / len(pixels)
                
                # Создаем хэш: 1 если пиксель > среднего, иначе 0
                hash_bits = ''.join('1' if pixel > avg else '0' for pixel in pixels)
                
                # Преобразуем в шестнадцатеричную строку
                return hex(int(hash_bits, 2))[2:].zfill(16)
        except Exception:
            return ImageHasher._compute_md5(image_path)
    
    @staticmethod
    def _compute_md5(file_path):
        """Вычисляет MD5 хэш файла как резервный вариант."""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception:
            return None
    
    @staticmethod
    def hamming_distance(hash1, hash2):
        """Вычисляет расстояние Хэмминга между двумя хэшами."""
        if not hash1 or not hash2 or len(hash1) != len(hash2):
            return float('inf')
        
        try:
            int1 = int(hash1, 16)
            int2 = int(hash2, 16)
            xor = int1 ^ int2
            return bin(xor).count('1')
        except ValueError:
            return float('inf')


class DatabaseManager:
    """Управление базой данных SQLite."""
    
    def __init__(self, db_path="image_hashes.db"):
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self._connect()
        self._create_tables()
    
    def _connect(self):
        """Подключение к базе данных."""
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
    
    def _create_tables(self):
        """Создание таблиц если они не существуют."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                file_size INTEGER,
                scan_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS duplicates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash_group TEXT NOT NULL,
                file_path TEXT NOT NULL,
                FOREIGN KEY (file_path) REFERENCES images(file_path)
            )
        ''')
        self.conn.commit()
    
    def add_image(self, file_path, file_hash, file_size):
        """Добавление изображения в базу данных."""
        try:
            self.cursor.execute('''
                INSERT OR REPLACE INTO images (file_path, file_hash, file_size)
                VALUES (?, ?, ?)
            ''', (file_path, file_hash, file_size))
            self.conn.commit()
        except sqlite3.Error as e:
            print(f"Ошибка БД: {e}")
    
    def get_all_images(self):
        """Получение всех изображений из базы данных."""
        self.cursor.execute('SELECT file_path, file_hash, file_size FROM images')
        return self.cursor.fetchall()
    
    def clear_database(self):
        """Очистка базы данных."""
        self.cursor.execute('DELETE FROM images')
        self.cursor.execute('DELETE FROM duplicates')
        self.conn.commit()
    
    def close(self):
        """Закрытие соединения с базой данных."""
        if self.conn:
            self.conn.close()


class ScanWorker(QThread):
    """Рабочий поток для сканирования файлов."""
    
    progress_signal = pyqtSignal(int, str)  # прогресс, текущий файл
    found_signal = pyqtSignal(str, str, int)  # путь, хэш, размер
    finished_signal = pyqtSignal(list)  # список всех найденных файлов
    error_signal = pyqtSignal(str)
    
    def __init__(self, folder_path, db_manager):
        super().__init__()
        self.folder_path = folder_path
        self.db_manager = db_manager
        self.is_supported_image = lambda p: p.lower().endswith(
            ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp')
        )
    
    def run(self):
        """Основной метод потока."""
        try:
            all_files = []
            image_files = []
            
            # Сбор всех файлов
            for root, dirs, files in os.walk(self.folder_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    all_files.append(file_path)
                    
                    if self.is_supported_image(file_path):
                        image_files.append(file_path)
            
            total = len(image_files)
            processed = 0
            
            for file_path in image_files:
                try:
                    file_size = os.path.getsize(file_path)
                    file_hash = ImageHasher.compute_hash(file_path)
                    
                    if file_hash:
                        self.db_manager.add_image(file_path, file_hash, file_size)
                        self.found_signal.emit(file_path, file_hash, file_size)
                    
                    processed += 1
                    self.progress_signal.emit(int(100 * processed / total), file_path)
                    
                except Exception as e:
                    self.error_signal.emit(f"Ошибка обработки {file_path}: {str(e)}")
            
            self.finished_signal.emit(image_files)
            
        except Exception as e:
            self.error_signal.emit(f"Критическая ошибка: {str(e)}")


class DuplicateFinderWorker(QThread):
    """Рабочий поток для поиска дублей."""
    
    progress_signal = pyqtSignal(int, str)
    duplicate_found_signal = pyqtSignal(list)  # список групп дублей
    finished_signal = pyqtSignal()
    error_signal = pyqtSignal(str)
    
    def __init__(self, db_manager):
        super().__init__()
        self.db_manager = db_manager
    
    def run(self):
        """Поиск дублей по хэшам."""
        try:
            images = self.db_manager.get_all_images()
            
            # Группировка по хэшам
            hash_groups = {}
            for file_path, file_hash, file_size in images:
                if file_hash not in hash_groups:
                    hash_groups[file_hash] = []
                hash_groups[file_hash].append({
                    'path': file_path,
                    'size': file_size,
                    'hash': file_hash
                })
            
            # Поиск групп с более чем одним файлом
            duplicates = []
            total_groups = len(hash_groups)
            processed = 0
            
            for file_hash, files in hash_groups.items():
                if len(files) > 1:
                    duplicates.append(files)
                
                processed += 1
                self.progress_signal.emit(int(100 * processed / total_groups), f"Анализ группы {file_hash[:8]}...")
            
            self.duplicate_found_signal.emit(duplicates)
            self.finished_signal.emit()
            
        except Exception as e:
            self.error_signal.emit(f"Ошибка поиска дублей: {str(e)}")


class MainWindow(QMainWindow):
    """Главное окно приложения."""
    
    def __init__(self):
        super().__init__()
        self.db_manager = None
        self.scan_worker = None
        self.finder_worker = None
        self.current_duplicates = []
        
        self.init_ui()
    
    def init_ui(self):
        """Инициализация пользовательского интерфейса."""
        self.setWindowTitle("Поиск дублей фотографий")
        self.setGeometry(100, 100, 1200, 800)
        
        # Центральный виджет
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Основной макет
        main_layout = QVBoxLayout(central_widget)
        
        # Верхняя панель с кнопками
        top_panel = QHBoxLayout()
        
        self.btn_select_folder = QPushButton("Выбрать папку")
        self.btn_select_folder.clicked.connect(self.select_folder)
        top_panel.addWidget(self.btn_select_folder)
        
        self.btn_scan = QPushButton("Сканировать")
        self.btn_scan.clicked.connect(self.start_scan)
        self.btn_scan.setEnabled(False)
        top_panel.addWidget(self.btn_scan)
        
        self.btn_find_duplicates = QPushButton("Найти дубли")
        self.btn_find_duplicates.clicked.connect(self.start_find_duplicates)
        self.btn_find_duplicates.setEnabled(False)
        top_panel.addWidget(self.btn_find_duplicates)
        
        self.btn_clear_db = QPushButton("Очистить БД")
        self.btn_clear_db.clicked.connect(self.clear_database)
        top_panel.addWidget(self.btn_clear_db)
        
        top_panel.addStretch()
        
        self.lbl_status = QLabel("Готов к работе")
        top_panel.addWidget(self.lbl_status)
        
        main_layout.addLayout(top_panel)
        
        # Прогресс бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        main_layout.addWidget(self.progress_bar)
        
        # Разделитель для таблицы и превью
        splitter = QSplitter(Qt.Horizontal)
        
        # Таблица результатов
        self.table_widget = QTableWidget()
        self.table_widget.setColumnCount(4)
        self.table_widget.setHorizontalHeaderLabels(["Хэш", "Путь к файлу", "Размер (КБ)", "Статус"])
        self.table_widget.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table_widget.itemSelectionChanged.connect(self.update_preview)
        splitter.addWidget(self.table_widget)
        
        # Панель превью
        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        
        self.preview_label = QLabel("Превью изображения")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(300, 300)
        self.preview_label.setStyleSheet("QLabel { border: 1px solid gray; }")
        preview_layout.addWidget(self.preview_label)
        
        self.btn_delete_selected = QPushButton("Удалить выбранные")
        self.btn_delete_selected.clicked.connect(self.delete_selected)
        preview_layout.addWidget(self.btn_delete_selected)
        
        splitter.addWidget(preview_panel)
        splitter.setSizes([800, 400])
        
        main_layout.addWidget(splitter)
        
        # Лог событий
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setPlaceholderText("Лог событий...")
        main_layout.addWidget(self.log_text)
        
        self.log_message("Приложение запущено. Выберите папку для сканирования.")
    
    def log_message(self, message):
        """Добавление сообщения в лог."""
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
    
    def select_folder(self):
        """Выбор папки для сканирования."""
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку")
        if folder:
            self.folder_path = folder
            self.lbl_status.setText(f"Папка: {folder}")
            self.btn_scan.setEnabled(True)
            self.log_message(f"Выбрана папка: {folder}")
            
            # Инициализация БД
            if self.db_manager:
                self.db_manager.close()
            db_path = os.path.join(folder, "image_hashes.db")
            self.db_manager = DatabaseManager(db_path)
    
    def start_scan(self):
        """Запуск сканирования."""
        if not hasattr(self, 'folder_path'):
            return
        
        self.btn_scan.setEnabled(False)
        self.btn_find_duplicates.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.table_widget.setRowCount(0)
        self.current_duplicates = []
        
        self.scan_worker = ScanWorker(self.folder_path, self.db_manager)
        self.scan_worker.progress_signal.connect(self.update_progress)
        self.scan_worker.found_signal.connect(self.add_file_to_table)
        self.scan_worker.finished_signal.connect(self.on_scan_finished)
        self.scan_worker.error_signal.connect(self.on_error)
        self.scan_worker.start()
        
        self.log_message("Начато сканирование...")
    
    def update_progress(self, value, current_file):
        """Обновление прогресс бара."""
        self.progress_bar.setValue(value)
        self.lbl_status.setText(f"Обработка: {os.path.basename(current_file)}")
    
    def add_file_to_table(self, file_path, file_hash, file_size):
        """Добавление файла в таблицу."""
        row = self.table_widget.rowCount()
        self.table_widget.insertRow(row)
        
        self.table_widget.setItem(row, 0, QTableWidgetItem(file_hash[:12] + "..."))
        self.table_widget.setItem(row, 1, QTableWidgetItem(file_path))
        self.table_widget.setItem(row, 2, QTableWidgetItem(f"{file_size / 1024:.1f}"))
        self.table_widget.setItem(row, 3, QTableWidgetItem("Отсканировано"))
    
    def on_scan_finished(self, files):
        """Завершение сканирования."""
        self.btn_scan.setEnabled(True)
        self.btn_find_duplicates.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(f"Сканирование завершено. Найдено {len(files)} изображений.")
        self.log_message(f"Сканирование завершено. Найдено {len(files)} изображений.")
    
    def start_find_duplicates(self):
        """Запуск поиска дублей."""
        if not self.db_manager:
            return
        
        self.btn_find_duplicates.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        
        self.finder_worker = DuplicateFinderWorker(self.db_manager)
        self.finder_worker.progress_signal.connect(self.update_progress)
        self.finder_worker.duplicate_found_signal.connect(self.on_duplicates_found)
        self.finder_worker.finished_signal.connect(self.on_find_finished)
        self.finder_worker.error_signal.connect(self.on_error)
        self.finder_worker.start()
        
        self.log_message("Поиск дублей...")
    
    def on_duplicates_found(self, duplicates):
        """Обработка найденных дублей."""
        self.current_duplicates = duplicates
        self.table_widget.setRowCount(0)
        
        total_duplicates = sum(len(group) - 1 for group in duplicates)
        
        for group_idx, group in enumerate(duplicates):
            for file_idx, file_info in enumerate(group):
                row = self.table_widget.rowCount()
                self.table_widget.insertRow(row)
                
                status = "Дубль" if file_idx > 0 else "Оригинал"
                self.table_widget.setItem(row, 0, QTableWidgetItem(file_info['hash'][:12] + "..."))
                self.table_widget.setItem(row, 1, QTableWidgetItem(file_info['path']))
                self.table_widget.setItem(row, 2, QTableWidgetItem(f"{file_info['size'] / 1024:.1f}"))
                self.table_widget.setItem(row, 3, QTableWidgetItem(status))
                
                # Подсветка дублей
                if file_idx > 0:
                    for col in range(4):
                        item = self.table_widget.item(row, col)
                        if item:
                            item.setBackground(Qt.lightGray)
        
        self.log_message(f"Найдено {len(duplicates)} групп дублей ({total_duplicates} лишних файлов)")
    
    def on_find_finished(self):
        """Завершение поиска дублей."""
        self.btn_find_duplicates.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText("Поиск дублей завершен")
    
    def on_error(self, error_msg):
        """Обработка ошибок."""
        self.log_message(f"ОШИБКА: {error_msg}")
        QMessageBox.warning(self, "Ошибка", error_msg)
        self.btn_scan.setEnabled(True)
        self.btn_find_duplicates.setEnabled(True)
        self.progress_bar.setVisible(False)
    
    def clear_database(self):
        """Очистка базы данных."""
        reply = QMessageBox.question(
            self, "Подтверждение",
            "Вы уверены, что хотите очистить базу данных?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes and self.db_manager:
            self.db_manager.clear_database()
            self.table_widget.setRowCount(0)
            self.current_duplicates = []
            self.log_message("База данных очищена")
    
    def delete_selected(self):
        """Удаление выбранных файлов."""
        selected_rows = self.table_widget.selectedItems()
        if not selected_rows:
            QMessageBox.information(self, "Информация", "Выберите файлы для удаления")
            return
        
        rows = set(item.row() for item in selected_rows)
        
        reply = QMessageBox.question(
            self, "Подтверждение",
            f"Вы уверены, что хотите удалить {len(rows)} файлов?",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            deleted_count = 0
            for row in sorted(rows, reverse=True):
                file_path_item = self.table_widget.item(row, 1)
                if file_path_item:
                    file_path = file_path_item.text()
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            deleted_count += 1
                            self.table_widget.removeRow(row)
                    except Exception as e:
                        self.log_message(f"Не удалось удалить {file_path}: {e}")
            
            self.log_message(f"Удалено {deleted_count} файлов")
    
    def update_preview(self):
        """Обновление превью изображения при выборе строки."""
        selected_rows = self.table_widget.selectedItems()
        if not selected_rows:
            self.preview_label.setText("Превью изображения")
            self.preview_label.setPixmap(QPixmap())
            return
        
        # Получаем путь к файлу из первой выбранной строки
        row = selected_rows[0].row()
        file_path_item = self.table_widget.item(row, 1)
        if not file_path_item:
            return
        
        file_path = file_path_item.text()
        
        try:
            from PIL import Image
            # Открываем изображение с помощью Pillow
            with Image.open(file_path) as img:
                # Конвертируем в формат, подходящий для QPixmap
                img = img.convert("RGB")
                # Масштабируем для превью
                preview_size = (300, 300)
                img.thumbnail(preview_size, Image.Resampling.LANCZOS)
                
                # Создаем QPixmap из данных изображения
                from io import BytesIO
                buffer = BytesIO()
                img.save(buffer, format="JPEG")
                pixmap = QPixmap()
                pixmap.loadFromData(buffer.getvalue())
                
                self.preview_label.setPixmap(pixmap)
                self.preview_label.setText("")
        except Exception as e:
            self.preview_label.setText(f"Ошибка загрузки превью:\n{str(e)}")
            self.preview_label.setPixmap(QPixmap())
    
    def closeEvent(self, event):
        """Закрытие приложения."""
        if self.db_manager:
            self.db_manager.close()
        event.accept()


def main():
    """Точка входа в приложение."""
    app = QApplication(sys.argv)
    
    # Настройка стиля
    app.setStyle("Fusion")
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
