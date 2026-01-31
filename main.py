import sys
import os
import sqlite3
from pathlib import Path
import time
import wave
import struct
import traceback
from typing import List, Tuple, Optional, Dict, Any, Union
from dataclasses import dataclass
from datetime import timedelta
import tempfile
import random

import tinytag
from rapidfuzz import fuzz, process

# Untuk audio dari video - PERBAIKAN IMPORT MOVIEPY
MOVIEPY_AVAILABLE = False
mp = None

try:
    # Coba import moviepy
    import moviepy.editor as mp_import
    mp = mp_import
    MOVIEPY_AVAILABLE = True
    print("✓ MoviePy successfully imported")
except ImportError as e:
    print(f"✗ MoviePy import error: {e}")
    print("To install MoviePy, run: pip install moviepy")
    print("Or install with: pip install moviepy imageio[ffmpeg]")
except Exception as e:
    print(f"✗ Unexpected error importing MoviePy: {e}")
    MOVIEPY_AVAILABLE = False

# Coba import pydub sebagai alternatif
try:
    from pydub import AudioSegment
    from pydub.playback import play
    PYDUB_AVAILABLE = True
except:
    PYDUB_AVAILABLE = False

# Untuk histogram audio
import numpy as np

# IMPORTANT: Set High DPI attributes BEFORE importing PyQt5
import ctypes
if hasattr(sys, 'getwindowsversion'):
    # For Windows
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("audio.everything.app")
    
# Set environment untuk audio backend - DIPERBAIKI
if sys.platform == "win32":
    # Coba berbagai backend untuk menghindari error video
    os.environ["QT_MULTIMEDIA_PREFERRED_PLUGINS"] = "directshow,windowsmediafoundation"
    # Nonaktifkan video output untuk menghindari error
    os.environ["QT_MULTIMEDIA_NO_VIDEO"] = "1"
    # Disable hardware acceleration untuk video
    os.environ["QT_MULTIMEDIA_VIDEO_DISABLE_HW_ACCELERATION"] = "1"
    # Suppress Qt multimedia warnings
    os.environ["QT_LOGGING_RULES"] = "qt.multimedia.*=false"
    
# Now import PyQt5
from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtMultimedia import *

import qdarkstyle


# ============================================================================
# DATABASE MODEL - DIPERBAIKI
# ============================================================================

@dataclass
class MediaFile:
    """Data class untuk menyimpan informasi file media"""
    path: str
    filename: str
    extension: str
    is_video: bool
    duration: float
    size: int
    last_modified: float
    title: str = ""
    artist: str = ""
    album: str = ""
    genre: str = ""
    bitrate: int = 0
    sample_rate: int = 0
    channels: int = 0


class AudioDatabase:
    """Database untuk menyimpan index file audio/video"""
    
    def __init__(self, db_path: str = "media_index.db"):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """Initialize database tables dengan error handling"""
        try:
            # First, backup old database if exists
            if os.path.exists(self.db_path):
                backup_path = self.db_path + ".backup"
                try:
                    import shutil
                    shutil.copy2(self.db_path, backup_path)
                    print(f"Backed up old database to: {backup_path}")
                except:
                    pass
            
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Drop and recreate table untuk memastikan schema benar
                cursor.execute('DROP TABLE IF EXISTS media_files')
                
                # Create table dengan schema yang lengkap
                cursor.execute('''
                    CREATE TABLE media_files (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        path TEXT UNIQUE NOT NULL,
                        filename TEXT NOT NULL,
                        extension TEXT NOT NULL,
                        is_video INTEGER NOT NULL,
                        duration REAL NOT NULL,
                        size INTEGER NOT NULL,
                        last_modified REAL NOT NULL,
                        title TEXT,
                        artist TEXT,
                        album TEXT,
                        genre TEXT,
                        bitrate INTEGER,
                        sample_rate INTEGER,
                        channels INTEGER,
                        indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                
                # Create indexes
                cursor.execute('CREATE INDEX idx_filename ON media_files(filename)')
                cursor.execute('CREATE INDEX idx_extension ON media_files(extension)')
                cursor.execute('CREATE INDEX idx_is_video ON media_files(is_video)')
                cursor.execute('CREATE INDEX idx_title ON media_files(title)')
                cursor.execute('CREATE INDEX idx_artist ON media_files(artist)')
                
                conn.commit()
                print("Database initialized successfully with correct schema")
                
        except Exception as e:
            print(f"Error initializing database: {e}")
            # Jika error, hapus database dan buat ulang
            try:
                if os.path.exists(self.db_path):
                    os.remove(self.db_path)
                self._init_database()
            except Exception as e2:
                print(f"Failed to recreate database: {e2}")
    
    def add_media_file(self, media_file: MediaFile):
        """Add atau update media file di database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                cursor.execute('''
                    INSERT OR REPLACE INTO media_files 
                    (path, filename, extension, is_video, duration, size, last_modified,
                     title, artist, album, genre, bitrate, sample_rate, channels)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    media_file.path,
                    media_file.filename,
                    media_file.extension,
                    1 if media_file.is_video else 0,
                    media_file.duration,
                    media_file.size,
                    media_file.last_modified,
                    media_file.title,
                    media_file.artist,
                    media_file.album,
                    media_file.genre,
                    media_file.bitrate,
                    media_file.sample_rate,
                    media_file.channels
                ))
                
                conn.commit()
                return True
        except Exception as e:
            print(f"Error adding media file to database: {e}")
            return False
    
    def get_all_files(self) -> List[MediaFile]:
        """Get semua files dari database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM media_files ORDER BY filename')
                rows = cursor.fetchall()
                
                return [self._row_to_media_file(row) for row in rows]
        except Exception as e:
            print(f"Error getting all files from database: {e}")
            return []
    
    def search_files(self, query: str, limit: int = 100) -> List[MediaFile]:
        """Search files dengan fuzzy matching"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if query:
                    # Search di multiple fields
                    search_term = f'%{query}%'
                    cursor.execute('''
                        SELECT * FROM media_files 
                        WHERE LOWER(filename) LIKE LOWER(?)
                           OR LOWER(title) LIKE LOWER(?)
                           OR LOWER(artist) LIKE LOWER(?)
                           OR LOWER(album) LIKE LOWER(?)
                        ORDER BY filename
                        LIMIT ?
                    ''', (search_term, search_term, search_term, search_term, limit))
                else:
                    cursor.execute('SELECT * FROM media_files ORDER BY filename LIMIT ?', (limit,))
                
                rows = cursor.fetchall()
                files = [self._row_to_media_file(row) for row in rows]
                
                # Fuzzy matching dengan RapidFuzz jika ada query
                if query and files:
                    try:
                        choices = [f"{f.filename} {f.title} {f.artist} {f.album}" for f in files]
                        results = process.extract(query, choices, limit=limit, scorer=fuzz.partial_ratio)
                        
                        # Sort berdasarkan similarity score
                        scored_files = []
                        for file, score in zip(files, [r[1] for r in results]):
                            if score > 30:  # Lower threshold
                                scored_files.append((file, score))
                        
                        scored_files.sort(key=lambda x: x[1], reverse=True)
                        files = [f for f, _ in scored_files]
                    except Exception as e:
                        print(f"Fuzzy search error (non-critical): {e}")
                        # Tetap gunakan hasil SQL jika fuzzy search gagal
                
                return files
        except Exception as e:
            print(f"Error searching files: {e}")
            return []
    
    def delete_file(self, file_path: str):
        """Delete file dari database"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM media_files WHERE path = ?', (file_path,))
                conn.commit()
                return True
        except Exception as e:
            print(f"Error deleting file from database: {e}")
            return False
    
    def clear_all(self):
        """Clear semua data dari database"""
        try:
            # Step 1: Delete all records
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('DELETE FROM media_files')
                conn.commit()
            
            # Step 2: VACUUM di connection terpisah
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('VACUUM')
                conn.commit()
            
            print("Database cleared and vacuumed successfully")
            return True
        except Exception as e:
            print(f"Error clearing database: {e}")
            # Fallback: recreate database
            try:
                if os.path.exists(self.db_path):
                    os.remove(self.db_path)
                self._init_database()
                return True
            except Exception as e2:
                print(f"Failed to recreate database: {e2}")
                return False
    
    def get_file_count(self) -> int:
        """Get total file count"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM media_files')
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            print(f"Error getting file count: {e}")
            return 0
    
    def _row_to_media_file(self, row) -> MediaFile:
        """Convert database row ke MediaFile object"""
        try:
            # Safe extraction dari row dengan default values
            def get_value(key, default):
                if key in row.keys():
                    val = row[key]
                    return val if val is not None else default
                return default
            
            return MediaFile(
                path=get_value('path', ''),
                filename=get_value('filename', ''),
                extension=get_value('extension', ''),
                is_video=bool(get_value('is_video', 0)),
                duration=float(get_value('duration', 0.0)),
                size=int(get_value('size', 0)),
                last_modified=float(get_value('last_modified', 0.0)),
                title=str(get_value('title', '')),
                artist=str(get_value('artist', '')),
                album=str(get_value('album', '')),
                genre=str(get_value('genre', '')),
                bitrate=int(get_value('bitrate', 0)),
                sample_rate=int(get_value('sample_rate', 0)),
                channels=int(get_value('channels', 0))
            )
        except Exception as e:
            print(f"Error converting row to MediaFile: {e}")
            # Return minimal valid MediaFile
            return MediaFile(
                path='',
                filename='',
                extension='',
                is_video=False,
                duration=0.0,
                size=0,
                last_modified=0.0,
                title='',
                artist='',
                album='',
                genre='',
                bitrate=0,
                sample_rate=0,
                channels=0
            )


# ============================================================================
# TABLE MODEL
# ============================================================================

class MediaTableModel(QAbstractTableModel):
    """Model untuk tabel media files"""
    
    COLUMNS = [
        ("Filename", 300),
        ("Duration", 100),
        ("Type", 80),
        ("Size", 100),
        ("Artist", 150),
        ("Album", 150),
        ("Genre", 100),
        ("Path", 400)
    ]
    
    def __init__(self):
        super().__init__()
        self.media_files = []
    
    def set_files(self, files: List[MediaFile]):
        """Set files ke model"""
        self.beginResetModel()
        self.media_files = files
        self.endResetModel()
    
    def rowCount(self, parent=None):
        return len(self.media_files)
    
    def columnCount(self, parent=None):
        return len(self.COLUMNS)
    
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        
        media_file = self.media_files[index.row()]
        col = index.column()
        
        if role == Qt.DisplayRole:
            if col == 0:  # Filename
                return media_file.filename
            elif col == 1:  # Duration
                if media_file.duration > 0:
                    return str(timedelta(seconds=int(media_file.duration)))[2:]
                return "N/A"
            elif col == 2:  # Type
                return "Video" if media_file.is_video else "Audio"
            elif col == 3:  # Size
                size_bytes = media_file.size
                if size_bytes < 1024:
                    return f"{size_bytes} B"
                elif size_bytes < 1024 * 1024:
                    return f"{size_bytes/1024:.1f} KB"
                elif size_bytes < 1024 * 1024 * 1024:
                    return f"{size_bytes/(1024*1024):.1f} MB"
                else:
                    return f"{size_bytes/(1024*1024*1024):.2f} GB"
            elif col == 4:  # Artist
                return media_file.artist if media_file.artist else "Unknown"
            elif col == 5:  # Album
                return media_file.album if media_file.album else "Unknown"
            elif col == 6:  # Genre
                return media_file.genre if media_file.genre else "Unknown"
            elif col == 7:  # Path
                return media_file.path
        
        elif role == Qt.UserRole:
            # Return file path untuk drag & drop
            return media_file.path
        
        elif role == Qt.TextAlignmentRole:
            if col in [1, 3]:  # Duration dan Size
                return Qt.AlignRight | Qt.AlignVCenter
            return Qt.AlignLeft | Qt.AlignVCenter
        
        elif role == Qt.ForegroundRole:
            if media_file.is_video:
                return QColor(100, 180, 255)  # Blue untuk video
            else:
                return QColor(100, 255, 150)  # Green untuk audio
        
        elif role == Qt.ToolTipRole:
            return f"Path: {media_file.path}\nDuration: {media_file.duration:.1f}s\nSize: {media_file.size:,} bytes"
        
        elif role == Qt.DecorationRole and col == 0:
            # Icon untuk file type
            try:
                app = QApplication.instance()
                if app:
                    if media_file.is_video:
                        return app.style().standardIcon(QStyle.SP_MediaPlay)
                    else:
                        return app.style().standardIcon(QStyle.SP_MediaVolume)
            except:
                pass
            return None
        
        return None
    
    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.COLUMNS[section][0]
        elif role == Qt.FontRole:
            font = QFont()
            font.setBold(True)
            return font
        return None
    
    def get_file_at(self, row: int) -> Optional[MediaFile]:
        """Get MediaFile pada row tertentu"""
        if 0 <= row < len(self.media_files):
            return self.media_files[row]
        return None
    
    def sort(self, column, order=Qt.AscendingOrder):
        """Sort table berdasarkan column"""
        self.layoutAboutToBeChanged.emit()
        
        if column == 0:  # Filename
            self.media_files.sort(key=lambda x: x.filename.lower(), reverse=(order == Qt.DescendingOrder))
        elif column == 1:  # Duration
            self.media_files.sort(key=lambda x: x.duration, reverse=(order == Qt.DescendingOrder))
        elif column == 2:  # Type
            self.media_files.sort(key=lambda x: x.is_video, reverse=(order == Qt.DescendingOrder))
        elif column == 3:  # Size
            self.media_files.sort(key=lambda x: x.size, reverse=(order == Qt.DescendingOrder))
        elif column == 4:  # Artist
            self.media_files.sort(key=lambda x: x.artist.lower(), reverse=(order == Qt.DescendingOrder))
        elif column == 5:  # Album
            self.media_files.sort(key=lambda x: x.album.lower(), reverse=(order == Qt.DescendingOrder))
        
        self.layoutChanged.emit()


# ============================================================================
# SCANNER WORKER - DIPERBAIKI
# ============================================================================

class ScannerWorker(QObject):
    """Worker untuk scanning files di background thread"""
    
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    
    def __init__(self, paths: List[str], database: AudioDatabase):
        super().__init__()
        self.paths = paths
        self.database = database
        self._is_running = True
        self.scanned_count = 0
        self.total_files = 0
    
    def scan(self):
        """Scan semua files di paths yang diberikan"""
        try:
            all_files = []
            
            # Count total files first untuk progress bar
            self.total_files = self._count_total_files()
            
            for i, path in enumerate(self.paths):
                if not self._is_running:
                    break
                
                if os.path.isfile(path):
                    media_file = self._scan_file(path)
                    if media_file:
                        all_files.append(media_file)
                        self.database.add_media_file(media_file)
                        self.scanned_count += 1
                        
                        # Update progress
                        if self.scanned_count % 5 == 0:
                            progress_percent = int((self.scanned_count / max(self.total_files, 1)) * 100)
                            self.progress.emit(progress_percent, self.total_files, 
                                             f"Scanned {self.scanned_count}/{self.total_files} files...")
                else:
                    self._scan_directory(path, all_files)
            
            self.finished.emit(all_files)
            
        except Exception as e:
            self.error.emit(str(e))
    
    def _count_total_files(self) -> int:
        """Count total files untuk progress estimation"""
        count = 0
        audio_exts = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
        video_exts = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v', '.webm'}
        supported_exts = audio_exts | video_exts
        
        for path in self.paths:
            if os.path.isfile(path):
                if Path(path).suffix.lower() in supported_exts:
                    count += 1
            else:
                try:
                    for root, dirs, files in os.walk(path):
                        for file in files:
                            if Path(file).suffix.lower() in supported_exts:
                                count += 1
                except:
                    pass
        
        return max(count, 1)  # Minimal 1 untuk menghindari division by zero
    
    def _scan_directory(self, directory: str, all_files: list):
        """Scan semua files di directory"""
        try:
            audio_exts = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
            video_exts = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v', '.webm'}
            supported_exts = audio_exts | video_exts
            
            for root, dirs, files in os.walk(directory):
                if not self._is_running:
                    break
                
                for file in files:
                    if not self._is_running:
                        break
                    
                    file_path = os.path.join(root, file)
                    ext = Path(file_path).suffix.lower()
                    
                    # Hanya proses file dengan extension yang didukung
                    if ext in supported_exts:
                        media_file = self._scan_file(file_path)
                        if media_file:
                            all_files.append(media_file)
                            self.database.add_media_file(media_file)
                            self.scanned_count += 1
                            
                            # Update progress setiap 5 files
                            if self.scanned_count % 5 == 0:
                                progress_percent = int((self.scanned_count / max(self.total_files, 1)) * 100)
                                self.progress.emit(progress_percent, self.total_files, 
                                                 f"Scanned {self.scanned_count}/{self.total_files} files...")
        
        except Exception as e:
            print(f"Error scanning directory {directory}: {e}")
    
    def _scan_file(self, file_path: str) -> Optional[MediaFile]:
        """Scan single file dan extract metadata"""
        try:
            # Check extension
            ext = Path(file_path).suffix.lower()
            audio_exts = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
            video_exts = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v', '.webm'}
            
            if ext not in audio_exts | video_exts:
                return None
            
            is_video = ext in video_exts
            
            # Get file stats
            try:
                stat = os.stat(file_path)
                file_size = stat.st_size
                last_modified = stat.st_mtime
            except:
                file_size = 0
                last_modified = 0
            
            # Get metadata dengan TinyTag
            try:
                tag = tinytag.TinyTag.get(file_path)
                duration = tag.duration or 0.0
                title = tag.title or Path(file_path).stem
                artist = tag.artist or ""
                album = tag.album or ""
                genre = tag.genre or ""
                bitrate = tag.bitrate or 0
                sample_rate = tag.samplerate or 0
                channels = getattr(tag, 'channels', 0)
            except Exception as tag_error:
                # print(f"Debug: Error reading tags for {file_path}: {tag_error}")
                duration = 0.0
                title = Path(file_path).stem
                artist = ""
                album = ""
                genre = ""
                bitrate = 0
                sample_rate = 0
                channels = 0
            
            return MediaFile(
                path=file_path,
                filename=Path(file_path).name,
                extension=ext[1:],  # Remove dot
                is_video=is_video,
                duration=float(duration),
                size=file_size,
                last_modified=last_modified,
                title=title,
                artist=artist,
                album=album,
                genre=genre,
                bitrate=bitrate,
                sample_rate=sample_rate,
                channels=channels
            )
            
        except Exception as e:
            # print(f"Debug: Error scanning file {file_path}: {e}")
            return None
    
    def stop(self):
        """Stop scanning"""
        self._is_running = False


# ============================================================================
# AUDIO PLAYER DENGAN FIX UNTUK VIDEO FILES (NO VIDEO OUTPUT)
# ============================================================================

class EnhancedAudioPlayer:
    """Audio player dengan support untuk video files - HANYA AUDIO"""
    
    def __init__(self):
        self.current_file = None
        self.current_audio_file = None
        self.is_playing = False
        self.duration = 0
        self.position = 0
        self.autoplay = False  # Autoplay setelah load
        self.repeat = False    # Repeat track setelah selesai
        self.media_ended = False  # Flag untuk track selesai
        
        # Timer untuk update UI
        self.timer = QTimer()
        self.timer.setInterval(100)  # Update setiap 100ms
        self.timer.timeout.connect(self._update_position)
        
        # Qt Multimedia player dengan video output disabled
        self.qt_player = QMediaPlayer()
        
        # FIX: Coba berbagai cara untuk disable video output
        self._disable_video_output()
        
        # Connect signals
        self.qt_player.positionChanged.connect(self._on_qt_position_changed)
        self.qt_player.durationChanged.connect(self._on_qt_duration_changed)
        self.qt_player.stateChanged.connect(self._on_qt_state_changed)
        self.qt_player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.qt_player.error.connect(self._on_player_error)
        
        # Connect media finished signal untuk autoplay/repeat
        self.qt_player.mediaStatusChanged.connect(self._on_media_finished)
        
        # Temporary files tracker
        self.temp_files = []
        
        # Suppress warnings
        import warnings
        warnings.filterwarnings("ignore", category=RuntimeWarning)
    
    def _disable_video_output(self):
        """Multiple methods to disable video output"""
        try:
            # Method 1: Set video output to None
            self.qt_player.setVideoOutput(None)
            
            # Method 2: Create dummy video output dan hide
            try:
                dummy_output = QVideoWidget()
                dummy_output.hide()
                self.qt_player.setVideoOutput(dummy_output)
            except:
                pass
                
        except Exception as e:
            # Ignore video output errors
            pass
    
    def load_file(self, file_path: str, autoplay: bool = False, repeat: bool = False) -> bool:
        """Load audio atau video file dengan autoplay dan repeat options"""
        try:
            self.current_file = file_path
            self.autoplay = autoplay
            self.repeat = repeat
            self.media_ended = False
            
            # Cek jika ini video file
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v'}
            is_video = Path(file_path).suffix.lower() in video_extensions
            
            file_to_load = file_path
            
            # Untuk video files, kita akan mencoba extract audio atau gunakan workaround
            if is_video:
                print(f"⚠ Video file detected: {Path(file_path).name}")
                
                # Coba extract audio jika MoviePy tersedia
                if MOVIEPY_AVAILABLE and mp is not None:
                    print(f"Extracting audio from video...")
                    extracted_audio = AudioAnalyzer.extract_audio_from_video(file_path)
                    if extracted_audio and os.path.exists(extracted_audio):
                        self.current_audio_file = extracted_audio
                        self.temp_files.append(extracted_audio)
                        file_to_load = extracted_audio
                        print(f"✓ Using extracted audio: {extracted_audio}")
                    else:
                        print(f"✗ Audio extraction failed, using original file")
                        file_to_load = file_path
                else:
                    print(f"MoviePy not available, using original file")
                    file_to_load = file_path
            else:
                self.current_audio_file = None
            
            # Get duration
            self.duration = AudioAnalyzer.get_audio_duration(file_path)
            
            print(f"Loading file for playback: {Path(file_to_load).name}, Duration: {self.duration:.1f}s")
            
            # FIX: Gunakan QUrl dengan proper encoding
            file_url = QUrl.fromLocalFile(file_to_load)
            media_content = QMediaContent(file_url)
            
            # Pastikan video output disabled
            self._disable_video_output()
            
            # Load media
            self.qt_player.setMedia(media_content)
            
            # Jika autoplay diaktifkan, play setelah load
            if self.autoplay:
                QTimer.singleShot(300, self.play)  # Delay lebih lama untuk pastikan media loaded
            
            return True
            
        except Exception as e:
            print(f"Error loading file: {e}")
            traceback.print_exc()
            return False
    
    def _on_media_status_changed(self, status):
        """Handle media status changes"""
        status_names = {
            QMediaPlayer.UnknownMediaStatus: "Unknown",
            QMediaPlayer.NoMedia: "No Media",
            QMediaPlayer.LoadingMedia: "Loading",
            QMediaPlayer.LoadedMedia: "Loaded",
            QMediaPlayer.StalledMedia: "Stalled",
            QMediaPlayer.BufferingMedia: "Buffering",
            QMediaPlayer.BufferedMedia: "Buffered",
            QMediaPlayer.EndOfMedia: "End",
            QMediaPlayer.InvalidMedia: "Invalid"
        }
        
        status_name = status_names.get(status, str(status))
        if status in [QMediaPlayer.LoadedMedia, QMediaPlayer.EndOfMedia, QMediaPlayer.InvalidMedia]:
            print(f"Media status: {status_name}")
    
    def _on_player_error(self, error):
        """Handle player errors"""
        error_msg = self.qt_player.errorString()
        # Ignore video-related errors
        if error_msg and "video" not in error_msg.lower() and "topology" not in error_msg.lower():
            print(f"Media player error: {error_msg}")
    
    def _on_qt_duration_changed(self, duration):
        """Handle duration changes"""
        if duration > 0:
            self.duration = duration / 1000.0  # Convert to seconds
            print(f"Duration updated: {self.duration:.1f}s")
    
    def _on_media_finished(self, status):
        """Handle when media finishes playing"""
        if status == QMediaPlayer.EndOfMedia:
            self.media_ended = True
            print("🎵 Track finished playing")
            
            # Jika repeat aktif, play ulang
            if self.repeat and self.current_file:
                print("🔁 Repeating track...")
                QTimer.singleShot(500, self.play)  # Delay sedikit sebelum repeat
            # Jika tidak repeat, stop
            elif not self.repeat:
                self.stop()
    
    def play(self):
        """Play audio"""
        if not self.current_file:
            print("No file loaded to play")
            return False
        
        try:
            # Coba play dengan error handling
            self.qt_player.play()
            self.is_playing = True
            self.timer.start()
            print(f"▶ Playing: {Path(self.current_file).name}")
            return True
        except Exception as e:
            print(f"Error playing: {e}")
            return False
    
    def pause(self):
        """Pause audio"""
        try:
            self.qt_player.pause()
            self.is_playing = False
            self.timer.stop()
            print("⏸ Audio paused")
        except Exception as e:
            print(f"Error pausing: {e}")
    
    def stop(self):
        """Stop audio"""
        try:
            self.qt_player.stop()
            self.is_playing = False
            self.position = 0
            self.timer.stop()
            print("⏹ Audio stopped")
        except Exception as e:
            print(f"Error stopping: {e}")
    
    def set_position(self, position: float):
        """Set playback position (seconds)"""
        if not self.duration or self.duration <= 0:
            return
        
        try:
            ms_position = int(position * 1000)
            self.qt_player.setPosition(ms_position)
        except Exception as e:
            print(f"Error setting position: {e}")
    
    def _on_qt_position_changed(self, position):
        """Update position from Qt player"""
        self.position = position / 1000.0  # Convert to seconds
    
    def _on_qt_state_changed(self, state):
        """Handle Qt player state changes"""
        if state == QMediaPlayer.StoppedState:
            self.is_playing = False
            self.position = 0
            self.timer.stop()
        elif state == QMediaPlayer.PlayingState:
            self.is_playing = True
        elif state == QMediaPlayer.PausedState:
            self.is_playing = False
    
    def _update_position(self):
        """Update position timer"""
        # Position sudah diupdate oleh signal Qt
        pass
    
    def cleanup(self):
        """Clean up temporary files"""
        for temp_file in self.temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
                    print(f"Cleaned up temp file: {temp_file}")
            except Exception as e:
                print(f"Error cleaning up temp file {temp_file}: {e}")
        self.temp_files.clear()


# ============================================================================
# DRAG TABLE VIEW DENGAN DRAG-OUT SUPPORT KE CAPCUT/EXPLORER
# ============================================================================

class DragTableView(QTableView):
    """TableView dengan support drag & drop ke aplikasi eksternal"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        
        # Custom styling
        self.verticalHeader().setVisible(False)
        self.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.setVerticalScrollMode(QTableView.ScrollPerPixel)
        
        # Timer untuk auto-scroll saat drag
        self.drag_scroll_timer = QTimer()
        self.drag_scroll_timer.timeout.connect(self._auto_scroll_drag)
        self.drag_scroll_margin = 50
        self.drag_scroll_speed = 20
    
    def startDrag(self, supportedActions):
        """Start drag dengan multiple file selection"""
        try:
            selected_rows = self.selectionModel().selectedRows(0)
            if not selected_rows:
                return
            
            # Collect file paths from selected rows
            model = self.model()
            file_paths = []
            
            for index in selected_rows:
                file_path = model.data(index.siblingAtColumn(0), Qt.UserRole)
                if file_path and os.path.exists(file_path):
                    file_paths.append(file_path)
            
            if not file_paths:
                return
            
            print(f"Preparing to drag {len(file_paths)} files to external application")
            
            # Create MIME data
            mime_data = QMimeData()
            
            # Format 1: File URLs (untuk Explorer, Capcut, Premiere)
            urls = [QUrl.fromLocalFile(path) for path in file_paths]
            mime_data.setUrls(urls)
            
            # Format 2: Text dengan paths
            mime_data.setText("\n".join(file_paths))
            
            # Create drag object
            drag = QDrag(self)
            drag.setMimeData(mime_data)
            
            # Set preview icon
            pixmap = self._create_drag_pixmap(len(file_paths))
            drag.setPixmap(pixmap)
            
            # Set hot spot
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
            
            print(f"Dragging {len(file_paths)} files: {[Path(p).name for p in file_paths[:3]]}...")
            
            # Start auto-scroll timer
            self.drag_scroll_timer.start(100)
            
            # Execute drag
            result = drag.exec_(Qt.CopyAction | Qt.MoveAction)
            
            # Stop auto-scroll timer
            self.drag_scroll_timer.stop()
            
            # Log result
            if result == Qt.CopyAction:
                print("✓ Drag completed: Copy action")
            elif result == Qt.MoveAction:
                print("✓ Drag completed: Move action")
            else:
                print("✗ Drag cancelled or failed")
            
        except Exception as e:
            print(f"Error in startDrag: {e}")
            traceback.print_exc()
            self.drag_scroll_timer.stop()
    
    def _create_drag_pixmap(self, file_count: int) -> QPixmap:
        """Create preview pixmap untuk drag operation"""
        pixmap = QPixmap(128, 128)
        pixmap.fill(Qt.transparent)
        
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        
        # Background circle
        painter.setBrush(QColor(100, 150, 220, 220))
        painter.setPen(QPen(QColor(70, 120, 200), 2))
        painter.drawEllipse(10, 10, 108, 108)
        
        # File count
        painter.setPen(Qt.white)
        painter.setFont(QFont("Arial", 24, QFont.Bold))
        
        count_text = str(file_count) if file_count <= 99 else "99+"
        painter.drawText(pixmap.rect(), Qt.AlignCenter, count_text)
        
        # File text
        if file_count == 1:
            file_text = "file"
        else:
            file_text = "files"
        
        painter.setFont(QFont("Arial", 10))
        painter.drawText(0, 90, 128, 30, Qt.AlignCenter, file_text)
        
        # Drag hint
        painter.setFont(QFont("Arial", 8))
        painter.setPen(QColor(200, 200, 200))
        painter.drawText(0, 110, 128, 20, Qt.AlignCenter, "Drag to export")
        
        painter.end()
        
        return pixmap
    
    def _auto_scroll_drag(self):
        """Auto-scroll saat drag di dekat edge"""
        if not self.underMouse():
            return
        
        pos = self.mapFromGlobal(QCursor.pos())
        viewport = self.viewport()
        rect = viewport.rect()
        
        # Scroll up
        if pos.y() < self.drag_scroll_margin:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - self.drag_scroll_speed
            )
        
        # Scroll down
        elif pos.y() > rect.height() - self.drag_scroll_margin:
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() + self.drag_scroll_speed
            )
    
    def dragEnterEvent(self, event):
        """Handle drag enter"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dragMoveEvent(self, event):
        """Handle drag move"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event):
        """Handle drop"""
        event.ignore()
    
    def mouseMoveEvent(self, event):
        """Handle mouse move untuk mulai drag"""
        if event.buttons() & Qt.LeftButton and hasattr(self, 'drag_start_pos'):
            # Start drag setelah mouse move tertentu
            drag_start_distance = 10  # pixels
            if (event.pos() - self.drag_start_pos).manhattanLength() > drag_start_distance:
                self.startDrag(Qt.CopyAction)
        super().mouseMoveEvent(event)
    
    def mousePressEvent(self, event):
        """Record mouse press position untuk drag"""
        if event.button() == Qt.LeftButton:
            self.drag_start_pos = event.pos()
        super().mousePressEvent(event)
    
    def contextMenuEvent(self, event):
        """Show context menu"""
        try:
            selected = self.selectionModel().selectedRows()
            if not selected:
                return
            
            menu = QMenu(self)
            
            # Play controls
            play_action = menu.addAction("▶ Play")
            stop_action = menu.addAction("⏹ Stop")
            
            menu.addSeparator()
            
            # Drag to external
            drag_action = menu.addAction("📤 Drag to Capcut/Explorer")
            
            # Quick export actions
            export_submenu = menu.addMenu("🚀 Quick Export To")
            export_desktop = export_submenu.addAction("Desktop")
            export_documents = export_submenu.addAction("Documents")
            export_custom = export_submenu.addAction("Custom Folder...")
            
            menu.addSeparator()
            
            # File operations
            open_action = menu.addAction("📂 Open File")
            open_location_action = menu.addAction("📁 Open File Location")
            
            menu.addSeparator()
            
            # Extract audio (for videos)
            if MOVIEPY_AVAILABLE:
                extract_action = menu.addAction("🎵 Extract Audio from Video")
            
            # Execute menu
            action = menu.exec_(self.mapToGlobal(event.pos()))
            
            if action == play_action:
                self.window().play_audio()
            elif action == stop_action:
                self.window().stop_audio()
            elif action == drag_action:
                self.startDrag(Qt.CopyAction)
            elif action == export_desktop:
                self._export_to_folder(Path.home() / "Desktop")
            elif action == export_documents:
                self._export_to_folder(Path.home() / "Documents")
            elif action == export_custom:
                self._export_to_custom_folder()
            elif action == open_action:
                self._open_selected_file()
            elif action == open_location_action:
                self._open_file_location()
            elif action == extract_action and MOVIEPY_AVAILABLE:
                self._extract_audio_from_selected()
                
        except Exception as e:
            print(f"Error in contextMenuEvent: {e}")
    
    def _export_to_folder(self, folder_path: Path):
        """Export selected files to specific folder"""
        try:
            selected = self.selectionModel().selectedRows()
            if not selected:
                return
            
            model = self.model()
            file_paths = []
            
            for index in selected:
                file_path = model.data(index.siblingAtColumn(0), Qt.UserRole)
                if file_path and os.path.exists(file_path):
                    file_paths.append(Path(file_path))
            
            if not file_paths:
                return
            
            # Create destination folder jika tidak ada
            folder_path.mkdir(parents=True, exist_ok=True)
            
            # Copy files
            success_count = 0
            for src_path in file_paths:
                try:
                    dst_path = folder_path / src_path.name
                    # Handle duplicate names
                    counter = 1
                    while dst_path.exists():
                        name_parts = src_path.stem.split('.')
                        if len(name_parts) > 1:
                            new_name = f"{name_parts[0]}_{counter}.{src_path.suffix[1:]}"
                        else:
                            new_name = f"{src_path.stem}_{counter}{src_path.suffix}"
                        dst_path = folder_path / new_name
                        counter += 1
                    
                    import shutil
                    shutil.copy2(src_path, dst_path)
                    success_count += 1
                except Exception as e:
                    print(f"Error copying {src_path}: {e}")
            
            # Show result
            QMessageBox.information(self.window(), "Export Complete",
                f"Successfully exported {success_count}/{len(file_paths)} files to:\n{folder_path}")
            
        except Exception as e:
            print(f"Error exporting to folder: {e}")
            QMessageBox.critical(self.window(), "Export Error", str(e))
    
    def _export_to_custom_folder(self):
        """Export to user-selected folder"""
        try:
            folder = QFileDialog.getExistingDirectory(
                self.window(), "Select Export Folder"
            )
            
            if folder:
                self._export_to_folder(Path(folder))
        except Exception as e:
            print(f"Error in custom folder export: {e}")
    
    def _extract_audio_from_selected(self):
        """Extract audio from selected video file"""
        try:
            selected = self.selectionModel().selectedRows()
            if not selected:
                return
            
            row = selected[0].row()
            model = self.model()
            if hasattr(model, 'get_file_at'):
                media_file = model.get_file_at(row)
                if media_file and media_file.is_video and MOVIEPY_AVAILABLE:
                    self.window().extract_audio_from_video(media_file.path)
        except Exception as e:
            print(f"Error extracting audio: {e}")
    
    def _open_selected_file(self):
        """Open selected file with default application"""
        try:
            selected = self.selectionModel().selectedRows()
            if not selected:
                return
            
            row = selected[0].row()
            model = self.model()
            if hasattr(model, 'get_file_at'):
                audio_file = model.get_file_at(row)
                if audio_file and os.path.exists(audio_file.path):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(audio_file.path))
        except Exception as e:
            print(f"Error opening file: {e}")
    
    def _open_file_location(self):
        """Open file location in file explorer"""
        try:
            selected = self.selectionModel().selectedRows()
            if not selected:
                return
            
            row = selected[0].row()
            model = self.model()
            if hasattr(model, 'get_file_at'):
                audio_file = model.get_file_at(row)
                if audio_file:
                    path = Path(audio_file.path)
                    if path.exists():
                        if sys.platform == "win32":
                            import subprocess
                            # Open folder and select file
                            subprocess.Popen(f'explorer /select,"{path}"')
                        else:
                            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        except Exception as e:
            print(f"Error opening file location: {e}")


# ============================================================================
# AUDIO ANALYZER DENGAN PERBAIKAN
# ============================================================================

class AudioAnalyzer:
    """Class untuk menganalisis audio dan membuat waveform"""
    
    @staticmethod
    def extract_audio_from_video(video_path: str) -> Optional[str]:
        """Extract audio dari video file ke temporary WAV file"""
        if not MOVIEPY_AVAILABLE or mp is None:
            print("✗ MoviePy not available for audio extraction")
            return None
            
        try:
            print(f"Extracting audio from: {video_path}")
            
            # Buat temporary file untuk audio
            temp_dir = tempfile.gettempdir()
            video_name = Path(video_path).stem
            safe_name = ''.join(c for c in video_name if c.isalnum() or c in '._- ')
            audio_filename = f"extracted_{safe_name}_{int(time.time())}.wav"
            audio_path = os.path.join(temp_dir, audio_filename)
            
            print(f"Output audio path: {audio_path}")
            
            # Extract audio menggunakan moviepy
            video = mp.VideoFileClip(video_path)
            if video.audio is not None:
                print("✓ Video has audio track, extracting...")
                video.audio.write_audiofile(audio_path, verbose=False, logger=None)
                video.close()
                print(f"✓ Audio extraction successful: {audio_path}")
                return audio_path
            else:
                print("✗ Video has no audio track")
                video.close()
                return None
        except Exception as e:
            print(f"✗ Error extracting audio from video: {e}")
            return None
    
    @staticmethod
    def get_audio_duration(file_path: str) -> float:
        """Get duration dari file audio/video"""
        try:
            tag = tinytag.TinyTag.get(file_path)
            duration = tag.duration if tag.duration is not None else 0.0
            return float(duration) if duration is not None else 0.0
        except Exception as e:
            print(f"Error getting duration for {file_path}: {e}")
            return 0.0
    
    @staticmethod
    def generate_waveform_data(file_path: str, num_points: int = 800) -> List[Tuple[float, float]]:
        """Generate waveform data dari file audio untuk visualisasi"""
        try:
            # Untuk video files
            video_extensions = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v'}
            is_video = Path(file_path).suffix.lower() in video_extensions
            
            if is_video and MOVIEPY_AVAILABLE and mp is not None:
                extracted_audio = AudioAnalyzer.extract_audio_from_video(file_path)
                if extracted_audio and os.path.exists(extracted_audio):
                    try:
                        data = AudioAnalyzer._read_wav_file(extracted_audio, num_points)
                        # Cleanup
                        try:
                            os.remove(extracted_audio)
                        except:
                            pass
                        return data
                    except:
                        pass
            
            if file_path.lower().endswith('.wav'):
                return AudioAnalyzer._read_wav_file(file_path, num_points)
            else:
                return AudioAnalyzer._generate_simplified_waveform(file_path, num_points)
                
        except Exception as e:
            print(f"Error generating waveform data: {e}")
            return AudioAnalyzer._generate_dummy_waveform(num_points)
    
    @staticmethod
    def _read_wav_file(file_path: str, num_points: int) -> List[Tuple[float, float]]:
        """Read WAV file dan generate waveform data"""
        try:
            with wave.open(file_path, 'rb') as wav_file:
                n_frames = wav_file.getnframes()
                sample_width = wav_file.getsampwidth()
                frames = wav_file.readframes(n_frames)
                
                if n_frames == 0:
                    return AudioAnalyzer._generate_dummy_waveform(num_points)
                
                if sample_width == 1:
                    fmt = f"{n_frames}B"
                    data = struct.unpack(fmt, frames)
                    data = [(x - 128) / 128.0 for x in data]
                elif sample_width == 2:
                    fmt = f"{n_frames}h"
                    data = struct.unpack(fmt, frames)
                    data = [x / 32768.0 for x in data]
                else:
                    return AudioAnalyzer._generate_dummy_waveform(num_points)
                
                # Downsample
                if len(data) > num_points * 10:
                    step = len(data) // num_points
                    data = data[::step]
                
                # Normalize
                if data:
                    max_abs = max(abs(min(data)), abs(max(data)))
                    if max_abs > 0:
                        data = [x / max_abs for x in data]
                    
                    # Convert to points
                    points = []
                    for i, val in enumerate(data[:num_points]):
                        x = i / len(data[:num_points])
                        points.append((x, val))
                    return points
                else:
                    return AudioAnalyzer._generate_dummy_waveform(num_points)
                    
        except Exception as e:
            print(f"Error reading WAV file: {e}")
            return AudioAnalyzer._generate_dummy_waveform(num_points)
    
    @staticmethod
    def _generate_simplified_waveform(file_path: str, num_points: int) -> List[Tuple[float, float]]:
        """Generate simplified waveform untuk non-WAV files"""
        try:
            duration = AudioAnalyzer.get_audio_duration(file_path)
            
            points = []
            for i in range(num_points):
                x = i / num_points
                t = x * 20
                y = (np.sin(t * np.pi * 2) * 0.5 + 
                     np.sin(t * np.pi * 4) * 0.3 + 
                     np.sin(t * np.pi * 8) * 0.2)
                y += np.random.uniform(-0.1, 0.1)
                points.append((x, y))
            return points
        except:
            return AudioAnalyzer._generate_dummy_waveform(num_points)
    
    @staticmethod
    def _generate_dummy_waveform(num_points: int) -> List[Tuple[float, float]]:
        """Generate dummy waveform data sebagai fallback"""
        points = []
        for i in range(num_points):
            x = i / num_points
            y = np.sin(x * np.pi * 8) * 0.7 + random.uniform(-0.1, 0.1)
            points.append((x, y))
        return points


# ============================================================================
# MAIN APPLICATION
# ============================================================================

class AudioEverythingApp(QMainWindow):
    """Aplikasi utama Audio Everything dengan semua fitur"""
    
    def __init__(self):
        super().__init__()
        
        # Initialize components
        self.database = AudioDatabase("media_index.db")
        self.scanner_worker = None
        self.scanner_thread = None
        self.search_timer = QTimer()
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self._perform_search)
        
        # Audio player
        self.audio_player = EnhancedAudioPlayer()
        self.audio_player.timer.timeout.connect(self._update_playback_ui)
        
        self.current_media_file = None
        self.playback_updating = False
        self.last_folder = str(Path.home())
        
        self._setup_ui()
        self._setup_connections()
        self._load_settings()
        
        # Load existing files
        self._load_existing_files()
        
        # Update file count
        self._update_file_count()
        
        print("✓ Application initialized successfully")
    
    def _setup_ui(self):
        """Setup user interface"""
        self.setWindowTitle("Audio Everything Pro - Media Search & Analysis")
        self.resize(1400, 900)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        
        # 1. Top bar
        top_bar = QWidget()
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # Buttons
        self.btn_select_folder = QPushButton("📁 Select Folder")
        self.btn_select_folder.setMinimumWidth(120)
        top_layout.addWidget(self.btn_select_folder)
        
        self.btn_select_files = QPushButton("📄 Select Files")
        self.btn_select_files.setMinimumWidth(120)
        top_layout.addWidget(self.btn_select_files)
        
        self.btn_rescan = QPushButton("↻ Rescan")
        top_layout.addWidget(self.btn_rescan)
        
        self.btn_clear_index = QPushButton("🗑️ Clear Index")
        top_layout.addWidget(self.btn_clear_index)
        
        self.btn_drag_help = QPushButton("📤 How to Drag")
        self.btn_drag_help.setMinimumWidth(120)
        top_layout.addWidget(self.btn_drag_help)
        
        top_layout.addStretch()
        
        # Status label
        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #888; font-style: italic;")
        top_layout.addWidget(self.lbl_status)
        
        # File count
        self.lbl_file_count = QLabel("0 files")
        self.lbl_file_count.setStyleSheet("color: #4CAF50; font-weight: bold;")
        top_layout.addWidget(self.lbl_file_count)
        
        main_layout.addWidget(top_bar)
        
        # 2. Drag instruction panel
        drag_info = QLabel(
            "💡 <b>Drag files to external apps:</b> Select files, then click and drag anywhere "
            "in the table to export to Capcut, Explorer, or other applications."
        )
        drag_info.setWordWrap(True)
        drag_info.setStyleSheet("""
            QLabel {
                background-color: rgba(100, 150, 220, 30);
                border: 1px solid rgba(100, 150, 220, 100);
                border-radius: 5px;
                padding: 8px;
                color: #ddd;
            }
        """)
        drag_info.setMaximumHeight(60)
        main_layout.addWidget(drag_info)
        
        # 3. Search bar
        search_container = QWidget()
        search_layout = QHBoxLayout(search_container)
        search_layout.setContentsMargins(0, 0, 0, 0)
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search audio/video files (fuzzy search enabled)...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self.search_input)
        
        self.btn_advanced_search = QPushButton("🔍 Advanced")
        self.btn_advanced_search.setMinimumWidth(100)
        search_layout.addWidget(self.btn_advanced_search)
        
        main_layout.addWidget(search_container)
        
        # 4. Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)
        
        # 5. Media table
        self.table_view = DragTableView()
        self.table_model = MediaTableModel()
        self.table_view.setModel(self.table_model)
        
        # Set column widths
        for i, (_, width) in enumerate(MediaTableModel.COLUMNS):
            self.table_view.setColumnWidth(i, width)
        
        # Selection
        self.table_view.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        
        main_layout.addWidget(self.table_view, 1)
        
        # 6. Waveform widget
        class SimpleWaveformWidget(QWidget):
            def __init__(self, parent=None):
                super().__init__(parent)
                self.waveform_data = []
                self.duration = 0
                self.current_position = 0
                self.setMinimumHeight(100)
                
                # Colors
                self.bg_color = QColor(25, 25, 30)
                self.waveform_color = QColor(100, 180, 255, 200)
                self.playhead_color = QColor(255, 50, 50, 220)
                self.text_color = QColor(200, 200, 200)
            
            def set_audio_data(self, waveform_data, duration):
                self.waveform_data = waveform_data
                self.duration = max(float(duration), 0.1)
                self.current_position = 0
                self.update()
            
            def set_position(self, position):
                self.current_position = max(0.0, min(float(position), self.duration))
                self.update()
            
            def paintEvent(self, event):
                painter = QPainter(self)
                painter.setRenderHint(QPainter.Antialiasing)
                painter.fillRect(self.rect(), self.bg_color)
                
                if not self.waveform_data or self.width() <= 0:
                    painter.setPen(self.text_color)
                    painter.drawText(self.rect(), Qt.AlignCenter, "No audio data")
                    return
                
                height = self.height()
                width = self.width()
                center_y = height // 2
                half_height = height // 2 * 0.8
                
                # Draw waveform
                painter.setPen(QPen(self.waveform_color, 1.5))
                path = QPainterPath()
                
                first = True
                for x_ratio, amplitude in self.waveform_data:
                    x = x_ratio * width
                    y = center_y + (amplitude * half_height)
                    
                    if first:
                        path.moveTo(x, y)
                        first = False
                    else:
                        path.lineTo(x, y)
                
                painter.drawPath(path)
                
                # Draw playhead
                if self.duration > 0:
                    pos_x = int((self.current_position / self.duration) * width)
                    painter.setPen(QPen(self.playhead_color, 2))
                    painter.drawLine(pos_x, 0, pos_x, height)
                
                # Draw time
                painter.setPen(self.text_color)
                current_time = str(timedelta(seconds=int(self.current_position)))[2:7]
                total_time = str(timedelta(seconds=int(self.duration)))[2:7]
                painter.drawText(10, 20, f"{current_time} / {total_time}")
        
        self.waveform_widget = SimpleWaveformWidget()
        main_layout.addWidget(self.waveform_widget)
        
        # 7. Playback controls
        playback_bar = QWidget()
        playback_layout = QHBoxLayout(playback_bar)
        playback_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_play = QPushButton("▶ Play")
        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_stop = QPushButton("⏹ Stop")
        
        # Add autoplay and repeat checkboxes
        self.chk_autoplay = QCheckBox("Auto Play")
        self.chk_autoplay.setChecked(False)
        self.chk_autoplay.setToolTip("Automatically play when file is selected")
        self.chk_autoplay.stateChanged.connect(self._on_autoplay_changed)
        
        self.chk_repeat = QCheckBox("Repeat")
        self.chk_repeat.setChecked(False)
        self.chk_repeat.setToolTip("Repeat the current track")
        self.chk_repeat.stateChanged.connect(self._on_repeat_changed)
        
        if MOVIEPY_AVAILABLE:
            self.btn_extract_audio = QPushButton("🎵 Extract Audio")
            self.btn_extract_audio.setToolTip("Extract audio from video files")
            playback_layout.addWidget(self.btn_extract_audio)
        
        self.playback_slider = QSlider(Qt.Horizontal)
        self.playback_slider.setRange(0, 1000)
        self.playback_slider.sliderMoved.connect(self._on_slider_moved)
        self.playback_slider.sliderPressed.connect(self._on_slider_pressed)
        self.playback_slider.sliderReleased.connect(self._on_slider_released)
        
        self.lbl_playback_time = QLabel("0:00 / 0:00")
        self.lbl_playback_time.setMinimumWidth(100)
        
        playback_layout.addWidget(self.btn_play)
        playback_layout.addWidget(self.btn_pause)
        playback_layout.addWidget(self.btn_stop)
        playback_layout.addWidget(self.chk_autoplay)
        playback_layout.addWidget(self.chk_repeat)
        playback_layout.addWidget(self.playback_slider)
        playback_layout.addWidget(self.lbl_playback_time)
        
        main_layout.addWidget(playback_bar)
        
        # Status bar
        self.statusBar().showMessage("Ready - Select files and drag to export")
        
        # Setup shortcuts
        self._setup_shortcuts()
    
    def _setup_shortcuts(self):
        """Setup keyboard shortcuts"""
        QShortcut(QKeySequence("Space"), self).activated.connect(self._toggle_play_pause)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(self.search_input.setFocus)
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._select_all_files)
        QShortcut(QKeySequence("Esc"), self).activated.connect(self.stop_audio)
    
    def _setup_connections(self):
        """Setup signal-slot connections"""
        self.btn_select_folder.clicked.connect(self._browse_folder)
        self.btn_select_files.clicked.connect(self._select_files)
        self.btn_rescan.clicked.connect(self._rescan_current)
        self.btn_clear_index.clicked.connect(self._clear_index)
        self.btn_advanced_search.clicked.connect(self._show_advanced_search)
        self.btn_drag_help.clicked.connect(self._show_drag_help)
        
        # Playback controls
        self.btn_play.clicked.connect(self.play_audio)
        self.btn_pause.clicked.connect(self.pause_audio)
        self.btn_stop.clicked.connect(self.stop_audio)
        
        if MOVIEPY_AVAILABLE and hasattr(self, 'btn_extract_audio'):
            self.btn_extract_audio.clicked.connect(self._extract_current_audio)
    
    def _on_autoplay_changed(self, state):
        """Handle autoplay checkbox change"""
        autoplay = state == Qt.Checked
        self.audio_player.autoplay = autoplay
        print(f"Autoplay: {'ON' if autoplay else 'OFF'}")
    
    def _on_repeat_changed(self, state):
        """Handle repeat checkbox change"""
        repeat = state == Qt.Checked
        self.audio_player.repeat = repeat
        print(f"Repeat: {'ON' if repeat else 'OFF'}")
    
    def _show_drag_help(self):
        """Show help dialog for dragging files"""
        help_text = """
        <h3>How to Drag Files to External Applications</h3>
        
        <b>Method 1: Click and Drag</b>
        1. Select one or more files in the table
        2. Click anywhere on the selected row(s) and drag outside the application
        3. Drop onto Capcut, Explorer, Desktop, or any application that accepts files
        
        <b>Method 2: Right-click Menu</b>
        1. Right-click on selected files
        2. Choose "📤 Drag to Capcut/Explorer"
        3. Drag the icon that appears
        
        <b>Method 3: Quick Export</b>
        1. Right-click on selected files
        2. Choose "🚀 Quick Export To"
        3. Select destination (Desktop, Documents, or Custom Folder)
        
        <b>Supported Applications:</b>
        • Capcut (video editing)
        • Adobe Premiere
        • Windows Explorer
        • Desktop
        • Any file manager
        • Email attachments
        • Cloud storage apps
        """
        
        msg = QMessageBox(self)
        msg.setWindowTitle("Drag & Drop Help")
        msg.setTextFormat(Qt.RichText)
        msg.setText(help_text)
        msg.setIcon(QMessageBox.Information)
        msg.exec_()
    
    def _load_settings(self):
        """Load application settings"""
        settings = QSettings("AudioEverythingPro", "AudioEverything")
        
        # Window state
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        
        # Last folder
        self.last_folder = settings.value("last_folder", str(Path.home()))
    
    def _save_settings(self):
        """Save application settings"""
        settings = QSettings("AudioEverythingPro", "AudioEverything")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("last_folder", self.last_folder)
    
    def _load_existing_files(self):
        """Load existing files dari database saat startup"""
        try:
            files = self.database.get_all_files()
            self.table_model.set_files(files)
            self.lbl_status.setText(f"Loaded {len(files)} files from database")
        except Exception as e:
            print(f"Error loading existing files: {e}")
    
    def _update_file_count(self):
        """Update file count label"""
        count = self.database.get_file_count()
        self.lbl_file_count.setText(f"{count} files")
    
    def _on_search_text_changed(self, text):
        """Handle search text change dengan delay"""
        self.search_timer.start(300)  # Delay 300ms
    
    def _perform_search(self):
        """Perform search"""
        query = self.search_input.text().strip()
        
        if not query:
            files = self.database.get_all_files()
            self.table_model.set_files(files)
        else:
            files = self.database.search_files(query, limit=1000)
            self.table_model.set_files(files)
        
        self._update_file_count()
    
    def _browse_folder(self):
        """Browse folder untuk scanning"""
        folder = QFileDialog.getExistingDirectory(
            self, "Select Folder to Scan", self.last_folder
        )
        
        if folder:
            self.last_folder = folder
            self._start_scanning([folder])
    
    def _select_files(self):
        """Select individual files"""
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Media Files", self.last_folder,
            "Media Files (*.mp3 *.wav *.flac *.aac *.ogg *.m4a *.wma *.mp4 *.avi *.mov *.mkv *.flv *.wmv *.m4v *.webm);;All Files (*.*)"
        )
        
        if files:
            self._start_scanning(files)
    
    def _start_scanning(self, paths: List[str]):
        """Start scanning files"""
        # Stop scanner sebelumnya jika masih running
        if self.scanner_thread and self.scanner_thread.isRunning():
            self.scanner_worker.stop()
            self.scanner_thread.quit()
            self.scanner_thread.wait()
        
        # Setup UI untuk scanning
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.lbl_status.setText("Scanning files...")
        self.btn_rescan.setEnabled(False)
        self.btn_select_folder.setEnabled(False)
        self.btn_select_files.setEnabled(False)
        
        # Create worker dan thread
        self.scanner_worker = ScannerWorker(paths, self.database)
        self.scanner_thread = QThread()
        
        # Move worker ke thread
        self.scanner_worker.moveToThread(self.scanner_thread)
        
        # Connect signals
        self.scanner_thread.started.connect(self.scanner_worker.scan)
        self.scanner_worker.progress.connect(self._on_scan_progress)
        self.scanner_worker.finished.connect(self._on_scan_finished)
        self.scanner_worker.error.connect(self._on_scan_error)
        
        # Start thread
        self.scanner_thread.start()
    
    def _on_scan_progress(self, percent, total, message):
        """Handle scan progress"""
        self.lbl_status.setText(message)
        self.progress_bar.setValue(percent)
    
    def _on_scan_finished(self, files):
        """Handle scan completion"""
        # Update UI
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(f"Scan complete. Found {len(files)} files.")
        self.btn_rescan.setEnabled(True)
        self.btn_select_folder.setEnabled(True)
        self.btn_select_files.setEnabled(True)
        
        # Update table
        self.table_model.set_files(files)
        self._update_file_count()
        
        # Cleanup thread
        if self.scanner_thread:
            self.scanner_thread.quit()
            self.scanner_thread.wait()
            self.scanner_thread = None
        
        # Show notification
        self.statusBar().showMessage(f"Indexed {len(files)} media files", 3000)
    
    def _on_scan_error(self, error_msg):
        """Handle scan error"""
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(f"Scan error: {error_msg}")
        self.btn_rescan.setEnabled(True)
        self.btn_select_folder.setEnabled(True)
        self.btn_select_files.setEnabled(True)
        
        QMessageBox.critical(self, "Scan Error", f"Error scanning files:\n{error_msg}")
    
    def _rescan_current(self):
        """Rescan folder/files"""
        QMessageBox.information(self, "Rescan", "To rescan, please select a folder or files again.")
    
    def _clear_index(self):
        """Clear semua indexed files"""
        reply = QMessageBox.question(
            self, "Clear Index",
            "Are you sure you want to clear all indexed files? This cannot be undone.",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            success = self.database.clear_all()
            if success:
                self.table_model.set_files([])
                self._update_file_count()
                self.lbl_status.setText("Index cleared")
                self.statusBar().showMessage("All indexed files cleared", 3000)
            else:
                QMessageBox.critical(self, "Clear Error", "Failed to clear index.")
    
    def _show_advanced_search(self):
        """Show advanced search dialog"""
        QMessageBox.information(self, "Advanced Search", "Advanced search feature coming soon!")
    
    def _on_selection_changed(self):
        """Handle table selection change"""
        try:
            selected = self.table_view.selectionModel().selectedRows()
            if not selected:
                return
            
            # Get first selected row
            row = selected[0].row()
            media_file = self.table_model.get_file_at(row)
            
            if media_file:
                self.current_media_file = media_file
                
                # Load media file dengan autoplay setting
                autoplay = self.chk_autoplay.isChecked()
                repeat = self.chk_repeat.isChecked()
                
                if self.audio_player.load_file(media_file.path, autoplay, repeat):
                    # Generate waveform data
                    waveform_data = AudioAnalyzer.generate_waveform_data(media_file.path)
                    
                    # Update waveform widget
                    self.waveform_widget.set_audio_data(waveform_data, media_file.duration)
                    
                    # Update UI
                    file_type = "Video" if media_file.is_video else "Audio"
                    duration_str = str(timedelta(seconds=int(media_file.duration)))[2:] if media_file.duration > 0 else "N/A"
                    self.lbl_status.setText(
                        f"Loaded {file_type}: {media_file.filename} ({duration_str})"
                    )
                    
                    # Reset playback UI
                    self.playback_slider.setValue(0)
                    self._update_playback_time(0, media_file.duration)
                    
                    # Update status bar
                    self.statusBar().showMessage(f"Loaded: {media_file.filename} - Ready to drag")
                    
                    # Update player controls
                    if autoplay:
                        self.btn_play.setEnabled(False)
                        self.btn_pause.setEnabled(True)
                        self.btn_stop.setEnabled(True)
                    else:
                        self.btn_play.setEnabled(True)
                        self.btn_pause.setEnabled(False)
                        self.btn_stop.setEnabled(False)
                else:
                    self.lbl_status.setText(f"Failed to load: {media_file.filename}")
                    
        except Exception as e:
            print(f"Error in selection changed: {e}")
    
    def _toggle_play_pause(self):
        """Toggle play/pause"""
        if self.audio_player.is_playing:
            self.pause_audio()
        else:
            self.play_audio()
    
    def _select_all_files(self):
        """Select semua files di table"""
        self.table_view.selectAll()
    
    def play_audio(self):
        """Play selected audio"""
        if self.audio_player.play():
            self.btn_play.setEnabled(False)
            self.btn_pause.setEnabled(True)
            self.btn_stop.setEnabled(True)
    
    def pause_audio(self):
        """Pause audio playback"""
        self.audio_player.pause()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(True)
    
    def stop_audio(self):
        """Stop audio playback"""
        self.audio_player.stop()
        self.btn_play.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.playback_slider.setValue(0)
        self._update_playback_time(0, self.audio_player.duration)
    
    def _update_playback_ui(self):
        """Update playback UI"""
        if not self.playback_updating and self.audio_player.duration > 0:
            position = self.audio_player.position
            slider_value = int((position / self.audio_player.duration) * 1000)
            self.playback_slider.setValue(slider_value)
            
            self._update_playback_time(position, self.audio_player.duration)
            
            self.waveform_widget.set_position(position)
    
    def _update_playback_time(self, current: float, total: float):
        """Update playback time label"""
        current_str = str(timedelta(seconds=int(current)))[2:7]
        total_str = str(timedelta(seconds=int(total)))[2:7]
        self.lbl_playback_time.setText(f"{current_str} / {total_str}")
    
    def _on_slider_moved(self, value):
        """Handle slider movement"""
        if self.audio_player.duration > 0:
            position = (value / 1000.0) * self.audio_player.duration
            self._update_playback_time(position, self.audio_player.duration)
    
    def _on_slider_pressed(self):
        """Handle slider pressed"""
        self.playback_updating = True
    
    def _on_slider_released(self):
        """Handle slider released"""
        if self.audio_player.duration > 0:
            position = (self.playback_slider.value() / 1000.0) * self.audio_player.duration
            self.audio_player.set_position(position)
        self.playback_updating = False
    
    def _extract_current_audio(self):
        """Extract audio dari video"""
        if not self.current_media_file or not self.current_media_file.is_video:
            QMessageBox.warning(self, "Extract Audio", "Please select a video file first")
            return
        
        if not MOVIEPY_AVAILABLE:
            QMessageBox.warning(self, "Extract Audio", "MoviePy is not available. Cannot extract audio.")
            return
        
        self.extract_audio_from_video(self.current_media_file.path)
    
    def extract_audio_from_video(self, video_path: str):
        """Extract audio dari video file"""
        if not MOVIEPY_AVAILABLE:
            return
        
        try:
            save_path, _ = QFileDialog.getSaveFileName(
                self, "Save Extracted Audio",
                str(Path(video_path).with_suffix('.wav')),
                "Audio Files (*.wav *.mp3);;All Files (*.*)"
            )
            
            if not save_path:
                return
            
            temp_audio = AudioAnalyzer.extract_audio_from_video(video_path)
            if temp_audio and os.path.exists(temp_audio):
                import shutil
                shutil.copy2(temp_audio, save_path)
                
                try:
                    os.remove(temp_audio)
                except:
                    pass
                
                QMessageBox.information(self, "Success", 
                    f"Audio extracted successfully to:\n{save_path}")
            else:
                QMessageBox.warning(self, "Error", "Failed to extract audio")
                
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Error extracting audio:\n{str(e)}")
    
    def closeEvent(self, event):
        """Handle application close"""
        self.audio_player.stop()
        self.audio_player.cleanup()
        self._save_settings()
        event.accept()


def main():
    """Main application entry point"""
    try:
        # Enable High DPI scaling
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        
        app = QApplication(sys.argv)
        app.setApplicationName("Audio Everything Pro")
        app.setOrganizationName("AudioEverythingPro")
        
        # Apply dark theme
        try:
            app.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
        except Exception as e:
            print(f"Error applying dark theme: {e}")
            app.setStyle("Fusion")
        
        window = AudioEverythingApp()
        window.show()
        
        sys.exit(app.exec_())
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    print("=" * 60)
    print("Audio Everything Pro - Starting...")
    print("=" * 60)
    
    # Check dependencies
    dependencies = ["moviepy", "numpy", "tinytag", "rapidfuzz", "PyQt5", "qdarkstyle"]
    
    for dep in dependencies:
        try:
            if dep == "moviepy":
                __import__("moviepy.editor")
                print(f"✓ {dep} is available")
            elif dep == "numpy":
                __import__("numpy")
                print(f"✓ {dep} is available")
            elif dep == "tinytag":
                __import__("tinytag")
                print(f"✓ {dep} is available")
            elif dep == "rapidfuzz":
                __import__("rapidfuzz")
                print(f"✓ {dep} is available")
            elif dep == "PyQt5":
                from PyQt5.QtWidgets import QApplication
                print(f"✓ {dep} is available")
            elif dep == "qdarkstyle":
                __import__("qdarkstyle")
                print(f"✓ {dep} is available")
        except ImportError:
            print(f"✗ {dep} is not available")
            if dep == "moviepy":
                print("   Install with: pip install moviepy")
    
    print("=" * 60)
    
    exit_code = main()
    sys.exit(exit_code)